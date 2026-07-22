"""
P3-2: Javis Gateway Manager — 多平台消息网关核心
统一管理多个消息平台的连接、消息收发、会话映射
"""
import json
import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from enum import Enum
from abc import ABC, abstractmethod


class Platform(Enum):
    TELEGRAM = "telegram"
    WECHAT = "wechat"
    SLACK = "slack"
    DISCORD = "discord"
    QQ = "qq"
    SIGNAL = "signal"
    MATRIX = "matrix"
    FEISHU = "feishu"
    DINGTALK = "dingtalk"


@dataclass
class Message:
    """统一消息格式"""
    platform: str
    chat_id: str
    sender_id: str
    sender_name: str = ""
    text: str = ""
    attachments: List[Dict] = field(default_factory=list)
    timestamp: float = 0
    reply_to: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "platform": self.platform,
            "chat_id": self.chat_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "text": self.text,
            "attachments": self.attachments,
            "timestamp": self.timestamp,
            "reply_to": self.reply_to,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Message":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Reply:
    """统一回复格式"""
    text: str = ""
    attachments: List[Dict] = field(default_factory=list)
    markdown: bool = False
    reply_to: Optional[str] = None


class PlatformAdapter(ABC):
    """平台适配器基类"""

    @property
    @abstractmethod
    def platform(self) -> Platform:
        pass

    @abstractmethod
    async def connect(self) -> bool:
        """连接平台"""
        pass

    @abstractmethod
    async def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    async def send_message(self, chat_id: str, reply: Reply) -> bool:
        """发送消息"""
        pass

    @abstractmethod
    async def listen(self, callback: Callable[[Message], Awaitable[None]]):
        """开始监听消息"""
        pass

    @abstractmethod
    async def get_chats(self) -> List[Dict]:
        """获取会话列表"""
        pass

    @abstractmethod
    async def get_chat_history(self, chat_id: str, limit: int = 50) -> List[Message]:
        """获取聊天历史"""
        pass

    @abstractmethod
    async def is_connected(self) -> bool:
        """是否已连接"""
        pass


class GatewayManager:
    """多平台消息网关管理器"""

    def __init__(self, config_path: str = ""):
        self._adapters: Dict[str, PlatformAdapter] = {}
        self._message_handlers: List[Callable[[Message], Awaitable[Any]]] = []
        self._session_map: Dict[str, str] = {}  # chat_id → session_id
        self._stats: Dict[str, Dict] = {}
        if config_path:
            self._load_config(config_path)

    def _load_config(self, config_path: str):
        """加载网关配置"""
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        for platform_config in config.get("platforms", []):
            self.enable_platform(platform_config)

    def register_adapter(self, adapter: PlatformAdapter):
        """注册平台适配器"""
        platform_name = adapter.platform.value
        self._adapters[platform_name] = adapter
        self._stats[platform_name] = {
            "messages_received": 0,
            "messages_sent": 0,
            "errors": 0,
            "connected": False,
        }

    def enable_platform(self, config: Dict):
        """启用并配置平台"""
        platform = config.get("platform", "")
        if platform == "telegram":
            from .telegram_adapter import TelegramAdapter
            adapter = TelegramAdapter(config.get("token", ""))
        elif platform == "wechat":
            from .wechat_adapter import WeChatAdapter
            adapter = WeChatAdapter(config)
        elif platform == "slack":
            from .slack_adapter import SlackAdapter
            adapter = SlackAdapter(config.get("token", ""), config.get("app_token", ""))
        else:
            return

        self.register_adapter(adapter)

    async def start_all(self):
        """启动所有已配置的平台"""
        for name, adapter in self._adapters.items():
            try:
                connected = await adapter.connect()
                self._stats[name]["connected"] = connected
                if connected:
                    await adapter.listen(self._on_message)
            except Exception as e:
                self._stats[name]["errors"] += 1

    async def stop_all(self):
        """停止所有平台连接"""
        for adapter in self._adapters.values():
            try:
                await adapter.disconnect()
            except Exception:
                pass

    async def _on_message(self, message: Message):
        """内部消息处理 — 分发给所有注册的处理器"""
        platform = message.platform
        self._stats[platform]["messages_received"] += 1

        for handler in self._message_handlers:
            try:
                await handler(message)
            except Exception as e:
                self._stats[platform]["errors"] += 1

    def on_message(self, handler: Callable[[Message], Awaitable[Any]]):
        """注册消息处理器"""
        self._message_handlers.append(handler)
        return handler  # 可用作装饰器

    async def send(self, platform: str, chat_id: str, text: str,
                   markdown: bool = False, attachments: List = None,
                   reply_to: str = None) -> bool:
        """发送消息到指定平台"""
        adapter = self._adapters.get(platform)
        if not adapter:
            return False

        reply = Reply(text=text, attachments=attachments or [],
                      markdown=markdown, reply_to=reply_to)

        try:
            ok = await adapter.send_message(chat_id, reply)
            if ok:
                self._stats[platform]["messages_sent"] += 1
            return ok
        except Exception:
            self._stats[platform]["errors"] += 1
            return False

    async def broadcast(self, text: str, platforms: List[str] = None,
                        chat_ids: Dict[str, str] = None) -> Dict[str, bool]:
        """广播消息到多个平台"""
        results = {}
        for name, adapter in self._adapters.items():
            if platforms and name not in platforms:
                continue
            chat_id = chat_ids.get(name) if chat_ids else "default"
            try:
                results[name] = await adapter.send_message(chat_id, Reply(text=text))
            except Exception:
                results[name] = False
        return results

    def map_session(self, chat_id: str, session_id: str):
        """会话映射: 将平台会话映射到 Javis 会话"""
        self._session_map[chat_id] = session_id

    def get_session(self, chat_id: str) -> Optional[str]:
        """反向查找: chat_id → session_id"""
        return self._session_map.get(chat_id)

    def get_status(self) -> Dict:
        """获取网关状态"""
        connected = [name for name, a in self._adapters.items() if a.is_connected()]
        return {
            "adapters": list(self._adapters.keys()),
            "connected": connected,
            "stats": self._stats.copy(),
            "session_mappings": len(self._session_map),
            "message_handlers": len(self._message_handlers),
        }


