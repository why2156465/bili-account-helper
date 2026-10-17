"""一次性迁移脚本：把项目从「我自己设计的骨架」改成「对齐原站契约」。

1. users 增加 plan_tier / traffic_used_mb
2. 新增 vip_orders / traffic_logs / redeem_codes / notices / ai_messages / ai_profiles
3. main.py 接入 vip_api，misc.py 移除旧 /api/vip 路由
4. proxy_pool.label -> name
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def patch(path: Path, old: str, new: str, required: bool = True) -> bool:
    txt = path.read_text(encoding="utf-8")
    if old not in txt:
        if required:
            print("!! 未找到片段:", path.name, old[:50].replace("\n", " "))
        return False
    path.write_text(txt.replace(old, new, 1), encoding="utf-8")
    print("patched", path.name, "->", new[:40].replace("\n", " "))
    return True


SCHEMA_EXTRA = """
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
    name       TEXT NOT NULL,
    persona    TEXT,
    scope      TEXT NOT NULL DEFAULT 'main',
    model      TEXT,
    created_at INTEGER NOT NULL
);

"""


def main() -> None:
    db = APP / "db.py"
    patch(db, "    traffic_mb    INTEGER NOT NULL DEFAULT 300,      -- 剩余流量",
          "    traffic_mb    INTEGER NOT NULL DEFAULT 300,      -- 总流量包(MB)\n"
          "    traffic_used_mb INTEGER NOT NULL DEFAULT 0,      -- 已用流量(MB)\n"
          "    plan_tier     TEXT NOT NULL DEFAULT '',          -- month | quarter | year")
    patch(db, "CREATE TABLE IF NOT EXISTS audit_admin (", SCHEMA_EXTRA + "CREATE TABLE IF NOT EXISTS audit_admin (")

    main_py = APP / "main.py"
    patch(main_py, "from .routers import accounts, ai, auth_api, dashboard, library, like, misc, pages, proxy, tasks_api",
          "from .routers import (accounts, ai, auth_api, dashboard, library, like, misc,\n"
          "                     pages, proxy, tasks_api, vip_api)")
    patch(main_py, "for r in (auth_api.router, accounts.router, like.router, tasks_api.router",
          "for r in (auth_api.router, accounts.router, proxy.router, like.router,\n"
          "          tasks_api.router")
    patch(main_py, "ai.router, dashboard.router, library.router, misc.router,",
          "          ai.router, dashboard.router, library.router, misc.router,\n"
          "          vip_api.router,")

    # misc.py：删掉旧的会员与代理块
    misc = APP / "routers" / "misc.py"
    txt = misc.read_text(encoding="utf-8")
    for start_mark, end_mark in (("# ---------------- 会员 ----------------", "# ---------------- B 站只读代理"),
                                 ("# ---------------- 代理池 ----------------", "# ---------------- 管理后台 ----------------")):
        s, e = txt.find(start_mark), txt.find(end_mark)
        if s >= 0 and e > s:
            txt = txt[:s] + txt[e:]
            print("misc.py: 删除段落", start_mark)
    misc.write_text(txt, encoding="utf-8")

    # proxy_pool.label -> name
    patch(db, "    label      TEXT,", "    name       TEXT,", required=False)

    print("done")


if __name__ == "__main__":
    main()
