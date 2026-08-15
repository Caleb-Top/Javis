import {
  normalizeVoiceProvenance,
  type BackendClient,
  type VoiceProvenance,
} from "../bridge/backendClient.ts";
import { resolveBackendEndpoints } from "../bridge/backendEndpoints.ts";
import type { RuntimeAccessScope } from "../bridge/runtimeAccess.ts";
import type { LiveState } from "./liveState";

export type VoiceNoiseProfile = "off" | "standard" | "strong";

export type VoiceInterruption = Readonly<{
  speechSequence: number;
  interruptedRequestId: string | null;
}>;

export type VoiceTranscript = Readonly<{
  text: string;
  turn: number;
  sequence: number;
  voiceProvenance: VoiceProvenance;
}>;

export type VoiceCaptureOptions = {
  onBargeIn(interruption: VoiceInterruption): void | Promise<void>;
  onPartial?(text: string): void;
  onTranscript?(transcript: VoiceTranscript): void | Promise<void>;
  onEmptyTranscript?(message: string): void;
  onLevel?(level: number): void;
  openStream?(url: string, protocols?: string[]): WebSocket;
  runtimeAccessToken?(scope: RuntimeAccessScope): string;
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
  refreshRuntimeAccess(): Promise<void>;
  probeMicrophone(): Promise<AudioProbeResult>;
  probeSystemAudio(): Promise<AudioProbeResult>;
  selfTest(source?: "microphone" | "system"): Promise<AudioProbeResult>;
};

const DEFAULT_RECONNECT_DELAYS_MS = [250, 500, 1000, 2000, 5000] as const;
const DEFAULT_STREAM_READY_TIMEOUT_MS = 10000;
const MAX_TRACKED_VOICE_EVENTS = 128;

type InterruptionBarrier = {
  generation: number;
  speechSequence: number;
  interruptedRequestId: string | null;
  failed: boolean;
  settled: Promise<void>;
};

