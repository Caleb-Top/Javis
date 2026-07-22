"""
微信 Bot — 通过 Webhook / 企业微信 / itchat 接入
P3-2: WeChat bot with webhook receiver, message routing, and multi-backend support
"""
import asyncio, json, logging, os, hashlib, time
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass, field

logger = logging.getLogger("gateway.wechat")


@dataclass
class WeChatConfig:
    webhook_url: str = ""
    corp_id: str = ""       # 企业微信
    corp_secret: str = ""   # 企业微信
    agent_id: int = 0       # 企业微信
    token: str = ""         # 公众号 Token
    encoding_aes_key: str = ""
    allowed_users: list[str] = field(default_factory=list)


class WeChatBot:
    """微信 Bot — 支持企业微信 Webhook + 个人微信"""

    def __init__(self, config: WeChatConfig, on_message: Callable = None):
        self.config = config
        self._on_message = on_message
        self._access_token = ""
        self._token_expires = 0.0

    async def start(self):
        """启动 Bot"""
        logger.info("WeChat Bot 启动")
        if self.config.corp_id and self.config.corp_secret:
            await self._refresh_access_token()

    async def stop(self):
        logger.info("WeChat Bot 停止")

    async def _refresh_access_token(self) -> str:
        """刷新企业微信 access_token"""
        import aiohttp
        url = (
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
            f"?corpid={self.config.corp_id}"
            f"&corpsecret={self.config.corp_secret}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                if data.get("errcode") == 0:
                    self._access_token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 7200)
                    logger.info("企业微信 access_token 已刷新")
                else:
                    logger.error(f"获取 access_token 失败: {data}")
        return self._access_token

    async def _ensure_token(self):
        """确保 token 有效"""
        if time.time() > self._token_expires - 60:
            await self._refresh_access_token()

    async def send_message(self, user_id: str, text: str,
                          msg_type: str = "text") -> dict:
        """发送企业微信消息"""
        await self._ensure_token()
        import aiohttp
        url = (
            "https://qyapi.weixin.qq.com/cgi-bin/message/send"
            f"?access_token={self._access_token}"
        )
        payload = {
            "touser": user_id,
            "msgtype": msg_type,
            "agentid": self.config.agent_id,
        }
        if msg_type == "text":
            payload["text"] = {"content": text[:2000]}
        elif msg_type == "markdown":
            payload["markdown"] = {"content": text[:4000]}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                return await resp.json()

    async def send_webhook(self, text: str,
                          msg_type: str = "text") -> dict:
        """通过 Webhook 发送消息"""
        if not self.config.webhook_url:
            return {"errcode": -1, "errmsg": "No webhook URL configured"}

        import aiohttp
        payload = {}
        if msg_type == "text":
            payload = {
                "msgtype": "text",
                "text": {"content": text[:2000]},
            }
        elif msg_type == "markdown":
            payload = {
                "msgtype": "markdown",
                "markdown": {"content": text[:4000]},
            }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.config.webhook_url, json=payload, timeout=10
            ) as resp:
                return await resp.json()

    async def handle_webhook_message(self, body: dict) -> Optional[str]:
        """处理 Webhook 回调消息"""
        msg_type = body.get("MsgType", body.get("msgtype", ""))
        content = ""

        if msg_type == "text":
            content = body.get("Content", body.get("text", {}).get("content", ""))
        elif msg_type == "event":
            event = body.get("Event", "")
            if event == "subscribe":
                content = "/start"

        if not content:
            return None

        user_id = body.get("FromUserName", body.get("from", {}).get("userid", ""))
        user_name = body.get("FromUserName", "")

        # 白名单检查
        if self.config.allowed_users and user_id not in self.config.allowed_users:
            return None

        if self._on_message:
            try:
                response = self._on_message({
                    "platform": "wechat",
                    "chat_id": user_id,
                    "user_id": user_id,
                    "user_name": user_name,
                    "text": content,
                })
                if asyncio.iscoroutine(response):
                    response = await response
                return str(response)[:2000] if response else None
            except Exception as e:
                logger.error(f"WeChat 消息处理失败: {e}")
                return f"处理失败: {e}"

        return f"收到: {content[:200]}"

    async def get_user_info(self, user_id: str) -> dict:
        """获取企业微信用户信息"""
        await self._ensure_token()
        import aiohttp
        url = (
            "https://qyapi.weixin.qq.com/cgi-bin/user/get"
            f"?access_token={self._access_token}&userid={user_id}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                return await resp.json()


def create_bot(appid: str = "", secret: str = "",
              on_message: Callable = None) -> WeChatBot:
    """创建微信 Bot"""
    return WeChatBot(
        config=WeChatConfig(
            corp_id=appid or os.environ.get("WECHAT_CORP_ID", ""),
            corp_secret=secret or os.environ.get("WECHAT_CORP_SECRET", ""),
            webhook_url=os.environ.get("WECHAT_WEBHOOK_URL", ""),
        ),
        on_message=on_message,
    )
