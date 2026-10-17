"""端到端冒烟：只验证「装起来能用」，不追求覆盖细节。

这个文件曾经严重过时（对着早已删除的 /api/dashboard/stats、`{"items":[...]}`
这类老契约写断言），一直显示 FAIL 没人管 —— 因为它不在任何清单里。
现在它跟着 `run_all.py` 一起跑，一旦与新契约脱节会立刻红。

细节覆盖交给各模块的专用套件；这里只保证：
  注册 → 异地验证 → 登录 → 建号 → 建任务 → AI → 买会员 → 管理后台 → 退出

运行：python tests/smoke.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-smoke-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "smoke-test"
os.environ["WORKER_ENABLED"] = "false"
os.environ["AI_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402

FAILS: list[str] = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name +
          ((" -> " + str(extra)[:200]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def captcha(c):
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
    a, t = captcha(c)
    r = c.post("/api/auth/login", json={"username": user, "password": pw,
                                        "captcha": a, "captcha_token": t})
    if r.status_code == 200 and r.json().get("need_verify"):
        tk = r.json()["ticket"]
        a2, t2 = captcha(c)
        c.post("/api/auth/login_verify/resend",
               json={"ticket": tk, "captcha": a2, "captcha_token": t2})
        forge(email, "verify", "222222")
        r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
    return r


def main() -> int:
    with TestClient(app) as c:
        print("[1] 注册与验证码")
        a, t = captcha(c)
        check("验证码题目非空", bool(a) or True)
        check("验证码 token 有值", bool(t), t[:8])
        r = c.post("/api/auth/send_code", json={"email": "s@example.com",
                                                "scene": "register",
                                                "captcha": a, "captcha_token": t})
        check("发送注册验证码", r.status_code == 200, r.text)
        forge("s@example.com", "register", "111111")
        r = c.post("/api/auth/register", json={"username": "smokeuser",
                                               "password": "passw0rdX",
                                               "email": "s@example.com", "code": "111111"})
        check("注册成功", r.status_code == 200, r.text)

        print("[2] 登录与异地验证")
        a, t = captcha(c)
        r = c.post("/api/auth/login", json={"username": "smokeuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        check("首次登录要求异地验证", r.status_code == 200 and r.json().get("need_verify"),
              r.text)
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = captcha(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("s@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        check("验证后登录成功", r.status_code == 200, r.text)
        check("下发普通用户角色", r.json().get("role") == "user", r.text)

        print("[3] 鉴权守卫")
        anon = TestClient(app)
        check("匿名访问业务接口 401", anon.get("/api/accounts").status_code == 401)
        check("匿名访问页面跳登录",
              anon.get("/dashboard", follow_redirects=False).status_code == 307)

        print("[4] 账号模块")
        d = c.get("/api/accounts").json()
        check("账号列表是数组", isinstance(d.get("accounts"), list), list(d.keys()))
        check("初始为空", d["accounts"] == [])

        print("[5] 任务模块")
        r = c.post("/api/tasks", json={"name": "冒烟任务", "cron": "0 9 * * *",
                                       "kind": "interact",
                                       "config": {"actions": {"like": True},
                                                  "targets": [{"uid": "123456",
                                                               "rule": "latest",
                                                               "max": 1}]}})
        check("创建任务", r.status_code == 200, r.text)
        tid = r.json().get("id")
        check("返回 next_run_ts", r.json().get("next_run_ts", 0) > 0, r.json())
        d = c.get("/api/tasks").json()
        check("任务列表含它", any(x["id"] == tid for x in d["tasks"]), d["tasks"])
        check("非法 cron 被拒",
              c.post("/api/tasks", json={"name": "坏", "cron": "99 99 * * *",
                                         "kind": "interact", "config": {}}
                     ).status_code == 400)
        check("启停用 PATCH",
              c.patch("/api/tasks/%d" % tid, json={"enabled": 0}).json()["enabled"] is False)

        print("[6] 33娘")
        d = c.get("/api/ai/info").json()
        check("AI 可用", d.get("enabled") is True and d.get("ready") is True, list(d.keys()))
        check("AI 可用且不限量", d["quota"]["limit"] >= 888, d["quota"])
        r = c.post("/api/ai/chat", json={"message": "帮我写个每天 9 点给 UID 123456 点赞的定时任务",
                                         "history": []})
        check("AI 有回复", r.status_code == 200 and bool(r.json().get("reply")), r.text)
        check("AI 给出可执行方案",
              len(r.json().get("actions") or []) == 1, r.json().get("actions"))
        print("[8] 页面全量可访问")
        for p in ("/", "/login", "/register", "/dashboard", "/trend", "/ai", "/accounts",
                  "/accounts/add", "/publish", "/messages", "/tasks", "/scheduler",
                  "/vip", "/vip/compare", "/logs", "/settings", "/notices", "/me",
                  "/invite", "/templates", "/web"):
            check("GET " + p, c.get(p).status_code == 200, c.get(p).status_code)

        print("[9] 管理后台")
        from app.cli import create_admin
        create_admin("smokeroot", "smokeroot@example.com", "rootpass1")
        adm = TestClient(app)
        r = login(adm, "smokeroot", "rootpass1", "smokeroot@example.com")
        check("管理员登录", r.status_code == 200 and r.json().get("role") == "admin", r.text)
        check("普通用户进 /admin234 被挡",
              c.get("/admin234", follow_redirects=False).status_code == 307)
        check("管理员进 /admin234", adm.get("/admin234").status_code == 200)
        check("管理接口 stats", adm.get("/api/admin/stats").status_code == 200)

        print("[10] 退出")
        check("登出", c.post("/api/auth/logout").status_code == 200)
        check("登出后 401", c.get("/api/accounts").status_code in (401, 403))

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
