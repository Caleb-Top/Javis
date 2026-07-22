"""
Skills 管理器 — 可安装的知识/指令束

Skills 是 Claude Agent 的扩展机制。每个 Skill 是一个文件夹，包含:
- SKILL.md          ← 核心指令 (必须)
- scripts/          ← 可执行脚本
- references/       ← 参考文档
- agents/           ← 子代理配置
"""
from __future__ import annotations
import os, glob, logging
from typing import Optional
from pathlib import Path

from .types import SkillDef

logger = logging.getLogger("agent.skills")


class SkillManager:
    """
    Skills 管理器。

    职责:
    1. 从文件系统扫描和加载 Skills
    2. 提供关键词匹配 (用户输入 → 对应 Skill)
    3. 执行 Skill 脚本
    """

    def __init__(self, skills_dirs: list[str]):
        """
        Args:
            skills_dirs: Skill 搜索目录列表
                        如 ["~/skills", "/opt/skills"]
        """
        self.skills_dirs = skills_dirs
        self._skills: dict[str, SkillDef] = {}
        self.refresh()

    # ═══════════════════════════════════════════════
    # 发现与加载
    # ═══════════════════════════════════════════════

    def refresh(self) -> int:
        """重新扫描所有 Skills 目录并加载"""
        self._skills.clear()
        count = 0

        for d in self.skills_dirs:
            if not os.path.isdir(d):
                continue

            # 寻找所有 SKILL.md
            for skill_md in glob.glob(os.path.join(d, "**/SKILL.md"), recursive=True):
                skill_dir = os.path.dirname(skill_md)
                name = os.path.basename(skill_dir)
                if name.startswith("."):
                    continue

                skill = self._load_skill(name, skill_dir)
                if skill:
                    self._skills[name] = skill
                    count += 1

        logger.info(f"Skills: 已加载 {count} 个")
        return count

    def _load_skill(self, name: str, skill_dir: str) -> Optional[SkillDef]:
        """从目录加载单个 Skill"""
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        if not os.path.exists(skill_md_path):
            return None

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"无法读取 Skill [{name}]: {e}")
            return None

        # 解析 frontmatter 获取 name 和 description
        description = ""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    import yaml
                    fm = yaml.safe_load(parts[1]) or {}
                    description = fm.get("description", "")
                except Exception:
                    pass

        # 如果没有 description, 尝试从内容第一行提取
        if not description:
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("---"):
                    description = line[:150]
                    break

        # 检查 scripts 目录
        scripts_dir = None
        scripts_path = os.path.join(skill_dir, "scripts")
        if os.path.isdir(scripts_path):
            scripts_dir = scripts_path

        # 检查 references 目录
        references = {}
        refs_path = os.path.join(skill_dir, "references")
        if os.path.isdir(refs_path):
            for fname in os.listdir(refs_path):
                fpath = os.path.join(refs_path, fname)
                if os.path.isfile(fpath):
                    references[fname] = fpath

        return SkillDef(
            name=name,
            description=description,
            instructions=content,
            scripts_dir=scripts_dir,
            references=references,
        )

    # ═══════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════

    def get(self, name: str) -> Optional[SkillDef]:
        return self._skills.get(name)

    def list_all(self) -> list[SkillDef]:
        return list(self._skills.values())

    def list_names(self) -> list[str]:
        return list(self._skills.keys())

    # ═══════════════════════════════════════════════
    # 匹配
    # ═══════════════════════════════════════════════

    def match(self, user_input: str) -> Optional[SkillDef]:
        """
        关键词匹配 — 找到与用户输入最相关的 Skill。

        匹配策略 (按优先级):
        1. 精确匹配 Skill 名称
        2. description 关键词匹配 (至少 2 个词)
        3. 常见的触发短语匹配
        """
        text = user_input.lower()

        # 精确名称匹配
        for name, skill in self._skills.items():
            if name.lower() in text:
                logger.info(f"Skill 匹配 (名称): {name}")
                return skill

        # 关键词匹配
        best_skill = None
        best_score = 0
        import re
        for name, skill in self._skills.items():
            desc_lower = skill.description.lower()
            # 提取纯单词 (去标点)
            keywords = [w for w in re.findall(r'[\w]+', desc_lower) if len(w) > 2]
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                best_skill = skill

        if best_score >= 2:
            logger.info(f"Skill 匹配 (关键词 score={best_score}): {best_skill.name}")
            return best_skill

        return None

    # ═══════════════════════════════════════════════
    # 脚本执行
    # ═══════════════════════════════════════════════

    def run_script(self, skill_name: str, script_name: str, *args) -> tuple[int, str]:
        """
        执行 Skill 中的脚本。

        Returns:
            (exit_code, output)
        """
        skill = self._skills.get(skill_name)
        if not skill:
            return -1, f"Skill 未找到: {skill_name}"

        if not skill.scripts_dir:
            return -1, f"Skill [{skill_name}] 无 scripts 目录"

        import subprocess
        script_path = os.path.join(skill.scripts_dir, script_name)
        if not os.path.exists(script_path):
            return -1, f"脚本未找到: {script_path}"

        try:
            result = subprocess.run(
                [script_path] + list(args),
                capture_output=True, text=True, timeout=30,
            )
            return result.returncode, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return -1, "脚本执行超时"
        except Exception as e:
            return -1, f"脚本执行失败: {e}"


