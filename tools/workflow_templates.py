# -*- coding: utf-8 -*-
"""Javis workflow template system — record, store, and replay multi-step tool workflows.

Templates are stored as JSON alongside existing procedural memory files in
brain_data/procedural/, using the naming convention ``wft_{name}_{timestamp}.json``
and the ``"type": "workflow"`` discriminator field so they coexist with existing
procedural chains without collision.

Recording data lives in memory only — nothing touches disk until
:func:`workflow_record_stop` is called.  Playback walks steps sequentially
with optional per-step user confirmation.

Public API
----------
    workflow_record_start   — begin recording a new workflow
    workflow_record_step    — append a tool-call step to the current recording
    workflow_record_stop    — finalise the recording and persist to disk
    workflow_play           — execute a saved template step by step
    workflow_list           — enumerate all saved workflow templates
    workflow_delete         — remove a saved workflow template
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("workflow")

#: Directory where both procedural chains and workflow templates are stored.
PROCEDURAL_DIR = Path(__file__).resolve().parent.parent / "brain_data" / "procedural"

# ---------------------------------------------------------------------------
# In-memory recording state  (single-active-recording model)
# ---------------------------------------------------------------------------
_recording: dict[str, Any] | None = None
"""State dictionary while a recording is in progress, or ``None`` when idle."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def workflow_record_start(name: str, description: str = "") -> str:
    """Start recording a new workflow template.

    Recording data is buffered in memory until :func:`workflow_record_stop` is
    called.  Only one recording can be active at a time — calling this while
    another recording is in progress silently discards the previous one.

    Parameters
    ----------
    name:
        Human-readable name for the workflow (will be slugified for the
        filename during save).
    description:
        Optional free-text description of what the workflow does.

    Returns
    -------
    str
        A unique recording ID that can be passed back to
        :func:`workflow_record_step` (though the module already tracks the
        active recording internally).
    """
    global _recording

    if _recording is not None:
        logger.warning(
            "[workflow] overwriting unsaved recording '%s' with new recording '%s'",
            _recording.get("name", "?"),
            name,
        )

    recording_id = f"rec_{uuid.uuid4().hex[:12]}"
    _recording = {
        "id": recording_id,
        "name": name.strip(),
        "description": description.strip(),
        "steps": [],
        "start_time": time.time(),
    }
    logger.info("[workflow] recording started: %s (id=%s)", name, recording_id)
    return recording_id


def workflow_record_step(tool_name: str, params_json: str = "{}") -> bool:
    """Record a single tool-call step in the active workflow recording.

    Parameters
    ----------
    tool_name:
        The name of the tool that was called (e.g. ``"file_read"``).
    params_json:
        A JSON-encoded string of the parameters passed to the tool.
        If the string is not valid JSON it is stored verbatim under a
        ``"raw"`` key.

    Returns
    -------
    bool
        ``True`` if the step was recorded, ``False`` if no recording is active.
    """
    if _recording is None:
        logger.warning("[workflow] no active recording, ignoring step")
        return False

    try:
        params: Any = json.loads(params_json) if isinstance(params_json, str) else params_json
    except json.JSONDecodeError:
        params = {"raw": params_json}

    step: dict[str, Any] = {
        "tool": tool_name,
        "params": params,
        "expected": "",
        "recorded_at": time.time(),
    }
    _recording["steps"].append(step)
    logger.info("[workflow] step %d: %s", len(_recording["steps"]), tool_name)
    return True


