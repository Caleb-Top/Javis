"""
记忆索引引擎 — SQLite 持久化检索层
====================================
职责:
  - 构建 brain_data/*.json 的读优化 SQLite 索引
  - FTS5 全文检索 (facts)
  - 不删除/不修改任何 JSON 文件 (源数据 0 丢失)
  - 索引不可用时自动静默降级

架构:
  Layer 0: JSON 文件 (源数据, 永不删除)
  Layer 1: SQLite (读优化索引, 可随时重建)
"""

import json, time, logging, os, glob
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("memory.indexer")

BRAIN_DIR = Path(__file__).parent.parent / "brain_data"
DB_PATH = BRAIN_DIR / "memory.db"
DB_READY = False

# ════════════════════════════════════════════
# SQLite 初始化
# ════════════════════════════════════════════

def _get_db():
    """获取数据库连接 (惰性初始化)"""
    global DB_READY
    import sqlite3
    db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=OFF")
    if not DB_READY:
        _create_tables(db)
        DB_READY = True
    return db


def _create_tables(db):
    """创建表结构"""
    # Episodic memory
    db.executescript("""
        CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            user_input TEXT,
            fingerprint_domain TEXT,
            fingerprint_task_type TEXT,
            fingerprint_platform TEXT,
            fingerprint_tools TEXT,
            outcome TEXT,
            tool_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            start_time REAL,
            end_time REAL,
            summary TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ep_domain ON episodes(fingerprint_domain);
        CREATE INDEX IF NOT EXISTS idx_ep_time ON episodes(start_time);

        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            content TEXT,
            category TEXT,
            source TEXT,
            confidence REAL DEFAULT 0.5,
            priority INTEGER DEFAULT 1,
            usage_count INTEGER DEFAULT 0,
            created_at REAL,
            updated_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_fact_prio ON facts(priority);
        CREATE INDEX IF NOT EXISTS idx_fact_cat ON facts(category);

        CREATE TABLE IF NOT EXISTS experiences (
            id TEXT PRIMARY KEY,
            intent TEXT,
            action TEXT,
            result TEXT,
            error TEXT,
            lesson TEXT,
            pattern TEXT,
            priority INTEGER DEFAULT 1,
            domain TEXT,
            error_category TEXT,
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_exp_domain ON experiences(domain);
        CREATE INDEX IF NOT EXISTS idx_exp_prio ON experiences(priority);

        CREATE TABLE IF NOT EXISTS semantic_rules (
            id TEXT PRIMARY KEY,
            fingerprint_domain TEXT,
            fingerprint_task_type TEXT,
            fingerprint_tool TEXT,
            conclusion TEXT,
            risk_level REAL DEFAULT 0.5,
            confidence REAL DEFAULT 0.5,
            evidence_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_rule_domain ON semantic_rules(fingerprint_domain);

        CREATE TABLE IF NOT EXISTS procedural_chains (
            id TEXT PRIMARY KEY,
            domain TEXT,
            task_type TEXT,
            tool_sequence TEXT,
            success_rate REAL DEFAULT 0.0,
            execution_count INTEGER DEFAULT 0,
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_proc_domain ON procedural_chains(domain);

        -- Metadata: track last sync time
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    db.commit()


# ════════════════════════════════════════════
# 全量重建索引
# ════════════════════════════════════════════

def rebuild_index():
    """从所有 JSON 文件全量重建 SQLite 索引"""
    db = _get_db()
    t0 = time.time()

    # Clear existing data
    for table in ["episodes", "facts", "experiences", "semantic_rules", "procedural_chains"]:
        db.execute(f"DELETE FROM {table}")

    counts = {"episodes": 0, "facts": 0, "experiences": 0, "rules": 0, "procedural": 0}

    # Episodes
    for fp in sorted((BRAIN_DIR / "episodes").glob("*.json")):
        try:
            d = json.loads(fp.read_text("utf-8"))
            fp_data = d.get("fingerprint", {})
            tools = fp_data.get("tools_involved", [])
            db.execute("""
                INSERT OR REPLACE INTO episodes
                (id, session_id, user_input, fingerprint_domain, fingerprint_task_type,
                 fingerprint_platform, fingerprint_tools, outcome,
                 tool_count, failure_count, duration_ms, start_time, end_time, summary)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get("id", fp.stem), d.get("session_id", ""),
                (d.get("user_input") or "")[:200],
                fp_data.get("domain", ""), fp_data.get("task_type", ""),
                fp_data.get("platform", ""),
                json.dumps(tools[:5], ensure_ascii=False),
                d.get("outcome", ""),
                d.get("tool_count", 0), d.get("failure_count", 0),
                d.get("duration_ms", 0),
                d.get("start_time", 0), d.get("end_time", 0),
                (d.get("user_input") or "")[:100])
            )
            counts["episodes"] += 1
        except:
            pass

    # Facts
    for fp in sorted((BRAIN_DIR / "facts").glob("*.json")):
        try:
            d = json.loads(fp.read_text("utf-8"))
            db.execute("""
                INSERT OR REPLACE INTO facts
                (id, content, category, source, confidence, priority, usage_count, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                d.get("id", fp.stem), d.get("content", ""),
                d.get("category", ""), d.get("source", ""),
                d.get("confidence", 0.5), d.get("priority", 1),
                d.get("usage_count", 0),
                d.get("created_at", 0), d.get("updated_at", 0))
            )
            counts["facts"] += 1
        except:
            pass

    # Create FTS5 index for facts
    try:
        db.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                content, category,
                content=facts, content_rowid=rowid
            );
            INSERT INTO facts_fts(facts_fts) VALUES('rebuild');
        """)
    except:
        pass

    # Experiences
    for fp in sorted((BRAIN_DIR / "experiences").glob("*.json")):
        try:
            d = json.loads(fp.read_text("utf-8"))
            db.execute("""
                INSERT OR REPLACE INTO experiences
                (id, intent, action, result, error, lesson, pattern,
                 priority, domain, error_category, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get("id", fp.stem), d.get("intent", ""),
                d.get("action", ""), d.get("result", ""),
                d.get("error", ""), d.get("lesson", ""),
                d.get("pattern", ""), d.get("priority", 1),
                d.get("domain", ""), d.get("error_category", ""),
                d.get("created_at", 0))
            )
            counts["experiences"] += 1
        except:
            pass

    # Semantic rules
    for fp in sorted((BRAIN_DIR / "semantic").glob("*.json")):
        try:
            d = json.loads(fp.read_text("utf-8"))
            fp_data = d.get("fingerprint", {})
            db.execute("""
                INSERT OR REPLACE INTO semantic_rules
                (id, fingerprint_domain, fingerprint_task_type, fingerprint_tool,
                 conclusion, risk_level, confidence, evidence_count, status, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get("id", fp.stem),
                fp_data.get("domain", ""),
                fp_data.get("task_type", ""),
                fp_data.get("tool", ""),
                d.get("conclusion", ""),
                d.get("risk_level", 0.5),
                d.get("confidence", 0.5),
                d.get("evidence_count", 0),
                d.get("status", "active"),
                d.get("created_at", 0))
            )
            counts["rules"] += 1
        except:
            pass

    # Procedural chains
    for fp in sorted((BRAIN_DIR / "procedural").glob("*.json")):
        try:
            d = json.loads(fp.read_text("utf-8"))
            db.execute("""
                INSERT OR REPLACE INTO procedural_chains
                (id, domain, task_type, tool_sequence, success_rate, execution_count, created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (
                d.get("id", fp.stem), d.get("domain", ""),
                d.get("task_type", ""),
                json.dumps(d.get("tool_sequence", []), ensure_ascii=False),
                d.get("success_rate", 0.0),
                d.get("execution_count", 0),
                d.get("created_at", 0))
            )
            counts["procedural"] += 1
        except:
            pass

    # Update meta
    db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_rebuild',?)",
               (str(time.time()),))
    db.commit()

    elapsed = time.time() - t0
    logger.info(f"SQlite 索引重建完成: {counts} ({elapsed*1000:.0f}ms)")
    return counts


# ════════════════════════════════════════════
# 增量同步
# ════════════════════════════════════════════

_last_sync = 0

def ensure_index():
    """确保索引就绪 (惰性重建)"""
    if not DB_PATH.exists() or DB_PATH.stat().st_size < 1000:
        rebuild_index()
        return

    # 检查 meta 是否过时
    db = _get_db()
    row = db.execute("SELECT value FROM meta WHERE key='last_rebuild'").fetchone()
    if not row:
        rebuild_index()


def incremental_sync():
    """增量同步（检查文件 mtime, 只同步变更的）"""
    global _last_sync
    ensure_index()
    db = _get_db()

    now = time.time()
    if now - _last_sync < 30:
        return
    _last_sync = now

    # Check if any JSON files changed since last rebuild
    row = db.execute("SELECT value FROM meta WHERE key='last_rebuild'").fetchone()
    last_rebuild = float(row[0]) if row else 0

    changed_dirs = []
    for dirname in ["episodes", "facts", "experiences", "semantic", "procedural"]:
        d = BRAIN_DIR / dirname
        if d.exists():
            for fp in d.glob("*.json"):
                if fp.stat().st_mtime > last_rebuild:
                    changed_dirs.append(dirname)
                    break

    if changed_dirs:
        logger.debug(f"检测到数据变更: {changed_dirs}, 重建索引")
        rebuild_index()


# ════════════════════════════════════════════
# 检索函数
# ════════════════════════════════════════════

def search_facts(query: str = "", domain: str = "", priority_min: int = 1, limit: int = 5) -> list[dict]:
    """检索 facts (FTS5 全文搜索 + 优先级排序)"""
    try:
        ensure_index()
        db = _get_db()

        if query:
            rows = db.execute("""
                SELECT f.* FROM facts f
                JOIN facts_fts ON f.rowid = facts_fts.rowid
                WHERE facts_fts MATCH ?
                ORDER BY f.priority DESC, f.usage_count DESC
                LIMIT ?
            """, (query, limit)).fetchall()
        elif domain:
            rows = db.execute("""
                SELECT * FROM facts
                WHERE category LIKE ? AND priority >= ?
                ORDER BY priority DESC, usage_count DESC
                LIMIT ?
            """, (f"{domain}%", priority_min, limit)).fetchall()
        else:
            rows = db.execute("""
                SELECT * FROM facts
                WHERE priority >= ?
                ORDER BY priority DESC, usage_count DESC
                LIMIT ?
            """, (priority_min, limit)).fetchall()

        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"search_facts 失败: {e}")
        return []


def get_priority_facts(min_priority: int = 5, limit: int = 10) -> list[dict]:
    """获取优先级事实 (永不截断)"""
    try:
        ensure_index()
        db = _get_db()
        rows = db.execute("""
            SELECT * FROM facts
            WHERE priority >= ?
            ORDER BY priority DESC, updated_at DESC
            LIMIT ?
        """, (min_priority, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"get_priority_facts 失败: {e}")
        return []


def search_experiences(domain: str = "", limit: int = 3) -> list[dict]:
    """按领域检索经验"""
    try:
        ensure_index()
        db = _get_db()
        if domain:
            rows = db.execute("""
                SELECT * FROM experiences
                WHERE domain = ? AND lesson != '' AND length(lesson) > 15
                ORDER BY priority DESC, created_at DESC
                LIMIT ?
            """, (domain, limit)).fetchall()
        else:
            rows = db.execute("""
                SELECT * FROM experiences
                WHERE lesson != '' AND length(lesson) > 10
                ORDER BY priority DESC, created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"search_experiences 失败: {e}")
        return []


