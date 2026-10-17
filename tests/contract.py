"""契约对齐测试：验证我们实现的接口形状与线上 /api 一致。

运行：python tests/contract.py
不访问 B 站，全部用临时库。
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-contract-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "contract-test-key"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402

FAILS: list[str] = []


def ck(name: str, cond: bool, extra: object = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name +
          ((" -> " + str(extra)[:220]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def cap(c: TestClient):
    d = c.get("/api/auth/captcha").json()
    q = d["q"]
    nums = [int(x) for x in re.findall(r"\d+", q)]
    sym = re.search(r"[+\-\u00d7\u00d7]", q)
    op = sym.group(0) if sym else "+"
    ans = {"+": nums[0] + nums[1], "-": nums[0] - nums[1],
           "\u00d7": nums[0] * nums[1]}.get(op, nums[0] + nums[1])
    return str(ans), d["token"]


def forge(email: str, scene: str, code: str) -> None:
    fp = hashlib.sha256(st.SECRET_KEY.encode() + code.encode() + email.encode()).hexdigest()
    with db() as conn:
        conn.execute("UPDATE email_codes SET code_hash=? WHERE email=? AND scene=? AND used=0",
                     (fp, email, scene))


ACCOUNT_KEYS = {
    "id", "mid", "uname", "face", "fans", "videos", "coins", "level", "status",
    "coin_ok", "cred_source", "last_check_at", "proxy", "alias_name", "alias_mid", "alias_face",
}


def main() -> int:
    with TestClient(app) as c:
        print("[1] 注册 + 登录")
        a, tk = cap(c)
        r = c.post("/api/auth/send_code", json={"email": "x@example.com", "scene": "register",
                                                "captcha": a, "captcha_token": tk})
        ck("send_code", r.status_code == 200, r.text)
        forge("x@example.com", "register", "111111")
        r = c.post("/api/auth/register", json={"username": "ctuser", "password": "passw0rdX",
                                               "email": "x@example.com", "code": "111111"})
        ck("register", r.status_code == 200, r.text)
        a, tk = cap(c)
        r = c.post("/api/auth/login", json={"username": "ctuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": tk})
        if r.json().get("need_verify"):
            ticket = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": ticket, "captcha": a2, "captcha_token": t2})
            forge("x@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": ticket, "code": "222222"})
        ck("login", r.status_code == 200, r.text)

        print("[9] 页面路由")
        for p in ("/accounts", "/accounts/add", "/dashboard", "/vip"):
            ck("GET " + p, c.get(p).status_code == 200, c.get(p).status_code)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("契约测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
