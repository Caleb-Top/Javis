"""JARVIS Web 版入口"""
import os,sys,json,logging,asyncio
from contextlib import asynccontextmanager
sys.excepthook=lambda t,v,tb:print(f"FATAL: {t.__name__}: {v}",file=sys.stderr,flush=True)
from pathlib import Path
ROOT=Path(__file__).parent;sys.path.insert(0,str(ROOT))
logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger=logging.getLogger("jarvis")

def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

_TEST_MODE = _env_flag("JAVIS_TEST_MODE")
_STARTUP_SIDE_EFFECTS = not _TEST_MODE and not _env_flag("JAVIS_DISABLE_STARTUP_SIDE_EFFECTS")

from core.runtime import create_runtime
from core.agent_run_recorder import AgentRunRecorder
from gateway.conversation_stall_harness import ConversationStallHarness
from gateway.conversation_ws import ConversationWebSocketGateway
from control.command_tasks import CommandTaskRunner
from evolution.service import EvolutionService
from memory.session_db import SessionEventStore
from perception.adapters.ocr import OcrAdapter
from perception.adapters.vlm import LocalVlmAdapter, OpenAICompatibleVlmDescriber
from perception.adapters.yolo import YoloDetectionAdapter
from perception.service import PerceptionService
from perception.video import VideoStreamAnalyzer
from voice.native_capture import (
    get_diagnostics as get_capture_diagnostics,
    probe_capture,
    start_capture,
    stop_capture,
)
from voice.continuous_capture import continuous_capture_manager
from voice.native_playback import NativePlaybackManager
from voice.runtime_diagnostics import VoiceDiagnosticsCollector
from voice.stt import get_diagnostics as get_stt_diagnostics, preload_model
from voice.streaming_ws import get_gateway_diagnostics, serve_continuous_voice_stream
from voice.tts import get_diagnostics as get_tts_diagnostics
from utils.local_surface_commands import match_local_surface_command

def _event_store_path() -> Path:
    configured = os.environ.get("JAVIS_EVENT_STORE_PATH")
    if configured:
        return Path(configured)
    if _TEST_MODE:
        import tempfile

        return Path(tempfile.gettempdir()) / f"javis_test_session_events_{os.getpid()}.sqlite"
    return ROOT / "memory" / "session_events.sqlite"

def _build_vlm_adapter() -> LocalVlmAdapter:
    try:
        from utils.config_api import load_config

        cfg = load_config()
    except Exception:
        cfg = {}
    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    local_cfg = model_cfg.get("local", {}) if isinstance(model_cfg.get("local", {}), dict) else {}
    vlm_cfg = model_cfg.get("vlm", {}) if isinstance(model_cfg.get("vlm", {}), dict) else {}
    model_name = (
        os.environ.get("JAVIS_VLM_MODEL")
        or vlm_cfg.get("name")
        or "llava:latest"
    )
    base_url = (
        os.environ.get("JAVIS_VLM_BASE_URL")
        or vlm_cfg.get("base_url")
        or local_cfg.get("base_url")
        or "http://localhost:11434/v1"
    )
    api_key = os.environ.get("JAVIS_VLM_API_KEY") or vlm_cfg.get("api_key") or local_cfg.get("api_key") or ""
    timeout = float(os.environ.get("JAVIS_VLM_TIMEOUT") or vlm_cfg.get("timeout", 8))
    enabled = str(os.environ.get("JAVIS_VLM_ENABLED", vlm_cfg.get("enabled", True))).lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if not enabled:
        return LocalVlmAdapter(model_name=str(model_name))
    describer = OpenAICompatibleVlmDescriber(
        base_url=str(base_url),
        model=str(model_name),
        api_key=str(api_key),
        timeout=timeout,
    )
    return LocalVlmAdapter(describer=describer, model_name=str(model_name))

runtime = create_runtime(ROOT, startup_side_effects=_STARTUP_SIDE_EFFECTS)
runtime.register_event_store(SessionEventStore(_event_store_path()))
perception_service = PerceptionService()
perception_service.register_adapter(OcrAdapter())
perception_service.register_adapter(YoloDetectionAdapter())
perception_service.register_adapter(_build_vlm_adapter())
runtime.register_subsystem(perception_service)
evolution_service = EvolutionService()
runtime.register_subsystem(evolution_service)
command_task_runner = CommandTaskRunner(ROOT, runtime.event_bus, runtime.registry.guard)
runtime.register_subsystem(command_task_runner)
brain = runtime.brain
learner = runtime.learner
registry = runtime.registry
llm = runtime.llm
engine = runtime.engine
agent = runtime.agent
SKILL_LIST = runtime.skill_list
CURRENT_SKILL = runtime.current_skill
native_playback_manager = NativePlaybackManager(service=continuous_capture_manager.service)
voice_diagnostics_collector = VoiceDiagnosticsCollector(
    capture_getter=get_capture_diagnostics,
    continuous_getter=continuous_capture_manager.status,
    gateway_getter=get_gateway_diagnostics,
    playback_getter=native_playback_manager.status,
    stt_getter=get_stt_diagnostics,
    tts_getter=get_tts_diagnostics,
)

def _register_always_on_tools():
    runtime.register_always_on_tools()

def _load_skill(sid):
    global CURRENT_SKILL, SKILL_LIST
    count = runtime.load_skill(sid)
    CURRENT_SKILL = runtime.current_skill
    SKILL_LIST = runtime.skill_list
    return count

def _discover():
    global SKILL_LIST
    SKILL_LIST = runtime.discover_skills()
    return SKILL_LIST
from fastapi import FastAPI,WebSocket,WebSocketDisconnect,Body
from fastapi.staticfiles import StaticFiles;from fastapi.responses import FileResponse
from utils.app_cors import install_desktop_cors


@asynccontextmanager
async def _app_lifespan(_app):
    warmup_task = None
    if _STARTUP_SIDE_EFFECTS:
        warmup_task = asyncio.create_task(asyncio.to_thread(preload_model))
    try:
        yield
    finally:
        await asyncio.to_thread(native_playback_manager.stop)
        await asyncio.to_thread(continuous_capture_manager.stop)
        if warmup_task is not None and warmup_task.done():
            try:
                warmup_task.result()
            except Exception as error:
                logger.warning("STT model warmup failed: %s", error)
        await runtime.aclose()


app=FastAPI(title="JARVIS",version="2.0",lifespan=_app_lifespan)
install_desktop_cors(app)

@app.get("/")
async def root():return FileResponse(str(ROOT/"web"/"index.html"))

@app.get("/favicon.ico")
async def favicon():return FileResponse(str(ROOT/"web"/"favicon.ico"))

def _save_uploaded_file_for_ws(path: str, content: str) -> Path:
    safe = Path(path or "").name
    if not safe or safe.startswith("."):
        safe = "uploaded_file.txt"
    uploads_root = Path(get_path_settings()["output_dir"]).resolve()
    full = (uploads_root / safe).resolve()
    if not str(full).startswith(str(uploads_root)):
        raise ValueError("upload path escapes uploads directory")
    uploads_root.mkdir(parents=True, exist_ok=True)
    full.write_text((content or "")[:100000], encoding="utf-8")
    return full

def _resolve_local_surface_action(text: str, payload: dict):
    return match_local_surface_command(text)


def _transcribe_voice_payload(audio: str) -> str:
    from voice.stt import transcribe

    return transcribe(audio)


async def _handle_ws_folder_file(command, ws):
    path = str(command.payload.get("path") or "")
    if not path:
        return
    try:
        full = _save_uploaded_file_for_ws(path, str(command.payload.get("content") or ""))
        await ws.send_json({"type": "folder_file_saved", "path": str(full)})
    except ValueError as exc:
        await ws.send_json({
            "type": "protocol.error",
            "payload": {"code": "invalid_upload_path", "message": str(exc)},
        })


