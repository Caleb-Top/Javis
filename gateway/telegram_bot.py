"""Telegram Bot 客户端"""
import logging
logger = logging.getLogger("gateway.telegram")

def create_bot(token: str = ""):
    """创建 Telegram Bot handler"""
    async def handler(message: str) -> str:
        return f"Telegram echo: {message[:500]}"
    return handler
