"""Conservative explicit preferences from original, source-bound user messages."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Mapping

from .claims import ClaimProposal, PREDICATE_REGISTRY
from .contracts import ExperienceEpisode


# These are entire preference objects, never keyword searches in arbitrary prose.
_OBJECTS = (
    ("communication.verbosity", "brief", r"(?:brief|short|concise) (?:answers|responses|replies)", r"(?:简短|简洁|精简)(?:的)?(?:回答|回复)"),
    ("communication.verbosity", "balanced", r"(?:balanced|moderately detailed) (?:answers|responses|replies)", r"(?:适中|详略适中)(?:的)?(?:回答|回复)"),
    ("communication.verbosity", "detailed", r"(?:detailed|in-depth) (?:answers|responses|replies)", r"(?:详细|详尽)(?:的)?(?:回答|回复)"),
    ("communication.directness", "direct", r"direct (?:answers|responses|replies|communication)", r"直接(?:的)?(?:回答|回复|沟通方式)"),
    ("communication.directness", "neutral", r"neutral communication", r"中性(?:的)?沟通方式"),
    ("communication.directness", "gentle", r"gentle (?:answers|responses|replies|communication)", r"温和(?:的)?(?:回答|回复|沟通方式)"),
    ("communication.formality", "casual", r"(?:casual|informal) (?:language|tone|responses)", r"(?:轻松|随意|非正式)(?:的)?(?:语气|表达|回复)"),
    ("communication.formality", "neutral", r"(?:a )?neutral tone", r"中性(?:的)?语气"),
    ("communication.formality", "formal", r"formal (?:language|tone|responses)", r"正式(?:的)?(?:语气|表达|回复)"),
    ("preference.units", "metric", r"(?:using )?(?:the )?metric (?:units|system)", r"(?:使用)?(?:公制|国际单位制)(?:单位)?"),
    ("preference.units", "imperial", r"(?:using )?(?:the )?imperial (?:units|system)", r"(?:使用)?英制(?:单位)?"),
    ("preference.response_format", "prose", r"(?:prose|(?:answers|responses) in prose)", r"(?:段落|正文)(?:形式)?(?:的)?(?:回答|回复)?"),
    ("preference.response_format", "bullets", r"(?:bullet points|bullets|bulleted (?:answers|responses|lists))", r"(?:分点|列表|项目符号)(?:形式)?(?:的)?(?:回答|回复)?"),
    ("preference.response_format", "table", r"(?:tables|tabular (?:answers|responses)|(?:answers|responses) in tables)", r"表格(?:形式)?(?:的)?(?:回答|回复)?"),
    ("communication.language", "zh", r"(?:Chinese|(?:answers|responses|replies) in Chinese)", r"(?:使用|用)?中文(?:回答|回复)?"),
    ("communication.language", "en", r"(?:English|(?:answers|responses|replies) in English)", r"(?:使用|用)?英文(?:回答|回复)?|(?:使用|用)?英语(?:回答|回复)?"),
)
_EN_REMEMBER = r"(?:please )?(?:remember|save|record|note)(?: that)?"
_ZH_REMEMBER = r"(?:请)?(?:记住|记下|记录)"
_PATTERNS = tuple(
    (predicate, value, re.compile(pattern, re.IGNORECASE | re.ASCII))
    for predicate, value, english, chinese in _OBJECTS
    for pattern in (
        rf"{_EN_REMEMBER}[ ,:]*I (?:prefer|like) (?:{english})[.!]?",
        rf"I (?:prefer|like) (?:{english})[,;.] {_EN_REMEMBER}[.!]?",
        rf"{_ZH_REMEMBER}[，,:： ]*我(?:更喜欢|偏好|喜欢|倾向于)(?:{chinese})[。！!]?",
        rf"我(?:更喜欢|偏好|喜欢|倾向于)(?:{chinese})[，,；;。 ]+{_ZH_REMEMBER}[。！!]?",
    )
)


def extract_claim_proposals(
    episode: ExperienceEpisode, evidence: Mapping[str, Any]
) -> tuple[ClaimProposal, ...]:
    """Return unprivileged proposals; unsupported or uncertain evidence yields none.

    The access projection must explicitly identify a non-guest owner. Each
    complete original message is matched independently, without concatenation,
    quote removal, truncation, or using any generated episode summaries.
    """
    if (
        not isinstance(episode, ExperienceEpisode)
        or not isinstance(evidence, Mapping)
        or episode.outcome != "completed"
        or episode.status != "active"
        or evidence.get("session_id") != episode.session_id
        or evidence.get("request_id") != episode.request_id
        or evidence.get("outcome") != "completed"
        or evidence.get("evidence_ready") is not True
        or evidence.get("redacted", False) is not False
        or evidence.get("redaction_receipt") is not None
        or evidence.get("terminal_conflict", False) is not False
    ):
        return ()
    access = evidence.get("access_projection")
    if (
        not isinstance(access, Mapping)
        or access.get("session_id") != episode.session_id
        or access.get("actor_subject_id") != episode.owner_subject_id
        or access.get("actor_kind") not in ("primary_user", "known_person")
        or access.get("guest_present") is not False
        or access.get("audience_ceiling") == "guest"
    ):
        return ()
    messages = evidence.get("messages")
    if not isinstance(messages, (list, tuple)):
        return ()
    counts = Counter(
        item.get("message_id") for item in messages
        if isinstance(item, Mapping) and type(item.get("message_id")) is str
    )
    found: dict[str, set[str]] = {}
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        message_id = message.get("message_id")
        if (
            type(message_id) is not str
            or message_id not in episode.source_message_ids
            or counts[message_id] != 1
            or message.get("role") != "user"
            or message.get("status") != "complete"
            or message.get("session_id") != episode.session_id
            or message.get("request_id") != episode.request_id
            or message.get("redacted", False) is not False
        ):
            continue
        content = message.get("content")
        if type(content) is not str:
            continue
        for predicate, value, pattern in _PATTERNS:
            if pattern.fullmatch(content.strip()):
                found.setdefault(predicate, set()).add(value)
    # Conflicting explicit statements are not resolved by guessing chronology.
    return tuple(
        ClaimProposal.from_dict({
            "predicate": predicate,
            "value_type": PREDICATE_REGISTRY[predicate].value_type,
            "value": next(iter(values)),
            "source_evidence_ids": (episode.episode_id,),
        })
        for predicate, values in sorted(found.items()) if len(values) == 1
    )
