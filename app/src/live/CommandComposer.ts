export const MAX_ROWS = 6;
const MAX_HEIGHT = 144;

export type CommandComposer = {
  focus(): void;
  setConnected(connected: boolean): void;
};

export function createCommandComposer(
  form: HTMLFormElement,
  textarea: HTMLTextAreaElement,
  onSubmit: (text: string) => boolean | Promise<boolean>,
): CommandComposer {
  let isComposing = false;
  const sendButton = form.querySelector<HTMLButtonElement>(".send-btn");

  function resize(): void {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_HEIGHT)}px`;
    textarea.style.overflowY = textarea.scrollHeight > MAX_HEIGHT ? "auto" : "hidden";
    textarea.dataset.rows = String(Math.min(MAX_ROWS, Math.max(1, textarea.value.split("\n").length)));
  }

  async function submit(): Promise<void> {
    const text = textarea.value.trim();
    if (!text) return;
    sendButton?.setAttribute("aria-busy", "true");
    const accepted = await onSubmit(text);
    sendButton?.removeAttribute("aria-busy");
    if (accepted) {
      textarea.value = "";
      resize();
    }
  }

  textarea.addEventListener("compositionstart", () => { isComposing = true; });
  textarea.addEventListener("compositionend", () => { isComposing = false; });
  textarea.addEventListener("input", resize);
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !isComposing) {
      event.preventDefault();
      void submit();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submit();
  });
  resize();

  return {
    focus: () => textarea.focus(),
    setConnected(connected) {
      if (sendButton) {
        sendButton.dataset.connected = String(connected);
        sendButton.title = connected ? "发送" : "等待连接，文字将进入有界队列";
      }
    },
  };
}
