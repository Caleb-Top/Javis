"""JAVIS Agent v3 — Phase-driven + Dynamic Prompt + Auto-Learning"""

import asyncio, json, logging, time, hashlib, uuid
from typing import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from core.llm_client import LLMClient, LLMResponse
from core.tool_registry import ToolRegistry
from core.tool_result import ToolResult
from core.planner import Planner
from core.reflector import Reflector, classify_error, map_tool_to_domain

logger = logging.getLogger("agent")
action_log = []

DANGEROUS_TOOLS = {
    "file_write": ("写入文件，可能覆盖已有数据", "dangerous"),
    "file_delete": ("删除文件或目录", "dangerous"),
    "run_code": ("执行Python代码，可能影响系统", "critical"),
    "execute_command": ("执行系统命令，可能影响系统", "critical"),
}

# 安全工具 — 读取类、不影响系统状态
SAFE_TOOLS = {
    "screenshot", "mouse_click", "keyboard_type", "keyboard_press",
    "get_window_state", "list_windows", "system_info", "file_list",
    "file_read", "camera_snapshot", "set_volume", "find_app",
    "focus_window", "open_app", "end_turn",
}

# ── 优化: 轻量请求 — 快速工具直接跳过规划阶段 ──
QUICK_TOOLS = {
    "system_info", "file_list", "file_read", "brain_status",
    "memory_status", "github_search", "find_app", "list_windows",
    "screenshot", "camera_snapshot", "open_app", "focus_window",
    "camera_list", "set_volume",
}

def _is_quick_request(user_input: str) -> bool:
    """判断是否是轻量请求 — 不需要规划"""
    quick_keywords = [
        "硬盘", "磁盘", "cpu", "内存", "显卡", "gpu", "空间",
        "github", "git push", "git pull", "git status", "git clone",
        "状态", "版本", "谁", "版本号", "今天", "时间", "日期",
        "打开", "截图", "截屏", "心跳", "报告",
    ]
    text = user_input.lower()
    return any(kw in text for kw in quick_keywords)

def _log(t, d):
    action_log.append({"time": time.strftime("%H:%M:%S"), "type": t, "detail": d})
    if len(action_log) > 500: action_log.pop(0)
    logger.info(f"[LOG] {t}: {d[:100]}")

# ── 导入权限级别 ──
try:
    from utils.config_api import get_permission_level, PERMISSION_LEVELS as PERM_LEVELS
    _PERM_LEVELS_AVAILABLE = True
except Exception:
    _PERM_LEVELS_AVAILABLE = False
BASE_SYSTEM_PROMPT = """你是 Javis — 一个真正能思考、能编程、能控制电脑的 AI 智能体。

## 你的说话方式（比功能更重要）

你的风格模板参考了 Claude Fable 5（你的搭档）——自然、不装、有温度。

**说人话，别念报告。**
- 不要上来就抛 "CPU:10.9% 内存:60.6%" 这种冷冰冰的数据行
- 先来一句自然的结论："还行，挺轻快的，CPU才跑了10%"
- 数据是支撑，不是开头

**交代背景，不要只扔结论。**
- 坏："✅ system_info — CPU:10.9% 内存:60.6%"
- 好："看了一眼系统，资源吃得不重，CPU 才 10%，内存占了六成，磁盘还有将近三分之二的空间。"

**你是助手，不是说明书。**
- 用"咱"、"我们"拉近距离没关系
- 复杂操作前简单说一句要干什么："我先看看你的文件结构"
- 做完后说一句结果，不用反复确认"要继续吗"

**语感上：**
- 短句优先，偶尔来点语气词（嗯、对了、不过）
- 不用每句话都感叹号
- 不用每段都加 emoji
- 数据和结论之间自然过渡，不要让用户感觉在读 log
- 遇到问题直说，不用铺垫
- 绝对不要复读工具返回的原始数据。工具返回了 "CPU:10% 内存:61%" 你看到了就行，换成你自己的话说

## 你的核心能力
当用户提出需求时, 按阶段推进:
1. 规划: 分析需求, 输出完整计划
2. 执行: 用 run_code 或工具逐步执行
3. 验证: 检查结果, 如果不对则重试

## run_code 是你最重要的工具
预置环境: user32, ctypes, os, subprocess, json, time, pyautogui, psutil, re, math, urllib

## 预置工具
open_app, find_app, focus_window, screenshot, mouse_click,
keyboard_type, keyboard_press, set_volume, list_windows,
read_ui_window, get_window_state

## 行为准则
1. 先分析再行动, 复杂任务必须输出计划
2. 能用 run_code 解决的, 就写代码
3. 执行后自己验证结果
4. 用中文回复，用上面的风格
5. 你有跨会话持久记忆。每次对话开始时，系统提示里的「经验规则」和「风格守则」
   是从你硬盘上的 brain_data/ 加载的——你的用户偏好、对话教训都写在那里。
   所以不要说自己"记不住"或"关窗就忘"。你记着呢，只是每次需要重新加载到上下文而已。
6. 如果忘记之前说过什么，用 brain_status 或 memory_status 查自己的记忆库"""


