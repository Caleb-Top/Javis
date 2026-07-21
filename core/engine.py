"""Javis 计算引擎 — 云API为主, 本地模型为备用算力

架构:
  用户请求 → 引擎调度器 → 首选: DeepSeek云API (快/强)
                              ↓ 失败时自动降级
                           备用: Ollama本地 deepseek-r1:8b (免费/隐私)

  模型文件位置: D:\Javis\ollama_models\  (迁移后)
  Ollama API:   http://localhost:11434/v1
"""

import os, json, logging, time, asyncio
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("engine")

# ── 配置 ──
PRIMARY_PROVIDER = "deepseek"        # 主算力: 云端 API
FALLBACK_PROVIDER = "local"          # 备用算力: 本地 Ollama
OLLAMA_BASE_URL = "http://localhost:11434/v1"
LOCAL_MODEL = "deepseek-r1:8b"       # 本地模型

# 模型能力分级 (用于任务路由)
MODEL_CAPABILITIES = {
    "deepseek-v4-pro":   {"speed": 9, "reasoning": 10, "tool_call": 10, "cost": "api"},
    "deepseek-v4-flash": {"speed": 10, "reasoning": 7, "tool_call": 9, "cost": "api"},
    "deepseek-chat":     {"speed": 10, "reasoning": 6, "tool_call": 9, "cost": "api"},
    "deepseek-r1:8b":    {"speed": 4,  "reasoning": 8, "tool_call": 5, "cost": "local"},
}


@dataclass
class ModelRoute:
    """一次推理的路由信息"""
    provider: str = ""
    model: str = ""
    is_fallback: bool = False
    latency_ms: float = 0.0
    error: str = ""


class InferenceEngine:
    """智能推理引擎: 自动路由、故障降级、热切换"""

    def __init__(self, llm_client):
        self.llm = llm_client
        self._fallback_active = False
        self._consecutive_failures = 0
        self._route_history: list[ModelRoute] = []
        self._saved_provider = llm_client.provider
        self._saved_model = llm_client.model

    @property
    def is_using_fallback(self) -> bool:
        return self._fallback_active

    async def chat_with_fallback(self, messages, tools, system) -> tuple:
        """带自动降级的 LLM 调用"""
        route = ModelRoute()

        # 主路径: 尝试云 API
        if not self._fallback_active:
            try:
                t0 = time.time()
                resp = await self.llm.chat_with_tools(messages, tools, system)
                route.latency_ms = (time.time() - t0) * 1000
                route.provider = self.llm.provider
                route.model = self.llm.model
                self._consecutive_failures = 0
                self._route_history.append(route)
                return resp, route
            except Exception as e:
                err_str = str(e)
                self._consecutive_failures += 1
                route.error = err_str[:100]
                logger.warning(f"云API失败 ({self._consecutive_failures}次): {err_str[:80]}")

                # 连续失败2次 → 降级到本地
                if self._consecutive_failures >= 2:
                    logger.info("⚠️ 云API连续失败, 自动降级到本地 Ollama")
                    self._fallback_active = True
                    # 保存当前配置以便恢复
                    self._saved_provider = self.llm.provider
                    self._saved_model = self.llm.model
                    # 切到本地
                    self._switch_to_local()

        # 备用路径: 本地 Ollama
        try:
            t0 = time.time()
            resp = await self.llm.chat_with_tools(messages, tools, system)
            route.latency_ms = (time.time() - t0) * 1000
            route.provider = "local"
            route.model = LOCAL_MODEL
            route.is_fallback = True
            self._route_history.append(route)
            return resp, route
        except Exception as e:
            route.error = str(e)[:100]
            route.is_fallback = True
            logger.error(f"本地模型也失败: {e}")
            raise

    def restore_primary(self):
        """恢复主算力 (云API)"""
        if self._fallback_active:
            self._switch_to_cloud()
            self._fallback_active = False
            self._consecutive_failures = 0
            logger.info("🔄 已恢复云API主算力")

    def get_power_status(self) -> dict:
        """获取算力状态"""
        return {
            "primary": f"{self.llm.provider}/{self.llm.model}",
            "fallback": f"local/{LOCAL_MODEL}" if not self._fallback_active else "当前在用",
            "active": "fallback" if self._fallback_active else "primary",
            "consecutive_failures": self._consecutive_failures,
            "local_model_path": "D:\\Javis\\ollama_models",
        }

    def _switch_to_local(self):
        """切换到本地 Ollama"""
        self.llm.switch_provider("local", LOCAL_MODEL, OLLAMA_BASE_URL)
        logger.info(f"切换到本地模型: {LOCAL_MODEL}")

    def _switch_to_cloud(self):
        """恢复云API"""
        self.llm.switch_provider(self._saved_provider, self._saved_model)
        logger.info(f"恢复云API: {self.llm.provider}/{self.llm.model}")
