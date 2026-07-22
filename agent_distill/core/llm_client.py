"""
LLM 客户端 — 管理与 AI 模型的通信
支持: Anthropic / OpenAI / 本地 Ollama / 任意 OpenAI 兼容端点
"""
from __future__ import annotations
import json, logging, time, os
from typing import Optional, Any
from dataclasses import dataclass, field

from .types import Message, ToolCall, ToolDef, AgentConfig

logger = logging.getLogger("agent.llm")


@dataclass
class LLMResponse:
    """LLM 返回的完整响应"""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""                   # 思考推演文本
    finish_reason: str = "stop"           # stop / tool_calls / length / error
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient:
    """
    LLM 客户端抽象层。

    核心设计:
    - 统一的 chat() 接口，屏蔽不同 provider 的差异
    - 自动处理 API 响应中的 tool_calls
    - 支持流式和非流式两种模式
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._provider = None
        self._client = None
        self._api_key = None
        self._base_url = None
        self._init_client()

    def _init_client(self):
        """根据配置初始化对应的 API 客户端"""
        # 尝试 Anthropic
        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if api_key:
                self._client = anthropic.Anthropic(api_key=api_key)
                self._provider = "anthropic"
                self._api_key = api_key
                logger.info("LLM: 使用 Anthropic API")
                return
        except ImportError:
            pass

        # 尝试 OpenAI 兼容 (Ollama 等)
        try:
            from openai import OpenAI
            base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
            api_key = os.getenv("OPENAI_API_KEY", "ollama")
            self._client = OpenAI(base_url=base_url, api_key=api_key)
            self._provider = "openai"
            self._base_url = base_url
            self._api_key = api_key
            logger.info(f"LLM: 使用 OpenAI 兼容端点: {base_url}")
            return
        except ImportError:
            pass

        # 降级: 模拟客户端 (用于测试)
        self._provider = "mock"
        self._client = None
        logger.warning("LLM: 使用模拟客户端 (无真实 API 连接)")

    # ═══════════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════════

    def chat(
        self,
        messages: list[Message],
        tools: Optional[list[ToolDef]] = None,
        system_prompt: str = "",
        stream: bool = False,
    ) -> LLMResponse:
        """
        发送消息并获取响应。

        Args:
            messages: 对话历史
            tools: 可用工具列表
            system_prompt: 系统提示
            stream: 是否流式

        Returns:
            LLMResponse 包含文本回复和工具调用
        """
        if self._provider == "anthropic":
            return self._chat_anthropic(messages, tools, system_prompt, stream)
        elif self._provider == "openai":
            return self._chat_openai(messages, tools, system_prompt, stream)
        else:
            return self._chat_mock(messages, tools, system_prompt)

    # ═══════════════════════════════════════════════
    # 模拟实现 (用于测试)
    # ═══════════════════════════════════════════════

    def _chat_mock(self, messages, tools, system_prompt) -> LLMResponse:
        return LLMResponse(
            content="[模拟响应] 这是模拟 LLM 客户端的回复。",
            finish_reason="stop",
        )

    # ═══════════════════════════════════════════════
    # Anthropic 实现
    # ═══════════════════════════════════════════════

    def _chat_anthropic(
        self,
        messages: list[Message],
        tools: Optional[list[ToolDef]],
        system_prompt: str,
        stream: bool,
    ) -> LLMResponse:
        import anthropic

        # 构建 Anthropic 消息列表
        api_messages = []
        for msg in messages:
            if msg.role.value == "system":
                # Anthropic 的 system 是独立参数
                if not system_prompt:
                    system_prompt = msg.content
                continue

            entry: dict = {"role": msg.role.value, "content": msg.content}

            # 处理工具调用
            if msg.tool_calls:
                # Anthropic 需要 content 为 tool_use blocks
                blocks = []
                for tc in msg.tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                entry["content"] = blocks

            # 处理工具结果
            if msg.role.value == "tool":
                entry["content"] = [{
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content,
                }]

            api_messages.append(entry)

        # 构建工具定义
        anthropic_tools = []
        if tools:
            for t in tools:
                anthropic_tools.append({
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                })

        # 发送请求
        start = time.time()
        reply = self._client.messages.create(
            model=self.config.model,
            messages=api_messages,
            system=system_prompt if system_prompt else anthropic.NOT_GIVEN,
            tools=anthropic_tools if anthropic_tools else anthropic.NOT_GIVEN,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )
        elapsed = time.time() - start

        return self._parse_anthropic_response(reply, elapsed)

    def _parse_anthropic_response(self, reply, elapsed: float) -> LLMResponse:
        """解析 Anthropic 的响应"""
        content_text = ""
        tool_calls = []
        reasoning = ""

        for block in reply.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))
            elif block.type == "thinking":
                reasoning += block.thinking or ""

        logger.info(
            f"LLM 响应: text={len(content_text)}chars, "
            f"tools={len(tool_calls)}, time={elapsed:.1f}s"
        )

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            reasoning=reasoning,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage={
                "input_tokens": reply.usage.input_tokens,
                "output_tokens": reply.usage.output_tokens,
            },
        )

    # ═══════════════════════════════════════════════
    # OpenAI 实现
    # ═══════════════════════════════════════════════

    def _chat_openai(
        self,
        messages: list[Message],
        tools: Optional[list[ToolDef]],
        system_prompt: str,
        stream: bool,
    ) -> LLMResponse:
        # 构建 OpenAI 消息列表
        api_messages: list[dict] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            if msg.role.value == "system":
                # system 消息已移到顶层
                if not system_prompt and not api_messages:
                    api_messages.append({"role": "system", "content": msg.content})
                continue

            entry: dict = {"role": msg.role.value, "content": msg.content}

            # 工具调用
            if msg.tool_calls:
                entry["tool_calls"] = [tc.to_api_dict() for tc in msg.tool_calls]

            # 工具结果
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id

            api_messages.append(entry)

        # 构建 OpenAI 工具定义
        openai_tools = None
        if tools:
            openai_tools = [t.to_api_dict() for t in tools]

        # 发送
        start = time.time()
        reply = self._client.chat.completions.create(
            model=self.config.model,
            messages=api_messages,
            tools=openai_tools,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            stream=stream,
        )

        if stream:
            return self._handle_stream(reply)

        elapsed = time.time() - start
        return self._parse_openai_response(reply, elapsed)

    def _parse_openai_response(self, reply, elapsed: float) -> LLMResponse:
        """解析 OpenAI 响应"""
        choice = reply.choices[0]
        msg = choice.message

        content = msg.content or ""
        tool_calls = []
        reasoning = ""

        # 解析 reasoning_content (DeepSeek/O1 等)
        if hasattr(msg, "reasoning_content"):
            reasoning = msg.reasoning_content or ""

        # 解析工具调用
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = {}
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {"raw": tc.function.arguments}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        logger.info(
            f"LLM 响应: text={len(content)}chars, "
            f"tools={len(tool_calls)}, time={elapsed:.1f}s"
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning=reasoning,
            finish_reason=choice.finish_reason or "stop",
            usage={
                "input_tokens": reply.usage.prompt_tokens,
                "output_tokens": reply.usage.completion_tokens,
            },
        )

    def _handle_stream(self, stream) -> LLMResponse:
        """处理流式响应 (简化版, 实际会用 generator)"""
        content_parts = []
        for chunk in stream:
            if chunk.choices[0].delta.content:
                content_parts.append(chunk.choices[0].delta.content)
        return LLMResponse(content="".join(content_parts))


