"""系统工具"""
import subprocess, os, logging, glob
from core.tool_result import ToolResult
from utils.error_messages import friendly_error
logger = logging.getLogger("tools.system")

def system_info(**kwargs) -> ToolResult:
    try:
        import psutil
        cpu=psutil.cpu_percent(interval=0.1); mem=psutil.virtual_memory(); disk=psutil.disk_usage("/")
        return ToolResult.success(f"CPU:{cpu}% 内存:{mem.percent}% 磁盘:{disk.percent}%")
    except ImportError:
        return ToolResult.failure("psutil 未安装")
    except Exception as e:
        logger.warning(f"system_info 异常: {e}")
        return ToolResult.failure(f"获取系统信息失败: {e}")
        return ToolResult.failure(f"获取系统信息失败: {e}")

def system_execute(command: str, timeout: int = 30) -> ToolResult:
    try:
        r=subprocess.run(command,shell=True,capture_output=True,text=True,timeout=timeout,encoding="gbk",errors="ignore")
        return ToolResult.success(r.stdout.strip() or r.stderr.strip() or f"(exit:{r.returncode})")
    except subprocess.TimeoutExpired: return ToolResult.failure("超时")
    except Exception as e: return ToolResult.failure(friendly_error(e))

def _is_store_lnk(path):
    try:
        r=subprocess.run(['powershell','-Command',f'$s=New-Object -ComObject WScript.Shell;$s=$s.CreateShortcut("{path}");$t=$s.TargetPath;if($t-eq""-or$t-like"*ms-windows-store*"){{Write-Output"STORE"}}else{{Write-Output"OK:$t"}}'],capture_output=True,text=True,timeout=5)
        o=r.stdout.strip()
        if o=="STORE": return True
        if o.startswith("OK:"):
            t=o[3:]
            if not t or not os.path.exists(t): return True
        return False
    except Exception as e:
        logger.debug(f"_is_store_lnk 异常: {e}")
        return False

def open_file(path: str) -> ToolResult:
    try:
        path=os.path.abspath(path.replace("/","\\"))
        if not os.path.exists(path): return ToolResult.failure(f"文件不存在: {path}")
        if path.lower().endswith(".lnk") and _is_store_lnk(path): return ToolResult.failure("快捷方式指向微软商店,已跳过")
        os.startfile(path)
        return ToolResult.success(f"已打开: {path}")
    except FileNotFoundError:
        return ToolResult.failure(f"文件不存在: {path}")
    except Exception as e:
        logger.warning(f"open_file 异常: {e}")
        return ToolResult.failure(friendly_error(e))

_APP_NAME_ALIASES={"qq":["qqmusic","tencent","qq"],"qq音乐":["qqmusic","tencent","qq"],"音乐":["qqmusic","cloudmusic"],"微信":["wechat","weixin"],"网易云":["cloudmusic","netease"],"word":["winword"],"excel":["excel"],"ppt":["powerpnt"],"chrome":["chrome","google chrome"],"浏览器":["chrome","msedge","firefox","brave","opera"]}
def _alias_search_names(name):
    n=name.lower().strip(); results=[n]
    for k,aliases in _APP_NAME_ALIASES.items():
        if k in n or n in k: results.extend(aliases)
    return list(set(results))

_APP_PRIORITY_PATTERNS={"qq音乐":["qq音乐","qqmusic"],"网易云":["cloudmusic","netease"],"微信":["wechat"],"qq":["qq"],"浏览器":["chrome","edge","firefox"]}
def _score_match(basename, raw_name):
    """给匹配结果打分，越高越接近用户意图"""
    bl=basename.lower(); rl=raw_name.lower()
    score=0
    if bl==rl: score+=100
    elif bl.startswith(rl)or rl.startswith(bl): score+=50
    if rl in bl: score+=30
    if bl in rl: score+=20
    # 特定关键词加权
    for kw in ["qq音乐","music","player","播放","音乐"]:
        if kw in bl: score+=10
    return score

def find_app(name: str) -> ToolResult:
    search_names=_alias_search_names(name); all_matches={}; seen=set()
    dirs=["C:/ProgramData/Microsoft/Windows/Start Menu/Programs",os.path.expanduser("~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs")]
    for sn in search_names:
        for base in dirs:
            for f in glob.glob(base+"/**/*.lnk",recursive=True):
                bn=os.path.basename(f).lower().replace(".lnk","")
                if sn.lower() in bn:
                    fn=f.replace("/","\\")
                    if fn not in seen:
                        seen.add(fn)
                        score=_score_match(bn,name)
                        all_matches[fn]=score
    if not all_matches: return ToolResult.failure(f"未找到 {name}")
    # 按分数排序，最高分优先
    sorted_paths=sorted(all_matches.keys(),key=lambda p:all_matches[p],reverse=True)
    return ToolResult.success("\n".join(sorted_paths[:10]))

