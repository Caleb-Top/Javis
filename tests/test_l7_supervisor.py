from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.events import EventBus
from core.life.l7.supervisor import (
    AuthoritySpec,
    L7Supervisor,
    L7SupervisorConflictError,
    RuntimeAuthorityAdapter,
)


@dataclass(frozen=True)
class _Receipt:
    schema_version: int
    receipt_id: str
    content_hash: str

    @classmethod
    def from_dict(cls, value):
        if set(value) != {"schema_version", "receipt_id", "content_hash"}:
            raise ValueError("receipt schema mismatch")
        receipt = cls(**value)
        if receipt.schema_version != 1:
            raise ValueError("receipt schema mismatch")
        if len(receipt.content_hash) != 64:
            raise ValueError("receipt hash mismatch")
        return receipt

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "content_hash": self.content_hash,
        }


_SPEC = AuthoritySpec(
    event_type="memory.receipt.committed",
    layer="l2",
    record_kind="receipt",
    id_field="receipt_id",
    record_id_field="receipt_id",
    contract_type=_Receipt,
)


class _Store:
    def __init__(self, name: str, log: list[str]):
        self.name = name
        self.log = log

    def close(self):
        self.log.append(f"close:{self.name}")


class _Coordinator:
    accepting_runs = True
    active_run_id = None

    def __init__(self, log: list[str]):
        self.log = log

    def begin_shutdown(self):
        self.accepting_runs = False
        self.log.append("coordinator:reject")
        return False

    async def shutdown(self):
        self.log.append("coordinator:checkpoint")


def _runtime(tmp_path: Path):
    root = tmp_path / "source"
    data_root = tmp_path / "data"
    root.mkdir()
    return SimpleNamespace(
        root=root.resolve(),
        data_root=data_root.resolve(),
        event_bus=EventBus(),
        subsystems={},
        life=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                identity=SimpleNamespace(identity_id="identity-1"),
                instance=SimpleNamespace(instance_id="instance-1"),
            )
        ),
    )


def _supervisor(runtime, resolver, *, queue_capacity=8, log=None):
    log = log if log is not None else []
    authority = RuntimeAuthorityAdapter(runtime, specs=(_SPEC,), resolver=resolver)
    return L7Supervisor(
        queue_capacity=queue_capacity,
        authority_adapter=authority,
        run_store_factory=lambda _layout: _Store("runs", log),
        growth_store_factory=lambda _layout: _Store("growth", log),
        coordinator_factory=lambda **_kwargs: _Coordinator(log),
    )


def _publish(runtime, receipt_id="receipt-1"):
    runtime.event_bus.publish(
        _SPEC.event_type,
        {"receipt_id": receipt_id},
        source="memory",
        schema_version=1,
    )


def test_supervisor_uses_the_runtime_data_root_and_rejects_a_second_owner(tmp_path: Path):
    runtime = _runtime(tmp_path)
    receipt = _Receipt(1, "receipt-1", hashlib.sha256(b"receipt-1").hexdigest())
    first = _supervisor(runtime, lambda *_args: receipt)
    second = _supervisor(runtime, lambda *_args: receipt)

    runtime.subsystems[first.name] = first
    assert first.start(runtime) is True
    try:
        assert first.layout.data_root == runtime.data_root
        assert first.status().state == "running"
        with pytest.raises(L7SupervisorConflictError):
            second.start(runtime)
    finally:
        assert first.stop(timeout=2.0) is True


def test_event_handler_is_nonblocking_and_authority_reads_happen_on_worker(tmp_path: Path):
    runtime = _runtime(tmp_path)
    receipt = _Receipt(1, "receipt-1", hashlib.sha256(b"receipt-1").hexdigest())

    def slow_resolver(*_args):
        time.sleep(0.15)
        return receipt

    supervisor = _supervisor(runtime, slow_resolver)
    runtime.subsystems[supervisor.name] = supervisor
    supervisor.start(runtime)
    try:
        started = time.perf_counter()
        _publish(runtime)
        assert time.perf_counter() - started < 0.05
        assert supervisor.wait_for_idle(timeout=2.0)
        assert supervisor.validated_checkpoint_ids() == ("receipt-1",)
    finally:
        supervisor.stop(timeout=2.0)


