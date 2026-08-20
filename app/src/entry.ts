import { isLocalAvatarPreviewLocation } from "./pet/avatar/AvatarPreviewRoute.ts";

if (isLocalAvatarPreviewLocation(window.location)) {
  void Promise.all([
    import("./pet/avatar/AvatarPreview.css"),
    import("./pet/avatar/AvatarPreview.ts"),
  ]).then(([, preview]) => preview.mountAvatarPreview(document.querySelector<HTMLElement>("#app")!));
} else {
  void import("./main.ts");
}
