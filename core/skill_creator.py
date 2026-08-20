"""
P3-3: /learn 技能闭环 — AI自创技能 + background_review + Curator + 技能市场
"""
import ast, json, logging, re, time
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum

logger = logging.getLogger("skill_creator")
_SAFE_SKILL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


def _governance_disabled(action: str) -> dict:
    return {
        "success": False,
        "code": "legacy_governance_disabled",
        "error": "legacy_governance_disabled",
        "action": action,
    }


class SkillStatus(str, Enum):
    DRAFT = "draft"
    REVIEWING = "reviewing"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


@dataclass
class SkillDef:
    name: str
    description: str
    system_prompt: str = ""
    category: str = "general"
    status: str = "active"
    version: str = "1.0.0"
    author: str = "Javis AI"
    tags: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    usage_count: int = 0
    rating: float = 0.0
    review_notes: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "description": self.description,
            "category": self.category, "status": self.status,
            "version": self.version, "author": self.author,
            "tags": self.tags, "dependencies": self.dependencies,
            "created_at": self.created_at, "usage_count": self.usage_count,
            "rating": self.rating,
        }

    def to_market_dict(self) -> Dict:
        """技能市场条目"""
        return {
            "name": self.name, "description": self.description,
            "category": self.category, "version": self.version,
            "author": self.author, "tags": self.tags,
            "usage_count": self.usage_count, "rating": self.rating,
            "status": self.status,
        }


class SkillReviewer:
    """技能审查器 — background_review"""

    QUALITY_CHECKS = [
        ("has_description", "必须有描述", lambda s: len(s.description) > 10),
        ("has_prompt", "必须有 system_prompt", lambda s: len(s.system_prompt) > 20),
        ("has_category", "必须有分类", lambda s: s.category != "general" or len(s.tags) > 0),
        ("unique_name", "名称不能冲突", lambda s: True),  # 由调用方检查
        ("prompt_length", "system_prompt 至少 100 字符", lambda s: len(s.system_prompt) >= 100),
        ("description_length", "描述至少 20 字符", lambda s: len(s.description) >= 20),
    ]

    @classmethod
    def review(cls, skill: SkillDef, existing_names: set = None) -> dict:
        """审查技能质量"""
        results = []
        passed = 0

        for check_id, check_desc, check_fn in cls.QUALITY_CHECKS:
            ok = check_fn(skill)
            if ok:
                passed += 1
            results.append({"check": check_id, "desc": check_desc, "passed": ok})

        # 名称冲突检查
        name_ok = True
        if existing_names and skill.name in existing_names:
            name_ok = False
            results.append({"check": "unique_name", "desc": "名称冲突", "passed": False})

        score = passed / len(cls.QUALITY_CHECKS)
        return {
            "passed": score >= 0.7,
            "score": round(score * 100),
            "checks": results,
            "recommendation": "approved" if score >= 0.8 else (
                "needs_improvement" if score >= 0.5 else "rejected"
            ),
        }


class SkillCurator:
    """技能策展器 — 管理技能市场"""

    MARKET_FILE = "data/skills/market.json"

    def __init__(self, base_dir: Path = None):
        self._base_dir = base_dir or Path(__file__).parent.parent
        self._market_file = self._base_dir / self.MARKET_FILE
        self._market: dict[str, dict] = {}
        self._load()

    def _load(self):
        if self._market_file.exists():
            try:
                self._market = json.loads(self._market_file.read_text())
            except Exception:
                self._market = {}

    def publish(self, skill: SkillDef) -> dict:
        """发布技能到市场"""
        return _governance_disabled("publish")

    def unpublish(self, name: str) -> dict:
        """下架技能"""
        return _governance_disabled("unpublish")

    def search(self, query: str = "", category: str = "",
              tags: List[str] = None) -> list[dict]:
        """搜索技能市场"""
        results = list(self._market.values())

        if query:
            q = query.lower()
            results = [s for s in results
                      if q in s.get("name", "").lower()
                      or q in s.get("description", "").lower()
                      or any(q in t.lower() for t in s.get("tags", []))]

        if category:
            results = [s for s in results if s.get("category") == category]

        if tags:
            results = [s for s in results
                      if any(t in s.get("tags", []) for t in tags)]

        return sorted(results, key=lambda s: s.get("usage_count", 0), reverse=True)

    def get_top(self, limit: int = 10) -> list[dict]:
        """热门技能"""
        return self.search()[:limit]

    def get_categories(self) -> list[str]:
        """所有分类"""
        cats = set(s.get("category", "general") for s in self._market.values())
        return sorted(cats)

    def get_stats(self) -> dict:
        return {
            "total": len(self._market),
            "categories": len(self.get_categories()),
            "total_usage": sum(s.get("usage_count", 0) for s in self._market.values()),
        }


