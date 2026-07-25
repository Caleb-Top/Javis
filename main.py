"""JARVIS Web 版入口"""
import os,sys,json,logging,asyncio
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
from control.command_tasks import CommandTaskRunner
from evolution.service import EvolutionService
from memory.session_db import SessionEventStore
from perception.adapters.ocr import OcrAdapter
from perception.adapters.vlm import LocalVlmAdapter, OpenAICompatibleVlmDescriber
from perception.adapters.yolo import YoloDetectionAdapter
from perception.service import PerceptionService
from perception.video import VideoStreamAnalyzer

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
app=FastAPI(title="JARVIS",version="2.0")

@app.get("/")
async def root():return FileResponse(str(ROOT/"web"/"index.html"))

@app.get("/favicon.ico")
async def favicon():return FileResponse(str(ROOT/"web"/"favicon.ico"))

def _save_uploaded_file_for_ws(path: str, content: str) -> Path:
    safe = Path(path or "").name
    if not safe or safe.startswith("."):
        safe = "uploaded_file.txt"
    uploads_root = (ROOT / "uploads").resolve()
    full = (uploads_root / safe).resolve()
    if not str(full).startswith(str(uploads_root)):
        raise ValueError("upload path escapes uploads directory")
    uploads_root.mkdir(parents=True, exist_ok=True)
    full.write_text((content or "")[:100000], encoding="utf-8")
    return full

@app.websocket("/ws")
async def ws(ws:WebSocket):
    await ws.accept()
    async def _agent_loop(text: str, session_id: str = "", cards: list | None = None):
        """并发运行 agent, 同时监听 WS 消息 (解决 confirm 死锁)"""
        q = asyncio.Queue()
        async def _run():
            try:
                async for msg in agent.chat(text, session_id=session_id, conversation_cards=cards or []):
                    await q.put(msg)
            finally:
                await q.put(None)
        task = asyncio.create_task(_run())
        running = True
        while running:
            gq = asyncio.create_task(q.get())
            rw = asyncio.create_task(ws.receive_text())
            done, pend = await asyncio.wait([gq, rw], return_when=asyncio.FIRST_COMPLETED)
            for t in pend: t.cancel()
            for t in done:
                try: r = t.result()
                except: continue
                if t == gq:
                    if r is None: running = False
                    else: await ws.send_json(r)
                else:
                    m2=json.loads(r);t2=m2.get("type","")
                    if t2=="confirm" and hasattr(agent,'resolve_confirm'):
                        agent.resolve_confirm(m2.get("payload",{}).get("confirmed",False))
                    elif t2=="permission_change":
                        perm = m2.get("payload",{}).get("permission","quick_auth")
                        try:
                            r = set_permission_level(perm)
                            runtime.sync_permission(perm)
                        except: pass
                    elif t2=="ping":
                        await ws.send_json({"type":"pong","tools":registry.count,"model":llm.model})
    try:
        while True:
            d=await ws.receive_text();m=json.loads(d);t=m.get("type","message")
            if t=="message":
                payload = m.get("payload",{})
                u=payload.get("text","").strip()
                if not u:continue
                await _agent_loop(
                    u,
                    session_id=str(payload.get("session_id","") or ""),
                    cards=payload.get("recent_cards",[]) if isinstance(payload.get("recent_cards",[]), list) else [],
                )
                continue
            elif t=="folder_file":
                p=m.get("payload",{}); path=p.get("path",""); content=p.get("content","")
                if path:
                    try:
                        full = _save_uploaded_file_for_ws(path, content)
                        logger.info(f"📁 已保存上传文件: {full.name} ({len(content)}字符)")
                    except ValueError:
                        logger.warning(f'路径遍历拦截: {path}')
                        continue
            elif t=="voice":
                ab=m.get("payload",{}).get("audio","")
                if ab:
                    from voice.stt import transcribe;txt=transcribe(ab)
                    if txt:
                        await _agent_loop(txt)
            elif t=="confirm":
                confirmed=m.get("payload",{}).get("confirmed",False)
                if hasattr(agent,'resolve_confirm'):
                    agent.resolve_confirm(confirmed)
            elif t=="tool":
                tn=m.get("payload",{}).get("name","");tp=m.get("payload",{}).get("params",{})
                if tn:
                    r=await registry.execute(tn,tp)
                    await ws.send_json({"type":"tool_result","tool":tn,"success":r.success,"data":(r.data or r.error or "")[:500],"image":r.image or ""})
                    await ws.send_json({"type":"done"})
            elif t=="ping":await ws.send_json({"type":"pong","tools":registry.count,"model":llm.model})
    except WebSocketDisconnect:pass

