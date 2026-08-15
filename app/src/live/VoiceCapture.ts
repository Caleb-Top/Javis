import type { BackendClient } from "../bridge/backendClient";
import { resolveBackendEndpoints } from "../bridge/backendEndpoints.ts";
import type { LiveState } from "./liveState";

export type VoiceNoiseProfile = "off" | "standard" | "strong";

export type VoiceCaptureOptions = {
  onBargeIn(): void | Promise<void>;
  onPartial?(text: string): void;
  onTranscript?(text: string): void;
  onEmptyTranscript?(message: string): void;
  onLevel?(level: number): void;
  openStream?(url: string): WebSocket;
  reconnectDelaysMs?: readonly number[];
  streamReadyTimeoutMs?: number;
  noiseProfile?(): VoiceNoiseProfile;
  deviceIndex?(): string | number | undefined;
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
  deviceIndex?: number;
  rate?: number;
  channels?: number;
  durationMs?: number;
  rms?: number;
  peak?: number;
  signalDetected?: boolean;
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
  applyInputDeviceChange(): Promise<void>;
  resumeListeningState(): void;
  probeMicrophone(): Promise<AudioProbeResult>;
  probeSystemAudio(): Promise<AudioProbeResult>;
  selfTest(source?: "microphone" | "system"): Promise<AudioProbeResult>;
};

const DEFAULT_RECONNECT_DELAYS_MS = [250, 500, 1000, 2000, 5000] as const;
const DEFAULT_STREAM_READY_TIMEOUT_MS = 10000;
const STREAM_RELEASE_TIMEOUT_MS = 1500;

