import type { BackendClient } from "../bridge/backendClient";
import type { LiveState } from "./liveState";

export type VoiceCaptureOptions = {
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
  probeMicrophone(): Promise<AudioProbeResult>;
  probeSystemAudio(): Promise<AudioProbeResult>;
  selfTest(source?: "microphone" | "system"): Promise<AudioProbeResult>;
};

export function createVoiceCapture(
  client: BackendClient,
  options: VoiceCaptureOptions,
): VoiceCapture {
  let recording = false;

  async function start(): Promise<void> {
    try {
      const result = await client.post<AudioProbeResult & { recording?: boolean }>(
        "/api/voice/capture/start",
        { source: "microphone" },
      );
      if (!result.ok) throw new Error(result.message);
      recording = true;
      options.onState("listening");
    } catch (error) {
      recording = false;
      options.onState("error");
      options.onError(error instanceof Error ? error.message : "本机麦克风采集失败");
    }
  }

  async function stop(): Promise<void> {
    if (!recording) return;
    recording = false;
    try {
      options.onState("thinking");
      const result = await client.post<AudioProbeResult>(
        "/api/voice/capture/stop",
        {},
      );
      if (!result.ok || !result.audioBase64) throw new Error(result.message);
      options.onAudio(result.audioBase64);
    } catch (error) {
      options.onState("error");
      options.onError(error instanceof Error ? error.message : "本机录音发送失败");
    }
  }

  async function toggle(): Promise<void> {
    if (recording) {
      await stop();
      return;
    }
    await start();
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
        message: error instanceof Error ? error.message : "本机音频检测失败",
      };
    }
  }

  async function probeMicrophone(): Promise<AudioProbeResult> {
    return probe("microphone");
  }

  async function probeSystemAudio(): Promise<AudioProbeResult> {
    return probe("system");
  }

  async function selfTest(
    source: "microphone" | "system" = "microphone",
  ): Promise<AudioProbeResult> {
    return probe(source);
  }

  return {
    toggle,
    stop,
    isRecording: () => recording,
    probeMicrophone,
    probeSystemAudio,
    selfTest,
  };
}
