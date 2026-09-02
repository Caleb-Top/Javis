"""JAVIS Agent v3 — Phase-driven + Dynamic Prompt + Auto-Learning"""

import asyncio, json, logging, time, hashlib, uuid, re
from typing import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from core.llm_client import (
    LLMClient,
    LLMResponse,
    ModelRuntimeUnavailableError,
    ModelSetupRequiredError,
)
from core.tool_registry import ToolRegistry
from core.tool_result import ToolResult
from core.planner import Planner
from core.reflector import Reflector, classify_error, map_tool_to_domain
from core.prompt_builder import PromptBuilder, build_dynamic_prompt
from core.hook_system import HookEvent, get_hook_manager
from core.memory_kernel import get_core_memory_kernel
from core.session_recall import build_session_recall_answer
from core.action_policy import evaluate_action
from core.cancellation import CancellationToken, RequestCancelled

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


LIVE_ACTION_KEYWORDS = (
    "打开", "关闭", "运行", "执行", "创建", "删除", "修改", "写入", "保存",
    "读取", "文件", "文件夹", "项目", "代码", "命令", "终端", "截图", "截屏",
    "屏幕", "摄像头", "搜索", "下载", "安装", "发送", "点击", "鼠标", "键盘",
    "音量", "系统", "浏览器", "网页", "git ", "github", "训练", "部署", "构建",
)

LIVE_BRIEF_SYSTEM = """你是 Javis，Eric 的本地桌面智能管家。
这是实时语音/文字交流。先给结论，短句，自然直接。
不要展示思考过程，不要复读原始数据，不要写报告。
严格遵守用户指定的输出格式。"""


def _extract_live_exact_reply(user_input: str) -> str | None:
    """Return a requested literal reply without letting a model rewrite it."""
    text = str(user_input or "").strip()
    patterns = (
        r"^(?:请)?(?:严格)?(?:仅|只)(?:需|要)?(?:回复|输出)\s*[:：]\s*(.+?)\s*$",
        r"^(?:please\s+)?(?:reply|respond|output)(?:\s+with)?\s+(?:exactly|only)\s*[:：]\s*(.+?)\s*$",
    )
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        answer = match.group(1).strip()
        paired_quotes = {('"', '"'), ("'", "'"), ("“", "”")}
        if len(answer) >= 2 and (answer[0], answer[-1]) in paired_quotes:
            answer = answer[1:-1].strip()
        return answer[:1000] if answer else None
    return None


def _is_live_fast_dialogue(user_input: str) -> bool:
    """Only route short, non-action conversation away from the full tool agent."""
    text = " ".join(str(user_input or "").strip().split())
    if not text or len(text) > 160:
        return False
    lowered = text.lower()
    return not any(keyword in lowered for keyword in LIVE_ACTION_KEYWORDS)

def _log(t, d):
    action_log.append({"time": time.strftime("%H:%M:%S"), "type": t, "detail": d})
    if len(action_log) > 500: action_log.pop(0)
    logger.info(f"[LOG] {t}: {d[:100]}")


def _model_failure_event(error: RuntimeError, route_name: str = "live") -> dict:
    """Return a terminal, actionable model failure for every conversation surface."""
    if isinstance(error, ModelSetupRequiredError):
        return {
            "type": "error",
            "code": "model_setup_required",
            "message": str(error),
            "recovery_action": "open_model_settings",
            "route": error.route_name,
            "reason": error.reason,
        }
    base_url = str(getattr(error, "base_url", ""))
    managed_runtime = bool(
        re.match(r"^https?://(?:127\.0\.0\.1|localhost):11435(?:/|$)", base_url, re.I)
    )
    logger.warning("Local model runtime unavailable on %s: %s", route_name, str(error)[:500])
    return {
        "type": "error",
        "code": "model_runtime_unavailable",
        "message": (
            "Local model runtime is unavailable. Open Model & Storage to check the connection."
        ),
        "recovery_action": (
            "restart_local_runtime" if managed_runtime else "open_model_settings"
        ),
        "route": route_name if route_name in {"live", "code"} else "live",
        "reason": (
            "managed_runtime_offline" if managed_runtime else "external_runtime_unavailable"
        ),
    }