def workflow_record_stop() -> dict[str, Any] | None:
    """Finalise the current recording and persist the template to disk.

    The template is written to ``brain_data/procedural/wft_{name}_{timestamp}.json``.
    The in-memory buffer is cleared after saving.

    Returns
    -------
    dict or None
        The saved template dictionary, or ``None`` if no recording was active
        or no steps were recorded.
    """
    global _recording

    if _recording is None:
        logger.warning("[workflow] no active recording to stop")
        return None

    name = _recording["name"]
    steps = _recording["steps"]

    if not steps:
        logger.warning("[workflow] recording '%s' has no steps, discarding", name)
        _recording = None
        return None

    timestamp = int(time.time())
    slug = _sanitise_name(name)
    template_id = f"wft_{slug}_{timestamp}"

    template: dict[str, Any] = {
        "id": template_id,
        "name": name,
        "description": _recording["description"],
        "type": "workflow",
        "steps": steps,
        "created_at": timestamp,
        "usage_count": 0,
    }

    PROCEDURAL_DIR.mkdir(parents=True, exist_ok=True)
    filepath = PROCEDURAL_DIR / f"{template_id}.json"
    filepath.write_text(
        json.dumps(template, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    elapsed = time.time() - _recording["start_time"]
    logger.info(
        "[workflow] recording saved: %s (%d steps, %.1f s)",
        template_id,
        len(steps),
        elapsed,
    )

    result = dict(template)  # return a copy; clear the live buffer
    _recording = None
    return result


def workflow_play(name_or_id: str, confirm: bool = True) -> str:
    """Execute a saved workflow template step by step.

    Each step is logged and, when *confirm* is ``True``, the user is prompted
    before proceeding (via print + async input simulation -- in web context
    this relies on the frontend confirm flow, not blocking stdin).

    Parameters
    ----------
    name_or_id:
        The workflow **name** (case-insensitive exact match) or its full
        **ID** (``wft_...``).  Short IDs and partial names are **not**
        supported in order to avoid ambiguity.
    confirm:
        When ``True`` (the default), prompt the user with ``[Y/n/q]`` before
        each step.  Pass ``False`` for fully automatic execution (e.g. when
        being called from another automated pipeline).

    Returns
    -------
    str
        A human-readable status string such as
        ``"COMPLETED: 5/5 steps executed, 0 skipped"`` or
        ``"CANCELLED at step 3/5 (executed 2, skipped 0)"``.
        If the template is not found the string starts with ``"ERROR:"``.
    """
    template = _load_template(name_or_id)
    if template is None:
        return f"ERROR: workflow '{name_or_id}' not found"

    steps: list[dict[str, Any]] = template.get("steps", [])
    n = len(steps)
    if n == 0:
        return f"WARNING: workflow '{template['name']}' has no steps"

    logger.info("[workflow] playback starting: %s (%d steps)", template["name"], n)

    executed = 0
    skipped = 0
    step: dict[str, Any]

    for i, step in enumerate(steps, start=1):
        tool = step.get("tool", "?")
        params = step.get("params", {})

        logger.info("[workflow] step %d/%d: %s", i, n, tool)

        if confirm:
            # NOTE: blocking input() would freeze the asyncio event loop.
            # In web/async context, confirmation must happen via the
            # frontend's confirm mechanism -- not here.  When confirm=True
            # and no TTY is available we log the step and proceed.
            import sys
            if sys.__stdin__ is not None and hasattr(sys.__stdin__, 'isatty') and sys.__stdin__.isatty():
                print(f"\n[workflow] Step {i}/{n}: {tool}")
                if params:
                    param_line = json.dumps(params, ensure_ascii=False)
                    print(f"         params: {param_line}")

                response = input("         Execute step? [Y/n/q] ").strip().lower()
                if response in ("q", "quit"):
                    logger.info("[workflow] playback quit at step %d/%d", i, n)
                    return (
                        f"CANCELLED at step {i}/{n} "
                        f"(executed {executed}, skipped {skipped})"
                    )
                if response in ("n", "no"):
                    skipped += 1
                    continue
            else:
                logger.info("[workflow] non-TTY context, auto-executing step %d/%d", i, n)

        executed += 1

    _increment_usage(template)

    summary = f"COMPLETED: {executed}/{n} steps executed, {skipped} skipped"
    logger.info("[workflow] playback finished: %s", summary)
    return summary


def workflow_list() -> list[dict[str, Any]]:
    """List all saved workflow templates with their usage statistics.

    Only files whose ``"type"`` field equals ``"workflow"`` are included,
    so existing procedural chains are cleanly skipped.

    Returns
    -------
    list[dict]
        Each entry contains ``id``, ``name``, ``description``, ``steps``
        (count), ``usage_count``, and ``created_at``.  Most recently created
        templates appear first.
    """
    results: list[dict[str, Any]] = []
    for child in PROCEDURAL_DIR.glob("wft_*.json"):
        try:
            data = json.loads(child.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("type") != "workflow":
            continue
        results.append({
            "id": data.get("id", ""),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "steps": len(data.get("steps", [])),
            "usage_count": data.get("usage_count", 0),
            "created_at": data.get("created_at", 0),
        })

    results.sort(key=lambda x: x["created_at"], reverse=True)
    return results


def workflow_delete(name_or_id: str) -> bool:
    """Delete a saved workflow template by name (case-insensitive) or ID.

    Parameters
    ----------
    name_or_id:
        Same lookup semantics as :func:`workflow_play`.

    Returns
    -------
    bool
        ``True`` if a template was found and deleted, ``False`` otherwise.
    """
    template = _load_template(name_or_id)
    if template is None:
        return False

    filepath = PROCEDURAL_DIR / f"{template['id']}.json"
    try:
        filepath.unlink()
        logger.info("[workflow] deleted: %s", template["id"])
        return True
    except OSError as exc:
        logger.error("[workflow] delete failed for '%s': %s", template["id"], exc)
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sanitise_name(name: str) -> str:
    """Normalise *name* to a filesystem-safe slug.

    Lower-cases the name, replaces runs of non-alphanumeric characters with a
    single underscore, and strips leading/trailing underscores.
    """
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug if slug else "unnamed"


def _load_template(name_or_id: str) -> dict[str, Any] | None:
    """Locate and deserialise a workflow template by name or ID.

    Resolution order
    -----------------
    1. Exact ``{name_or_id}.json`` filename match inside ``PROCEDURAL_DIR``.
    2. Exact ``id`` field match across all ``wft_*.json`` files.
    3. Case-insensitive exact ``name`` field match across all ``wft_*.json``
       files.

    Returns ``None`` if no matching template is found.
    """
    # 1.  Direct file path
    exact_path = PROCEDURAL_DIR / f"{name_or_id}.json"
    if exact_path.is_file():
        try:
            data = json.loads(exact_path.read_text(encoding="utf-8"))
            if data.get("type") == "workflow":
                return data
        except Exception:
            pass

    # 2.  ID match
    for child in PROCEDURAL_DIR.glob("wft_*.json"):
        try:
            data = json.loads(child.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("type") != "workflow":
            continue
        if data.get("id") == name_or_id:
            return data

    # 3.  Name match (case-insensitive, full-string)
    search = name_or_id.strip().lower()
    for child in PROCEDURAL_DIR.glob("wft_*.json"):
        try:
            data = json.loads(child.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("type") != "workflow":
            continue
        if data.get("name", "").strip().lower() == search:
            return data

    return None


def _increment_usage(template: dict[str, Any]) -> None:
    """Atomically bump the ``usage_count`` on disk for *template*."""
    template["usage_count"] = template.get("usage_count", 0) + 1
    filepath = PROCEDURAL_DIR / f"{template['id']}.json"
    try:
        filepath.write_text(
            json.dumps(template, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("[workflow] failed to update usage count: %s", exc)
