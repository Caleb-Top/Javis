export const RUNTIME_ACCESS_SCOPES = [
  "conversation",
  "diagnostics.read",
  "life.read",
  "playback",
  "voice.capture",
] as const;

export const MEMORY_RUNTIME_ACCESS_SCOPES = [
  "memory.read",
  "memory.manage",
  "memory.delete",
  "memory.migrate",
] as const;

export const MEMORY_DESKTOP_RUNTIME_ACCESS_SCOPES = [
  "conversation",
  ...MEMORY_RUNTIME_ACCESS_SCOPES,
] as const;

export type RuntimeAccessScope =
  | typeof RUNTIME_ACCESS_SCOPES[number]
  | typeof MEMORY_RUNTIME_ACCESS_SCOPES[number];

export type RuntimeAccessIssueRequest = {
  clientInstanceId: string;
  scopes: RuntimeAccessScope[];
  ttlSeconds: number;
};

type RuntimeAccessGrant = {
  token: string;
  runtimeBootId: string;
  clientInstanceId: string;
  scopes: RuntimeAccessScope[];
  issuedAtEpoch: number;
  expiresAtEpoch: number;
};

export type RuntimeAccessSnapshot = {
  schemaVersion: 1;
  revision: number;
  ready: boolean;
  runtimeBootId: string;
  clientInstanceId: string;
  scopes: RuntimeAccessScope[];
  expiresAtEpoch: number;
};

export type RuntimeAccessProvider = {
  ensure(force?: boolean): Promise<RuntimeAccessSnapshot>;
  tokenForScope(scope: RuntimeAccessScope): string;
  headers(scope: RuntimeAccessScope): Record<string, string>;
  webSocketProtocols(scope: RuntimeAccessScope): string[];
  snapshot(): RuntimeAccessSnapshot;
  subscribe(listener: (snapshot: RuntimeAccessSnapshot) => void): () => void;
  dispose(): void;
};

type RuntimeAccessProviderOptions = {
  clientInstanceId: string;
  issue?(request: RuntimeAccessIssueRequest): Promise<string>;
  now?(): number;
  ttlSeconds?: number;
  refreshSkewSeconds?: number;
  scopes?: readonly RuntimeAccessScope[];
};

const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43,256}$/;
const IDENTIFIER_PATTERN = /^[^\u0000-\u001f\u007f]{1,256}$/;

async function issueFromTauri(request: RuntimeAccessIssueRequest): Promise<string> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("issue_runtime_capability", {
    clientInstanceId: request.clientInstanceId,
    scopes: request.scopes,
    ttlSeconds: request.ttlSeconds,
  });
}

function parseGrant(
  raw: string,
  request: RuntimeAccessIssueRequest,
  now: number,
): RuntimeAccessGrant {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("runtime capability response is not valid JSON");
  }
  if (!value || typeof value !== "object") {
    throw new Error("runtime capability response is invalid");
  }
  const response = value as Record<string, unknown>;
  const token = String(response.token || "");
  const runtimeBootId = String(response.runtime_boot_id || "").trim();
  const clientInstanceId = String(response.client_instance_id || "").trim();
  const scopes = Array.isArray(response.scopes)
    ? response.scopes.map((scope) => String(scope))
    : [];
  const issuedAtEpoch = Number(response.issued_at_epoch);
  const expiresAtEpoch = Number(response.expires_at_epoch);
  if (
    response.ok !== true
    || !TOKEN_PATTERN.test(token)
    || !IDENTIFIER_PATTERN.test(runtimeBootId)
    || clientInstanceId !== request.clientInstanceId
    || scopes.length !== request.scopes.length
    || scopes.some((scope, index) => scope !== request.scopes[index])
    || !Number.isFinite(issuedAtEpoch)
    || !Number.isFinite(expiresAtEpoch)
    || issuedAtEpoch >= expiresAtEpoch
    || expiresAtEpoch <= now
  ) {
    throw new Error("runtime capability response failed validation");
  }
  return {
    token,
    runtimeBootId,
    clientInstanceId,
    scopes: [...request.scopes],
    issuedAtEpoch,
    expiresAtEpoch,
  };
}

function boundedClientId(value: string): string {
  const clientId = String(value || "").trim();
  if (!IDENTIFIER_PATTERN.test(clientId) || clientId.length > 128) {
    throw new Error("runtime access requires a bounded client instance id");
  }
  return clientId;
}

