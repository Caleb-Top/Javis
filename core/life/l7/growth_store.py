"""Append-only persistence and deployment gates for governed L7 growth."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from core.runtime_access import RuntimeAccessPrincipal

from .contracts import (
    CandidateState,
    CandidateTransitionV1,
    GrowthActorKind,
    GrowthCandidateV1,
    GrowthDecisionKind,
    GrowthDecisionV1,
    SkillArtifactV1,
    SkillDeploymentV1,
    canonical_json_bytes,
)
from .layout import DataRootLayout


STORE_SCHEMA_VERSION = 1
DATABASE_RELATIVE_PATH = Path("growth") / "candidates" / "growth.sqlite3"
GENESIS_HASH = "0" * 64
MAX_QUERY_LIMIT = 500
_HASH = re.compile(r"[0-9a-f]{64}")
_CHAIN_DOMAIN = b"javis.life.l7.growth-chain.v1\x00"
_DECISION_STATES = {
    GrowthDecisionKind.APPROVE: CandidateState.AWAITING_APPROVAL,
    GrowthDecisionKind.REJECT: CandidateState.AWAITING_APPROVAL,
    GrowthDecisionKind.REVOKE: CandidateState.ACTIVE,
}
_DECISION_TARGETS = {
    CandidateState.APPROVED: GrowthDecisionKind.APPROVE,
    CandidateState.REJECTED: GrowthDecisionKind.REJECT,
    CandidateState.REVOKED: GrowthDecisionKind.REVOKE,
}
_APPEND_ONLY_TABLES = (
    "candidate_versions",
    "candidate_chain",
    "candidate_transitions",
    "growth_decisions",
    "skill_artifacts",
    "skill_deployments",
)


class GrowthStoreError(RuntimeError):
    pass


class GrowthStoreClosedError(GrowthStoreError):
    pass


class GrowthStoreConflictError(GrowthStoreError):
    pass


class GrowthStoreCASMismatchError(GrowthStoreConflictError):
    pass


class GrowthStoreAuthorizationError(GrowthStoreConflictError):
    pass


class GrowthStoreIntegrityError(GrowthStoreError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    candidate: GrowthCandidateV1
    state: CandidateState
    revision: int
    updated_at_utc: str
    last_sequence: int
    last_chain_hash: str

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id

    @property
    def candidate_version_id(self) -> str:
        return self.candidate.candidate_version_id


@dataclass(frozen=True, slots=True)
class CandidateHead:
    candidate_id: str
    candidate_version_id: str
    version_number: int
    revision: int


@dataclass(frozen=True, slots=True)
class DeploymentGate:
    allowed: bool
    reason_code: str
    deployment: SkillDeploymentV1 | None = None


@dataclass(frozen=True, slots=True)
class GrowthIntegrityReport:
    ok: bool
    schema_version: int
    candidates_scanned: int
    events_scanned: int
    artifacts_scanned: int
    errors: tuple[str, ...]


_SCHEMA = (
    """
    CREATE TABLE store_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE candidate_versions (
        candidate_version_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        previous_version_id TEXT,
        previous_content_hash TEXT,
        owner_subject_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        UNIQUE(candidate_id, version_number),
        UNIQUE(candidate_id, candidate_version_id)
    )
    """,
    """
    CREATE TABLE candidate_heads (
        candidate_id TEXT PRIMARY KEY,
        candidate_version_id TEXT NOT NULL REFERENCES candidate_versions(candidate_version_id),
        version_number INTEGER NOT NULL,
        revision INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE candidate_projection (
        candidate_version_id TEXT PRIMARY KEY REFERENCES candidate_versions(candidate_version_id),
        state TEXT NOT NULL,
        revision INTEGER NOT NULL,
        updated_at_utc TEXT NOT NULL,
        last_sequence INTEGER NOT NULL,
        last_chain_hash TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE candidate_chain (
        candidate_version_id TEXT NOT NULL REFERENCES candidate_versions(candidate_version_id),
        sequence INTEGER NOT NULL,
        event_kind TEXT NOT NULL CHECK(event_kind IN ('candidate', 'transition', 'decision', 'artifact', 'deployment')),
        event_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        previous_hash TEXT NOT NULL,
        chain_hash TEXT NOT NULL,
        PRIMARY KEY(candidate_version_id, sequence),
        UNIQUE(candidate_version_id, event_kind, event_id),
        UNIQUE(candidate_version_id, chain_hash)
    )
    """,
    """
    CREATE TABLE candidate_transitions (
        transition_id TEXT PRIMARY KEY,
        candidate_version_id TEXT NOT NULL REFERENCES candidate_versions(candidate_version_id),
        idempotency_key TEXT NOT NULL,
        expected_revision INTEGER NOT NULL,
        from_state TEXT NOT NULL,
        to_state TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        previous_chain_hash TEXT NOT NULL,
        chain_hash TEXT NOT NULL,
        UNIQUE(candidate_version_id, idempotency_key),
        UNIQUE(candidate_version_id, sequence),
        FOREIGN KEY(candidate_version_id, sequence)
            REFERENCES candidate_chain(candidate_version_id, sequence)
    )
    """,
    """
    CREATE TABLE growth_decisions (
        decision_id TEXT PRIMARY KEY,
        candidate_version_id TEXT NOT NULL REFERENCES candidate_versions(candidate_version_id),
        idempotency_key TEXT NOT NULL,
        expected_revision INTEGER NOT NULL,
        decision_kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        previous_chain_hash TEXT NOT NULL,
        chain_hash TEXT NOT NULL,
        UNIQUE(candidate_version_id, idempotency_key),
        UNIQUE(candidate_version_id, sequence),
        FOREIGN KEY(candidate_version_id, sequence)
            REFERENCES candidate_chain(candidate_version_id, sequence)
    )
    """,
    """
    CREATE TABLE skill_artifacts (
        artifact_id TEXT PRIMARY KEY,
        candidate_version_id TEXT NOT NULL REFERENCES candidate_versions(candidate_version_id),
        payload_sha256 TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        previous_chain_hash TEXT NOT NULL,
        chain_hash TEXT NOT NULL,
        UNIQUE(candidate_version_id, artifact_id),
        UNIQUE(candidate_version_id, sequence),
        FOREIGN KEY(candidate_version_id, sequence)
            REFERENCES candidate_chain(candidate_version_id, sequence)
    )
    """,
    """
    CREATE TABLE skill_deployments (
        deployment_id TEXT PRIMARY KEY,
        candidate_version_id TEXT NOT NULL REFERENCES candidate_versions(candidate_version_id),
        artifact_id TEXT NOT NULL REFERENCES skill_artifacts(artifact_id),
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        previous_chain_hash TEXT NOT NULL,
        chain_hash TEXT NOT NULL,
        UNIQUE(candidate_version_id, deployment_id),
        UNIQUE(candidate_version_id, sequence),
        FOREIGN KEY(candidate_version_id, sequence)
            REFERENCES candidate_chain(candidate_version_id, sequence)
    )
    """,
    "CREATE INDEX candidate_version_order ON candidate_versions(candidate_id, version_number)",
    "CREATE INDEX candidate_state_query ON candidate_projection(state, updated_at_utc)",
    "CREATE INDEX candidate_transition_order ON candidate_transitions(candidate_version_id, sequence)",
    "CREATE INDEX growth_decision_order ON growth_decisions(candidate_version_id, sequence)",
    "CREATE INDEX artifact_candidate ON skill_artifacts(candidate_version_id, sequence)",
    "CREATE INDEX deployment_candidate ON skill_deployments(candidate_version_id, sequence)",
)


def _chain_hash(previous_hash: str, event_kind: str, event_id: str, payload_hash: str) -> str:
    material = b"\x00".join(
        (
            previous_hash.encode("ascii"),
            event_kind.encode("ascii"),
            event_id.encode("utf-8"),
            payload_hash.encode("ascii"),
        )
    )
    return hashlib.sha256(_CHAIN_DOMAIN + material).hexdigest()


def _permission_delta_hash(candidate: GrowthCandidateV1) -> str:
    permission_delta = candidate.to_dict()["permission_delta"]
    return hashlib.sha256(canonical_json_bytes(permission_delta)).hexdigest()


def _require_limit(limit: Any) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_QUERY_LIMIT:
        raise ValueError(f"limit must be in [1, {MAX_QUERY_LIMIT}]")
    return limit


def _require_hash(value: str, field_name: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256 hex")
    return value


def _subject_for_principal(principal: RuntimeAccessPrincipal) -> str:
    return "subject-primary-" + principal.client_id_hash[:32]


def _require_local_user(
    principal: RuntimeAccessPrincipal | None,
    actor_subject_id: str,
) -> None:
    if not isinstance(principal, RuntimeAccessPrincipal):
        raise GrowthStoreAuthorizationError("local user authority is required")
    if "evolution.write" not in principal.scopes:
        raise GrowthStoreAuthorizationError("evolution.write scope is required")
    if principal.binding_source not in {
        "packaged_desktop",
        "development_web",
        "loopback_web",
    }:
        raise GrowthStoreAuthorizationError("decision must originate from the local runtime")
    if _subject_for_principal(principal) != actor_subject_id:
        raise GrowthStoreAuthorizationError("decision actor is not bound to the local principal")


class GrowthStore:
    """Immutable candidate history with exact artifact and approval gates."""

    def __init__(
        self,
        data_root: DataRootLayout | str | Path,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 60_000:
            raise ValueError("busy_timeout_ms must be in [1, 60000]")
        self._layout = data_root if isinstance(data_root, DataRootLayout) else None
        if self._layout is not None:
            layout = self._layout.ensure_directories()
            candidates_dir = layout.assert_safe_path(layout.candidates)
            self._artifact_root = layout.assert_safe_path(layout.artifacts_sha256)
            self.data_root = layout.data_root
        else:
            root = Path(data_root).expanduser().resolve()
            if root.exists() and not root.is_dir():
                raise ValueError("data_root must be a directory")
            self.data_root = root
            candidates_dir = root / "growth" / "candidates"
            self._artifact_root = root / "growth" / "artifacts" / "sha256"
            candidates_dir.mkdir(parents=True, exist_ok=True)
            self._artifact_root.mkdir(parents=True, exist_ok=True)
        self.path = candidates_dir / "growth.sqlite3"
        self._busy_timeout_ms = busy_timeout_ms
        self._state_lock = threading.Lock()
        self._closed = False
        self._initialize()
        report = self.verify_integrity()
        if not report.ok:
            self._closed = True
            raise GrowthStoreIntegrityError("growth store integrity verification failed")

    def connection_settings(self) -> dict[str, int | str]:
        with self._read_connection() as db:
            return {
                "journal_mode": str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "foreign_keys": int(db.execute("PRAGMA foreign_keys").fetchone()[0]),
                "query_only": int(db.execute("PRAGMA query_only").fetchone()[0]),
                "schema_version": int(db.execute("PRAGMA user_version").fetchone()[0]),
            }

    def create_candidate(self, candidate: GrowthCandidateV1) -> CandidateSnapshot:
        if not isinstance(candidate, GrowthCandidateV1):
            raise TypeError("candidate must be GrowthCandidateV1")
        if candidate.version_number != 1:
            raise GrowthStoreConflictError("first candidate version must have version_number 1")
        if candidate.previous_version_id is not None or candidate.previous_content_hash is not None:
            raise GrowthStoreConflictError("first candidate version cannot have a predecessor")
        with self._write_transaction() as db:
            existing = db.execute(
                "SELECT payload_json FROM candidate_versions WHERE candidate_version_id = ?",
                (candidate.candidate_version_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != self._json(candidate):
                    raise GrowthStoreConflictError("candidate version ID is bound to different content")
                return self._snapshot_db(db, candidate.candidate_version_id)
            if db.execute(
                "SELECT 1 FROM candidate_heads WHERE candidate_id = ?", (candidate.candidate_id,)
            ).fetchone() is not None:
                raise GrowthStoreConflictError("candidate already has a first version")
            self._insert_candidate_db(db, candidate)
            db.execute(
                "INSERT INTO candidate_heads VALUES (?, ?, 1, 1)",
                (candidate.candidate_id, candidate.candidate_version_id),
            )
            return self._snapshot_db(db, candidate.candidate_version_id)

    def create_next_version(
        self,
        candidate: GrowthCandidateV1,
        *,
        expected_head_revision: int,
    ) -> CandidateSnapshot:
        if not isinstance(candidate, GrowthCandidateV1):
            raise TypeError("candidate must be GrowthCandidateV1")
        if type(expected_head_revision) is not int or expected_head_revision < 1:
            raise ValueError("expected_head_revision must be a positive integer")
        if candidate.version_number <= 1:
            raise GrowthStoreConflictError("next candidate version must be greater than 1")
        with self._write_transaction() as db:
            existing = db.execute(
                "SELECT payload_json FROM candidate_versions WHERE candidate_version_id = ?",
                (candidate.candidate_version_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != self._json(candidate):
                    raise GrowthStoreConflictError("candidate version ID is bound to different content")
                return self._snapshot_db(db, candidate.candidate_version_id)
            head = db.execute(
                "SELECT * FROM candidate_heads WHERE candidate_id = ?", (candidate.candidate_id,)
            ).fetchone()
            if head is None:
                raise GrowthStoreConflictError("candidate does not have a predecessor")
            if head["revision"] != expected_head_revision:
                raise GrowthStoreCASMismatchError("candidate head revision is stale")
            predecessor = self._candidate_db(db, head["candidate_version_id"])
            if (
                candidate.version_number != head["version_number"] + 1
                or candidate.previous_version_id != predecessor.candidate_version_id
                or candidate.previous_content_hash != predecessor.content_hash
            ):
                raise GrowthStoreConflictError("candidate predecessor must be the exact current head")
            if (
                candidate.identity_id != predecessor.identity_id
                or candidate.instance_id != predecessor.instance_id
                or candidate.owner_subject_id != predecessor.owner_subject_id
                or candidate.candidate_kind is not predecessor.candidate_kind
            ):
                raise GrowthStoreConflictError("candidate lineage identity is immutable")
            self._insert_candidate_db(db, candidate)
            updated = db.execute(
                "UPDATE candidate_heads SET candidate_version_id = ?, version_number = ?, revision = revision + 1 "
                "WHERE candidate_id = ? AND revision = ?",
                (
                    candidate.candidate_version_id,
                    candidate.version_number,
                    candidate.candidate_id,
                    expected_head_revision,
                ),
            )
            if updated.rowcount != 1:
                raise GrowthStoreCASMismatchError("candidate head revision is stale")
            return self._snapshot_db(db, candidate.candidate_version_id)

    def get_candidate(self, candidate_version_id: str) -> CandidateSnapshot | None:
        with self._read_connection() as db:
            row = db.execute(
                "SELECT 1 FROM candidate_versions WHERE candidate_version_id = ?",
                (candidate_version_id,),
            ).fetchone()
            return None if row is None else self._snapshot_db(db, candidate_version_id)

    get_candidate_version = get_candidate

    def get_head(self, candidate_id: str) -> CandidateHead | None:
        with self._read_connection() as db:
            row = db.execute(
                "SELECT * FROM candidate_heads WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                return None
            return CandidateHead(
                candidate_id=row["candidate_id"],
                candidate_version_id=row["candidate_version_id"],
                version_number=row["version_number"],
                revision=row["revision"],
            )

    def list_candidates(
        self,
        *,
        state: CandidateState | str | None = None,
        limit: int = 100,
    ) -> tuple[CandidateSnapshot, ...]:
        _require_limit(limit)
        state_value = None if state is None else CandidateState(state).value
        query = (
            "SELECT candidate_version_id FROM candidate_projection "
            + ("WHERE state = ? " if state_value is not None else "")
            + "ORDER BY updated_at_utc DESC, candidate_version_id LIMIT ?"
        )
        params: tuple[Any, ...] = (state_value, limit) if state_value is not None else (limit,)
        with self._read_connection() as db:
            rows = db.execute(query, params).fetchall()
            return tuple(self._snapshot_db(db, row[0]) for row in rows)

    def list_candidate_versions(
        self, candidate_id: str, *, limit: int = 100
    ) -> tuple[CandidateSnapshot, ...]:
        _require_limit(limit)
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT candidate_version_id FROM candidate_versions WHERE candidate_id = ? "
                "ORDER BY version_number DESC LIMIT ?",
                (candidate_id, limit),
            ).fetchall()
            return tuple(self._snapshot_db(db, row[0]) for row in rows)

    def store_artifact(
        self,
        artifact: SkillArtifactV1,
        payload: bytes,
        manifest: bytes,
    ) -> SkillArtifactV1:
        if not isinstance(artifact, SkillArtifactV1):
            raise TypeError("artifact must be SkillArtifactV1")
        if not isinstance(payload, bytes) or not isinstance(manifest, bytes):
            raise TypeError("artifact payload and manifest must be bytes")
        if hashlib.sha256(payload).hexdigest() != artifact.payload_sha256:
            raise GrowthStoreConflictError("artifact payload hash does not match contract")
        if hashlib.sha256(manifest).hexdigest() != artifact.manifest_sha256:
            raise GrowthStoreConflictError("artifact manifest hash does not match contract")
        if len(payload) != artifact.total_bytes:
            raise GrowthStoreConflictError("artifact byte count does not match contract")
        payload_json = self._json(artifact)
        with self._read_connection() as db:
            existing = db.execute(
                "SELECT payload_json FROM skill_artifacts WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload_json:
                    raise GrowthStoreConflictError("artifact ID is bound to different content")
                self._verify_artifact_files(artifact)
                return artifact
            projection = self._projection_for_write(db, artifact.candidate_version_id)
            if CandidateState(projection["state"]) not in {
                CandidateState.SANDBOXING,
                CandidateState.VALIDATION_FAILED,
            }:
                raise GrowthStoreConflictError("artifact can only be recorded from sandbox validation")
        payload_path, manifest_path = self._artifact_paths(artifact.payload_sha256, create=True)
        self._write_immutable(payload_path, payload)
        self._write_immutable(manifest_path, manifest)
        with self._write_transaction() as db:
            existing = db.execute(
                "SELECT payload_json FROM skill_artifacts WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload_json:
                    raise GrowthStoreConflictError("artifact ID is bound to different content")
                return artifact
            projection = self._projection_for_write(db, artifact.candidate_version_id)
            if CandidateState(projection["state"]) not in {
                CandidateState.SANDBOXING,
                CandidateState.VALIDATION_FAILED,
            }:
                raise GrowthStoreConflictError("artifact can only be recorded from sandbox validation")
            sequence, previous, chain_hash = self._append_chain_db(
                db,
                artifact.candidate_version_id,
                "artifact",
                artifact.artifact_id,
                artifact.content_hash,
            )
            db.execute(
                "INSERT INTO skill_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact.artifact_id,
                    artifact.candidate_version_id,
                    artifact.payload_sha256,
                    artifact.manifest_sha256,
                    payload_json,
                    artifact.content_hash,
                    sequence,
                    previous,
                    chain_hash,
                ),
            )
            self._advance_chain_projection_db(
                db, artifact.candidate_version_id, sequence, chain_hash, artifact.created_at_utc
            )
            return artifact

    record_artifact = store_artifact

    def read_artifact_payload(self, artifact_id: str) -> bytes:
        artifact = self.get_artifact(artifact_id)
        if artifact is None:
            raise GrowthStoreConflictError("artifact does not exist")
        payload_path, manifest_path = self._artifact_paths(artifact.payload_sha256, create=False)
        try:
            payload = payload_path.read_bytes()
            manifest = manifest_path.read_bytes()
        except OSError as exc:
            raise GrowthStoreIntegrityError("artifact content is unavailable") from exc
        if (
            hashlib.sha256(payload).hexdigest() != artifact.payload_sha256
            or hashlib.sha256(manifest).hexdigest() != artifact.manifest_sha256
            or len(payload) != artifact.total_bytes
        ):
            raise GrowthStoreIntegrityError("artifact content failed integrity verification")
        return payload

    def get_artifact(self, artifact_id: str) -> SkillArtifactV1 | None:
        with self._read_connection() as db:
            row = db.execute(
                "SELECT payload_json FROM skill_artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            return None if row is None else SkillArtifactV1.from_dict(json.loads(row[0]))

    def record_decision(
        self,
        decision: GrowthDecisionV1,
        *,
        principal: RuntimeAccessPrincipal,
    ) -> GrowthDecisionV1:
        if not isinstance(decision, GrowthDecisionV1):
            raise TypeError("decision must be GrowthDecisionV1")
        _require_local_user(principal, decision.actor_subject_id)
        with self._write_transaction() as db:
            payload_json = self._json(decision)
            existing = db.execute(
                "SELECT payload_json FROM growth_decisions WHERE decision_id = ?",
                (decision.decision_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload_json:
                    raise GrowthStoreConflictError("decision ID is bound to different content")
                return decision
            idem = db.execute(
                "SELECT payload_json FROM growth_decisions WHERE candidate_version_id = ? AND idempotency_key = ?",
                (decision.candidate_version_id, decision.idempotency_key),
            ).fetchone()
            if idem is not None:
                if idem["payload_json"] != payload_json:
                    raise GrowthStoreConflictError("decision idempotency key is bound to different content")
                return decision
            projection = self._projection_for_write(
                db, decision.candidate_version_id, decision.expected_revision
            )
            candidate = self._candidate_db(db, decision.candidate_version_id)
            if candidate.owner_subject_id != decision.actor_subject_id:
                raise GrowthStoreAuthorizationError("only the candidate owner may decide")
            if CandidateState(projection["state"]) is not _DECISION_STATES[decision.decision_kind]:
                raise GrowthStoreConflictError("decision is not valid for the current candidate state")
            if decision.candidate_content_hash != candidate.content_hash:
                raise GrowthStoreConflictError("decision is bound to a different candidate content hash")
            if decision.permission_delta_hash != _permission_delta_hash(candidate):
                raise GrowthStoreConflictError("decision permission delta hash does not match candidate")
            artifact = self._artifact_db(db, decision.artifact_id)
            if (
                artifact is None
                or artifact.candidate_version_id != decision.candidate_version_id
                or artifact.payload_sha256 != decision.artifact_payload_sha256
                or artifact.test_receipt_ids != decision.test_receipt_ids
            ):
                raise GrowthStoreConflictError(
                    "decision does not bind the exact artifact receipts"
                )
            sequence, previous, chain_hash = self._append_chain_db(
                db,
                decision.candidate_version_id,
                "decision",
                decision.decision_id,
                decision.content_hash,
            )
            db.execute(
                "INSERT INTO growth_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.candidate_version_id,
                    decision.idempotency_key,
                    decision.expected_revision,
                    decision.decision_kind.value,
                    payload_json,
                    decision.content_hash,
                    sequence,
                    previous,
                    chain_hash,
                ),
            )
            self._advance_chain_projection_db(
                db, decision.candidate_version_id, sequence, chain_hash, decision.created_at_utc
            )
            return decision

    append_decision = record_decision

    def append_transition(self, transition: CandidateTransitionV1) -> CandidateSnapshot:
        if not isinstance(transition, CandidateTransitionV1):
            raise TypeError("transition must be CandidateTransitionV1")
        if transition.actor_kind is GrowthActorKind.MODEL_PROPOSER:
            raise GrowthStoreAuthorizationError("model proposers may only create candidate proposals")
        with self._write_transaction() as db:
            existing = db.execute(
                "SELECT payload_json FROM candidate_transitions WHERE transition_id = ?",
                (transition.transition_id,),
            ).fetchone()
            payload_json = self._json(transition)
            if existing is not None:
                if existing["payload_json"] != payload_json:
                    raise GrowthStoreConflictError("transition ID is bound to different content")
                return self._snapshot_db(db, transition.candidate_version_id)
            idem = db.execute(
                "SELECT payload_json FROM candidate_transitions WHERE candidate_version_id = ? AND idempotency_key = ?",
                (transition.candidate_version_id, transition.idempotency_key),
            ).fetchone()
            if idem is not None:
                if idem["payload_json"] != payload_json:
                    raise GrowthStoreConflictError("transition idempotency key is bound to different content")
                return self._snapshot_db(db, transition.candidate_version_id)
            projection = self._projection_for_write(
                db, transition.candidate_version_id, transition.expected_revision
            )
            current_state = CandidateState(projection["state"])
            if current_state is not transition.from_state:
                raise GrowthStoreCASMismatchError("candidate state is stale")
            self._validate_transition_evidence_db(db, transition)
            sequence, previous, chain_hash = self._append_chain_db(
                db,
                transition.candidate_version_id,
                "transition",
                transition.transition_id,
                transition.content_hash,
            )
            db.execute(
                "INSERT INTO candidate_transitions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition.transition_id,
                    transition.candidate_version_id,
                    transition.idempotency_key,
                    transition.expected_revision,
                    transition.from_state.value,
                    transition.to_state.value,
                    payload_json,
                    transition.content_hash,
                    sequence,
                    previous,
                    chain_hash,
                ),
            )
            updated = db.execute(
                "UPDATE candidate_projection SET state = ?, revision = revision + 1, updated_at_utc = ?, "
                "last_sequence = ?, last_chain_hash = ? WHERE candidate_version_id = ? AND revision = ?",
                (
                    transition.to_state.value,
                    transition.created_at_utc,
                    sequence,
                    chain_hash,
                    transition.candidate_version_id,
                    transition.expected_revision,
                ),
            )
            if updated.rowcount != 1:
                raise GrowthStoreCASMismatchError("candidate revision is stale")
            return self._snapshot_db(db, transition.candidate_version_id)

    def record_deployment(self, deployment: SkillDeploymentV1) -> SkillDeploymentV1:
        if not isinstance(deployment, SkillDeploymentV1):
            raise TypeError("deployment must be SkillDeploymentV1")
        with self._write_transaction() as db:
            payload_json = self._json(deployment)
            existing = db.execute(
                "SELECT payload_json FROM skill_deployments WHERE deployment_id = ?",
                (deployment.deployment_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload_json:
                    raise GrowthStoreConflictError("deployment ID is bound to different content")
                return deployment
            projection = self._projection_for_write(
                db, deployment.candidate_version_id, deployment.revision
            )
            state = CandidateState(projection["state"])
            if state not in {CandidateState.DEPLOYING, CandidateState.ROLLING_BACK}:
                raise GrowthStoreConflictError("deployment is not valid for the current candidate state")
            artifact = self._artifact_db(db, deployment.artifact_id)
            approval = self._decision_db(db, deployment.approval_id)
            if (
                artifact is None
                or artifact.candidate_version_id != deployment.candidate_version_id
                or artifact.payload_sha256 != deployment.artifact_payload_sha256
                or artifact.skill_name != deployment.skill_name
                or artifact.required_capabilities != deployment.required_capabilities
            ):
                raise GrowthStoreConflictError("deployment does not bind the exact artifact")
            if (
                approval is None
                or approval.decision_kind is not GrowthDecisionKind.APPROVE
                or approval.candidate_version_id != deployment.candidate_version_id
                or approval.artifact_id != deployment.artifact_id
                or approval.artifact_payload_sha256 != deployment.artifact_payload_sha256
            ):
                raise GrowthStoreConflictError("deployment does not bind an exact approval")
            if state is CandidateState.ROLLING_BACK and (
                deployment.previous_deployment_id is None
                or deployment.rollback_receipt_id is None
                or self._deployment_db(db, deployment.previous_deployment_id) is None
            ):
                raise GrowthStoreConflictError("rollback deployment requires prior deployment and receipt")
            sequence, previous, chain_hash = self._append_chain_db(
                db,
                deployment.candidate_version_id,
                "deployment",
                deployment.deployment_id,
                deployment.content_hash,
            )
            db.execute(
                "INSERT INTO skill_deployments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    deployment.deployment_id,
                    deployment.candidate_version_id,
                    deployment.artifact_id,
                    payload_json,
                    deployment.content_hash,
                    sequence,
                    previous,
                    chain_hash,
                ),
            )
            self._advance_chain_projection_db(
                db, deployment.candidate_version_id, sequence, chain_hash, deployment.deployed_at_utc
            )
            return deployment

    append_deployment = record_deployment

    def deployment_gate(
        self,
        *,
        candidate_version_id: str,
        artifact_id: str,
        artifact_payload_sha256: str,
        catalog_record_id: str,
        approval_id: str,
        catalog_active: bool = True,
    ) -> DeploymentGate:
        _require_hash(artifact_payload_sha256, "artifact_payload_sha256")
        if not catalog_active:
            return DeploymentGate(False, "catalog_disabled")
        with self._read_connection() as db:
            try:
                snapshot = self._snapshot_db(db, candidate_version_id)
            except GrowthStoreConflictError:
                return DeploymentGate(False, "candidate_missing")
            if snapshot.state in {
                CandidateState.REJECTED,
                CandidateState.REVOKED,
                CandidateState.ROLLED_BACK,
                CandidateState.EXPIRED,
                CandidateState.VALIDATION_FAILED,
                CandidateState.DEPLOYMENT_FAILED,
            }:
                return DeploymentGate(False, f"candidate_{snapshot.state.value}")
            artifact = self._artifact_db(db, artifact_id)
            approval = self._decision_db(db, approval_id)
            if (
                artifact is None
                or artifact.candidate_version_id != candidate_version_id
                or artifact.payload_sha256 != artifact_payload_sha256
            ):
                return DeploymentGate(False, "artifact_mismatch")
            if (
                approval is None
                or approval.decision_kind is not GrowthDecisionKind.APPROVE
                or approval.candidate_version_id != candidate_version_id
                or approval.artifact_id != artifact_id
                or approval.artifact_payload_sha256 != artifact_payload_sha256
                or approval.candidate_content_hash != snapshot.candidate.content_hash
            ):
                return DeploymentGate(False, "approval_mismatch")
            row = db.execute(
                "SELECT payload_json FROM skill_deployments WHERE candidate_version_id = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (candidate_version_id,),
            ).fetchone()
            deployment = None if row is None else SkillDeploymentV1.from_dict(json.loads(row[0]))
            if deployment is not None and deployment.catalog_record_id != catalog_record_id:
                return DeploymentGate(False, "catalog_record_mismatch")
            if snapshot.state not in {
                CandidateState.APPROVED,
                CandidateState.DEPLOYING,
                CandidateState.ACTIVE,
                CandidateState.ROLLING_BACK,
            }:
                return DeploymentGate(False, "candidate_not_approved")
            return DeploymentGate(True, "authorized", deployment)

    def require_deployment(self, **values: Any) -> SkillDeploymentV1 | None:
        result = self.deployment_gate(**values)
        if not result.allowed:
            raise GrowthStoreAuthorizationError(result.reason_code)
        return result.deployment

    def get_decision(self, decision_id: str) -> GrowthDecisionV1 | None:
        with self._read_connection() as db:
            return self._decision_db(db, decision_id)

    def get_deployment(self, deployment_id: str) -> SkillDeploymentV1 | None:
        with self._read_connection() as db:
            return self._deployment_db(db, deployment_id)

    def list_transitions(
        self, candidate_version_id: str, *, limit: int = 100
    ) -> tuple[CandidateTransitionV1, ...]:
        return self._list_contracts(
            "candidate_transitions", CandidateTransitionV1, candidate_version_id, limit
        )

    def list_decisions(
        self, candidate_version_id: str, *, limit: int = 100
    ) -> tuple[GrowthDecisionV1, ...]:
        return self._list_contracts(
            "growth_decisions", GrowthDecisionV1, candidate_version_id, limit
        )

    def list_artifacts(
        self, candidate_version_id: str, *, limit: int = 100
    ) -> tuple[SkillArtifactV1, ...]:
        return self._list_contracts(
            "skill_artifacts", SkillArtifactV1, candidate_version_id, limit
        )

    def list_deployments(
        self, candidate_version_id: str, *, limit: int = 100
    ) -> tuple[SkillDeploymentV1, ...]:
        return self._list_contracts(
            "skill_deployments", SkillDeploymentV1, candidate_version_id, limit
        )

    def verify_integrity(self) -> GrowthIntegrityReport:
        errors: list[str] = []
        candidates_scanned = 0
        events_scanned = 0
        artifacts_scanned = 0
        schema_version = 0
        try:
            with self._read_connection() as db:
                schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
                if schema_version != STORE_SCHEMA_VERSION:
                    errors.append("schema_version_mismatch")
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    errors.append("sqlite_integrity_failed")
                trigger_names = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                }
                for table in _APPEND_ONLY_TABLES:
                    for action in ("update", "delete"):
                        if f"prevent_{table}_{action}" not in trigger_names:
                            errors.append(f"trigger_missing:{table}:{action}")
                version_rows = db.execute(
                    "SELECT * FROM candidate_versions ORDER BY candidate_id, version_number"
                ).fetchall()
                by_candidate: dict[str, list[GrowthCandidateV1]] = {}
                for row in version_rows:
                    candidates_scanned += 1
                    try:
                        candidate = GrowthCandidateV1.from_dict(json.loads(row["payload_json"]))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        errors.append(f"candidate:{row['candidate_version_id']}:invalid_contract")
                        continue
                    if (
                        candidate.candidate_version_id != row["candidate_version_id"]
                        or candidate.candidate_id != row["candidate_id"]
                        or candidate.version_number != row["version_number"]
                        or candidate.previous_version_id != row["previous_version_id"]
                        or candidate.previous_content_hash != row["previous_content_hash"]
                        or candidate.owner_subject_id != row["owner_subject_id"]
                        or candidate.content_hash != row["content_hash"]
                    ):
                        errors.append(f"candidate:{candidate.candidate_version_id}:index_mismatch")
                        continue
                    by_candidate.setdefault(candidate.candidate_id, []).append(candidate)
                    event_result = self._verify_candidate_chain_db(db, candidate)
                    events_scanned += event_result[0]
                    errors.extend(event_result[1])
                for candidate_id, versions in by_candidate.items():
                    for index, candidate in enumerate(versions, start=1):
                        if candidate.version_number != index:
                            errors.append(f"candidate:{candidate_id}:noncontiguous_versions")
                            break
                        if index == 1:
                            valid_previous = (
                                candidate.previous_version_id is None
                                and candidate.previous_content_hash is None
                            )
                        else:
                            previous = versions[index - 2]
                            valid_previous = (
                                candidate.previous_version_id == previous.candidate_version_id
                                and candidate.previous_content_hash == previous.content_hash
                            )
                        if not valid_previous:
                            errors.append(f"candidate:{candidate_id}:invalid_lineage")
                            break
                    head = db.execute(
                        "SELECT * FROM candidate_heads WHERE candidate_id = ?", (candidate_id,)
                    ).fetchone()
                    latest = versions[-1]
                    if (
                        head is None
                        or head["candidate_version_id"] != latest.candidate_version_id
                        or head["version_number"] != latest.version_number
                        or head["revision"] != latest.version_number
                    ):
                        errors.append(f"candidate:{candidate_id}:head_mismatch")
                artifact_rows = db.execute("SELECT payload_json FROM skill_artifacts").fetchall()
                for row in artifact_rows:
                    artifacts_scanned += 1
                    try:
                        artifact = SkillArtifactV1.from_dict(json.loads(row[0]))
                        self._verify_artifact_files(artifact)
                    except (GrowthStoreError, OSError, TypeError, ValueError, json.JSONDecodeError):
                        errors.append("artifact:integrity_failed")
        except (sqlite3.Error, OSError, ValueError):
            errors.append("database_unreadable")
        return GrowthIntegrityReport(
            ok=not errors,
            schema_version=schema_version,
            candidates_scanned=candidates_scanned,
            events_scanned=events_scanned,
            artifacts_scanned=artifacts_scanned,
            errors=tuple(errors),
        )

    def close(self) -> None:
        with self._state_lock:
            self._closed = True

    shutdown = close

    def __enter__(self) -> "GrowthStore":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _initialize(self) -> None:
        try:
            with closing(
                sqlite3.connect(
                    self.path,
                    timeout=self._busy_timeout_ms / 1_000,
                    isolation_level=None,
                )
            ) as db:
                db.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
                db.execute("PRAGMA foreign_keys = ON")
                if str(db.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() != "wal":
                    raise GrowthStoreIntegrityError("WAL mode is unavailable")
                db.execute("PRAGMA synchronous = FULL")
                version = int(db.execute("PRAGMA user_version").fetchone()[0])
                if version not in (0, STORE_SCHEMA_VERSION):
                    raise GrowthStoreIntegrityError("growth store schema is newer than this runtime")
                if version == 0:
                    db.execute("BEGIN IMMEDIATE")
                    try:
                        for statement in _SCHEMA:
                            db.execute(statement)
                        db.execute("INSERT INTO store_meta VALUES (1, ?)", (STORE_SCHEMA_VERSION,))
                        for table in _APPEND_ONLY_TABLES:
                            db.execute(
                                f"CREATE TRIGGER prevent_{table}_update BEFORE UPDATE ON {table} "
                                "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
                            )
                            db.execute(
                                f"CREATE TRIGGER prevent_{table}_delete BEFORE DELETE ON {table} "
                                "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
                            )
                        db.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")
                        db.execute("COMMIT")
                    except BaseException:
                        if db.in_transaction:
                            db.execute("ROLLBACK")
                        raise
        except GrowthStoreError:
            raise
        except sqlite3.Error as exc:
            raise GrowthStoreIntegrityError("growth store initialization failed") from exc

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        uri = self.path.resolve().as_uri() + "?mode=ro"
        try:
            db = sqlite3.connect(uri, uri=True, timeout=self._busy_timeout_ms / 1_000)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            db.execute("PRAGMA query_only = ON")
        except sqlite3.Error as exc:
            raise GrowthStoreIntegrityError("growth store is unavailable") from exc
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        try:
            db = sqlite3.connect(
                self.path, timeout=self._busy_timeout_ms / 1_000, isolation_level=None
            )
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            db.execute("PRAGMA synchronous = FULL")
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise GrowthStoreError("growth store is busy or unavailable") from exc
        try:
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _insert_candidate_db(self, db: sqlite3.Connection, candidate: GrowthCandidateV1) -> None:
        db.execute(
            "INSERT INTO candidate_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate.candidate_version_id,
                candidate.candidate_id,
                candidate.version_number,
                candidate.previous_version_id,
                candidate.previous_content_hash,
                candidate.owner_subject_id,
                self._json(candidate),
                candidate.content_hash,
                candidate.created_at_utc,
            ),
        )
        chain_hash = _chain_hash(
            GENESIS_HASH,
            "candidate",
            candidate.candidate_version_id,
            candidate.content_hash,
        )
        db.execute(
            "INSERT INTO candidate_chain VALUES (?, 1, 'candidate', ?, ?, ?, ?)",
            (
                candidate.candidate_version_id,
                candidate.candidate_version_id,
                candidate.content_hash,
                GENESIS_HASH,
                chain_hash,
            ),
        )
        db.execute(
            "INSERT INTO candidate_projection VALUES (?, ?, 1, ?, 1, ?)",
            (
                candidate.candidate_version_id,
                CandidateState.PROPOSED.value,
                candidate.created_at_utc,
                chain_hash,
            ),
        )

    def _append_chain_db(
        self,
        db: sqlite3.Connection,
        candidate_version_id: str,
        event_kind: str,
        event_id: str,
        payload_hash: str,
    ) -> tuple[int, str, str]:
        projection = db.execute(
            "SELECT last_sequence, last_chain_hash FROM candidate_projection WHERE candidate_version_id = ?",
            (candidate_version_id,),
        ).fetchone()
        if projection is None:
            raise GrowthStoreConflictError("candidate version does not exist")
        sequence = projection["last_sequence"] + 1
        previous = projection["last_chain_hash"]
        chain_hash = _chain_hash(previous, event_kind, event_id, payload_hash)
        db.execute(
            "INSERT INTO candidate_chain VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                candidate_version_id,
                sequence,
                event_kind,
                event_id,
                payload_hash,
                previous,
                chain_hash,
            ),
        )
        return sequence, previous, chain_hash

    @staticmethod
    def _advance_chain_projection_db(
        db: sqlite3.Connection,
        candidate_version_id: str,
        sequence: int,
        chain_hash: str,
        updated_at_utc: str,
    ) -> None:
        updated = db.execute(
            "UPDATE candidate_projection SET updated_at_utc = ?, last_sequence = ?, last_chain_hash = ? "
            "WHERE candidate_version_id = ? AND last_sequence = ?",
            (updated_at_utc, sequence, chain_hash, candidate_version_id, sequence - 1),
        )
        if updated.rowcount != 1:
            raise GrowthStoreCASMismatchError("candidate event head is stale")

    @staticmethod
    def _projection_for_write(
        db: sqlite3.Connection,
        candidate_version_id: str,
        expected_revision: int | None = None,
    ) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM candidate_projection WHERE candidate_version_id = ?",
            (candidate_version_id,),
        ).fetchone()
        if row is None:
            raise GrowthStoreConflictError("candidate version does not exist")
        if expected_revision is not None and row["revision"] != expected_revision:
            raise GrowthStoreCASMismatchError("candidate revision is stale")
        return row

    def _validate_transition_evidence_db(
        self, db: sqlite3.Connection, transition: CandidateTransitionV1
    ) -> None:
        expected_decision_kind = _DECISION_TARGETS.get(transition.to_state)
        if expected_decision_kind is not None:
            decision = self._decision_db(db, transition.decision_id)
            if (
                decision is None
                or decision.decision_kind is not expected_decision_kind
                or decision.candidate_version_id != transition.candidate_version_id
                or decision.expected_revision != transition.expected_revision
                or transition.actor_kind is not GrowthActorKind.USER
                or transition.actor_id != decision.actor_subject_id
            ):
                raise GrowthStoreConflictError("transition does not bind the exact user decision")
        if transition.to_state in {
            CandidateState.VALIDATED,
            CandidateState.AWAITING_APPROVAL,
        }:
            artifact = self._artifact_db(db, transition.artifact_id)
            if (
                artifact is None
                or artifact.candidate_version_id != transition.candidate_version_id
                or not transition.receipt_ids
                or transition.receipt_ids != artifact.test_receipt_ids
            ):
                raise GrowthStoreConflictError("validation transition requires exact artifact receipts")
        if transition.to_state in {CandidateState.DEPLOYING, CandidateState.ACTIVE}:
            artifact = self._artifact_db(db, transition.artifact_id)
            approval = db.execute(
                "SELECT 1 FROM growth_decisions WHERE candidate_version_id = ? AND decision_kind = 'approve' "
                "AND json_extract(payload_json, '$.artifact_id') = ? LIMIT 1",
                (transition.candidate_version_id, transition.artifact_id),
            ).fetchone()
            if artifact is None or approval is None:
                raise GrowthStoreConflictError("deployment transition requires approved exact artifact")
        if transition.to_state is CandidateState.ACTIVE:
            deployment = db.execute(
                "SELECT 1 FROM skill_deployments WHERE candidate_version_id = ? AND artifact_id = ? LIMIT 1",
                (transition.candidate_version_id, transition.artifact_id),
            ).fetchone()
            if deployment is None:
                raise GrowthStoreConflictError("activation requires an immutable deployment receipt")
        if transition.to_state in {
            CandidateState.VALIDATION_FAILED,
            CandidateState.DEPLOYMENT_FAILED,
            CandidateState.ROLLING_BACK,
            CandidateState.ROLLED_BACK,
        } and not transition.receipt_ids:
            raise GrowthStoreConflictError("transition requires validation or rollback receipts")

    @staticmethod
    def _json(contract: Any) -> str:
        return contract.canonical_json_bytes().decode("utf-8")

    @staticmethod
    def _candidate_db(db: sqlite3.Connection, candidate_version_id: str) -> GrowthCandidateV1:
        row = db.execute(
            "SELECT payload_json FROM candidate_versions WHERE candidate_version_id = ?",
            (candidate_version_id,),
        ).fetchone()
        if row is None:
            raise GrowthStoreConflictError("candidate version does not exist")
        try:
            return GrowthCandidateV1.from_dict(json.loads(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GrowthStoreIntegrityError("candidate contract is invalid") from exc

    @staticmethod
    def _artifact_db(
        db: sqlite3.Connection, artifact_id: str | None
    ) -> SkillArtifactV1 | None:
        if artifact_id is None:
            return None
        row = db.execute(
            "SELECT payload_json FROM skill_artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return None if row is None else SkillArtifactV1.from_dict(json.loads(row[0]))

    @staticmethod
    def _decision_db(
        db: sqlite3.Connection, decision_id: str | None
    ) -> GrowthDecisionV1 | None:
        if decision_id is None:
            return None
        row = db.execute(
            "SELECT payload_json FROM growth_decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        return None if row is None else GrowthDecisionV1.from_dict(json.loads(row[0]))

    @staticmethod
    def _deployment_db(
        db: sqlite3.Connection, deployment_id: str | None
    ) -> SkillDeploymentV1 | None:
        if deployment_id is None:
            return None
        row = db.execute(
            "SELECT payload_json FROM skill_deployments WHERE deployment_id = ?",
            (deployment_id,),
        ).fetchone()
        return None if row is None else SkillDeploymentV1.from_dict(json.loads(row[0]))

    @staticmethod
    def _snapshot_db(db: sqlite3.Connection, candidate_version_id: str) -> CandidateSnapshot:
        row = db.execute(
            "SELECT v.payload_json, p.* FROM candidate_versions AS v "
            "JOIN candidate_projection AS p USING(candidate_version_id) "
            "WHERE v.candidate_version_id = ?",
            (candidate_version_id,),
        ).fetchone()
        if row is None:
            raise GrowthStoreConflictError("candidate version does not exist")
        try:
            candidate = GrowthCandidateV1.from_dict(json.loads(row["payload_json"]))
            state = CandidateState(row["state"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GrowthStoreIntegrityError("candidate projection is invalid") from exc
        return CandidateSnapshot(
            candidate=candidate,
            state=state,
            revision=row["revision"],
            updated_at_utc=row["updated_at_utc"],
            last_sequence=row["last_sequence"],
            last_chain_hash=row["last_chain_hash"],
        )

    def _list_contracts(
        self,
        table: str,
        contract_type: Any,
        candidate_version_id: str,
        limit: int,
    ) -> tuple[Any, ...]:
        _require_limit(limit)
        with self._read_connection() as db:
            rows = db.execute(
                f"SELECT payload_json FROM {table} WHERE candidate_version_id = ? "
                "ORDER BY sequence LIMIT ?",
                (candidate_version_id, limit),
            ).fetchall()
            return tuple(contract_type.from_dict(json.loads(row[0])) for row in rows)

    def _artifact_paths(self, payload_hash: str, *, create: bool) -> tuple[Path, Path]:
        _require_hash(payload_hash, "payload_sha256")
        directory = self._artifact_root / payload_hash[:2] / payload_hash
        if self._layout is not None:
            directory = self._layout.assert_safe_path(directory)
        else:
            root = self._artifact_root.resolve()
            resolved = directory.resolve(strict=False)
            if root != resolved and root not in resolved.parents:
                raise GrowthStoreIntegrityError("artifact path escapes the fixed data root")
            for parent in (self._artifact_root, self._artifact_root / payload_hash[:2], directory):
                if parent.exists() and parent.is_symlink():
                    raise GrowthStoreIntegrityError("artifact path traverses a symlink")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        if self._layout is not None:
            self._layout.assert_safe_path(directory)
        return directory / "payload.bin", directory / "manifest.bin"

    @staticmethod
    def _write_immutable(path: Path, content: bytes) -> None:
        if path.exists():
            try:
                existing = path.read_bytes()
            except OSError as exc:
                raise GrowthStoreIntegrityError("artifact content cannot be inspected") from exc
            if existing != content:
                raise GrowthStoreIntegrityError("content-addressed artifact collision detected")
            return
        try:
            with path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if path.read_bytes() != content:
                raise GrowthStoreIntegrityError("content-addressed artifact collision detected")
        except OSError as exc:
            raise GrowthStoreIntegrityError("artifact content could not be committed") from exc

    def _verify_artifact_files(self, artifact: SkillArtifactV1) -> None:
        payload_path, manifest_path = self._artifact_paths(
            artifact.payload_sha256, create=False
        )
        payload = payload_path.read_bytes()
        manifest = manifest_path.read_bytes()
        if (
            hashlib.sha256(payload).hexdigest() != artifact.payload_sha256
            or hashlib.sha256(manifest).hexdigest() != artifact.manifest_sha256
            or len(payload) != artifact.total_bytes
        ):
            raise GrowthStoreIntegrityError("artifact content failed integrity verification")

    def _verify_candidate_chain_db(
        self, db: sqlite3.Connection, candidate: GrowthCandidateV1
    ) -> tuple[int, tuple[str, ...]]:
        errors: list[str] = []
        version_id = candidate.candidate_version_id
        rows = db.execute(
            "SELECT * FROM candidate_chain WHERE candidate_version_id = ? ORDER BY sequence",
            (version_id,),
        ).fetchall()
        previous = GENESIS_HASH
        state = CandidateState.PROPOSED
        revision = 1
        updated_at = candidate.created_at_utc
        for expected_sequence, row in enumerate(rows, start=1):
            if row["sequence"] != expected_sequence:
                errors.append(f"candidate:{version_id}:noncontiguous_chain")
                break
            expected_hash = _chain_hash(
                previous, row["event_kind"], row["event_id"], row["payload_hash"]
            )
            if row["previous_hash"] != previous or row["chain_hash"] != expected_hash:
                errors.append(f"candidate:{version_id}:invalid_chain")
                break
            if expected_sequence == 1:
                if (
                    row["event_kind"] != "candidate"
                    or row["event_id"] != version_id
                    or row["payload_hash"] != candidate.content_hash
                ):
                    errors.append(f"candidate:{version_id}:invalid_genesis")
            else:
                event = self._event_contract_db(db, row)
                if event is None or event.content_hash != row["payload_hash"]:
                    errors.append(f"candidate:{version_id}:invalid_event:{expected_sequence}")
                elif isinstance(event, CandidateTransitionV1):
                    if (
                        event.candidate_version_id != version_id
                        or event.expected_revision != revision
                        or event.from_state is not state
                    ):
                        errors.append(f"candidate:{version_id}:invalid_transition:{expected_sequence}")
                    else:
                        state = event.to_state
                        revision += 1
                        updated_at = event.created_at_utc
                else:
                    if event.candidate_version_id != version_id:
                        errors.append(f"candidate:{version_id}:foreign_event:{expected_sequence}")
                    updated_at = getattr(event, "created_at_utc", None) or getattr(
                        event, "deployed_at_utc", updated_at
                    )
            previous = row["chain_hash"]
        event_count = sum(
            db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE candidate_version_id = ?", (version_id,)
            ).fetchone()[0]
            for table in (
                "candidate_transitions",
                "growth_decisions",
                "skill_artifacts",
                "skill_deployments",
            )
        )
        if len(rows) != event_count + 1:
            errors.append(f"candidate:{version_id}:orphan_event")
        projection = db.execute(
            "SELECT * FROM candidate_projection WHERE candidate_version_id = ?", (version_id,)
        ).fetchone()
        if (
            projection is None
            or not rows
            or projection["state"] != state.value
            or projection["revision"] != revision
            or projection["updated_at_utc"] != updated_at
            or projection["last_sequence"] != len(rows)
            or projection["last_chain_hash"] != rows[-1]["chain_hash"]
        ):
            errors.append(f"candidate:{version_id}:projection_mismatch")
        return len(rows), tuple(errors)

    @staticmethod
    def _event_contract_db(db: sqlite3.Connection, chain: sqlite3.Row) -> Any | None:
        table_and_type = {
            "transition": ("candidate_transitions", "transition_id", CandidateTransitionV1),
            "decision": ("growth_decisions", "decision_id", GrowthDecisionV1),
            "artifact": ("skill_artifacts", "artifact_id", SkillArtifactV1),
            "deployment": ("skill_deployments", "deployment_id", SkillDeploymentV1),
        }.get(chain["event_kind"])
        if table_and_type is None:
            return None
        table, id_column, contract_type = table_and_type
        row = db.execute(
            f"SELECT * FROM {table} WHERE {id_column} = ?", (chain["event_id"],)
        ).fetchone()
        if row is None or any(
            (
                row["candidate_version_id"] != chain["candidate_version_id"],
                row["content_hash"] != chain["payload_hash"],
                row["sequence"] != chain["sequence"],
                row["previous_chain_hash"] != chain["previous_hash"],
                row["chain_hash"] != chain["chain_hash"],
            )
        ):
            return None
        try:
            return contract_type.from_dict(json.loads(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _ensure_open(self) -> None:
        with self._state_lock:
            if self._closed:
                raise GrowthStoreClosedError("growth store is closed")


__all__ = [
    "DATABASE_RELATIVE_PATH",
    "GENESIS_HASH",
    "MAX_QUERY_LIMIT",
    "STORE_SCHEMA_VERSION",
    "CandidateHead",
    "CandidateSnapshot",
    "DeploymentGate",
    "GrowthIntegrityReport",
    "GrowthStore",
    "GrowthStoreAuthorizationError",
    "GrowthStoreCASMismatchError",
    "GrowthStoreClosedError",
    "GrowthStoreConflictError",
    "GrowthStoreError",
    "GrowthStoreIntegrityError",
]
