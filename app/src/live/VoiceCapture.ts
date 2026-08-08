import type { BackendClient } from "../bridge/backendClient";
import { resolveBackendEndpoints } from "../bridge/backendEndpoints.ts";
import type { LiveState } from "./liveState";

export type VoiceNoiseProfile = "off" | "standard" | "strong";

export type VoiceCaptureOptions = {
  onBargeIn(): void | Promise<void>;
  onPartial?(text: string): void;
  onTranscript?(text: string): void;
  onLevel?(level: number): void;
  openStream?(url: string): WebSocket;
  noiseProfile?(): VoiceNoiseProfile;
  onAudio(audioBase64: string): void;
  onState(state: LiveState): void;
  onError(message: string): void;
};

export type AudioProbeResult = {
  ok: boolean;
  source: "microphone" | "system";
  status: "ready" | "unsupported" | "denied" | "no-audio" | "empty" | "error";
  message: string;
  mimeType?: string;
  bytes?: number;
  trackLabel?: string;
  audioBase64?: string;
};

export type VoiceCapture = {
  toggle(): Promise<void>;
  stop(): Promise<void>;
  isRecording(): boolean;
  startContinuous(): Promise<void>;
  pauseContinuous(): Promise<void>;
  isContinuous(): boolean;
  setNoiseProfile(profile: VoiceNoiseProfile): Promise<void>;
  resumeListeningState(): void;
  probeMicrophone(): Promise<AudioProbeResult>;
  probeSystemAudio(): Promise<AudioProbeResult>;
  selfTest(source?: "microphone" | "system"): Promise<AudioProbeResult>;
};

export function createVoiceCapture(
  client: BackendClient,
  options: VoiceCaptureOptions,
): VoiceCapture {
  let continuous = false;
  let wantsContinuous = false;
  let streamSocket: WebSocket | null = null;
  let startResolve: (() => void) | null = null;
  let startReject: ((error: Error) => void) | null = null;
  let noiseProfile: VoiceNoiseProfile = options.noiseProfile?.() ?? "standard";

  function settleStart(error?: Error): void {
    if (error) startReject?.(error);
    else startResolve?.();
    startResolve = null;
    startReject = null;
  }

  function handleStreamEvent(message: Record<string, unknown>): void {
    const type = String(message.type || "");
    if (type === "audio.stream.ready") {
      continuous = true;
      options.onState("listening");
      settleStart();
      return;
    }
    if (type === "audio.level") {
      options.onLevel?.(Number(message.level || 0));
      return;
    }
    if (type === "speech.start") {
      void Promise.resolve(options.onBargeIn())
        .then(() => options.onState("listening"))
        .catch((error) => options.onError(
          error instanceof Error ? error.message : "barge-in failed",
        ));
      return;
    }
    if (type === "transcript.partial") {
      const text = String(message.text || "").trim();
      if (text) options.onPartial?.(text);
      return;
    }
    if (type === "transcript.final") {
      const text = String(message.text || "").trim();
      if (!text) return;
      options.onState("thinking");
      options.onTranscript?.(text);
      return;
    }
    if (type === "audio.error") {
      const error = new Error(String(message.message || "native audio stream failed"));
      options.onState("error");
      options.onError(error.message);
      settleStart(error);
    }
  }

  async function startContinuous(): Promise<void> {
    if (wantsContinuous && streamSocket) return;
    wantsContinuous = true;
    options.onState("listening");
    const endpoints = resolveBackendEndpoints();
    const socket = options.openStream?.(`${endpoints.websocket}/ws_voice_stream`)
      ?? new WebSocket(`${endpoints.websocket}/ws_voice_stream`);
    streamSocket = socket;
    const ready = new Promise<void>((resolve, reject) => {
      startResolve = resolve;
      startReject = reject;
    });
    socket.onopen = () => {
      socket.send(JSON.stringify({
        type: "audio.stream.start",
        payload: {
          session_id: client.sessionId(),
          noise_profile: noiseProfile,
          protocol_version: 1,
        },
      }));
    };
    socket.onmessage = (event) => {
      try {
        handleStreamEvent(JSON.parse(event.data) as Record<string, unknown>);
      } catch {
        options.onError("invalid native audio event");
      }
    };
    socket.onerror = () => {
      const error = new Error("native audio stream connection failed");
      options.onState("error");
      options.onError("语音服务尚未就绪，正在等待本地运行时。");
      settleStart(error);
    };
    socket.onclose = () => {
      streamSocket = null;
      continuous = false;
      if (wantsContinuous) {
        wantsContinuous = false;
        options.onState("error");
        options.onError("native audio stream disconnected");
        settleStart(new Error("native audio stream disconnected"));
      }
    };
    return ready;
  }

  async function pauseContinuous(): Promise<void> {
    wantsContinuous = false;
    continuous = false;
    const socket = streamSocket;
    streamSocket = null;
    if (socket && socket.readyState === 1) {
      socket.send(JSON.stringify({
        type: "audio.stream.stop",
        payload: { session_id: client.sessionId() },
      }));
    }
    socket?.close();
    options.onState("idle");
  }

  async function toggle(): Promise<void> {
    if (wantsContinuous) {
      await pauseContinuous();
      return;
    }
    await startContinuous();
  }

  async function setNoiseProfile(profile: VoiceNoiseProfile): Promise<void> {
    if (!["off", "standard", "strong"].includes(profile)) return;
    if (profile === noiseProfile) return;
    noiseProfile = profile;
    if (!wantsContinuous) return;
    await pauseContinuous();
    await startContinuous();
  }

  async function probe(source: "microphone" | "system"): Promise<AudioProbeResult> {
    try {
      return await client.post<AudioProbeResult>("/api/voice/capture/probe", {
        source,
        duration: source === "microphone" ? 2.4 : 1.2,
      });
    } catch (error) {
      return {
        ok: false,
        source,
        status: "error",
        message: error instanceof Error ? error.message : "native audio probe failed",
      };
    }
  }

  return {
    toggle,
    stop: pauseContinuous,
    isRecording: () => continuous,
    startContinuous,
    pauseContinuous,
    isContinuous: () => continuous,
    setNoiseProfile,
    resumeListeningState: () => {
      if (continuous) options.onState("listening");
    },
    probeMicrophone: () => probe("microphone"),
    probeSystemAudio: () => probe("system"),
    selfTest: (source = "microphone") => probe(source),
  };
}
