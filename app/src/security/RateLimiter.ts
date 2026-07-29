export const DEFAULT_ACTION_WINDOW_MS = 300;
export const VOICE_ACTION_WINDOW_MS = 600;

export type RateLimitResult = { allowed: boolean; remaining: number };

export class RateLimiter {
  private lastAction = new Map<string, number>();

  take(key: string, windowMs = DEFAULT_ACTION_WINDOW_MS, now = Date.now()): RateLimitResult {
    const previous = this.lastAction.get(key) || 0;
    const remaining = Math.max(0, windowMs - (now - previous));
    if (remaining > 0) return { allowed: false, remaining };
    this.lastAction.set(key, now);
    return { allowed: true, remaining: 0 };
  }
}
