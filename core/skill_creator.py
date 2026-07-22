"""
P3-3: /learn 技能闭环 — Javis Skill Creator
AI 自主创建技能 -> 审查 -> 持久化 -> 复用
借鉴 Hermes /learn 闭环设计
"""
import os
import json
import hashlib
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from enum import Enum


class SkillStatus(Enum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"
    ACTIVE = "active"


@dataclass
class Skill:
    """技能定义"""
    name: str
    description: str
    category: str = "general"         # general / code / data / communication / automation
    system_prompt: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    examples: List[Dict] = field(default_factory=list)
    version: int = 1
    author: str = "javis-ai"
    status: SkillStatus = SkillStatus.DRAFT
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    usage_count: int = 0
    success_rate: float = 0.0
    metadata: Dict = field(default_factory=dict)

    @property
    def skill_id(self) -> str:
        name_hash = hashlib.sha256(self.name.encode()).hexdigest()[:8]
        return f"{self.category}/{self.name}-{name_hash}"

    def to_dict(self) -> Dict:
        return {
            "id": self.skill_id,
            "name": self.name, "description": self.description,
            "category": self.category, "version": self.version,
            "author": self.author, "status": self.status.value,
            "usage_count": self.usage_count, "success_rate": self.success_rate,
            "allowed_tools": self.allowed_tools,
            "example_count": len(self.examples),
            "created_at": time.strftime("%Y-%m-%d", time.localtime(self.created_at)),
        }

    def to_markdown(self) -> str:
        """生成 SKILL.md 格式"""
        md = f"""# {self.name}

**Category:** {self.category}
**Version:** {self.version}
**Author:** {self.author}
**Status:** {self.status.value}

## Description
{self.description}

## System Prompt
{self.system_prompt}

## Allowed Tools
{chr(10).join(f'- {t}' for t in self.allowed_tools) if self.allowed_tools else 'None'}

## Examples
"""
        for ex in self.examples:
            md += f"\n### {ex.get('title', 'Example')}\n"
            md += f"**User:** {ex.get('user', '')}\n\n"
            md += f"**Assistant:** {ex.get('assistant', '')}\n"
        return md


class SkillReviewer:
    """技能审查器 — 自动质量检查"""

    MIN_DESCRIPTION_LENGTH = 20
    MIN_SYSTEM_PROMPT_LENGTH = 50
    MAX_NAME_LENGTH = 64
    FORBIDDEN_TERMS = ["hack", "exploit", "malware", "phishing", "steal"]

    @classmethod
    def review(cls, skill: Skill) -> Dict[str, Any]:
        """审查一个技能, 返回审查结果"""
        issues = []
        warnings = []
        score = 100

        # 名称检查
        if len(skill.name) < 3:
            issues.append("Name too short (min 3 chars)")
            score -= 20
        if len(skill.name) > cls.MAX_NAME_LENGTH:
            issues.append(f"Name too long (max {cls.MAX_NAME_LENGTH} chars)")
            score -= 10
        if not skill.name.replace("-", "").replace("_", "").isalnum():
            issues.append("Name contains invalid characters")
            score -= 15

        # 描述检查
        if len(skill.description) < cls.MIN_DESCRIPTION_LENGTH:
            warnings.append(f"Description too short (min {cls.MIN_DESCRIPTION_LENGTH} chars)")
            score -= 10

        # 系统提示词检查
        if len(skill.system_prompt) < cls.MIN_SYSTEM_PROMPT_LENGTH:
            warnings.append(f"System prompt too short (min {cls.MIN_SYSTEM_PROMPT_LENGTH} chars)")
            score -= 15

        # 安全审查
        full_text = f"{skill.name} {skill.description} {skill.system_prompt}".lower()
        for term in cls.FORBIDDEN_TERMS:
            if term in full_text:
                issues.append(f"Contains forbidden term: '{term}'")
                score -= 50

        # 无工具检查
        if not skill.allowed_tools:
            warnings.append("No tools specified — skill may have limited functionality")

        # 决定状态
        if issues:
            status = SkillStatus.REJECTED
        elif warnings:
            status = SkillStatus.REVIEW
        else:
            status = SkillStatus.APPROVED

        return {
            "passed": len(issues) == 0,
            "status": status.value,
            "score": max(0, score),
            "issues": issues,
            "warnings": warnings,
        }


class SkillStore:
    """技能存储 — 持久化、索引、检索"""

    def __init__(self, skills_dir: str = ""):
        self.skills_dir = Path(skills_dir) if skills_dir else Path(__file__).parent.parent / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, Skill] = {}
        self._load_all()

    def _skill_path(self, name: str) -> Path:
        safe_name = name.replace("/", "-").replace("\\", "-")
        return self.skills_dir / f"{safe_name}.json"

    def _load_all(self):
        """加载所有已持久化的技能"""
        for fpath in self.skills_dir.glob("*.json"):
            try:
                data = json.loads(fpath.read_text(encoding='utf-8'))
                data["status"] = SkillStatus(data.get("status", "draft"))
                skill = Skill(**{k: v for k, v in data.items()
                               if k in Skill.__dataclass_fields__})
                self._index[skill.skill_id] = skill
            except Exception:
                pass

    def save(self, skill: Skill):
        """保存技能到磁盘"""
        data = {k: (v.value if isinstance(v, SkillStatus) else v)
                for k, v in skill.__dict__.items()
                if not k.startswith("_")}
        self._skill_path(skill.skill_id).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        self._index[skill.skill_id] = skill

    def get(self, skill_id: str) -> Optional[Skill]:
        return self._index.get(skill_id)

    def list_all(self, category: str = "", status: SkillStatus = None) -> List[Dict]:
        """列出所有技能"""
        skills = list(self._index.values())
        if category:
            skills = [s for s in skills if s.category == category]
        if status:
            skills = [s for s in skills if s.status == status]
        return [s.to_dict() for s in skills]

    def delete(self, skill_id: str) -> bool:
        """删除技能"""
        if skill_id in self._index:
            path = self._skill_path(skill_id)
            if path.exists():
                path.unlink()
            del self._index[skill_id]
            return True
        return False

    def search(self, query: str) -> List[Dict]:
        """模糊搜索技能"""
        query_lower = query.lower()
        results = []
        for skill in self._index.values():
            text = f"{skill.name} {skill.description} {skill.category}".lower()
            if query_lower in text:
                results.append(skill.to_dict())
        return results