# ── 导入权限级别 ──
try:
    from utils.config_api import get_permission_level, PERMISSION_LEVELS as PERM_LEVELS
    _PERM_LEVELS_AVAILABLE = True
except Exception:
    _PERM_LEVELS_AVAILABLE = False

# ════════════════════════════════════════════════════════════
# P0-8: 三层 Prompt 架构
# Tier 1 — 身份 + 工具: 你是谁，你能用什么工具
# Tier 2 — 经验 + 风格: 从 brain/Memory 动态加载
# Tier 3 — 阶段指引: 当前 plan-execute-verify 阶段
# ════════════════════════════════════════════════════════════

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
5. 只有当前请求附带的、带来源引用的受控记忆上下文可以作为长期记忆使用。
6. 没有受控记忆上下文时不要声称记得跨会话事实，也不要从旧 Brain 或全局索引兜底召回。"""


def build_dynamic_prompt(brain=None) -> str:
    """Return the static compatibility prompt without consulting legacy Brain."""
    return BASE_SYSTEM_PROMPT


@dataclass
class AgentState:
    messages: list = field(default_factory=list)
    step: int = 0
    phase: str = "planning"  # planning | executing | verifying


class Agent:
    def __init__(self, llm, tools, brain=None, learner=None, engine=None, tool_catalog=None):
        self.llm = llm
        self.tools = tools
        self.state = AgentState()
        self.engine = engine
        self.tool_catalog = tool_catalog
        self.planner = Planner()
        self.reflector = Reflector(brain=None)
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
        # ── P0-8: 三层 Prompt 架构 ──
        self.prompt_builder = PromptBuilder(brain=None)

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

        # ── P1-3: Hooks 系统集成 ──
        self.hook_manager = get_hook_manager()

        # Long-term memory is supplied only through a request-scoped RecallBundle.

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

    def _should_auto_approve(self, tool_name: str, params: dict | None = None) -> bool:
        """根据权限级别决定工具是否自动批准"""
        if evaluate_action(tool_name, params or {}).action != "allow":
            return False
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
        self.invalidate_memory_context()

    def invalidate_memory_context(self) -> None:
        """Drop every assembled prompt that could contain recalled memory."""

        self._cached_prompt = ""
        self._cached_prompt_step = -1
        if hasattr(self, 'prompt_builder'):
            self.prompt_builder.invalidate_cache()

    def _conversation_history(
        self,
        conversation_cards: list[dict] | None,
        limit: int = 40,
    ) -> list[dict]:
        history = []
        for card in (conversation_cards or [])[-max(1, int(limit)):]:
            if not isinstance(card, dict) or card.get("status") == "interrupted":
                continue
            role = str(card.get("role") or "").strip()
            content = str(card.get("content") or card.get("text") or "").strip()
            if role in {"user", "assistant"} and content:
                history.append({"role": role, "content": content[:4000]})
        return history

    def _completion_event(
        self,
        success: bool | None = None,
        detail: str = "",
    ) -> dict:
        if success is None:
            success = (
                not self._action_history
                or self._action_history[-1].get("result") == "success"
            )
        event = {"type": "done", "success": bool(success)}
        if detail:
            event["detail"] = detail
        elif not success:
            event["detail"] = "No verified fallback succeeded"
        return event

    def _select_tool_schemas(self, user_input: str) -> list[dict]:
        if self.tool_catalog is None:
            return self.tools.get_schemas()
        selected = self.tool_catalog.schemas_for(user_input, limit=12)
        selected_names = {
            schema.get("function", {}).get("name")
            for schema in selected
        }
        for name in ("tool_search", "tool_inspect", "end_turn"):
            if name in selected_names:
                continue
            tool = self.tools.get(name)
            if tool is None:
                continue
            selected.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
            selected_names.add(name)
        return selected

    def _build_system_prompt(self, force: bool = False, recall_bundle=None) -> str:
        """构建完整 System Prompt（三层架构: 身份 + 记忆 + 阶段）

        P0-8: 使用 PromptBuilder 组装三层 Prompt:
          Layer 1 — 基础身份层（静态）: 个性、语气、核心能力
          Layer 2 — 请求级记忆层: 经 ACL 校验的 RecallBundle 与来源引用
          Layer 3 — 阶段指引层: 当前阶段指引 + 护栏摘要 + 安全规则

        Args:
            force: 强制重建所有层（跳过缓存）
        """
        prompt = self.prompt_builder.build(
            phase=self.state.phase,
            step=self.state.step,
            force_rebuild=force,
            recall_bundle=recall_bundle,
        )

        # 保持旧缓存的兼容性
        self._cached_prompt = prompt
        self._cached_prompt_step = self.state.step

        return prompt

    async def chat(
        self,
        user_input: str,
        session_id: str = "",
        conversation_cards: list[dict] | None = None,
        interaction_mode: str = "",
        cancellation: CancellationToken | None = None,
        recall_bundle=None,
    ) -> AsyncGenerator[dict, None]:
        token = cancellation or CancellationToken()
        self._action_history = []
        await token.checkpoint()
        core_answer = get_core_memory_kernel().answer_if_core_query(user_input)
        if core_answer:
            await token.checkpoint()
            self.state.messages.append({"role": "user", "content": user_input[:500]})
            self.state.messages.append({"role": "assistant", "content": core_answer})
            _log("core_memory", core_answer[:100])
            yield {"type": "text_delta", "text": core_answer}
            yield self._completion_event(True)
            return

        session_answer = build_session_recall_answer(
            user_input,
            state_messages=self.state.messages,
            conversation_cards=conversation_cards,
            session_id=session_id,
        )
        if session_answer:
            await token.checkpoint()
            self.state.messages.append({"role": "user", "content": user_input[:500]})
            self.state.messages.append({"role": "assistant", "content": session_answer})
            _log("session_recall", session_answer[:100])
            yield {"type": "text_delta", "text": session_answer}
            yield self._completion_event(True)
            return

        if interaction_mode == "live" and _is_live_fast_dialogue(user_input):
            exact_reply = _extract_live_exact_reply(user_input)
            if exact_reply is not None:
                await token.checkpoint()
                self.state.messages.append({"role": "user", "content": user_input[:500]})
                self.state.messages.append({"role": "assistant", "content": exact_reply})
                _log("live_exact", exact_reply[:100])
                yield {"type": "text_delta", "text": exact_reply}
                yield self._completion_event(True)
                return
            history_source = (
                conversation_cards
                if conversation_cards is not None
                else self.state.messages
            )
            history = self._conversation_history(history_source, limit=12)
            if history and history[-1].get("role") == "user" and history[-1].get("content") == user_input:
                history.pop()
            messages = history + [{"role": "user", "content": user_input}]
            yield {
                "type": "activity",
                "activity": "understanding",
                "detail": "Understanding the request",
            }
            try:
                if self.engine:
                    response, route = await token.race(
                        self.engine.chat_brief_with_fallback(
                            messages,
                            LIVE_BRIEF_SYSTEM,
                            max_tokens=256,
                        )
                    )
                    _log("live_fast", f"{route.provider}/{route.model} {route.latency_ms:.0f}ms")
                else:
                    response = await token.race(
                        self.llm.chat_brief(messages, LIVE_BRIEF_SYSTEM, max_tokens=256)
                    )
                answer = str(response.text or "").strip()
                if not answer:
                    raise RuntimeError("empty live response")
                await token.checkpoint()
                self.state.messages.append({"role": "user", "content": user_input[:500]})
                self.state.messages.append({"role": "assistant", "content": answer[:2000]})
                self._after_learn(user_input)
                yield {"type": "text_delta", "text": answer}
                yield self._completion_event(True)
                return
            except RequestCancelled:
                raise
            except (ModelSetupRequiredError, ModelRuntimeUnavailableError) as exc:
                yield _model_failure_event(exc, interaction_mode)
                self._after_learn(user_input)
                return
            except Exception as exc:
                logger.warning("Live fast path fallback: %s", str(exc)[:120])

        window = (
            self._conversation_history(conversation_cards, limit=40)
            if conversation_cards is not None
            else self._conversation_history(self.state.messages, limit=40)
        )
        context = ""

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

        # ── P1-3: UserPromptSubmit Hook ──
        try:
            await self.hook_manager.trigger(HookEvent.USER_PROMPT_SUBMIT, {
                "user_input": user_input, "step": self.state.step})
        except Exception:
            pass
        # ── End UserPromptSubmit Hook ──

        yield {
            "type": "activity",
            "activity": "understanding",
            "detail": "Understanding the request",
        }
        yield {
            "type": "activity",
            "activity": "executing" if self.state.phase == "executing" else "planning",
            "detail": "Preparing the next step",
        }

        recent_calls = []
        has_executed = False

        for _ in range(self.max_steps):
            await token.checkpoint()
            self.state.step += 1
            intf = Path(__file__).parent.parent / "data" / "interrupt.flg"
            if intf.exists():
                try: intf.unlink()
                except: pass
                yield {"type": "text_delta", "text": "已中断"}
                self._after_learn(user_input)
                yield self._completion_event(False, "Interrupted")
                return

            # 每轮使用动态 System Prompt（含经验注入 + 阶段指引）
            sys_prompt = self._build_system_prompt(recall_bundle=recall_bundle)

            resp = None
            if self.engine:
                for r in range(self.max_retries + 1):
                    try:
                        resp, route = await token.race(
                            self.engine.chat_with_fallback(
                                messages,
                                self._select_tool_schemas(user_input),
                                sys_prompt,
                            )
                        )
                        if route.is_fallback:
                            logger.info(f"⚠️ 使用备用算力: local/{route.model}")
                        break
                    except RequestCancelled:
                        raise
                    except (ModelSetupRequiredError, ModelRuntimeUnavailableError) as e:
                        yield _model_failure_event(e, interaction_mode)
                        self._after_learn(user_input)
                        return
                    except Exception as e:
                        if r < self.max_retries:
                            logger.warning(f"引擎重试 {r+1}/{self.max_retries}: {str(e)[:80]}")
                            await token.race(asyncio.sleep(0.5))
                        else:
                            yield {"type": "error", "message": "算力全部不可用"}
                            self._after_learn(user_input)
                            return
            else:
                for r in range(self.max_retries + 1):
                    try:
                        resp = await token.race(
                            self.llm.chat_with_tools(
                                messages=messages,
                                tools=self._select_tool_schemas(user_input),
                                system=sys_prompt,
                            )
                        )
                        break
                    except RequestCancelled:
                        raise
                    except (ModelSetupRequiredError, ModelRuntimeUnavailableError) as e:
                        yield _model_failure_event(e, interaction_mode)
                        self._after_learn(user_input)
                        return
                    except Exception as e:
                        if r < self.max_retries:
                            logger.warning(f"LLM重试 {r+1}: {str(e)[:80]}")
                            await token.race(asyncio.sleep(0.5))
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
                    await token.checkpoint()

                    if tn == "end_turn":
                        _log("end_turn", str(tp)[:80])
                        messages.append({"role": "tool", "tool_call_id": tc.get("id", f"c{self.state.step}"),
                                         "content": "[任务结束]"})
                        self.state.messages.append({"role": "assistant", "content": "[任务结束]"})
                        self.planner.complete_plan(summary="完成")
                        yield {"type": "text_delta", "text": "✅ 任务完成。"}
                        self._after_learn(user_input)
                        yield self._completion_event()
                        return

                    ck = f"{tn}:{json.dumps(tp, sort_keys=True, ensure_ascii=False)}"
                    recent_calls.append(ck)
                    if len(recent_calls) >= 4 and sum(1 for c in recent_calls[-6:] if c == ck) >= 4:
                        yield {"type": "tool_result", "tool": tn, "success": False, "data": "循环检测"}
                        self.state.messages.append({"role": "assistant", "content": f"[循环中止: {tn}]"})
                        self._after_learn(user_input)
                        yield self._completion_event(False, "Repeated tool loop detected")
                        return

                    yield {"type": "tool_start", "tool": tn, "params": tp}
                    _log("tool", tn)

                    decision = evaluate_action(tn, tp)
                    if decision.action == "deny":
                        yield {"type": "tool_result", "tool": tn, "success": False, "data": decision.reason}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", f"c{self.state.step}"),
                            "content": decision.reason,
                        })
                        continue

                    action_confirmed = False
                    if not self._should_auto_approve(tn, tp):
                        tool_info = DANGEROUS_TOOLS.get(tn, ("未分类操作", "normal"))
                        reason = decision.reason or (tool_info[0] if isinstance(tool_info, tuple) else tool_info)
                        perm_info = PERM_LEVELS.get(self._permission_level, {})
                        yield {"type": "confirm_required", "tool": tn, "reason": reason, "params": tp,
                               "permission_level": self._permission_level,
                               "permission_label": perm_info.get("label", ""),
                               "permission_icon": perm_info.get("icon", "")}
                        confirmed = await token.race(self.wait_for_confirm())
                        if not confirmed:
                            yield {"type": "tool_result", "tool": tn, "success": False, "data": "用户已取消"}
                            messages.append({"role": "tool", "tool_call_id": tc.get("id", f"c{self.state.step}"),
                                             "content": "用户取消了该操作"})
                            continue
                        action_confirmed = True

                    # ── P1-3: PreToolUse Hook ──
                    try:
                        pre_hook = await self.hook_manager.trigger(HookEvent.PRE_TOOL_USE, {
                            "tool_name": tn, "tool_params": tp, "user_input": user_input})
                        if not pre_hook.allowed:
                            yield {"type": "tool_result", "tool": tn, "success": False,
                                   "data": f"Hook blocked: {pre_hook.message}"}
                            messages.append({"role": "tool", "tool_call_id": tc.get("id", f"c{self.state.step}"),
                                             "content": f"Blocked: {pre_hook.message}"})
                            continue
                    except Exception:
                        pass
                    # ── End PreToolUse Hook ──

                    t0 = time.time()
                    try:
                        result = await self.tools.execute(tn, tp, confirmed=action_confirmed)
                    except RequestCancelled:
                        raise
                    except Exception as e:
                        result = ToolResult.failure(str(e))
                    elapsed_ms = (time.time() - t0) * 1000

                    # ── P1-3: PostToolUse / PostToolUseFailure Hooks ──
                    try:
                        if result.success:
                            await self.hook_manager.trigger(HookEvent.POST_TOOL_USE, {
                                "tool_name": tn, "tool_params": tp, "success": True,
                                "result_data": (result.data or "")[:500],
                                "duration_ms": elapsed_ms})
                        else:
                            await self.hook_manager.trigger(HookEvent.POST_TOOL_USE_FAILURE, {
                                "tool_name": tn, "tool_params": tp, "error": result.error or "unknown",
                                "duration_ms": elapsed_ms})
                    except Exception:
                        pass
                    # ── End PostToolUse Hooks ──

                    act = {"tool": tn, "params": tp, "result": "success" if result.success else "failure", "error": result.error or ""}
                    self._action_history.append(act)

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
                    await token.checkpoint()
                    if not result.success:
                        yield {
                            "type": "activity",
                            "activity": "fallback",
                            "detail": f"{tn} failed; choosing another path",
                        }
                continue

            completion = self._completion_event()
            text = resp.text or ""
            if not completion["success"]:
                text = "\u4efb\u52a1\u672a\u5b8c\u6210\uff1a\u6ca1\u6709\u9a8c\u8bc1\u6210\u529f\u7684\u66ff\u4ee3\u8def\u5f84\u3002"
            if text:
                await token.checkpoint()
                self.state.phase = "verifying" if has_executed else "planning"
                if has_executed:
                    yield {
                        "type": "activity",
                        "activity": "verifying",
                        "detail": "Verifying the result",
                    }
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
            yield completion
            return

        if not any(m.get("role") == "assistant" and m.get("content") for m in self.state.messages[-5:]):
            self.state.messages.append({"role": "assistant", "content": "[任务执行超出步数上限]"})
        self._after_learn(user_input)
        yield self._completion_event(False, "Maximum execution steps reached")

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
        """Legacy automatic learning is quarantined by the L2 memory boundary."""
        return

    def _after_learn(self, user_input: str):
        """Retained as a no-op compatibility hook; L2 projection owns persistence."""
        return