export function createRuntimeAccessProvider(
  options: RuntimeAccessProviderOptions,
): RuntimeAccessProvider {
  const clientInstanceId = boundedClientId(options.clientInstanceId);
  const issue = options.issue ?? issueFromTauri;
  const now = options.now ?? (() => Date.now() / 1000);
  const ttlSeconds = options.ttlSeconds ?? 300;
  const refreshSkewSeconds = options.refreshSkewSeconds ?? 30;
  const requestedScopes = options.scopes
    ? [...options.scopes]
    : [...RUNTIME_ACCESS_SCOPES];
  const allowedScopes = new Set<RuntimeAccessScope>([
    ...RUNTIME_ACCESS_SCOPES,
    ...MEMORY_RUNTIME_ACCESS_SCOPES,
  ]);
  if (
    requestedScopes.length < 1
    || requestedScopes.length > allowedScopes.size
    || new Set(requestedScopes).size !== requestedScopes.length
    || requestedScopes.some((scope) => !allowedScopes.has(scope))
  ) {
    throw new Error("runtime access scopes must be unique supported values");
  }
  if (!Number.isInteger(ttlSeconds) || ttlSeconds < 1 || ttlSeconds > 300) {
    throw new Error("runtime capability TTL must be between 1 and 300 seconds");
  }
  if (!Number.isFinite(refreshSkewSeconds) || refreshSkewSeconds < 0) {
    throw new Error("runtime capability refresh skew must be non-negative");
  }

  const listeners = new Set<(snapshot: RuntimeAccessSnapshot) => void>();
  let current: RuntimeAccessGrant | null = null;
  let revision = 0;
  let pending: Promise<RuntimeAccessSnapshot> | null = null;
  let refreshTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
  let disposed = false;

  function isUsable(scope?: RuntimeAccessScope): boolean {
    return Boolean(
      current
      && current.expiresAtEpoch > now()
      && (!scope || current.scopes.includes(scope)),
    );
  }

  function snapshot(): RuntimeAccessSnapshot {
    return {
      schemaVersion: 1,
      revision,
      ready: isUsable(),
      runtimeBootId: current?.runtimeBootId ?? "",
      clientInstanceId,
      scopes: current ? [...current.scopes] : [],
      expiresAtEpoch: current?.expiresAtEpoch ?? 0,
    };
  }

  function publish(): RuntimeAccessSnapshot {
    const next = snapshot();
    listeners.forEach((listener) => listener({ ...next, scopes: [...next.scopes] }));
    return next;
  }

  function clearRefreshTimer(): void {
    if (refreshTimer === null) return;
    globalThis.clearTimeout(refreshTimer);
    refreshTimer = null;
  }

  function handleRefreshFailure(): void {
    if (disposed) return;
    const stillUsable = isUsable();
    if (!stillUsable && current) {
      current = null;
      revision += 1;
      publish();
    }
    clearRefreshTimer();
    refreshTimer = globalThis.setTimeout(() => {
      refreshTimer = null;
      void ensure(true).catch(handleRefreshFailure);
    }, stillUsable ? 1000 : 5000);
  }

  function scheduleRefresh(): void {
    clearRefreshTimer();
    if (!current || disposed) return;
    const delaySeconds = Math.max(
      0.25,
      current.expiresAtEpoch - now() - refreshSkewSeconds,
    );
    refreshTimer = globalThis.setTimeout(() => {
      refreshTimer = null;
      void ensure(true).catch(handleRefreshFailure);
    }, delaySeconds * 1000);
  }

  async function ensure(force = false): Promise<RuntimeAccessSnapshot> {
    if (disposed) throw new Error("runtime access provider is disposed");
    if (
      !force
      && current
      && current.expiresAtEpoch - now() > refreshSkewSeconds
    ) {
      return snapshot();
    }
    if (pending) return pending;
    const request: RuntimeAccessIssueRequest = {
      clientInstanceId,
      scopes: [...requestedScopes],
      ttlSeconds,
    };
    pending = issue(request)
      .then((raw) => {
        if (disposed) throw new Error("runtime access provider is disposed");
        current = parseGrant(raw, request, now());
        revision += 1;
        scheduleRefresh();
        return publish();
      })
      .finally(() => {
        pending = null;
      });
    return pending;
  }

  function tokenForScope(scope: RuntimeAccessScope): string {
    return isUsable(scope) ? current!.token : "";
  }

  function headers(scope: RuntimeAccessScope): Record<string, string> {
    const token = tokenForScope(scope);
    return token ? { "X-Javis-Runtime-Capability": token } : {};
  }

  function webSocketProtocols(scope: RuntimeAccessScope): string[] {
    const token = tokenForScope(scope);
    return token
      ? ["javis-runtime-v1", `javis-capability.${token}`]
      : [];
  }

  function subscribe(listener: (snapshot: RuntimeAccessSnapshot) => void): () => void {
    listeners.add(listener);
    listener(snapshot());
    return () => listeners.delete(listener);
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    clearRefreshTimer();
    current = null;
    listeners.clear();
  }

  return {
    ensure,
    tokenForScope,
    headers,
    webSocketProtocols,
    snapshot,
    subscribe,
    dispose,
  };
}
