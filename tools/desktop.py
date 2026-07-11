"""桌面控制工具 — 截屏、鼠标、键盘、窗口管理"""

import base64
import io
import logging
import os
import re
import ctypes
import time
from core.tool_result import ToolResult
from utils.error_messages import friendly_error

logger = logging.getLogger("tools.desktop")


# ═══════════════════════════════════════════════════════════════
# 统一智能窗口搜索 — 所有 name 匹配逻辑集中在这里
# ═══════════════════════════════════════════════════════════════

# 应用中文名 → 进程英文名 映射表
APP_ALIASES = {
    "qq": ["qqmusic", "tencent", "qq"],
    "qq音乐": ["qqmusic", "tencent"],
    "音乐": ["qqmusic", "cloudmusic", "netease"],
    "微信": ["wechat", "weixin"],
    "chrome": ["chrome", "google"],
    "谷歌": ["chrome", "google"],
    "edge": ["msedge", "edge"],
    "浏览器": ["chrome", "msedge", "firefox", "brave", "opera"],
    "firefox": ["firefox"],
    "网易云": ["cloudmusic", "netease"],
    "word": ["winword"],
    "excel": ["excel"],
    "ppt": ["powerpnt"],
    "记事本": ["notepad"],
    "计算器": ["calculator"],
    "设置": ["systemsettings", "settings"],
    "资源管理器": ["explorer"],
    "终端": ["cmd", "powershell", "windowsTerminal", "wt"],
    "cmd": ["cmd"],
    "powershell": ["powershell"],
    "vs code": ["code"],
    "code": ["code"],
    "vscode": ["code"],
    "pycharm": ["pycharm"],
    "idea": ["idea"],
    "clion": ["clion"],
}

# 需要过滤的系统窗口关键词
_NOISE_WORDS = ["program manager", "nvidia", "default ime", "msctfime ui",
                "windows input experience", "com.ccswitch", "console window",
                "settings", "设置", "windows 输入体验"]


# ── 窗口缓存 (5秒内不重复扫描, psutil 延迟加载) ──
_window_cache: list[tuple[int, str]] | None = None   # [(hwnd, title)] — 不存进程名
_window_proc_cache: dict[int, str] = {}               # {hwnd: process_name} — 按需加载
_window_cache_time: float = 0
_WINDOW_CACHE_TTL = 5.0


def _get_proc_name(hwnd: int) -> str:
    """按需获取进程名 (带缓存, 避免重复 psutil)"""
    if hwnd in _window_proc_cache:
        return _window_proc_cache[hwnd]
    try:
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        import psutil as _ps
        proc = _ps.Process(pid.value)
        name = proc.name().lower().replace(".exe", "")
        _window_proc_cache[hwnd] = name
        return name
    except Exception:
        _window_proc_cache[hwnd] = ""
        return ""


def _get_all_windows() -> list[tuple[int, str]]:
    """获取所有可见窗口 — 返回 (hwnd, title)，进程名按需惰性加载"""
    global _window_cache, _window_cache_time
    now = time.time()
    if _window_cache is not None and now - _window_cache_time < _WINDOW_CACHE_TTL:
        return _window_cache

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    titles = []

    def callback(hwnd, _):
        try:
            length = user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            title = buf.value.strip()
            if title and user32.IsWindowVisible(hwnd):
                tl = title.lower()
                if any(nw in tl for nw in _NOISE_WORDS):
                    return True
                titles.append((hwnd, title))
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    _window_cache = titles
    _window_cache_time = now
    return titles  # 只返回 (hwnd, title)，进程名在 _score_match 需要时才惰性加载


