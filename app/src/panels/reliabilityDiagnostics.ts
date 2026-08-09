import type { BackendReliabilitySnapshot } from "../bridge/backendClient.ts";


export type BackendReliabilitySummary = {
  label: string;
  status: "pass" | "warn" | "fail";
  message: string;
};

export type DiagnosticStatus = "pass" | "warn" | "fail";

export type DiagnosticChecksSummary = {
  counts: Record<DiagnosticStatus, number>;
  overall: DiagnosticStatus;
  text: string;
};

type VoiceQueueDiagnostics = {
  current?: number;
  peak?: number;
  limit?: number;
  dropped?: number;
  dropped_frames?: number;
};

export type VoiceRuntimeDiagnostics = {
  continuous?: {
    running?: boolean;
    session_attached?: boolean;
    owner_generation?: number;
    owner_identity?: string;
    counters?: {
      frames?: number;
      turns?: number;
      transcript_final?: number;
      transcript_empty?: number;
      audio_error?: number;
    };
    queues?: {
      capture?: VoiceQueueDiagnostics;
      transcription?: VoiceQueueDiagnostics;
    };
  };
  gateway?: {
    active_tasks?: number;
    reconnect_attempts_total?: number;
    recoveries_total?: number;
    errors?: {
      recoverable_total?: number;
      nonrecoverable_total?: number;
    };
  };
};

const REQUEST_PHASE_LABELS: Record<BackendReliabilitySnapshot["requestPhase"], string> = {
  idle: "空闲",
  "waiting-accepted": "等待确认",
  "waiting-first-response": "等待首字",
  responding: "响应中",
  terminal: "已结束",
  "timed-out": "已超时",
};


export function summarizeDiagnosticChecks(
  checks: ReadonlyArray<{ status: DiagnosticStatus }>,
): DiagnosticChecksSummary {
  const counts: Record<DiagnosticStatus, number> = { pass: 0, warn: 0, fail: 0 };
  for (const check of checks) {
    counts[check.status] += 1;
  }
  const overall: DiagnosticStatus = counts.fail > 0
    ? "fail"
    : counts.warn > 0
      ? "warn"
      : "pass";
  return {
    counts,
    overall,
    text: `${counts.pass} 项通过，${counts.warn} 项需留意，${counts.fail} 项失败`,
  };
}


export function summarizeBackendReliability(
  snapshot: BackendReliabilitySnapshot,
): BackendReliabilitySummary {
  const timeouts = snapshot.timeoutCounts;
  const timeoutTotal = timeouts.accepted + timeouts["first-response"] + timeouts.overall;
  const status = snapshot.connectionPhase === "error"
    ? "fail"
    : timeoutTotal > 0 || snapshot.connectionPhase === "reconnecting"
      ? "warn"
      : "pass";
  return {
    label: "会话响应保护",
    status,
    message: [
      `ACK ${timeouts.accepted}`,
      `首字 ${timeouts["first-response"]}`,
      `总时限 ${timeouts.overall}`,
      `当前 ${REQUEST_PHASE_LABELS[snapshot.requestPhase]}`,
      `重连 ${snapshot.reconnectCount}`,
      `恢复 ${snapshot.recoveryCount}`,
    ].join(" · "),
  };
}


function count(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0;
}


export function summarizeVoiceRuntime(
  snapshot: VoiceRuntimeDiagnostics,
): BackendReliabilitySummary {
  const continuous = snapshot.continuous ?? {};
  const counters = continuous.counters ?? {};
  const captureQueue = continuous.queues?.capture ?? {};
  const transcriptionQueue = continuous.queues?.transcription ?? {};
  const gateway = snapshot.gateway ?? {};
  const recoverableErrors = count(gateway.errors?.recoverable_total);
  const nonrecoverableErrors = count(gateway.errors?.nonrecoverable_total);
  const recoveries = count(gateway.recoveries_total);
  const captureDrops = count(captureQueue.dropped_frames);
  const transcriptionDrops = count(transcriptionQueue.dropped);
  const audioErrors = count(counters.audio_error);
  const hasWarning = nonrecoverableErrors > 0
    || recoverableErrors > recoveries
    || captureDrops > 0
    || transcriptionDrops > 0
    || audioErrors > 0;
  return {
    label: "连续语音运行链",
    status: hasWarning ? "warn" : "pass",
    message: [
      continuous.running ? "运行中" : "待机",
      `Owner ${count(continuous.owner_generation) || "-"}`,
      `任务 ${count(gateway.active_tasks)}`,
      `帧 ${count(counters.frames)}`,
      `轮次 ${count(counters.turns)}`,
      `转写 ${count(counters.transcript_final)}/${count(counters.transcript_empty)}/${audioErrors}`,
      `队列 ${count(captureQueue.current)}/${count(captureQueue.peak)}`,
      `丢帧 ${captureDrops}`,
      `转写丢弃 ${transcriptionDrops}`,
      `重连 ${count(gateway.reconnect_attempts_total)}/${recoveries}`,
    ].join(" · "),
  };
}
