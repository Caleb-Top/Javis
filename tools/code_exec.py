"""⭐ 代码执行引擎 — 自进化式多语言运行时

架构:
  run_code(code, language) → _LANGUAGE_HANDLERS[language](code)
                                 ↕ 动态注册/修改
   Agent 可通过 register_language / update_language 自我进化

核心能力:
  - 内置: python, powershell, cmd, node, c, cpp
  - 动态注册新语言: register_language("rust", handler_source)
  - 自我审查: read_language_handler(name), list_languages()
  - 自我修改: update_language_handler(name, new_source)
  - 从经验学习: 每次执行存入 experience store
"""
import sys, os, io, json, subprocess, logging, traceback, textwrap, base64, tempfile, random
from pathlib import Path
from core.tool_result import ToolResult
logger = logging.getLogger("tools.code_exec")

# ── 外部依赖注入 ──
REGISTRY = None  # 由 main.py 设置 (工具注册中心)
_BRAIN = None    # 由 main.py 设置 (大脑引用, 用于经验学习)
_TOOLS_DIR = Path(__file__).parent.parent / "tools_lib"

def set_brain(brain):
    """将大脑实例注入执行引擎, 使代码执行经验能被学习"""
    global _BRAIN
    _BRAIN = brain
    logger.info("🧠 大脑已连接到执行引擎")

def _learn_from_code(lang: str, code: str, success: bool, output: str):
    """将代码执行经验注入大脑"""
    if not _BRAIN:
        return
    try:
        category = f"code_exec.{lang}"
        snippet = code[:150].replace("\n", " ").strip()
        if success:
            _BRAIN.learn_fact(
                f"代码执行成功 [{lang}]: {snippet} → {output[:80]}",
                category=category, source="code_exec", priority=1
            )
        else:
            _BRAIN.record_experience(
                intent=f"执行 {lang} 代码",
                action=category,
                result="failure",
                error=output[:200],
                lesson=f"代码执行失败 [{lang}]: {snippet}",
                priority=2, domain="code_exec",
                error_category=output.split("\n")[0][:40]
            )
    except Exception:
        pass  # 学习失败不影响主流程

# ═══════════════════════════════════════════════════════════════
# 沙箱保护 — 共享自 core/workspace_manager.py
# ═══════════════════════════════════════════════════════════════
from core.workspace_manager import (
    JARVIS_ROOT as _JARVIS_ROOT,
    SANDBOX_PROTECTED_DIRS as _SANDBOX_PROTECTED_DIRS,
    SANDBOX_PROTECTED_FILES as _SANDBOX_PROTECTED_FILES,
    sandbox_check_path as _sandbox_check_path,
)

# ═══════════════════════════════════════════════════════════════
# ★ 自进化核心: 动态语言处理器注册表 ★
# ═══════════════════════════════════════════════════════════════

_LANGUAGE_HANDLERS = {}   # language_name → handler_function(code: str) -> str
_LANGUAGE_SOURCES = {}    # language_name → handler_source_code (用于自我审查/修改)
_LANGUAGE_INFO   = {}     # language_name → {"compiler": "...", "version": "..."}
_EXEC_HISTORY    = []     # 经验学习: [(lang, code_snippet, success, output_snippet), ...]
_MAX_HISTORY     = 500    # 最多保留的经验数

def _init_handlers():
    """初始化所有内置语言处理器"""
    def reg(name, func, source, info=None):
        _LANGUAGE_HANDLERS[name] = func
        _LANGUAGE_SOURCES[name] = source
        _LANGUAGE_INFO[name] = info or {"compiler": "builtin", "version": "1.0"}

    reg("python", _exec_python, _SRC_PYTHON, {"compiler": "cpython", "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"})
    reg("powershell", _exec_powershell, _SRC_POWERSHELL, {"compiler": "pwsh", "version": "builtin"})
    reg("cmd", _exec_cmd, _SRC_CMD, {"compiler": "cmd.exe", "version": "builtin"})
    reg("node", _exec_node, _SRC_NODE, _detect_node_info())
    reg("c", _exec_c, _SRC_C, _detect_compiler_info("gcc"))
    reg("cpp", _exec_cpp, _SRC_CPP, _detect_compiler_info("g++", ["-static"]))

    logger.info(f"语言引擎初始化: {len(_LANGUAGE_HANDLERS)} 个处理就绪")
    for name in _LANGUAGE_HANDLERS:
        info = _LANGUAGE_INFO[name]
        logger.info(f"  [{name}] {info.get('compiler','?')} {info.get('version','')}")

