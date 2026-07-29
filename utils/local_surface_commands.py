"""Recognize explicit requests for native Javis surfaces."""

from __future__ import annotations

import re


_COMMAND_PATTERNS = (
    (
        "code",
        re.compile(r"^(?:请)?(?:打开|进入|切换到|显示|调出)?(?:code|代码)(?:页面|界面|模式|工作台)?$"),
    ),
    (
        "settings",
        re.compile(r"^(?:请)?(?:打开|进入|切换到|显示|调出)?(?:设置|setting|settings)(?:页面|界面|面板)?$"),
    ),
    (
        "diagnostics",
        re.compile(r"^(?:请)?(?:打开|进入|显示|运行)?(?:诊断|自检|diagnostics?)(?:页面|界面|面板)?$"),
    ),
    (
        "live",
        re.compile(r"^(?:请)?(?:打开|进入|切换到|返回|回到|显示)?(?:live|交流|交流球|主界面)(?:页面|界面|模式)?$"),
    ),
)


def match_local_surface_command(text: str) -> str | None:
    normalized = re.sub(r"""[，。！？,.!?：:；;'\"“”‘’\s]""", "", str(text or "").strip().lower())
    for command, pattern in _COMMAND_PATTERNS:
        if pattern.fullmatch(normalized):
            return command
    return None
