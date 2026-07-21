"""JARVIS Web 版入口"""
import os,sys,json,logging,asyncio
sys.excepthook=lambda t,v,tb:print(f"FATAL: {t.__name__}: {v}",file=sys.stderr,flush=True)
from pathlib import Path
ROOT=Path(__file__).parent;sys.path.insert(0,str(ROOT))
logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger=logging.getLogger("jarvis")

# ── 工具路径初始化 (Tesseract, ImageMagick 等) ──
try:
    from tools.setup import setup as _tool_setup
    _r = _tool_setup()
    for _k,_v in _r.items():
        logger.info(f"工具初始化 {_k}: {_v}")
except Exception as _e:
    logger.warning(f"工具路径初始化跳过: {_e}")
from core.llm_client import LLMClient;from core.tool_registry import ToolRegistry,ToolDef;from core.agent import Agent
from core.engine import InferenceEngine,LOCAL_MODEL
from knowledge.brain import Brain;from knowledge.learner import Learner
from knowledge.papers_db import ingest_to_brain;from knowledge.human_knowledge import inject_to_brain as inject_human
brain=Brain();learner=Learner()
try:brain.cleanup()
except:pass
try:ingest_to_brain(brain);logger.info("论文知识已注入大脑")
except Exception as e:logger.warning(f"论文注入跳过:{e}")
try:n=inject_human(brain);logger.info(f"人类文明知识已注入:{n}条")
except Exception as e:logger.warning(f"人类知识注入跳过:{e}")
registry=ToolRegistry();llm=LLMClient(str(ROOT/"config.yaml"))
try:
    import tools.code_exec as _ce
    _ce.REGISTRY=registry
    _ce.set_brain(brain)
    logger.info("🔗 执行引擎已连接大脑和注册中心")
except Exception as e:
    logger.warning(f"执行引擎初始化跳过: {e}")
try:
    from tools_lib.tool_superpowers import inject_to_brain as _sp_inj
    _sp_inj(brain)
    logger.info("⚡ Superpowers 技能已注入大脑")
except Exception as _sp_e:
    logger.warning(f"Superpowers 注入跳过: {_sp_e}")
try:
    from tools_lib.tool_plugin_creator import inject_to_brain as _pc_inj
    _pc_inj(brain)
    logger.info("🧩 Plugin Creator 技能已注入大脑")
except Exception as _pc_e:
    logger.warning(f"Plugin Creator 注入跳过: {_pc_e}")
try:
    from tools_lib.tool_anthropic_plugins import inject_to_brain as _ap_inj
    _ap_inj(brain)
    logger.info("📦 Anthropic 插件库已注入大脑")
except Exception as _ap_e:
    logger.warning(f"Anthropic 插件注入跳过: {_ap_e}")
try:
    from tools_lib.tool_catch2 import inject_to_brain as _ct_inj
    _ct_inj(brain)
    logger.info("Catch2 C++ 测试已注入大脑")
except Exception as _ct_e:
    logger.warning(f"Catch2 注入跳过: {_ct_e}")
from tools.manifest import register_agent_tools;register_agent_tools(registry)
engine=InferenceEngine(llm)
import importlib,pkgutil;import skills as skills_pkg
SKILL_LIST=[];CURRENT_SKILL="全功能"
# ── 启动 Escape 键中断钩子 (全局键盘监听) ──
try:
    import threading
    from core.tray import _start_escape_hook
    _hook_thread = threading.Thread(target=_start_escape_hook, daemon=True)
    _hook_thread.start()
    logger.info("Escape 中断钩子已启动")
except Exception as e:
    logger.warning(f"Escape 钩子未启动: {e}")

def _load_skill(sid):
    global CURRENT_SKILL;registry.clear()
    try:m=importlib.import_module(f"skills.{sid}");c=m.register(registry);CURRENT_SKILL=sid;return c
    except Exception as e:logger.error(f"技能{sid}:{e}");return 0
def _discover():
    global SKILL_LIST;SKILL_LIST=[]
    for m in pkgutil.iter_modules(skills_pkg.__path__):
        mod=importlib.import_module(f"skills.{m.name}")
        SKILL_LIST.append({"id":m.name,"name":getattr(mod,"SKILL_NAME",m.name),"icon":getattr(mod,"SKILL_ICON","🔧"),"desc":getattr(mod,"SKILL_DESC","")})
