export type ConversationWatchdogTimeouts = {
  acceptedMs: number;
  firstResponseMs: number;
  overallMs: number;
};

export function watchdogTiming(timeScale: number): ConversationWatchdogTimeouts {
  return {
    acceptedMs: timeScale === 1
      ? 3000
      : Math.max(300, Math.round(3000 * timeScale)),
    firstResponseMs: timeScale === 1
      ? 15000
      : Math.max(1500, Math.round(15000 * timeScale)),
    overallMs: timeScale === 1
      ? 60000
      : Math.max(3000, Math.round(60000 * timeScale)),
  };
}

export function backendClientTimeoutOverrides(
  timeScale: number,
): Record<string, never> | { requestTimeouts: ConversationWatchdogTimeouts } {
  if (timeScale === 1) return {};
  return { requestTimeouts: watchdogTiming(timeScale) };
}
