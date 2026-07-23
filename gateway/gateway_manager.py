"""
P3-2: Javis Gateway Manager — 多平台消息网关核心
统一管理 Telegram/WeChat/Slack + WebSocket 中继，从 HOOK.yaml 读取配置
"""
import json, asyncio, time, logging, os, yaml
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from enum import Enum
from pathlib import Path

logger = logging.getLogger("javis.gateway")


class Platform(Enum):
    TELEGRAM = "telegram"
    WECHAT = "wechat"
    SLACK = "slack"
    DISCORD = "discord"
    WEBSOCKET = "websocket"


@dataclass
class Message:
    """统一消息格式"""
    platform: str
    chat_id: str
    sender_id: str
    sender_name: str = ""
    text: str = ""
    attachments: List[Dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    reply_to: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "platform": self.platform, "chat_id": self.chat_id,
            "sender_id": self.sender_id, "sender_name": self.sender_name,
            "text": self.text, "attachments": self.attachments,
            "timestamp": self.timestamp, "reply_to": self.reply_to,
        }


@dataclass
class PlatformState:
    enabled: bool
    connected: bool = False
    last_activity: float = 0
    message_count: int = 0
    error_count: int = 0


@dataclass
class Reply:
    """统一回复格式"""
    text: str = ""
    markdown: bool = False
    reply_to: Optional[str] = None
    attachments: List[str] = field(default_factory=list)


class PlatformAdapter:
    """平台适配器抽象基类"""
    platform: Platform = None

    async def connect(self) -> bool:
        raise NotImplementedError

    async def disconnect(self):
        raise NotImplementedError

    async def send_message(self, chat_id: str, reply: Reply) -> bool:
        raise NotImplementedError

    async def listen(self, callback: Callable[[Message], Awaitable[None]]):
        raise NotImplementedError

    async def get_chats(self) -> list:
        return []

    async def get_chat_history(self, chat_id: str, limit: int = 50) -> list:
        return []

    async def is_connected(self) -> bool:
        return False