class SkillCreator:
    """/learn 技能闭环 — AI 自主创建、审查、学习"""

    def __init__(self, store: SkillStore = None):
        self.store = store or SkillStore()
        self.reviewer = SkillReviewer()
        self._creation_history: List[Dict] = []
        self._feedback_cache: Dict[str, List[Dict]] = {}

    def create_skill(self, name: str, description: str,
                     system_prompt: str, category: str = "general",
                     allowed_tools: List[str] = None,
                     examples: List[Dict] = None) -> Dict:
        """创建新技能"""
        skill = Skill(
            name=name, description=description,
            system_prompt=system_prompt, category=category,
            allowed_tools=allowed_tools or [],
            examples=examples or [],
            status=SkillStatus.DRAFT
        )

        # 自动审查
        review = self.reviewer.review(skill)

        if review["passed"]:
            skill.status = SkillStatus.APPROVED
        elif review["status"] == SkillStatus.REVIEW:
            skill.status = SkillStatus.REVIEW
        else:
            skill.status = SkillStatus.REJECTED

        # 持久化
        self.store.save(skill)

        # 记录创建历史
        self._creation_history.append({
            "skill_id": skill.skill_id,
            "name": name, "timestamp": time.time(),
            "review": review
        })

        return {
            "success": review["passed"],
            "skill": skill.to_dict(),
            "review": review,
            "skill_id": skill.skill_id,
        }

    def learn_from_interaction(self, user_message: str, assistant_response: str,
                               success: bool, skill_category: str = "general") -> Optional[Skill]:
        """从交互中学习, 自动提取模式创建技能"""
        # 由 AI 引擎调用: 分析交互模式, 提取可复用的技能
        # 这是占位接口, 实际由 LLM 驱动的元认知循环调用
        return None

    def record_feedback(self, skill_id: str, feedback: str, rating: int):
        """记录技能使用反馈"""
        if skill_id not in self._feedback_cache:
            self._feedback_cache[skill_id] = []
        self._feedback_cache[skill_id].append({
            "feedback": feedback, "rating": rating, "timestamp": time.time()
        })

        # 更新技能成功率
        skill = self.store.get(skill_id)
        if skill:
            all_ratings = [f["rating"] for f in self._feedback_cache[skill_id]]
            skill.success_rate = sum(all_ratings) / len(all_ratings)
            skill.updated_at = time.time()
            self.store.save(skill)

    def deprecate_stale_skills(self, min_usage: int = 0,
                               max_idle_days: int = 30) -> List[str]:
        """废弃过时技能"""
        deprecated = []
        cutoff = time.time() - max_idle_days * 86400
        for skill in self.store._index.values():
            if skill.status == SkillStatus.ACTIVE:
                if skill.usage_count <= min_usage and skill.updated_at < cutoff:
                    skill.status = SkillStatus.DEPRECATED
                    self.store.save(skill)
                    deprecated.append(skill.skill_id)
        return deprecated

    def list_skills(self, category: str = "") -> List[Dict]:
        """便捷方法: 列出技能 (main.py 兼容)"""
        return self.store.list_all(category=category)

    def get_stats(self) -> Dict:
        """获取技能系统统计"""
        skills = list(self.store._index.values())
        return {
            "total_skills": len(skills),
            "by_status": {s.value: sum(1 for sk in skills if sk.status == s) for s in SkillStatus},
            "by_category": {},
            "recent_creations": self._creation_history[-5:] if self._creation_history else [],
            "avg_success_rate": sum(s.success_rate for s in skills) / len(skills) if skills else 0,
        }


