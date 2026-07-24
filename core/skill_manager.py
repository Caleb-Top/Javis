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
        if self._skills_dir.exists():
            for f in self._skills_dir.glob('*.py'):
                n = f.stem
                if not n.startswith('_'):
                    self._status[n] = 'active'

    def install(self, name: str, source_path: str) -> dict:
        if name in self._status:
            return {'success': False, 'error': f'{name} already installed'}
        import shutil
        src = Path(source_path)
        if not src.exists():
            return {'success': False, 'error': f'Source not found: {source_path}'}
        dest = self._skills_dir / src.name
        shutil.copy2(str(src), str(dest))
        self._status[name] = 'active'
        logger.info(f'Skill installed: {name}')
        return {'success': True, 'action': 'install', 'name': name, 'path': str(dest)}

    def enable(self, name: str) -> dict:
        if name not in self._status:
            return {'success': False, 'error': f'Skill not found: {name}'}
        self._status[name] = 'active'
        logger.info(f'Skill enabled: {name}')
        return {'success': True, 'action': 'enable', 'name': name}

    def disable(self, name: str) -> dict:
        if name not in self._status:
            return {'success': False, 'error': f'Skill not found: {name}'}
        self._status[name] = 'disabled'
        logger.info(f'Skill disabled: {name}')
        return {'success': True, 'action': 'disable', 'name': name}

    def uninstall(self, name: str, delete_file: bool = True) -> dict:
        if name not in self._status:
            return {'success': False, 'error': f'Skill not found: {name}'}
        if delete_file:
            for f in self._skills_dir.glob(f'{name}*.py'):
                f.unlink()
        del self._status[name]
        logger.info(f'Skill uninstalled: {name}')
        return {'success': True, 'action': 'uninstall', 'name': name}

    def list_installed(self) -> list[dict]:
        return [{'name': n, 'status': s} for n, s in self._status.items()]

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

def register_in_manifest(reg):
    from core.tool_registry import ToolDef
    mgr = get_manager()

    async def _install(args):
        return mgr.install(args['name'], args['source_path'])
    async def _enable(args):
        return mgr.enable(args['name'])
    async def _disable(args):
        return mgr.disable(args['name'])
    async def _uninstall(args):
        return mgr.uninstall(args['name'], args.get('delete_file', True))
    async def _list(args):
        return {'success': True, 'skills': mgr.list_installed(), **mgr.count()}
    async def _status(args):
        info = mgr.get_status(args['name'])
        if info is None:
            return {'success': False, 'error': f'Skill not found: {args["name"]}'}
        return {'success': True, **info}

    reg.register_many([
        ToolDef('skill_install', 'Install skill from file path',
                {'type': 'object', 'properties': {
                    'name': {'type': 'string'},
                    'source_path': {'type': 'string'}
                }, 'required': ['name', 'source_path']}, _install, 'skill'),
        ToolDef('skill_enable', 'Enable a disabled skill',
                {'type': 'object', 'properties': {
                    'name': {'type': 'string'}
                }, 'required': ['name']}, _enable, 'skill'),
        ToolDef('skill_disable', 'Disable skill (keep file, stop loading)',
                {'type': 'object', 'properties': {
                    'name': {'type': 'string'}
                }, 'required': ['name']}, _disable, 'skill'),
        ToolDef('skill_uninstall', 'Uninstall skill (optional file deletion)',
                {'type': 'object', 'properties': {
                    'name': {'type': 'string'},
                    'delete_file': {'type': 'boolean', 'default': True}
                }, 'required': ['name']}, _uninstall, 'skill'),
        ToolDef('skill_list_installed', 'List all installed skills with status',
                {'type': 'object', 'properties': {}, 'required': []}, _list, 'skill'),
        ToolDef('skill_status', 'Query single skill install status',
                {'type': 'object', 'properties': {
                    'name': {'type': 'string'}
                }, 'required': ['name']}, _status, 'skill'),
    ])
