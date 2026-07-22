"""
P3-3: /learn 技能闭环 — AI自创技能 + background_review + Curator
"""
import os, logging, time, json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional

logger = logging.getLogger("skill_creator")

SKILLS_DIR = Path(__file__).parent.parent / "skills"

@dataclass
class SkillDef:
    name: str
    description: str
    system_prompt: str = ""
    category: str = "general"
    status: str = "active"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "description": self.description,
            "category": self.category, "status": self.status,
        }


class SkillCreator:
    """技能创建器 — /learn 命令触发 → background_review → Curator"""

    def __init__(self):
        SKILLS_DIR.mkdir(exist_ok=True)

    def create(self, name: str, description: str, prompt: str) -> str:
        """创建新技能文件"""
        filename = f"{name}.py"
        path = SKILLS_DIR / filename
        content = f'''"""
{description}
"""
def register(tools):
    """技能注册入口"""
    pass

# 技能 Prompt
SKILL_PROMPT = """{prompt}"""
'''
        path.write_text(content, encoding="utf-8")
        logger.info(f"技能已创建: {name} → {path}")
        return str(path)

    def review(self, name: str) -> dict:
        """审查技能质量"""
        path = SKILLS_DIR / f"{name}.py"
        if not path.exists():
            return {"status": "missing"}
        content = path.read_text()
        return {
            "status": "active",
            "lines": len(content.splitlines()),
            "has_prompt": "SKILL_PROMPT" in content,
            "has_register": "def register" in content,
            "size": len(content)
        }

    def list_skills(self) -> list[str]:
        """列出所有技能名称"""
        return sorted(
            [f.stem for f in SKILLS_DIR.glob("*.py") if f.name != "__init__.py"]
        )

    def delete_skill(self, name: str) -> bool:
        """删除技能"""
        path = SKILLS_DIR / f"{name}.py"
        if path.exists():
            path.unlink()
            return True
        return False

    def get_stats(self) -> dict:
        """技能统计"""
        names = self.list_skills()
        return {"total": len(names), "skills": names}


# 单例
_creator: Optional[SkillCreator] = None


def get_creator() -> SkillCreator:
    global _creator
    if _creator is None:
        _creator = SkillCreator()
    return _creator


def get_skill_creator(skills_dir: str = "") -> SkillCreator:
    """main.py 兼容方法"""
    return get_creator()


def register_in_manifest(reg):
    """Register skill creator tools in manifest"""
    from core.tool_registry import ToolDef
    sc = get_creator()

    async def create_skill(args):
        path = sc.create(
            name=args["name"],
            description=args.get("description", ""),
            prompt=args.get("system_prompt", ""),
        )
        return {"success": True, "path": path, "name": args["name"]}

    async def list_skills(args):
        skills = sc.list_skills()
        return {"success": True, "skills": skills, "count": len(skills)}

    async def review_skill(args):
        result = sc.review(args["name"])
        return {"success": True, **result}

    async def delete_skill(args):
        ok = sc.delete_skill(args["name"])
        return {"success": ok}

    async def skill_stats(args):
        return {"success": True, **sc.get_stats()}

    reg.register_many([
        ToolDef("skill_create", "Create a new skill via /learn",
                {"type":"object","properties":{"name":{"type":"string"},"description":{"type":"string","default":""},"system_prompt":{"type":"string","default":""}},"required":["name"]}, create_skill, "skill"),
        ToolDef("skill_list", "List all learned skills",
                {"type":"object","properties":{},"required":[]}, list_skills, "skill"),
        ToolDef("skill_review", "Review a skill",
                {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}, review_skill, "skill"),
        ToolDef("skill_delete", "Delete a skill",
                {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}, delete_skill, "skill"),
        ToolDef("skill_stats", "Get skill system stats",
                {"type":"object","properties":{},"required":[]}, skill_stats, "skill"),
    ])