# 全局单例
_creator: Optional[SkillCreator] = None


def get_skill_creator(skills_dir: str = "") -> SkillCreator:
    global _creator
    if _creator is None:
        store = SkillStore(skills_dir) if skills_dir else SkillStore()
        _creator = SkillCreator(store=store)
    return _creator


def register_in_manifest(reg):
    """Register skill creator tools"""
    from core.tool_registry import ToolDef
    sc = get_skill_creator()

    async def create_skill(args):
        result = sc.create_skill(
            name=args["name"],
            description=args["description"],
            system_prompt=args["system_prompt"],
            category=args.get("category", "general"),
            allowed_tools=args.get("allowed_tools", []),
            examples=args.get("examples", []),
        )
        return {"success": result["success"], **result}

    async def list_skills(args):
        category = args.get("category", "")
        status = args.get("status", "")
        status_enum = SkillStatus(status) if status else None
        skills = sc.store.list_all(category, status_enum)
        return {"success": True, "skills": skills, "count": len(skills)}

    async def search_skills(args):
        results = sc.store.search(args["query"])
        return {"success": True, "results": results, "count": len(results)}

    async def get_skill(args):
        skill = sc.store.get(args["skill_id"])
        if skill:
            return {"success": True, "skill": skill.to_dict()}
        return {"success": False, "error": "Skill not found"}

    async def delete_skill(args):
        ok = sc.store.delete(args["skill_id"])
        return {"success": ok}

    async def skill_feedback(args):
        sc.record_feedback(args["skill_id"], args.get("feedback", ""), args.get("rating", 3))
        return {"success": True}

    async def skill_stats(args):
        return {"success": True, **sc.get_stats()}

    async def deprecate_skills(args):
        deprecated = sc.deprecate_stale_skills(
            min_usage=args.get("min_usage", 0),
            max_idle_days=args.get("max_idle_days", 30)
        )
        return {"success": True, "deprecated": deprecated}

    reg.register_many([
        ToolDef("skill_create", "AI creates a new skill autonomously",
                {"type":"object","properties":{"name":{"type":"string"},"description":{"type":"string"},"system_prompt":{"type":"string"},"category":{"type":"string","default":"general"},"allowed_tools":{"type":"array","items":{"type":"string"}},"examples":{"type":"array"}},"required":["name","description","system_prompt"]}, create_skill, "skill"),
        ToolDef("skill_list", "List all learned skills",
                {"type":"object","properties":{"category":{"type":"string","default":""},"status":{"type":"string","default":""}},"required":[]}, list_skills, "skill"),
        ToolDef("skill_search", "Search skills by keyword",
                {"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}, search_skills, "skill"),
        ToolDef("skill_get", "Get skill details",
                {"type":"object","properties":{"skill_id":{"type":"string"}},"required":["skill_id"]}, get_skill, "skill"),
        ToolDef("skill_delete", "Delete a skill",
                {"type":"object","properties":{"skill_id":{"type":"string"}},"required":["skill_id"]}, delete_skill, "skill"),
        ToolDef("skill_feedback", "Record feedback on skill usage",
                {"type":"object","properties":{"skill_id":{"type":"string"},"feedback":{"type":"string","default":""},"rating":{"type":"integer","default":3}},"required":["skill_id"]}, skill_feedback, "skill"),
        ToolDef("skill_stats", "Get skill system statistics",
                {"type":"object","properties":{},"required":[]}, skill_stats, "skill"),
        ToolDef("skill_deprecate", "Deprecate stale skills",
                {"type":"object","properties":{"min_usage":{"type":"integer","default":0},"max_idle_days":{"type":"integer","default":30}},"required":[]}, deprecate_skills, "skill"),
    ])
