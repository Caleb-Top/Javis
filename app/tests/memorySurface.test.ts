import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  beginMemoryAction,
  createMemorySurfaceState,
  deletionProgressPercent,
  describeSourceHandling,
  reduceMemorySurfaceState,
  selectedMemorySource,
} from "../src/memory/memoryState.ts";
import type {
  DeletionProgress,
  MemorySource,
  MemoryStatus,
} from "../src/memory/memoryTypes.ts";

const surfaceSource = readFileSync(
  new URL("../src/memory/MemorySurface.ts", import.meta.url),
  "utf8",
);
const conversationDrawerSource = readFileSync(
  new URL("../src/panels/ConversationDrawer.ts", import.meta.url),
  "utf8",
);
const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

function source(id: string, kind: MemorySource["kind"] = "experience_episode"): MemorySource {
  return Object.freeze({
    id,
    kind,
    revision: 2,
    title: "Evidence-backed source",
    summary: "A bounded autobiographical memory summary.",
    detail: "Verified result",
    startedAtUtc: "2026-08-20T10:00:00.000Z",
    endedAtUtc: "2026-08-20T10:05:00.000Z",
    status: "active",
    evidenceCount: 2,
    sourceEvidenceIds: Object.freeze(["event-source-1", "message-source-1"]),
  });
}

function status(): MemoryStatus {
  return Object.freeze({
    serviceState: "ready",
    sharedMemories: Object.freeze([]),
    legacy: Object.freeze({
      state: "read_only",
      quarantineCount: 1,
      candidateCount: 2,
      lastScanAtUtc: null,
      detail: "旧归档保持只读。",
    }),
  });
}

test("memory surface state selects a real source and preserves its evidence chain", () => {
  const episode = source("episode-surface-1");
  const journal = source("journal-surface-1", "journal_entry");
  const loaded = reduceMemorySurfaceState(createMemorySurfaceState(), {
    type: "load.succeeded",
    episodes: [episode],
    journal: [journal],
    status: status(),
  });

  assert.equal(loaded.loadState, "ready");
  assert.equal(loaded.selectedSourceId, episode.id);
  assert.deepEqual(selectedMemorySource(loaded)?.sourceEvidenceIds, [
    "event-source-1",
    "message-source-1",
  ]);
  assert.equal(JSON.stringify(loaded).includes("owner_subject_id"), false);
});

test("double clicks cannot enqueue the same revision action twice", () => {
  const key = "shared.confirm:shared-surface-1:1";
  const first = beginMemoryAction(createMemorySurfaceState(), key);
  const second = beginMemoryAction(first.state, key);

  assert.equal(first.accepted, true);
  assert.equal(second.accepted, false);
  assert.deepEqual(second.state.pendingActions, [key]);
});

test("deletion progress and source handling communicate the destructive boundary", () => {
  const progress: DeletionProgress = Object.freeze({
    deletionRequestId: "deletion-surface-1",
    state: "prompt_invalidated",
    scope: "episode",
    sourceHandling: "source_and_derived",
    attempt: 0,
    lastReasonCode: null,
    updatedAtUtc: "2026-08-20T10:05:00.000Z",
    completedAtUtc: null,
  });

  assert.equal(deletionProgressPercent(progress), 89);
  assert.equal(deletionProgressPercent({ ...progress, state: "verified" }), 100);
  assert.equal(describeSourceHandling("derived_only").destructive, false);
  assert.match(describeSourceHandling("derived_only").detail, /保留原始对话来源/);
  assert.equal(describeSourceHandling("source_and_derived").destructive, true);
  assert.match(describeSourceHandling("source_and_derived").detail, /删除所选对话来源正文/);
});

test("surface errors remain bounded so long server text cannot break the drawer", () => {
  const failed = reduceMemorySurfaceState(createMemorySurfaceState(), {
    type: "load.failed",
    message: "x".repeat(2_000),
  });

  assert.equal(failed.error.length, 500);
  assert.match(failed.error, /\.\.\.$/);
  assert.match(cssSource, /\.memory-banner\s*\{[^}]*overflow-wrap:\s*anywhere/s);
  assert.match(cssSource, /\.memory-shared-row p\s*\{[^}]*overflow:\s*auto/s);
  assert.match(cssSource, /@media \(max-width: 440px\)[\s\S]*?white-space:\s*normal/);
});

test("memory surface is an operational keyboard-accessible drawer, not a blank marketing page", () => {
  assert.match(surfaceSource, /role="tablist"/);
  assert.match(surfaceSource, /role="tabpanel"/);
  assert.match(surfaceSource, /role="alertdialog"/);
  assert.match(surfaceSource, /event\.key === "ArrowRight"/);
  assert.match(surfaceSource, /event\.key === "Escape"/);
  assert.match(surfaceSource, /event\.key !== "Tab"/);
  assert.match(surfaceSource, /maxlength="8000"/);
  assert.match(surfaceSource, /maxlength="32768"/);
  assert.match(surfaceSource, /sourceEvidenceIds:\s*source\.sourceEvidenceIds/);
  assert.match(surfaceSource, /确认前不会进入召回/);
  assert.match(surfaceSource, /旧归档保持只读/);
  assert.doesNotMatch(surfaceSource, /hero|立即开始|探索未来|learn more/i);
  assert.match(conversationDrawerSource, /open-memory-manager/);
  assert.match(conversationDrawerSource, /javis:open-memory/);
  assert.match(mainSource, /\.more-control[^\n]+conversationDrawer\.open/);
});
