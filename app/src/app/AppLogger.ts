import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "../bridge/sidecarClient";

export type AppLogLevel = "debug" | "info" | "warn" | "error";

export const AppLogger = {
  write(level: AppLogLevel, component: string, message: string): void {
    if (!isTauriRuntime()) {
      console[level === "debug" ? "debug" : level](`[${component}] ${message}`);
      return;
    }
    void invoke("write_app_log", { level, component, message }).catch(() => undefined);
  },
};
