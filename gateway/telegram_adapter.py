"""P3-2: Telegram 适配器"""

import asyncio
from typing import Dict, List, Optional, Callable, Awaitable
from .gateway_manager import PlatformAdapter, Platform, Message, Reply


class TelegramAdapter(PlatformAdapter):
    platform = Platform.TELEGRAM

    def __init__(self, token: str):
        self.token = token
        self._connected = False
        self._offset: int = 0
        self._listen_task: Optional[asyncio.Task] = None

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def connect(self) -> bool:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/getMe", timeout=10) as resp:
                    data = await resp.json()
                    self._connected = data.get("ok", False)
        except Exception:
            self._connected = False
        return self._connected

    async def disconnect(self):
        self._connected = False
        if self._listen_task:
            self._listen_task.cancel()

    async def send_message(self, chat_id: str, reply: Reply) -> bool:
        import aiohttp
        method = "sendMessage" if not reply.markdown else "sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": reply.text,
            "parse_mode": "Markdown" if reply.markdown else "",
            "reply_to_message_id": reply.reply_to,
        }
        if not payload["parse_mode"]:
            del payload["parse_mode"]
        if not payload["reply_to_message_id"]:
            del payload["reply_to_message_id"]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/{method}", json=payload, timeout=15) as resp:
                    data = await resp.json()
                    return data.get("ok", False)
        except Exception:
            return False

    async def listen(self, callback: Callable[[Message], Awaitable[None]]):
        self._listen_task = asyncio.create_task(self._poll(callback))

    async def _poll(self, callback: Callable[[Message], Awaitable[None]]):
        import aiohttp
        while self._connected:
            try:
                async with aiohttp.ClientSession() as session:
                    params = {"offset": self._offset, "timeout": 30, "limit": 10}
                    async with session.get(f"{self.base_url}/getUpdates", params=params, timeout=35) as resp:
                        data = await resp.json()
                    if data.get("ok"):
                        for update in data.get("result", []):
                            self._offset = update["update_id"] + 1
                            if "message" in update:
                                msg = update["message"]
                                await callback(Message(
                                    platform=self.platform.value,
                                    chat_id=str(msg["chat"]["id"]),
                                    sender_id=str(msg["from"]["id"]),
                                    sender_name=msg["from"].get("first_name", ""),
                                    text=msg.get("text", ""),
                                    timestamp=msg.get("date", 0),
                                ))
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(3)

    async def get_chats(self) -> List[Dict]:
        return []  # Telegram bot can't list chats

    async def get_chat_history(self, chat_id: str, limit: int = 50) -> List[Message]:
        return []  # Requires updates polling history

    async def is_connected(self) -> bool:
        return self._connected
