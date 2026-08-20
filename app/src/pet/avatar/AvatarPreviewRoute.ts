type PreviewLocation = Pick<Location, "hostname" | "search">;

export function isLocalAvatarPreviewLocation(location: PreviewLocation): boolean {
  const local = location.hostname === "127.0.0.1" || location.hostname === "localhost";
  return local && new URLSearchParams(location.search).get("avatar-preview") === "1";
}