async def _handle_ws_tool(command, ws):
    tool_name = str(command.payload.get("name") or "")
    params = command.payload.get("params")
    params = params if isinstance(params, dict) else {}
    if not tool_name:
        return
    recorder = AgentRunRecorder(
        runtime.agent_runs,
        f"Execute tool: {tool_name}",
        interaction_mode="tool",
    )
    recorder.record({"type": "tool_start", "tool": tool_name, "params": params})
    result = await registry.execute(tool_name, params)
    event = {
        "type": "tool_result",
        "tool": tool_name,
        "success": result.success,
        "data": (result.data or result.error or "")[:500],
        "image": result.image or "",
    }
    recorder.record(event)
    recorder.record({"type": "done"})
    await ws.send_json(event)
    await ws.send_json({"type": "done"})


async def _handle_ws_permission_change(command, ws):
    permission = str(command.payload.get("permission") or "quick_auth")
    try:
        result = set_permission_level(permission)
        runtime.sync_permission(permission)
        await ws.send_json({"type": "permission_changed", "payload": result})
    except Exception as exc:
        await ws.send_json({
            "type": "protocol.error",
            "payload": {"code": "permission_change_failed", "message": str(exc)[:200]},
        })


conversation_stall_harness = ConversationStallHarness.from_environment()
conversation_gateway = ConversationWebSocketGateway(
    runtime,
    transcribe=_transcribe_voice_payload,
    local_action_resolver=_resolve_local_surface_action,
    command_handlers={
        "folder_file": _handle_ws_folder_file,
        "tool": _handle_ws_tool,
        "permission_change": _handle_ws_permission_change,
    },
    stall_harness=conversation_stall_harness,
)


@app.websocket("/ws")
async def ws(ws: WebSocket):
    await conversation_gateway.serve(ws)


@app.websocket("/ws_voice_stream")
async def ws_voice_stream(ws: WebSocket):
    await serve_continuous_voice_stream(ws, continuous_capture_manager)


from utils.config_api import get_status,set_api_key,set_provider,set_model_name,get_effort,set_effort,EFFORT_LEVELS,get_permission_level,set_permission_level,PERMISSION_LEVELS,get_path_settings,set_path_settings,get_model_connection_settings,set_model_connection_settings,discover_remote_provider_models,_get_api_key
from core.agent import action_log
from utils.memory import save_conversation,load_conversation,list_conversations,delete_conversation

@app.get("/api/status")
async def api_status():
    s=get_status();s["service"]="javis";s["desktop_api_version"]=2;s["capabilities"]={"continuous_voice":True,"model_settings":True,"scoped_diagnostics":True};s["skill"]=CURRENT_SKILL;s["tool_count"]=registry.count;s["skill_count"]=runtime.skill_catalog.stats()["total"];s["operational_skill_count"]=len(runtime.skill_list);s["skills"]=SKILL_LIST;s["brain"]=brain.get_stats()
    try:s["engine"]=engine.get_power_status()
    except Exception as e:logger.debug(f"引擎状态获取异常: {e}")
    return s

@app.get("/api/voice/diagnostics")
async def api_voice_diagnostics():
    return await voice_diagnostics_collector.collect()

@app.post("/api/voice/playback/speak")
async def api_voice_playback_speak(data: dict = Body(default={})):
    text = str(data.get("text", "") or "").strip()[:3000]
    if not text:
        return {"ok": False, "error": "text is required", "active": False}
    reservation = await asyncio.to_thread(native_playback_manager.reserve)
    from voice.tts import synthesize

    audio, mime = await synthesize(text)
    if not audio:
        return {"ok": False, "error": "speech synthesis failed", "active": False}
    if mime != "audio/wav":
        return {
            "ok": False,
            "error": "native playback currently requires local PCM WAV speech",
            "mime": mime,
            "active": False,
        }
    import base64

    try:
        wav_bytes = base64.b64decode(audio, validate=True)
        result = await asyncio.to_thread(
            native_playback_manager.play_reserved_wav,
            wav_bytes,
            None,
            None,
            None,
            reservation,
        )
    except Exception as error:
        return {"ok": False, "error": str(error), "active": False}
    return {**result, "mime": mime}

@app.post("/api/voice/playback/stop")
async def api_voice_playback_stop():
    return await asyncio.to_thread(native_playback_manager.stop)

@app.post("/api/voice/capture/start")
async def api_voice_capture_start(data: dict = Body(default={})):
    try:
        return await asyncio.to_thread(
            start_capture,
            str(data.get("source", "microphone") or "microphone"),
            data.get("device_index"),
        )
    except Exception as error:
        return {
            "ok": False,
            "status": "error",
            "source": str(data.get("source", "microphone") or "microphone"),
            "message": str(error),
        }

@app.post("/api/voice/capture/stop")
async def api_voice_capture_stop():
    return await asyncio.to_thread(stop_capture)

@app.post("/api/voice/capture/probe")
async def api_voice_capture_probe(data: dict = Body(default={})):
    source = str(data.get("source", "microphone") or "microphone")
    try:
        raw_device_index = data.get("device_index")
        device_index = (
            None
            if raw_device_index is None or raw_device_index == ""
            else int(raw_device_index)
        )
        return await asyncio.to_thread(
            probe_capture,
            source,
            float(data.get("duration", 1.2) or 1.2),
            device_index,
        )
    except Exception as error:
        return {
            "ok": False,
            "status": "error",
            "source": source,
            "message": str(error),
        }

@app.post("/api/voice/stt/test")
async def api_voice_stt_test(data: dict = Body(...)):
    audio = str(data.get("audio", "") or "")
    if not audio:
        return {"ok": False, "error": "audio is required"}
    if len(audio) > 12_000_000:
        return {"ok": False, "error": "audio payload is too large"}
    from voice.stt import transcribe

    transcript = await asyncio.to_thread(
        transcribe,
        audio,
        str(data.get("language", "zh") or "zh"),
    )
    return {
        "ok": bool(transcript.strip()),
        "transcript": transcript.strip(),
        "error": "" if transcript.strip() else "no speech recognized",
    }

@app.post("/api/voice/tts/test")
async def api_voice_tts_test(data: dict = Body(default={})):
    text = str(data.get("text", "") or "Javis 语音输出正常").strip()[:120]
    from voice.tts import synthesize

    audio, mime = await synthesize(text)
    if not audio:
        return {"ok": False, "error": "speech synthesis failed"}
    import base64

    try:
        byte_count = len(base64.b64decode(audio, validate=True))
    except Exception:
        byte_count = 0
    return {"ok": True, "audio": audio, "mime": mime, "bytes": byte_count}

@app.get("/api/runtime/status")
async def api_runtime_status():
    return runtime.get_runtime_status()

def _catalog_error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": str(message)[:500]}}

@app.get("/api/tool-catalog")
async def api_tool_catalog(
    q: str = "",
    preset: str = "",
    category: str = "",
    tag: str = "",
    max_risk: str = "",
    limit: int = 50,
):
    try:
        bounded_limit = max(1, min(int(limit), 100))
        if q.strip():
            tools = runtime.tool_catalog.search(
                q,
                preset=preset or None,
                max_risk=max_risk or None,
                limit=bounded_limit,
            )
        else:
            categories = tuple(value.strip() for value in category.split(",") if value.strip())
            tags = tuple(value.strip() for value in tag.split(",") if value.strip())
            tools = runtime.tool_catalog.list_tools(
                preset=preset or None,
                categories=categories,
                tags=tags,
                max_risk=max_risk or None,
            )[:bounded_limit]
        return {"ok": True, "tools": tools, "count": len(tools)}
    except (KeyError, TypeError, ValueError) as error:
        return _catalog_error("invalid_tool_query", str(error))

@app.get("/api/tool-catalog/{name}")
async def api_tool_catalog_inspect(name: str):
    tool = runtime.tool_catalog.inspect(name)
    if tool is None:
        return _catalog_error("tool_not_found", f"Unknown tool: {name}")
    return {"ok": True, "tool": tool, "health": runtime.tool_catalog.health(name)[0]}

