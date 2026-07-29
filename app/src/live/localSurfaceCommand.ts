export type LocalSurfaceCommand =
  | "code"
  | "settings"
  | "diagnostics"
  | "live";

const LOCAL_SURFACE_COMMANDS = new Set<LocalSurfaceCommand>([
  "code",
  "settings",
  "diagnostics",
  "live",
]);

export function isLocalSurfaceCommand(
  value: unknown,
): value is LocalSurfaceCommand {
  return typeof value === "string" &&
    LOCAL_SURFACE_COMMANDS.has(value as LocalSurfaceCommand);
}

function normalizeCommand(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[，。！？,.!?：:；;'"“”‘’\s]/g, "");
}

const COMMAND_PATTERNS: Array<{
  command: LocalSurfaceCommand;
  pattern: RegExp;
}> = [
  {
    command: "code",
    pattern: /^(?:请)?(?:打开|进入|切换到|显示|调出)?(?:code|代码)(?:页面|界面|模式|工作台)?$/,
  },
  {
    command: "settings",
    pattern: /^(?:请)?(?:打开|进入|切换到|显示|调出)?(?:设置|setting|settings)(?:页面|界面|面板)?$/,
  },
  {
    command: "diagnostics",
    pattern: /^(?:请)?(?:打开|进入|显示|运行)?(?:诊断|自检|diagnostics?)(?:页面|界面|面板)?$/,
  },
  {
    command: "live",
    pattern: /^(?:请)?(?:打开|进入|切换到|返回|回到|显示)?(?:live|交流|交流球|主界面)(?:页面|界面|模式)?$/,
  },
];

export function parseLocalSurfaceCommand(
  text: string,
): LocalSurfaceCommand | null {
  const normalized = normalizeCommand(text);
  const matched = COMMAND_PATTERNS.find(({ pattern }) =>
    pattern.test(normalized)
  );
  return matched?.command ?? null;
}