class SkillCreator:
    """技能创建器 — /learn 命令 → 审查 → 策展 → 市场"""

    def __init__(self, skills_dir: str = None):
        self._skills_dir = Path(skills_dir) if skills_dir else (
            Path(__file__).parent.parent / "skills"
        )
        self._reviewer = SkillReviewer()
        self._curator = SkillCurator(Path(skills_dir).parent if skills_dir else None)

    @staticmethod
    def _validate_name(name: str) -> str:
        safe_name = (name or "").strip()
        if not _SAFE_SKILL_NAME_RE.fullmatch(safe_name):
            raise ValueError("技能名只能包含英文字母、数字、下划线或连字符，并且不能包含路径")
        return safe_name

    def create(self, name: str, description: str, prompt: str,
              category: str = "general", tags: list = None,
              author: str = "Javis AI") -> dict:
        """创建新技能文件 — /learn 命令触发"""
        return self._legacy_disabled("create")

    @staticmethod
    def _legacy_disabled(action: str) -> dict:
        return _governance_disabled(action)

    def review(self, name: str) -> dict:
        """手动审查技能"""
        try:
            name = self._validate_name(name)
        except ValueError:
            return {"status": "invalid", "name": name}
        path = self._skills_dir / f"{name}.py"
        if not path.exists():
            return {"status": "missing", "name": name}

        content = path.read_text(encoding="utf-8")

        lines = len(content.splitlines())
        has_prompt = "SKILL_PROMPT" in content
        has_meta = "SKILL_META" in content
        has_register = "def register" in content
        size = len(content)

        prompt_len = 0
        prompt_match = re.search(r"^SKILL_PROMPT\s*=\s*(.+)$", content, flags=re.MULTILINE)
        if prompt_match:
            try:
                prompt_len = len(ast.literal_eval(prompt_match.group(1)))
            except (SyntaxError, ValueError):
                prompt_len = len(prompt_match.group(1))

        checks = [
            {"check": "has_prompt", "passed": has_prompt},
            {"check": "has_meta", "passed": has_meta},
            {"check": "has_register", "passed": has_register},
            {"check": "prompt_min_100", "passed": prompt_len >= 100},
            {"check": "min_lines", "passed": lines >= 10},
        ]

        passed = sum(1 for c in checks if c["passed"])
        score = int(passed / len(checks) * 100)

        return {
            "status": "active" if score >= 60 else "needs_improvement",
            "name": name,
            "lines": lines,
            "size": size,
            "score": score,
            "checks": checks,
            "recommendation": "approved" if score >= 80 else (
                "needs_improvement" if score >= 40 else "rejected"
            ),
        }

    def improve(self, name: str, new_prompt: str = "",
               new_description: str = "") -> dict:
        """改进已存在的技能"""
        return self._legacy_disabled("improve")

    def list_skills(self) -> list[str]:
        """列出所有技能名称"""
        return sorted([
            f.stem for f in self._skills_dir.glob("*.py")
            if f.name != "__init__.py"
        ])

    def delete_skill(self, name: str) -> dict:
        """删除技能"""
        return self._legacy_disabled("delete")

    def export_skill(self, name: str, target_dir: str) -> dict:
        """导出技能到目录"""
        return self._legacy_disabled("export")

    def import_skill(self, source_path: str) -> dict:
        """导入技能文件"""
        return self._legacy_disabled("import")

    def get_stats(self) -> dict:
        """技能统计"""
        names = self.list_skills()
        market_stats = self._curator.get_stats()
        return {
            "total": len(names),
            "skills": names,
            "market": market_stats,
            "categories": self._curator.get_categories(),
        }

    def market_search(self, query: str = "", category: str = "",
                     tags: list = None) -> list[dict]:
        """搜索技能市场"""
        return self._curator.search(query, category, tags)

    def market_top(self, limit: int = 10) -> list[dict]:
        """热门技能"""
        return self._curator.get_top(limit)


# 全局单例
_creator: Optional[SkillCreator] = None


def get_creator(skills_dir: str = None) -> SkillCreator:
    global _creator
    if _creator is None:
        _creator = SkillCreator(skills_dir)
    return _creator


def get_skill_creator(skills_dir: str = "") -> SkillCreator:
    """main.py 兼容接口"""
    return get_creator(skills_dir if skills_dir else None)


# ── 注册到 manifest ──

def register_in_manifest(reg):
    """注册技能创建工具到 manifest"""
    from core.tool_registry import ToolDef
    sc = get_creator()

    async def list_skills():
        skills = sc.list_skills()
        return {"success": True, "skills": skills, "count": len(skills)}

    async def review_skill(name: str):
        result = sc.review(name)
        return {"success": True, **result}

    async def skill_stats():
        return {"success": True, **sc.get_stats()}

    async def market_search(query: str = "", category: str = "", tags: list | None = None):
        results = sc.market_search(
            query=query,
            category=category,
            tags=tags or [],
        )
        return {"success": True, "results": results, "count": len(results)}

    async def market_top(limit: int = 10):
        results = sc.market_top(limit)
        return {"success": True, "results": results, "count": len(results)}

    reg.register_many([
        ToolDef("skill_list", "列出所有已学习技能",
                {"type":"object","properties":{},"required":[]},
                list_skills, "skill"),
        ToolDef("skill_review", "审查技能质量",
                {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]},
                review_skill, "skill"),
        ToolDef("skill_stats", "技能系统统计",
                {"type":"object","properties":{},"required":[]},
                skill_stats, "skill"),
        ToolDef("skill_market_search", "搜索技能市场",
                {"type":"object","properties":{
                    "query":{"type":"string","default":""},
                    "category":{"type":"string","default":""},
                    "tags":{"type":"array","items":{"type":"string"},"default":[]},
                },"required":[]},
                market_search, "skill"),
        ToolDef("skill_market_top", "热门技能排行",
                {"type":"object","properties":{"limit":{"type":"integer","default":10}},"required":[]},
                market_top, "skill"),
    ])
