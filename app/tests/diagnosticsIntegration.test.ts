import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const diagnosticsSource = readFileSync(
  new URL("../src/panels/DiagnosticsPanel.ts", import.meta.url),
  "utf8",
);

test("diagnostics expands into a full settings-sized native surface", () => {
  const handler = mainSource.match(
    /const showDiagnosticsSurface[\s\S]*?\n\};/,
  )?.[0] || "";

  assert.match(handler, /setDesktopMode\("settings"\)/);
  assert.match(handler, /diagnostics\.open\(\)/);
});

test("all diagnostics entry points use the full-surface handler", () => {
  assert.match(mainSource, /onOpenDiagnostics: showDiagnosticsSurface/);
  assert.match(
    mainSource,
    /javis:open-diagnostics", showDiagnosticsSurface/,
  );
  assert.match(
    mainSource,
    /javis:\/\/open-diagnostics", showDiagnosticsSurface/,
  );
});

test("closing diagnostics notifies the shell so it can restore the previous mode", () => {
  assert.match(diagnosticsSource, /type DiagnosticsPanelOptions/);
  assert.match(diagnosticsSource, /options\.onClose\?\.\(\)/);
});

test("microphone diagnostics passes only when the backend measured real signal", () => {
  assert.match(diagnosticsSource, /result\.signalDetected\s*===\s*true/);
  assert.doesNotMatch(diagnosticsSource, /result\.signalDetected\s*!==\s*false/);
});
