"""
浏览器操控工具 — 基于 Playwright
===================================
功能:
  browser_open      → 打开浏览器并导航到 URL
  browser_click     → 点击页面元素 (CSS 选择器)
  browser_fill      → 填写输入框
  browser_get_text  → 获取页面文本
  browser_screenshot→ 截取当前页面 (自动送入视觉管道)
  browser_close     → 关闭浏览器

设计:
  - 全局单例 BrowserManager, 复用浏览器实例
  - 默认无头模式, 可通过 browser_open 切换
  - 截图送入 describe_screen 做语义分析
"""

import os, json, time, logging, asyncio
from pathlib import Path

logger = logging.getLogger("browser")

# ── 全局单例浏览器管理器 ──
_manager = None


class BrowserManager:
    """全局浏览器管理器 — 复用实例, 单浏览器多标签"""

    def __init__(self):
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._headless = True

    async def _ensure(self):
        """确保浏览器已启动"""
        if self._browser is not None and self._page is not None:
            try:
                # 快速检查页面是否还活着
                await asyncio.wait_for(self._page.evaluate("1+1"), timeout=2.0)
                return
            except:
                pass
        await self._start()

    async def _start(self):
        """启动 Playwright + Chromium"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("Playwright 未安装: pip install playwright && playwright install chromium")

        try:
            self._playwright = await async_playwright().start()
            launch_options = {"headless": self._headless}
            self._browser = await self._playwright.chromium.launch(**launch_options)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Javis/1.0"
            )
            self._page = await self._context.new_page()
            logger.info("✅ 浏览器已启动")
        except Exception as e:
            self._browser = None
            self._page = None
            raise RuntimeError(f"浏览器启动失败: {e}")

    async def open(self, url: str, headless: bool = None) -> str:
        """打开 URL, 返回页面标题"""
        if headless is not None:
            self._headless = headless
        await self._ensure()
        try:
            await asyncio.wait_for(self._page.goto(url, wait_until="domcontentloaded"), timeout=30.0)
            title = await self._page.title()
            return f"已打开 {url} 标题: {title}"
        except asyncio.TimeoutError:
            return f"打开 {url} 超时, 但页面可能部分加载"
        except Exception as e:
            return f"打开失败: {e}"

    async def click(self, selector: str) -> str:
        """点击 CSS 选择器指定的元素"""
        await self._ensure()
        try:
            await asyncio.wait_for(self._page.click(selector), timeout=10.0)
            return f"已点击: {selector}"
        except Exception as e:
            return f"点击失败 ({selector}): {e}"

    async def fill(self, selector: str, text: str) -> str:
        """填写输入框"""
        await self._ensure()
        try:
            await asyncio.wait_for(self._page.fill(selector, text), timeout=10.0)
            return f"已填写 {selector}: {text[:50]}"
        except Exception as e:
            return f"填写失败 ({selector}): {e}"

    async def get_text(self, selector: str = "body") -> str:
        """获取页面元素文本, 默认获取整个页面"""
        await self._ensure()
        try:
            el = await asyncio.wait_for(self._page.query_selector(selector), timeout=10.0)
            if el is None:
                return f"未找到元素: {selector}"
            text = await asyncio.wait_for(el.inner_text(), timeout=10.0)
            return text[:5000]
        except Exception as e:
            return f"获取文本失败: {e}"

    async def screenshot(self) -> dict:
        """截取当前页面, 返回截图信息"""
        await self._ensure()
        try:
            import io, base64
            from PIL import Image
            screenshot_bytes = await asyncio.wait_for(self._page.screenshot(type="png"), timeout=15.0)
            img = Image.open(io.BytesIO(screenshot_bytes))
            w, h = img.size

            # 保存到临时文件
            ts = int(time.time())
            screen_dir = Path(__file__).parent.parent / "data" / "screenshots"
            screen_dir.mkdir(parents=True, exist_ok=True)
            path = screen_dir / f"browser_{ts}.png"
            img.save(str(path))

            # 送入视觉管道分析
            result = {"path": str(path), "width": w, "height": h}
            try:
                from tools.javis_vision import describe_screen
                vision_result = describe_screen(str(path))
                result["description"] = vision_result.get("description", "")
                result["yolo"] = vision_result.get("yolo", [])
            except Exception as ve:
                logger.debug(f"视觉分析跳过: {ve}")

            return result
        except Exception as e:
            return {"error": f"截图失败: {e}"}

    async def close(self) -> str:
        """关闭浏览器"""
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self._browser = None
            self._context = None
            self._page = None
            self._playwright = None
            logger.info("🔒 浏览器已关闭")
            return "浏览器已关闭"
        except Exception as e:
            return f"关闭时出错: {e}"

    async def status(self) -> dict:
        """浏览器状态"""
        if self._browser and self._page:
            try:
                title = await self._page.title()
                url = self._page.url
                return {"alive": True, "title": title, "url": url, "headless": self._headless}
            except:
                pass
        return {"alive": False}


# ── 工具接口 ──

async def browser_open(url: str, headless: bool = True) -> str:
    """打开浏览器并导航到指定 URL"""
    global _manager
    if _manager is None:
        _manager = BrowserManager()
    return await _manager.open(url, headless=headless)


async def browser_click(selector: str) -> str:
    """点击页面中 CSS 选择器指定的元素"""
    global _manager
    if _manager is None or not _manager._browser:
        return "浏览器未打开, 请先调用 browser_open"
    return await _manager.click(selector)


async def browser_fill(selector: str, text: str) -> str:
    """在输入框中填入文本"""
    global _manager
    if _manager is None or not _manager._browser:
        return "浏览器未打开, 请先调用 browser_open"
    return await _manager.fill(selector, text)


async def browser_get_text(selector: str = "body") -> str:
    """获取页面文本内容"""
    global _manager
    if _manager is None or not _manager._browser:
        return "浏览器未打开, 请先调用 browser_open"
    return await _manager.get_text(selector)


async def browser_screenshot() -> dict:
    """截取浏览器当前页面截图并分析"""
    global _manager
    if _manager is None or not _manager._browser:
        return {"error": "浏览器未打开, 请先调用 browser_open"}
    return await _manager.screenshot()


async def browser_close() -> str:
    """关闭浏览器释放资源"""
    global _manager
    if _manager is None:
        return "浏览器未打开"
    return await _manager.close()


async def browser_status() -> dict:
    """查看浏览器状态"""
    global _manager
    if _manager is None:
        _manager = BrowserManager()
    return await _manager.status()
