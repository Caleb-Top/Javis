export type ModelRouteName = "live" | "code";

export type ModelInstallProgressState =
  | "idle"
  | "planned"
  | "approved"
  | "verifying"
  | "staging"
  | "running"
  | "committing"
  | "rolling_back"
  | "completed"
  | "failed";

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
  return !["idle", "completed", "failed"].includes(state);
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
