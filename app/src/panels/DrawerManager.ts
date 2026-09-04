export type DrawerName = "conversations" | "control" | "memory";

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

  function focusableElements(drawer: HTMLElement): HTMLElement[] {
    return Array.from(drawer.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((element) => !element.closest("[hidden], [aria-hidden=\"true\"]"));
  }

  function close(): void {
    if (!activeDrawer) return;
    const drawer = drawers.get(activeDrawer);
    drawer?.setAttribute("data-open", "false");
    drawer?.setAttribute("aria-hidden", "true");
    if (drawer) drawer.inert = true;
    root.dataset.open = "false";
    activeDrawer = null;
    restoreFocus?.focus();
    restoreFocus = null;
  }

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || !activeDrawer) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const drawer = drawers.get(activeDrawer);
    if (!drawer) return;
    const focusable = focusableElements(drawer);
    if (!focusable.length) {
      event.preventDefault();
      drawer.focus();
      return;
    }
    const current = document.activeElement;
    const currentIndex = focusable.indexOf(current as HTMLElement);
    const nextIndex = event.shiftKey
      ? currentIndex <= 0 ? focusable.length - 1 : currentIndex - 1
      : currentIndex < 0 || currentIndex === focusable.length - 1 ? 0 : currentIndex + 1;
    if (currentIndex < 0 || nextIndex === 0 || nextIndex === focusable.length - 1) {
      event.preventDefault();
      focusable[nextIndex].focus();
    }
  });

  return {
    register(name, element) {
      drawers.set(name, element);
      element.dataset.open = "false";
      element.setAttribute("aria-hidden", "true");
      element.tabIndex = -1;
      element.inert = true;
      root.appendChild(element);
      element.querySelector<HTMLButtonElement>(".drawer-close")?.addEventListener("click", close);
    },
    open(name, trigger = null) {
      if (activeDrawer) {
        const previous = drawers.get(activeDrawer);
        previous?.setAttribute("data-open", "false");
        previous?.setAttribute("aria-hidden", "true");
        if (previous) previous.inert = true;
      }
      activeDrawer = name;
      restoreFocus = trigger;
      root.dataset.open = "true";
      const drawer = drawers.get(name);
      drawer?.setAttribute("data-open", "true");
      drawer?.setAttribute("aria-hidden", "false");
      if (drawer) {
        drawer.inert = false;
        (focusableElements(drawer)[0] ?? drawer).focus();
      }
    },
    close,
    active: () => activeDrawer,
  };
}