def _score_match(search: str, title: str, hwnd: int) -> int:
    """高速打分 — 优先 title 匹配, 别名提前, 早期返回. 进程名按需惰性加载"""
    s = search.lower().strip()
    t = title.lower()

    # 1. 标题包含 → 完美命中 (不需要进程名)
    if s in t:
        return 1000

    # 2. 快速别名扫标题 (最常用的匹配)
    for key, aliases in APP_ALIASES.items():
        if key in s:
            for alias in aliases:
                if alias in t:
                    return 500

    # ↓ 以下需要使用进程名，惰性加载
    p = _get_proc_name(hwnd).lower()

    # 3. 进程名匹配
    if s in p:
        return 400

    # 4. 别名扫进程名
    for key, aliases in APP_ALIASES.items():
        if key in s:
            for alias in aliases:
                if alias in p:
                    return 300

    # 5. 快速字符串检查 (不用 re 和循环)
    if len(s) >= 2:
        for i in range(len(t) - len(s) + 1):
            if t[i:i+len(s)] == s:
                return 200  # 子串在标题中 (顺序一致)

    # 6. 关键词拆分 (用简单 split, 只对标题)
    for sep in (' ', '-', '_', '（', '(', '）', ')', '·'):
        if sep in s:
            parts = [x for x in s.split(sep) if len(x) >= 2]
            match_count = 0
            for p_part in parts:
                if p_part in t:
                    match_count += 1
            if match_count >= len(parts) * 0.6:
                return 150

    # 7. 单个汉字 (很简单, 快速)
    for ch in s:
        if '一' <= ch <= '鿿' and ch in t:
            return 100

    return 0


