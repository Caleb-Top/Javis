"""P3-2: 微信适配器 (占位实现, 实际需接入企业微信或itchat)"""

import asyncio
from typing import Dict, List, Optional, Callable, Awaitable
from .gateway_manager import PlatformAdapter, Platform, Message, Reply


class WeChatAdapter(PlatformAdapter):
    platform = Platform.WECHAT

    def __init__(self, config: Dict):
        self.config = config
        self._connected = False

    async def connect(self) -> bool:
        # 占位: 实际需接入企业微信 Webhook 或 itchat
        self._connected = bool(self.config.get("webhook_url"))
        return self._connected

    async def disconnect(self):
        self._connected = False

    async def send_message(self, chat_id: str, reply: Reply) -> bool:
        if not self._connected:
            return False
        try:
            import aiohttp
            payload = {
                "msgtype": "markdown" if reply.markdown else "text",
                "markdown" if reply.markdown else "text": {"content": reply.text},
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(self.config["webhook_url"], json=payload, timeout=10) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def listen(self, callback: Callable[[Message], Awaitable[None]]):
        # 微信 webhook 模式不支持主动监听
        pass

    async def get_chats(self) -> List[Dict]:
        return [{"id": "default", "name": "WeChat Webhook"}]

    async def get_chat_history(self, chat_id: str, limit: int = 50) -> List[Message]:
        return []

    async def is_connected(self) -> bool:
        return self._connected
