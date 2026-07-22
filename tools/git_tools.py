"""Git 工具集 — subprocess 调 git CLI，返回 ToolResult"""

import subprocess
import os
import re
import logging
from typing import Optional

from core.tool_result import ToolResult

logger = logging.getLogger("git_tools")

# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------

_TIMEOUT_NETWORK = 120   # push/pull/clone 网络操作超时
_TIMEOUT_LOCAL = 30      # 本地操作超时

def _run(args: list[str], cwd: str, timeout: int = _TIMEOUT_LOCAL) -> ToolResult:
    """统一执行 git 命令，返回 ToolResult"""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
        )
        if r.returncode == 0:
            return ToolResult.success(r.stdout.strip() or "(empty)")
        return ToolResult.failure(r.stderr.strip() or r.stdout.strip())
    except subprocess.TimeoutExpired:
        return ToolResult.failure("操作超时，请检查网络或仓库大小")
    except FileNotFoundError:
        return ToolResult.failure("未找到 git 命令，请确认 git 已安装并在 PATH 中")
    except Exception as e:
        return ToolResult.failure(f"git 执行异常: {e}")

def _resolve_repo(repo_path: str) -> str:
    """解析仓库路径为绝对路径"""
    return os.path.abspath(repo_path)


# ===================================================================
# P0-1-1  git_status
# ===================================================================

def git_status(repo_path: str = ".") -> ToolResult:
    """返回分支、分类文件(staged/unstaged/untracked)、remote

    解析 git status --porcelain -b 输出，提取：
      - branch: 当前分支名
      - staged:   M / A / D / R 开头的文件
      - unstaged:  M /  D 在第二列的文件
      - untracked: ?? 开头的文件
      - remote: 上游分支（如 origin/main）
    """
    cwd = _resolve_repo(repo_path)
    r = _run(["status", "--porcelain", "-b"], cwd)
    if not r.success:
        return r

    lines = r.data.split("\n")

    # 第一行是分支信息: ## main...origin/main
    branch = "unknown"
    remote = ""
    header = lines[0] if lines else ""
    m = re.match(r"## (.+?)(?:\.\.\.(.+))?", header)
    if m:
        branch = m.group(1)
        remote = m.group(2) or ""

    staged, unstaged, untracked = [], [], []

    for line in lines[1:]:
        if not line or len(line) < 2:
            continue
        idx = line[:2].rstrip()
        wt = line[1:2]
        path = line[3:].strip()
        # staged: index column 非空格
        if idx and idx != " " and idx != "?":
            staged.append({"status": idx, "file": path})
        # unstaged: working tree column 非空格
        if wt and wt != " " and wt != "?":
            unstaged.append({"status": wt, "file": path})
        # untracked
        if line.startswith("??"):
            untracked.append(path)

    return ToolResult.success(
        f"分支: {branch}\n"
        f"Remote: {remote or '(无)'}\n"
        f"Staged: {len(staged)} 个\n"
        f"Unstaged: {len(unstaged)} 个\n"
        f"Untracked: {len(untracked)} 个\n\n"
        + ("Staged:\n" + "\n".join(f"  {s['status']} {s['file']}" for s in staged) + "\n\n" if staged else "")
        + ("Unstaged:\n" + "\n".join(f"  {u['status']} {u['file']}" for u in unstaged) + "\n\n" if unstaged else "")
        + ("Untracked:\n" + "\n".join(f"  {f}" for f in untracked) if untracked else "")
    )


# ===================================================================
# P0-1-2  git_log
# ===================================================================

def git_log(repo_path: str = ".", count: int = 10) -> ToolResult:
    """最近 N 条提交，单行格式"""
    cwd = _resolve_repo(repo_path)
    fmt = "--format=%h | %an | %ar | %s"
    return _run(["log", fmt, f"-{count}"], cwd)


# ===================================================================
# P0-1-3  git_diff
# ===================================================================

