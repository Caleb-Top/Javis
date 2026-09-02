from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.agent import Agent
from core.life.memory.contracts import RecallBundle


NOW = "2026-08-20T10:00:00.000Z"


class _Tools:
    def get_schemas(self):
        return []


class _LLM:
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    async def chat_with_tools(self, *, messages, tools, system):
        del messages, tools
        self.system_prompts.append(system)
        return SimpleNamespace(
            tool_calls=(),
            text="bounded response",
            reasoning_content="",
        )


class _ForbiddenLegacyMemory:
    def __getattr__(self, name):
        raise AssertionError(f"legacy memory was consulted: {name}")


def _bundle(text: str = "request-scoped-private-marker") -> RecallBundle:
    return RecallBundle.from_dict(
        {
            "schema_version": 1,
            "query_id": "query-prompt-integration",
            "context_id": "context-prompt-integration",
            "items": [
                {
                    "item_id": "episode-prompt-integration",
                    "item_kind": "experience_episode",
                    "prompt_text": text,
                    "occurred_at_utc": NOW,
                    "epistemic_label": "evidence_derived",
                    "source_citation_token": "citation-prompt-integration",
                    "owner_label": "actor",
                    "audience": "owner_private",
                    "confidence": 1.0,
                }
            ],
            "acl_epoch": 2,
            "index_generation": 3,
            "generated_at_utc": NOW,
            "truncated": False,
            "reason_code": None,
        }
    )


async def _collect(agent: Agent, **kwargs):
    return [
        event
        async for event in agent.chat(
            "Please handle this bounded request.",
            session_id="legacy-compatible-session",
            **kwargs,
        )
    ]


def _agent():
    llm = _LLM()
    forbidden = _ForbiddenLegacyMemory()
    agent = Agent(llm, _Tools(), brain=forbidden, learner=forbidden, engine=None)
    agent.max_retries = 0
    return agent, llm


def test_legacy_agent_chat_signature_runs_with_empty_request_memory():
    agent, llm = _agent()

    events = asyncio.run(_collect(agent))

    assert events[-1] == {"type": "done", "success": True}
    assert "request-scoped-private-marker" not in llm.system_prompts[-1]
    assert "brain_data" not in llm.system_prompts[-1]
    assert "JAVIS_REQUEST_MEMORY_V1" not in llm.system_prompts[-1]


def test_only_validated_request_bundle_enters_the_model_prompt_with_citation():
    agent, llm = _agent()
    bundle = _bundle()

    events = asyncio.run(_collect(agent, recall_bundle=bundle))

    assert events[-1] == {"type": "done", "success": True}
    prompt = llm.system_prompts[-1]
    assert "JAVIS_REQUEST_MEMORY_V1" in prompt
    assert "request-scoped-private-marker" in prompt
    assert "citation-prompt-integration" in prompt


def test_unvalidated_memory_mapping_is_rejected_before_model_dispatch():
    agent, llm = _agent()

    with pytest.raises(TypeError, match="RecallBundle"):
        asyncio.run(_collect(agent, recall_bundle=_bundle().to_dict()))

    assert llm.system_prompts == []