def _detect_node_info():
    """探测 Node.js 版本"""
    try:
        exe = _find_tool("nodejs", "node.exe")
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
        ver = r.stdout.strip() if r.returncode == 0 else "?"
        return {"compiler": "node", "version": ver}
    except: return {"compiler": "node", "version": "?"}

def _detect_compiler_info(cc, extra_flags=None):
    """探测 C/C++ 编译器版本"""
    try:
        exe = _find_tool("mingw32/bin", f"{cc}.exe")
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
        first = r.stdout.split("\n")[0].strip() if r.returncode == 0 else "?"
        return {"compiler": first, "version": first.split()[-1] if first else "?"}
    except: return {"compiler": cc, "version": "?"}

def _find_tool(subdir, exe_name):
    """在 tools/ 下找工具"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(base, "tools", subdir, exe_name)
    if os.path.isfile(candidate):
        return candidate
    which = subprocess.run(["where", exe_name.replace(".exe","")], capture_output=True, text=True, timeout=5)
    if which.returncode == 0 and which.stdout.strip():
        return which.stdout.strip().split("\n")[0].strip()
    return exe_name  # fallback — hope PATH has it


class _SandboxOS:
    """os 模块的沙箱包装 — 拦截 remove/unlink/rmdir/rename/replace"""
    def __init__(self, real_os): self._r = real_os
    def __getattr__(self, name): return getattr(self._r, name)
    def remove(self, path, *a, **kw): _sandbox_check_path(path); return self._r.remove(path, *a, **kw)
    def unlink(self, path, *a, **kw): _sandbox_check_path(path); return self._r.unlink(path, *a, **kw)
    def rmdir(self, path, *a, **kw): _sandbox_check_path(path); return self._r.rmdir(path, *a, **kw)
    def rename(self, src, dst, *a, **kw): _sandbox_check_path(src); _sandbox_check_path(dst); return self._r.rename(src, dst, *a, **kw)
    def replace(self, src, dst, *a, **kw): _sandbox_check_path(src); _sandbox_check_path(dst); return self._r.replace(src, dst, *a, **kw)


# ═══════════════════════════════════════════════════════════════
# 执行引擎: 各语言处理器实现
# ═══════════════════════════════════════════════════════════════

def _exec_python(code: str) -> str:
    old_stdout, old_stderr = sys.stdout, sys.stderr
    captured = io.StringIO()
    sys.stdout = captured; sys.stderr = captured
    try:
        import ctypes, time, math, re, struct
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        _real_open = __builtins__["open"] if isinstance(__builtins__, dict) else __builtins__.open
        def _safe_open(file, mode="r", *args, **kwargs):
            if any(c in mode for c in "wax+"):
                _sandbox_check_path(str(file) if not isinstance(file, str) else file)
            return _real_open(file, mode, *args, **kwargs)

        # ── 自进化内建工具: 注册新语言 / 审查引擎 ──
        def register_language(name: str, handler_source: str, compiler_path: str = "", version: str = "1.0"):
            """★ 注册一门新语言到执行引擎, 立即生效 ★
               handler_source 必须是完整的 Python 函数 def handler(code): -> str
               之后 run_code(code, language=name) 就能调用它"""
            return _do_register_language(name, handler_source, compiler_path, version)

        def update_language_handler(name: str, handler_source: str):
            """★ 修改已有语言的处理逻辑 ★
               读取 → 修改 → update 三步实现核心自进化"""
            return _do_update_language(name, handler_source)

        def read_language_handler(name: str) -> str:
            """审查指定语言的处理器源代码"""
            src = _LANGUAGE_SOURCES.get(name)
            if src: return f"# 语言: {name}\n{src}"
            return f"未找到处理器: {name}"

        def list_languages() -> str:
            """列出所有已注册的语言处理器"""
            lines = [f"已注册 {len(_LANGUAGE_HANDLERS)} 个语言处理器:"]
            for name, func in sorted(_LANGUAGE_HANDLERS.items()):
                info = _LANGUAGE_INFO.get(name, {})
                cc = info.get("compiler", "?")
                ver = info.get("version", "?")
                lines.append(f"  [{name}] {cc} {ver}")
            return "\n".join(lines)

        def learn_from_execution(lang: str, code_snippet: str, success: bool, result: str):
            """将本次执行记录为经验, 供后续学习"""
            _EXEC_HISTORY.append((lang, code_snippet[:200], success, result[:100]))
            if len(_EXEC_HISTORY) > _MAX_HISTORY:
                _EXEC_HISTORY.pop(0)
            return f"经验已记录 (共 {len(_EXEC_HISTORY)} 条)"

        def get_experience(pattern: str = "", lang: str = "", limit: int = 10) -> str:
            """查询执行经验, 按语言/内容筛选"""
            results = _EXEC_HISTORY
            if lang: results = [e for e in results if e[0] == lang]
            if pattern: results = [e for e in results if pattern.lower() in e[1].lower()]
            lines = [f"匹配 {len(results)}/{len(_EXEC_HISTORY)} 条经验:"]
            for l, c, s, r in results[-limit:]:
                lines.append(f"  [{l}] {'✅' if s else '❌'} {c[:80]} → {r[:60]}")
            return "\n".join(lines)

        # 内置工具保存
        def save_tool(name: str, description: str, handler_code: str, category: str = "general"):
            safe = name.replace(" ","_").replace("-","_")
            path = _TOOLS_DIR / f"tool_{safe}.py"
            content = f'"""Javis自创: {name}"""\nTOOL_NAME="{name}"\nTOOL_DESC="{description}"\nTOOL_CATEGORY="{category}"\nTOOL_PARAMS={{"type":"object","properties":{{}},"required":[]}}\n\ndef handler(**kwargs):\n{textwrap.indent(textwrap.dedent(handler_code.strip()), "    ")}\n'
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                compile(content, path.name, "exec")
                if REGISTRY:
                    from core.tool_registry import ToolDef
                    def wrapper(**kw):
                        try:
                            local = {"ToolResult": ToolResult}
                            exec(textwrap.dedent(handler_code.strip()), local)
                            return local.get("result") or ToolResult.success("ok")
                        except Exception as e:
                            return ToolResult.failure(str(e))
                    REGISTRY.register(ToolDef(name, description, {"type":"object","properties":{},"required":[]}, wrapper, category))
                    _update_agent_tools()
                return f"✅ 工具 '{name}' 已保存并注册"
            except Exception as e:
                return f"❌ 保存失败: {e}"

        def _update_agent_tools():
            try:
                import main as _m
                if hasattr(_m, 'agent'): _m.agent.tools = REGISTRY
            except: pass

        env = {
            "__builtins__": __builtins__,
            "open": _safe_open,
            "ctypes": ctypes, "user32": user32, "kernel32": kernel32,
            "os": _SandboxOS(os), "sys": sys, "io": io, "json": json, "time": time,
            "math": math, "random": random, "re": re, "struct": struct,
            "subprocess": subprocess, "base64": base64,
            "ToolResult": ToolResult,
            "REGISTRY": REGISTRY,
            "save_tool": save_tool,
            # ★ 自进化工具 ★
            "register_language": register_language,
            "update_language_handler": update_language_handler,
            "read_language_handler": read_language_handler,
            "list_languages": list_languages,
            "learn_from_execution": learn_from_execution,
            "get_experience": get_experience,
        }
        if REGISTRY:
            from core.tool_registry import ToolDef
            env["ToolDef"] = ToolDef
        for m in ['pyautogui', 'psutil', 'shutil', 'glob', 'datetime', 'hashlib', 'PIL']:
            try: env[m] = __import__(m)
            except ImportError: env[m] = None

        cleaned = textwrap.dedent(code.strip())
        compiled = compile(cleaned, "<code>", "exec", flags=0)
        exec(compiled, env)
        output = captured.getvalue()
        if not output.strip() and "result" in env: output = str(env["result"])
        if not output.strip() and "r" in env: output = str(env["r"])
        return output.strip() or "(成功)"
    except PermissionError as e: return f"⛔ 沙箱拦截: {e}"
    except Exception as e:
        tb = traceback.format_exc()
        return "错误:\n" + "\n".join(tb.split("\n")[-5:])[:600]
    finally:
        sys.stdout = old_stdout; sys.stderr = old_stderr


def _exec_powershell(code: str) -> str:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", code],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="ignore")
        return (r.stdout.strip() or r.stderr.strip() or f"(exit:{r.returncode})")[:2000]
    except subprocess.TimeoutExpired: return "超时(30s)"
    except Exception as e: return f"错误: {e}"


def _exec_node(code: str) -> str:
    try:
        node_exe = os.environ.get("NODE_EXE", _find_tool("nodejs", "node.exe"))
        with tempfile.NamedTemporaryFile(suffix=".cjs", mode="w", delete=False, encoding="utf-8") as f:
            f.write(code); tmppath = f.name
        r = subprocess.run([node_exe, tmppath], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        out = r.stdout.strip(); err = r.stderr.strip()
        os.unlink(tmppath)
        if out: return out[:2000]
        if err: return f"[stderr] {err[:1900]}"
        return f"(exit:{r.returncode})"
    except subprocess.TimeoutExpired: return "超时(30s)"
    except FileNotFoundError: return "错误: node.exe 未安装"
    except Exception as e: return f"错误: {e}"


def _exec_cmd(code: str) -> str:
    try:
        lines = [l.strip() for l in code.split("\n")]
        flat = " && ".join(l for l in lines if l and not l.startswith("@"))
        if not flat: flat = " ".join(l for l in lines if l)
        # 安全: 使用 cmd /c 代替 shell=True
        r = subprocess.run(["cmd","/c",flat], capture_output=True, timeout=30, encoding="utf-8", errors="replace")
        out = r.stdout.strip(); err = r.stderr.strip()
        if out: return out[:2000]
        if err: return f"[stderr] {err[:1900]}"
        return f"(exit:{r.returncode})"
    except subprocess.TimeoutExpired: return "超时(30s)"
    except Exception as e: return f"错误: {e}"


def _exec_c(code: str) -> str:
    return _exec_compiled(code, "c", "gcc")


def _exec_cpp(code: str) -> str:
    return _exec_compiled(code, "cpp", "g++", ["-static"])


def _exec_compiled(code: str, lang: str, compiler_name: str, extra_flags=None) -> str:
    """通用编译执行器 — C/C++/Rust/Go/Fortran 都遵循此模式"""
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        compiler = _find_tool("mingw32/bin", f"{compiler_name}.exe")
        tag = f"jv_{random.randint(10000,99999)}"
        src_path = os.path.join(tempfile.gettempdir(), f"{tag}.{lang}")
        exe_path = os.path.join(tempfile.gettempdir(), f"{tag}.exe")

        with open(src_path, "wb") as f:
            f.write(code.encode("utf-8"))

        cr_args = [compiler, src_path, "-o", exe_path, "-O2"]
        if extra_flags:
            cr_args += extra_flags
        cr = subprocess.run(cr_args, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        if cr.returncode != 0:
            return f"[编译错误]\n{cr.stderr.strip()[:1500]}"

        rr = subprocess.run([exe_path], capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")

        for p in [src_path, exe_path]:
            try: os.unlink(p)
            except: pass

        out = rr.stdout.strip(); err = rr.stderr.strip()
        if out: return out[:2000]
        if err: return f"[stderr] {err[:1900]}"
        return f"(exit:{rr.returncode})"
    except subprocess.TimeoutExpired: return "编译/运行超时"
    except FileNotFoundError: return f"错误: {compiler_name} 编译器未安装"
    except Exception as e: return f"错误: {e}"


# ═══════════════════════════════════════════════════════════════
# ★ 自进化核心 API (可从 run_code 内部或外部调用) ★
# ═══════════════════════════════════════════════════════════════

def _do_register_language(name: str, handler_source: str, compiler_path: str = "", version: str = "1.0") -> str:
    """注册新语言 — 编译 handler 源码并注入运行时"""
    try:
        cleaned = textwrap.dedent(handler_source.strip())
        # 注入执行环境
        local_ns = {
            "subprocess": subprocess, "os": os, "json": json,
            "tempfile": tempfile, "random": random, "Path": Path,
            "ToolResult": ToolResult, "_find_tool": _find_tool,
            "_exec_compiled": _exec_compiled,
        }
        exec(cleaned, local_ns)
        # 必须导出 handler(code: str) -> str
        handler = local_ns.get("handler")
        if not handler:
            return f"❌ 注册失败: handler 函数未定义"

        # 测试编译
        test_code = local_ns.get("_TEST_CODE", "return 'ok'")
        try:
            result = handler(test_code)
        except:
            pass  # 不强制测试

        _LANGUAGE_HANDLERS[name] = handler
        _LANGUAGE_SOURCES[name] = handler_source
        _LANGUAGE_INFO[name] = {"compiler": compiler_path or "dynamic", "version": version}

        # ★ 持久化到 tools_lib 下的语言注册, 重启后可用 ★
        _persist_language_registration(name, handler_source, compiler_path, version)

        return f"✅ 语言 '{name}' 注册成功 (version {version})"
    except Exception as e:
        return f"❌ 注册失败: {e}"


def _do_update_language(name: str, handler_source: str) -> str:
    """修改已有语言的处理逻辑"""
    if name not in _LANGUAGE_HANDLERS:
        return f"❌ 未知语言: {name}, 请先 register_language"
    return _do_register_language(name, handler_source,
        _LANGUAGE_INFO.get(name, {}).get("compiler", ""),
        _LANGUAGE_INFO.get(name, {}).get("version", "?"))


def _persist_language_registration(name, handler_source, compiler_path, version):
    """将语言注册写入持久文件, 重启后自动加载"""
    try:
        reg_dir = Path(__file__).parent.parent / "tools_lib" / "_languages"
        reg_dir.mkdir(parents=True, exist_ok=True)
        reg_file = reg_dir / f"{name}.py"
        # Always overwrite — never append.
        source = "# Auto-registered language: " + name + chr(10)
        source += "# compiler: " + str(compiler_path) + ", version: " + str(version) + chr(10) + chr(10)
        source += handler_source + chr(10) + chr(10)
        source += 'if __name__ == "__main__":' + chr(10)
        source += '    import sys' + chr(10)
        source += '    r = handler(sys.stdin.read())' + chr(10)
        source += '    print(r)' + chr(10)
        reg_file.write_text(source, encoding="utf-8")
    except Exception as e:
        logger.warning(f"持久化语言 {name} 失败: {e}")


def _load_persisted_languages():
    """在启动时加载持久化的语言注册"""
    reg_dir = Path(__file__).parent.parent / "tools_lib" / "_languages"
    if not reg_dir.exists():
        return
    for f in sorted(reg_dir.glob("*.py")):
        try:
            name = f.stem
            source = f.read_text(encoding="utf-8")
            # 提取 handler 源码 (去掉 auto-gen 注释头)
            lines = source.split("\n")
            start = 0
            for i, l in enumerate(lines):
                if l.strip().startswith("def handler"):
                    start = i
                    source_body = "\n".join(lines[start:])
                    _do_register_language(name, source_body, "persisted", "auto")
                    logger.info(f"  加载持久化语言: {name}")
                    break
        except Exception as e:
            logger.warning(f"加载语言 {f.name} 失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 源模板 (用于自我审查时的展示)
# ═══════════════════════════════════════════════════════════════

_SRC_PYTHON = "def handler(code): return _exec_python(code)"
_SRC_POWERSHELL = "def handler(code): return _exec_powershell(code)"
_SRC_CMD = "def handler(code): return _exec_cmd(code)"
_SRC_NODE = "def handler(code): return _exec_node(code)"
_SRC_C = "def handler(code): return _exec_c(code)"
_SRC_CPP = "def handler(code): return _exec_cpp(code)"


# ═══════════════════════════════════════════════════════════════
# ★ 主入口: run_code — 自进化引擎外部接口 ★
# ═══════════════════════════════════════════════════════════════

def run_code(code: str, language: str = "python", **kwargs) -> ToolResult:
    """执行代码 — 支持多种语言的动态运行时。

    内置语言: python, powershell, cmd, node, c, cpp
    动态语言: register_language() 注册的新语言

    ★ 自进化工具 (在 python 执行环境中可用):
       register_language(name, handler_source) — 注册新语言
       update_language_handler(name, handler_source) — 修改已有语言
       read_language_handler(name) — 审查处理器源码
       list_languages() — 列出所有语言
       learn_from_execution(lang, code, success, result) — 记录经验
       get_experience(pattern, lang) — 查询执行经验
       save_tool(name, desc, handler_code, category) — 持久化工具
    """
    if not code or not code.strip():
        return ToolResult.failure("代码不能为空")

    # 如果不是已有语言 → 尝试动态查找
    handler = _LANGUAGE_HANDLERS.get(language)
    if handler:
        try:
            output = handler(code)
        except Exception as e:
            output = f"运行时错误: {e}"
    else:
        # 未知语言 → 给一个友好的自我修复提示
        available = ", ".join(_LANGUAGE_HANDLERS.keys())
        output = f"未知语言 '{language}'。可用: {available}\n提示: 用 register_language('{language}', handler_source) 注册"

    # 记录经验
    success = "错误" not in output and "未知语言" not in output
    _EXEC_HISTORY.append((language, code[:200], success, output[:100]))
    if len(_EXEC_HISTORY) > _MAX_HISTORY:
        _EXEC_HISTORY.pop(0)

    # 注入大脑学习
    _learn_from_code(language, code, success, output)

    return ToolResult.success(f"[{language}]\n{output[:1000]}")


# ═══════════════════════════════════════════════════════════════
# 一次性注册暴露到工具层的自进化接口
# ═══════════════════════════════════════════════════════════════

def tools_for_registry():
    """给 tools_lib/loader 调用的额外工具"""
    from core.tool_registry import ToolDef
    return [
        ToolDef("list_languages", "列出所有已注册的编程语言执行器", {"type":"object","properties":{},"required":[]},
            lambda **kw: ToolResult.success(list_languages_text()), "engine_core"),
        ToolDef("register_language", "注册新编程语言执行器 (可持久化)", {"type":"object","properties":{
            "name":{"type":"string","description":"语言名"}, "handler_source":{"type":"string","description":"def handler(code): 源码"},
            "compiler_path":{"type":"string","description":"编译器路径(可选)"}, "version":{"type":"string","description":"版本号"}},
            "required":["name","handler_source"]},
            lambda **kw: ToolResult.success(_do_register_language(kw.get("name",""), kw.get("handler_source",""), kw.get("compiler_path",""), kw.get("version","1.0"))), "engine_core"),
        ToolDef("update_language_handler", "修改已有语言执行器的处理逻辑", {"type":"object","properties":{
            "name":{"type":"string"}, "handler_source":{"type":"string"}}, "required":["name","handler_source"]},
            lambda **kw: ToolResult.success(_do_update_language(kw.get("name",""), kw.get("handler_source",""))), "engine_core"),
        ToolDef("read_language_handler", "查看指定语言执行器的源代码", {"type":"object","properties":{
            "name":{"type":"string","description":"语言名"}}, "required":["name"]},
            lambda **kw: ToolResult.success(_read_language_handler_text(kw.get("name",""))), "engine_core"),
        ToolDef("get_experience", "查询代码执行经验数据用于学习", {"type":"object","properties":{
            "pattern":{"type":"string","description":"筛选文本"}, "lang":{"type":"string","description":"筛选语言"},
            "limit":{"type":"integer","description":"返回条数"}}, "required":[]},
            lambda **kw: ToolResult.success(get_experience_text(kw.get("pattern",""), kw.get("lang",""), kw.get("limit",10))), "engine_core"),
    ]

def list_languages_text() -> str:
    lines = [f"语言引擎: {len(_LANGUAGE_HANDLERS)} 个注册"]
    for name in sorted(_LANGUAGE_HANDLERS):
        info = _LANGUAGE_INFO.get(name, {})
        lines.append(f"  [{name}] {info.get('compiler','?')} {info.get('version','')}")
    return "\n".join(lines)

def _read_language_handler_text(name: str) -> str:
    src = _LANGUAGE_SOURCES.get(name)
    if src: return f"# ── {name} 语言处理器 ──\n{src}"
    return f"未找到: {name}"

def get_experience_text(pattern="", lang="", limit=10):
    results = _EXEC_HISTORY
    if lang: results = [e for e in results if e[0] == lang]
    if pattern: results = [e for e in results if pattern.lower() in e[1].lower()]
    lines = [f"经验 {len(results)}/{len(_EXEC_HISTORY)} 条 (显示 {min(limit,len(results))}):"]
    for l, c, s, r in results[-limit:]:
        lines.append(f"  [{l}] {'OK' if s else 'FAIL'} {c[:80]} → {r[:60]}")
    if not results:
        lines.append("  (暂无经验数据)")
    return "\n".join(lines)


# ── 启动时初始化 ──
_init_handlers()
_load_persisted_languages()