@app.get("/api/skill-catalog")
async def api_skill_catalog(
    q: str = "",
    status: str = "",
    source: str = "",
    limit: int = 50,
):
    try:
        bounded_limit = max(1, min(int(limit), 100))
        if q.strip():
            skills = runtime.skill_catalog.search(q, limit=bounded_limit)
            if status:
                skills = [skill for skill in skills if skill["status"] == status]
            if source:
                skills = [skill for skill in skills if skill["source"] == source]
        else:
            skills = runtime.skill_catalog.list_skills(
                status=status or None,
                source=source or None,
            )[:bounded_limit]
        return {"ok": True, "skills": skills, "count": len(skills)}
    except (TypeError, ValueError) as error:
        return _catalog_error("invalid_skill_query", str(error))

@app.get("/api/skill-catalog/{name}")
async def api_skill_catalog_inspect(name: str):
    skill = runtime.skill_catalog.get(name)
    if skill is None:
        return _catalog_error("skill_not_found", f"Unknown skill: {name}")
    return {"ok": True, "skill": skill}

@app.post("/api/skill-catalog/evaluate")
async def api_skill_catalog_evaluate(data: dict = Body(...)):
    name = str(data.get("name", "")).strip()
    if not name:
        return _catalog_error("invalid_skill", "name is required")
    try:
        skill = runtime.skill_catalog.record_evaluation(
            name,
            str(data.get("status", "untested")),
            score=data.get("score"),
            details=data.get("details") if isinstance(data.get("details"), dict) else {},
        )
        return {"ok": True, "skill": skill}
    except KeyError:
        return _catalog_error("skill_not_found", f"Unknown skill: {name}")
    except (TypeError, ValueError) as error:
        return _catalog_error("skill_evaluation_rejected", str(error))

@app.post("/api/skill-catalog/status")
async def api_skill_catalog_status(data: dict = Body(...)):
    name = str(data.get("name", "")).strip()
    target = str(data.get("status", "")).strip()
    if not name or not target:
        return _catalog_error("invalid_skill", "name and status are required")
    try:
        skill = runtime.skill_catalog.promote(name, target)
        return {"ok": True, "skill": skill}
    except KeyError:
        return _catalog_error("skill_not_found", f"Unknown skill: {name}")
    except (TypeError, ValueError) as error:
        return _catalog_error("skill_transition_rejected", str(error))

@app.get("/api/agent-runs")
async def api_agent_runs(status: str = "", limit: int = 50):
    runs = runtime.agent_runs.list_runs(status=status or None, limit=max(1, min(int(limit), 100)))
    return {"ok": True, "runs": runs, "count": len(runs)}

@app.post("/api/agent-runs")
async def api_agent_run_create(data: dict = Body(...)):
    objective = str(data.get("objective", "")).strip()
    if not objective:
        return _catalog_error("invalid_run", "objective is required")
    try:
        run = runtime.agent_runs.create_run(
            objective,
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            parent_run_id=str(data.get("parent_run_id") or "") or None,
        )
        return {"ok": True, "run": run}
    except (KeyError, TypeError, ValueError) as error:
        return _catalog_error("run_create_failed", str(error))

@app.get("/api/agent-runs/{run_id}")
async def api_agent_run_get(run_id: str):
    run = runtime.agent_runs.get_run(run_id, include_graph=True)
    if run is None:
        return _catalog_error("run_not_found", f"Unknown run: {run_id}")
    return {"ok": True, "run": run}

@app.post("/api/agent-runs/{run_id}/cancel")
async def api_agent_run_cancel(run_id: str, data: dict = Body(default={})):
    try:
        run = runtime.agent_runs.cancel_run(run_id, reason=str(data.get("reason", "")))
        return {"ok": True, "run": run}
    except KeyError:
        return _catalog_error("run_not_found", f"Unknown run: {run_id}")
    except (TypeError, ValueError, RuntimeError) as error:
        return _catalog_error("run_transition_rejected", str(error))

@app.post("/api/agent-runs/{run_id}/resume")
async def api_agent_run_resume(run_id: str):
    try:
        result = runtime.agent_runs.resume_run(run_id)
        return {"ok": True, **result}
    except KeyError:
        return _catalog_error("run_not_found", f"Unknown run: {run_id}")
    except (TypeError, ValueError, RuntimeError) as error:
        return _catalog_error("run_transition_rejected", str(error))

@app.post("/api/agent-runs/approvals/{approval_id}")
async def api_agent_approval_resolve(approval_id: str, data: dict = Body(...)):
    try:
        approval = runtime.agent_runs.resolve_approval(
            approval_id,
            approved=bool(data.get("approved", False)),
            response=data.get("response") if isinstance(data.get("response"), dict) else {},
        )
        return {"ok": True, "approval": approval}
    except KeyError:
        return _catalog_error("approval_not_found", f"Unknown approval: {approval_id}")
    except (TypeError, ValueError, RuntimeError) as error:
        return _catalog_error("approval_transition_rejected", str(error))

@app.get("/api/blueprint/coverage")
async def api_blueprint_coverage():
    from blueprint.audit import BlueprintAuditor

    return BlueprintAuditor(runtime).coverage()

@app.post("/api/diagnostics/self-test")
async def api_diagnostics_self_test(data: dict = Body(default={})):
    from blueprint.audit import BlueprintAuditor
    from memory.indexer import index_status
    from utils.system_diagnostics import (
        build_report,
        check_directory,
        check_model_directory,
        probe_ollama,
        probe_remote_provider,
    )

    scope = str(data.get("scope", "full"))
    if scope not in {"full", "model", "data"}:
        scope = "full"
    checks = []

    if scope == "full":
        runtime_status = runtime.get_runtime_status()
        tools = int(runtime_status.get("tools", 0) or 0)
        subsystems = runtime_status.get("subsystems", [])
        checks.append({
            "id": "runtime",
            "label": "Python 运行时",
            "status": "pass" if tools > 0 and len(subsystems) >= 3 else "fail",
            "message": f"{tools} 个工具，{len(subsystems)} 个核心子系统已挂载",
            "action": "restart_runtime" if tools <= 0 else "",
        })
        coverage = BlueprintAuditor(runtime).coverage()
        score = float(coverage.get("score", 0) or 0)
        checks.append({
            "id": "blueprint",
            "label": "五大系统",
            "status": "pass" if score >= 0.8 else "warn",
            "message": f"蓝图覆盖率 {round(score * 100)}%",
        })
        from voice.stt import get_diagnostics as get_stt_diagnostics
        from voice.tts import get_diagnostics as get_tts_diagnostics

        voice_parts = {
            "采集": bool(get_capture_diagnostics().get("available")),
            "STT": bool(get_stt_diagnostics().get("available")),
            "TTS": bool(get_tts_diagnostics().get("available")),
        }
        unavailable = [name for name, available in voice_parts.items() if not available]
        checks.append({
            "id": "voice_components",
            "label": "语音组件",
            "status": "warn" if unavailable else "pass",
            "message": "组件已就绪" if not unavailable else f"未就绪：{'、'.join(unavailable)}",
        })

    paths = get_path_settings()
    if scope in {"full", "model"}:
        checks.append(check_model_directory(paths["model_dir"]))
        model_settings = get_model_connection_settings()
        local = model_settings["local"]
        remote = model_settings["remote"]
        local_check, remote_check = await asyncio.gather(
            asyncio.to_thread(
                probe_ollama,
                str(local.get("base_url", "http://127.0.0.1:11434/v1")),
                str(local.get("model") or ""),
            ),
            asyncio.to_thread(
                probe_remote_provider,
                str(remote.get("provider") or ""),
                str(remote.get("base_url") or ""),
                _get_api_key(str(remote.get("provider") or "")),
                str(remote.get("model") or ""),
            ),
        )
        checks.extend([local_check, remote_check])

    if scope in {"full", "data"}:
        store_status = runtime.get_runtime_status().get("event_store", {})
        checks.append({
            "id": "memory_store",
            "label": "长效记忆",
            "status": "pass" if runtime.event_store is not None else "fail",
            "message": f"事件库可用，已记录 {store_status.get('events', 0)} 个事件",
        })
        memory_index = index_status()
        checks.append({
            "id": "memory_index",
            "label": "记忆索引",
            "status": "warn" if memory_index.get("error") else "pass",
            "message": (
                str(memory_index.get("error"))
                if memory_index.get("error")
                else f"事实 {memory_index.get('facts', 0)}，经验 {memory_index.get('experiences', 0)}"
            ),
        })
        checks.extend([
            check_directory("workspace_dir", "项目工作区", paths["workspace_dir"], create=True, writable=True),
            check_directory("output_dir", "导入与输出", paths["output_dir"], create=True, writable=True),
            check_directory("backup_dir", "备份目录", paths["backup_dir"], create=True, writable=True),
        ])

    return build_report(checks, scope)

