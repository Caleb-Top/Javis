import type { ExpressionIntent } from "../../life/lifeTypes.ts";
import { mapExpressionIntent } from "./ExpressionMapper.ts";
import {
  ExpressionMixer,
  type ExpressionMixerSnapshot,
} from "./ExpressionMixer.ts";
import type { AvatarCapability } from "./avatarTypes.ts";

export type AvatarExpressionSink = {
  setExpressionTarget(target: ExpressionMixerSnapshot): void;
};

export type AvatarControllerOptions = {
  sink: AvatarExpressionSink;
  capabilities: readonly AvatarCapability[] | Readonly<Partial<Record<AvatarCapability, boolean>>>;
  now?: () => number;
};

export type AvatarController = {
  applyIntent(intent: ExpressionIntent): boolean;
  tick(now?: number): void;
  setSpeakingLevel(level: number): void;
  interrupt(): void;
  dispose(): void;
  revision(): number;
};

export function createAvatarController(options: AvatarControllerOptions): AvatarController {
  const now = options.now ?? Date.now;
  const mixer = new ExpressionMixer(options.capabilities);
  let latestRevision = -1;
  let disposed = false;

  function publish(frame = mixer.snapshot()): void {
    if (!disposed) options.sink.setExpressionTarget(frame);
  }

  function applyIntent(intent: ExpressionIntent): boolean {
    const currentTime = now();
    const expiry = Date.parse(intent.expires_at);
    if (
      disposed
      || intent.schema_version !== 1
      || !Number.isInteger(intent.revision)
      || intent.revision <= latestRevision
      || !Number.isFinite(expiry)
      || expiry <= currentTime
    ) return false;

    latestRevision = intent.revision;
    const target = mapExpressionIntent(intent);
    mixer.submit(target);
    if (intent.interrupt) mixer.interrupt();
    publish(mixer.tick(currentTime));
    return true;
  }

  function tick(at = now()): void {
    if (!disposed) publish(mixer.tick(at));
  }

  function interrupt(): void {
    if (disposed) return;
    mixer.interrupt();
    publish();
  }

  function setSpeakingLevel(level: number): void {
    if (disposed) return;
    mixer.setSpeakingLevel(level);
    publish();
  }

  function dispose(): void {
    if (disposed) return;
    interrupt();
    disposed = true;
  }

  return { applyIntent, tick, setSpeakingLevel, interrupt, dispose, revision: () => latestRevision };
}