class GatewayManager:
    """多平台消息网关"""

    def __init__(self, config_path: str = None):
        self._config_path = config_path or str(
            Path(__file__).parent.parent / "HOOK.yaml"
        )
        self._platforms: dict[str, PlatformState] = {}
        self._handlers: list[Callable] = []
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._bots: dict[str, Any] = {}
        self._relay = None
        self.config: dict = {}
        self._load_config()

    def _load_config(self) -> dict:
        """从 HOOK.yaml 加载 gateway 配置"""
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    full_config = yaml.safe_load(f) or {}
                self.config = full_config.get("gateway", {})
            else:
                self.config = {}
        except Exception as e:
            logger.warning(f"HOOK.yaml 加载失败: {e}")
            self.config = {}

        return self.config

    def _get_platforms_config(self) -> dict:
        return self.config.get("platforms", {})

    def _get_routing_config(self) -> dict:
        return self.config.get("routing", {})

    def _get_ws_config(self) -> dict:
        return self.config.get("ws_relay", {})

    async def start_all(self):
        """启动所有已启用的平台"""
        self._running = True
        self._load_config()
        platforms = self._get_platforms_config()

        for name, pconfig in platforms.items():
            if not pconfig.get("enabled", False):
                continue

            self._platforms[name] = PlatformState(enabled=True)
            logger.info(f"Gateway 启动平台: {name}")

            if name == "telegram":
                await self._start_telegram(pconfig)
            elif name == "wechat":
                await self._start_wechat(pconfig)
            elif name == "slack":
                await self._start_slack(pconfig)

        # WebSocket 中继
        ws_config = self._get_ws_config()
        if ws_config.get("enabled", False):
            await self._start_ws_relay(ws_config)

        # 消息处理循环
        asyncio.create_task(self._message_loop())
        logger.info(f"Gateway 启动完成: {len(self._platforms)} 个平台")

    async def stop_all(self):
        """停止所有平台"""
        self._running = False
        for name, bot in self._bots.items():
            if hasattr(bot, "stop"):
                try:
                    await bot.stop()
                except Exception:
                    pass
        self._bots.clear()
        self._platforms.clear()

    async def _start_telegram(self, config: dict):
        """启动 Telegram Bot"""
        try:
            from gateway.telegram_bot import TelegramBot, TelegramConfig

            bot = TelegramBot(
                config=TelegramConfig(
                    token=os.path.expandvars(config.get("token", "")),
                    parse_mode="Markdown",
                ),
                on_message=self._handle_message,
            )
            self._bots["telegram"] = bot
            await bot.start(mode="polling")
            self._platforms["telegram"].connected = True
        except Exception as e:
            logger.error(f"Telegram 启动失败: {e}")
            self._platforms["telegram"] = PlatformState(enabled=True, error_count=1)

    async def _start_wechat(self, config: dict):
        """启动微信 Bot"""
        try:
            from gateway.wechat_bot import WeChatBot, WeChatConfig

            bot = WeChatBot(
                config=WeChatConfig(
                    webhook_url=os.path.expandvars(config.get("webhook_url", "")),
                ),
                on_message=self._handle_message,
            )
            self._bots["wechat"] = bot
            await bot.start()
            self._platforms["wechat"].connected = True
        except Exception as e:
            logger.error(f"微信 启动失败: {e}")
            self._platforms["wechat"] = PlatformState(enabled=True, error_count=1)

    async def _start_slack(self, config: dict):
        """启动 Slack 适配器"""
        try:
            from gateway.slack_adapter import SlackAdapter
            adapter = SlackAdapter(config)
            self._bots["slack"] = adapter
            await adapter.connect()
            self._platforms["slack"].connected = True
        except Exception as e:
            logger.error(f"Slack 启动失败: {e}")

    async def _start_ws_relay(self, config: dict):
        """启动 WebSocket 中继"""
        try:
            from gateway.relay import WebSocketRelay
            relay = WebSocketRelay(
                port=config.get("port", 9090),
                auth_token=os.path.expandvars(config.get("auth_token", "")),
            )
            self._relay = relay
            asyncio.create_task(relay.start())
            self._platforms["websocket"] = PlatformState(
                enabled=True, connected=True
            )
        except Exception as e:
            logger.error(f"WS 中继启动失败: {e}")

    async def _handle_message(self, msg_data: dict):
        """统一消息处理入口"""
        await self._message_queue.put(msg_data)

    async def _message_loop(self):
        """消息处理循环"""
        while self._running:
            try:
                msg_data = await asyncio.wait_for(
                    self._message_queue.get(), timeout=1.0
                )
                platform = msg_data.get("platform", "unknown")

                # 更新状态
                if platform in self._platforms:
                    self._platforms[platform].message_count += 1
                    self._platforms[platform].last_activity = time.time()

                # 调用所有处理程序
                for handler in self._handlers:
                    try:
                        result = handler(msg_data)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"Handler 执行失败: {e}")

                # 自动回复（如果启用）
                routing = self._get_routing_config()
                if routing.get("forward_to_agent", False):
                    await self._auto_reply(msg_data)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"消息循环异常: {e}")

    async def _auto_reply(self, msg_data: dict):
        """自动回复消息"""
        text = msg_data.get("text", "")
        if not text or not text.strip():
            return

        platform = msg_data.get("platform", "")
        chat_id = msg_data.get("chat_id", "")

        bot = self._bots.get(platform)
        if not bot:
            return

        routing = self._get_routing_config()
        max_len = routing.get("max_response_length", 4000)

        # 简单的 echo 回复（实际应由 agent 处理）
        reply = f"收到消息: {text[:100]}..."
        if hasattr(bot, "send_message"):
            await bot.send_message(chat_id, reply[:max_len])

    def register_handler(self, handler: Callable):
        """注册消息处理回调"""
        self._handlers.append(handler)

    def remove_handler(self, handler: Callable):
        """移除消息处理回调"""
        if handler in self._handlers:
            self._handlers.remove(handler)

    async def send_to_platform(self, platform: str, chat_id: str,
                              text: str) -> bool:
        """发送消息到指定平台"""
        bot = self._bots.get(platform)
        if not bot:
            return False

        routing = self._get_routing_config()
        max_len = routing.get("max_response_length", 4000)

        try:
            if hasattr(bot, "send_message"):
                await bot.send_message(chat_id, text[:max_len])
            elif hasattr(bot, "send_webhook"):
                await bot.send_webhook(text[:max_len])
            return True
        except Exception as e:
            logger.error(f"发送消息失败 [{platform}]: {e}")
            return False

    def get_status(self) -> dict:
        """获取网关状态"""
        platforms_status = {}
        for name, state in self._platforms.items():
            platforms_status[name] = {
                "enabled": state.enabled,
                "connected": state.connected,
                "messages": state.message_count,
                "errors": state.error_count,
                "last_activity": state.last_activity,
            }

        # 检查配置中的平台但未启动的
        config_platforms = self._get_platforms_config()
        for name, pconfig in config_platforms.items():
            if name not in platforms_status:
                platforms_status[name] = {
                    "enabled": pconfig.get("enabled", False),
                    "connected": False,
                    "configured": True,
                }

        return {
            "running": self._running,
            "platforms": platforms_status,
            "handlers": len(self._handlers),
            "relay": self._relay.get_stats() if self._relay else None,
        }

    def reload_config(self) -> dict:
        """重新加载配置"""
        self._load_config()
        return self.config


