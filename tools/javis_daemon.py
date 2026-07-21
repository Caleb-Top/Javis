"""
Javis 常驻守护进程 — 后台独立运行, 主动提醒, 状态同步
======================================================
功能:
  - 独立后台进程, 网页关闭也不死
  - WebSocket 连接主进程, 断线自动重连
  - 定时器检查到期提醒, 通过 WebSocket 推送
  - 监视 brain_data 变化, 记录事件到日志
  - 网页重启后同步对话状态

架构:
  javis_daemon.py (独立 asyncio loop)
      │
      ├── WebSocket ←→ main.py (FastAPI)
      │                    │
      │                    └── WebSocket ←→ Web 前端
      │
      ├── brain_data/reminders/  (持久化提醒)
      └── brain_data/daemon_state.json (状态同步)

启动方式:
  1. main.py 中 asyncio.create_task(daemon.start())
  2. 或独立进程: python -m tools.javis_daemon
"""

import os, json, time, logging, asyncio, threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("daemon")

BRAIN_DIR = Path(__file__).parent.parent / "brain_data"
REMINDERS_DIR = BRAIN_DIR / "reminders"
STATE_FILE = BRAIN_DIR / "daemon_state.json"
WS_URL = "ws://127.0.0.1:8080/ws_daemon"
HEARTBEAT_INTERVAL = 30
REMINDER_CHECK_INTERVAL = 30


