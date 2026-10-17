"""本地启动：python run_local.py

做三件事：读 .env -> 建表 -> 起 uvicorn。
首次运行会顺带创建一个演示账号，并把验证码打到控制台（未配 SMTP 时的开发模式）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def seed_demo() -> None:
    """建演示账号：demo01 / passw0rdX，并预置一条已知 IP 省掉异地验证。"""
    from app.db import db, init_db
    from app.security import hash_password, new_invite_code

    init_db()
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username='demo01'").fetchone()
        if row:
            print("[seed] 演示账号已存在：demo01 / passw0rdX")
            return
        cur = conn.execute(
            "INSERT INTO users(username,email,password_hash,role,plan,plan_until,traffic_mb,"
            "invite_code,created_at) VALUES(?,?,?,'user','free',0,300,?,?)",
            ("demo01", "demo01@example.com", hash_password("passw0rdX"),
             new_invite_code(), int(time.time())),
        )
        for ip in ("127.0.0.1", "::1", "testclient"):
            conn.execute(
                "INSERT OR IGNORE INTO known_ips(user_id,ip,created_at) VALUES(?,?,?)",
                (cur.lastrowid, ip, int(time.time())),
            )
    print("[seed] 已创建演示账号：demo01 / passw0rdX")


def main() -> None:
    load_env()
    os.environ.setdefault("SECRET_KEY", "local-dev-only-secret")
    seed_demo()

    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print("\n" + "=" * 56)
    print("  %s 已启动" % os.environ.get("APP_NAME", "B站账号管理助手"))
    print("  首页      http://%s:%d/" % (host, port))
    print("  登录      http://%s:%d/login      (demo01 / passw0rdX)" % (host, port))
    print("  控制台    http://%s:%d/dashboard" % (host, port))
    print("  管理后台  http://%s:%d/%s" % (host, port, os.environ.get("ADMIN_PATH", "admin234")))
    print("  未配 SMTP 时，邮箱验证码会打印在本终端（[MAIL-DEV]）")
    print("=" * 56 + "\n")
    uvicorn.run("app.main:app", host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