def test_queue_overload_is_bounded_and_one_bad_receipt_does_not_kill_worker(tmp_path: Path):
    runtime = _runtime(tmp_path)
    calls = 0

    def resolver(_layer, _kind, receipt_id):
        nonlocal calls
        calls += 1
        if receipt_id == "bad-receipt":
            return {
                "schema_version": 1,
                "receipt_id": receipt_id,
                "content_hash": "a" * 64,
                "raw_payload": "must be rejected",
            }
        time.sleep(0.1)
        return _Receipt(1, receipt_id, hashlib.sha256(receipt_id.encode()).hexdigest())

    supervisor = _supervisor(runtime, resolver, queue_capacity=1)
    runtime.subsystems[supervisor.name] = supervisor
    supervisor.start(runtime)
    try:
        _publish(runtime, "bad-receipt")
        _publish(runtime, "receipt-2")
        _publish(runtime, "receipt-3")
        assert supervisor.wait_for_idle(timeout=3.0)
        status = supervisor.status()
        assert status.metrics["rejected"] >= 1
        assert status.metrics["overloaded"] >= 1
        assert status.state == "running"

        _publish(runtime, "receipt-final")
        assert supervisor.wait_for_idle(timeout=2.0)
        assert "receipt-final" in supervisor.validated_checkpoint_ids()
        assert calls >= 2
    finally:
        supervisor.stop(timeout=2.0)


def test_shutdown_rejects_runs_then_checkpoints_before_closing_stores(tmp_path: Path):
    runtime = _runtime(tmp_path)
    log: list[str] = []
    receipt = _Receipt(1, "receipt-1", "a" * 64)
    supervisor = _supervisor(runtime, lambda *_args: receipt, log=log)
    runtime.subsystems[supervisor.name] = supervisor
    supervisor.start(runtime)

    assert supervisor.stop(timeout=2.0) is True
    assert log == [
        "coordinator:reject",
        "coordinator:checkpoint",
        "close:growth",
        "close:runs",
    ]
    assert supervisor.enqueue_event(
        runtime.event_bus.publish(
            _SPEC.event_type,
            {"receipt_id": "late-receipt"},
            source="memory",
        )
    ) is False


def test_untrusted_event_payload_is_rejected_before_authority_access(tmp_path: Path):
    runtime = _runtime(tmp_path)
    calls = []
    supervisor = _supervisor(runtime, lambda *args: calls.append(args))
    runtime.subsystems[supervisor.name] = supervisor
    supervisor.start(runtime)
    try:
        runtime.event_bus.publish(
            _SPEC.event_type,
            {"receipt_id": "receipt-1", "raw_screen": "secret pixels"},
            source="memory",
        )
        assert supervisor.wait_for_idle(timeout=2.0)
        assert calls == []
        assert supervisor.status().metrics["rejected"] == 1
    finally:
        supervisor.stop(timeout=2.0)


def test_even_benign_extra_event_fields_are_rejected_as_non_thin(tmp_path: Path):
    runtime = _runtime(tmp_path)
    calls = []
    supervisor = _supervisor(runtime, lambda *args: calls.append(args))
    runtime.subsystems[supervisor.name] = supervisor
    supervisor.start(runtime)
    try:
        runtime.event_bus.publish(
            _SPEC.event_type,
            {"receipt_id": "receipt-1", "summary": "must be re-read from authority"},
            source="memory",
        )
        assert supervisor.wait_for_idle(timeout=2.0)
        assert calls == []
        assert supervisor.status().metrics["rejected"] == 1
    finally:
        supervisor.stop(timeout=2.0)