class JavisDaemon:
    """常驻守护进程 — 提醒 + 同步 + 监控"""

    def __init__(self):
        self._ws = None
        self._running = False
        self._session_id = None
        self._last_sync = 0
        self._reminders = []  # 内存中的活跃提醒
        self._pending_messages = []  # 暂存待发送消息
        REMINDERS_DIR.mkdir(parents=True, exist_ok=True)

    # ════════════════════════════════════════════
    # 启动/停止
    # ════════════════════════════════════════════

    async def start(self):
        """启动守护进程主循环"""
        self._running = True
        self._load_state()
        self._load_reminders()

        logger.info("🏠 Javis 守护进程已启动")
        logger.info(f"  提醒文件: {REMINDERS_DIR}")
        logger.info(f"  状态文件: {STATE_FILE}")

        # 并发运行三个循环
        await asyncio.gather(
            self._ws_loop(),
            self._reminder_loop(),
            self._monitor_loop(),
            return_exceptions=True,
        )

    async def stop(self):
        """停止守护进程"""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except:
                pass
        self._save_state()
        logger.info("🏠 Javis 守护进程已停止")

    # ════════════════════════════════════════════
    # WebSocket 连接 (断线自动重连)
    # ════════════════════════════════════════════

    async def _ws_loop(self):
        """WebSocket 循环 — 连接/重连/心跳"""
        while self._running:
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    # 检测主进程是否存活
                    try:
                        r = await asyncio.wait_for(
                            client.get("http://127.0.0.1:8080/api/status", timeout=5),
                            timeout=5
                        )
                        if r.status_code == 200:
                            data = r.json()
                            logger.debug(f"主进程就绪: {data.get('skill', '?')}")
                    except:
                        logger.debug("主进程未就绪, 等待重试...")
                        await asyncio.sleep(5)
                        continue

                # 连接 WebSocket
                import websockets
                self._ws = await websockets.connect(WS_URL, ping_interval=20, ping_timeout=10)
                logger.info("🔗 守护进程已连接主进程 WebSocket")

                # 发送身份标识
                await self._ws.send(json.dumps({"type": "daemon_hello", "payload": {"version": 2}}))

                # 同步状态
                await self._sync_to_main()

                # 心跳 + 消息接收循环
                while self._running:
                    try:
                        await self._ws.send(json.dumps({"type": "ping"}))
                        resp = await asyncio.wait_for(self._ws.recv(), timeout=30)
                        if resp:
                            data = json.loads(resp)
                            if data.get("type") == "state_sync":
                                self._handle_sync(data.get("payload", {}))
                            elif data.get("type") == "add_reminder":
                                self._handle_add_reminder(data.get("payload", {}))
                        await asyncio.sleep(HEARTBEAT_INTERVAL)
                    except Exception as e:
                        logger.debug(f"WS 消息异常: {e}")
                        break
            except Exception as e:
                logger.debug(f"WS 连接失败 (将在 {HEARTBEAT_INTERVAL}s 后重试): {e}")

            # 清理
            try:
                if self._ws:
                    await self._ws.close()
            except:
                pass
            self._ws = None

            if self._running:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

    # ════════════════════════════════════════════
    # 提醒系统
    # ════════════════════════════════════════════

    def add_reminder(self, message: str, delay_seconds: int = 300,
                     repeat: str = "once", context: dict = None):
        """添加一条定时提醒"""
        reminder = {
            "id": f"rem_{int(time.time())}_{len(self._reminders)}",
            "message": message,
            "due_at": time.time() + delay_seconds,
            "repeat": repeat,
            "context": context or {},
            "triggered": False,
            "created_at": time.time(),
        }
        self._reminders.append(reminder)
        self._save_reminders()
        logger.info(f"⏰ 提醒已设置: {message} ({(delay_seconds/60):.0f}分钟后)")
        return reminder["id"]

    async def _check_reminders(self):
        """检查到期提醒"""
        now = time.time()
        triggered = []
        for r in self._reminders:
            if not r.get("triggered") and now >= r.get("due_at", 0):
                r["triggered"] = True
                triggered.append(r)

        if triggered:
            self._save_reminders()
            for r in triggered:
                await self._push_reminder(r)

        return len(triggered)

    async def _push_reminder(self, reminder: dict):
        """推送提醒 (通过 WS 或暂存队列)"""
        msg_text = f"⏰ {reminder['message']}"
        self._pending_messages.append(reminder)

        logger.info(f"🔔 提醒: {reminder['message']}")

        # 通过 WebSocket 推送
        if self._ws:
            try:
                await self._ws.send(json.dumps({
                    "type": "reminder",
                    "payload": {
                        "id": reminder["id"],
                        "message": reminder["message"],
                        "context": reminder.get("context", {}),
                    }
                }))
            except:
                pass

    def get_pending_reminders(self) -> list:
        """获取所有未推送的提醒 (供主进程拉取)"""
        pending = list(self._pending_messages)
        self._pending_messages.clear()
        return pending

    def _handle_add_reminder(self, payload: dict):
        """处理主进程发来的添加提醒请求"""
        msg = payload.get("message", "")
        delay = payload.get("delay_seconds", 300)
        if msg:
            self.add_reminder(msg, delay)

    # ════════════════════════════════════════════
    # 后台循环
    # ════════════════════════════════════════════

    async def _reminder_loop(self):
        """定时检查提醒"""
        while self._running:
            await asyncio.sleep(REMINDER_CHECK_INTERVAL)
            try:
                n = await self._check_reminders()
                if n:
                    logger.debug(f"触发了 {n} 条提醒")
            except Exception as e:
                logger.debug(f"提醒检查异常: {e}")

    async def _monitor_loop(self):
        """监视 brain_data 变化 (每60秒)"""
        last_facts = 0
        last_exps = 0
        while self._running:
            await asyncio.sleep(60)
            try:
                facts_dir = BRAIN_DIR / "facts"
                exps_dir = BRAIN_DIR / "experiences"
                f_count = len(list(facts_dir.glob("*.json"))) if facts_dir.exists() else 0
                e_count = len(list(exps_dir.glob("*.json"))) if exps_dir.exists() else 0

                if f_count != last_facts or e_count != last_exps:
                    logger.info(f"📊 brain_data 变化: facts {last_facts}→{f_count}, exps {last_exps}→{e_count}")
                    last_facts = f_count
                    last_exps = e_count
                    self._save_state()
            except Exception as e:
                logger.debug(f"监控异常: {e}")

    # ════════════════════════════════════════════
    # 状态同步
    # ════════════════════════════════════════════

    async def _sync_to_main(self):
        """向主进程同步状态"""
        pending = self.get_pending_reminders()
        if pending and self._ws:
            try:
                await self._ws.send(json.dumps({
                    "type": "sync",
                    "payload": {
                        "pending_reminders": pending,
                        "daemon_uptime": time.time() - self._last_sync if self._last_sync else 0,
                    }
                }))
            except:
                pass

    def _handle_sync(self, payload: dict):
        """处理主进程发来的同步数据"""
        self._session_id = payload.get("session_id", self._session_id)
        self._last_sync = time.time()
        logger.debug(f"状态已同步: session={self._session_id}")

    # ════════════════════════════════════════════
    # 持久化
    # ════════════════════════════════════════════

    def _save_state(self):
        """保存守护进程状态"""
        try:
            state = {
                "last_sync": self._last_sync,
                "session_id": self._session_id,
                "reminder_count": len(self._reminders),
                "triggered_count": sum(1 for r in self._reminders if r.get("triggered")),
                "pending_count": len(self._pending_messages),
                "updated_at": time.time(),
            }
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"状态保存失败: {e}")

    def _load_state(self):
        """加载守护进程状态"""
        if STATE_FILE.exists():
            try:
                state = json.loads(STATE_FILE.read_text("utf-8"))
                self._last_sync = state.get("last_sync", 0)
                self._session_id = state.get("session_id")
                logger.debug(f"已加载守护进程状态: last_sync={self._last_sync}")
            except Exception as e:
                logger.debug(f"状态加载失败: {e}")

    def _save_reminders(self):
        """持久化提醒到文件"""
        try:
            for r in self._reminders:
                path = REMINDERS_DIR / f"{r['id']}.json"
                path.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.debug(f"提醒保存失败: {e}")

    def _load_reminders(self):
        """从磁盘加载提醒"""
        if REMINDERS_DIR.exists():
            for f in sorted(REMINDERS_DIR.glob("*.json")):
                try:
                    r = json.loads(f.read_text("utf-8"))
                    if not r.get("triggered"):
                        self._reminders.append(r)
                except:
                    pass
        logger.info(f"已加载 {len(self._reminders)} 条活跃提醒")

    # ════════════════════════════════════════════
    # 状态查询
    # ════════════════════════════════════════════

    def status(self) -> dict:
        """守护进程状态"""
        return {
            "running": self._running,
            "ws_connected": self._ws is not None,
            "active_reminders": len([r for r in self._reminders if not r.get("triggered")]),
            "triggered_reminders": sum(1 for r in self._reminders if r.get("triggered")),
            "pending_messages": len(self._pending_messages),
            "session_id": self._session_id,
            "last_sync": datetime.fromtimestamp(self._last_sync).strftime("%H:%M:%S") if self._last_sync else "never",
        }

    def status_text(self) -> str:
        """人类可读的状态"""
        s = self.status()
        lines = [
            f"🏠 Javis 守护进程",
            f"  运行: {'✅' if s['running'] else '❌'}",
            f"  连接: {'✅' if s['ws_connected'] else '❌'}",
            f"  待提醒: {s['active_reminders']}",
            f"  已提醒: {s['triggered_reminders']}",
            f"  待同步: {s['pending_messages']}",
            f"  最后同步: {s['last_sync']}",
        ]
        return "\n".join(lines)


# ════════════════════════════════════════════
# 全局单例
# ════════════════════════════════════════════

_daemon = None


def get_daemon() -> JavisDaemon:
    global _daemon
    if _daemon is None:
        _daemon = JavisDaemon()
    return _daemon


# ════════════════════════════════════════════
# CLI 入口 (独立启动)
# ════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [daemon] %(message)s")

    d = get_daemon()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "add_reminder":
            msg = sys.argv[2] if len(sys.argv) > 2 else "提醒"
            delay = int(sys.argv[3]) if len(sys.argv) > 3 else 300
            d.add_reminder(msg, delay)
            print(f"已添加提醒: {msg} ({(delay/60):.0f}分钟后)")
        elif cmd == "status":
            print(d.status_text())
        elif cmd == "list_reminders":
            for r in d._reminders:
                due = datetime.fromtimestamp(r["due_at"]).strftime("%H:%M")
                status = "✅" if r["triggered"] else "⏳"
                print(f"  {status} {due} {r['message']}")
        else:
            print(f"未知命令: {cmd}")
            print("可用: add_reminder, status, list_reminders")
    else:
        # 启动守护进程
        asyncio.run(d.start())