function eventCounter(value: unknown): number {
  const counter = Number(value);
  return Number.isInteger(counter) && counter >= 0 ? counter : 0;
}

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
  let transcriptDelivery = Promise.resolve();
  const interruptionBarriers: InterruptionBarrier[] = [];
  const handledSpeechStarts = new Set<string>();
  const handledFinals = new Set<string>();
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

  function rememberEvent(events: Set<string>, key: string): boolean {
    if (events.has(key)) return false;
    events.add(key);
    if (events.size > MAX_TRACKED_VOICE_EVENTS) {
      const oldest = events.values().next().value;
      if (oldest) events.delete(oldest);
    }
    return true;
  }

  function reportEventError(error: unknown, fallback: string): void {
    const message = error instanceof Error ? error.message : fallback;
    try {
      options.onError(message);
    } catch {
      // Presentation failures cannot strand the continuous voice lifecycle.
    }
  }

  function beginInterruption(
    message: Record<string, unknown>,
    generation: number,
  ): void {
    const speechSequence = eventCounter(message.sequence);
    const eventKey = `${generation}:${speechSequence}`;
    if (!rememberEvent(handledSpeechStarts, eventKey)) return;
    const interruptedRequestId = typeof client.activeRequestId === "function"
      ? client.activeRequestId()
      : null;
    const interruption = Object.freeze({
      speechSequence,
      interruptedRequestId,
    });
    const barrier: InterruptionBarrier = {
      generation,
      speechSequence,
      interruptedRequestId,
      failed: false,
      settled: Promise.resolve(),
    };
    interruptionBarriers.push(barrier);
    try {
      const outcome = options.onBargeIn(interruption);
      barrier.settled = Promise.resolve(outcome).then(
        () => {
          try {
            options.onState("listening");
          } catch (error) {
            reportEventError(error, "listening state update failed");
          }
        },
        (error) => {
          barrier.failed = true;
          reportEventError(error, "barge-in failed");
        },
      );
    } catch (error) {
      barrier.failed = true;
      reportEventError(error, "barge-in failed");
    }
  }

  function takeInterruptionBarrier(generation: number): InterruptionBarrier | null {
    const index = interruptionBarriers.findIndex(
      (barrier) => barrier.generation === generation,
    );
    if (index < 0) return null;
    return interruptionBarriers.splice(index, 1)[0];
  }

  function enqueueFinalTranscript(
    message: Record<string, unknown>,
    socket: WebSocket,
    generation: number,
  ): void {
    const text = String(message.text || "").trim();
    if (!text) return;
    const turn = eventCounter(message.turn);
    const sequence = eventCounter(message.sequence);
    const finalKey = `${generation}:${sequence}:${turn}`;
    if (!rememberEvent(handledFinals, finalKey)) return;
    const barrier = takeInterruptionBarrier(generation);
    transcriptDelivery = transcriptDelivery.then(async () => {
      await barrier?.settled;
      if (barrier?.failed || !isCurrentSocket(socket, generation)) return;
      let voiceProvenance: VoiceProvenance;
      try {
        voiceProvenance = normalizeVoiceProvenance(
          message.voice_provenance as VoiceProvenance,
          client.sessionId(),
        );
        if (
          voiceProvenance.voice_sequence !== sequence
          || voiceProvenance.voice_turn !== turn
        ) {
          throw new TypeError("voice provenance does not match the final transcript");
        }
      } catch (error) {
        options.onState("listening");
        reportEventError(error, "verified voice provenance is unavailable");
        return;
      }
      options.onState("thinking");
      await options.onTranscript?.(Object.freeze({
        text,
        turn,
        sequence,
        voiceProvenance,
      }));
    }).catch((error) => {
      if (isCurrentSocket(socket, generation)) options.onState("listening");
      reportEventError(error, "final transcript handling failed");
    });
  }

  function enqueueEmptyTranscript(
    socket: WebSocket,
    generation: number,
  ): void {
    const barrier = takeInterruptionBarrier(generation);
    transcriptDelivery = transcriptDelivery.then(async () => {
      await barrier?.settled;
      if (barrier?.failed || !isCurrentSocket(socket, generation)) return;
      options.onState("listening");
      options.onEmptyTranscript?.(
        "\u6ca1\u6709\u8bc6\u522b\u5230\u8bed\u97f3\uff0c\u8bf7\u518d\u8bf4\u4e00\u6b21",
      );
    }).catch((error) => reportEventError(error, "empty transcript handling failed"));
  }

  function handleStreamEvent(
    message: Record<string, unknown>,
    socket: WebSocket,
    generation: number,
  ): void {
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
      beginInterruption(message, generation);
      return;
    }
    if (type === "transcript.partial") {
      const text = String(message.text || "").trim();
      if (text) options.onPartial?.(text);
      return;
    }
    if (type === "transcript.final") {
      enqueueFinalTranscript(message, socket, generation);
      return;
    }
    if (type === "transcript.empty") {
      enqueueEmptyTranscript(socket, generation);
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
      const url = `${endpoints.websocket}/ws_voice_stream`;
      const token = options.runtimeAccessToken?.("voice.capture") ?? "";
      const protocols = token
        ? ["javis-runtime-v1", `javis-capability.${token}`]
        : undefined;
      socket = options.openStream?.(url, protocols)
        ?? (protocols ? new WebSocket(url, protocols) : new WebSocket(url));
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
      handleStreamEvent(message, socket, generation);
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

  async function pauseContinuous(): Promise<void> {
    wantsContinuous = false;
    continuous = false;
    reconnectAttempt = 0;
    clearReconnectTimer();
    clearStreamReadyTimer();
    socketGeneration += 1;
    interruptionBarriers.splice(0);
    handledSpeechStarts.clear();
    handledFinals.clear();
    const socket = streamSocket;
    streamSocket = null;
    settleStart(new Error("native audio stream start cancelled"));
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

  async function refreshRuntimeAccess(): Promise<void> {
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
    refreshRuntimeAccess,
    resumeListeningState: () => {
      if (continuous) options.onState("listening");
    },
    probeMicrophone: () => probe("microphone"),
    probeSystemAudio: () => probe("system"),
    selfTest: (source = "microphone") => probe(source),
  };
}
