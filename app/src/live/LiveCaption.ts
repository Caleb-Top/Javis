export type LiveCaption = {
  begin(requestId: string, text?: string): void;
  setText(text: string, requestId?: string): void;
  append(delta: string, requestId?: string): void;
};

export function createLiveCaption(element: HTMLElement, onExpand: () => void): LiveCaption {
  let activeRequest = "";
  let fullText = "";
  let hasResponse = false;
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
    begin(requestId, text = "正在理解") {
      activeRequest = requestId;
      fullText = text;
      hasResponse = false;
      render();
    },
    setText(text, requestId = "") {
      activeRequest = requestId;
      fullText = text;
      hasResponse = Boolean(requestId);
      render();
    },
    append(delta, requestId = "") {
      if (requestId && requestId !== activeRequest) return;
      if (!hasResponse) fullText = "";
      fullText += delta;
      hasResponse = true;
      render();
    },
  };
}