export function createVoiceCapture(
  client: BackendClient,
  options: VoiceCaptureOptions,
): VoiceCapture {
  let continuous = false;
  let wantsContinuous = false;
  let streamSocket: WebSocket | null = null;
  let startResolve: (() => void) | null = null;
  let startReject: ((error: Error) => void) | null = null;
  let startPromise: Promise<void> | null = null;
  let reconnectTimer: number | null = null;
  let streamReadyTimer: number | null = null;
  let reconnectAttempt = 0;
  let socketGeneration = 0;
  let inputDeviceChangeTask: Promise<void> = Promise.resolve();
  let noiseProfile: VoiceNoiseProfile = options.noiseProfile?.() ?? "standard";
  const configuredReconnectDelays = (options.reconnectDelaysMs ?? DEFAULT_RECONNECT_DELAYS_MS)
    .filter((delay) => Number.isFinite(delay) && delay >= 0);
  const reconnectDelays = configuredReconnectDelays.length
    ? configuredReconnectDelays
    : [...DEFAULT_RECONNECT_DELAYS_MS];
  const configuredReadyTimeout = Number(options.streamReadyTimeoutMs);
  const streamReadyTimeoutMs = Number.isFinite(configuredReadyTimeout)
    && configuredReadyTimeout > 0
    ? configuredReadyTimeout
    : DEFAULT_STREAM_READY_TIMEOUT_MS;

  function settleStart(error?: Error): void {
    const resolve = startResolve;
    const reject = startReject;
    startResolve = null;
    startReject = null;
    startPromise = null;
    if (error) reject?.(error);
    else resolve?.();
  }

  function clearReconnectTimer(): void {
    if (reconnectTimer === null) return;
    globalThis.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  function clearStreamReadyTimer(): void {
    if (streamReadyTimer === null) return;
    globalThis.clearTimeout(streamReadyTimer);
    streamReadyTimer = null;
  }

  function isCurrentSocket(socket: WebSocket, generation: number): boolean {
    return wantsContinuous
      && streamSocket === socket
      && socketGeneration === generation;
  }

  function handleStreamEvent(message: Record<string, unknown>): void {
    const type = String(message.type || "");
    if (type === "audio.stream.ready") {
      reconnectAttempt = 0;
      clearReconnectTimer();
      clearStreamReadyTimer();
      continuous = true;
      settleStart();
      options.onState("listening");
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
    if (type === "transcript.empty") {
      options.onState("listening");
      options.onEmptyTranscript?.("没有识别到语音，请再说一次");
      return;
    }
  }

  function scheduleReconnect(): void {
    if (!wantsContinuous || reconnectTimer !== null || streamSocket) return;
    const delay = reconnectDelays[
      Math.min(reconnectAttempt, reconnectDelays.length - 1)
    ];
    reconnectAttempt += 1;
    reconnectTimer = globalThis.setTimeout(() => {
      reconnectTimer = null;
      if (!wantsContinuous || streamSocket) return;
      openContinuousSocket();
    }, delay);
  }

  function reportConnectionFailure(error: Error, publicMessage: string): void {
    continuous = false;
    settleStart(error);
    scheduleReconnect();
    options.onState("error");
    options.onError(publicMessage);
  }

  function retireSocket(
    socket: WebSocket,
    generation: number,
    error: Error,
    publicMessage: string,
  ): void {
    if (!isCurrentSocket(socket, generation)) return;
    streamSocket = null;
    continuous = false;
    clearStreamReadyTimer();
    try {
      if (socket.readyState !== 3) socket.close();
    } catch {
      // The reconnect path does not depend on a successful close handshake.
    }
    reportConnectionFailure(error, publicMessage);
  }

  function openContinuousSocket(): void {
    if (!wantsContinuous || streamSocket) return;
    let socket: WebSocket;
    try {
      const endpoints = resolveBackendEndpoints();
      socket = options.openStream?.(`${endpoints.websocket}/ws_voice_stream`)
        ?? new WebSocket(`${endpoints.websocket}/ws_voice_stream`);
    } catch (cause) {
      const error = cause instanceof Error
        ? cause
        : new Error("native audio stream connection failed");
      reportConnectionFailure(error, "语音服务尚未就绪，正在等待本地运行时。");
      return;
    }

    const generation = socketGeneration + 1;
    socketGeneration = generation;
    streamSocket = socket;
    socket.onopen = () => {
      if (!isCurrentSocket(socket, generation)) return;
      try {
        const deviceIndex = options.deviceIndex?.();
        const payload: Record<string, unknown> = {
          session_id: client.sessionId(),
          noise_profile: noiseProfile,
          protocol_version: 1,
        };
        if (deviceIndex !== undefined && deviceIndex !== null && deviceIndex !== "") {
          payload.device_index = Number(deviceIndex);
        }
        socket.send(JSON.stringify({
          type: "audio.stream.start",
          payload,
        }));
      } catch (cause) {
        retireSocket(
          socket,
          generation,
          cause instanceof Error ? cause : new Error("native audio stream connection failed"),
          "语音服务尚未就绪，正在等待本地运行时。",
        );
      }
    };
    socket.onmessage = (event) => {
      if (!isCurrentSocket(socket, generation)) return;
      let message: Record<string, unknown>;
      try {
        message = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        options.onError("invalid native audio event");
        return;
      }
      if (String(message.type || "") === "audio.error") {
        const error = new Error(String(message.message || "native audio stream failed"));
        retireSocket(socket, generation, error, error.message);
        return;
      }
      handleStreamEvent(message);
    };
    socket.onerror = () => {
      retireSocket(
        socket,
        generation,
        new Error("native audio stream connection failed"),
        "语音服务尚未就绪，正在等待本地运行时。",
      );
    };
    socket.onclose = () => {
      retireSocket(
        socket,
        generation,
        new Error("native audio stream disconnected"),
        "native audio stream disconnected",
      );
    };
    clearStreamReadyTimer();
    streamReadyTimer = globalThis.setTimeout(() => {
      retireSocket(
        socket,
        generation,
        new Error("native audio stream ready timed out"),
        "native audio stream ready timed out",
      );
    }, streamReadyTimeoutMs);
  }

  async function startContinuous(): Promise<void> {
    if (wantsContinuous) return startPromise ?? Promise.resolve();
    wantsContinuous = true;
    continuous = false;
    reconnectAttempt = 0;
    clearReconnectTimer();
    clearStreamReadyTimer();
    const ready = new Promise<void>((resolve, reject) => {
      startResolve = resolve;
      startReject = reject;
    });
    startPromise = ready;
    try {
      options.onState("listening");
      openContinuousSocket();
    } catch (cause) {
      wantsContinuous = false;
      settleStart(cause instanceof Error ? cause : new Error("native audio stream start failed"));
    }
    return ready;
  }

  async function pauseContinuous(waitForRelease = false): Promise<void> {
    wantsContinuous = false;
    continuous = false;
    reconnectAttempt = 0;
    clearReconnectTimer();
    clearStreamReadyTimer();
    socketGeneration += 1;
    const socket = streamSocket;
    streamSocket = null;
    settleStart(new Error("native audio stream start cancelled"));
    let releasePromise: Promise<void> | null = null;
    if (socket && waitForRelease && socket.readyState === 1) {
      releasePromise = new Promise<void>((resolve) => {
        let settled = false;
        let timer: ReturnType<typeof globalThis.setTimeout> | null = null;
        const finish = (): void => {
          if (settled) return;
          settled = true;
          if (timer !== null) globalThis.clearTimeout(timer);
          resolve();
        };
        timer = globalThis.setTimeout(finish, STREAM_RELEASE_TIMEOUT_MS);
        socket.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data) as Record<string, unknown>;
            if (String(message.type || "") === "audio.stream.stopped") finish();
          } catch {
            // Ignore unrelated or malformed events while waiting for release.
          }
        };
        socket.onerror = finish;
        socket.onclose = finish;
      });
    }
    if (socket && socket.readyState === 1) {
      try {
        socket.send(JSON.stringify({
          type: "audio.stream.stop",
          payload: { session_id: client.sessionId() },
        }));
      } catch {
        // The user-requested stop still owns the lifecycle if the socket vanished.
      }
    }
    if (releasePromise) await releasePromise;
    if (socket) {
      socket.onopen = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      try {
        socket.close();
      } catch {
        // Stop remains complete even if the close handshake fails.
      }
    }
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

  function applyInputDeviceChange(): Promise<void> {
    inputDeviceChangeTask = inputDeviceChangeTask.catch(() => undefined).then(async () => {
      if (!wantsContinuous) return;
      await pauseContinuous(true);
      await startContinuous();
    });
    return inputDeviceChangeTask;
  }

  async function probeMicrophoneWithStreamPause(): Promise<AudioProbeResult> {
    const shouldResume = wantsContinuous;
    if (shouldResume) await pauseContinuous(true);
    const result = await probe("microphone");
    if (shouldResume) {
      try {
        await startContinuous();
      } catch {
        // The normal stream callbacks already surface a reconnect failure.
      }
    }
    return result;
  }

  async function probe(source: "microphone" | "system"): Promise<AudioProbeResult> {
    try {
      const payload: Record<string, unknown> = {
        source,
        duration: source === "microphone" ? 4.5 : 1.2,
      };
      const deviceIndex = source === "microphone" ? options.deviceIndex?.() : undefined;
      if (deviceIndex !== undefined && deviceIndex !== null && deviceIndex !== "") {
        payload.device_index = Number(deviceIndex);
      }
      return await client.post<AudioProbeResult>("/api/voice/capture/probe", payload);
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
    applyInputDeviceChange,
    resumeListeningState: () => {
      if (continuous) options.onState("listening");
    },
    probeMicrophone: probeMicrophoneWithStreamPause,
    probeSystemAudio: () => probe("system"),
    selfTest: (source = "microphone") => probe(source),
  };
}
