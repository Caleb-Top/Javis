"""工作区管理器 — Javis 的自我意识和文件管理核心"""

import os, json, time, uuid, shutil, logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from core.tool_result import ToolResult

logger = logging.getLogger("workspace_manager")

# ═══════════════════════════════════════════════════════════════
# 共享沙箱常量 — 迁移自 tools/code_exec.py
# 用于: code_exec 沙箱 + file_delete 工作区工具
# ═══════════════════════════════════════════════════════════════
JARVIS_ROOT = Path(__file__).parent.parent.resolve()
SANDBOX_PROTECTED_DIRS = [
    JARVIS_ROOT / d for d in
    ("core", "knowledge", "tools", "data", "brain_data", "skills", "voice", "web", "utils")
]
SANDBOX_PROTECTED_FILES = [
    JARVIS_ROOT / "main.py",
    JARVIS_ROOT / "config.yaml",
]


def sandbox_check_path(target) -> None:
    """拒绝修改 JAVIS 系统文件/目录 — 与 code_exec 沙箱相同逻辑"""
    try:
        tp = Path(target).resolve()
        for pf in SANDBOX_PROTECTED_FILES:
            if tp == pf:
                raise PermissionError(f"[沙箱] 禁止操作 JARVIS 系统文件: {pf.name}")
        for pd in SANDBOX_PROTECTED_DIRS:
            if tp == pd or pd in tp.parents:
                raise PermissionError(f"[沙箱] 禁止操作 JARVIS 系统目录: {pd.name}/")
    except PermissionError:
        raise
    except Exception:
        pass  # 无法解析的路径放行


# ═══════════════════════════════════════════════════════════════
# 工作区数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class WorkspaceEntry:
    """工作区文件清单条目"""
    path: str               # 相对于 workspace/ 的路径
    purpose: str            # 为什么创建它
    category: str           # "temp" | "project" | "thought"
    created_at: float       # time.time()
    created_by: str         # 创建时用的工具名
    task_snippet: str       # 用户输入的前80字符
    size: int = 0           # 文件字节数
    tags: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 工作区管理器
# ═══════════════════════════════════════════════════════════════

WORKSPACE_ROOT = JARVIS_ROOT / "workspace"
MANIFEST_PATH = WORKSPACE_ROOT / "manifest.json"