# ═══════════════════════════════════════════════
# 内置 Skills 注册
# ═══════════════════════════════════════════════

def register_builtin_skills() -> list[SkillDef]:
    """
    返回内置的 Skills 列表。

    每个 Skill 的 description 是关键 — 它决定了触发条件。
    真正的 SKILL.md 内容在磁盘上，这里是内存中的索引。
    """
    return [
        SkillDef(
            name="docx",
            description="Word 文档创建/编辑/模板 — 触发: 'Word doc', '.docx', '报告', '备忘录'",
            instructions="[从 skills/docx/SKILL.md 加载]",
            installed=True,
        ),
        SkillDef(
            name="xlsx",
            description="Excel 电子表格/CSV 处理 — 触发: 'Excel', '.xlsx', '.csv', '表格'",
            instructions="[从 skills/xlsx/SKILL.md 加载]",
            installed=True,
        ),
        SkillDef(
            name="pptx",
            description="PowerPoint 演示文稿 — 触发: 'PPT', '演示', 'slides', '.pptx'",
            instructions="[从 skills/pptx/SKILL.md 加载]",
            installed=True,
        ),
        SkillDef(
            name="pdf",
            description="PDF 创建/合并/拆分/填表 — 触发: 'PDF', '.pdf', '合并PDF'",
            instructions="[从 skills/pdf/SKILL.md 加载]",
            installed=True,
        ),
        SkillDef(
            name="pdf-reading",
            description="PDF 内容提取/阅读策略 — 触发: 需要从 PDF 提取内容时",
            instructions="[从 skills/pdf-reading/SKILL.md 加载]",
            installed=True,
        ),
        SkillDef(
            name="frontend-design",
            description="UI 设计指导 — 触发: '设计', 'UI', '前端', '美化'",
            instructions="[从 skills/frontend-design/SKILL.md 加载]",
            installed=True,
        ),
        SkillDef(
            name="schedule",
            description="定时任务管理 — 触发: '定时', '每天', '提醒', '日程'",
            instructions="[从 skills/schedule/SKILL.md 加载]",
            installed=True,
        ),
        SkillDef(
            name="superpowers",
            description="14个开发工作流技能 — 触发: '开发', 'TDD', '调试', '代码审查'",
            instructions="[从 skills/superpowers/SKILL.md 加载]",
            installed=True,
        ),
        SkillDef(
            name="auto-git-sync",
            description="自动 Git 推送设置 — 触发: '自动推送', 'sync this project', 'GitHub 同步'",
            instructions="[从 skills/auto-git-sync/SKILL.md 加载]",
            installed=True,
        ),
        SkillDef(
            name="consolidate-memory",
            description="Memory 维护 — 合并重复项/清理过期/修剪索引",
            instructions="[从 skills/consolidate-memory/SKILL.md 加载]",
            installed=True,
        ),
    ]
