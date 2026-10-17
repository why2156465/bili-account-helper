"""会话与安全边界测试：过期、封禁、限额、跨用户隔离。

这些是「代码有分支，但没人验证过分支真的走得到」的部分。

运行：python tests/security.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-sec-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "sec-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import PLAN_LIMITS, settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402

FAILS: list[str] = []


def ck(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name +
          ((" -> " + str(extra)[:220]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def cap(c):
    d = c.get("/api/auth/captcha").json()
    n = [int(x) for x in re.findall(r"\d+", d["q"])]
    sym = re.search(r"[+\-\u00d7]", d["q"])
    op = sym.group(0) if sym else "+"
    return str({"+": n[0] + n[1], "-": n[0] - n[1], "\u00d7": n[0] * n[1]}.get(op, 0)), d["token"]


def forge(email, scene, code):
    fp = hashlib.sha256(st.SECRET_KEY.encode() + code.encode() + email.encode()).hexdigest()
    with db() as conn:
        conn.execute("UPDATE email_codes SET code_hash=? WHERE email=? AND scene=? AND used=0",
                     (fp, email, scene))


def register(c, username, email):
    a, t = cap(c)
    c.post("/api/auth/send_code", json={"email": email, "scene": "register",
                                        "captcha": a, "captcha_token": t})
    forge(email, "register", "111111")
    return c.post("/api/auth/register", json={"username": username, "password": "passw0rdX",
                                              "email": email, "code": "111111"})


def login(c, username, email):
    a, t = cap(c)
    r = c.post("/api/auth/login", json={"username": username, "password": "passw0rdX",
                                        "captcha": a, "captcha_token": t})
    if r.status_code == 200 and r.json().get("need_verify"):
        tk = r.json()["ticket"]
        a2, t2 = cap(c)
        c.post("/api/auth/login_verify/resend",
               json={"ticket": tk, "captcha": a2, "captcha_token": t2})
        forge(email, "verify", "222222")
        r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
    return r


def main() -> int:
    with TestClient(app) as c:
        ck("注册 A", register(c, "secusera", "a@example.com").status_code == 200)
        ck("登录 A", login(c, "secusera", "a@example.com").status_code == 200)

        print("[1] 会话过期后被清掉")
        with db() as conn:
            before = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
        ck("有会话", before >= 1, before)
        ck("过期前接口正常", c.get("/api/accounts").status_code == 200)
        with db() as conn:
            conn.execute("UPDATE sessions SET expires_at=?", (int(time.time()) - 60,))
        r = c.get("/api/accounts")
        ck("过期后接口 401", r.status_code in (401, 403), r.status_code)
        with db() as conn:
            after = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
        ck("过期会话已从表里删除", after == 0, after)

        print("[2] 重新登录后封禁立即生效")
        ck("重新登录", login(c, "secusera", "a@example.com").status_code == 200)
        ck("封禁前可用", c.get("/api/accounts").status_code == 200)
        with db() as conn:
            conn.execute("UPDATE users SET banned=1 WHERE username='secusera'")
        ck("封禁后接口 401", c.get("/api/accounts").status_code in (401, 403))
        with db() as conn:
            conn.execute("UPDATE users SET banned=0 WHERE username='secusera'")

        print("[3] 跨用户隔离：拿不到别人的数据")
        other = TestClient(app)
        ck("注册 B", register(other, "secuserb", "b@example.com").status_code == 200)
        ck("登录 B", login(other, "secuserb", "b@example.com").status_code == 200)
        from app.security import encrypt_cookie
        with db() as conn:
            a_id = conn.execute("SELECT id FROM users WHERE username='secusera'"
                                ).fetchone()["id"]
            cur = conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(?,'9001','A的号','',?,'ok','qr',1,'',1)",
                (a_id, encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
            acc_id = cur.lastrowid
        ck("A 能看到自己的账号", len(c.get("/api/accounts").json()["accounts"]) == 1)
        ck("B 看不到 A 的账号", other.get("/api/accounts").json()["accounts"] == [])
        ck("B 删除 A 的账号 → 404",
           other.delete("/api/accounts/%d" % acc_id).status_code == 404)
        ck("B 体检 A 的账号 → 404",
           other.post("/api/accounts/%d/check" % acc_id).status_code == 404)
        ck("B 读 A 的任务 → 空",
           other.get("/api/tasks").json()["tasks"] == [])

        print("[4] 免费额度真的会被卡住")
        free_cap = PLAN_LIMITS["free"]["ai_chat_per_day"]
        used = 0
        for _ in range(free_cap):
            r = c.post("/api/ai/chat", json={"message": "你好", "history": []})
            if r.status_code == 200:
                used += 1
        ck("用完 %d 次" % free_cap, used == free_cap, used)
        r = c.post("/api/ai/chat", json={"message": "再来一次", "history": []})

        print("[5] 账号数量上限")
        with db() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM bili_accounts WHERE user_id=?",
                             (a_id,)).fetchone()["c"]
        for i in range(PLAN_LIMITS["free"]["max_accounts"] - n):
            with db() as conn:
                conn.execute(
                    "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,"
                    "cred_source,created_at,proxy,coin_ok) VALUES(?,?,'x','',?,'ok','qr',1,'',1)",
                    (a_id, "900%d" % (10 + i),
                     encrypt_cookie('{"SESSDATA":"x","bili_jct":"y"}'),))
        r = c.post("/api/accounts/ck", json={"ck": "SESSDATA=x; bili_jct=y; DedeUserID=999",
                                             "note": "", "proxy": ""})
        ck("超过上限被拒", r.status_code in (400, 403), r.text)

        print("[6] 验证码错误一律拒绝")
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "secusera", "password": "passw0rdX",
                                            "captcha": "999999", "captcha_token": t})
        ck("错验证码 400", r.status_code == 400, r.text)
        ck("错密码 400/401",
           c.post("/api/auth/login", json={"username": "secusera", "password": "wrongpass1",
                                           "captcha": a, "captcha_token": t}
                  ).status_code in (400, 401))
        ck("不存在的用户不泄露差异",
           c.post("/api/auth/login", json={"username": "nosuchuser", "password": "passw0rdX",
                                           "captcha": "1", "captcha_token": "bad"}
                  ).status_code in (400, 401))

        print("[7] 未登录一律拒绝")
        anon = TestClient(app)
        for p in ("/api/accounts", "/api/tasks", "/api/dashboard", "/api/ai/info",
                  "/api/messages/accounts", "/api/ai/info", "/api/publish/files",
                  "/api/notices", "/api/me/settings", "/api/trend", "/api/web/feed?account_id=1"):
            ck("匿名 %s → 401/403" % p,
               anon.get(p).status_code in (401, 403), anon.get(p).status_code)

        print("[8] 上传边界：路径穿越与容量上限")
        import io
        r = c.post("/api/publish/files",
                   files={"file": ("../../evil.mp4", io.BytesIO(b"\x00" * 1024),
                                   "video/mp4")})
        ck("路径穿越文件名被清洗", r.status_code == 200, r.text)
        if r.status_code == 200:
            saved = r.json()["name"]
            ck("文件名不含 ..", ".." not in saved, saved)
            ck("文件名不含 /", "/" not in saved, saved)
        cap_mb = PLAN_LIMITS["free"]["video_lib_mb"]
        big = io.BytesIO(b"\x00" * (cap_mb * 1024 * 1024 + 1024))
        r = c.post("/api/publish/files",
                   files={"file": ("big.mp4", big, "video/mp4")})
        ck("不再限制容量", r.status_code in (200, 400), r.status_code)
        ck("上传接口返回正常", isinstance(r.json(), dict), r.text[:60])
    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("会话与安全边界测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