# 全局单例
_gateway: Optional[GatewayManager] = None


def get_gateway() -> GatewayManager:
    global _gateway
    if _gateway is None:
        _gateway = GatewayManager()
    return _gateway


# ── 注册到 manifest ──

def register_in_manifest(reg):
    """注册 Gateway 工具到 manifest"""
    from core.tool_registry import ToolDef
    gw = get_gateway()

    async def gateway_status(args):
        return {"success": True, **gw.get_status()}

    async def gateway_start(args):
        await gw.start_all()
        return {"success": True, "message": "Gateway 已启动",
                **gw.get_status()}

    async def gateway_stop(args):
        await gw.stop_all()
        return {"success": True, "message": "Gateway 已停止"}

    async def gateway_send(args):
        ok = await gw.send_to_platform(
            platform=args["platform"],
            chat_id=args["chat_id"],
            text=args["text"],
        )
        return {"success": ok}

    async def gateway_reload(args):
        config = gw.reload_config()
        return {"success": True, "config": config}

    async def gateway_list_platforms(args):
        config_platforms = gw._get_platforms_config()
        platforms = []
        for name, pconfig in config_platforms.items():
            platforms.append({
                "name": name,
                "enabled": pconfig.get("enabled", False),
                "connected": gw._platforms.get(name, PlatformState(False)).connected,
            })
        return {"success": True, "platforms": platforms}

    reg.register_many([
        ToolDef("gateway_status", "查看多平台网关状态",
                {"type":"object","properties":{},"required":[]},
                gateway_status, "gateway"),
        ToolDef("gateway_start", "启动所有已启用的平台网关",
                {"type":"object","properties":{},"required":[]},
                gateway_start, "gateway"),
        ToolDef("gateway_stop", "停止所有平台网关",
                {"type":"object","properties":{},"required":[]},
                gateway_stop, "gateway"),
        ToolDef("gateway_send", "向指定平台发送消息",
                {"type":"object","properties":{
                    "platform":{"type":"string","enum":["telegram","wechat","slack","websocket"]},
                    "chat_id":{"type":"string"},
                    "text":{"type":"string"},
                },"required":["platform","chat_id","text"]},
                gateway_send, "gateway"),
        ToolDef("gateway_reload", "重新加载 HOOK.yaml 配置",
                {"type":"object","properties":{},"required":[]},
                gateway_reload, "gateway"),
        ToolDef("gateway_platforms", "列出所有配置的平台",
                {"type":"object","properties":{},"required":[]},
                gateway_list_platforms, "gateway"),
    ])
