"""Gateway 多平台消息网关"""
import yaml, os, logging
from pathlib import Path

logger = logging.getLogger("gateway")

class Gateway:
    def __init__(self):
        self._platforms = {}
        self._relay_config = {}
        self._load_hooks()

    def _load_hooks(self):
        hook_path = Path("gateway/HOOK.yaml")
        if hook_path.exists():
            self._relay_config = yaml.safe_load(hook_path.read_text()) or {}
            logger.info(f"Gateway hooks 已加载: {len(self._relay_config)} 条")

    def register(self, name: str, handler):
        self._platforms[name] = handler
        logger.info(f"Gateway 平台注册: {name}")

    async def relay(self, platform: str, message: str) -> str:
        if platform not in self._platforms:
            return f"未知平台: {platform}"
        try:
            return await self._platforms[platform](message)
        except Exception as e:
            logger.error(f"Gateway relay 失败 [{platform}]: {e}")
            return str(e)
