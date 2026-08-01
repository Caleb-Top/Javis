"""Persistent discovery and governance catalog for Javis skills."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from core.events import EventBus, EventType

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


SCHEMA_VERSION = 1
ALLOWED_LICENSES = {
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC0-1.0",
    "ISC",
    "MPL-2.0",
}
EVALUATION_STATES = {"untested", "passed", "failed"}
SKILL_STATES = {"candidate", "staged", "active", "disabled", "rejected"}


class SkillGovernanceError(ValueError):
    """Raised when a skill cannot pass a governance transition."""


class SkillCatalog:
    """Indexes skill documents without importing or executing their content."""

    def __init__(self, path: str | Path, event_bus: EventBus | None = None):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.event_bus = event_bus
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._fts_enabled = False
        self._initialize()

    @property
    def fts_enabled(self) -> bool:
        return self._fts_enabled

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    name TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    license TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evaluation_status TEXT NOT NULL,
                    evaluation_score REAL,
                    evaluation_details_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            try:
                self._connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS skill_search USING fts5(name, description, tags)"
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def discover(
        self,
        root: str | Path,
        *,
        source: str,
        default_license: str | None = None,
    ) -> list[str]:
        root_path = Path(root).expanduser().resolve()
        discovered = []
        for path in sorted(root_path.rglob("SKILL.md"), key=lambda value: str(value).lower()):
            skill = self.register_document(
                path,
                source=source,
                default_license=default_license,
            )
            discovered.append(skill["name"])
        return discovered

    def register_document(
        self,
        path: str | Path,
        *,
        source: str,
        default_license: str | None = None,
    ) -> dict[str, Any]:
        document_path = Path(path).expanduser().resolve()
        content = document_path.read_text(encoding="utf-8")
        metadata, body = _parse_skill_document(content)
        name = str(metadata.get("name") or document_path.parent.name).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
            raise SkillGovernanceError(f"invalid skill name: {name}")
        description = str(metadata.get("description") or _first_content_line(body) or name).strip()
        version = str(metadata.get("version") or "0.0.0").strip()
        license_name = _normalize_license(metadata.get("license") or default_license)
        tags = _normalize_tags(metadata.get("tags"))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = time.time()

        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT content_hash, status, evaluation_status, evaluation_score, "
                "evaluation_details_json, created_at FROM skills WHERE name = ?",
                (name,),
            ).fetchone()
            content_changed = existing is not None and existing["content_hash"] != content_hash
            if existing is None or content_changed:
                status = "candidate"
                evaluation_status = "untested"
                evaluation_score = None
                evaluation_details_json = "{}"
            else:
                status = existing["status"]
                evaluation_status = existing["evaluation_status"]
                evaluation_score = existing["evaluation_score"]
                evaluation_details_json = existing["evaluation_details_json"]
            created_at = float(existing["created_at"]) if existing is not None else now
            self._connection.execute(
                """
                INSERT INTO skills (
                    name, description, path, source, license, version, status,
                    evaluation_status, evaluation_score, evaluation_details_json,
                    tags_json, metadata_json, content_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description=excluded.description,
                    path=excluded.path,
                    source=excluded.source,
                    license=excluded.license,
                    version=excluded.version,
                    status=excluded.status,
                    evaluation_status=excluded.evaluation_status,
                    evaluation_score=excluded.evaluation_score,
                    evaluation_details_json=excluded.evaluation_details_json,
                    tags_json=excluded.tags_json,
                    metadata_json=excluded.metadata_json,
                    content_hash=excluded.content_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    name,
                    description,
                    str(document_path),
                    str(source or "unknown"),
                    license_name,
                    version,
                    status,
                    evaluation_status,
                    evaluation_score,
                    evaluation_details_json,
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    content_hash,
                    created_at,
                    now,
                ),
            )
            self._sync_search(name, description, tags)
        skill = self.get(name)
        self._publish(
            EventType.SKILL_DISCOVERED,
            {
                "name": name,
                "source": source,
                "license": license_name,
                "status": skill["status"],
                "content_changed": content_changed,
            },
        )
        return skill

    def _sync_search(self, name: str, description: str, tags: list[str]) -> None:
        if not self._fts_enabled:
            return
        self._connection.execute("DELETE FROM skill_search WHERE name = ?", (name,))
        self._connection.execute(
            "INSERT INTO skill_search(name, description, tags) VALUES (?, ?, ?)",
            (name, description, " ".join(tags)),
        )

    def get(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM skills WHERE name = ?", (name,)).fetchone()
        return _row_to_skill(row) if row is not None else None

    def list_skills(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        values: list[Any] = []
        if status:
            clauses.append("status = ?")
            values.append(status)
        if source:
            clauses.append("source = ?")
            values.append(source)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM skills{where} ORDER BY name COLLATE NOCASE",
                values,
            ).fetchall()
        return [_row_to_skill(row) for row in rows]

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        terms = [term for term in re.split(r"[^\w.-]+", query.lower()) if term]
        if not terms:
            return self.list_skills()[:max(0, limit)]
        names: list[str] = []
        if self._fts_enabled:
            expression = " AND ".join(f'"{term.replace(chr(34), "")}"*' for term in terms)
            try:
                with self._lock:
                    rows = self._connection.execute(
                        "SELECT name FROM skill_search WHERE skill_search MATCH ? "
                        "ORDER BY bm25(skill_search), name COLLATE NOCASE LIMIT ?",
                        (expression, max(0, limit)),
                    ).fetchall()
                names = [str(row["name"]) for row in rows]
            except sqlite3.OperationalError:
                names = []
        if names:
            return [skill for name in names if (skill := self.get(name)) is not None]

        ranked = []
        for skill in self.list_skills():
            name = skill["name"].lower()
            description = skill["description"].lower()
            tags = [tag.lower() for tag in skill["tags"]]
            searchable = " ".join((name, description, *tags))
            if any(term not in searchable for term in terms):
                continue
            score = sum(
                (100 if term == name else 60 if name.startswith(term) else 40 if term in name else 0)
                + (30 if term in tags else 0)
                + (10 if term in description else 0)
                for term in terms
            )
            ranked.append((-score, skill["name"], skill))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [skill for _, _, skill in ranked[:max(0, limit)]]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = int(self._connection.execute("SELECT COUNT(*) FROM skills").fetchone()[0])
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS count FROM skills GROUP BY status ORDER BY status"
            ).fetchall()
        return {
            "total": total,
            "by_status": {str(row["status"]): int(row["count"]) for row in rows},
            "fts_enabled": self._fts_enabled,
        }

    def record_evaluation(
        self,
        name: str,
        status: str,
        *,
        score: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in EVALUATION_STATES:
            raise SkillGovernanceError(f"invalid evaluation status: {status}")
        if self.get(name) is None:
            raise KeyError(name)
        if score is not None and not 0 <= float(score) <= 1:
            raise SkillGovernanceError("evaluation score must be between 0 and 1")
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE skills SET evaluation_status = ?, evaluation_score = ?, "
                "evaluation_details_json = ?, updated_at = ? WHERE name = ?",
                (
                    status,
                    float(score) if score is not None else None,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                    time.time(),
                    name,
                ),
            )
        skill = self.get(name)
        self._publish(
            EventType.SKILL_EVALUATED,
            {"name": name, "status": status, "score": score},
        )
        return skill

    def promote(self, name: str, target_status: str) -> dict[str, Any]:
        if target_status not in SKILL_STATES:
            raise SkillGovernanceError(f"invalid skill status: {target_status}")
        skill = self.get(name)
        if skill is None:
            raise KeyError(name)
        current = skill["status"]
        transitions = {
            "candidate": {"staged", "rejected"},
            "staged": {"active", "candidate", "rejected", "disabled"},
            "active": {"disabled", "candidate"},
            "disabled": {"staged", "candidate", "rejected"},
            "rejected": {"candidate"},
        }
        if target_status not in transitions.get(current, set()):
            raise SkillGovernanceError(f"invalid skill transition: {current} -> {target_status}")
        if target_status in {"staged", "active"}:
            if skill["license"] not in ALLOWED_LICENSES:
                raise SkillGovernanceError(f"license is not approved: {skill['license']}")
            if skill["evaluation_status"] != "passed":
                raise SkillGovernanceError("skill evaluation has not passed")
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE skills SET status = ?, updated_at = ? WHERE name = ?",
                (target_status, time.time(), name),
            )
        updated = self.get(name)
        self._publish(
            EventType.SKILL_STATUS_CHANGED,
            {"name": name, "previous_status": current, "status": target_status},
        )
        return updated

    def _publish(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event_type, payload, source="skill_catalog")


