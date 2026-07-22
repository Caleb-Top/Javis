"""
/learn 技能闭环 — AI自创技能 + background_review + Curator
"""
import os, logging, time
from pathlib import Path

logger = logging.getLogger("skill_creator")

SKILLS_DIR = Path(__file__).parent.parent / "skills"

class SkillCreator:
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
        return sorted(
            [f.stem for f in SKILLS_DIR.glob("*.py") if f.name != "__init__.py"]
        )

# 单例
_creator: SkillCreator | None = None

def get_creator() -> SkillCreator:
    global _creator
    if _creator is None:
        _creator = SkillCreator()
    return _creator
