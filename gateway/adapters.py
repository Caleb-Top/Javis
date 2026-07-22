"""
P3-2: 平台适配器基类和具体实现
支持 Telegram, WeChat, DingTalk, Slack 等平台
"""
import json
import time
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from enum import Enum

logger = logging.getLogger("javis.gateway.adapters")


class PlatformType(Enum):
    TELEGRAM = "telegram"
    WECHAT = "wechat"
    DINGTALK = "dingtalk"
    SLACK = "slack"
    DISCORD = "discord"
    FEISHU = "feishu"
    QQ = "qq"
    MATRIX = "matrix"
    LINE = "line"
    WHATSAPP = "whatsapp"


@dataclass
class IncomingMessage:
    """接收到的消息"""
    platform: PlatformType
    chat_id: str
    user_id: str
    user_name: str = ""
    content: str = ""
    message_type: str = "text"  # text/image/file/voice
    media_url: Optional[str] = None
    media_data: Optional[bytes] = None
    reply_to_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    """发送的消息"""
    content: str
    chat_id: str
    reply_to_id: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    parse_mode: str = "markdown"  # markdown/html/plain
    keyboard: Optional[List[List[Dict]]] = None  # 内联键盘


class BaseAdapter(ABC):
    """平台适配器基类"""

    platform: PlatformType

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self._handlers: List[Callable[[IncomingMessage], Awaitable[None]]] = []

    @abstractmethod
    async def connect(self) -> bool:
        """连接到平台"""
        ...

    @abstractmethod
    async def disconnect(self) -> bool:
        """断开连接"""
        ...

    @abstractmethod
    async def send_message(self, msg: OutgoingMessage) -> bool:
        """发送消息"""
        ...

    @abstractmethod
    async def get_name(self) -> str:
        """适配器名称"""
        ...

    def on_message(self, handler: Callable[[IncomingMessage], Awaitable[None]]):
        """注册消息处理器"""
        self._handlers.append(handler)

    async def _emit_message(self, msg: IncomingMessage):
        """触发消息处理器"""
        for handler in self._handlers:
            try:
                await handler(msg)
            except Exception as e:
                logger.error(f"Handler error in {self.platform.value}: {e}")


class TelegramAdapter(BaseAdapter):
    """Telegram Bot 适配器"""

    platform = PlatformType.TELEGRAM

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._bot = None
        self._token = self.config.get("token", "")
        self._offset: int = 0
        self._polling: bool = False

    async def connect(self) -> bool:
        """通过 Telegram Bot API 连接"""
        try:
            # 尝试使用 aiogram (如果安装)
            try:
                from aiogram import Bot, Dispatcher
                self._bot = Bot(token=self._token)
                bot_info = await self._bot.get_me()
                logger.info(f"Telegram connected: @{bot_info.username}")
                self._polling = True
                return True
            except ImportError:
                # HTTP 降级
                import aiohttp
                self._session = aiohttp.ClientSession()
                async with self._session.get(
                    f"https://api.telegram.org/bot{self._token}/getMe"
                ) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        logger.info(f"Telegram connected (HTTP): @{data['result']['username']}")
                        return True
                return False
        except Exception as e:
            logger.error(f"Telegram connect failed: {e}")
            return False

    async def disconnect(self) -> bool:
        self._polling = False
        return True

    async def send_message(self, msg: OutgoingMessage) -> bool:
        try:
            if self._bot:
                await self._bot.send_message(
                    chat_id=msg.chat_id,
                    text=msg.content,
                    reply_to_message_id=int(msg.reply_to_id) if msg.reply_to_id else None,
                    parse_mode=msg.parse_mode.upper() if msg.parse_mode != "markdown" else None,
                )
                return True
            else:
                import aiohttp
                payload = {
                    "chat_id": msg.chat_id,
                    "text": msg.content,
                    "parse_mode": msg.parse_mode.upper() if msg.parse_mode != "markdown" else "HTML",
                }
                if msg.reply_to_id:
                    payload["reply_to_message_id"] = int(msg.reply_to_id)
                async with self._session.post(
                    f"https://api.telegram.org/bot{self._token}/sendMessage",
                    json=payload
                ) as resp:
                    return (await resp.json()).get("ok", False)
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def get_name(self) -> str:
        return "Telegram"


