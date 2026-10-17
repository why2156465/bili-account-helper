"""静态 SQL 校验：检查代码里 SELECT 的列是否真的在表里存在。

教训来源：/api/me/settings 查了 users.proxy 这一列，但 users 表从来没有这列。
556 项测试全绿也发现不了——因为没有任何测试调用那个接口。
运行：python tests/schema_check.py
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("SECRET_KEY", "schema-check")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="bp-schema-"), "s.db")

from app.db import init_db  # noqa: E402

TABLES = "users bili_accounts proxy_pool interact_tasks automation_rules templates vip_orders traffic_logs redeem_codes notices ai_messages ai_profiles ai_templates publish_sessions publish_files publish_covers publish_records op_logs sessions known_ips email_codes captchas login_tickets quota_usage audit_admin account_snapshots dm_threads dm_messages"

SELECT = re.compile(
    r"SELECT\s+([\w\s,.*()]+?)\s+FROM\s+([a-z_]+)((?:\s+(?:LEFT|INNER|RIGHT)?\s*JOIN\s+[a-z_]+\s+\w+"
    r"(?:\s+ON\s+[\w.=\s]+)?)*)",
    re.I,
)
JOINED = re.compile(r"JOIN\s+([a-z_]+)\s+(\w+)", re.I)


def load_schema() -> dict[str, set[str]]:
    init_db()
    from app.config import settings
    c = sqlite3.connect(settings.DB_PATH)
    out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        out[t] = {r[1] for r in c.execute("PRAGMA table_info(%s)" % t)}
    c.close()
    return out


def main() -> int:
    schema = load_schema()
    print("表数量:", len(schema))

    bad = []
    checked = 0
    for f in glob.glob(os.path.join(ROOT, "app", "**", "*.py"), recursive=True):
        src = open(f, encoding="utf-8").read()
        for m in SELECT.finditer(src):
            cols, tbl, joins = m.group(1), m.group(2).lower(), (m.group(3) or "")
            if tbl not in schema:
                continue

            # 展开 JOIN：别名 -> 真实表，用于判断 col 属于哪张表
            alias_to_table = {}
            joined_tables = set()
            for jm in JOINED.finditer(joins):
                jt, alias = jm.group(1).lower(), jm.group(2)
                if jt in schema:
                    alias_to_table[alias] = jt
                    joined_tables.add(jt)

            for raw in cols.split(","):
                col = raw.strip()
                if not col or col == "*" or "(" in col or ")" in col:
                    continue
                col = re.sub(r"\s+AS\s+\w+$", "", col, flags=re.I).strip()
                parts = col.split(".")
                if len(parts) == 2:
                    qual, name = parts[0].strip(), parts[1].strip()
                    owner = alias_to_table.get(qual, qual.lower())
                    if owner not in schema:
                        continue          # 别名/子查询，跳过
                    tables_to_try = [owner]
                else:
                    name = parts[0].strip()
                    # 未限定：可能来自 FROM 表或任一 JOIN 表
                    tables_to_try = [tbl] + sorted(joined_tables)
                if not name or not name.isidentifier():
                    continue
                checked += 1
                if not any(name in schema[t] for t in tables_to_try if t in schema):
                    line = src[: m.start()].count("\n") + 1
                    bad.append((os.path.basename(f), line, tbl, name))

    print("检查列引用:", checked)
    print()
    if bad:
        print("发现 %d 处引用了不存在的列：" % len(bad))
        for f, line, tbl, col in bad:
            print("  ✗ %s:%d  表 %s 没有列 %s" % (f, line, tbl, col))
        return 1
    print("所有 SELECT 列都存在于对应表中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