# 全局单例
_gateway: Optional[GatewayManager] = None


def get_gateway() -> GatewayManager:
    global _gateway
    if _gateway is None:
        _gateway = GatewayManager()
    return _gateway


def register_in_manifest(reg):
    """Register gateway tools"""
    from core.tool_registry import ToolDef
    gw = get_gateway()

    async def gateway_status(args):
        return {"success": True, **gw.get_status()}

    async def gateway_send(args):
        ok = await gw.send(
            platform=args["platform"],
            chat_id=args.get("chat_id", "default"),
            text=args["text"],
            markdown=args.get("markdown", False)
        )
        return {"success": ok}

    async def gateway_broadcast(args):
        results = await gw.broadcast(
            text=args["text"],
            platforms=args.get("platforms")
        )
        return {"success": True, "results": results}

    async def gateway_map_session(args):
        gw.map_session(args["chat_id"], args["session_id"])
        return {"success": True}

    async def gateway_chats(args):
        platform = args.get("platform", "")
        adapter = gw._adapters.get(platform)
        if adapter:
            chats = await adapter.get_chats()
            return {"success": True, "chats": chats}
        return {"success": False, "error": f"Platform {platform} not connected"}

    async def gateway_history(args):
        adapter = gw._adapters.get(args.get("platform", ""))
        if adapter:
            messages = await adapter.get_chat_history(
                args.get("chat_id", "default"),
                args.get("limit", 50)
            )
            return {"success": True, "messages": [m.to_dict() for m in messages]}
        return {"success": False, "error": "Platform not connected"}

    reg.register_many([
        ToolDef("gateway_status", "Get gateway status for all platforms",
                {"type":"object","properties":{},"required":[]}, gateway_status, "gateway"),
        ToolDef("gateway_send", "Send a message to a platform",
                {"type":"object","properties":{"platform":{"type":"string"},"chat_id":{"type":"string","default":"default"},"text":{"type":"string"},"markdown":{"type":"boolean","default":false}},"required":["platform","text"]}, gateway_send, "gateway"),
        ToolDef("gateway_broadcast", "Broadcast message to multiple platforms",
                {"type":"object","properties":{"text":{"type":"string"},"platforms":{"type":"array","items":{"type":"string"}}},"required":["text"]}, gateway_broadcast, "gateway"),
        ToolDef("gateway_map_session", "Map a chat to a Javis session",
                {"type":"object","properties":{"chat_id":{"type":"string"},"session_id":{"type":"string"}},"required":["chat_id","session_id"]}, gateway_map_session, "gateway"),
        ToolDef("gateway_chats", "List chats for a platform",
                {"type":"object","properties":{"platform":{"type":"string"}},"required":["platform"]}, gateway_chats, "gateway"),
        ToolDef("gateway_history", "Get chat history from a platform",
                {"type":"object","properties":{"platform":{"type":"string"},"chat_id":{"type":"string","default":"default"},"limit":{"type":"integer","default":50}},"required":["platform"]}, gateway_history, "gateway"),
    ])
