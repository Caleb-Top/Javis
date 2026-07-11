# Javis vs Codex Computer Use — 完整对比表

## 一、工具 API 面

| 功能 | Codex 工具 | 参数 | Javis 工具 | 状态 |
|------|-----------|------|-----------|------|
| **截图** | `screenshot` | window_id | `screenshot` | ✅ |
| **截图+UI+边界** | `get_window_state` | window_id, include_text, include_screenshot | `get_window_state` | ✅ |
| **鼠标点击** | `click` | x, y, click_count, mouse_button | `mouse_click` | ✅ |
| **按控件点击** | `click_element` | element_index | `click_element` | ✅ |
| **拖拽** | `drag` | from_x, from_y, to_x, to_y | `mouse_drag` | ✅ |
| **滚动** | `scroll` | scrollX, scrollY | `mouse_scroll` | ✅ |
| **控件内滚动** | `scroll_element` | element_index, direction | `scroll_element` | ✅ |
| **键盘输入** | `type_text` | text | `keyboard_type` | ✅ |
| **按键** | `press_key` | keys (数组) | `keyboard_press` + KEY_MAP | ✅ |
| **填值** | `set_value` | element_index, value | `set_value` | ✅ |
| **等待** | `wait` | ms (毫秒) | `wait` | ✅ |
| **打开应用** | `launch_app` | app_id / app | `open_app`, `launch_app` (新增) | ✅ |
| **聚焦窗口** | `activate_window` | window_id | `focus_window` | ✅ |
| **列窗口** | `list_windows` | — | `list_windows` | ✅ |
| **列应用** | `list_apps` | — | `find_app` | ✅ |
| **读 UI** | `get_window_state` → accessibility | — | `read_ui_window` | ✅ |
| **前台窗口** | 内部 | — | `get_foreground_window` | ✅ |
| **次级操作** | `perform_secondary_action` | element_index, action | `perform_secondary_action` | ✅ |
| **终止** | `end_turn` | — | ❌ 无 | **缺失** |

## 二、Codex 有且 Javis 已补齐的 3 项 ✅

| # | 工具 | 状态 | 版本 |
|---|------|------|------|
| 1 | `scroll_element(element_index, direction)` | ✅ 已实现 | v47 |
| 2 | `set_value(element_index, value)` | ✅ 已实现 | v47 |
| 3 | `perform_secondary_action(element_index, action)` | ✅ 已实现 | v47 |

## 三、Javis 有但 Codex 没有的

| 功能 | 说明 |
|------|------|
| **技能系统** | 6 大类技能可切换 |
| **LLM 接入** | DeepSeek/GLM/Kimi/千问/OAI/Claude/本地 |
| **对话持久化** | 文件系统记忆，重启不丢 |
| **拖拽** | `mouse_drag`（Codex 也有） |
| **窗口缓存验证** | 操作前自动检查窗口边界 |
| **循环检测** | 同一工具调用 3 次以上自动中断 |
| **action 计数器** | 30 次上限防死循环 |
| **语音 TTS/STT** | 语音合成+识别 |
| **前端 UI** | 浏览器 Web 界面 |
| **.exe 技能编译** | 每个技能可单独打包为独立 exe |

## 四、质量/流程差距

| 维度 | Codex | Javis | 差距 |
|------|-------|-------|------|
| **截图引擎** | Direct3D GPU | mss CPU | 速度 |
| **UI 自动化** | UIA (原生) | Win32 EnumChildWindows | 深度 |
| **操作验证** | 每次操作前 verify window | 提示词建议但无强制 | 可靠性 |
| **用户打断** | Escape 键检测 | ❌ 无 | 安全性 |
| **应用风险** | low/high 分级 | ❌ 无 | 安全 |
| **拾取精确** | UIA → HWND → GetWindowRect | Win32 GetWindowRect | 精度一致 |
| **进程名匹配** | EnumProcessModules | GetWindowModuleFileNameW | 一致 |
