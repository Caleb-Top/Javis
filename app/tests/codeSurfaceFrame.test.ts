import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("Code fills the transparent native window without exposing a black frame", () => {
  assert.match(
    styles,
    /body\[data-desktop-mode="code"\] #code-root\s*\{[^}]*padding:\s*0;[^}]*background:\s*var\(--shell-bg\);/s,
  );
  assert.match(
    styles,
    /body\[data-desktop-mode="code"\] \.code-surface\s*\{[^}]*border:\s*0;[^}]*border-radius:\s*0;[^}]*box-shadow:\s*none;/s,
  );
});