def search_episodes(domain: str = "", task_type: str = "", limit: int = 3) -> list[dict]:
    """检索情景记忆"""
    try:
        ensure_index()
        db = _get_db()
        if domain and task_type:
            rows = db.execute("""
                SELECT * FROM episodes
                WHERE fingerprint_domain = ? AND fingerprint_task_type = ?
                ORDER BY start_time DESC
                LIMIT ?
            """, (domain, task_type, limit)).fetchall()
        elif domain:
            rows = db.execute("""
                SELECT * FROM episodes
                WHERE fingerprint_domain = ?
                ORDER BY start_time DESC
                LIMIT ?
            """, (domain, limit)).fetchall()
        else:
            rows = db.execute("""
                SELECT * FROM episodes
                ORDER BY start_time DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"search_episodes 失败: {e}")
        return []


def search_rules(domain: str = "", limit: int = 2) -> list[dict]:
    """检索语义规则"""
    try:
        ensure_index()
        db = _get_db()
        if domain:
            rows = db.execute("""
                SELECT * FROM semantic_rules
                WHERE fingerprint_domain = ? AND status = 'active'
                ORDER BY confidence DESC
                LIMIT ?
            """, (domain, limit)).fetchall()
        else:
            rows = db.execute("""
                SELECT * FROM semantic_rules
                WHERE status = 'active'
                ORDER BY confidence DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"search_rules 失败: {e}")
        return []


