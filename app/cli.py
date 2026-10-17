"""初始化管理员账号：python -m app.cli create-admin"""
from __future__ import annotations

import argparse
import getpass
import sys
import time

from .db import db, init_db
from .security import hash_password


def create_admin(username: str, email: str, password: str) -> None:
    init_db()
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=? OR email=?",
                        (username, email)).fetchone():
            print("用户已存在")
            sys.exit(1)
        conn.execute(
            "INSERT INTO users(username,email,password_hash,role,plan,plan_until,traffic_mb,"
            "created_at) VALUES(?,?,?,'admin','vip',0,-1,?)",
            (username, email, hash_password(password), int(time.time())),
        )
    print("管理员已创建：%s" % username)


def reset_password(email: str, password: str) -> None:
    init_db()
    with db() as conn:
        cur = conn.execute("UPDATE users SET password_hash=? WHERE email=?",
                           (hash_password(password), email))
        print("已更新 %d 个账号" % cur.rowcount)


def main() -> None:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("create-admin")
    a.add_argument("username")
    a.add_argument("email")

    r = sub.add_parser("reset-password")
    r.add_argument("email")

    sub.add_parser("init-db")

    args = ap.parse_args()
    if args.cmd == "create-admin":
        pw = getpass.getpass("密码: ")
        create_admin(args.username, args.email, pw)
    elif args.cmd == "reset-password":
        pw = getpass.getpass("新密码: ")
        reset_password(args.email, pw)
    else:
        init_db()
        print("数据库已初始化")


if __name__ == "__main__":
    main()
