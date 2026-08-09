import assert from "node:assert/strict";
import test from "node:test";

import {
  applyInstalledLocalProfile,
  getModelInstallControlAvailability,
  getModelInstallTargets,
  isActiveModelInstallState,
} from "../src/settings/modelRouteDrafts.ts";

const routes = {
  live: {
    source: "local" as const,
    local: { model: "live-local", base_url: "http://127.0.0.1:11435/v1" },
    remote: {
      provider: "deepseek",
      model: "live-cloud",
      base_url: "https://api.deepseek.com/v1",
    },
  },
  code: {
    source: "remote" as const,
    local: { model: "code-local", base_url: "http://127.0.0.1:22435/v1" },
    remote: {
      provider: "openai",
      model: "code-cloud",
      base_url: "https://api.openai.com/v1",
    },
  },
};

test("independent install updates only the route that opened the unified settings", () => {
  assert.deepEqual(getModelInstallTargets(false, "live"), ["live"]);
  assert.deepEqual(getModelInstallTargets(false, "code"), ["code"]);

  const next = applyInstalledLocalProfile(
    routes,
    ["code"],
    { model: "installed-code", base_url: "http://127.0.0.1:11435/v1" },
  );

  assert.deepEqual(next.live, routes.live);
  assert.equal(next.code.source, "remote");
  assert.deepEqual(next.code.remote, routes.code.remote);
  assert.deepEqual(next.code.local, {
    model: "installed-code",
    base_url: "http://127.0.0.1:11435/v1",
  });
});

test("shared install updates the single profile represented by both routes", () => {
  const targets = getModelInstallTargets(true, "code");
  const next = applyInstalledLocalProfile(
    routes,
    targets,
    { model: "shared-installed", base_url: "http://127.0.0.1:11435/v1" },
  );

  assert.deepEqual(targets, ["live", "code"]);
  assert.equal(next.live.local.model, "shared-installed");
  assert.equal(next.code.local.model, "shared-installed");
  assert.equal(next.live.source, "local");
  assert.equal(next.code.source, "remote");
});

test("route update is immutable and ignores duplicate targets", () => {
  const next = applyInstalledLocalProfile(
    routes,
    ["live", "live"],
    { model: "new-live", base_url: "http://127.0.0.1:31435/v1" },
  );

  assert.notEqual(next, routes);
  assert.equal(routes.live.local.model, "live-local");
  assert.equal(routes.code.local.model, "code-local");
  assert.equal(next.live.local.model, "new-live");
  assert.equal(next.code.local.model, "code-local");
});

test("every non-terminal installer phase remains visibly active", () => {
  for (const state of [
    "planned",
    "approved",
    "verifying",
    "staging",
    "running",
    "pausing",
    "paused",
    "resuming",
    "cancelling",
    "committing",
    "rolling_back",
  ] as const) {
    assert.equal(isActiveModelInstallState(state), true, state);
  }
  for (const state of ["idle", "completed", "failed", "cancelled"] as const) {
    assert.equal(isActiveModelInstallState(state), false, state);
  }
});

test("installer controls follow the active job state and server capabilities", () => {
  assert.deepEqual(
    getModelInstallControlAvailability({
      state: "running",
      jobId: "job-1",
      cancellable: true,
      pausable: true,
    }),
    { pause: true, resume: false, cancel: true },
  );
  assert.deepEqual(
    getModelInstallControlAvailability({
      state: "paused",
      jobId: "job-1",
      cancellable: true,
      pausable: true,
    }),
    { pause: false, resume: true, cancel: true },
  );
  assert.deepEqual(
    getModelInstallControlAvailability({
      state: "committing",
      jobId: "job-1",
      cancellable: false,
      pausable: false,
    }),
    { pause: false, resume: false, cancel: false },
  );
  assert.deepEqual(
    getModelInstallControlAvailability({
      state: "running",
      jobId: "",
      cancellable: true,
      pausable: true,
    }),
    { pause: false, resume: false, cancel: false },
  );
});
