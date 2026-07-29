import type { LiveState } from "../live/liveState";

export type StateSignal = {
  source: "sidecar" | "websocket" | "voice" | "tool" | "permission" | "ui";
  state: LiveState;
  timestamp: number;
  requestId?: string;
  detail?: string;
  terminal?: boolean;
};

export type RuntimeSnapshot = {
  state: LiveState;
  source: StateSignal["source"];
  detail: string;
  requestId?: string;
  updatedAt: number;
};

export type RuntimeStateListener = (snapshot: RuntimeSnapshot) => void;

const SURFACE_STATE_LABELS: Record<LiveState, string> = {
  idle: "待命",
  listening: "聆听中",
  thinking: "思考中",
  speaking: "回答中",
  executing: "执行中",
  blocked: "等待授权",
  error: "需要检查",
  offline: "后端离线",
};

const DETAIL_ALIASES: Record<string, string> = {
  tool_start: "调用工具",
  tool_call: "调用工具",
  tool_result: "工具已完成",
  text_delta: "生成回答",
  confirm_required: "等待确认",
  done: "任务完成",
};

function readableDetail(rawDetail: string): string {
  let detail = rawDetail.trim();
  if (!detail) return "";
  if ((detail.startsWith("{") && detail.endsWith("}")) ||
      (detail.startsWith("[") && detail.endsWith("]"))) {
    try {
      const parsed = JSON.parse(detail) as Record<string, unknown>;
      detail = String(
        parsed.text ||
        parsed.detail ||
        parsed.tool ||
        parsed.name ||
        parsed.status ||
        "",
      ).trim();
    } catch {
      return "";
    }
  }
  const alias = DETAIL_ALIASES[detail.toLowerCase()];
  return alias || detail.replace(/\s+/g, " ");
}

function truncateStatus(text: string, limit = 34): string {
  const characters = Array.from(text);
  return characters.length <= limit
    ? text
    : `${characters.slice(0, limit - 1).join("")}…`;
}

export function formatSurfaceStatus(
  snapshot: Pick<RuntimeSnapshot, "state" | "detail">,
): string {
  const label = SURFACE_STATE_LABELS[snapshot.state];
  const detail = readableDetail(snapshot.detail || "");
  const normalized = detail.replace(/^Javis\s*/i, "");
  const redundant = new Set([
    label,
    "已待命",
    "正在待命",
    "我在听",
    "正在思考",
    "正在回答",
    "正在执行",
    "后端离线",
  ]);
  return truncateStatus(
    !normalized || redundant.has(normalized)
      ? label
      : `${label} · ${normalized}`,
  );
}
