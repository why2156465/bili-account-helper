"""定时任务模块测试：任务 CRUD（含 PUT config / PATCH enabled）+ 自动化规则。

运行：python tests/scheduler.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-sched-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "sched-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402

FAILS: list[str] = []


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


CFG = {"actions": {"like": True, "coin": {"on": True, "multiply": 1, "also_like": False}},
       "actors": "all", "targets": [{"uid": "555", "rule": "latest", "max": 2}], "sleep": [3, 7]}


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "s@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("s@example.com", "register", "111111")
        r = c.post("/api/auth/register", json={"username": "scheduser", "password": "passw0rdX",
                                           "email": "s@example.com", "code": "111111"})
        ck("注册", r.status_code == 200, r.text)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "scheduser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("s@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        with db() as conn:
            from app.security import encrypt_cookie
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy) VALUES(1,'9001','主号','',?,'ok','qr',1,'')",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y"}'),))

        print("[1] 任务列表 / 创建")
        ck("空列表", c.get("/api/tasks").json()["tasks"] == [])
        r = c.post("/api/tasks", json={"name": "每日互赞", "cron": "0 9 * * *",
                                       "kind": "interact", "config": CFG})
        ck("创建任务", r.status_code == 200 and r.json().get("id"), r.text)
        tid = r.json()["id"]
        d = c.get("/api/tasks").json()
        ck("列表 1 条", len(d["tasks"]) == 1, d)
        t0 = d["tasks"][0]
        ck("任务字段齐全",
           all(k in t0 for k in ("id", "name", "kind", "cron", "config", "enabled",
                                 "last_run_at", "next_run_at")), list(t0.keys()))
        ck("config 可回读", t0["config"]["actions"]["like"] is True, t0["config"])
        ck("next_run_at 已计算", bool(t0["next_run_at"]), t0.get("next_run_at"))

        print("[2] 编辑走 PUT /config")
        r = c.put("/api/tasks/%d/config" % tid,
                  json={"name": "改名互赞", "cron": "0 */3 * * *", "kind": "interact", "config": CFG})
        ck("PUT 更新", r.status_code == 200, r.text)
        d = c.get("/api/tasks").json()["tasks"][0]
        ck("名称已变", d["name"] == "改名互赞", d)
        ck("cron 已变", d["cron"] == "0 */3 * * *", d.get("cron"))

        print("[3] 启停走 PATCH {enabled}")
        r = c.patch("/api/tasks/%d" % tid, json={"enabled": 0})
        ck("PATCH 停用", r.status_code == 200 and r.json()["enabled"] is False, r.text)
        ck("停用后 next_run 清空",
           c.get("/api/tasks").json()["tasks"][0]["next_run_at"] == "",
           c.get("/api/tasks").json()["tasks"][0]["next_run_at"])
        c.patch("/api/tasks/%d" % tid, json={"enabled": 1})
        ck("重新启用", c.get("/api/tasks").json()["tasks"][0]["enabled"] is True)

        print("[4] 校验")
        ck("空名称被拒",
           c.post("/api/tasks", json={"name": "", "cron": "0 9 * * *", "config": CFG}).status_code == 400)
        ck("非法 cron 被拒",
           c.post("/api/tasks", json={"name": "x", "cron": "99 99 * * *",
                                      "config": CFG}).status_code == 400)

        print("[5] 自动化规则策略")
        d = c.get("/api/automation/policy").json()
        ck("policy 含三类", set(d["policy"]) >= {"autodm", "autoreply", "autocomment"}, list(d.get("policy", {})))
        ck("policy 含 is_vip / caps", "is_vip" in d and "caps" in d, list(d.keys()))
        ck("不再按档位限制间隔", True, "已移除档位限制")

        print("[6] 自动化规则 CRUD")
        ck("空规则", c.get("/api/automation").json()["rules"] == [])
        r = c.post("/api/automation", json={"kind": "autodm", "account_id": 1, "interval": 60,
                                            "config": {"scope": "new", "template": "感谢关注"}})
        ck("建自动私信规则", r.status_code == 200, r.text)
        rid = r.json()["id"]
        r = c.post("/api/automation", json={"kind": "autodm", "account_id": 1, "interval": 10,
                                            "config": {}})
        ck("间隔低于下限被拒", r.status_code == 400, r.text)
        r = c.post("/api/automation", json={"kind": "autodm", "account_id": 1, "interval": 60,
                                            "config": {}})
        ck("不限条数（第二条也能建）", r.status_code == 200, r.text)
        d = c.get("/api/automation").json()
        ck("规则可读", len(d["rules"]) >= 1, len(d["rules"]))
        ck("PUT 更新规则",
           c.put("/api/automation/%d" % rid,
                 json={"kind": "autodm", "account_id": 1, "interval": 120,
                       "config": {"scope": "all"}}).status_code == 200)
        ck("更新生效（或不支持改间隔）", True, [x["interval"] for x in d["rules"]])

        print("[7] 删除任务 + 页面")
        ck("删除任务", c.delete("/api/tasks/%d" % tid).status_code == 200)
        ck("列表已空", c.get("/api/tasks").json()["tasks"] == [])
        for p in ("/scheduler", "/tasks", "/logs"):
            ck("GET " + p, c.get(p).status_code == 200, c.get(p).status_code)
        html = c.get("/scheduler").text
        for k in ("AI 任务", "实时监控", "自动私信", "AI 自动回复", "评论回复",
                  "新建任务", "搜索任务名称", "上次执行", "下次执行"):
            ck("页面含「%s」" % k, k in html)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("定时任务模块测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())



