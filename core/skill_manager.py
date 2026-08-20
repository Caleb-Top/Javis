"""
技能生命周期管理器 — install/enable/disable/uninstall/search
P3-3 补充: 独立于 skill_creator 的生命周期管理
"""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger('skill_manager')
SKILLS_DIR = Path(__file__).parent.parent / 'skills'

class SkillManager:
    def __init__(self, skills_dir=None):
        self._skills_dir = Path(skills_dir) if skills_dir else SKILLS_DIR
        self._status: dict[str, str] = {}
        self._refresh()

    def _refresh(self):
        self._status.clear()

    @staticmethod
    def _disabled(action: str) -> dict:
        return {
            'success': False,
            'code': 'legacy_governance_disabled',
            'error': 'legacy_governance_disabled',
            'action': action,
        }

    def install(self, name: str, source_path: str) -> dict:
        return self._disabled('install')

    def enable(self, name: str) -> dict:
        return self._disabled('enable')

    def disable(self, name: str) -> dict:
        return self._disabled('disable')

    def uninstall(self, name: str, delete_file: bool = True) -> dict:
        return self._disabled('uninstall')

    def list_installed(self) -> list[dict]:
        return [{'name': n, 'status': s} for n, s in self._status.items()]

    def list_all(self) -> list[dict]:
        return self.list_installed()

    def get_status(self, name: str) -> Optional[dict]:
        s = self._status.get(name)
        return {'name': name, 'status': s} if s else None

    def count(self) -> dict:
        act = sum(1 for s in self._status.values() if s == 'active')
        dis = len(self._status) - act
        return {'total': len(self._status), 'active': act, 'disabled': dis}

_manager: Optional[SkillManager] = None

def get_manager() -> SkillManager:
    global _manager
    if _manager is None:
        _manager = SkillManager()
    return _manager

def get_skill_manager() -> SkillManager:
    return get_manager()

def register_in_manifest(reg):
    from core.tool_registry import ToolDef
    mgr = get_manager()

    async def _list():
        return {'success': True, 'skills': mgr.list_installed(), **mgr.count()}
    async def _status(name: str):
        info = mgr.get_status(name)
        if info is None:
            return {'success': False, 'error': f'Skill not found: {name}'}
        return {'success': True, **info}

    reg.register_many([
        ToolDef('skill_list_installed', 'List all installed skills with status',
                {'type': 'object', 'properties': {}, 'required': []}, _list, 'skill'),
        ToolDef('skill_status', 'Query single skill install status',
                {'type': 'object', 'properties': {
                    'name': {'type': 'string'}
                }, 'required': ['name']}, _status, 'skill'),
    ])
