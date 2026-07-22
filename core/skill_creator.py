"""
P3-3: /learn 技能闭环 — AI自创技能 + background_review + Curator + 技能市场
"""
import os, logging, time, json, asyncio, shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum

logger = logging.getLogger("skill_creator")


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
        self._market_file.parent.mkdir(parents=True, exist_ok=True)
        if self._market_file.exists():
            try:
                self._market = json.loads(self._market_file.read_text())
            except Exception:
                self._market = {}

    def _save(self):
        self._market_file.write_text(json.dumps(
            self._market, indent=2, ensure_ascii=False
        ))

    def publish(self, skill: SkillDef) -> bool:
        """发布技能到市场"""
        self._market[skill.name] = skill.to_market_dict()
        self._save()
        logger.info(f"技能已发布到市场: {skill.name}")
        return True

    def unpublish(self, name: str) -> bool:
        """下架技能"""
        if name in self._market:
            del self._market[name]
            self._save()
            return True
        return False

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
        self._skills_dir.mkdir(exist_ok=True)
        self._reviewer = SkillReviewer()
        self._curator = SkillCurator(Path(skills_dir).parent if skills_dir else None)
        self._review_queue: asyncio.Queue = asyncio.Queue()

    def create(self, name: str, description: str, prompt: str,
              category: str = "general", tags: list = None,
              author: str = "Javis AI") -> str:
        """创建新技能文件 — /learn 命令触发"""
        # 检查名称冲突
        existing = self.list_skills()
        if name in existing:
            logger.warning(f"技能已存在: {name}")
            return str(self._skills_dir / f"{name}.py")

        filename = f"{name}.py"
        path = self._skills_dir / filename

        content = f'''"""
{description}

Category: {category}
Author: {author}
Version: 1.0.0
Tags: {", ".join(tags or [])}
"""

# 技能 Prompt
SKILL_PROMPT = """{prompt}"""


def register(tools):
    """技能注册入口 — 加载时被 Javis 调用"""
    pass


# 技能元数据
SKILL_META = {{
    "name": "{name}",
    "description": "{description}",
    "category": "{category}",
    "author": "{author}",
    "version": "1.0.0",
    "tags": {json.dumps(tags or [])},
}}
'''
        path.write_text(content, encoding="utf-8")
        logger.info(f"技能已创建: {name} → {path}")

        # 加入审查队列
        skill = SkillDef(
            name=name, description=description,
            system_prompt=prompt, category=category,
            tags=tags or [], author=author,
        )
        asyncio.create_task(self._background_review(skill))

        return str(path)

    async def _background_review(self, skill: SkillDef):
        """后台审查技能"""
        await asyncio.sleep(1)  # 短暂延迟

        existing = set(self.list_skills())
        result = self._reviewer.review(skill, existing)

        if result["passed"]:
            skill.status = SkillStatus.ACTIVE.value
            self._curator.publish(skill)
            logger.info(f"技能审查通过: {skill.name} (score={result['score']})")
        elif result["recommendation"] == "needs_improvement":
            skill.status = SkillStatus.REVIEWING.value
            skill.review_notes = f"需要改进: {result['score']}分"
            logger.info(f"技能待改进: {skill.name} (score={result['score']})")
        else:
            skill.status = SkillStatus.REJECTED.value
            skill.review_notes = f"未通过审查: {result['score']}分"
            logger.info(f"技能被拒绝: {skill.name} (score={result['score']})")

        # 保存审查结果
        self._save_review_result(skill, result)

    def _save_review_result(self, skill: SkillDef, result: dict):
        """保存审查结果"""
        review_dir = self._skills_dir.parent / "data" / "skills" / "reviews"
        review_dir.mkdir(parents=True, exist_ok=True)
        review_file = review_dir / f"{skill.name}.json"
        review_file.write_text(json.dumps({
            "skill": skill.to_dict(),
            "review": result,
            "reviewed_at": time.time(),
        }, indent=2, ensure_ascii=False))

    def review(self, name: str) -> dict:
        """手动审查技能"""
        path = self._skills_dir / f"{name}.py"
        if not path.exists():
            return {"status": "missing", "name": name}

        content = path.read_text()

        lines = len(content.splitlines())
        has_prompt = "SKILL_PROMPT" in content
        has_meta = "SKILL_META" in content
        has_register = "def register" in content
        size = len(content)

        # 提取 prompt 长度
        prompt_start = content.find('SKILL_PROMPT = """')
        prompt_len = 0
        if prompt_start >= 0:
            prompt_content = content[prompt_start:]
            prompt_len = len(prompt_content)

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
               new_description: str = "") -> bool:
        """改进已存在的技能"""
        path = self._skills_dir / f"{name}.py"
        if not path.exists():
            return False

        content = path.read_text()

        if new_prompt:
            # 替换 SKILL_PROMPT
            import re
            content = re.sub(
                r'SKILL_PROMPT = """[\s\S]*?"""',
                f'SKILL_PROMPT = """{new_prompt}"""',
                content,
            )

        if new_description:
            # 替换 docstring 中的描述
            content = re.sub(
                r'^"""[\s\S]*?"""',
                f'"""{new_description}"""',
                content, count=1,
            )

        path.write_text(content, encoding="utf-8")
        logger.info(f"技能已改进: {name}")
        return True

    def list_skills(self) -> list[str]:
        """列出所有技能名称"""
        return sorted([
            f.stem for f in self._skills_dir.glob("*.py")
            if f.name != "__init__.py"
        ])

    def delete_skill(self, name: str) -> bool:
        """删除技能"""
        path = self._skills_dir / f"{name}.py"
        if path.exists():
            path.unlink()
            self._curator.unpublish(name)
            logger.info(f"技能已删除: {name}")
            return True
        return False

    def export_skill(self, name: str, target_dir: str) -> Optional[str]:
        """导出技能到目录"""
        path = self._skills_dir / f"{name}.py"
        if not path.exists():
            return None

        target = Path(target_dir) / f"{name}.py"
        shutil.copy2(str(path), str(target))
        return str(target)

    def import_skill(self, source_path: str) -> Optional[str]:
        """导入技能文件"""
        source = Path(source_path)
        if not source.exists():
            return None

        target = self._skills_dir / source.name
        shutil.copy2(str(source), str(target))
        return str(target)

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

    async def create_skill(args):
        path = sc.create(
            name=args["name"],
            description=args.get("description", ""),
            prompt=args.get("system_prompt", ""),
            category=args.get("category", "general"),
            tags=args.get("tags", []),
            author=args.get("author", "Javis AI"),
        )
        return {"success": True, "path": path, "name": args["name"]}

    async def list_skills(args):
        skills = sc.list_skills()
        return {"success": True, "skills": skills, "count": len(skills)}

    async def review_skill(args):
        result = sc.review(args["name"])
        return {"success": True, **result}

    async def improve_skill(args):
        ok = sc.improve(
            name=args["name"],
            new_prompt=args.get("system_prompt", ""),
            new_description=args.get("description", ""),
        )
        return {"success": ok, "name": args["name"]}

    async def delete_skill(args):
        ok = sc.delete_skill(args["name"])
        return {"success": ok, "name": args["name"]}

    async def skill_stats(args):
        return {"success": True, **sc.get_stats()}

    async def market_search(args):
        results = sc.market_search(
            query=args.get("query", ""),
            category=args.get("category", ""),
            tags=args.get("tags", []),
        )
        return {"success": True, "results": results, "count": len(results)}

    async def market_top(args):
        limit = args.get("limit", 10)
        results = sc.market_top(limit)
        return {"success": True, "results": results, "count": len(results)}

    async def export_skill_tool(args):
        path = sc.export_skill(args["name"], args.get("target_dir", "."))
        if path:
            return {"success": True, "path": path}
        return {"success": False, "error": f"Skill not found: {args['name']}"}

    async def import_skill_tool(args):
        path = sc.import_skill(args["source_path"])
        if path:
            return {"success": True, "path": path}
        return {"success": False, "error": "Import failed"}

    reg.register_many([
        ToolDef("skill_create", "创建新技能 (/learn 命令)",
                {"type":"object","properties":{
                    "name":{"type":"string"},
                    "description":{"type":"string","default":""},
                    "system_prompt":{"type":"string","default":""},
                    "category":{"type":"string","default":"general"},
                    "tags":{"type":"array","items":{"type":"string"},"default":[]},
                    "author":{"type":"string","default":"Javis AI"},
                },"required":["name"]},
                create_skill, "skill"),
        ToolDef("skill_list", "列出所有已学习技能",
                {"type":"object","properties":{},"required":[]},
                list_skills, "skill"),
        ToolDef("skill_review", "审查技能质量",
                {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]},
                review_skill, "skill"),
        ToolDef("skill_improve", "改进已有技能",
                {"type":"object","properties":{
                    "name":{"type":"string"},
                    "system_prompt":{"type":"string","default":""},
                    "description":{"type":"string","default":""},
                },"required":["name"]},
                improve_skill, "skill"),
        ToolDef("skill_delete", "删除技能",
                {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]},
                delete_skill, "skill"),
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
        ToolDef("skill_export", "导出技能文件",
                {"type":"object","properties":{
                    "name":{"type":"string"},
                    "target_dir":{"type":"string","default":"."},
                },"required":["name"]},
                export_skill_tool, "skill"),
        ToolDef("skill_import", "导入技能文件",
                {"type":"object","properties":{"source_path":{"type":"string"}},"required":["source_path"]},
                import_skill_tool, "skill"),
    ])