@app.post("/api/perception/ingest")
async def api_perception_ingest(data: dict = Body(...)):
    service = runtime.subsystems.get("perception")
    ingest = getattr(service, "ingest", None)
    if not callable(ingest):
        return {"ok": False, "error": "perception subsystem unavailable"}
    summary = str(data.get("summary", "")).strip()
    if not summary:
        return {"ok": False, "error": "summary is required"}
    event = ingest(
        source=str(data.get("source", "unknown")),
        modality=str(data.get("modality", "unknown")),
        summary=summary,
        confidence=data.get("confidence", 1.0),
        metadata=data.get("metadata", {}),
    )
    return {"ok": True, "event": event.to_dict()}

@app.post("/api/perception/yolo/detect")
async def api_perception_yolo_detect(data: dict = Body(...)):
    service = runtime.subsystems.get("perception")
    get_adapter = getattr(service, "get_adapter", None)
    adapter = get_adapter("yolo") if callable(get_adapter) else None
    if adapter is None:
        return {"ok": False, "error": "YOLO adapter unavailable"}
    image_path = str(data.get("image_path") or data.get("path") or "").strip()
    if not image_path:
        return {"ok": False, "error": "image_path is required"}
    try:
        resolved = _resolve_workspace_path(image_path)
        image = _load_perception_image(resolved)
        source = str(data.get("source") or _workspace_display_path(resolved))
        event = adapter.detect(
            image=image,
            perception=service,
            source=source,
            conf_threshold=float(data.get("conf_threshold", 0.25)),
            iou_threshold=float(data.get("iou_threshold", 0.45)),
        )
        return {"ok": True, "event": event.to_dict()}
    except Exception as e:
        logger.warning(f"YOLO 感知检测失败: {e}")
        return {"ok": False, "error": str(e)[:200]}

@app.post("/api/perception/image/analyze")
async def api_perception_image_analyze(data: dict = Body(...)):
    service = runtime.subsystems.get("perception")
    analyze_image = getattr(service, "analyze_image", None)
    if not callable(analyze_image):
        return {"ok": False, "error": "perception image pipeline unavailable"}
    image_path = str(data.get("image_path") or data.get("path") or "").strip()
    if not image_path:
        return {"ok": False, "error": "image_path is required"}
    try:
        resolved = _resolve_workspace_path(image_path)
        image = _load_perception_image(resolved)
        source = str(data.get("source") or _workspace_display_path(resolved))
        adapters = data.get("adapters") or ["ocr", "yolo"]
        if not isinstance(adapters, list):
            return {"ok": False, "error": "adapters must be a list"}
        events = analyze_image(
            image=image,
            source=source,
            adapter_names=[str(name) for name in adapters],
            adapter_options={
                "ocr": {
                    "min_confidence": float(data.get("ocr_min_confidence", 0.3)),
                },
                "yolo": {
                    "conf_threshold": float(data.get("conf_threshold", 0.25)),
                    "iou_threshold": float(data.get("iou_threshold", 0.45)),
                },
            },
        )
        return {"ok": True, "events": [event.to_dict() for event in events]}
    except Exception as e:
        logger.warning(f"本地图片感知分析失败: {e}")
        return {"ok": False, "error": str(e)[:200]}

@app.post("/api/perception/video/analyze")
async def api_perception_video_analyze(data: dict = Body(...)):
    service = runtime.subsystems.get("perception")
    if service is None:
        return {"ok": False, "error": "perception video pipeline unavailable"}
    video_path = str(data.get("video_path") or data.get("path") or "").strip()
    if not video_path:
        return {"ok": False, "error": "video_path is required"}
    try:
        resolved = _resolve_workspace_path(video_path)
        adapters = data.get("adapters") or ["ocr", "yolo"]
        if not isinstance(adapters, list):
            return {"ok": False, "error": "adapters must be a list"}
        analyzer = VideoStreamAnalyzer(
            perception=service,
            change_threshold=float(data.get("change_threshold", 0.08)),
            segment_seconds=float(data.get("segment_seconds", 8.0)),
            max_frames=int(data.get("max_frames", 600)),
        )
        result = analyzer.analyze_frames(
            _iter_video_frames(resolved, sample_seconds=float(data.get("sample_seconds", 1.0))),
            source=str(data.get("source") or _workspace_display_path(resolved)),
            adapters=[str(name) for name in adapters],
            adapter_options={
                "ocr": {"min_confidence": float(data.get("ocr_min_confidence", 0.3))},
                "yolo": {
                    "conf_threshold": float(data.get("conf_threshold", 0.25)),
                    "iou_threshold": float(data.get("iou_threshold", 0.45)),
                },
            },
        )
        return result
    except Exception as e:
        logger.warning(f"视频感知分析失败: {e}")
        return {"ok": False, "error": str(e)[:200]}

@app.post("/api/perception/screen/analyze")
async def api_perception_screen_analyze(data: dict = Body(...)):
    service = runtime.subsystems.get("perception")
    analyze_image = getattr(service, "analyze_image", None)
    if not callable(analyze_image):
        return {"ok": False, "error": "perception screen pipeline unavailable"}
    try:
        if data.get("image_bgra_base64"):
            image = _decode_perception_bgra(
                str(data["image_bgra_base64"]),
                int(data.get("width", 0)),
                int(data.get("height", 0)),
            )
        else:
            image = _capture_perception_screenshot(data.get("area"))
        source = str(data.get("source") or "screen")
        adapters = data.get("adapters") or ["ocr", "yolo"]
        if not isinstance(adapters, list):
            return {"ok": False, "error": "adapters must be a list"}
        events = analyze_image(
            image=image,
            source=source,
            adapter_names=[str(name) for name in adapters],
            adapter_options={
                "ocr": {
                    "min_confidence": float(data.get("ocr_min_confidence", 0.3)),
                },
                "yolo": {
                    "conf_threshold": float(data.get("conf_threshold", 0.25)),
                    "iou_threshold": float(data.get("iou_threshold", 0.45)),
                },
            },
        )
        return {"ok": True, "events": [event.to_dict() for event in events]}
    except Exception as e:
        logger.warning(f"屏幕感知分析失败: {e}")
        return {"ok": False, "error": str(e)[:200]}

@app.post("/api/perception/camera/analyze")
async def api_perception_camera_analyze(data: dict = Body(...)):
    service = runtime.subsystems.get("perception")
    analyze_image = getattr(service, "analyze_image", None)
    if not callable(analyze_image):
        return {"ok": False, "error": "perception camera pipeline unavailable"}
    try:
        device_id = int(data.get("device_id", 0))
        image = _capture_perception_camera(device_id)
        source = str(data.get("source") or f"camera:{device_id}")
        adapters = data.get("adapters") or ["ocr", "yolo"]
        if not isinstance(adapters, list):
            return {"ok": False, "error": "adapters must be a list"}
        events = analyze_image(
            image=image,
            source=source,
            adapter_names=[str(name) for name in adapters],
            adapter_options={
                "ocr": {
                    "min_confidence": float(data.get("ocr_min_confidence", 0.3)),
                },
                "yolo": {
                    "conf_threshold": float(data.get("conf_threshold", 0.25)),
                    "iou_threshold": float(data.get("iou_threshold", 0.45)),
                },
            },
        )
        return {"ok": True, "events": [event.to_dict() for event in events]}
    except Exception as e:
        logger.warning(f"摄像头感知分析失败: {e}")
        return {"ok": False, "error": str(e)[:200]}

