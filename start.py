"""Javis 启动器 — 自动守护、崩溃恢复、日志管理
用法:
  python start.py                  # 默认 8080 端口
  python start.py --port 8080      # 指定端口
  python start.py --no-restart     # 崩溃后不重启
"""
import os, sys, time, subprocess, socket, locale
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{level}] {msg}\n")
    except Exception:
        pass


def is_port_free(port: int) -> bool:
    """检查端口是否可用"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        result = s.connect_ex(('127.0.0.1', port))
        s.close()
        return result != 0
    except Exception:
        return True


def kill_port(port: int):
    """强制释放端口"""
    try:
        import subprocess
        import re
        if sys.platform == "win32":
            r = subprocess.run(
                f'netstat -ano | findstr ":{port} " | findstr LISTENING',
                shell=True, capture_output=True, text=True, timeout=5
            )
            for line in r.stdout.splitlines():
                parts = line.strip().split()
                if parts and parts[-1].isdigit():
                    pid = int(parts[-1])
                    if pid and pid != os.getpid():
                        subprocess.run(f"taskkill /F /PID {pid}",
                                       shell=True, capture_output=True, timeout=3)
                        log(f"已释放端口 {port} (PID {pid})")
        else:
            subprocess.run(f"fuser -k {port}/tcp", shell=True, capture_output=True, timeout=3)
    except Exception as e:
        log(f"释放端口失败: {e}", "WARN")


def run_server(port: int, max_restarts: int = 5):
    """运行 Javis 服务器，支持崩溃重启"""
    python = str(ROOT / "venv" / "Scripts" / "python.exe") if (ROOT / "venv" / "Scripts" / "python.exe").exists() else "python"

    env = os.environ.copy()
    env["PORT"] = str(port)
    if "PYTHONUNBUFFERED" not in env:
        env["PYTHONUNBUFFERED"] = "1"

    restart_count = 0
    last_crash_time = 0

    while restart_count < max_restarts:
        log(f"启动 Javis (端口 {port}, 第 {restart_count + 1} 次)")
        log(f"日志文件: {LOG_FILE}")

        try:
            proc = subprocess.Popen(
                [python, "-u", str(ROOT / "main.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, cwd=str(ROOT),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                bufsize=1, universal_newlines=True, encoding="utf-8", errors="replace"
            )
        except FileNotFoundError:
            log(f"Python 未找到: {python}", "ERROR")
            sys.exit(1)

        # 读取输出直到进程退出
        log_file_fh = open(LOG_FILE, "a", encoding="utf-8")
        try:
            for line in proc.stdout:
                stripped = line.rstrip("\n\r")
                # 终端编码兼容: 如果是 GBK 环境，转码并替换不可见字符
                try:
                    enc = locale.getpreferredencoding() or "utf-8"
                except Exception:
                    enc = "utf-8"
                if enc.upper() not in ("UTF-8", "UTF8"):
                    stripped = stripped.encode(enc, errors="replace").decode(enc)
                print(stripped)
                log_file_fh.write(line.rstrip("\n\r") + "\n")
                log_file_fh.flush()
        except Exception:
            pass
        finally:
            try:
                log_file_fh.close()
            except Exception:
                pass

        returncode = proc.poll()
        now = time.time()

        if returncode == 0:
            log("服务器正常退出", "INFO")
            break

        if returncode == -9 or returncode == -15:
            log(f"服务器被终止 (signal {abs(returncode)})", "INFO")
            break

        # 检查是否刚重启过（防止快速崩溃循环）
        if now - last_crash_time < 5:
            restart_count += 1
            log(f"快速崩溃 #{restart_count}, 等待重试...", "WARN")
            if restart_count >= max_restarts:
                log(f"服务器连续崩溃 {max_restarts} 次，停止重试", "FATAL")
                break
            time.sleep(2 ** restart_count)  # 指数退避
        else:
            restart_count = 0  # 正常运行了一段时间，重置计数器

        last_crash_time = now
        restart_count += 1
        time.sleep(1)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Javis 服务器启动器")
    parser.add_argument("--port", "-p", type=int, default=8080, help="监听端口")
    parser.add_argument("--no-restart", action="store_true", help="崩溃后不重启")
    args = parser.parse_args()

    print("=" * 38)
    print("  J.A.R.V.I.S  |  服务启动器")
    print("=" * 38)
    print(f"  [端口] {args.port}")
    print(f"  [日志] {LOG_FILE}")
    print(f"  [恢复] {'关闭' if args.no_restart else '开启 (最多5次)'}")
    print()

    if not is_port_free(args.port):
        log(f"端口 {args.port} 已被占用，尝试释放...", "WARN")
        kill_port(args.port)
        time.sleep(2)

    try:
        run_server(args.port, max_restarts=0 if args.no_restart else 5)
    except KeyboardInterrupt:
        log("用户中断", "INFO")
    finally:
        kill_port(args.port)
        log("服务已停止")


if __name__ == "__main__":
    main()
