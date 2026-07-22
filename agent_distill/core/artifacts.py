"""
Artifact 系统 — 持久化交互式 HTML 页面

Artifact 是:
- 自包含 HTML 页面
- 在侧边栏中渲染
- 跨会话持久化
- 可以通过 window.cowork API 调用 MCP 工具
- 支持 Chart.js, Grid.js, Mermaid 图表

核心 API (页面内可用):
  window.cowork.callMcpTool(name, args)  → 调用 MCP 工具
  window.cowork.askClaude(prompt, data)  → 轻量 LLM 推理
  window.cowork.runScheduledTask(id)     → 触发定时任务
"""
from __future__ import annotations
import os, json, uuid, logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("agent.artifacts")


@dataclass
class ArtifactDef:
    """Artifact 定义"""
    id: str                               # kebab-case slug
    name: str
    description: str                      # 显示在列表中的描述
    html_path: str                        # HTML 文件路径
    mcp_tools: list[str] = field(default_factory=list)  # 页面可用的 MCP 工具
    created_at: str = ""
    updated_at: str = ""


class ArtifactManager:
    """
    Artifact 管理器。

    职责:
    1. 创建新 Artifact (生成 HTML 文件 + 注册到清单)
    2. 更新已有 Artifact
    3. 列出/删除 Artifact
    """

    ALLOWED_CDN_LIBS = {
        "chart.js": (
            '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js" '
            'integrity="sha384-iU8HYtnGQ8Cy4zl7gbNMOhsDTTKX02BTXptVP/vqAWIaTfM7isw76iyZCsjL2eVi" '
            'crossorigin="anonymous"></script>'
        ),
        "gridjs": (
            '<script src="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/gridjs.umd.js" '
            'integrity="sha384-/XXDzxe4FsGiAe50i/u9pY/Vy/uX654MHB1xoc1BJNnH1WXHhqHga9g3q5tF4gj7" '
            'crossorigin="anonymous"></script>'
        ),
        "mermaid": (
            '<script src="https://cdn.jsdelivr.net/npm/mermaid@11.15.0/dist/mermaid.min.js" '
            'integrity="sha384-yQ4mmBBT+vhTAwjFH0toJXNYJ6O4usWnt6EPIdWwrRvx2V/n5lXuDZQwQFeSFydF" '
            'crossorigin="anonymous"></script>'
        ),
    }

    def __init__(self, artifacts_dir: str):
        self.artifacts_dir = artifacts_dir
        os.makedirs(artifacts_dir, exist_ok=True)
        self._manifest_path = os.path.join(artifacts_dir, "manifest.json")

    # ═══════════════════════════════════════════════
    # CRUD
    # ═══════════════════════════════════════════════

    def create(
        self,
        id: str,
        html_content: str,
        description: str = "",
        mcp_tools: Optional[list[str]] = None,
    ) -> ArtifactDef:
        """创建新 Artifact"""
        import datetime

        # 验证 ID
        if not id.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"无效的 Artifact ID: {id}")

        # 写入 HTML 文件
        html_path = os.path.join(self.artifacts_dir, f"{id}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # 注册到清单
        now = datetime.datetime.now().isoformat()
        artifact = ArtifactDef(
            id=id,
            name=id.replace("-", " ").title(),
            description=description,
            html_path=html_path,
            mcp_tools=mcp_tools or [],
            created_at=now,
            updated_at=now,
        )
        self._add_to_manifest(artifact)

        logger.info(f"Artifact 创建: {id}")
        return artifact

    def update(self, id: str, html_content: str, description: str = "") -> Optional[ArtifactDef]:
        """更新已有 Artifact"""
        import datetime
        artifact = self.get(id)
        if not artifact:
            return None

        with open(artifact.html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        artifact.description = description or artifact.description
        artifact.updated_at = datetime.datetime.now().isoformat()
        self._update_manifest(artifact)

        logger.info(f"Artifact 更新: {id}")
        return artifact

    def get(self, id: str) -> Optional[ArtifactDef]:
        manifest = self._read_manifest()
        for entry in manifest:
            if entry.get("id") == id:
                return ArtifactDef(**entry)
        return None

    def list_all(self) -> list[ArtifactDef]:
        manifest = self._read_manifest()
        return [ArtifactDef(**e) for e in manifest]

    def delete(self, id: str) -> bool:
        artifact = self.get(id)
        if not artifact:
            return False
        if os.path.exists(artifact.html_path):
            os.remove(artifact.html_path)
        self._remove_from_manifest(id)
        return True

    # ═══════════════════════════════════════════════
    # 清单管理
    # ═══════════════════════════════════════════════

    def _read_manifest(self) -> list[dict]:
        if not os.path.exists(self._manifest_path):
            return []
        try:
            with open(self._manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _write_manifest(self, data: list[dict]) -> None:
        with open(self._manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _add_to_manifest(self, artifact: ArtifactDef) -> None:
        data = self._read_manifest()
        data = [e for e in data if e.get("id") != artifact.id]
        data.append({
            "id": artifact.id,
            "name": artifact.name,
            "description": artifact.description,
            "html_path": artifact.html_path,
            "mcp_tools": artifact.mcp_tools,
            "created_at": artifact.created_at,
            "updated_at": artifact.updated_at,
        })
        self._write_manifest(data)

    def _update_manifest(self, artifact: ArtifactDef) -> None:
        self._add_to_manifest(artifact)  # 同样的逻辑

    def _remove_from_manifest(self, id: str) -> None:
        data = self._read_manifest()
        data = [e for e in data if e.get("id") != id]
        self._write_manifest(data)


# ═══════════════════════════════════════════════
# Artifact HTML 模板
# ═══════════════════════════════════════════════

def build_artifact_html(
    title: str,
    body_html: str,
    inline_js: str = "",
    use_chartjs: bool = False,
    use_gridjs: bool = False,
    use_mermaid: bool = False,
) -> str:
    """
    构建 Artifact 的完整 HTML 文档。

    规则:
    - 完全自包含 (CSS/JS 全部内联)
    - 只允许 Chart.js / Grid.js / Mermaid 三种 CDN 库
    - 禁止 localStorage/sessionStorage
    - 浅色主题 (light mode)
    """
    cdn_scripts = ""
    if use_chartjs:
        cdn_scripts += ArtifactManager.ALLOWED_CDN_LIBS["chart.js"] + "\n"
    if use_gridjs:
        cdn_scripts += ArtifactManager.ALLOWED_CDN_LIBS["gridjs"] + "\n"
        cdn_scripts += (
            '<link rel="stylesheet" '
            'href="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/theme/mermaid.min.css" '
            'integrity="sha384-jZvDSsmGB9oGGT/4l9bHXGoAv1OxvG/cFmSo0dZaSqmBgvQTKDBFAMftlXTmMbNW" '
            'crossorigin="anonymous">\n'
        )
    if use_mermaid:
        cdn_scripts += ArtifactManager.ALLOWED_CDN_LIBS["mermaid"] + "\n"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    {cdn_scripts}
    <style>
        :root {{ color-scheme: light; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #ffffff;
            color: #1a1a1a;
            padding: 24px;
            line-height: 1.6;
        }}
        .container {{ max-width: 960px; margin: 0 auto; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 16px; }}
        .card {{
            background: #f8f9fa;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 16px;
        }}
        button {{
            padding: 8px 16px;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            background: #ffffff;
            cursor: pointer;
            font-size: 0.875rem;
        }}
        button:hover {{ background: #f3f4f6; }}
        .status {{ font-size: 0.75rem; color: #6b7280; margin-top: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        {body_html}
        <p class="status">Artifact — 数据在打开时自动刷新</p>
    </div>
    <script>
    {inline_js}
    </script>
</body>
</html>"""