@app.get("/api/engine/status")
async def api_engine_status():return engine.get_power_status()

@app.post("/api/engine/restore")
async def api_engine_restore():engine.restore_primary();return {"ok":True,"status":engine.get_power_status()}

@app.get("/api/brain/stats")
async def api_brain_stats():return brain.get_stats()

@app.get("/api/brain/facts")
async def api_brain_facts():return {"facts":[{"content":f.content[:80],"category":f.category,"confidence":round(f.confidence,2)} for f in brain._facts[-50:]]}

@app.get("/api/logs")
async def api_logs():return {"logs":action_log[-100:]}

@app.post("/api/logs/clear")
async def api_logs_clear():action_log.clear();return {"ok":True}

@app.get("/api/memory/conversations")
async def api_mem_list():return {"conversations":list_conversations()}

@app.get("/api/memory/events")
async def api_memory_events(type: str = "", limit: int = 50):
    store = getattr(runtime, "event_store", None)
    recent = getattr(store, "recent_events", None)
    if not callable(recent):
        return {"ok": False, "events": [], "error": "event store unavailable"}
    events = recent(limit=limit, event_type=type or None)
    return {"ok": True, "events": events, "count": len(events)}

@app.post("/api/memory/consolidate")
async def api_memory_consolidate(data: dict = Body(...)):
    store = getattr(runtime, "event_store", None)
    if store is None:
        return {"ok": False, "result": {}, "error": "event store unavailable"}
    try:
        from memory.consolidation import EventMemoryConsolidator

        result = EventMemoryConsolidator(store).consolidate(limit=int(data.get("limit", 500)))
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "result": {}, "error": str(e)[:200]}

@app.get("/api/memory/candidates")
async def api_memory_candidates(kind: str = "", status: str = "candidate", limit: int = 50):
    store = getattr(runtime, "event_store", None)
    candidates = getattr(store, "memory_candidates", None)
    if not callable(candidates):
        return {"ok": False, "candidates": [], "error": "memory candidates unavailable"}
    items = candidates(kind=kind or None, status=status or None, limit=limit)
    return {"ok": True, "candidates": items, "count": len(items)}

@app.get("/api/memory/recall")
async def api_memory_recall(q: str = "", limit: int = 10):
    store = getattr(runtime, "event_store", None)
    recall = getattr(store, "recall", None)
    if not callable(recall):
        return {"ok": False, "results": [], "error": "memory recall unavailable"}
    results = recall(q, limit=limit)
    return {"ok": True, "query": q, "results": results, "count": len(results)}

@app.post("/api/memory/candidates/status")
async def api_memory_candidate_status(data: dict = Body(...)):
    store = getattr(runtime, "event_store", None)
    update = getattr(store, "update_memory_candidate_status", None)
    if not callable(update):
        return {"ok": False, "error": "memory candidates unavailable"}
    candidate_id = str(data.get("candidate_id", "")).strip()
    status = str(data.get("status", "")).strip()
    if not candidate_id:
        return {"ok": False, "error": "candidate_id is required"}
    updated = update(candidate_id, status)
    if not updated:
        return {"ok": False, "error": "candidate not found or status invalid"}
    runtime.event_bus.publish(
        "memory.candidate.status_changed",
        {"candidate_id": candidate_id, "status": status},
        source="memory",
    )
    return {"ok": True, "candidate_id": candidate_id, "status": status}

@app.post("/api/memory/apply-active")
async def api_memory_apply_active(data: dict = Body(...)):
    try:
        from memory.activation import ActiveMemoryApplier

        result = ActiveMemoryApplier(runtime).apply(limit=int(data.get("limit", 100)))
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "result": {}, "error": str(e)[:200]}

@app.post("/api/memory/materialize-procedural")
async def api_memory_materialize_procedural(data: dict = Body(...)):
    try:
        from memory.procedural import PROCEDURAL_DIR
        from memory.procedural_materializer import ProceduralMemoryMaterializer

        output_dir = Path(data.get("output_dir") or PROCEDURAL_DIR)
        result = ProceduralMemoryMaterializer(runtime.event_store, output_dir).materialize(
            limit=int(data.get("limit", 100))
        )
        runtime.event_bus.publish("memory.procedural.materialized", result, source="memory")
        return {"ok": True, "result": result, "output_dir": str(output_dir)}
    except Exception as e:
        return {"ok": False, "result": {}, "error": str(e)[:200]}

@app.post("/api/evolution/review")
async def api_evolution_review(data: dict = Body(...)):
    service = runtime.subsystems.get("evolution")
    review = getattr(service, "review", None)
    if not callable(review):
        return {"ok": False, "result": {}, "error": "evolution service unavailable"}
    try:
        result = review(limit=int(data.get("limit", 500)))
        runtime.event_bus.publish("evolution.review.completed", result, source="evolution")
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "result": {}, "error": str(e)[:200]}

@app.get("/api/evolution/candidates")
async def api_evolution_candidates(kind: str = "", status: str = "candidate", limit: int = 50):
    store = getattr(runtime, "event_store", None)
    candidates = getattr(store, "evolution_candidates", None)
    if not callable(candidates):
        return {"ok": False, "candidates": [], "error": "evolution candidates unavailable"}
    items = candidates(kind=kind or None, status=status or None, limit=limit)
    return {"ok": True, "candidates": items, "count": len(items)}

@app.post("/api/evolution/candidates/status")
async def api_evolution_candidate_status(data: dict = Body(...)):
    store = getattr(runtime, "event_store", None)
    update = getattr(store, "update_evolution_candidate_status", None)
    if not callable(update):
        return {"ok": False, "error": "evolution candidates unavailable"}
    candidate_id = str(data.get("candidate_id", "")).strip()
    status = str(data.get("status", "")).strip()
    if not candidate_id:
        return {"ok": False, "error": "candidate_id is required"}
    updated = update(candidate_id, status)
    if not updated:
        return {"ok": False, "error": "candidate not found or status transition invalid"}
    runtime.event_bus.publish(
        "evolution.candidate.status_changed",
        {"candidate_id": candidate_id, "status": status},
        source="evolution",
    )
    return {"ok": True, "candidate_id": candidate_id, "status": status}

@app.post("/api/evolution/candidates/validate")
async def api_evolution_candidate_validate(data: dict = Body(...)):
    store = getattr(runtime, "event_store", None)
    validate = getattr(store, "validate_evolution_candidate", None)
    if not callable(validate):
        return {"ok": False, "error": "evolution validation unavailable"}
    candidate_id = str(data.get("candidate_id", "")).strip()
    if not candidate_id:
        return {"ok": False, "error": "candidate_id is required"}
    result = validate(candidate_id)
    runtime.event_bus.publish(
        "evolution.candidate.validated",
        {"candidate_id": candidate_id, "ok": result.get("ok"), "status": result.get("status")},
        source="evolution",
    )
    return {"candidate_id": candidate_id, **result}

@app.post("/api/evolution/candidates/performance")
async def api_evolution_candidate_performance(data: dict = Body(...)):
    store = getattr(runtime, "event_store", None)
    record = getattr(store, "record_evolution_performance", None)
    if not callable(record):
        return {"ok": False, "error": "evolution performance tracking unavailable"}
    candidate_id = str(data.get("candidate_id", "")).strip()
    if not candidate_id:
        return {"ok": False, "error": "candidate_id is required"}
    result = record(
        candidate_id,
        success=bool(data.get("success", False)),
        latency_ms=data.get("latency_ms"),
        quality_score=data.get("quality_score"),
        window=int(data.get("window", 5)),
        max_failure_rate=float(data.get("max_failure_rate", 0.5)),
        max_latency_ms=data.get("max_latency_ms"),
        min_quality_score=data.get("min_quality_score"),
    )
    runtime.event_bus.publish(
        "evolution.candidate.performance",
        {
            "candidate_id": candidate_id,
            "ok": result.get("ok"),
            "rolled_back": result.get("rolled_back", False),
            "failure_rate": result.get("failure_rate"),
        },
        source="evolution",
    )
    return result

