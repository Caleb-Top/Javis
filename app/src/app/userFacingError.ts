export function toUserFacingError(reason: unknown): string {
  const message = reason instanceof Error ? reason.message : String(reason || "");
  const normalized = message.toLowerCase();
  if (
    normalized.includes("native audio stream")
    || normalized.includes("voice stream")
    || normalized.includes("websocket")
  ) {
    return "语音服务尚未就绪，Javis 正在恢复连接。";
  }
  return "界面暂时不可用，请点击恢复。";
}