def git_diff(repo_path: str = ".", staged: bool = False) -> ToolResult:
    """差异输出，超 3000 字符自动切为 --stat

    Args:
        staged: True 看暂存区 diff，False 看工作区 diff
    """
    cwd = _resolve_repo(repo_path)

    # 先获取完整 diff
    args = ["diff"]
    if staged:
        args.append("--staged")
    r = _run(args, cwd)
    if not r.success:
        return r

    if len(r.data) > 3000:
        # 超长切换为 --stat
        args_stat = ["diff", "--stat"]
        if staged:
            args_stat.append("--staged")
        r_stat = _run(args_stat, cwd)
        if r_stat.success:
            return ToolResult.success(
                f"[Diff 超过 3000 字符，已切换为 --stat 模式]\n\n{r_stat.data}"
            )
    return r


# ===================================================================
# P0-1-4  git_push
# ===================================================================

def _translate_push_error(error: str) -> str:
    """翻译常见 push 错误为用户友好信息"""
    if "rejected" in error.lower():
        if "non-fast-forward" in error.lower():
            return "推送被拒绝: 远程有新的提交，请先 pull 再 push"
        if "fetch first" in error.lower():
            return "推送被拒绝: 请先拉取远程变更"
        return "推送被拒绝: 请检查远程分支状态"
    if "remote rejected" in error.lower():
        return "推送被远程仓库拒绝: 可能是权限不足或分支受保护"
    if "Authentication failed" in error.lower():
        return "推送失败: 认证失败，请检查凭据"
    if "could not resolve host" in error.lower():
        return "推送失败: 无法解析远程仓库地址，请检查网络"
    return error


