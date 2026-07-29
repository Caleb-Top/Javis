import { runtimeStateCoordinator } from "../state/RuntimeStateCoordinator";

export type LiveState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "executing"
  | "blocked"
  | "error"
  | "offline";

const captions: Record<LiveState, string> = {
  idle: "Javis 已待命",
  listening: "我在听",
  thinking: "我在思考",
  speaking: "正在回答",
  executing: "正在执行",
  blocked: "等待你的授权",
  error: "遇到问题",
  offline: "后端离线"
};

export function setLiveState(state: LiveState): void {
  runtimeStateCoordinator.signal({ source: "ui", state, timestamp: Date.now(), detail: captions[state] });
}

export function liveStateCaption(state: LiveState): string {
  return captions[state];
}
