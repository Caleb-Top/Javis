import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { getPetSkin } from "../src/pet/PetSkinRegistry.ts";
import { PetSkinLoadGeneration } from "../src/pet/petTypes.ts";

const petSource = readFileSync(new URL("../src/pet/PetSurface.ts", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => { resolve = next; });
  return { promise, resolve };
}

test("registers procedural3d, sprite2d and orb skins", () => {
  assert.equal(getPetSkin("javis-lightform").kind, "procedural3d");
  assert.equal(getPetSkin("javis-anime").kind, "sprite2d");
  assert.equal(getPetSkin("javis-orb").kind, "orb");
});

test("stale loads dispose without replacing a newer 2d or orb selection", async () => {
  const loads = new PetSkinLoadGeneration();
  const pending = deferred<{ dispose(): void }>();
  let activeSkin = "javis-anime";
  let disposeCount = 0;
  const generation = loads.next();
  const settlement = pending.promise.then((loaded) => {
    if (loads.accept(generation, loaded)) activeSkin = "javis-lightform";
  });

  loads.next();
  activeSkin = "javis-orb";
  pending.resolve({ dispose: () => { disposeCount += 1; } });
  await settlement;

  assert.equal(activeSkin, "javis-orb");
  assert.equal(disposeCount, 1);
});

test("dispose invalidates pending generations and production releases mounted resources", async () => {
  const loads = new PetSkinLoadGeneration();
  const pending = deferred<{ dispose(): void }>();
  let disposeCount = 0;
  let mounted = false;
  const generation = loads.next();
  const settlement = pending.promise.then((loaded) => {
    mounted = loads.accept(generation, loaded);
  });

  loads.invalidate();
  pending.resolve({ dispose: () => { disposeCount += 1; } });
  await settlement;

  assert.equal(mounted, false);
  assert.equal(disposeCount, 1);
  assert.match(petSource, /function releaseAvatar\(\)[\s\S]*?controller\?\.dispose\(\)[\s\S]*?mountedSurface\?\.dispose\(\)[\s\S]*?avatarHost\.replaceChildren\(\)/);
  assert.match(petSource, /dispose:\s*\(\)\s*=>\s*\{[\s\S]*?skinLoads\.invalidate\(\)[\s\S]*?releaseAvatar\(\)[\s\S]*?unsubscribeExpression\(\)/);
  assert.match(petSource, /removeEventListener\("javis:stop-audio"/);
  assert.match(petSource, /removeEventListener\("javis:playback-envelope-start"/);
});

test("3d mounting uses manifest fallback, controller capabilities and current intent only", () => {
  assert.match(petSource, /avatarAssetRegistry\.get\(skin\.manifestId\)/);
  assert.match(petSource, /avatarAssetRegistry\.resolveFallback\(skin\.manifestId\)/);
  assert.match(petSource, /applySkin\(fallbackSkinFor\(skin\), false\)/);
  assert.match(petSource, /skinLoads\.accept\(generation, mountedSurface\)/);
  assert.match(petSource, /createAvatarController\(\{[\s\S]*?sink:\s*mountedSurface,[\s\S]*?capabilities:\s*manifest\.capabilities/);
  assert.match(petSource, /isUnexpiredIntent\(latestIntent\)/);
  assert.match(petSource, /intent\.revision <= latestIntent\.revision/);
  assert.match(petSource, /status\.textContent = formatSurfaceStatus\(snapshot\)/);
  assert.match(petSource, /status\.dataset\.visible = "true"/);
});

test("expression and playback event boundaries subscribe once and clean the mouth immediately", () => {
  assert.match(petSource, /options\.subscribeExpression\?\.\(handleExpression\)/);
  assert.match(petSource, /document\.addEventListener\("javis:stop-audio", handleStopAudio\)/);
  assert.match(petSource, /handleStopAudio[\s\S]*?stopSpeakingEnvelope\(\)[\s\S]*?avatarController\?\.interrupt\(\)/);
  assert.match(petSource, /document\.addEventListener\("javis:playback-envelope-start", handlePlaybackEnvelopeStart\)/);
  assert.match(petSource, /speakingEnvelope\.start\(detail\?\.durationMs, Date\.now\(\)\)/);
  assert.match(petSource, /avatarController\.tick\(now\)/);
  assert.match(petSource, /avatarController\.setSpeakingLevel\(speakingEnvelope\.level\(now\)\)/);
  assert.match(petSource, /intent\.interrupt \|\| intent\.base_state === "listening"/);
});

test("the original button remains the only pointer, drag, click and context-menu owner", () => {
  assert.match(petSource, /class="pet-avatar-host" aria-hidden="true"/);
  assert.match(petSource, /installPointerDrag\(spriteButton/);
  assert.match(petSource, /spriteButton\.addEventListener\("click"/);
  assert.match(petSource, /spriteButton\.addEventListener\("contextmenu"/);
  assert.doesNotMatch(petSource, /avatarHost\.addEventListener\("(?:pointer|click|contextmenu)/);
  assert.match(cssSource, /\.pet-avatar-host[\s\S]*?pointer-events:\s*none/);
  assert.match(cssSource, /\.pet-avatar-host \.avatar-canvas[\s\S]*?background:\s*transparent !important[\s\S]*?pointer-events:\s*none !important/);
  assert.match(cssSource, /\.pet-sprite-button[\s\S]*?z-index:\s*2/);
  assert.match(cssSource, /\.pet-status[\s\S]*?z-index:\s*3/);
});

test("production mounting wires performance samples and bounded context recovery", () => {
  assert.match(petSource, /new PerformanceGovernor\(\{/);
  assert.match(petSource, /new AvatarFallbackCoordinator\(\{ initialTier: avatarTier \}\)/);
  assert.match(petSource, /onFrame: \(durationMs\) => handleAvatarFrame\(skin, generation, durationMs\)/);
  assert.match(petSource, /onContextLost: \(\) => handleAvatarContextLost\(skin, generation\)/);
  assert.match(petSource, /onContextRestored: \(\) => handleAvatarContextRestored\(skin, generation\)/);
  assert.match(petSource, /fallbackCoordinator\.onContextLost\(\)/);
  assert.match(petSource, /coordinator\.requestContextRecovery\(\)/);
  assert.match(petSource, /coordinator\.onContextRestored\(latestIntent \|\| undefined\)/);
  assert.match(petSource, /fallbackCoordinator\.onRecoverySucceeded\(latestIntent \|\| undefined\)/);
  assert.match(petSource, /showVisualFallback\(skin\)/);
  assert.match(petSource, /if \(recovery\.canAttemptRecovery\) scheduleRecoveryRetry\(skin, generation\)/);
  assert.match(petSource, /cancelRecoveryTimer\(\)/);
});