@app.get("/api/memory/conversations/{sid}")
async def api_mem_get(sid:str):return {"id":sid,"cards":load_conversation(sid)}

@app.post("/api/memory/conversations/{sid}")
async def api_mem_save(sid:str,data:dict):save_conversation(sid,data.get("cards",[]),name=data.get("name",""));return {"ok":True}

@app.post("/api/memory/conversations/{sid}/rename")
async def api_mem_rename(sid:str,data:dict):
    name=data.get("name","")
    import json as _json, pathlib
    ip=pathlib.Path(__file__).parent/"memory"/"index.json"
    if ip.exists():
        try:
            idx=_json.loads(ip.read_text(encoding="utf-8"))
            for c in idx.get("conversations",[]):
                if c["id"]==sid: c["name"]=name;break
            ip.write_text(_json.dumps(idx,ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception as e:
            logger.warning(f"会话重命名失败: {e}")
            return {"ok": False, "error": str(e)[:200]}
    return {"ok":True,"name":name}

@app.delete("/api/memory/conversations/{sid}")
async def api_mem_del(sid:str):delete_conversation(sid);return {"ok":True}

# ═══════════════════════════════════════════════════════════════
# P0-10: FTS5 全文搜索 + 索引管理 API
# ═══════════════════════════════════════════════════════════════

@app.get("/api/memory/search")
async def api_memory_search(q: str = "", type: str = "facts", limit: int = 20):
    """FTS5 全文搜索记忆库: facts | episodes | all"""
    if not q or len(q) < 2:
        return {"ok": False, "results": [], "error": "搜索词至少2个字符"}
    try:
        from memory.indexer import search_facts, search_episodes, ensure_index, index_status
        ensure_index()
        results = []
        if type in ("facts", "all"):
            r = search_facts(q, limit=limit)
            for row in r:
                results.append({
                    "type": "fact",
                    "content": row.get("content", "")[:200],
                    "category": row.get("category", ""),
                    "confidence": row.get("confidence", 0),
                    "priority": row.get("priority", 1),
                })
        if type in ("episodes", "all"):
            r = search_episodes(q, limit=limit)
            for row in r:
                results.append({
                    "type": "episode",
                    "content": row.get("user_input", "")[:200],
                    "domain": row.get("fingerprint_domain", ""),
                    "outcome": row.get("outcome", ""),
                    "tool_count": row.get("tool_count", 0),
                })
        return {"ok": True, "results": results[:limit], "count": len(results[:limit])}
    except Exception as e:
        logger.warning(f"FTS5 搜索失败: {e}")
        return {"ok": False, "results": [], "error": str(e)[:200]}

@app.get("/api/memory/index/status")
async def api_memory_index_status():
    """记忆索引状态"""
    try:
        from memory.indexer import index_status
        return {"ok": True, "status": index_status()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

@app.post("/api/memory/index/rebuild")
async def api_memory_index_rebuild():
    """重建记忆索引"""
    try:
        from memory.indexer import rebuild_index
        rebuild_index()
        from memory.indexer import index_status
        return {"ok": True, "status": index_status()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

@app.get("/api/memory/search/ui")
async def api_memory_search_ui():
    """记忆搜索 UI 配置"""
    try:
        from memory.indexer import index_status
        status = index_status()
    except:
        status = {"error": "索引不可用"}
    return {
        "ok": True,
        "index_status": status,
        "search_types": [
            {"id": "facts", "label": "知识事实", "desc": "搜索学习到的知识和事实"},
            {"id": "episodes", "label": "执行记录", "desc": "搜索历史执行任务"},
            {"id": "all", "label": "全部", "desc": "搜索所有记忆类型"},
        ],
    }

@app.get("/api/skills")
async def api_skills():_discover();return {"skills":SKILL_LIST,"current":CURRENT_SKILL,"count":registry.count}

@app.post("/api/skills/activate")
async def api_activate_skill(data: dict = Body(...)):
    sid = data.get("skill", "全功能")
    if sid not in [s["id"] for s in SKILL_LIST]:
        return {"applied": False, "error": "未知"}
    c = _load_skill(sid)
    agent.tools = registry
    return {"applied": True, "skill": CURRENT_SKILL, "count": c}

@app.post("/api/config/provider")
async def a1(d:dict):
    try:r=set_provider(d.get("provider","local"));llm.reload();return {**r,"applied":True}
    except Exception as e:return {"applied":False,"error":str(e)[:100]}

@app.post("/api/config/apikey")
async def a2(d:dict):
    p=d.get("provider","openai");k=d.get("api_key","")
    if not k:return {"applied":False,"error":"Key不能为空"}
    try:r=set_api_key(p,k);llm.reload();return {**r,"applied":True}
    except Exception as e:return {"applied":False,"error":str(e)[:100]}

@app.post("/api/config/model")
async def api_set_model(d: dict):
    try:
        r = set_model_name(d.get("provider", "local"), d.get("model", ""))
        llm.reload()
        return {**r, "applied": True}
    except Exception as e:
        return {"applied": False, "error": str(e)[:100]}

@app.get("/api/config/models")
async def api_get_model_connections():
    return get_model_connection_settings()

@app.post("/api/config/models")
async def api_set_model_connections(d: dict = Body(...)):
    result = set_model_connection_settings(d)
    if result.get("applied"):
        llm.reload()
    return result

@app.post("/api/config/models/remote")
async def api_get_remote_models(d: dict = Body(default={})):
    return await asyncio.to_thread(
        discover_remote_provider_models,
        str(d.get("provider") or ""),
        str(d.get("base_url") or ""),
        str(d.get("api_key") or ""),
    )

@app.post("/api/config/models/local")
async def api_get_local_models(d: dict = Body(default={})):
    from utils.system_diagnostics import get_ollama_models
    from utils.model_installer import get_local_runtime_state

    settings = get_model_connection_settings()
    base_url = str(
        d.get("base_url")
        or settings.get("local", {}).get("base_url")
        or "http://127.0.0.1:11435/v1"
    )
    try:
        models = await asyncio.to_thread(get_ollama_models, base_url)
        return {
            "connected": True,
            "state": "connected",
            "models": models,
            "message": f"Ollama 在线，发现 {len(models)} 个模型",
        }
    except Exception as error:
        runtime = get_local_runtime_state()
        managed_url = ":11435" in base_url
        if managed_url and not runtime["installed"]:
            return {
                "connected": False,
                "state": "not_installed",
                "models": [],
                "message": "尚未安装 Javis 本地模型；请点击“安装或导入模型”完成设置",
            }
        return {
            "connected": False,
            "state": "offline",
            "models": [],
            "message": f"Ollama 无法连接：{str(error)[:120]}",
        }

@app.get("/api/config/models/install/search")
async def api_search_installable_models(q: str = "", limit: int = 12):
    from utils.model_installer import search_huggingface_models

    try:
        return await asyncio.to_thread(search_huggingface_models, q, limit)
    except Exception as error:
        return {"ok": False, "models": [], "error": str(error)[:300]}

@app.post("/api/config/models/install/detect")
async def api_detect_model_addon(d: dict = Body(...)):
    from utils.model_installer import detect_model_addon

    return await asyncio.to_thread(detect_model_addon, str(d.get("path") or ""))

@app.get("/api/config/models/install/progress")
async def api_get_model_install_progress():
    from utils.model_installer import get_model_install_progress

    return get_model_install_progress()


@app.post("/api/config/models/install/pause")
async def api_pause_model_install(d: dict = Body(...)):
    from utils.model_installer import pause_model_install

    return pause_model_install(str(d.get("job_id") or ""))


@app.post("/api/config/models/install/resume")
async def api_resume_model_install(d: dict = Body(...)):
    from utils.model_installer import resume_model_install

    return resume_model_install(str(d.get("job_id") or ""))


@app.post("/api/config/models/install/cancel")
async def api_cancel_model_install(d: dict = Body(...)):
    from utils.model_installer import cancel_model_install

    return cancel_model_install(str(d.get("job_id") or ""))

@app.post("/api/config/models/install/plan")
async def api_plan_model_install(d: dict = Body(...)):
    from utils.model_installer import plan_model_install

    try:
        return await asyncio.to_thread(plan_model_install, d)
    except Exception as error:
        return {"ok": False, "error": str(error)[:300]}

@app.post("/api/config/models/install")
async def api_install_model(d: dict = Body(...)):
    from utils.model_installer import ModelInstallCancelled, install_model

    try:
        return await asyncio.to_thread(install_model, d)
    except PermissionError as error:
        return {"ok": False, "error": str(error)[:300], "approval_required": True}
    except ModelInstallCancelled as error:
        return {"ok": False, "error": str(error)[:300], "cancelled": True}
    except Exception as error:
        return {"ok": False, "error": str(error)[:300]}

@app.get("/api/config/paths")
async def api_get_paths():
    return {"applied": True, "paths": get_path_settings()}

@app.post("/api/config/paths")
async def api_set_paths(d: dict = Body(...)):
    values = d.get("paths", d)
    try:
        return set_path_settings(values)
    except Exception as e:
        return {"applied": False, "error": str(e)[:200]}

@app.get("/api/config/effort")
async def api_get_effort():
    level = get_effort()
    info = EFFORT_LEVELS.get(level, EFFORT_LEVELS["balanced"])
    return {"effort": level, "label": info["label"], "desc": info["desc"], "temperature": info["temperature"], "max_tokens": info["max_tokens"]}

@app.post("/api/config/effort")
async def api_set_effort(d: dict):
    level = d.get("effort", "balanced")
    try:
        r = set_effort(level)
        llm.reload()
        return r
    except Exception as e:
        return {"applied": False, "error": str(e)[:100]}

# ── 权限级别 API ──

@app.get("/api/config/permission")
async def api_get_permission():
    perm = get_permission_level()
    info = PERMISSION_LEVELS.get(perm, PERMISSION_LEVELS["quick_auth"])
    return {
        "permission": perm,
        "label": info["label"],
        "icon": info["icon"],
        "desc": info["desc"],
        "color": info["color"],
        "levels": {k: {"label": v["label"], "icon": v["icon"], "desc": v["desc"], "color": v["color"]}
                   for k, v in PERMISSION_LEVELS.items()},
    }

@app.post("/api/config/permission")
async def api_set_permission(d: dict):
    level = d.get("permission", "quick_auth")
    try:
        r = set_permission_level(level)
        runtime.sync_permission(level)
        return r
    except Exception as e:
        return {"applied": False, "error": str(e)[:100]}

# 兼容旧版 /api/config/mode（前端可能还引用）
@app.get("/api/config/mode")
async def api_get_mode_legacy():
    e = get_effort()
    info = EFFORT_LEVELS.get(e, EFFORT_LEVELS["balanced"])
    return {"mode": e, "label": info["label"], "desc": info["desc"]}

app.mount("/static",StaticFiles(directory=str(ROOT/"web")),name="static")

ROOT_RESOLVED = ROOT.resolve()

def _configured_workspace_root() -> Path:
    return Path(get_path_settings()["workspace_dir"]).resolve()

def _workspace_display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT_RESOLVED))
    except ValueError:
        return str(resolved)

def _resolve_workspace_path(path: str | Path) -> Path:
    supplied = Path(path)
    candidate = supplied.resolve() if supplied.is_absolute() else (ROOT / supplied).resolve()
    for allowed_root in (ROOT_RESOLVED, _configured_workspace_root()):
        try:
            candidate.relative_to(allowed_root)
            return candidate
        except ValueError:
            continue
    raise ValueError("路径不在 Javis 或已授权工作区内")

def _load_perception_image(path: str | Path):
    resolved = Path(path)
    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"图片不存在: {resolved}")
    cv2 = __import__("cv2")
    image = cv2.imread(str(resolved))
    if image is None:
        raise ValueError(f"无法读取图片: {resolved}")
    return image

def _capture_perception_screenshot(area: list | None = None):
    from tools.desktop import screenshot

    result = screenshot(area=area)
    if not result.success or not result.image:
        raise RuntimeError(result.error or result.data or "screenshot failed")
    return _decode_perception_image_base64(result.image)

def _capture_perception_camera(device_id: int = 0):
    from tools.camera import camera_snapshot

    result = camera_snapshot(device_id=device_id)
    if not result.success or not result.image:
        raise RuntimeError(result.error or result.data or "camera capture failed")
    return _decode_perception_image_base64(result.image)

def _decode_perception_image_base64(image_base64: str):
    import base64
    import numpy as np

    cv2 = __import__("cv2")
    raw = base64.b64decode(image_base64)
    data = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码截图图像")
    return image

def _decode_perception_bgra(image_base64: str, width: int, height: int):
    import base64
    import numpy as np

    if width <= 0 or height <= 0 or width * height > 33_177_600:
        raise ValueError("屏幕图像尺寸无效")
    raw = base64.b64decode(image_base64, validate=True)
    expected = width * height * 4
    if len(raw) != expected:
        raise ValueError(f"屏幕像素长度无效: {len(raw)} != {expected}")
    bgra = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))
    return bgra[:, :, :3].copy()

