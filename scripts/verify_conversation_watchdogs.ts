#!/usr/bin/env node

import {
  backendClientTimeoutOverrides,
  watchdogTiming,
} from "./conversation_watchdog_timing.ts";

type TimeoutPhase = "accepted" | "first-response" | "overall";

type Scenario = {
  name: string;
  trigger: string;
  phase: TimeoutPhase;
  expectedMs: number;
};

const SCENARIOS: Scenario[] = [
  {
    name: "no-ack",
    trigger: "/__javis_stall__/no-ack",
    phase: "accepted",
    expectedMs: 3000,
  },
  {
    name: "after-ack",
    trigger: "/__javis_stall__/after-ack",
    phase: "first-response",
    expectedMs: 15000,
  },
  {
    name: "after-delta",
    trigger: "/__javis_stall__/after-delta",
    phase: "overall",
    expectedMs: 60000,
  },
];

function argument(name: string, fallback = ""): string {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] ?? "") : fallback;
}

function requireLoopbackOrigin(value: string): string {
  const parsed = new URL(value);
  if (parsed.protocol !== "http:") throw new Error("watchdog origin must use http");
  const host = parsed.hostname.replace(/^\[|\]$/g, "");
  if (host !== "127.0.0.1" && host !== "::1") {
    throw new Error("watchdog origin must be loopback");
  }
  if (!parsed.port) throw new Error("watchdog origin requires an explicit port");
  return parsed.origin;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function withTimeout<T>(
  promise: Promise<T>,
  milliseconds: number,
  label: string,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out`)), milliseconds);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

const origin = requireLoopbackOrigin(argument("--origin"));
const timeScale = Number(argument("--time-scale", "1"));
if (!Number.isFinite(timeScale) || timeScale <= 0 || timeScale > 1) {
  throw new Error("time scale must be greater than zero and at most one");
}

Object.defineProperty(globalThis, "window", {
  configurable: true,
  value: globalThis,
});
Object.defineProperty(globalThis, "document", {
  configurable: true,
  value: { querySelector: () => null },
});

const { createBackendClient } = await import("../app/src/bridge/backendClient.ts");

async function runScenario(scenario: Scenario, index: number) {
  const { acceptedMs, firstResponseMs, overallMs } = watchdogTiming(timeScale);
  let resolveConnected!: () => void;
  const connected = new Promise<void>((resolve) => {
    resolveConnected = resolve;
  });
  let resolveFailure!: (value: Record<string, unknown>) => void;
  const localFailure = new Promise<Record<string, unknown>>((resolve) => {
    resolveFailure = resolve;
  });
  const observedTypes: string[] = [];
  const client = createBackendClient({
    sessionId: `watchdog-${process.pid}-${index}-${Date.now()}`,
    backendOrigin: origin,
    ...backendClientTimeoutOverrides(timeScale),
    onConnection: (snapshot) => {
      if (snapshot.websocket) resolveConnected();
    },
    onEvent: (event) => {
      observedTypes.push(String(event.type || "unknown"));
      if (event.type === "request.failed" && event.local === true) {
        resolveFailure(event);
      }
    },
  });

  try {
    client.connect();
    await withTimeout(connected, 5000, `${scenario.name} connection`);
    const startedAt = performance.now();
    const requestId = client.send(scenario.trigger);
    if (!requestId) throw new Error(`${scenario.name} request was not submitted`);
    const event = await withTimeout(
      localFailure,
      overallMs + Math.max(5000, Math.round(5000 * timeScale)),
      `${scenario.name} local terminal`,
    );
    const elapsedMs = Math.round(performance.now() - startedAt);
    const phase = String((event.payload as Record<string, unknown> | undefined)?.phase || "");
    if (phase !== scenario.phase) {
      throw new Error(
        `${scenario.name} ended in ${phase || "no phase"}, expected ${scenario.phase}; events=${observedTypes.join(",")}`,
      );
    }

    const expectedMs = scenario.phase === "accepted"
      ? acceptedMs
      : scenario.phase === "first-response"
        ? firstResponseMs
        : overallMs;
    const earlyTolerance = timeScale === 1
      ? 750
      : Math.max(15, Math.round(expectedMs * 0.4));
    const lateTolerance = timeScale === 1
      ? 5000
      : Math.max(250, expectedMs);
    if (elapsedMs < Math.max(0, expectedMs - earlyTolerance)) {
      throw new Error(`${scenario.name} fired too early at ${elapsedMs} ms`);
    }
    if (elapsedMs > expectedMs + lateTolerance) {
      throw new Error(`${scenario.name} fired too late at ${elapsedMs} ms`);
    }

    const reliability = client.reliabilitySnapshot();
    if (reliability.timeoutCounts[scenario.phase] !== 1) {
      throw new Error(`${scenario.name} timeout count is not exactly one`);
    }
    const totalTimeouts = Object.values(reliability.timeoutCounts)
      .reduce((total, value) => total + value, 0);
    if (totalTimeouts !== 1) throw new Error(`${scenario.name} counted multiple watchdogs`);
    if (client.activeRequestId() !== null || client.queueSize() !== 0) {
      throw new Error(`${scenario.name} left frontend request state behind`);
    }

    await delay(Math.max(25, Math.round(100 * timeScale)));
    return {
      name: scenario.name,
      phase: scenario.phase,
      elapsed_ms: elapsedMs,
      expected_ms: expectedMs,
      timeout_count: reliability.timeoutCounts[scenario.phase],
      request_phase: reliability.requestPhase,
      timeout_source: timeScale === 1
        ? "backend-production-defaults"
        : "scaled-harness-override",
    };
  } finally {
    client.dispose();
  }
}

const scenarios = [];
for (const [index, scenario] of SCENARIOS.entries()) {
  scenarios.push(await runScenario(scenario, index));
}
process.stdout.write(`${JSON.stringify({ scenarios })}\n`);
