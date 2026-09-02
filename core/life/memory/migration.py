"""Read-only legacy Brain inventory and quarantined migration candidates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


_MAX_FILES = 10_000
_MAX_FILE_BYTES = 1_048_576
_MAX_PAYLOAD_BYTES = 16_384
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SENSITIVE_KEYS = frozenset(
    {"api_key", "authorization", "credential", "password", "secret", "token"}
)
_AUTO_CATEGORIES = (
    "conversation.user_msgs",
    "memory.summary",
    "self_learned",
    "self_reflection",
    "session.topic",
)
_PERMISSION_TERMS = (
    "approval bypass",
    "full_access",
    "permission",
    "root token",
    "授权绕过",
    "自动批准",
    "管理员权限",
    "免确认",
    "无需确认",
    "全权限",
)


class LegacyMigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LegacyManifestEntry:
    relative_path: str
    source_sha256: str
    byte_count: int
    item_kind: str
    parse_state: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "source_sha256": self.source_sha256,
            "byte_count": self.byte_count,
            "item_kind": self.item_kind,
            "parse_state": self.parse_state,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class LegacyMigrationManifest:
    schema_version: int
    migration_id: str
    legacy_root_hash: str
    entries: tuple[LegacyManifestEntry, ...]
    created_at_utc: str
    manifest_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "migration_id": self.migration_id,
            "legacy_root_hash": self.legacy_root_hash,
            "entries": [entry.to_dict() for entry in self.entries],
            "created_at_utc": self.created_at_utc,
            "manifest_digest": self.manifest_digest,
        }


@dataclass(frozen=True, slots=True)
class LegacyMemoryCandidate:
    schema_version: int
    candidate_id: str
    migration_id: str
    manifest_digest: str
    source_relative_path_hash: str
    source_sha256: str
    item_kind: str
    disposition: str
    reason_codes: tuple[str, ...]
    payload: Mapping[str, Any]
    created_at_utc: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("legacy candidate schema_version must equal 1")
        for value, name in (
            (self.candidate_id, "candidate_id"),
            (self.migration_id, "migration_id"),
            (self.item_kind, "item_kind"),
            (self.disposition, "disposition"),
        ):
            if type(value) is not str or not value or len(value) > 256:
                raise ValueError(f"{name} must be bounded text")
        for value, name in (
            (self.manifest_digest, "manifest_digest"),
            (self.source_relative_path_hash, "source_relative_path_hash"),
            (self.source_sha256, "source_sha256"),
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise ValueError(f"{name} must be lowercase SHA-256")
        if self.disposition not in {"candidate", "quarantined"}:
            raise ValueError("legacy candidate disposition is invalid")
        if not self.reason_codes or len(self.reason_codes) > 16:
            raise ValueError("legacy candidate requires bounded reason codes")
        frozen = json.loads(_canonical_json(dict(self.payload)))
        if len(_canonical_json(frozen).encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise ValueError("legacy candidate payload is too large")
        object.__setattr__(self, "payload", MappingProxyType(frozen))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "migration_id": self.migration_id,
            "manifest_digest": self.manifest_digest,
            "source_relative_path_hash": self.source_relative_path_hash,
            "source_sha256": self.source_sha256,
            "item_kind": self.item_kind,
            "disposition": self.disposition,
            "reason_codes": list(self.reason_codes),
            "payload": dict(self.payload),
            "created_at_utc": self.created_at_utc,
        }


class LegacyBrainScanner:
    """Inventory a fixed legacy root without following links or changing it."""

    def __init__(self, legacy_root: str | Path) -> None:
        root = Path(legacy_root).expanduser()
        if not root.is_absolute():
            raise ValueError("legacy_root must be absolute")
        self.legacy_root = Path(os.path.abspath(root))

    def scan(
        self,
        *,
        migration_id: str,
        created_at_utc: str | None = None,
    ) -> tuple[LegacyMigrationManifest, tuple[LegacyMemoryCandidate, ...]]:
        if not migration_id or len(migration_id) > 256:
            raise ValueError("migration_id must be bounded")
        created = created_at_utc or _utc_now()
        root_hash = hashlib.sha256(str(self.legacy_root).casefold().encode("utf-8")).hexdigest()
        parsed: list[tuple[LegacyManifestEntry, dict[str, Any]]] = []
        if self.legacy_root.is_symlink():
            raise LegacyMigrationError("legacy_root_symlink_rejected")
        files = [] if not self.legacy_root.exists() else sorted(
            (path for path in self.legacy_root.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(self.legacy_root).as_posix().casefold(),
        )
        if len(files) > _MAX_FILES:
            raise LegacyMigrationError("legacy_file_limit_exceeded")
        for path in files:
            relative = path.relative_to(self.legacy_root).as_posix()
            kind = _item_kind(relative)
            reasons: list[str] = []
            payload: dict[str, Any] = {}
            parse_state = "ignored"
            source = b""
            if path.is_symlink() or _has_link_parent(path, self.legacy_root):
                parse_state = "quarantined"
                reasons.append("source_link_rejected")
            else:
                try:
                    size = path.stat().st_size
                    if size > _MAX_FILE_BYTES:
                        raise LegacyMigrationError("source_file_too_large")
                    source = path.read_bytes()
                    if kind == "unknown":
                        reasons.append("unsupported_legacy_kind")
                    elif path.suffix.casefold() != ".json":
                        reasons.append("unsupported_legacy_format")
                    else:
                        value = json.loads(source.decode("utf-8"))
                        if not isinstance(value, dict):
                            raise ValueError("legacy JSON must be an object")
                        payload, redacted = _sanitize(value)
                        parse_state = "parsed"
                        reasons.extend(_classify(kind, payload, redacted=redacted))
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError, LegacyMigrationError) as exc:
                    parse_state = "quarantined"
                    reasons.append(
                        str(exc) if isinstance(exc, LegacyMigrationError) else "corrupt_legacy_json"
                    )
            source_sha = hashlib.sha256(source).hexdigest()
            entry = LegacyManifestEntry(
                relative_path=relative,
                source_sha256=source_sha,
                byte_count=len(source),
                item_kind=kind,
                parse_state=parse_state,
                reason_codes=tuple(sorted(set(reasons or ["ignored_non_memory_file"]))),
            )
            parsed.append((entry, payload))

        entries = tuple(entry for entry, _ in parsed)
        manifest_base = {
            "schema_version": 1,
            "migration_id": migration_id,
            "legacy_root_hash": root_hash,
            "entries": [entry.to_dict() for entry in entries],
            "created_at_utc": created,
        }
        digest = hashlib.sha256(_canonical_json(manifest_base).encode("utf-8")).hexdigest()
        manifest = LegacyMigrationManifest(
            schema_version=1,
            migration_id=migration_id,
            legacy_root_hash=root_hash,
            entries=entries,
            created_at_utc=created,
            manifest_digest=digest,
        )
        candidates: list[LegacyMemoryCandidate] = []
        for entry, payload in parsed:
            if entry.item_kind == "unknown" and entry.parse_state == "ignored":
                continue
            reasons = tuple(sorted(set(entry.reason_codes)))
            disposition = "candidate" if reasons == ("explicit_owned_legacy_statement",) else "quarantined"
            candidate_id = "legacy-" + hashlib.sha256(
                f"{migration_id}\0{entry.relative_path}\0{entry.source_sha256}".encode("utf-8")
            ).hexdigest()
            candidates.append(
                LegacyMemoryCandidate(
                    schema_version=1,
                    candidate_id=candidate_id,
                    migration_id=migration_id,
                    manifest_digest=digest,
                    source_relative_path_hash=hashlib.sha256(
                        entry.relative_path.encode("utf-8")
                    ).hexdigest(),
                    source_sha256=entry.source_sha256,
                    item_kind=entry.item_kind,
                    disposition=disposition,
                    reason_codes=reasons,
                    payload=payload,
                    created_at_utc=created,
                )
            )
        return manifest, tuple(candidates)

    @staticmethod
    def write_manifest(
        manifest: LegacyMigrationManifest,
        data_root: str | Path,
    ) -> Path:
        root = Path(data_root).expanduser()
        if not root.is_absolute():
            raise ValueError("data_root must be absolute")
        target_dir = Path(os.path.abspath(root)) / "memory" / "migrations" / manifest.migration_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "manifest.json"
        encoded = (_canonical_json(manifest.to_dict()) + "\n").encode("utf-8")
        descriptor, temp_name = tempfile.mkstemp(prefix=".manifest-", dir=target_dir)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        return target


def _item_kind(relative_path: str) -> str:
    head = relative_path.split("/", 1)[0].casefold()
    return {
        "episodes": "legacy_episode",
        "experiences": "legacy_experience",
        "facts": "legacy_fact",
        "procedural": "legacy_procedural",
        "semantic": "legacy_semantic",
    }.get(head, "unknown")


def _has_link_parent(path: Path, root: Path) -> bool:
    current = path.parent
    while current != root:
        if current == current.parent or current.is_symlink():
            return True
        current = current.parent
    return root.is_symlink()


def _classify(kind: str, payload: Mapping[str, Any], *, redacted: bool) -> list[str]:
    reasons: list[str] = []
    owner = payload.get("owner_subject_id")
    source = str(payload.get("source") or "")
    category = str(payload.get("category") or "")
    text = " ".join(
        str(payload.get(name) or "")
        for name in ("content", "intent", "lesson", "result", "user_input")
    ).casefold()
    if redacted:
        reasons.append("secret_redacted")
    if type(owner) is not str or not owner.strip():
        reasons.append("source_unowned")
    if not source or source in {"self", "self_reflection", "auto"}:
        reasons.append("source_unverified")
    if category.startswith(_AUTO_CATEGORIES):
        reasons.append("auto_generated_summary")
    if any(term in text for term in _PERMISSION_TERMS):
        reasons.append("permission_statement_quarantined")
    if kind in {"legacy_episode", "legacy_experience"} and not payload.get("source_terminal_event_id"):
        reasons.append("canonical_terminal_missing")
    if not reasons and owner and source == "conversation":
        reasons.append("explicit_owned_legacy_statement")
    return reasons or ["legacy_evidence_unverified"]


def _sanitize(value: Any) -> tuple[Any, bool]:
    redacted = False

    def visit(node: Any, depth: int = 0) -> Any:
        nonlocal redacted
        if depth > 8:
            raise ValueError("legacy payload is too deeply nested")
        if isinstance(node, Mapping):
            result = {}
            for key, child in node.items():
                if type(key) is not str or len(key) > 128:
                    raise ValueError("legacy payload key is invalid")
                lowered = key.casefold()
                if lowered in _SENSITIVE_KEYS or any(
                    marker in lowered for marker in ("password", "secret", "token")
                ):
                    result[key] = "[redacted]"
                    redacted = True
                else:
                    result[key] = visit(child, depth + 1)
            return result
        if isinstance(node, list):
            return [visit(item, depth + 1) for item in node[:128]]
        if node is None or isinstance(node, (bool, int, float)):
            return node
        return str(node)[:4096]

    sanitized = visit(value)
    if len(_canonical_json(sanitized).encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("legacy payload exceeds quarantine limit")
    return sanitized, redacted


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "LegacyBrainScanner",
    "LegacyManifestEntry",
    "LegacyMemoryCandidate",
    "LegacyMigrationError",
    "LegacyMigrationManifest",
]