def _iter_video_frames(path: str | Path, sample_seconds: float = 1.0):
    cv2 = __import__("cv2")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"无法打开视频: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(float(sample_seconds or 1.0) * fps))
    index = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % step == 0:
                yield index / fps, frame
            index += 1
    finally:
        cap.release()

def _validate_project_name(name: str) -> str:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("项目名不能包含路径分隔符")
    return name

# ═══════════════════════════════════════════════════════════════
# ★ WORKSPACE API — 工作台: 终端/文件/项目/GitHub/浏览器 ★
# ═══════════════════════════════════════════════════════════════

@app.post("/api/workspace/terminal")
async def api_terminal_exec(data: dict = Body(...)):
    """Execute a terminal command through the audited control subsystem."""
    cmd = data.get("command", "").strip()
    if not cmd:
        return {"ok": False, "output": "命令不能为空", "exit_code": -1}
    return command_task_runner.execute(
        command=cmd,
        shell=data.get("shell", "cmd"),
        timeout=data.get("timeout", 15),
        cwd=data.get("cwd") or data.get("path") or ROOT,
        root_token=data.get("root_token") or data.get("token") or "",
    )

@app.get("/api/control/commands")
async def api_control_commands(limit: int = 50):
    return {"ok": True, "tasks": command_task_runner.recent_tasks(limit)}

@app.post("/api/control/commands/start")
async def api_control_command_start(data: dict = Body(...)):
    return command_task_runner.start_command(
        command=str(data.get("command", "")).strip(),
        shell=data.get("shell", "cmd"),
        timeout=data.get("timeout", 15),
        cwd=data.get("cwd") or data.get("path") or ROOT,
        root_token=data.get("root_token") or data.get("token") or "",
    )

@app.get("/api/control/commands/{task_id}")
async def api_control_command_get(task_id: str):
    return command_task_runner.get_task(task_id)

@app.post("/api/control/commands/{task_id}/cancel")
async def api_control_command_cancel(task_id: str):
    return command_task_runner.cancel_task(task_id)

@app.post("/api/control/rollback/restore")
async def api_control_rollback_restore(data: dict = Body(...)):
    rollback_id = str(data.get("rollback_id", "") or data.get("id", "")).strip()
    if not rollback_id:
        return {"ok": False, "error": "rollback_id is required"}
    return command_task_runner.restore_rollback_point(rollback_id)

