# -*- coding: utf-8 -*-
"""情景记忆 — 每次对话被视为一个完整的 Episode

数据结构:
  episode = {
    id, session_id, fingerprint (domain, task, tools),
    timeline [{step, tool, result, latency, error}],
    outcome (success/failure/partial), duration, user_feedback,
    created_at
  }

存储: brain_data/episodes/{id}.json
"""
import json, os, time, logging, hashlib, re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("memory.episodic")

EPISODES_DIR = Path(__file__).parent.parent / "brain_data" / "episodes"
MAX_EPISODES = 500


# ── 指纹提取 ──

def extract_fingerprint(user_input: str, tools_involved: list[str] = None) -> dict:
    """从用户输入和工具调用中提取场景指纹"""
    t = user_input.lower()
    fp = {"domain": "general", "platform": "", "task_type": "general", "tools_involved": tools_involved or []}

    # 领域判断
    domains = {
        "file_ops": ["文件", "文件夹", "目录", "pdf", "word", "excel", "文档", "格式", "转换", "file", "readme"],
        "code_hosting": ["github", "gitlab", "仓库", "repo", "代码", "开源", "clone", "push", "pull", "git"],
        "system": ["系统", "cpu", "内存", "磁盘", "进程", "任务管理器", "设置", "配置"],
        "web": ["网页", "浏览器", "搜索", "上网", "下载", "url", "http", "网站"],
        "desktop": ["截图", "鼠标", "键盘", "窗口", "桌面", "应用", "打开"],
        "code": ["写代码", "编程", "python", "c++", "rust", "脚本", "编译"],
        "communication": ["邮件", "微信", "钉钉", "消息", "通知", "聊天"],
    }
    for domain, keywords in domains.items():
        if any(k in t for k in keywords):
            fp["domain"] = domain
            break

    # 平台判断
    platforms = {
        "github": ["github"],
        "gitlab": ["gitlab"],
        "windows": ["windows", "win"],
        "linux": ["linux", "ubuntu", "debian"],
        "ollama": ["ollama", "本地模型"],
    }
    for plat, keywords in platforms.items():
        if any(k in t for k in keywords):
            fp["platform"] = plat
            break

    # 任务类型
    task_types = {
        "tool_search": ["找工具", "搜索", "查找", "推荐", "有没有", "什么好"],
        "convert": ["转换", "转word", "转pdf", "转excel", "格式"],
        "debug": ["bug", "报错", "错误", "失败", "出问题", "崩溃"],
        "explore": ["看看", "浏览", "查看", "检查", "状态", "信息"],
        "install": ["安装", "下载", "配置", "部署"],
    }
    for tt, keywords in task_types.items():
        if any(k in t for k in keywords):
            fp["task_type"] = tt
            break

    return fp


class Episode:
    """单次会话的情景记录"""

    def __init__(self, user_input: str = "", session_id: str = ""):
        now = time.time()
        self.id = f"ep_{int(now)}_{hashlib.md5(user_input.encode() if user_input else str(now).encode()).hexdigest()[:6]}"
        self.session_id = session_id
        self.user_input = user_input[:200]
        self.fingerprint = extract_fingerprint(user_input)
        self.timeline: list[dict] = []
        self.outcome = "unknown"  # success / failure / partial
        self.duration_ms = 0
        self.start_time = now
        self.end_time = None
        self.user_feedback = ""
        self.tool_count = 0
        self.failure_count = 0

    def record_tool_call(self, tool: str, params: dict, result: str, error: str = "", latency_ms: int = 0):
        """记录一次工具调用到时间线"""
        step = len(self.timeline) + 1
        entry = {"step": step, "tool": tool, "result": result, "error": error[:200], "latency_ms": latency_ms}
        if error:
            # 提取错误码
            error_codes = {
                "ENCODING": ["编码", "gbk", "utf", "decode", "encode", "charset"],
                "TIMEOUT": ["超时", "timeout", "timed"],
                "NOT_FOUND": ["不存在", "not found", "找不到", "404"],
                "PERMISSION": ["权限", "denied", "拒绝", "沙箱"],
                "PARAM": ["参数", "required", "missing", "validation"],
                "DEPENDENCY": ["模块", "import", "not installed", "安装"],
                "API": ["api", "connection", "网络", "auth"],
            }
            entry["error_code"] = "UNKNOWN"
            for ec, keywords in error_codes.items():
                if any(k in error.lower() for k in keywords):
                    entry["error_code"] = ec
                    break
        self.timeline.append(entry)
        self.tool_count += 1
        if result == "failure":
            self.failure_count += 1

    def finish(self, outcome: str = "", feedback: str = ""):
        """结束 episode 并写入磁盘"""
        self.end_time = time.time()
        self.duration_ms = int((self.end_time - self.start_time) * 1000)
        self.outcome = outcome or ("success" if self.failure_count == 0 else "failure" if self.tool_count == self.failure_count else "partial")
        self.user_feedback = feedback[:200]
        self._save()

    def _save(self):
        EPISODES_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "id": self.id, "session_id": self.session_id, "user_input": self.user_input,
            "fingerprint": self.fingerprint, "timeline": self.timeline,
            "outcome": self.outcome, "duration_ms": self.duration_ms,
            "tool_count": self.tool_count, "failure_count": self.failure_count,
            "start_time": self.start_time, "end_time": self.end_time, "user_feedback": self.user_feedback,
        }
        (EPISODES_DIR / f"{self.id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"💾 情景已存档: {self.id} ({self.outcome}, {self.tool_count}工具, {self.failure_count}失败)")

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


# ── 检索 ──

def load_episode(ep_id: str) -> Optional[dict]:
    p = EPISODES_DIR / f"{ep_id}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def list_episodes(limit: int = 20) -> list[dict]:
    files = sorted(EPISODES_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    result = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result.append({
                "id": data.get("id", f.stem), "outcome": data.get("outcome", "?"),
                "fingerprint": data.get("fingerprint", {}), "duration_ms": data.get("duration_ms", 0),
                "tool_count": data.get("tool_count", 0), "failure_count": data.get("failure_count", 0),
                "start_time": data.get("start_time", 0),
            })
        except:
            pass
    return result


def find_episodes_by_fingerprint(fp: dict, min_match: int = 2) -> list[dict]:
    """按指纹匹配查找历史 episodes"""
    matches = []
    for f in EPISODES_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            efp = data.get("fingerprint", {})
            score = 0
            for key in ["domain", "platform", "task_type"]:
                if fp.get(key) and fp.get(key) == efp.get(key):
                    score += 1
            tools_fp = set(fp.get("tools_involved", []))
            tools_ep = set(efp.get("tools_involved", []))
            if tools_fp and tools_ep:
                common = tools_fp & tools_ep
                score += len(common) * 2
            if score >= min_match:
                matches.append(data)
        except:
            pass
    matches.sort(key=lambda x: x.get("start_time", 0), reverse=True)
    return matches[:20]
