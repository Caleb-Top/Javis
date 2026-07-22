"""
Telegram Bot — 通过 python-telegram-bot 或 HTTP API 接入
P3-2: Full Telegram bot with message routing, inline keyboards, and file handling
"""
import asyncio, json, logging, os
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass, field

logger = logging.getLogger("gateway.telegram")

TELEGRAM_API = "https://api.telegram.org"


@dataclass
class TelegramConfig:
    token: str = ""
    allowed_users: list[int] = field(default_factory=list)
    allowed_chats: list[int] = field(default_factory=list)
    parse_mode: str = "Markdown"
    webhook_url: str = ""
    polling_interval: int = 2


class TelegramBot:
    """Telegram Bot — 支持 polling 和 webhook 两种模式"""

    def __init__(self, config: TelegramConfig, on_message: Callable = None):
        self.config = config
        self._on_message = on_message
        self._running = False
        self._offset = 0
        self._session = None

    async def start(self, mode: str = "polling"):
        """启动 Bot"""
        self._running = True
        logger.info(f"Telegram Bot 启动: mode={mode}")

        if mode == "webhook" and self.config.webhook_url:
            await self._set_webhook()
        else:
            asyncio.create_task(self._polling_loop())

    async def stop(self):
        self._running = False
        logger.info("Telegram Bot 停止")

    async def send_message(self, chat_id: int, text: str,
                          parse_mode: str = None,
                          reply_to: int = None,
                          keyboard: list = None) -> dict:
        """发送消息"""
        import aiohttp
        url = f"{TELEGRAM_API}/bot{self.config.token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text[:4000],
            "parse_mode": parse_mode or self.config.parse_mode,
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        if keyboard:
            payload["reply_markup"] = json.dumps(keyboard)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as resp:
                    return await resp.json()
        except Exception as e:
            logger.error(f"Telegram 发送失败: {e}")
            return {"ok": False, "error": str(e)}

    async def send_photo(self, chat_id: int, photo_url: str,
                        caption: str = "") -> dict:
        """发送图片"""
        import aiohttp
        url = f"{TELEGRAM_API}/bot{self.config.token}/sendPhoto"
        payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                return await resp.json()

    async def send_document(self, chat_id: int, file_path: str,
                           caption: str = "") -> dict:
        """发送文件"""
        import aiohttp
        url = f"{TELEGRAM_API}/bot{self.config.token}/sendDocument"
        data = aiohttp.FormData()
        data.add_field("chat_id", str(chat_id))
        data.add_field("document", open(file_path, "rb"))
        if caption:
            data.add_field("caption", caption)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, timeout=30) as resp:
                return await resp.json()

    async def get_updates(self) -> list[dict]:
        """获取更新"""
        import aiohttp
        url = f"{TELEGRAM_API}/bot{self.config.token}/getUpdates"
        params = {"offset": self._offset, "timeout": 30}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=35) as resp:
                result = await resp.json()
                if result.get("ok"):
                    return result["result"]
        return []

    async def _polling_loop(self):
        """轮询消息"""
        while self._running:
            updates = await self.get_updates()
            for update in updates:
                self._offset = max(self._offset, update["update_id"] + 1)
                await self._handle_update(update)
            await asyncio.sleep(self.config.polling_interval)

    async def _handle_update(self, update: dict):
        """处理单条更新"""
        message = update.get("message", {})
        if not message:
            return

        chat = message.get("chat", {})
        user = message.get("from", {})
        text = message.get("text", "") or message.get("caption", "")

        chat_id = chat.get("id")
        user_id = user.get("id")

        # 用户/群组白名单
        allowed_users = self.config.allowed_users
        allowed_chats = self.config.allowed_chats
        if allowed_users and user_id not in allowed_users:
            return
        if allowed_chats and chat_id not in allowed_chats:
            return

        if text and self._on_message:
            try:
                response = self._on_message({
                    "platform": "telegram",
                    "chat_id": str(chat_id),
                    "user_id": str(user_id),
                    "user_name": user.get("username", user.get("first_name", "")),
                    "text": text,
                    "message_id": message.get("message_id"),
                    "chat_type": chat.get("type"),
                })
                if asyncio.iscoroutine(response):
                    response = await response
                if response:
                    await self.send_message(chat_id, str(response)[:4000])
            except Exception as e:
                logger.error(f"消息处理失败: {e}")
                await self.send_message(chat_id, f"处理失败: {e}")

    async def _set_webhook(self):
        """设置 Webhook"""
        import aiohttp
        url = f"{TELEGRAM_API}/bot{self.config.token}/setWebhook"
        payload = {"url": self.config.webhook_url}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                result = await resp.json()
                logger.info(f"Telegram Webhook 设置: {result}")

    async def get_chat_info(self, chat_id: int) -> dict:
        """获取会话信息"""
        import aiohttp
        url = f"{TELEGRAM_API}/bot{self.config.token}/getChat"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json={"chat_id": chat_id}, timeout=10
            ) as resp:
                return await resp.json()


def create_bot(token: str = "", on_message: Callable = None) -> TelegramBot:
    """创建 Telegram Bot 实例"""
    return TelegramBot(
        config=TelegramConfig(
            token=token or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        ),
        on_message=on_message,
    )