from utils.config_api import get_status,set_api_key,set_provider,set_model_name,get_effort,set_effort,EFFORT_LEVELS,get_permission_level,set_permission_level,PERMISSION_LEVELS
from core.agent import action_log
from utils.memory import save_conversation,load_conversation,list_conversations,delete_conversation

@app.get("/api/status")
async def api_status():
    s=get_status();s["skill"]=CURRENT_SKILL;s["skill_count"]=registry.count;s["skills"]=SKILL_LIST;s["brain"]=brain.get_stats()
    try:s["engine"]=engine.get_power_status()
    except Exception as e:logger.debug(f"引擎状态获取异常: {e}")
    return s

@app.get("/api/runtime/status")
async def api_runtime_status():
    return runtime.get_runtime_status()

@app.get("/api/blueprint/coverage")
async def api_blueprint_coverage():
    from blueprint.audit import BlueprintAuditor

    return BlueprintAuditor(runtime).coverage()

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
        source = str(data.get("source") or resolved.relative_to(ROOT_RESOLVED))
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
        source = str(data.get("source") or resolved.relative_to(ROOT_RESOLVED))
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
            source=str(data.get("source") or resolved.relative_to(ROOT_RESOLVED)),
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
        except:pass
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

def _resolve_workspace_path(path: str | Path) -> Path:
    candidate = (ROOT / Path(path)).resolve()
    try:
        candidate.relative_to(ROOT_RESOLVED)
    except ValueError as exc:
        raise ValueError("路径不能超出 Javis 项目根目录") from exc
    return candidate

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
                    "name": f.name, "path": str(f.relative_to(ROOT)) if ROOT in f.parents else str(f),
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
            return {"ok": True, "binary": True, "name": fp.name, "size": fp.stat().st_size}
        content = fp.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "content": content[:50000], "name": fp.name, "size": len(content)}
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
        from core.workspace_manager import sandbox_check_path
        sandbox_check_path(fp)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(fp.relative_to(ROOT)), "size": len(content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/workspace/project")
async def api_workspace_project(data: dict = Body(...)):
    """创建新项目"""
    action = data.get("action", "create")
    name = data.get("name", "").strip()
    project_type = data.get("type", "empty")
    source_path = data.get("source_path", "")

    projects_dir = ROOT / "workspace" / "projects"

    if action == "list":
        projects_dir.mkdir(parents=True, exist_ok=True)
        projects = []
        for p in sorted(projects_dir.iterdir()):
            if p.is_dir():
                files = list(p.rglob("*"))[:20]
                projects.append({
                    "name": p.name, "path": str(p.relative_to(ROOT)),
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
        dst = _resolve_workspace_path(Path("workspace") / "projects" / name)
        if dst.exists(): return {"ok": False, "error": "项目名已存在"}
        import shutil
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return {"ok": True, "path": str(dst.relative_to(ROOT)), "type": "from_folder"}

    if action == "create":
        dst = _resolve_workspace_path(Path("workspace") / "projects" / name)
        if dst.exists(): return {"ok": False, "error": "项目名已存在"}
        dst.mkdir(parents=True)
        if project_type == "python":
            (dst / "main.py").write_text(f'"""\n{name}\n"""\n\n\ndef main():\n    print("Hello from {name}")\n\n\nif __name__ == "__main__":\n    main()\n', encoding="utf-8")
        elif project_type == "html":
            (dst / "index.html").write_text(f'<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<title>{name}</title>\n</head>\n<body>\n<h1>{name}</h1>\n</body>\n</html>\n', encoding="utf-8")
        elif project_type == "node":
            (dst / "index.js").write_text(f'// {name}\nconsole.log("Hello from {name}");\n', encoding="utf-8")
        (dst / ".gitkeep").write_text("")
        return {"ok": True, "path": str(dst.relative_to(ROOT)), "type": project_type}

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
    sc=cfg.get("server",{});h=sc.get("host","127.0.0.1");p=int(sc.get("port", os.environ.get("PORT", 8087)))
    print(f"JARVIS http://{h}:{p}  {llm.model}  {registry.count}工具")
    try:__import__('asyncio').run(__import__('voice.tts',fromlist=['']).preload_phrases())
    except ImportError:logger.debug("TTS 模块未安装，跳过语音预加载")
    except Exception as e:logger.warning(f"语音预加载失败: {e}")
    from voice.tts import _trim_cache
    try:_trim_cache()
    except Exception:pass
    uvicorn.run(app,host=h,port=p,log_level="info")
