"""Progressive discovery and health metadata for registered Javis tools."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from core.tool_guardrails import TOOL_RISK_LEVELS
from core.tool_registry import ToolDef, ToolRegistry
from core.tool_result import ToolResult


RISK_ORDER = {
    "safe": 0,
    "low": 1,
    "medium": 2,
    "dangerous": 3,
    "critical": 4,
}
RISK_NAMES = {value: name for name, value in RISK_ORDER.items()}


@dataclass(frozen=True)
class ToolPreset:
    name: str
    categories: tuple[str, ...] = ()
    required_tags: tuple[str, ...] = ()
    excluded_tags: tuple[str, ...] = ()
    max_risk: str | None = None


class ToolCatalog:
    """Lightweight discovery facade over the authoritative ToolRegistry."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._presets: dict[str, ToolPreset] = {}

    def define_preset(self, preset: ToolPreset) -> None:
        if not preset.name.strip():
            raise ValueError("preset name cannot be empty")
        if preset.max_risk is not None:
            self._risk_value(preset.max_risk)
        self._presets[preset.name] = preset

    def list_tools(
        self,
        *,
        preset: str | None = None,
        categories: tuple[str, ...] | list[str] = (),
        tags: tuple[str, ...] | list[str] = (),
        max_risk: str | None = None,
    ) -> list[dict[str, Any]]:
        selected_preset = self._presets.get(preset) if preset else None
        if preset and selected_preset is None:
            raise KeyError(f"unknown tool preset: {preset}")
        category_filter = set(categories or (selected_preset.categories if selected_preset else ()))
        tag_filter = set(tags or (selected_preset.required_tags if selected_preset else ()))
        excluded_tags = set(selected_preset.excluded_tags if selected_preset else ())
        risk_filter = max_risk or (selected_preset.max_risk if selected_preset else None)
        max_risk_value = self._risk_value(risk_filter) if risk_filter else None

        results = []
        for name in sorted(self.registry.list_all()):
            tool = self.registry.get(name)
            if tool is None:
                continue
            tool_tags = set(tool.tags)
            if category_filter and tool.category not in category_filter:
                continue
            if tag_filter and not tag_filter.issubset(tool_tags):
                continue
            if excluded_tags.intersection(tool_tags):
                continue
            if max_risk_value is not None and self._risk_value(self._risk_name(tool)) > max_risk_value:
                continue
            results.append(self._summary(tool))
        return results

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        preset: str | None = None,
        max_risk: str | None = None,
    ) -> list[dict[str, Any]]:
        terms = [term for term in re.split(r"[^\w-]+", query.lower()) if term]
        candidates = self.list_tools(preset=preset, max_risk=max_risk)
        if not terms:
            return candidates[:max(0, limit)]
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for item in candidates:
            name = item["name"].lower()
            description = item["description"].lower()
            category = item["category"].lower()
            tags = [tag.lower() for tag in item["tags"]]
            searchable = " ".join((name, description, category, *tags))
            if any(term not in searchable for term in terms):
                continue
            score = 0
            for term in terms:
                if term == name:
                    score += 100
                elif name.startswith(term):
                    score += 60
                elif term in name:
                    score += 40
                if term in tags:
                    score += 30
                if term == category:
                    score += 20
                if term in description:
                    score += 10
            ranked.append((-score, item["name"], item))
        ranked.sort(key=lambda value: (value[0], value[1]))
        return [item for _, _, item in ranked[:max(0, limit)]]

    def inspect(self, name: str) -> dict[str, Any] | None:
        tool = self.registry.get(name)
        if tool is None:
            return None
        return {
            **self._summary(tool),
            "parameters": copy.deepcopy(tool.parameters),
            "permission": tool.permission,
            "timeout_seconds": tool.timeout_seconds,
        }

    def schemas_for(
        self,
        query: str,
        *,
        limit: int = 12,
        preset: str | None = None,
        max_risk: str | None = None,
    ) -> list[dict[str, Any]]:
        selected = self.search(query, limit=limit, preset=preset, max_risk=max_risk)
        schemas = []
        for item in selected:
            tool = self.registry.get(item["name"])
            if tool is None:
                continue
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": copy.deepcopy(tool.parameters),
                },
            })
        return schemas

    async def execute(
        self,
        name: str,
        params: dict | None,
        *,
        confirmed: bool = False,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ToolResult:
        return await self.registry.execute(
            name,
            params,
            confirmed=confirmed,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    def health(self, name: str | None = None) -> list[dict[str, Any]]:
        names = [name] if name else sorted(self.registry.list_all())
        results = []
        for tool_name in names:
            tool = self.registry.get(tool_name)
            if tool is None:
                continue
            results.append({
                "name": tool_name,
                "category": tool.category,
                **self.registry.execution_stats(tool_name),
            })
        return results

    def _summary(self, tool: ToolDef) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "category": tool.category,
            "tags": list(tool.tags),
            "source": tool.source,
            "risk": self._risk_name(tool),
        }

    @staticmethod
    def _risk_name(tool: ToolDef) -> str:
        if tool.risk:
            return tool.risk
        return RISK_NAMES.get(TOOL_RISK_LEVELS.get(tool.name, 2), "medium")

    @staticmethod
    def _risk_value(risk: str) -> int:
        try:
            return RISK_ORDER[risk]
        except KeyError as exc:
            raise ValueError(f"unknown tool risk: {risk}") from exc