def _parse_skill_document(content: str) -> tuple[dict[str, Any], str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return {}, content
    raw_metadata = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:])
    if yaml is not None:
        parsed = yaml.safe_load(raw_metadata) or {}
        return (parsed if isinstance(parsed, dict) else {}), body
    metadata: dict[str, Any] = {}
    for line in raw_metadata.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("'\"")
    return metadata, body


def _first_content_line(body: str) -> str:
    for line in body.splitlines():
        value = line.strip().lstrip("#").strip()
        if value:
            return value
    return ""


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip().strip("[]")
        values = stripped.split(",") if "," in stripped else stripped.split()
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = ()
    normalized = []
    seen = set()
    for item in values:
        tag = str(item).strip()
        key = tag.lower()
        if not tag or key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
    return normalized


def _normalize_license(value: Any) -> str:
    license_name = str(value or "UNKNOWN").strip()
    aliases = {
        "apache 2.0": "Apache-2.0",
        "apache-2": "Apache-2.0",
        "mit license": "MIT",
        "cc0": "CC0-1.0",
    }
    return aliases.get(license_name.lower(), license_name or "UNKNOWN")


def _row_to_skill(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "description": row["description"],
        "path": row["path"],
        "source": row["source"],
        "license": row["license"],
        "version": row["version"],
        "status": row["status"],
        "evaluation_status": row["evaluation_status"],
        "evaluation_score": row["evaluation_score"],
        "evaluation_details": json.loads(row["evaluation_details_json"] or "{}"),
        "tags": json.loads(row["tags_json"] or "[]"),
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "content_hash": row["content_hash"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
