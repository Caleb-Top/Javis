const MAX_MOUTH_LEVEL = 0.65;
const FALLBACK_MOUTH_LEVEL = 0.25;
const DEFAULT_DURATION_MS = 480;

export class SpeakingEnvelope {
  #startedAt = 0;
  #durationMs = 0;
  #active = false;
  #fallback = false;

  start(durationMs: number | undefined, now: number): void {
    const hasDuration = typeof durationMs === "number" && Number.isFinite(durationMs) && durationMs > 0;
    this.#durationMs = hasDuration ? Math.min(120_000, Math.max(120, durationMs)) : DEFAULT_DURATION_MS;
    this.#startedAt = Number.isFinite(now) ? now : 0;
    this.#fallback = !hasDuration;
    this.#active = true;
  }

  level(now: number): number {
    if (!this.#active) return 0;
    const elapsed = Math.max(0, (Number.isFinite(now) ? now : this.#startedAt) - this.#startedAt);
    if (elapsed >= this.#durationMs) {
      this.stop();
      return 0;
    }
    if (elapsed === 0) return 0;

    const attack = Math.min(1, elapsed / 80);
    const release = Math.min(1, (this.#durationMs - elapsed) / 120);
    const edge = Math.max(0, Math.min(attack, release));
    const syllable = Math.abs(Math.sin(elapsed * 0.019) + 0.42 * Math.sin(elapsed * 0.041));
    const ceiling = this.#fallback ? FALLBACK_MOUTH_LEVEL : MAX_MOUTH_LEVEL;
    return Math.min(ceiling, Math.max(0, (0.08 + syllable * ceiling * 0.72) * edge));
  }

  stop(): void {
    this.#active = false;
    this.#durationMs = 0;
  }

  active(): boolean {
    return this.#active;
  }
}
