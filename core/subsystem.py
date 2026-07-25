"""Standard lifecycle contract for runtime-mounted Javis subsystems."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SubsystemStatus:
    state: str
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseSubsystem(Protocol):
    name: str

    def start(self, runtime: Any) -> None:
        ...

    def status(self) -> SubsystemStatus | dict[str, Any]:
        ...

    def stop(self) -> None:
        ...
