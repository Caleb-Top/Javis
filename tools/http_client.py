"""HTTP request tools for Javis.

Provides get/post wrappers around httpx.AsyncClient.
All responses are truncated to 5000 characters to prevent context overflow.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("http_client")

_TRUNCATE_LIMIT = 5000
_DEFAULT_TIMEOUT = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _truncate(text: str, limit: int = _TRUNCATE_LIMIT) -> str:
    """Truncate text to *limit* characters, appending a notice if cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... (truncated, {len(text)} total chars)"


def _format_error(label: str, exc: Exception) -> str:
    """Return a human-readable one-line error string for common httpx exceptions."""
    if isinstance(exc, httpx.TimeoutException):
        return f"[{label}] Request timed out: {exc}"
    if isinstance(exc, httpx.ConnectError):
        return f"[{label}] Connection failed: {exc}"
    if isinstance(exc, httpx.RemoteProtocolError):
        return f"[{label}] Remote protocol error: {exc}"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"[{label}] HTTP {exc.response.status_code}: {exc}"
    return f"[{label}] {type(exc).__name__}: {exc}"


async def http_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float | int = _DEFAULT_TIMEOUT,
) -> str:
    """Perform an HTTP GET request and return a truncated summary.

    Parameters
    ----------
    url : str
        Target URL.
    headers : dict or None
        Optional extra HTTP headers.
    timeout : float
        Request timeout in seconds (default 30).

    Returns
    -------
    str
        Summary string containing the status code and truncated body,
        or an error description on failure.
    """
    merged_headers = {"User-Agent": _USER_AGENT}
    if headers:
        merged_headers.update(headers)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            logger.info("GET %s", url)
            resp = await client.get(url, headers=merged_headers, follow_redirects=True)
            resp.raise_for_status()
            body = resp.text
    except Exception as exc:
        logger.warning("GET %s failed: %s", url, exc)
        return _format_error("GET", exc)

    snippet = _truncate(body)
    content_type = resp.headers.get("content-type", "")
    logger.info("GET %s -> %s (%s)", url, resp.status_code, content_type)
    return (
        f"Status: {resp.status_code}\n"
        f"Content-Type: {content_type}\n"
        f"Content-Length: {len(body)} bytes\n\n"
        f"{snippet}"
    )


async def http_post(
    url: str,
    json: Any = None,
    data: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float | int = _DEFAULT_TIMEOUT,
) -> str:
    """Perform an HTTP POST request and return a truncated summary.

    Parameters
    ----------
    url : str
        Target URL.
    json : Any
        JSON-serialisable payload (sets Content-Type to application/json).
    data : Any
        Raw form/data payload.
    headers : dict or None
        Optional extra HTTP headers.
    timeout : float
        Request timeout in seconds (default 30).

    Returns
    -------
    str
        Summary string containing the status code and truncated body,
        or an error description on failure.
    """
    merged_headers = {"User-Agent": _USER_AGENT}
    if headers:
        merged_headers.update(headers)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            logger.info("POST %s", url)
            resp = await client.post(
                url,
                json=json,
                data=data,
                headers=merged_headers,
                follow_redirects=True,
            )
            resp.raise_for_status()
            body = resp.text
    except Exception as exc:
        logger.warning("POST %s failed: %s", url, exc)
        return _format_error("POST", exc)

    snippet = _truncate(body)
    content_type = resp.headers.get("content-type", "")
    logger.info("POST %s -> %s (%s)", url, resp.status_code, content_type)
    return (
        f"Status: {resp.status_code}\n"
        f"Content-Type: {content_type}\n"
        f"Content-Length: {len(body)} bytes\n\n"
        f"{snippet}"
    )