@app.post("/api/control/root-token")
async def api_control_root_token(data: dict = Body(...)):
    return command_task_runner.issue_root_token(
        reason=str(data.get("reason", "") or "api root session"),
        ttl_sec=int(data.get("ttl_sec", 300)),
    )

@app.post("/api/control/fuse/trip")
async def api_control_fuse_trip(data: dict = Body(...)):
    return command_task_runner.trip_fuse(str(data.get("reason", "") or "manual fuse tripped"))

@app.post("/api/control/fuse/reset")
async def api_control_fuse_reset():
    return command_task_runner.reset_fuse()

@app.get("/api/workspace/explore")
async def api_workspace_explore(path: str = "."):
    """浏览文件目录 (文件树)"""
    import os, stat
    try:
        base = _resolve_workspace_path(path)
        if not base.exists() or not base.is_dir():
            return {"ok": False, "error": f"目录不存在: {path}"}
        entries = []
        for f in sorted(base.iterdir()):
            try:
                is_dir = f.is_dir()
                st = f.stat()
                entries.append({
                    "name": f.name, "path": _workspace_display_path(f),
                    "is_dir": is_dir, "size": st.st_size if not is_dir else 0,
                    "modified": st.st_mtime,
                })
            except: pass
        # 快速访问 (常用目录)
        quick = []
        if ROOT.name in str(base):
            for d in ["core", "tools_lib", "skills", "utils", "web", "knowledge", "brain_data"]:
                p = ROOT / d
                if p.exists(): quick.append({"name": d, "path": d, "is_dir": True})
        return {"ok": True, "path": str(base), "entries": entries[:200], "quick": quick}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/workspace/read")
async def api_workspace_read(path: str = ""):
    """读取文件内容"""
    if not path: return {"ok": False, "error": "路径为空"}
    try:
        fp = _resolve_workspace_path(path)
        if not fp.exists() or not fp.is_file():
            return {"ok": False, "error": f"文件不存在: {path}"}
        ext = fp.suffix.lower()
        binary_exts = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.bmp', '.exe', '.dll', '.zip', '.7z', '.pdf'}
        if ext in binary_exts:
            return {"ok": True, "binary": True, "name": fp.name, "size": fp.stat().st_size, "modified": fp.stat().st_mtime}
        content = fp.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "content": content[:50000], "name": fp.name, "size": len(content), "modified": fp.stat().st_mtime}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/workspace/save")
async def api_workspace_save(data: dict = Body(...)):
    """保存文件"""
    path = data.get("path", "")
    content = data.get("content", "")
    if not path: return {"ok": False, "error": "路径为空"}
    try:
        fp = _resolve_workspace_path(path)
        expected_modified = data.get("expected_modified")
        if expected_modified is not None and fp.exists():
            actual_modified = fp.stat().st_mtime
            if abs(float(expected_modified) - actual_modified) > 0.000001:
                return {
                    "ok": False,
                    "conflict": True,
                    "error": "文件已在外部修改，请重新加载后再保存",
                    "modified": actual_modified,
                }
        from core.workspace_manager import sandbox_check_path
        sandbox_check_path(fp)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        try:
            result_path = _workspace_display_path(fp)
        except ValueError:
            result_path = str(fp)
        return {"ok": True, "path": result_path, "size": len(content), "modified": fp.stat().st_mtime}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/workspace/project")
async def api_workspace_project(data: dict = Body(...)):
    """创建新项目"""
    action = data.get("action", "create")
    name = data.get("name", "").strip()
    project_type = data.get("type", "empty")
    source_path = data.get("source_path", "")

    projects_dir = Path(get_path_settings()["workspace_dir"]).resolve() / "projects"

    if action == "list":
        projects_dir.mkdir(parents=True, exist_ok=True)
        projects = []
        for p in sorted(projects_dir.iterdir()):
            if p.is_dir():
                files = list(p.rglob("*"))[:20]
                projects.append({
                    "name": p.name, "path": _workspace_display_path(p),
                    "file_count": len(files),
                    "created": p.stat().st_ctime,
                })
        return {"ok": True, "projects": projects, "projects_dir": str(projects_dir)}

    if not name: return {"ok": False, "error": "项目名不能为空"}
    try:
        name = _validate_project_name(name)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if action == "from_folder":
        if not source_path: return {"ok": False, "error": "请选择源文件夹"}
        src = Path(source_path)
        if not src.exists(): return {"ok": False, "error": f"源文件夹不存在: {source_path}"}
        dst = (projects_dir / name).resolve()
        if dst.exists(): return {"ok": False, "error": "项目名已存在"}
        import shutil
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return {"ok": True, "path": _workspace_display_path(dst), "type": "from_folder"}

    if action == "create":
        dst = (projects_dir / name).resolve()
        if dst.exists(): return {"ok": False, "error": "项目名已存在"}
        dst.mkdir(parents=True)
        if project_type == "python":
            (dst / "main.py").write_text(f'"""\n{name}\n"""\n\n\ndef main():\n    print("Hello from {name}")\n\n\nif __name__ == "__main__":\n    main()\n', encoding="utf-8")
        elif project_type == "html":
            (dst / "index.html").write_text(f'<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<title>{name}</title>\n</head>\n<body>\n<h1>{name}</h1>\n</body>\n</html>\n', encoding="utf-8")
        elif project_type == "node":
            (dst / "index.js").write_text(f'// {name}\nconsole.log("Hello from {name}");\n', encoding="utf-8")
        (dst / ".gitkeep").write_text("")
        return {"ok": True, "path": _workspace_display_path(dst), "type": project_type}

    return {"ok": False, "error": f"未知操作: {action}"}

@app.get("/api/workspace/github")
async def api_workspace_github():
    """检查 GitHub CLI 状态"""
    import subprocess, shutil
    result = {"available": False, "version": "", "auth": False, "user": ""}
    try:
        r = subprocess.run(["where", "gh"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            result["available"] = True
            result["path"] = r.stdout.strip().split("\n")[0]
            vr = subprocess.run(["gh", "--version"], capture_output=True, text=True, timeout=5)
            result["version"] = vr.stdout.strip()[:80] if vr.returncode == 0 else ""
            ar = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
            result["auth"] = ar.returncode == 0
            if ar.returncode == 0 and ar.stdout:
                for line in ar.stdout.split("\n"):
                    if "github.com" in line and "Logged in" in line:
                        # gh outputs: "✓ Logged in to github.com account USERNAME (keyring)"
                        # or: "✓ Logged in to github.com as USER"
                        parts = line
                        for sep in ["account ", " as "]:
                            if sep in parts:
                                result["user"] = parts.split(sep)[-1].split()[0].split("(")[0].strip()
                                break
                        break
    except: pass
    # 常用仓库链接
    result["links"] = [
        {"name": "JARVIS 官方", "url": "https://github.com/Javis/Javis"},
        {"name": "DeepSeek", "url": "https://github.com/deepseek-ai"},
        {"name": "Anthropic", "url": "https://github.com/anthropics"},
    ]
    return result

# ═══ END WORKSPACE API ═══
if __name__=="__main__":
    import uvicorn,yaml
    try:
        with (ROOT/"config.yaml").open(encoding="utf-8") as f:
            cfg=yaml.safe_load(f)
    except yaml.YAMLError as e:logger.warning(f"config.yaml 解析异常: {e}");cfg={}
    except FileNotFoundError:cfg={};logger.info("使用默认配置")
    sc=cfg.get("server",{});h=sc.get("host","127.0.0.1");p=int(os.environ.get("PORT", sc.get("port", 8087)))
    print(f"JARVIS http://{h}:{p}  {llm.model}  {registry.count}工具")
    try:__import__('asyncio').run(__import__('voice.tts',fromlist=['']).preload_phrases())
    except ImportError:logger.debug("TTS 模块未安装，跳过语音预加载")
    except Exception as e:logger.warning(f"语音预加载失败: {e}")
    from voice.tts import _trim_cache
    try:_trim_cache()
    except Exception:pass
    uvicorn.run(app,host=h,port=p,log_level="info")
