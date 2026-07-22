"""P3-2: Slack 适配器 (占位实现, 实际需接入 Slack Bolt SDK)"""

import asyncio
from typing import Dict, List, Optional, Callable, Awaitable
from .gateway_manager import PlatformAdapter, Platform, Message, Reply


class SlackAdapter(PlatformAdapter):
    platform = Platform.SLACK

    def __init__(self, bot_token: str, app_token: str = ""):
        self.bot_token = bot_token
        self.app_token = app_token
        self._connected = False

    async def connect(self) -> bool:
        self._connected = bool(self.bot_token)
        return self._connected

    async def disconnect(self):
        self._connected = False

    async def send_message(self, chat_id: str, reply: Reply) -> bool:
        if not self._connected:
            return False
        try:
            import aiohttp
            payload = {"channel": chat_id, "text": reply.text}
            if reply.markdown:
                payload["blocks"] = [{
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": reply.text}
                }]
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.bot_token}",
                          "Content-Type": "application/json"}
                async with session.post("https://slack.com/api/chat.postMessage",
                                       json=payload, headers=headers, timeout=10) as resp:
                    data = await resp.json()
                    return data.get("ok", False)
        except Exception:
            return False

    async def listen(self, callback: Callable[[Message], Awaitable[None]]):
        # Socket Mode 实现需接入 Slack Bolt
        pass

    async def get_chats(self) -> List[Dict]:
        if not self._connected:
            return []
        try:
            import aiohttp, json
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.bot_token}"}
                async with session.get("https://slack.com/api/conversations.list",
                                      headers=headers, timeout=10) as resp:
                    data = await resp.json()
                    return [{"id": ch["id"], "name": ch.get("name", ""),
                            "type": ch.get("is_channel") and "channel" or "dm"}
                            for ch in data.get("channels", []) if ch.get("is_member")]
        except Exception:
            return []

    async def get_chat_history(self, chat_id: str, limit: int = 50) -> List[Message]:
        return []

    async def is_connected(self) -> bool:
        return self._connected
