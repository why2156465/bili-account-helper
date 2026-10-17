"""SQLite 建表与迁移。字段命名与前端/API 保持一致。"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from .config import settings

# 每线程事务状态：让 db() 可重入，避免在事务里再开事务导致 database is locked
_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',      -- user | admin
    plan          TEXT NOT NULL DEFAULT 'free',      -- free | vip
    plan_until    INTEGER NOT NULL DEFAULT 0,        -- unix ts
    traffic_mb    INTEGER NOT NULL DEFAULT 300,      -- 总流量包(MB)
    traffic_used_mb INTEGER NOT NULL DEFAULT 0,      -- 已用流量(MB)
    plan_tier     TEXT NOT NULL DEFAULT '',          -- month | quarter | year
    verify_login  INTEGER NOT NULL DEFAULT 1,        -- 异地登录邮箱验证开关
    email_verify  INTEGER NOT NULL DEFAULT 1,        -- 设置页通知开关
    invite_code   TEXT UNIQUE,
    invited_by    TEXT,
    banned        INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    last_login_at INTEGER NOT NULL DEFAULT 0,
    last_login_ip TEXT
);

CREATE TABLE IF NOT EXISTS known_ips (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    ip         TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, ip)
);

CREATE TABLE IF NOT EXISTS email_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    scene      TEXT NOT NULL,        -- register | reset | verify
    code_hash  TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_codes_email ON email_codes(email, scene);

CREATE TABLE IF NOT EXISTS captchas (
    token      TEXT PRIMARY KEY,
    answer     TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS login_tickets (
    ticket      TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    ip          TEXT,
    expires_at  INTEGER NOT NULL,
    verified    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    sid        TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    ip         TEXT,
    ua         TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

-- B 站账号：字段名严格对齐原站 /api/accounts 返回的对象
CREATE TABLE IF NOT EXISTS bili_accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    mid           TEXT,                          -- B 站 UID
    uname         TEXT,                          -- 真实昵称（原站字段名 uname）
    face          TEXT,                          -- 真实头像 URL
    fans          INTEGER NOT NULL DEFAULT 0,
    videos        INTEGER NOT NULL DEFAULT 0,
    coins         REAL    NOT NULL DEFAULT 0,
    level         INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'ok', -- ok | expired | captcha
    coin_ok       INTEGER NOT NULL DEFAULT 0,    -- bili_ticket 是否有效（投币用）
    cred_source   TEXT    NOT NULL DEFAULT 'ck', -- qr | sms | ck | ckfile | paste
    last_check    TEXT    NOT NULL DEFAULT '',   -- 体检时间（展示用字符串）
    proxy         TEXT    NOT NULL DEFAULT '',   -- 该号专属出口 IP，空=跟随平台策略
    alias_name    TEXT,                          -- 隐私展示名（只影响本站显示）
    alias_mid     TEXT,
    alias_face    TEXT,
    cred_enc      TEXT NOT NULL,                 -- Fernet 加密后的 Cookie
    cred_mask     TEXT NOT NULL DEFAULT '',      -- 只显示键名与长度
    coeff         REAL NOT NULL DEFAULT 1.0,     -- 风控系数
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acc_user ON bili_accounts(user_id);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL,      -- like | upload | dm | ai | monitor
    title       TEXT NOT NULL,
    account_id  INTEGER,
    config      TEXT NOT NULL DEFAULT '{}',   -- JSON
    cron        TEXT,               -- 五段 cron，NULL 表示一次性
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    last_run_at INTEGER NOT NULL DEFAULT 0,
    next_run_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_user ON tasks(user_id);

CREATE TABLE IF NOT EXISTS task_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    status     TEXT NOT NULL,       -- running | ok | fail | skipped
    message    TEXT,
    started_at INTEGER NOT NULL,
    ended_at   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_run_task ON task_runs(task_id);

CREATE TABLE IF NOT EXISTS op_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    account_id INTEGER,
    action     TEXT NOT NULL,
    target     TEXT,
    result     TEXT,
    code       INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oplog_user ON op_logs(user_id);

CREATE TABLE IF NOT EXISTS dm_threads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    peer_uid   TEXT NOT NULL,
    peer_nm    TEXT,
    last_msg   TEXT,
    unread     INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, account_id, peer_uid)
);

CREATE TABLE IF NOT EXISTS dm_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  INTEGER NOT NULL,
    direction  TEXT NOT NULL,       -- in | out
    body       TEXT,
    img_url    TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proxy_pool (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT,
    url        TEXT NOT NULL,       -- scheme://user:pass@host:port
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    day        TEXT NOT NULL,       -- YYYY-MM-DD
    n          INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, kind, day)
);


CREATE TABLE IF NOT EXISTS vip_orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no   TEXT NOT NULL UNIQUE,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    amount     REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    paid_at    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS traffic_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    delta_mb   INTEGER NOT NULL,
    reason     TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS redeem_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,
    value      INTEGER NOT NULL,
    used_by    INTEGER,
    used_at    INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    title      TEXT,
    body       TEXT,
    read       INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'main',
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_profiles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    account_id INTEGER,
    name       TEXT NOT NULL,
    persona    TEXT,
    scope      TEXT NOT NULL DEFAULT 'main',
    model      TEXT,
    created_at INTEGER NOT NULL
);


CREATE TABLE IF NOT EXISTS interact_tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'interact',
    cron       TEXT NOT NULL,
    config     TEXT NOT NULL DEFAULT '{}',
    enabled    INTEGER NOT NULL DEFAULT 1,
    last_run   INTEGER NOT NULL DEFAULT 0,
    next_run   INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'interact',
    config     TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);


CREATE TABLE IF NOT EXISTS automation_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    account_id INTEGER,
    interval   INTEGER NOT NULL DEFAULT 60,
    config     TEXT NOT NULL DEFAULT '{}',
    enabled    INTEGER NOT NULL DEFAULT 1,
    last_run   INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);


CREATE TABLE IF NOT EXISTS publish_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'local',
    name       TEXT,
    size       INTEGER NOT NULL DEFAULT 0,
    account_id INTEGER,
    info       TEXT NOT NULL DEFAULT '{}',
    pct        INTEGER NOT NULL DEFAULT 0,
    state      TEXT NOT NULL DEFAULT 'running',
    message    TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS publish_files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    code       INTEGER NOT NULL DEFAULT 1,
    name       TEXT NOT NULL,
    path       TEXT NOT NULL,
    size       INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_covers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    code       INTEGER NOT NULL DEFAULT 1,
    path       TEXT NOT NULL,
    ratio      TEXT NOT NULL DEFAULT '16:9',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_records (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    account_id INTEGER,
    title      TEXT,
    bvid       TEXT,
    aid        TEXT,
    tid        TEXT,
    cover      TEXT,
    status     TEXT NOT NULL DEFAULT 'ok',
    created_at INTEGER NOT NULL
);


CREATE TABLE IF NOT EXISTS ai_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'main',
    model      TEXT,
    persona    TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS account_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    day        TEXT NOT NULL,             -- YYYY-MM-DD
    views      INTEGER NOT NULL DEFAULT 0,
    likes      INTEGER NOT NULL DEFAULT 0,
    fans       INTEGER NOT NULL DEFAULT 0,
    videos     INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(account_id, day)
);

CREATE TABLE IF NOT EXISTS audit_admin (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT,
    created_at INTEGER NOT NULL
);
"""


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


def connect() -> sqlite3.Connection:
    _ensure_dir(settings.DB_PATH)
    conn = sqlite3.connect(settings.DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """一次事务：进入即 BEGIN IMMEDIATE 拿写锁，避免长事务互等导致 database is locked。

    可重入：同一线程已持有事务时内层复用外层连接，
    这样在 db() 块里调 log_op() / admin_audit() 不会自己跟自己抢锁。
    """
    st = _local
    depth = getattr(st, "depth", 0)
    if depth:
        st.depth = depth + 1
        try:
            yield st.conn
        finally:
            st.depth = depth
        return

    conn = connect()
    st.conn = conn
    st.depth = 1
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        if conn.in_transaction:
            conn.execute("COMMIT")
    except Exception:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        st.depth = 0
        st.conn = None
        conn.close()


def init_db() -> None:
    # executescript 自带事务控制，直接走原始连接
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
