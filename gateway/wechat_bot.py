"""微信 Bot 客户端"""
import logging
logger = logging.getLogger("gateway.wechat")

def create_bot(appid: str = ""):
    async def handler(message: str) -> str:
        return f"WeChat echo: {message[:500]}"
    return handler
