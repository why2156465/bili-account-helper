"""管理后台测试：8 个 GET 接口 + 用户/公告/兑换码写操作 + 权限隔离。

运行：python tests/admin.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-admin-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "admin-test"
os.environ["WORKER_ENABLED"] = "false"
os.environ["ADMIN_PATH"] = "admin234"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402

FAILS: list[str] = []

# 线上实测到的 6 个 GET（会员相关的 orders / redeem 已随功能移除）
ADMIN_GETS = ["/api/admin/stats", "/api/admin/users", "/api/admin/logs",
              "/api/admin/accounts", "/api/admin/settings", "/api/admin/notices"]


def ck(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name +
          ((" -> " + str(extra)[:200]) if extra and not cond else ""))
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


def login(c, user, pw, email):
    a, t = cap(c)
    r = c.post("/api/auth/login", json={"username": user, "password": pw,
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
        # 普通用户
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "n@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("n@example.com", "register", "111111")
        c.post("/api/auth/register", json={"username": "normal01", "password": "passw0rdX",
                                           "email": "n@example.com", "code": "111111"})
        r = login(c, "normal01", "passw0rdX", "n@example.com")
        ck("普通用户登录", r.status_code == 200, r.text)

        print("[1] 普通用户访问 admin 接口全部 403（与线上一致）")
        for p in ADMIN_GETS:
            ck("GET " + p + " → 403", c.get(p).status_code == 403, c.get(p).status_code)
        ck("普通用户进 /admin234 → 307",
           c.get("/admin234", follow_redirects=False).status_code == 307)

        # 管理员
        from app.cli import create_admin
        create_admin("rootadmin", "root@example.com", "rootpass1")
        adm = TestClient(app)
        r = login(adm, "rootadmin", "rootpass1", "root@example.com")
        ck("管理员登录", r.status_code == 200 and r.json().get("role") == "admin", r.text)

        print("[2] 8 个 GET 接口都返回 200 且形状正确")
        shapes = {}
        for p in ADMIN_GETS:
            rr = adm.get(p)
            ck("GET " + p, rr.status_code == 200, rr.status_code)
            if rr.status_code == 200:
                shapes[p] = rr.json()
        ck("users 返回 users 数组", isinstance(shapes.get("/api/admin/users", {}).get("users"), list))
        ck("accounts 返回 accounts 数组",
           isinstance(shapes.get("/api/admin/accounts", {}).get("accounts"), list))
        ck("logs 含 logs+audit",
           "logs" in shapes.get("/api/admin/logs", {}) and "audit" in shapes.get("/api/admin/logs", {}),
           list(shapes.get("/api/admin/logs", {}).keys()))
        ck("settings 含运行时配置",
           "app_name" in shapes.get("/api/admin/settings", {}).get("settings", {}),
           shapes.get("/api/admin/settings"))
        ck("notices 返回 notices 数组",
           isinstance(shapes.get("/api/admin/notices", {}).get("notices"), list))

        print("[3] 后台页面可访问")
        rr = adm.get("/admin234")
        ck("GET /admin234", rr.status_code == 200, rr.status_code)
        for k in ("管理后台", "用户总数", "今日新增", "B站账号", "日志与审计", "公告"):
            ck("页面含「%s」" % k, k in rr.text)

        print("[4] 用户管理写操作")
        with db() as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='normal01'").fetchone()["id"]
        ck("封禁用户", adm.post("/api/admin/users/%d" % uid,
                                json={"banned": True}).status_code == 200)
        with db() as conn:
            ck("banned=1", conn.execute("SELECT banned FROM users WHERE id=?",
                                        (uid,)).fetchone()["banned"] == 1)
        ck("被封禁用户会话失效", c.get("/api/accounts").status_code in (401, 403))
        ck("解封", adm.post("/api/admin/users/%d" % uid, json={"banned": False}).status_code == 200)

        print("[5] 公告广播")
        r = adm.post("/api/admin/notices", json={"title": "系统维护", "body": "今晚 2 点维护"})
        ck("发公告", r.status_code == 200 and r.json()["sent"] >= 2, r.text)
        d = adm.get("/api/admin/notices").json()
        ck("公告已入库", len(d["notices"]) >= 2, len(d.get("notices", [])))
        ck("空标题被拒",
           adm.post("/api/admin/notices", json={"title": ""}).status_code == 400)

        print("[7] 审计记录")
        d = adm.get("/api/admin/logs").json()
        ck("审计有记录", len(d["audit"]) >= 3, len(d.get("audit", [])))
        ck("审计含 ban",
           any("ban" in a["action"] for a in d["audit"]),
           [a["action"] for a in d["audit"]][:5])

        print("[8] 设置读写")
        ck("GET settings", adm.get("/api/admin/settings").status_code == 200)
        r = adm.post("/api/admin/settings", json={"app_name": "测试站"})
        ck("POST settings", r.status_code == 200, r.text)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("管理后台测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