def build_dynamic_prompt(brain=None) -> str:
    """构建动态 System Prompt：基础提示 + 经验 + 风格 + 上次话题 + 你说"""
    rules = []
    if brain:
        seen_exp = set()
        for exp in brain.get_priority_experiences(min_priority=3):
            if exp.lesson and len(exp.lesson) > 10 and exp.lesson[:100] not in seen_exp:
                seen_exp.add(exp.lesson[:100])
                rules.append(exp.lesson[:120])
        rules = rules[:5]
        style_rules = []
        seen = set()
        for f in sorted(brain._facts, key=lambda x: -x.priority):
            if f.category.startswith("user_style") and f.priority >= 4 and f.content[:60] not in seen:
                seen.add(f.content[:60])
                style_rules.append(f.content[:100])
        if style_rules:
            rules.append("风格: " + "; ".join(style_rules[:5]))
        tops = sorted([f for f in brain._facts if f.category == "session.topic"],
                      key=lambda x: x.created_at, reverse=True)[:3]
        if tops:
            rules.append("上次: " + "; ".join(t.content[:60] for t in tops))
        msgs = sorted([f for f in brain._facts if f.category == "conversation.user_msgs"],
                      key=lambda x: x.created_at, reverse=True)[:3]
        if msgs:
            rules.append("你说: " + "; ".join(f.content[:50] for f in msgs))
    if rules:
        return (BASE_SYSTEM_PROMPT +
                "\n\n## 📋 记忆\n" +
                "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules[:8])))
    return BASE_SYSTEM_PROMPT


@dataclass
class AgentState:
    messages: list = field(default_factory=list)
    step: int = 0
    phase: str = "planning"  # planning | executing | verifying


class Agent:
    def __init__(self, llm, tools, brain=None, learner=None, engine=None):
        self.llm = llm
        self.tools = tools
        self.state = AgentState()
        self.engine = engine
        self.planner = Planner()
        self.reflector = Reflector(brain=brain)
        self._current_episode = None
        # ── 优化: System Prompt 缓存 ──
        self._cached_prompt = ""
        self._cached_prompt_step = -1
        try:
            from utils.config_api import load_config
            _cfg = load_config().get("model", {})
            self.max_steps = _cfg.get("max_steps", 20)
            self.max_retries = _cfg.get("max_retries", 3)
        except Exception:
            self.max_steps = 20
            self.max_retries = 3
        self._action_count = 0
        self._action_history = []
        self.brain = brain
        self.learner = learner

        self._confirm_event: asyncio.Event | None = None
        self._confirm_result: bool | None = None
        try:
            from utils.config_api import load_config, get_permission_level
            cfg = load_config()
            self._confirm_dangerous = cfg.get("agent", {}).get("confirm_dangerous", True)
            self._permission_level = get_permission_level()
        except Exception:
            self._confirm_dangerous = True
            self._permission_level = "quick_auth"

        # 启动时扫描已有工作区知识，自动吸收
        try:
            from core.workspace_manager import WORKSPACE_ROOT
            for f in (WORKSPACE_ROOT / "thoughts").glob("*.md"):
                if self.brain:
                    content = f.read_text(encoding="utf-8")
                    lines = content.split(chr(10))
                    title = f.stem.replace("_", " ").replace("-", " ")[:60]
                    self.brain.learn_fact(f"知识: {title} ({len(lines)}行)",
                                          category="self_learned.workspace",
                                          source="self_reflection", priority=2)
                    # 提取前5个标题
                    count = 0
                    for line in lines:
                        ls = line.strip()
                        if ls.startswith("##") and len(ls) > 5 and count < 5:
                            self.brain.learn_fact(ls.lstrip("#").strip()[:80],
                                                  category="self_learned.workspace",
                                                  source="self_reflection", priority=1)
                            count += 1
                    logger.info(f"🧠 吸收已有知识: {title}")
        except Exception:
            pass

    def set_confirm_handler(self):
        self._confirm_event = asyncio.Event()

    async def wait_for_confirm(self) -> bool:
        if self._confirm_event is None:
            return False
        self._confirm_result = None
        self._confirm_event.clear()
        try:
            await asyncio.wait_for(self._confirm_event.wait(), timeout=60.0)
            return self._confirm_result is True
        except asyncio.TimeoutError:
            return False

    def resolve_confirm(self, confirmed: bool):
        self._confirm_result = confirmed
        if self._confirm_event:
            self._confirm_event.set()

    # ── 权限级别判断 ──
    def _get_permission_number(self) -> int:
        try:
            return PERM_LEVELS.get(self._permission_level, {}).get("level", 2)
        except Exception:
            return 2

    def _should_auto_approve(self, tool_name: str) -> bool:
        """根据权限级别决定工具是否自动批准"""
        perm_num = self._get_permission_number()

        # Level 1 — 完全访问：全部自动批准
        if perm_num <= 1:
            return True

        # Level 4 — 完全审批：全部需要确认
        if perm_num >= 4:
            return False

        # Level 2-3: 安全工具自动通过
        if tool_name in SAFE_TOOLS:
            return True

        # 危险/关键工具 — 始终需要确认
        # (对于 Level 2 和 Level 3，危险操作都要确认)
        if tool_name in DANGEROUS_TOOLS:
            return False

        # 未分类工具：
        # Level 2 — 自动通过（宽松）
        # Level 3 — 需要确认（严格）
        return perm_num < 3

    def reset(self):
        self.state = AgentState()
        self._action_history = []

    def _build_system_prompt(self) -> str:
        """构建完整 System Prompt（静态 + 经验注入 + 阶段指引 + 缓存）"""
        # ── 优化: 缓存 — 每 5 轮重建一次 ──
        if self._cached_prompt and self.state.step - self._cached_prompt_step < 5:
            return self._cached_prompt
        prompt = build_dynamic_prompt(brain=self.brain)
        phase_guide = {
            "planning": "\n\n【当前阶段: 规划】先分析需求, 输出完整计划后再执行工具.",
            "executing": "\n\n【当前阶段: 执行】按计划逐步执行, 每步汇报结果.",
            "verifying": "\n\n【当前阶段: 验证】检查上一步是否正确, 如失败则重试.",
        }
        prompt += phase_guide.get(self.state.phase, "")
        self._cached_prompt = prompt
        self._cached_prompt_step = self.state.step
        return prompt

    async def chat(self, user_input: str) -> AsyncGenerator[dict, None]:
        window = list(self.state.messages[-40:])
        try:
            from memory.episodic import Episode, extract_fingerprint
            self._current_episode = Episode(user_input, session_id=str(time.time()))
        except Exception:
            self._current_episode = None

        # 跨会话记忆: 使用统一控制器多通道检索
        context = ""
        try:
            from memory.controller import get_controller
            ctrl = get_controller(self.brain)
            context = ctrl.context_block(user_input)
        except Exception:
            pass

        # 优化: 轻量请求跳过规划
        if _is_quick_request(user_input):
            self.state.phase = "executing"
            _log("quick_path", f"轻量: {user_input[:40]}")
        else:
            self.state.phase = "planning"
            self.planner.create_plan(user_input)
            plan_snapshot = self.planner.get_plan_snapshot()
            if plan_snapshot:
                context = plan_snapshot + "\n" + context if context else plan_snapshot

        msg_content = user_input + context if context else user_input
        messages = window + [{"role": "user", "content": msg_content}]
        self.state.messages.append({"role": "user", "content": user_input[:500]})
        self.state.step = 0
        self._action_count = 0
        self._action_history = []
        _log("user_input", user_input[:100])
        yield {"type": "thinking", "content": "思考中..."}

        recent_calls = []
        has_executed = False

        for _ in range(self.max_steps):
            self.state.step += 1
            intf = Path(__file__).parent.parent / "data" / "interrupt.flg"
            if intf.exists():
                try: intf.unlink()
                except: pass
                yield {"type": "text_delta", "text": "已中断"}
                self._after_learn(user_input)
                yield {"type": "done"}
                return

            # 每轮使用动态 System Prompt（含经验注入 + 阶段指引）
            sys_prompt = self._build_system_prompt()

            resp = None
            if self.engine:
                for r in range(self.max_retries + 1):
                    try:
                        resp, route = await self.engine.chat_with_fallback(
                            messages, self.tools.get_schemas(), sys_prompt)
                        if route.is_fallback:
                            logger.info(f"⚠️ 使用备用算力: local/{route.model}")
                        break
                    except Exception as e:
                        if r < self.max_retries:
                            logger.warning(f"引擎重试 {r+1}/{self.max_retries}: {str(e)[:80]}")
                            await asyncio.sleep(0.5)
                        else:
                            yield {"type": "error", "message": "算力全部不可用"}
                            self._after_learn(user_input)
                            return
            else:
                for r in range(self.max_retries + 1):
                    try:
                        resp = await self.llm.chat_with_tools(
                            messages=messages, tools=self.tools.get_schemas(), system=sys_prompt)
                        break
                    except Exception as e:
                        if r < self.max_retries:
                            logger.warning(f"LLM重试 {r+1}: {str(e)[:80]}")
                            await asyncio.sleep(0.5)
                        else:
                            yield {"type": "error", "message": f"LLM失败"}
                            self._after_learn(user_input)
                            return
            if not resp:
                yield {"type": "error", "message": "LLM无响应"}
                self._after_learn(user_input)
                return

            if resp.tool_calls:
                self.state.phase = "executing"
                has_executed = True
                calls = []
                for tc in resp.tool_calls:
                    calls.append({
                        "id": tc.get("id", f"c{self.state.step}"),
                        "type": "function",
                        "function": {"name": tc.get("name", "?"),
                                     "arguments": json.dumps(tc.get("params", {}), ensure_ascii=False)}
                    })
                msg = {"role": "assistant", "content": None, "tool_calls": calls}
                if resp.reasoning_content:
                    msg["reasoning_content"] = resp.reasoning_content
                messages.append(msg)
                tool_summary = {"role": "assistant", "content": f"[调用工具]" + str([tc.get('name', '?') for tc in resp.tool_calls])}
                if resp.reasoning_content:
                    tool_summary["reasoning_content"] = resp.reasoning_content[:100]
                self.state.messages.append(tool_summary)

                for tc in resp.tool_calls:
                    self._action_count += 1
                    tn = tc.get("name", "?")
                    tp = tc.get("params", {})

                    if tn == "end_turn":
                        _log("end_turn", str(tp)[:80])
                        messages.append({"role": "tool", "tool_call_id": tc.get("id", f"c{self.state.step}"),
                                         "content": "[任务结束]"})
                        self.state.messages.append({"role": "assistant", "content": "[任务结束]"})
                        self.planner.complete_plan(summary="完成")
                        yield {"type": "text_delta", "text": "✅ 任务完成。"}
                        self._after_learn(user_input)
                        yield {"type": "done"}
                        return

                    ck = f"{tn}:{json.dumps(tp, sort_keys=True, ensure_ascii=False)}"
                    recent_calls.append(ck)
                    if len(recent_calls) >= 4 and sum(1 for c in recent_calls[-6:] if c == ck) >= 4:
                        yield {"type": "tool_result", "tool": tn, "success": False, "data": "循环检测"}
                        self.state.messages.append({"role": "assistant", "content": f"[循环中止: {tn}]"})
                        self._after_learn(user_input)
                        yield {"type": "done"}
                        return

                    yield {"type": "tool_start", "tool": tn, "params": tp}
                    _log("tool", tn)

                    if not self._should_auto_approve(tn):
                        tool_info = DANGEROUS_TOOLS.get(tn, ("未分类操作", "normal"))
                        reason = tool_info[0] if isinstance(tool_info, tuple) else tool_info
                        perm_info = PERM_LEVELS.get(self._permission_level, {})
                        yield {"type": "confirm_required", "tool": tn, "reason": reason, "params": tp,
                               "permission_level": self._permission_level,
                               "permission_label": perm_info.get("label", ""),
                               "permission_icon": perm_info.get("icon", "")}
                        confirmed = await self.wait_for_confirm()
                        if not confirmed:
                            yield {"type": "tool_result", "tool": tn, "success": False, "data": "用户已取消"}
                            messages.append({"role": "tool", "tool_call_id": tc.get("id", f"c{self.state.step}"),
                                             "content": "用户取消了该操作"})
                            continue

                    try:
                        result = await self.tools.execute(tn, tp)
                    except Exception as e:
                        result = ToolResult.failure(str(e))

                    act = {"tool": tn, "params": tp, "result": "success" if result.success else "failure", "error": result.error or ""}
                    self._action_history.append(act)
                    try:
                        if self._current_episode:
                            self._current_episode.record_tool_call(tn, tp, "success" if result.success else "failure", result.error or "", latency_ms=0)
                    except Exception:
                        pass

                    if not result.success and self.learner:
                        self.learner.learn_from_error(result.error, {"tool": tn, "params": tp})

                    yield {"type": "tool_result", "tool": tn, "success": result.success,
                           "data": (result.data or result.error or "")[:800]}
                    raw = (result.data or result.error or "")[:2000]
                    # system_info: 不让LLM看到原始数据, 防止复读
                    if tn == "system_info" and result.success and result.data:
                        import re
                        m = re.search(r"CPU:([\d.]+)%.*?内存:([\d.]+)%.*?磁盘:([\d.]+)%", result.data)
                        if m:
                            raw = f"[system] CPU {m.group(1)}%, memory {m.group(2)}%, disk {m.group(3)}%"
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", f"c{self.state.step}"),
                                     "content": raw})
                    self.state.messages.append({"role": "assistant", "content": f"[{tn}: {'ok' if result.success else 'fail'}]"})
                continue

            text = resp.text or ""
            if text:
                self.state.phase = "verifying" if has_executed else "planning"
                # 从LLM输出中提取计划步骤, 自动注册到Planner
                if not has_executed and not self._action_history:
                    self._parse_plan_from_text(text)
                am = {"role": "assistant", "content": text}
                if resp.reasoning_content:
                    am["reasoning_content"] = resp.reasoning_content
                self.state.messages.append(am)
                _log("reply", text[:100])
                yield {"type": "text_delta", "text": text}
            else:
                self.state.messages.append({"role": "assistant", "content": "[工具执行完成]"})

            self._after_learn(user_input)
            if self._current_episode:
                self._current_episode.finish(outcome="success" if not any(a.get("result") == "failure" for a in self._action_history) else "failure")
                self._current_episode = None
            if self.brain:
                topic = user_input[:80]
                self.brain.learn_fact("会话主题: " + topic, category="session.topic", source="self", priority=3)
            yield {"type": "done"}
            return

        if not any(m.get("role") == "assistant" and m.get("content") for m in self.state.messages[-5:]):
            self.state.messages.append({"role": "assistant", "content": "[任务执行超出步数上限]"})
        self._after_learn(user_input)
        if self._current_episode:
            self._current_episode.finish(outcome="success" if not any(a.get("result") == "failure" for a in self._action_history) else "failure")
            self._current_episode = None
        if self.brain:
            topic = user_input[:80]
            self.brain.learn_fact("会话主题: " + topic, category="session.topic", source="self", priority=3)
        yield {"type": "done"}

    def _parse_plan_from_text(self, text: str):
        """从LLM回复中提取计划步骤, 自动注册到Planner"""
        if not self.planner.current_plan():
            return
        lines = text.split(chr(10))
        prev_task = None
        prev_id = ""
        for line in lines:
            line = line.strip()
            # 匹配 "1. xxx", "1️⃣ xxx", "- xxx"
            import re
            m = re.match(r'^(\d+)[.、．]\s+(.+)', line)
            if not m:
                m = re.match(r'^[\d]+[️⃣]\s*(.+)', line)
            if not m:
                m = re.match(r'^[-•*]\s+(.+)', line)
            if m:
                goal = m.group(1) if len(m.groups()) == 1 else m.group(1)
                deps = [prev_id] if prev_id else None
                task = self.planner.add_task(goal[:80], depends_on=deps, expected_output="")
                prev_id = task.id
        # 如果有计划, 更新上下文
        snapshot = self.planner.get_plan_snapshot()
        if snapshot:
            logger.info(f"📋 自动注册计划完成")

    def _auto_learn_from_actions(self):
        """从本轮执行的动作中自动提取知识并学习"""
        if not self.brain or not self._action_history:
            return
        try:
            for act in self._action_history:
                tool = act.get("tool", "")
                params = act.get("params", {})
                result = act.get("result", "")

                # 1. create_workspace_file → 读取内容并学习
                if tool == "create_workspace_file" and result == "success":
                    path = params.get("path", "")
                    purpose = params.get("purpose", "")
                    category = params.get("category", "")
                    content = params.get("content", "")
                    if content and len(content) > 50:
                        cat = category if category in ("thought", "project") else "knowledge"
                        lines = content.split(chr(10))
                        # 提取带#或数字的标题行作为知识点
                        key_points = []
                        for line in lines:
                            ls = line.strip()
                            if ls.startswith("#") and len(ls) > 5:
                                key_points.append(ls.lstrip("#").strip())
                            elif ls.startswith("- **") and "**" in ls:
                                key_points.append(ls.replace("- **", "").replace("**", ""))
                        for kp in key_points[:8]:
                            self.brain.learn_fact(kp, category=f"self_learned.{cat}",
                                                  source="self_reflection", priority=2)
                        # 整体摘要作为事实
                        summary = content[:200].strip()
                        self.brain.learn_fact(f"自主知识: {purpose[:60]} - {summary[:100]}",
                                              category=f"self_learned.{cat}",
                                              source="self_reflection", priority=2)
                        logger.info(f"🧠 自主学习: 从'{path}'提取{len(key_points)}个知识点")

                # 2. create_temp_file → 临时内容摘要学习
                elif tool == "create_temp_file" and result == "success":
                    content = params.get("content", "")
                    purpose = params.get("purpose", "")
                    if content and len(content) > 80:
                        summary = content[:150].strip()
                        self.brain.learn_fact(f"临时知识: {purpose[:40]} - {summary[:80]}",
                                              category="self_learned.temp",
                                              source="self_reflection", priority=1)

            # 3. 从最后一条assistant回复中提取知识（如果有结构化内容）
            if not self._action_history:
                return
            last_reply = ""
            for m in reversed(self.state.messages):
                if m.get("role") == "assistant" and m.get("content"):
                    text = m["content"]
                    if isinstance(text, str) and len(text) > 100:
                        last_reply = text
                        break
            if last_reply:
                # 从回复中提取关键段落（含列表、分类等结构化内容）
                lines = last_reply.split(chr(10))
                for line in lines:
                    ls = line.strip()
                    # 匹配 "**xxx**" 格式的知识点
                    if ls.startswith("**") and "**" in ls[2:]:
                        self.brain.learn_fact(ls.strip("*").strip(),
                                              category="self_learned.reply",
                                              source="self_reflection", priority=1)
                    # 匹配含"⭐"的高价值信息
                    if "⭐" in ls or "★" in ls:
                        self.brain.learn_fact(ls[:120],
                                              category="self_learned.reply",
                                              source="self_reflection", priority=2)
        except Exception:
            pass

    def _after_learn(self, user_input: str):
        if self.brain:
            try:
                from memory.controller import get_controller
                get_controller(self.brain).memorize(user_input)
            except Exception:
                pass
        if not self.learner or not self.brain:
            return
        try:
            self.state.phase = "verifying"
            if self._action_history:
                result = self.reflector.reflect(user_input, self._action_history)
                for act in self._action_history:
                    error_text = act.get("error", "")
                    tool_name = act.get("tool", "")
                    domain = map_tool_to_domain(tool_name)
                    err_cat = classify_error(error_text)
                    priority = result.priority if act.get("result") == "failure" else 1
                    self.brain.record_experience(
                        intent=user_input[:50], action=tool_name,
                        result=act.get("result", "unknown"), error=error_text[:100],
                        lesson=result.reusable_lesson or f"{tool_name}: {error_text[:60]}",
                        priority=priority, domain=domain, error_category=err_cat,
                    )
                if result.reusable_lesson and result.priority >= 3:
                    self.brain.learn_fact(f"[经验] {result.reusable_lesson}",
                        category=f"experience.{result.domain}", source="self_reflection",
                        priority=result.priority)
            if self._action_history:
                reply = ""
                for m in reversed(self.state.messages):
                    if m.get("role") == "assistant" and isinstance(m.get("content"), str) and len(m["content"]) > 10:
                        reply = m["content"][:500]
                        break
                if reply:
                    self.learner.learn_from_conversation(user_input, reply, self._action_history)
                    self.brain.learn_style(user_input, reply)
            self._auto_learn_from_actions()
        except Exception:
            pass