def _smart_match_window(name: str) -> tuple[int, str] | None:
    """智能搜索窗口: 前台优先 + 缓存 + 打分, 用时 &lt;5ms"""
    s = name.lower().strip()
    user32 = ctypes.windll.user32

    # ★ 快速路径1: 前台窗口是不是目标?
    fg_hwnd = user32.GetForegroundWindow()
    if fg_hwnd:
        length = user32.GetWindowTextLengthW(fg_hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(fg_hwnd, buf, length)
        fg_title = buf.value.strip()
        if fg_title and _score_match(name, fg_title, fg_hwnd) >= 400:
            logger.info(f"前台窗口匹配: \"{fg_title}\"")
            return (fg_hwnd, fg_title)

    # ★ 快速路径2: 从缓存/扫描找
    wins = _get_all_windows()
    if not wins:
        return None

    best_score = 0
    best_match = None

    for hwnd, title in wins:
        score = _score_match(name, title, hwnd)
        if score > best_score:
            best_score = score
            best_match = (hwnd, title)
            if score >= 1000:
                return best_match

    if best_match:
        logger.info(f"窗口匹配: \"{name}\" → \"{best_match[1]}\" (score={best_score})")
        return best_match
    return None


def _filter_candidates(name: str) -> list[tuple[int, str]]:
    return _get_all_windows()


# ═══════════════════════════════════════════════════════════════
# 截图
# ═══════════════════════════════════════════════════════════════

def screenshot(area: list = None, **kwargs) -> ToolResult:
    """截取全屏并返回图片 (缩放至最大1024px, 省token)"""
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            if area and len(area) >= 4:
                monitor = {"top": int(area[1]), "left": int(area[0]), "width": int(area[2]), "height": int(area[3])}
            else:
                monitor = sct.monitors[1]
            img = sct.grab(monitor)

            from PIL import Image
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            orig_w, orig_h = pil_img.width, pil_img.height

            # 缩放至最大 1024px (节省 token)
            MAX_DIM = 1024
            if orig_w > MAX_DIM or orig_h > MAX_DIM:
                ratio = min(MAX_DIM / orig_w, MAX_DIM / orig_h)
                new_w, new_h = int(orig_w * ratio), int(orig_h * ratio)
                pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
            else:
                new_w, new_h = orig_w, orig_h

            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode()

            return ToolResult(
                success=True,
                data=f"截图成功: {orig_w}x{orig_h} → {new_w}x{new_h} ({len(b64)//1024}KB)",
                image=b64,
            )
    except ImportError as e:
        return ToolResult.failure(friendly_error(e))


def screenshot_region(x: int, y: int, w: int, h: int) -> ToolResult:
    """截取屏幕指定区域"""
    try:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            monitor = {"top": y, "left": x, "width": w, "height": h}
            img = sct.grab(monitor)
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()

            return ToolResult.success(
                f"区域截图 ({x},{y} {w}x{h}) 成功, base64 长度 {len(b64)}"
            )
    except ImportError as e:
        return ToolResult.failure(friendly_error(e))


# ---------------------------------------------------------------------------
# 鼠标控制
# ---------------------------------------------------------------------------

def mouse_click(x: int = None, y: int = None, position: list = None, button: str = "left", **kwargs) -> ToolResult:
    """鼠标点击 (x,y) 或 position:[x,y]。也支持 kwargs 传坐标"""
    # 兼容各种参数格式: position, pos, 直接 x/y, 列表
    if position and len(position) >= 2:
        x, y = int(position[0]), int(position[1])
    if x is None and y is None and "pos" in kwargs:
        x, y = int(kwargs["pos"][0]), int(kwargs["pos"][1])
    if x is None or y is None:
        return ToolResult.failure("请提供 x,y 坐标 或 position:[x,y]")
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        x, y = int(x), int(y)
        pyautogui.click(x, y, button=button)
        cap_result = screenshot()
        return ToolResult.success(
            f"鼠标 {button}键 点击 ({x}, {y}) 成功",
            screenshot=cap_result.data if cap_result.success else ""
        )
    except pyautogui.FailSafeException:
        return ToolResult.success(f"鼠标点击完成 (安全模式已触发)")
    except ValueError as e:
        return ToolResult.failure(f"坐标参数无效: x={x}, y={y}")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def mouse_double_click(x: int = None, y: int = None, position: list = None, **kwargs) -> ToolResult:
    """鼠标双击"""
    if position and len(position) >= 2:
        x, y = int(position[0]), int(position[1])
    if x is None or y is None:
        return ToolResult.failure("请提供坐标")
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.click(int(x), int(y), clicks=2)
        return ToolResult.success(f"鼠标双击 ({x}, {y}) 成功")
    except pyautogui.FailSafeException:
        return ToolResult.success("鼠标双击完成")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def mouse_scroll(amount: int = 3) -> ToolResult:
    """鼠标滚动, 正数向上, 负数向下"""
    try:
        import pyautogui
        pyautogui.scroll(amount)
        return ToolResult.success(f"鼠标滚动 {amount} 格")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def mouse_move(x: int = None, y: int = None, position: list = None, **kwargs) -> ToolResult:
    """移动鼠标到坐标"""
    if position and len(position) >= 2:
        x, y = int(position[0]), int(position[1])
    if x is None or y is None:
        return ToolResult.failure("请提供坐标")
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.moveTo(int(x), int(y))
        return ToolResult.success(f"鼠标移动到 ({x}, {y})")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int, button: str = "left") -> ToolResult:
    """从 (start_x,start_y) 拖拽到 (end_x,end_y) (用于拖动滑块/文件/滚动条)"""
    try:
        import pyautogui
        pyautogui.moveTo(start_x, start_y)
        pyautogui.drag(end_x - start_x, end_y - start_y, button=button, duration=0.3)
        return ToolResult.success(f"拖拽: ({start_x},{start_y}) → ({end_x},{end_y})")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


# ---------------------------------------------------------------------------
# 键盘控制
# ---------------------------------------------------------------------------

def keyboard_type(text: str) -> ToolResult:
    """键盘输入文本"""
    if not text or not text.strip():
        return ToolResult.failure("输入文本为空")
    try:
        import pyautogui
        pyautogui.write(text, interval=0.02)     # 模拟人类打字速度
        return ToolResult.success(f"键盘输入: {text[:100]}")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def wait(seconds: float = 1.0) -> ToolResult:
    """等待指定秒数 (用于操作间隔)"""
    import time
    time.sleep(float(seconds))
    return ToolResult.success(f"已等待 {seconds} 秒")


# ---------------------------------------------------------------------------
# 窗口管理 (Windows)
# ---------------------------------------------------------------------------

