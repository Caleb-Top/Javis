"""Javis 后台服务 — 系统托盘 + Escape中断 + 唤醒词检测"""

import os, sys, threading, time, json, logging
import ctypes, ctypes.wintypes
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

INTERRUPT_FILE = ROOT / "data" / "interrupt.flg"
WAKE_FILE = ROOT / "data" / "wake.flg"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [tray] %(message)s")
logger = logging.getLogger("tray")

try:
    import win32event
    mtx = win32event.CreateMutex(None, False, "JavisTrayMutex")
    if win32event.GetLastError() == 183:
        sys.exit(0)
except: pass

ESCAPE_HOOK_RUNNING = True

def _start_escape_hook():
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 256
    VK_ESCAPE = 27

    def hook_proc(nCode, wParam, lParam):
        if nCode >= 0 and wParam == WM_KEYDOWN:
            vk = ctypes.cast(lParam, ctypes.POINTER(ctypes.c_ulong)).contents.value
            if vk == VK_ESCAPE:
                try:
                    INTERRUPT_FILE.parent.mkdir(parents=True, exist_ok=True)
                    INTERRUPT_FILE.write_text("1")
                    logger.info("Escape 按下")
                    def _clear():
                        time.sleep(5)
                        if INTERRUPT_FILE.exists(): INTERRUPT_FILE.unlink()
                    threading.Thread(target=_clear, daemon=True).start()
                except: pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    callback = HOOKPROC(hook_proc)
    hook = user32.SetWindowsHookExA(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)
    logger.info("Escape 监听已启动")

    msg = ctypes.wintypes.MSG()
    while ESCAPE_HOOK_RUNNING:
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret == 0: break
        user32.TranslateMessageW(msg)
        user32.DispatchMessageW(msg)
    if hook: user32.UnhookWindowsHookEx(hook)

def run_tray():
    thread = threading.Thread(target=_start_escape_hook, daemon=True)
    thread.start()
    logger.info("后台服务已启动 (Escape中断 + 唤醒词)")
    try:
        import pystray
        from pystray import MenuItem as Item
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=(0, 212, 255, 220))
        draw.ellipse([8, 8, 56, 56], fill=(10, 10, 18, 240))
        draw.text((18, 14), "J", fill=(0, 212, 255))
        icon = pystray.Icon("Javis", img, "Javis 桌面助手")
        icon.run()
    except ImportError:
        while True: time.sleep(10)

if __name__ == "__main__":
    run_tray()
