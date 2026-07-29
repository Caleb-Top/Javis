export type LiveCaption = {
  setText(text: string, requestId?: string): void;
  append(delta: string, requestId?: string): void;
};

export function createLiveCaption(element: HTMLElement, onExpand: () => void): LiveCaption {
  let activeRequest = "";
  let fullText = "";
  element.classList.add("line-clamp");
  element.addEventListener("click", () => {
    if (fullText.length > 120) onExpand();
  });

  function render(): void {
    element.textContent = fullText || "Javis 已待命";
    element.title = fullText.length > 120 ? "展开完整回答" : "";
    element.dataset.expandable = String(fullText.length > 120);
  }

  return {
    setText(text, requestId = "") {
      activeRequest = requestId;
      fullText = text;
      render();
    },
    append(delta, requestId = "") {
      if (requestId && requestId !== activeRequest) {
        activeRequest = requestId;
        fullText = "";
      }
      fullText += delta;
      render();
    },
  };
}
