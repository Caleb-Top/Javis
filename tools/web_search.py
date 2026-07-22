"""Web Search 和 Web Fetch 工具 (P0-4)

提供两个工具:
- web_search: 搜索引擎查询，返回标题+URL+摘要
- web_fetch: 抓取指定 URL 的网页内容并解析纯文本
"""

import json
import logging
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from html.parser import HTMLParser
from core.tool_result import ToolResult

logger = logging.getLogger("web_search")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ── HTML 到纯文本 ──
class _TextExtractor(HTMLParser):
    """从 HTML 中提取纯文本，跳过 script/style"""
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = False
        if tag in ("p", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.skip:
            t = data.strip()
            if t:
                self.text.append(t)

    def get_text(self) -> str:
        raw = " ".join(self.text)
        # 压缩连续空白
        raw = re.sub(r'\n\s*\n', '\n\n', raw)
        raw = re.sub(r' {2,}', ' ', raw)
        return raw.strip()


def _extract_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.get_text()


def _fetch_url(url: str, timeout: int = 15) -> str:
    """同步抓取 URL"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # 只读前 2MB
            raw = resp.read(2 * 1024 * 1024)
            # 尝试检测编码
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                return raw.decode(charset, errors="replace")
            except Exception:
                return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════
# WebSearch — 使用 DuckDuckGo HTML 版（无需 API key）
# ═══════════════════════════════════════════════════════════════
def web_search(
    query: str,
    count: int = 10,
    allowed_domains: list[str] = None,
    blocked_domains: list[str] = None,
    **kwargs,
) -> ToolResult:
    """搜索引擎查询"""
    if not query or not query.strip():
        return ToolResult.failure("query 不能为空")

    query = query.strip()
    # DuckDuckGo HTML 搜索
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    html = _fetch_url(url)

    if not html:
        return ToolResult.failure("搜索请求失败，请检查网络连接")

    # 解析搜索结果
    results = _parse_ddg(html)

    # 域名过滤
    allowed = set(allowed_domains or [])
    blocked = set(blocked_domains or [])
    filtered = []
    for r in results:
        try:
            host = urllib.parse.urlparse(r["url"]).hostname or ""
        except Exception:
            host = ""
        if allowed and host not in allowed and not any(d in host for d in allowed):
            continue
        if blocked and (host in blocked or any(d in host for d in blocked)):
            continue
        filtered.append(r)
        if len(filtered) >= count:
            break

    return ToolResult.success(json.dumps({
        "query": query,
        "results": filtered[:count],
        "total": len(filtered),
    }, ensure_ascii=False, indent=2))


def _parse_ddg(html: str) -> list[dict]:
    """解析 DuckDuckGo HTML 结果"""
    results = []
    # 匹配结果块
    # 找 class="result" 或 class="result__body"
    result_blocks = re.findall(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html, re.DOTALL | re.IGNORECASE
    )
    snippet_blocks = re.findall(
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        html, re.DOTALL | re.IGNORECASE
    )

    for i, (href, title_html) in enumerate(result_blocks):
        title = re.sub(r'<[^>]+>', '', title_html).strip()
        snippet = ""
        if i < len(snippet_blocks):
            snippet = re.sub(r'<[^>]+>', '', snippet_blocks[i]).strip()
        if title and href.startswith("http"):
            results.append({"title": title, "url": href, "snippet": snippet[:500]})
    return results


# ═══════════════════════════════════════════════════════════════
# WebFetch — 抓取网页内容
# ═══════════════════════════════════════════════════════════════
def web_fetch(
    url: str,
    max_chars: int = 8000,
    timeout: int = 15,
    raw_html: bool = False,
    **kwargs,
) -> ToolResult:
    """抓取网页内容并提取纯文本"""
    if not url or not url.strip():
        return ToolResult.failure("url 不能为空")

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return ToolResult.failure("url 必须以 http:// 或 https:// 开头")

    html = _fetch_url(url, timeout=min(timeout, 30))
    if not html:
        return ToolResult.failure(f"无法访问: {url}")

    if raw_html:
        return ToolResult.success(html[:max_chars])

    text = _extract_text(html)
    truncated = text[:max_chars]
    if len(text) > max_chars:
        truncated += f"\n\n... (截断, 原文 {len(text)} 字符)"

    return ToolResult.success(truncated)
