"""
P3-2: 消息中继 (Relay) — 多平台消息路由、过滤、转换
WebSocket relay for real-time bidirectional message forwarding
"""
import json, time, asyncio, logging, os, yaml
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from enum import Enum

logger = logging.getLogger("javis.gateway.relay")


class MessageDirection(Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


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
    platform: str
    chat_id: str
    user_id: str
    content: str
    priority: MessagePriority = MessagePriority.NORMAL
    role: str = "user"
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "id": self.id, "direction": self.direction.value,
            "platform": self.platform, "chat_id": self.chat_id,
            "user_id": self.user_id, "content": self.content,
            "priority": self.priority.value, "role": self.role,
            "timestamp": self.timestamp, "metadata": self.metadata,
        }


class FilterRule:
    def __init__(self, name: str, condition: Callable[[RelayMessage], bool]):
        self.name = name
        self.condition = condition

    def match(self, msg: RelayMessage) -> bool:
        try:
            return self.condition(msg)
        except Exception:
            return False


class TransformRule:
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
    """消息中继 — 多平台路由核心"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._filters: list[FilterRule] = []
        self._transforms: list[TransformRule] = []
        self._ws_clients: dict[str, list] = {}  # chat_id -> [websockets]
        self._message_log: list[RelayMessage] = []
        self._stats = {"in": 0, "out": 0, "dropped": 0}

    def add_filter(self, rule: FilterRule):
        self._filters.append(rule)

    def add_transform(self, rule: TransformRule):
        self._transforms.append(rule)

    def add_ws_client(self, chat_id: str, ws):
        if chat_id not in self._ws_clients:
            self._ws_clients[chat_id] = []
        self._ws_clients[chat_id].append(ws)
        logger.info(f"WS 客户端连接: {chat_id} ({len(self._ws_clients[chat_id])} clients)")

    def remove_ws_client(self, chat_id: str, ws):
        if chat_id in self._ws_clients:
            self._ws_clients[chat_id] = [c for c in self._ws_clients[chat_id] if c != ws]

    async def route_incoming(self, msg: RelayMessage) -> Optional[RelayMessage]:
        """路由入站消息"""
        self._stats["in"] += 1

        # 应用过滤器
        for f in self._filters:
            if f.match(msg):
                logger.debug(f"消息被过滤 [{f.name}]: {msg.id}")
                self._stats["dropped"] += 1
                return None

        # 应用转换
        for t in self._transforms:
            msg = t.apply(msg)

        # 记录日志
        self._message_log.append(msg)
        if len(self._message_log) > 500:
            self._message_log = self._message_log[-500:]

        return msg

    async def broadcast_outgoing(self, msg: RelayMessage):
        """广播出站消息到 WebSocket 客户端"""
        self._stats["out"] += 1
        self._message_log.append(msg)

        # 发送到匹配的 WS 客户端
        clients = self._ws_clients.get(msg.chat_id, [])
        clients += self._ws_clients.get("*", [])  # 全局广播

        dead_clients = []
        for ws in clients:
            try:
                await ws.send_text(json.dumps(msg.to_dict(), ensure_ascii=False))
            except Exception:
                dead_clients.append((msg.chat_id, ws))

        for chat_id, ws in dead_clients:
            self.remove_ws_client(chat_id, ws)

    def get_stats(self) -> dict:
        return {**self._stats, "ws_clients": sum(len(v) for v in self._ws_clients.values())}

    def get_recent_messages(self, limit: int = 50) -> list[dict]:
        return [m.to_dict() for m in self._message_log[-limit:]]


# ── HOOK.yaml 集成 ──

def load_hooks_config(hook_yaml_path: str = None) -> dict:
    """加载 HOOK.yaml 中的 gateway 配置"""
    if hook_yaml_path is None:
        hook_yaml_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "HOOK.yaml"
        )
    if not os.path.exists(hook_yaml_path):
        return {}

    try:
        with open(hook_yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return config.get("gateway", {})
    except Exception as e:
        logger.error(f"HOOK.yaml 加载失败: {e}")
        return {}


# ── WebSocket 中继服务 ──

class WebSocketRelay:
    """WebSocket 中继服务 — 实时双向消息转发"""

    def __init__(self, host: str = "0.0.0.0", port: int = 9090,
                auth_token: str = ""):
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.relay = MessageRelay()

    async def start(self):
        """启动 WebSocket 中继"""
        try:
            from fastapi import FastAPI, WebSocket, WebSocketDisconnect
            import uvicorn

            app = FastAPI(title="Javis Gateway Relay")
            relay = self.relay

            @app.websocket("/ws/{chat_id}")
            async def ws_endpoint(ws: WebSocket, chat_id: str):
                # 鉴权
                token = ws.query_params.get("token", "")
                if self.auth_token and token != self.auth_token:
                    await ws.close(code=4003, reason="未授权")
                    return

                await ws.accept()
                relay.add_ws_client(chat_id, ws)
                logger.info(f"WS 已连接: chat={chat_id}")

                try:
                    while True:
                        data = await ws.receive_text()
                        msg_data = json.loads(data)
                        rmsg = RelayMessage(
                            id=f"ws-{int(time.time()*1000)}",
                            direction=MessageDirection.INCOMING,
                            platform="websocket",
                            chat_id=chat_id,
                            user_id=msg_data.get("user_id", "ws_user"),
                            content=msg_data.get("content", ""),
                        )
                        processed = await relay.route_incoming(rmsg)
                        if processed:
                            await relay.broadcast_outgoing(processed)
                except WebSocketDisconnect:
                    logger.info(f"WS 断开: chat={chat_id}")
                except Exception as e:
                    logger.error(f"WS 错误: {e}")
                finally:
                    relay.remove_ws_client(chat_id, ws)

            @app.get("/health")
            async def health():
                return {"status": "ok", "stats": relay.get_stats()}

            @app.get("/stats")
            async def stats():
                return relay.get_stats()

            @app.get("/messages")
            async def messages(limit: int = 50):
                return {"messages": relay.get_recent_messages(limit)}

            config = uvicorn.Config(app, host=self.host, port=self.port,
                                   log_level="info")
            server = uvicorn.Server(config)
            logger.info(f"WebSocket 中继启动: ws://{self.host}:{self.port}/ws/{{chat_id}}")
            await server.serve()

        except ImportError:
            logger.warning("FastAPI/uvicorn 未安装，跳过 WS 中继")
        except Exception as e:
            logger.error(f"WS 中继启动失败: {e}")