class WorkspaceManager:
    """管理 Javis 的自我工作区"""

    def __init__(self):
        self._ensure_structure()

    # ── 目录结构 ──

    def _ensure_structure(self):
        """确保工作区目录存在"""
        for d in ["", "temp", "projects", "thoughts"]:
            (WORKSPACE_ROOT / d).mkdir(parents=True, exist_ok=True)

    # ── 清单读写 ──

    def _load_manifest(self) -> list[WorkspaceEntry]:
        """读取 manifest.json，返回所有条目"""
        if not MANIFEST_PATH.exists():
            return []
        try:
            data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            return [WorkspaceEntry(**e) for e in data.get("entries", [])]
        except Exception as e:
            logger.debug(f"读取工作区清单失败: {e}")
            return []

    def _save_manifest(self, entries: list[WorkspaceEntry]):
        """写入 manifest.json"""
        data = {
            "version": 1,
            "updated_at": time.time(),
            "entries": [
                {
                    "path": e.path,
                    "purpose": e.purpose,
                    "category": e.category,
                    "created_at": e.created_at,
                    "created_by": e.created_by,
                    "task_snippet": e.task_snippet,
                    "size": e.size,
                    "tags": e.tags,
                }
                for e in entries
            ]
        }
        MANIFEST_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # ── 核心操作 ──

    def create_file(self, rel_path: str, content: str, purpose: str,
                    category: str = "thought", task_context: str = "") -> ToolResult:
        """在工作区创建文件并注册到清单"""
        if category not in ("temp", "project", "thought"):
            return ToolResult.failure(f"无效分类: {category}，可选: temp/project/thought")

        # 路径安全
        safe_name = rel_path.replace("..", "").replace("~", "").replace("/", "_").replace("\\", "_")
        if not safe_name:
            safe_name = f"{uuid.uuid4().hex[:8]}.txt"

        sub_dir = "temp" if category == "temp" else ("projects" if category == "project" else "thoughts")
        full_dir = WORKSPACE_ROOT / sub_dir
        full_dir.mkdir(parents=True, exist_ok=True)
        full_path = full_dir / safe_name

        try:
            full_path.write_text(content, encoding="utf-8")
        except Exception as e:
            return ToolResult.failure(f"写入失败: {e}")

        entry = WorkspaceEntry(
            path=f"{sub_dir}/{safe_name}",
            purpose=purpose,
            category=category,
            created_at=time.time(),
            created_by="create_workspace_file",
            task_snippet=task_context[:80],
            size=len(content),
        )
        entries = self._load_manifest()
        entries.append(entry)
        self._save_manifest(entries)

        return ToolResult.success(
            f"✅ 已创建 {category} 文件: workspace/{sub_dir}/{safe_name}\n"
            f"   用途: {purpose}\n"
            f"   大小: {len(content)} 字符"
        )

    def create_temp(self, content: str, purpose: str, task_context: str = "") -> ToolResult:
        """快速创建临时文件（自动 UUID 命名）"""
        safe_name = f"temp_{uuid.uuid4().hex[:8]}_{int(time.time())}.txt"
        return self.create_file(safe_name, content, purpose, "temp", task_context)

    def list_workspace(self) -> ToolResult:
        """列出工作区所有文件"""
        self._ensure_structure()
        entries = self._load_manifest()
        if not entries:
            # 也扫描磁盘上是否有未被清单追踪的文件
            orphan = []
            for sub in ["temp", "projects", "thoughts"]:
                d = WORKSPACE_ROOT / sub
                if d.exists():
                    orphan.extend([f"{sub}/{f.name}" for f in d.iterdir() if f.is_file()])
            if orphan:
                return ToolResult.success(f"📂 工作区文件 ({len(orphan)} 个, 未追踪):\n" + "\n".join(f"  📄 {p}" for p in orphan))
            return ToolResult.success("📂 工作区为空")

        lines = [f"📂 工作区文件 ({len(entries)} 个):"]
        # 按分类分组
        for cat in ("temp", "project", "thought"):
            cat_entries = [e for e in entries if e.category == cat]
            if cat_entries:
                cat_name = {"temp": "临时", "project": "项目", "thought": "思考"}[cat]
                lines.append(f"\n  [{cat_name}]")
                for e in sorted(cat_entries, key=lambda x: x.created_at, reverse=True):
                    age = time.time() - e.created_at
                    age_str = f"{age/60:.0f}分钟前" if age < 3600 else f"{age/3600:.1f}小时前"
                    size_str = f"{e.size}B" if e.size < 1024 else f"{e.size/1024:.1f}KB"
                    lines.append(f"    📄 {e.path} ({size_str}, {age_str})")
                    lines.append(f"        📝 {e.purpose[:60]}")

        return ToolResult.success("\n".join(lines))

    def cleanup_temp(self, confirmed: bool = False) -> ToolResult:
        """清理临时文件。需要 confirmed=True 才执行删除"""
        entries = self._load_manifest()
        temp_entries = [e for e in entries if e.category == "temp"]

        if not temp_entries:
            # 检查磁盘上是否有残留 temp 文件
            temp_dir = WORKSPACE_ROOT / "temp"
            orphan_files = list(temp_dir.iterdir()) if temp_dir.exists() else []
            if orphan_files:
                msg = f"📋 发现 {len(orphan_files)} 个未追踪的临时文件。\n是否删除它们？请回复时把 confirmed=true 传入 cleanup_temp。"
                return ToolResult.success(msg)
            return ToolResult.success("✅ 没有临时文件需要清理")

        if not confirmed:
            files_list = "\n".join(f"  📄 {e.path} ({e.purpose[:40]})" for e in temp_entries)
            return ToolResult.success(
                f"📋 发现 {len(temp_entries)} 个临时文件:\n{files_list}\n"
                f"需要清理吗？请回复确认，我收到后会调用 cleanup_temp(confirmed=true) 执行删除。"
            )

        # 执行删除
        deleted = []
        remaining = []
        for e in entries:
            full_path = WORKSPACE_ROOT / e.path
            try:
                if e.category == "temp" and full_path.exists():
                    full_path.unlink()
                    deleted.append(e.path)
                else:
                    remaining.append(e)
            except Exception as ex:
                logger.debug(f"清理临时文件失败 {e.path}: {ex}")
                remaining.append(e)

        self._save_manifest(remaining)
        msg = f"✅ 已清理 {len(deleted)} 个临时文件" + ("\n" + "\n".join(f"  🗑 {p}" for p in deleted)) if deleted else "没有文件被删除"
        return ToolResult.success(msg)

    def organize(self) -> ToolResult:
        """扫描工作区根目录，将游离文件归入子目录"""
        self._ensure_structure()
        moved = 0
        # 按扩展名推断分类
        ext_map = {
            ".md": "thoughts", ".txt": "thoughts",
            ".json": "projects", ".yaml": "projects", ".yml": "projects",
            ".py": "projects", ".js": "projects", ".html": "projects",
            ".log": "temp", ".tmp": "temp", ".cache": "temp",
        }
        for f in WORKSPACE_ROOT.iterdir():
            if not f.is_file() or f.name == "manifest.json":
                continue
            target_sub = "thoughts"
            for ext, sub in ext_map.items():
                if f.suffix.lower() == ext:
                    target_sub = sub
                    break
            target = WORKSPACE_ROOT / target_sub / f.name
            if not target.exists():
                try:
                    f.rename(target)
                    moved += 1
                except Exception as e:
                    logger.debug(f"整理文件失败 {f.name}: {e}")
        if moved:
            return ToolResult.success(f"✅ 已整理 {moved} 个文件到对应的子目录")
        return ToolResult.success("📂 工作区结构已经整齐，无需整理")

    def reflect(self) -> ToolResult:
        """分析工作区状态，给出建议"""
        self._ensure_structure()
        entries = self._load_manifest()

        # 统计磁盘文件
        disk_files = {}
        for sub in ["temp", "projects", "thoughts"]:
            d = WORKSPACE_ROOT / sub
            if d.exists():
                disk_files[sub] = [f for f in d.iterdir() if f.is_file()]

        # 统计清单
        by_cat = {"temp": 0, "project": 0, "thought": 0}
        total_size = 0
        for e in entries:
            if e.category in by_cat:
                by_cat[e.category] += 1
            total_size += e.size

        # 计算年龄
        old_files = [e for e in entries if time.time() - e.created_at > 86400 * 7]  # 7天以上

        lines = [
            "🧠 工作区自我分析",
            f"━━━━━━━━━━━━━━",
            f"📊 统计:",
            f"   临时文件: {by_cat['temp']} 个",
            f"   项目文件: {by_cat['project']} 个",
            f"   思考笔记: {by_cat['thought']} 个",
            f"   总大小: {total_size/1024:.1f}KB",
        ]

        # 磁盘 vs 清单一致性
        tracked = set(e.path for e in entries)
        for sub, files in disk_files.items():
            for f in files:
                rel = f"{sub}/{f.name}"
                if rel not in tracked:
                    lines.append(f"\n⚠️ 未追踪文件: {rel}")

        if old_files:
            lines.append(f"\n💡 建议: 有 {len(old_files)} 个文件超过7天未使用，可以考虑归档或清理")

        if by_cat["temp"] > 5:
            lines.append(f"💡 建议: 临时文件较多({by_cat['temp']}个)，可运行 cleanup_temp 清理")

        # 记录到 brain
        lines.append(f"\n✅ 分析完成")
        return ToolResult.success("\n".join(lines))

    def delete_file(self, path: str) -> ToolResult:
        """删除文件或目录（含沙箱检查）。实现了缺失的 file_delete 工具"""
        # 检查沙箱保护
        try:
            sandbox_check_path(path)
        except PermissionError as e:
            return ToolResult.failure(str(e))
        except Exception:
            pass

        try:
            p = Path(path)
            if not p.exists():
                return ToolResult.failure(f"路径不存在: {path}")
            if p.is_file():
                p.unlink()
                # 尝试从清单移除
                self._remove_from_manifest(path)
                return ToolResult.success(f"✅ 已删除文件: {path}")
            elif p.is_dir():
                shutil.rmtree(p)
                return ToolResult.success(f"✅ 已删除目录: {path}")
            else:
                return ToolResult.failure(f"无法删除: {path}")
        except Exception as e:
            return ToolResult.failure(f"删除失败: {e}")

    def _remove_from_manifest(self, abs_path: str):
        """如果删除的文件在工作区内，从清单移除对应条目"""
        try:
            rp = Path(abs_path).resolve()
            wr = WORKSPACE_ROOT.resolve()
            if wr in rp.parents or rp == wr:
                rel = str(rp.relative_to(wr)).replace("\\", "/")
                entries = self._load_manifest()
                entries = [e for e in entries if e.path != rel]
                self._save_manifest(entries)
        except Exception:
            pass

    def get_temp_files(self) -> list[WorkspaceEntry]:
        """返回所有临时文件条目"""
        return [e for e in self._load_manifest() if e.category == "temp"]

    def record_as_fact(self, brain):
        """将工作区统计写入 brain 事实"""
        if not brain:
            return
        try:
            entries = self._load_manifest()
            by_cat = {"temp": 0, "project": 0, "thought": 0}
            for e in entries:
                if e.category in by_cat:
                    by_cat[e.category] += 1
            total = sum(by_cat.values())
            if total > 0:
                brain.learn_fact(
                    f"工作区状态: {total}个文件 (临时{by_cat['temp']}, 项目{by_cat['project']}, 思考{by_cat['thought']})",
                    category="self_awareness",
                    source="self_reflection"
                )
        except Exception:
            pass