def search_procedural(domain: str = "", task_type: str = "", limit: int = 2) -> list[dict]:
    """检索程序记忆链"""
    try:
        ensure_index()
        db = _get_db()
        if domain and task_type:
            rows = db.execute("""
                SELECT * FROM procedural_chains
                WHERE domain = ? AND task_type = ? AND success_rate > 0.7
                ORDER BY success_rate DESC, execution_count DESC
                LIMIT ?
            """, (domain, task_type, limit)).fetchall()
        elif domain:
            rows = db.execute("""
                SELECT * FROM procedural_chains
                WHERE domain = ? AND success_rate > 0.7
                ORDER BY success_rate DESC
                LIMIT ?
            """, (domain, limit)).fetchall()
        else:
            rows = db.execute("""
                SELECT * FROM procedural_chains
                WHERE success_rate > 0.7
                ORDER BY execution_count DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"search_procedural 失败: {e}")
        return []


def index_status() -> dict:
    """索引状态"""
    try:
        ensure_index()
        db = _get_db()
        counts = {}
        for table in ["episodes", "facts", "experiences", "semantic_rules", "procedural_chains"]:
            row = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = row[0] if row else 0
        row = db.execute("SELECT value FROM meta WHERE key='last_rebuild'").fetchone()
        last_rebuild = float(row[0]) if row else 0
        return {
            **counts,
            "db_size_mb": round(DB_PATH.stat().st_size / 1024 / 1024, 2) if DB_PATH.exists() else 0,
            "last_rebuild": time.strftime('%H:%M:%S', time.localtime(last_rebuild)) if last_rebuild else "never",
        }
    except:
        return {"error": "index not available"}
