export type ModelRouteName = "live" | "code";

export function getEditableModelRoute(
  requestedRoute: ModelRouteName,
  shareLiveCode: boolean,
): ModelRouteName {
  return shareLiveCode ? "live" : requestedRoute;
}

export function resolveHydratedModelRoute(
  currentRoute: ModelRouteName,
  entryRoute: ModelRouteName,
  alreadyHydrated: boolean,
): ModelRouteName {
  return alreadyHydrated ? currentRoute : entryRoute;
}

export function buildModelRoutingPayload<
  Routes extends Record<ModelRouteName, unknown>,
>(
  routes: Routes,
  activeRoute: ModelRouteName,
  shareLiveCode: boolean,
): {
  active_route: ModelRouteName;
  share_live_code: boolean;
  routes: Routes;
} {
  return {
    active_route: activeRoute,
    share_live_code: shareLiveCode,
    routes,
  };
}

export function enableSharedModelRoute<
  Routes extends Record<ModelRouteName, unknown>,
>(routes: Routes): Routes {
  return {
    ...routes,
    code: structuredClone(routes.live),
  } as Routes;
}

export type ModelInstallProgressState =
  | "idle"
  | "planned"
  | "approved"
  | "verifying"
  | "staging"
  | "running"
  | "pausing"
  | "paused"
  | "resuming"
  | "cancelling"
  | "committing"
  | "rolling_back"
  | "completed"
  | "failed"
  | "cancelled";

export type ModelInstallControlAvailability = {
  pause: boolean;
  resume: boolean;
  cancel: boolean;
};

export function getModelInstallControlAvailability(progress: {
  state: ModelInstallProgressState;
  jobId: string;
  cancellable: boolean;
  pausable: boolean;
}): ModelInstallControlAvailability {
  if (!progress.jobId) return { pause: false, resume: false, cancel: false };
  return {
    pause: progress.pausable && !["pausing", "paused", "cancelling"].includes(progress.state),
    resume: progress.state === "paused",
    cancel: progress.cancellable && progress.state !== "cancelling",
  };
}

export type InstalledLocalProfile = {
  model: string;
  base_url: string;
};

type RouteWithLocalProfile = {
  local: InstalledLocalProfile;
};

export function isActiveModelInstallState(
  state: ModelInstallProgressState,
): boolean {
  return !["idle", "completed", "failed", "cancelled"].includes(state);
}

export function getModelInstallTargets(
  shareLiveCode: boolean,
  activeRoute: ModelRouteName,
): ModelRouteName[] {
  return shareLiveCode ? ["live", "code"] : [activeRoute];
}

export function applyInstalledLocalProfile<
  Routes extends Record<ModelRouteName, RouteWithLocalProfile>,
>(
  routes: Routes,
  targets: readonly ModelRouteName[],
  profile: InstalledLocalProfile,
): Routes {
  const next = { ...routes };
  const selected = new Set(targets);
  for (const routeName of ["live", "code"] as const) {
    if (!selected.has(routeName)) continue;
    next[routeName] = {
      ...routes[routeName],
      local: { ...profile },
    };
  }
  return next;
}