class WeChatAdapter(BaseAdapter):
    """微信适配器 (企业微信 + 个人微信)"""

    platform = PlatformType.WECHAT

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._corp_id = self.config.get("corp_id", "")
        self._secret = self.config.get("secret", "")
        self._token = None

    async def connect(self) -> bool:
        """连接企业微信"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                    params={"corpid": self._corp_id, "corpsecret": self._secret}
                ) as resp:
                    data = await resp.json()
                    if data.get("errcode") == 0:
                        self._token = data["access_token"]
                        logger.info("WeChat (企业微信) connected")
                        return True
            logger.warning("WeChat connect failed, using webhook mode")
            return bool(self.config.get("webhook_url"))
        except Exception as e:
            logger.error(f"WeChat connect failed: {e}")
            return False

    async def disconnect(self) -> bool:
        self._token = None
        return True

    async def send_message(self, msg: OutgoingMessage) -> bool:
        """通过企业微信发送"""
        import aiohttp
        if self._token:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "touser": msg.chat_id,
                    "msgtype": "text",
                    "agentid": self.config.get("agent_id", 0),
                    "text": {"content": msg.content},
                }
                async with session.post(
                    f"https://qyapi.weixin.qq.com/cgi-bin/message/send",
                    params={"access_token": self._token},
                    json=payload
                ) as resp:
                    return (await resp.json()).get("errcode") == 0
        elif webhook := self.config.get("webhook_url"):
            async with aiohttp.ClientSession() as session:
                payload = {"msgtype": "text", "text": {"content": msg.content}}
                async with session.post(webhook, json=payload) as resp:
                    return resp.status == 200
        return False

    async def get_name(self) -> str:
        return "WeChat"


class DingTalkAdapter(BaseAdapter):
    """钉钉适配器"""

    platform = PlatformType.DINGTALK

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._webhook = self.config.get("webhook_url", "")
        self._secret = self.config.get("secret", "")

    async def connect(self) -> bool:
        return bool(self._webhook)

    async def disconnect(self) -> bool:
        return True

    async def send_message(self, msg: OutgoingMessage) -> bool:
        import aiohttp
        try:
            # 如果有 secret, 需要签名
            url = self._webhook
            if self._secret:
                import hashlib
                import hmac
                timestamp = str(round(time.time() * 1000))
                sign = hmac.new(
                    self._secret.encode(), f"{timestamp}\n{self._secret}".encode(), hashlib.sha256
                ).digest()
                import base64
                url = f"{self._webhook}&timestamp={timestamp}&sign={base64.b64encode(sign).decode()}"

            async with aiohttp.ClientSession() as session:
                payload = {
                    "msgtype": "text",
                    "text": {"content": msg.content},
                }
                async with session.post(url, json=payload) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.error(f"DingTalk send failed: {e}")
            return False

    async def get_name(self) -> str:
        return "DingTalk"


class SlackAdapter(BaseAdapter):
    """Slack 适配器"""

    platform = PlatformType.SLACK

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._token = self.config.get("bot_token", "")
        self._channel = self.config.get("channel", "")

    async def connect(self) -> bool:
        return bool(self._token)

    async def disconnect(self) -> bool:
        return True

    async def send_message(self, msg: OutgoingMessage) -> bool:
        try:
            from slack_sdk import WebClient
            client = WebClient(token=self._token)
            channel = msg.chat_id or self._channel
            response = client.chat_postMessage(
                channel=channel,
                text=msg.content,
                thread_ts=msg.reply_to_id,
            )
            return response["ok"]
        except ImportError:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                payload = {
                    "channel": msg.chat_id or self._channel,
                    "text": msg.content,
                }
                if msg.reply_to_id:
                    payload["thread_ts"] = msg.reply_to_id
                async with session.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {self._token}"},
                    json=payload
                ) as resp:
                    return (await resp.json()).get("ok", False)
        except Exception as e:
            logger.error(f"Slack send failed: {e}")
            return False

    async def get_name(self) -> str:
        return "Slack"


# 适配器工厂
ADAPTER_MAP: Dict[PlatformType, type] = {
    PlatformType.TELEGRAM: TelegramAdapter,
    PlatformType.WECHAT: WeChatAdapter,
    PlatformType.DINGTALK: DingTalkAdapter,
    PlatformType.SLACK: SlackAdapter,
}


def create_adapter(platform: PlatformType, config: Dict[str, Any] = None) -> BaseAdapter:
    """创建平台适配器"""
    adapter_cls = ADAPTER_MAP.get(platform)
    if not adapter_cls:
        raise ValueError(f"Unsupported platform: {platform}")
    return adapter_cls(config)