def list_windows() -> ToolResult:
    """列出当前活动窗口 (已滤除系统/后台窗口)"""
    try:
        wins = _get_all_windows()
        if wins:
            return ToolResult.success("当前窗口:\n" + "\n".join(f"  {t}" for _, t in wins[:10]))
        return ToolResult.success("未检测到打开的窗口")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


# ── 窗口边界缓存 (用于操作前验证) ──
_window_bounds_cache: dict = {}


def _get_window_rect(hwnd: int) -> dict | None:
    """获取窗口边界"""
    import ctypes
    try:
        user32 = ctypes.windll.user32
        rect = ctypes.create_string_buffer(16)
        if user32.GetWindowRect(hwnd, rect):
            import struct
            l, t, r, b = struct.unpack('4i', rect.raw[:16])
            return {"x": l, "y": t, "width": r - l, "height": b - t}
    except Exception:
        pass
    return None


def get_window_state(window_id: str = "") -> ToolResult:
    """获取窗口完整状态: 截图+UI树+边界+文字 (一次性返回)"""
    user32 = ctypes.windll.user32

    # 1) 找目标窗口
    if window_id:
        matched = _smart_match_window(window_id)
        target_hwnd = matched[0] if matched else user32.GetForegroundWindow()
    else:
        target_hwnd = user32.GetForegroundWindow()

    if not target_hwnd:
        return ToolResult.failure("没有前台窗口")

    # 获取窗口标题
    length = user32.GetWindowTextLengthW(target_hwnd) + 1
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(target_hwnd, buf, length)
    win_title = buf.value.strip()

    # 2) 截图
    b64 = ""
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            orig_w, orig_h = pil_img.width, pil_img.height
            MAX_DIM = 1024
            if orig_w > MAX_DIM or orig_h > MAX_DIM:
                ratio = min(MAX_DIM / orig_w, MAX_DIM / orig_h)
                pil_img = pil_img.resize((int(orig_w * ratio), int(orig_h * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass

    # 3) 控件列表
    controls = []
    try:
        def enum_child(hwnd, _):
            l = user32.GetWindowTextLengthW(hwnd) + 1
            b = ctypes.create_unicode_buffer(l)
            user32.GetWindowTextW(hwnd, b, l)
            t = b.value.strip()
            if t and user32.IsWindowVisible(hwnd):
                controls.append(t[:50])
            return True
        user32.EnumChildWindows(target_hwnd, ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(enum_child), 0)
    except Exception:
        pass

    seen = set()
    unique_ctrl = [c for c in controls if c not in seen and len(c) >= 2 and (seen.add(c) or True)]

    # 4) 窗口边界
    bounds = _get_window_rect(target_hwnd)

    # 构造返回
    result_lines = [f"窗口: {win_title}", f"句柄: {target_hwnd}"]
    if bounds:
        result_lines.append(f"位置: x={bounds['x']} y={bounds['y']} {bounds['width']}x{bounds['height']}")
    if unique_ctrl:
        result_lines.append(f"控件 ({len(unique_ctrl)} 项):")
        result_lines += [f"  [{i}] {t}" for i, t in enumerate(unique_ctrl[:25])]
    result_lines.append(f"截图: {len(b64)//1024}KB (已缩放)")

    return ToolResult(success=True, data="\n".join(result_lines), image=b64)


def _get_controls(title_filter: str = "") -> tuple[int, str, list[tuple[int, str]]] | None:
    """通用: 根据标题找窗口 → 枚举子控件 → 返回 (hwnd, title, [(child_hwnd, child_text), ...])"""
    if title_filter:
        matched = _smart_match_window(title_filter)
        if not matched:
            return None
        target_hwnd = matched[0]
        target_title = matched[1]
    else:
        target_hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not target_hwnd:
            return None
        length = ctypes.windll.user32.GetWindowTextLengthW(target_hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        ctypes.windll.user32.GetWindowTextW(target_hwnd, buf, length)
        target_title = buf.value.strip()

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    controls = []

    def enum_child(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        t = buf.value.strip()
        if t and user32.IsWindowVisible(hwnd):
            controls.append((hwnd, t[:50]))
        return True

    user32.EnumChildWindows(target_hwnd, WNDENUMPROC(enum_child), 0)
    return (target_hwnd, target_title, controls)


def click_element(element_index: int = 0, title_filter: str = "") -> ToolResult:
    """按控件索引点击 (从 read_ui_window 序号点击, 不需要坐标)"""
    try:
        result = _get_controls(title_filter)
        if not result:
            return ToolResult.failure(f"未找到窗口: {title_filter or '(任何)'}")
        hwnd, win_title, controls = result
        if not controls:
            return ToolResult.failure(f"窗口 \"{win_title}\" 内无文本控件")
        if element_index < 0 or element_index >= len(controls):
            return ToolResult.failure(f"索引 {element_index} 超出 (0-{len(controls)-1})")

        child_hwnd, child_text = controls[element_index]
        rect = ctypes.create_string_buffer(16)
        if not ctypes.windll.user32.GetWindowRect(child_hwnd, rect):
            return ToolResult.failure("无法获取控件位置")
        import struct
        l, t, r, b = struct.unpack('4i', rect.raw[:16])
        cx, cy = (l + r) // 2, (t + b) // 2

        import pyautogui
        pyautogui.click(cx, cy)
        return ToolResult.success(f"已点击控件 [{element_index}]: {child_text[:30]}")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def scroll_element(element_index: int = 0, direction: str = "down", title_filter: str = "") -> ToolResult:
    """在指定控件内滚动 (列表/下拉框)"""
    try:
        result = _get_controls(title_filter)
        if not result:
            return ToolResult.failure(f"未找到窗口: {title_filter or '(任何)'}")
        hwnd, win_title, controls = result
        if not controls:
            return ToolResult.failure(f"窗口 \"{win_title}\" 内无控件")
        if element_index < 0 or element_index >= len(controls):
            return ToolResult.failure(f"索引 {element_index} 超出 (0-{len(controls)-1})")

        child_hwnd, child_text = controls[element_index]
        delta = -3 if direction in ("up", "upward") else 3
        rect = ctypes.create_string_buffer(16)
        if ctypes.windll.user32.GetWindowRect(child_hwnd, rect):
            import struct
            l, t, r, b = struct.unpack('4i', rect.raw[:16])
            import pyautogui
            pyautogui.click((l + r) // 2, (t + b) // 2)
            pyautogui.scroll(delta)
        return ToolResult.success(f"已滚动控件 [{element_index}]: {child_text[:30]} ({direction})")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def set_value(element_index: int = 0, value: str = "", title_filter: str = "") -> ToolResult:
    """直接填充输入框的值 (不需要猜坐标)"""
    try:
        result = _get_controls(title_filter)
        if not result:
            return ToolResult.failure(f"未找到窗口: {title_filter or '(任何)'}")
        hwnd, win_title, controls = result
        if not controls:
            return ToolResult.failure(f"窗口 \"{win_title}\" 内无控件")
        if element_index < 0 or element_index >= len(controls):
            return ToolResult.failure(f"索引 {element_index} 超出 (0-{len(controls)-1})")

        child_hwnd, child_text = controls[element_index]
        import pyautogui
        rect = ctypes.create_string_buffer(16)
        if ctypes.windll.user32.GetWindowRect(child_hwnd, rect):
            import struct
            l, t, r, b = struct.unpack('4i', rect.raw[:16])
            pyautogui.click((l + r) // 2, (t + b) // 2)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.write(value, interval=0.01)
        return ToolResult.success(f"已填写 [{element_index}]: {child_text[:20]} → \"{value[:30]}\"")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def perform_secondary_action(element_index: int = 0, action: str = "click", title_filter: str = "") -> ToolResult:
    """对控件执行右击/双击等操作"""
    try:
        result = _get_controls(title_filter)
        if not result:
            return ToolResult.failure(f"未找到窗口")
        hwnd, win_title, controls = result
        if not controls:
            return ToolResult.failure(f"窗口内没有控件")
        if element_index < 0 or element_index >= len(controls):
            return ToolResult.failure(f"索引 {element_index} 超出")

        child_hwnd, child_text = controls[element_index]
        rect = ctypes.create_string_buffer(16)
        if not ctypes.windll.user32.GetWindowRect(child_hwnd, rect):
            return ToolResult.failure("无法获取控件位置")
        import struct
        l, t, r, b = struct.unpack('4i', rect.raw[:16])
        cx, cy = (l + r) // 2, (t + b) // 2

        import pyautogui
        action = action.lower()
        if action in ("click", "leftclick", "left_click"):
            pyautogui.click(cx, cy)
        elif action in ("rightclick", "right_click", "right"):
            pyautogui.click(cx, cy, button="right")
        elif action in ("doubleclick", "double_click", "double"):
            pyautogui.doubleClick(cx, cy)
        else:
            return ToolResult.failure(f"不支持的操作: {action}")

        return ToolResult.success(f"已{action}控件 [{element_index}]: {child_text[:30]}")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


# ── 增强键名映射 (类似 Codex 的 200+ 键枚举) ──
KEY_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl",
    "alt": "alt", "option": "alt", "opt": "alt",
    "shift": "shift",
    "win": "win", "windows": "win", "meta": "win", "command": "win", "cmd": "win", "super": "win",
    "enter": "enter", "return": "enter", "cr": "enter",
    "tab": "tab",
    "space": "space", "spc": "space",
    "escape": "esc", "esc": "esc",
    "backspace": "backspace", "bs": "backspace", "delete": "delete", "del": "delete",
    "insert": "insert", "ins": "insert",
    "home": "home", "end": "end",
    "pageup": "pageup", "pgup": "pageup",
    "pagedown": "pagedown", "pgdn": "pagedown",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "capslock": "capslock", "caps": "capslock",
    "numlock": "numlock", "num": "numlock",
    "scrolllock": "scrolllock",
    "printscreen": "printscreen", "prtsc": "printscreen",
    "pause": "pause", "break": "pause",
    "f1": "f1","f2": "f2","f3": "f3","f4": "f4","f5": "f5","f6": "f6",
    "f7": "f7","f8": "f8","f9": "f9","f10": "f10","f11": "f11","f12": "f12",
    # Numpad
    "num0": "num0", "num1": "num1", "num2": "num2", "num3": "num3",
    "num4": "num4", "num5": "num5", "num6": "num6", "num7": "num7",
    "num8": "num8", "num9": "num9",
    "num+": "num+", "num-": "num-", "num*": "num*", "num/": "num/",
    "num.": "num.", "numenter": "numenter",
    # 媒体键 (Win32 VK codes, 用 _VK_ 前缀标记)
    "volume_up": "_VK_175", "volup": "_VK_175",
    "volume_down": "_VK_174", "voldown": "_VK_174",
    "volume_mute": "_VK_173", "mute": "_VK_173",
    "next_track": "_VK_176", "next": "_VK_176",
    "prev_track": "_VK_177", "prev": "_VK_177",
    "play_pause": "_VK_179", "playpause": "_VK_179",
}

# 媒体键 VK 码映射
_VK_MEDIA = {
    "_VK_173": 0xAD, "_VK_174": 0xAE, "_VK_175": 0xAF,
    "_VK_176": 0xB0, "_VK_177": 0xB1, "_VK_179": 0xB3,
}


def _send_vk(vk_code: int):
    """通过 Win32 keybd_event 发送虚拟键码"""
    ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk_code, 0, 2, 0)


def set_volume(level: int = 50) -> ToolResult:
    """设置系统音量 (0-100) — Python ctypes keybd_event 按键模拟"""
    try:
        vol = max(0, min(100, level))
        import ctypes as _c, time
        user32 = _c.windll.user32
        VK_UP, VK_DOWN = 0xAF, 0xAE

        # 先降到最低
        for i in range(50):
            user32.keybd_event(VK_DOWN, 0, 0, 0)
            time.sleep(0.005)
            user32.keybd_event(VK_DOWN, 0, 2, 0)
            time.sleep(0.005)
        time.sleep(0.1)

        # 再升到目标
        steps = max(1, vol // 2)
        for i in range(steps):
            user32.keybd_event(VK_UP, 0, 0, 0)
            time.sleep(0.005)
            user32.keybd_event(VK_UP, 0, 2, 0)
            time.sleep(0.005)

        return ToolResult.success(f"系统音量已调至 {vol}%")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def keyboard_press(keys: str = "", key: str = "", key_combination: str = "", keys_array: list = None) -> ToolResult:
    norm_keys = []
    if keys_array and isinstance(keys_array, list):
        norm_keys = [KEY_ALIASES.get(k.lower(), k.lower()) for k in keys_array]
    else:
        combo = (key_combination or keys or key).strip()
        if not combo:
            return ToolResult.failure("请提供 keys 参数")
        norm_keys = normalize_keys(combo)
    try:
        # 检查是否有媒体键 (VK 前缀), 直接用 Win32 API
        vk_keys = [k for k in norm_keys if k.startswith("_VK_")]
        if vk_keys:
            for vk_name in vk_keys:
                vk_code = _VK_MEDIA.get(vk_name)
                if vk_code:
                    _send_vk(vk_code)
            # 防多键组合
            if len(vk_keys) == len(norm_keys):
                return ToolResult.success(f"组合键: {'+'.join(norm_keys)}")

        import pyautogui
        pyautogui.hotkey(*norm_keys)
        return ToolResult.success(f"组合键: {'+'.join(norm_keys)}")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def focus_window(name: str) -> ToolResult:
    """聚焦到指定窗口 (智能匹配: 标题/进程名/别名/汉字, 一次完成)"""
    try:
        matched = _smart_match_window(name)
        if matched is None:
            all_wins = _get_all_windows()
            window_list = "\n".join(f"  [{i}] {t}" for i, (_, t) in enumerate(all_wins[:10]))
            return ToolResult.failure(
                f"未找到匹配 \"{name}\" 的窗口:\n{window_list}"
            )

        hwnd, title = matched
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        user32.SetForegroundWindow(hwnd)    # 激活前台
        user32.BringWindowToTop(hwnd)       # 置顶
        time.sleep(0.3)
        return ToolResult.success(f"已聚焦窗口: {title}")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def get_foreground_window() -> ToolResult:
    """获取当前前台窗口 (最前面的窗口名称)"""
    import ctypes
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ToolResult.success("当前没有前台窗口")

        length = user32.GetWindowTextLengthW(hwnd) + 1
        buffer = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buffer, length)
        title = buffer.value.strip()

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = "unknown"
        try:
            import psutil as _psutil
            proc = _psutil.Process(pid.value)
            process_name = proc.name()
        except Exception:
            pass

        return ToolResult.success(f"当前前台: \"{title}\" ({process_name})")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def read_ui_window(title_filter: str = "") -> ToolResult:
    """读取窗口内的所有可见控件/按钮/输入框文字 (智能匹配窗口)"""
    try:
        result = _get_controls(title_filter)
        if not result:
            return ToolResult.failure(f"未找到窗口: {title_filter or '(任何)'}")
        hwnd, win_title, controls = result

        if not controls:
            return ToolResult.success(f"窗口 \"{win_title}\" 内无文本控件")

        seen = set()
        texts = [t for _, t in controls]
        unique = [t for t in texts if t not in seen and len(t) >= 2 and (seen.add(t) or True)]
        return ToolResult.success(
            f"窗口 \"{win_title}\" 控件 ({len(unique)} 项):\n" +
            "\n".join(f"  [{i}] {t}" for i, t in enumerate(unique[:30]))
        )
    except Exception as e:
        return ToolResult.failure(friendly_error(e))
