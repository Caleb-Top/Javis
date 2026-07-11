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
4. 用中文回复，用上面的风格"""


def build_dynamic_prompt(brain=None) -> str:
    """构建动态 System Prompt：基础提示 + 高优先级经验规则"""
    rules = []
    if brain:
        for exp in brain.get_priority_experiences(min_priority=3):
            if exp.lesson and len(exp.lesson) > 10:
                rules.append(exp.lesson[:120])
        rules = rules[:5]
    if rules:
        return (BASE_SYSTEM_PROMPT +
                "\n\n## 📋 经验规则（从过去错误中学习）\n" +
                "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules)))
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
        """构建完整 System Prompt（静态 + 经验注入 + 阶段指引）"""
        prompt = build_dynamic_prompt(brain=self.brain)
        phase_guide = {
            "planning": "\n\n【当前阶段: 规划】先分析需求, 输出完整计划后再执行工具.",
            "executing": "\n\n【当前阶段: 执行】按计划逐步执行, 每步汇报结果.",
            "verifying": "\n\n【当前阶段: 验证】检查上一步是否正确, 如失败则重试.",
        }
        return prompt + phase_guide.get(self.state.phase, "")

    async def chat(self, user_input: str) -> AsyncGenerator[dict, None]:
        window = list(self.state.messages[-40:])

        # ── 上下文构建 ──
        context = ""
        if self.brain:
            facts = self.brain.recall(user_input, max_results=3)
            if facts:
                context = "\n[相关知识]:\n" + "\n".join(f"- {f.content[:80]}" for f in facts)
            exps = self.brain.get_experiences(user_input, max_results=2)
            if exps:
                context += "\n[相关经验]:\n" + "\n".join(f"- {e.intent[:40]}: {e.lesson[:60]}" for e in exps)

        # ── 规划阶段 ──
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

                    if not result.success and self.learner:
                        self.learner.learn_from_error(result.error, {"tool": tn, "params": tp})

                    yield {"type": "tool_result", "tool": tn, "success": result.success,
                           "data": (result.data or result.error or "")[:800]}
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", f"c{self.state.step}"),
                                     "content": (result.data or result.error or "")[:2000]})
                    self.state.messages.append({"role": "assistant", "content": f"[{tn}: {'✅' if result.success else '❌'}]"})
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
            yield {"type": "done"}
            return

        if not any(m.get("role") == "assistant" and m.get("content") for m in self.state.messages[-5:]):
            self.state.messages.append({"role": "assistant", "content": "[任务执行超出步数上限]"})
        self._after_learn(user_input)
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
        if not self.learner or not self.brain:
            return
        try:
            self.state.phase = "verifying"
            for word in ["喜欢", "习惯", "不要", "请"]:
                if word in user_input:
                    self.brain.learn_fact(f"用户偏好: {user_input[:60]}", category="user_pref", priority=2)
                    break

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
                    if m.get("role") == "assistant" and m.get("content"):
                        c = m["content"]
                        if isinstance(c, str) and len(c) > 10:
                            reply = c[:500]
                            break
                if reply:
                    self.learner.learn_from_conversation(user_input, reply, self._action_history)

            # ★ 自主知识学习：从工具执行结果中自动提取知识
            self._auto_learn_from_actions()

            try:
                from core.workspace_manager import WorkspaceManager
                wm = WorkspaceManager()
                if wm.get_temp_files():
                    wm.record_as_fact(self.brain)
            except: pass

            try:
                from utils.memory import save_conversation
                sid = uuid.uuid4().hex[:12]
                cards = []
                for m in self.state.messages[-50:]:
                    role = m.get("role","")
                    content = m.get("content","")
                    if isinstance(content, str) and content:
                        cards.append({"role":role,"text":content[:500]})
                if cards:
                    save_conversation(f"auto_{sid}", cards)
            except: pass

            try:
                self.brain.cleanup()
            except: pass

            logger.info(f"自学习完成: {len(self._action_history)} 次操作 phase={self.state.phase}")
        except Exception as e:
            logger.debug(f"自学习跳过: {e}")