def open_app(name: str = "", app: str = "", **kwargs) -> ToolResult:
    target = name or app or kwargs.get("app_name","") or kwargs.get("application","") or kwargs.get("target","")
    if not target: return ToolResult.failure("请提供应用名称")
    result = find_app(target)
    if result.success:
        paths=[p.strip() for p in result.data.split("\n") if p.strip() and "卸载" not in p]
        if paths: return open_file(paths[0])
    return ToolResult.failure(f"未找到 {target}，请确认已安装")


def brain_status(**kwargs) -> ToolResult:
    """检视自身的 Brain（知识库/记忆/经验）状态"""
    try:
        import main as _main
        b = getattr(_main, 'brain', None)
        if not b:
            return ToolResult.success("大脑实例未加载")
        stats = b.get_stats()
        high_pri = [f for f in b._facts if f.priority >= 4]
        recent = b._facts[-10:] if len(b._facts) >= 10 else b._facts[:]
        lines = [
            f"大脑: {stats['facts_count']} 事实, {stats['experiences_count']} 经验",
            f"分类 ({len(stats.get('categories', {}))}):",
        ]
        for cat, cnt in sorted(stats.get('categories', {}).items(), key=lambda x: -x[1])[:8]:
            lines.append(f"  {cat}: {cnt}")
        lines.append("")
        lines.append(f"高优先级(>=4★): {len(high_pri)} 条")
        for f in high_pri[-5:]:
            lines.append(f"  [{f.priority}★] {f.content[:70]}")
        lines.append("")
        lines.append(f"最近学习:")
        for f in recent[-5:]:
            lines.append(f"  [{f.category}] {f.content[:60]}")
        return ToolResult.success("\n".join(lines))
    except Exception as e:
        return ToolResult.failure(f"检视失败: {e}")


def memory_status(**kwargs) -> ToolResult:
    """检视四层记忆系统的完整状态"""
    try:
        import main as _main
        b = getattr(_main, 'brain', None)
        lines = ["记忆系统状态"]
        if b:
            s = b.get_stats()
            lines.append(f"事实:{s['facts_count']} 经验:{s['experiences_count']}")
        try:
            from memory.semantic import get_stats
            sm = get_stats()
            lines.append(f"语义规则:{sm.get('total_rules',0)}条 高风险:{sm.get('high_risk',0)}")
        except:
            lines.append("语义记忆:未启动")
        try:
            from memory.procedural import get_stats
            pm = get_stats()
            lines.append(f"程序记忆:{pm.get('total',0)}条链 成功率:{pm.get('avg_success_rate',0):.0%}")
        except:
            lines.append("程序记忆:未启动")
        return ToolResult.success("\n".join(lines))
    except Exception as e:
        return ToolResult.failure(f"检视失败: {e}")



def github_search(**kwargs) -> ToolResult:
    """搜索 GitHub 仓库，支持 Bearer token 认证"""
    query = kwargs.get("query", "").strip()
    sort = kwargs.get("sort", "stars")
    order = kwargs.get("order", "desc")
    per_page = min(kwargs.get("per_page", 10), 20)
    if not query:
        return ToolResult.failure("搜索关键词不能为空")
    try:
        import requests
        token = os.environ.get("GH_TOKEN", "")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": sort, "order": order, "per_page": per_page},
            headers=headers, timeout=15
        )
        if r.status_code == 403:
            return ToolResult.failure("GitHub API 限流，请在 tools/gh/.token 配置 token")
        if r.status_code != 200:
            return ToolResult.failure(f"GitHub API 错误: {r.status_code}")
        data = r.json()
        items = data.get("items", [])
        if not items:
            return ToolResult.success(f"搜索 '{query}' 无结果")
        lines = [f"GitHub 搜索: {query} ({data.get('total_count', 0)} 结果)"]
        for item in items[:per_page]:
            name = item.get("full_name", "?")
            stars = item.get("stargazers_count", 0)
            desc = (item.get("description") or "无描述")[:80]
            lang = item.get("language") or "?"
            url = item.get("html_url", "")
            lines.append(f"  [{stars}★] {name} ({lang})")
            lines.append(f"    {desc}")
            lines.append(f"    {url}")
        return ToolResult.success(chr(10).join(lines))
    except Exception as e:
        return ToolResult.failure(f"搜索失败: {e}")