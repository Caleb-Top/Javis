export type DrawerName = "conversations" | "control";

export type DrawerManager = {
  register(name: DrawerName, element: HTMLElement): void;
  open(name: DrawerName, trigger?: HTMLElement | null): void;
  close(): void;
  active(): DrawerName | null;
};

export function createDrawerManager(root: HTMLElement): DrawerManager {
  const drawers = new Map<DrawerName, HTMLElement>();
  let activeDrawer: DrawerName | null = null;
  let restoreFocus: HTMLElement | null = null;

  function close(): void {
    if (!activeDrawer) return;
    drawers.get(activeDrawer)?.setAttribute("data-open", "false");
    root.dataset.open = "false";
    activeDrawer = null;
    restoreFocus?.focus();
    restoreFocus = null;
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && activeDrawer) close();
  });

  return {
    register(name, element) {
      drawers.set(name, element);
      element.dataset.open = "false";
      root.appendChild(element);
      element.querySelector<HTMLButtonElement>(".drawer-close")?.addEventListener("click", close);
    },
    open(name, trigger = null) {
      if (activeDrawer) drawers.get(activeDrawer)?.setAttribute("data-open", "false");
      activeDrawer = name;
      restoreFocus = trigger;
      root.dataset.open = "true";
      const drawer = drawers.get(name);
      drawer?.setAttribute("data-open", "true");
      drawer?.querySelector<HTMLElement>("input, button, textarea, select")?.focus();
    },
    close,
    active: () => activeDrawer,
  };
}
