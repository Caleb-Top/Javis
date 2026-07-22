"""
P3-2: 消息中继 (Relay) — 借鉴 Hermes Gateway Relay
消息路由、过滤、转换的统一中继层
"""
import json
import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from enum import Enum

from .adapters import PlatformType, IncomingMessage, OutgoingMessage

logger = logging.getLogger("javis.gateway.relay")


class MessageDirection(Enum):
    INCOMING = "incoming"   # 平台 → Javis
    OUTGOING = "outgoing"   # Javis → 平台


class MessagePriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


@dataclass
class RelayMessage:
    """中继消息 — 内部统一格式"""
    id: str
    direction: MessageDirection
    platform: PlatformType
    chat_id: str
    user_id: str
    content: str
    priority: MessagePriority = MessagePriority.NORMAL
    role: str = "user"  # user/assistant/system
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


class FilterRule:
    """消息过滤规则"""

    def __init__(self, name: str, condition: Callable[[RelayMessage], bool]):
        self.name = name
        self.condition = condition

    def match(self, msg: RelayMessage) -> bool:
        try:
            return self.condition(msg)
        except Exception:
            return False


class TransformRule:
    """消息转换规则"""

    def __init__(self, name: str, transform: Callable[[RelayMessage], RelayMessage]):
        self.name = name
        self.transform = transform

    def apply(self, msg: RelayMessage) -> RelayMessage:
        try:
            return self.transform(msg)
        except Exception as e:
            logger.error(f"Transform {self.name} error: {e}")
            return msg


class MessageRelay:
    """
    消息中继 — 借鉴 Hermes Gateway Relay

    功能:
    - 多平台消息统一路由
    - 消息过滤 (敏感词/垃圾信息)
    - 消息转换 (格式统一/内容增强)
    - 优先级队列
    - 速率限制
    """

    def __init__(self):
        self._filters: List[FilterRule] = []
        self._transforms: List[TransformRule] = []
        self._handlers: Dict[PlatformType, List[Callable[[RelayMessage], Awaitable[None]]]] = {}
        self._queue: asyncio.PriorityQueue = None
        self._running: bool = False
        self._message_count: Dict[str, int] = {}  # 速率限制
        self._rate_limit: int = 30  # 每用户每分钟最大消息数
        self._rate_window: int = 60  # 速率窗口(秒)

    def add_filter(self, name: str, condition: Callable[[RelayMessage], bool]):
        """添加过滤规则"""
        self._filters.append(FilterRule(name, condition))
        logger.info(f"Added filter: {name}")

    def add_transform(self, name: str, transform: Callable[[RelayMessage], RelayMessage]):
        """添加转换规则"""
        self._transforms.append(TransformRule(name, transform))
        logger.info(f"Added transform: {name}")

    def on_platform_message(self, platform: PlatformType,
                            handler: Callable[[RelayMessage], Awaitable[None]]):
        """注册平台消息处理器"""
        if platform not in self._handlers:
            self._handlers[platform] = []
        self._handlers[platform].append(handler)

    async def route_incoming(self, incoming: IncomingMessage) -> Optional[RelayMessage]:
        """
        处理入站消息: 过滤 → 转换 → 路由
        Returns: 处理后的 RelayMessage (被过滤则返回 None)
        """
        msg_id = f"{incoming.platform.value}:{incoming.chat_id}:{int(time.time()*1000)}"

        msg = RelayMessage(
            id=msg_id,
            direction=MessageDirection.INCOMING,
            platform=incoming.platform,
            chat_id=incoming.chat_id,
            user_id=incoming.user_id,
            content=incoming.content,
            role="user",
            metadata={
                "user_name": incoming.user_name,
                "message_type": incoming.message_type,
                "media_url": incoming.media_url,
            },
            raw=incoming.raw,
        )

        # 速率限制检查
        if not self._check_rate_limit(msg.user_id):
            logger.warning(f"Rate limit exceeded for user {msg.user_id}")
            return None

        # 过滤
        for f in self._filters:
            if f.match(msg):
                logger.info(f"Message filtered by '{f.name}'")
                return None

        # 转换
        for t in self._transforms:
            msg = t.apply(msg)

        # 路由到处理器
        handlers = self._handlers.get(incoming.platform, [])
        for handler in handlers:
            try:
                await handler(msg)
            except Exception as e:
                logger.error(f"Handler error for {incoming.platform.value}: {e}")

        return msg

    async def route_outgoing(self, outgoing: OutgoingMessage, platform: PlatformType) -> RelayMessage:
        """处理出站消息"""
        msg_id = f"out:{platform.value}:{outgoing.chat_id}:{int(time.time()*1000)}"

        msg = RelayMessage(
            id=msg_id,
            direction=MessageDirection.OUTGOING,
            platform=platform,
            chat_id=outgoing.chat_id,
            user_id="javis",
            content=outgoing.content,
            role="assistant",
            metadata={
                "parse_mode": outgoing.parse_mode,
                "reply_to": outgoing.reply_to_id,
            },
        )

        return msg

    def _check_rate_limit(self, user_id: str) -> bool:
        """检查速率限制"""
        now = time.time()
        key = f"rate:{user_id}:{int(now // self._rate_window)}"

        count = self._message_count.get(key, 0)
        if count >= self._rate_limit:
            return False

        self._message_count[key] = count + 1
        # 清理旧窗口
        self._clean_rate_keys(now)
        return True

    def _clean_rate_keys(self, now: float):
        """清理过期的速率限制键"""
        threshold = int(now // self._rate_window) - 2
        expired = [k for k in self._message_count
                   if k.startswith("rate:") and int(k.rsplit(":", 1)[-1]) < threshold]
        for k in expired:
            del self._message_count[k]

    def start_processing(self):
        """启动消息处理循环"""
        if not self._running:
            self._running = True
            logger.info("MessageRelay started")

    def stop_processing(self):
        """停止消息处理"""
        self._running = False
        logger.info("MessageRelay stopped")

    def status(self) -> Dict[str, Any]:
        """获取中继状态"""
        return {
            "running": self._running,
            "filters": len(self._filters),
            "transforms": len(self._transforms),
            "platforms": [p.value for p in self._handlers],
            "rate_limits_active": len(self._message_count),
        }