def git_push(
    repo_path: str = ".",
    remote: str = "origin",
    branch: str = "",
    force: bool = False,
) -> ToolResult:
    """前置检查 + 推送 + 友好错误翻译

    前置检查:
      1. 是否有未提交变更 (避免推送不完整)
      2. 是否有上游分支(若无则设置)
    """
    cwd = _resolve_repo(repo_path)

    # 前置检查 1: 未提交变更
    r_st = _run(["status", "--porcelain"], cwd)
    if r_st.success and r_st.data.strip() and r_st.data.strip() != "(empty)":
        # 检查是否只有 untracked
        lines = [l for l in r_st.data.split("\n") if l.strip()]
        has_tracked = any(not l.startswith("??") for l in lines)
        if has_tracked:
            return ToolResult.failure(
                "推送前需要先提交本地变更。请使用 git_save 保存变更后再推送。"
            )

    # 前置检查 2: 上游分支
    if not branch:
        r_b = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
        if not r_b.success:
            return ToolResult.failure("无法获取当前分支名")
        branch = r_b.data.strip()

    r_upstream = _run(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"], cwd)
    if not r_upstream.success:
        # 设置上游
        set_up = _run(["push", "--set-upstream", remote, branch], cwd, _TIMEOUT_NETWORK)
        if set_up.success:
            return ToolResult.success(f"已设置上游分支并推送成功: {remote}/{branch}")
        return ToolResult.failure(
            f"首次推送失败 (设置 upstream 失败): {_translate_push_error(set_up.error)}"
        )

    # 正常推送
    args = ["push", remote, branch]
    if force:
        args.insert(2, "--force")
    r = _run(args, cwd, _TIMEOUT_NETWORK)

    if r.success:
        return ToolResult.success(f"推送成功: {remote}/{branch}")
    return ToolResult.failure(_translate_push_error(r.error))


# ===================================================================
# P0-1-5  git_pull
# ===================================================================

def git_pull(
    repo_path: str = ".",
    remote: str = "origin",
    branch: str = "",
    rebase: bool = False,
) -> ToolResult:
    """拉取远程更新"""
    cwd = _resolve_repo(repo_path)

    if not branch:
        r_b = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
        if r_b.success:
            branch = r_b.data.strip()

    args = ["pull", remote]
    if branch:
        args.append(branch)
    if rebase:
        args.insert(2, "--rebase")

    return _run(args, cwd, _TIMEOUT_NETWORK)


# ===================================================================
# P0-1-6  git_save
# ===================================================================

def git_save(
    repo_path: str = ".",
    message: str = "",
    files: Optional[list[str]] = None,
    add_all: bool = False,
) -> ToolResult:
    """add + commit 原子操作

    Args:
        files: 指定要 add 的文件列表（相对路径）
        add_all: True 时 git add -A，忽略 files
        message: 提交信息，为空时自动生成
    """
    cwd = _resolve_repo(repo_path)

    if not message:
        message = "auto: save checkpoint"

    # 1. add
    if add_all:
        r_add = _run(["add", "-A"], cwd)
    elif files:
        r_add = _run(["add"] + files, cwd)
    else:
        return ToolResult.failure("请指定 files 或设置 add_all=True")

    if not r_add.success:
        return ToolResult.failure(f"add 失败: {r_add.error}")

    # 2. 检查是否有东西要提交
    r_check = _run(["diff", "--staged", "--quiet"], cwd)
    if r_check.success:
        return ToolResult.success("没有可提交的变更")

    # 3. commit
    r_commit = _run(["commit", "-m", message], cwd)
    if r_commit.success:
        return ToolResult.success(f"已保存: {r_commit.data.strip()}")
    return ToolResult.failure(f"commit 失败: {r_commit.error}")


# ===================================================================
# P0-1-7  git_clone
# ===================================================================

def git_clone(url: str, target_dir: str = "", shallow: bool = False) -> ToolResult:
    """克隆仓库

    Args:
        url: 仓库地址
        target_dir: 目标目录，为空时使用仓库名
        shallow: True 时只克隆最新一层(--depth 1)
    """
    args = ["clone"]
    if shallow:
        args.append("--depth=1")
    args.append(url)
    if target_dir:
        args.append(target_dir)

    # clone 在父目录执行，所以 cwd="."
    return _run(args, os.getcwd(), _TIMEOUT_NETWORK)


# ===================================================================
# P0-1-8  git_branch
# ===================================================================

def git_branch(
    repo_path: str = ".",
    action: str = "list",
    name: str = "",
) -> ToolResult:
    """分支管理: list / create / switch / merge

    Args:
        action: list(默认), create, switch, merge
        name: 分支名（create/switch/merge 时需要）
    """
    cwd = _resolve_repo(repo_path)

    if action == "list":
        return _run(["branch", "-a"], cwd)

    if not name:
        return ToolResult.failure(f"action={action} 需要指定 name 参数")

    if action == "create":
        return _run(["checkout", "-b", name], cwd)

    if action == "switch":
        return _run(["checkout", name], cwd)

    if action == "merge":
        r = _run(["merge", name], cwd, _TIMEOUT_LOCAL)
        if not r.success and "CONFLICT" in r.error:
            return ToolResult.failure(
                f"合并冲突: {name}\n请手动解决冲突后运行 git add . && git commit"
            )
        return r

    return ToolResult.failure(f"未知 action: {action}，可选: list/create/switch/merge")


# ===================================================================
# P0-1-9  git_init
# ===================================================================

def git_init(
    repo_path: str = ".",
    remote_url: str = "",
    branch: str = "main",
) -> ToolResult:
    """初始化仓库 + 可选的远程关联

    Args:
        repo_path: 仓库路径
        remote_url: 远程地址（可选）
        branch: 默认分支名（默认 main）
    """
    cwd = _resolve_repo(repo_path)

    # 1. init
    r_init = _run(["init"], cwd)
    if not r_init.success:
        return ToolResult.failure(f"初始化失败: {r_init.error}")

    # 2. 设默认分支名 (if not main)
    if branch != "master":
        _run(["checkout", "-b", branch], cwd)

    # 3. 关联远程
    if remote_url:
        r_remote = _run(["remote", "add", "origin", remote_url], cwd)
        if not r_remote.success and "already exists" not in r_remote.error:
            return ToolResult.failure(f"远程关联失败: {r_remote.error}")

    return ToolResult.success(f"仓库已初始化: {cwd}\n分支: {branch}" +
                              (f"\n远程: {remote_url}" if remote_url else ""))
