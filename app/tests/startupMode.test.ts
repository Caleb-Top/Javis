import assert from "node:assert/strict";
import test from "node:test";

import { getStartupDesktopMode } from "../src/app/startupMode.ts";

test("uses a full surface while first-run onboarding is required", () => {
  assert.equal(getStartupDesktopMode(true), "code");
});

test("starts directly in Live after onboarding is complete", () => {
  assert.equal(getStartupDesktopMode(false), "live");
});