_discover();_load_skill("全功能");agent=Agent(llm,registry,brain=brain,learner=learner,engine=engine)
agent.set_confirm_handler()
from fastapi import FastAPI,WebSocket,WebSocketDisconnect,Body
from fastapi.staticfiles import StaticFiles;from fastapi.responses import FileResponse
app=FastAPI(title="JARVIS",version="2.0")

@app.get("/")
async def root():return FileResponse(str(ROOT/"web"/"index.html"))

@app.get("/favicon.ico")
async def favicon():return FileResponse(str(ROOT/"web"/"favicon.ico"))

@app.websocket("/ws")
async def ws(ws:WebSocket):
    await ws.accept()
    async def _agent_loop(text: str):
        """并发运行 agent, 同时监听 WS 消息 (解决 confirm 死锁)"""
        q = asyncio.Queue()
        async def _run():
            try:
                async for msg in agent.chat(text):
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
                            if hasattr(agent, '_permission_level'):
                                agent._permission_level = perm
                        except: pass
                    elif t2=="ping":
                        await ws.send_json({"type":"pong","tools":registry.count,"model":llm.model})
    try:
        while True:
            d=await ws.receive_text();m=json.loads(d);t=m.get("type","message")
            if t=="message":
                u=m.get("payload",{}).get("text","").strip()
                if not u:continue
                await _agent_loop(u)
                continue
            elif t=="folder_file":
                p=m.get("payload",{}); path=p.get("path",""); content=p.get("content","")
                if path:
                    safe_path = path.replace("..","").replace("~","")
                    full = ROOT / "uploads" / safe_path
                    full.parent.mkdir(parents=True, exist_ok=True)
                    full.write_text(content[:100000], encoding="utf-8")
                    logger.info(f"📁 已保存上传文件: {safe_path} ({len(content)}字符)")
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
        # 将新权限同步到 Agent 实例
        if hasattr(agent, '_permission_level'):
            agent._permission_level = level
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

# ═══════════════════════════════════════════════════════════════
# ★ WORKSPACE API — 工作台: 终端/文件/项目/GitHub/浏览器 ★
# ═══════════════════════════════════════════════════════════════

@app.post("/api/workspace/terminal")
async def api_terminal_exec(data: dict = Body(...)):
    """执行终端命令"""
    import subprocess
    cmd = data.get("command", "").strip()
    if not cmd: return {"ok": False, "output": "命令不能为空"}
    shell = data.get("shell", "cmd")
    timeout = min(data.get("timeout", 15), 60)
    try:
        if shell == "powershell":
            r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        else:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, encoding="gbk", errors="replace")
        out = r.stdout.strip() or r.stderr.strip() or f"(exit:{r.returncode})"
        return {"ok": True, "output": out[:5000], "exit_code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "命令超时", "exit_code": -1}
    except Exception as e:
        return {"ok": False, "output": str(e)[:500], "exit_code": -1}

@app.get("/api/workspace/explore")
async def api_workspace_explore(path: str = "."):
    """浏览文件目录 (文件树)"""
    import os, stat
    try:
        base = Path(ROOT / path).resolve()
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
        fp = (ROOT / path).resolve()
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
        fp = (ROOT / path).resolve()
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

    if action == "from_folder":
        if not source_path: return {"ok": False, "error": "请选择源文件夹"}
        src = Path(source_path)
        if not src.exists(): return {"ok": False, "error": f"源文件夹不存在: {source_path}"}
        dst = projects_dir / name
        if dst.exists(): return {"ok": False, "error": "项目名已存在"}
        import shutil
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return {"ok": True, "path": str(dst.relative_to(ROOT)), "type": "from_folder"}

    if action == "create":
        dst = projects_dir / name
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
    try:cfg=yaml.safe_load(open(ROOT/"config.yaml",encoding="utf-8"))
    except yaml.YAMLError as e:logger.warning(f"config.yaml 解析异常: {e}");cfg={}
    except FileNotFoundError:cfg={};logger.info("使用默认配置")
    sc=cfg.get("server",{});h=sc.get("host","127.0.0.1");p=int(os.environ.get("PORT", sc.get("port", 8080)))
    print(f"JARVIS http://{h}:{p}  {llm.model}  {registry.count}工具")
    try:__import__('asyncio').run(__import__('voice.tts',fromlist=['']).preload_phrases())
    except ImportError:logger.debug("TTS 模块未安装，跳过语音预加载")
    except Exception as e:logger.warning(f"语音预加载失败: {e}")
    from voice.tts import _trim_cache
    try:_trim_cache()
    except Exception:pass
    uvicorn.run(app,host=h,port=p,log_level="info")
