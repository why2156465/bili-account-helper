"""互赞任务模块测试：配置形状、目标解析、执行、进度、模板。

运行：python tests/interact.py
不访问 B 站（目标解析用假数据），但会走真实的线程与状态机。
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

TMP = tempfile.mkdtemp(prefix="bp-interact-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "interact-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import app.routers.interact as interact  # noqa: E402
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


def login(c):
    a, t = cap(c)
    c.post("/api/auth/send_code", json={"email": "i@example.com", "scene": "register",
                                        "captcha": a, "captcha_token": t})
    forge("i@example.com", "register", "111111")
    c.post("/api/auth/register", json={"username": "ituser", "password": "passw0rdX",
                                       "email": "i@example.com", "code": "111111"})
    a, t = cap(c)
    r = c.post("/api/auth/login", json={"username": "ituser", "password": "passw0rdX",
                                        "captcha": a, "captcha_token": t})
    if r.json().get("need_verify"):
        tk = r.json()["ticket"]
        a2, t2 = cap(c)
        c.post("/api/auth/login_verify/resend",
               json={"ticket": tk, "captcha": a2, "captcha_token": t2})
        forge("i@example.com", "verify", "222222")
        r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
    return r.status_code == 200


FAKE_VIDEOS = [
    {"aid": 1001, "bvid": "BV1aa411c7m1", "title": "第一个视频", "date": "2026-10-17",
     "ts": 1792220000, "author": "UP甲", "mid": 555},
    {"aid": 1002, "bvid": "BV1aa411c7m2", "title": "第二个视频", "date": "2026-10-15",
     "ts": 1792050000, "author": "UP甲", "mid": 555},
    {"aid": 1003, "bvid": "BV1aa411c7m3", "title": "第三个视频", "date": "2026-10-10",
     "ts": 1791620000, "author": "UP甲", "mid": 555},
]


def fake_fetch(cli, uid, limit=50):
    return list(FAKE_VIDEOS)


def main() -> int:
    interact._fetch_up_videos = fake_fetch

    with TestClient(app) as c:
        ck("登录", login(c))

        print("[1] 账号准备")
        from app.security import encrypt_cookie

        enc = encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}')
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy) VALUES(1,'9001','主号','',?,'ok','qr',1,'')",
                (enc,))
        d = c.get("/api/accounts").json()
        ck("账号可读", len(d["accounts"]) == 1, d)

        print("[2] 预览接口 + 6 种规则")
        cases = [
            ({"uid": "555", "rule": "latest", "max": 1}, 1),
            ({"uid": "555", "rule": "all", "max": 10}, 3),
            ({"uid": "555", "rule": "random", "max": 20, "n": 2}, 2),
            ({"uid": "555", "rule": "date", "date": "2026-10-15"}, 1),
            ({"uid": "555", "rule": "range", "from": "2026-10-10", "to": "2026-10-15", "max": 10}, 2),
        ]
        for cfg, expect in cases:
            r = c.post("/api/tasks/preview", json={"targets": [cfg]})
            got = r.json()["results"][0]["count"] if r.status_code == 200 else -1
            ck("规则 %-7s 命中 %d（期望 %d）" % (cfg["rule"], got, expect), got == expect, r.text)
        ck("latest 取到最新一条",
           c.post("/api/tasks/preview",
                  json={"targets": [{"uid": "555", "rule": "latest", "max": 1}]}
                  ).json()["results"][0]["videos"][0]["bvid"] == "BV1aa411c7m1")

        print("[3] 配置校验")
        r = c.post("/api/tasks/run", json={"actions": {}, "targets": [{"uid": "555"}]})
        ck("空动作被拒", r.status_code == 400, r.text)
        r = c.post("/api/tasks/run", json={"actions": {"like": True}, "targets": []})
        ck("空目标被拒", r.status_code == 400, r.text)

        print("[4] 执行 + 进度状态机")
        cfg = {
            "actions": {"like": True,
                        "coin": {"on": True, "multiply": 2, "also_like": False},
                        "comment": {"on": True, "text": "主播我爱你",
                                    "emotes": ["[doge]"], "emote": True}},
            "actors": "all",
            "targets": [{"uid": "555", "rule": "latest", "max": 2}],
            "sleep": [1, 1],
        }
        r = c.post("/api/tasks/run", json=cfg)
        ck("提交执行", r.status_code == 200 and r.json().get("ok"), r.text)
        ck("返回 jid 与 total", "jid" in r.json() and r.json().get("total") == 2, r.json())

        time.sleep(0.4)
        p = c.get("/api/tasks/progress").json()
        ck("进度快照字段齐全",
           all(k in p for k in ("running", "label", "total", "done", "percent", "logs",
                                "ok", "fail", "eta", "paused", "canceled")), list(p.keys()))
        ck("进度记录到执行中或已结束", p["total"] == 2, p.get("total"))

        print("[5] 暂停 / 停止 / 收起")
        ck("pause", c.post("/api/tasks/pause").status_code == 200)
        ck("resume", c.post("/api/tasks/resume").status_code == 200)
        ck("stop", c.post("/api/tasks/stop").status_code == 200)
        ck("dismiss", c.post("/api/tasks/progress/dismiss").status_code == 200)

        print("[6] 模板")
        r = c.post("/api/templates", json={"name": "夜间互赞", "kind": "interact", "config": cfg})
        ck("存模板", r.status_code == 200, r.text)
        tpl_id = r.json()["id"]
        d = c.get("/api/templates?kind=interact").json()
        ck("模板列表", len(d["templates"]) == 1 and d["templates"][0]["name"] == "夜间互赞", d)
        d = c.get("/api/templates").json()
        ck("模板 config 可回读", d["templates"][0]["config"]["actions"].get("like") is True, d)

        print("[7] 定时任务 CRUD")
        r = c.post("/api/tasks", json={"name": "每日互赞", "cron": "0 9 * * *",
                                       "kind": "interact", "config": cfg})
        ck("建定时任务", r.status_code == 200 and r.json().get("next_run_ts", 0) > 0, r.text)
        tid = r.json()["id"]
        d = c.get("/api/tasks").json()
        ck("任务列表", len(d["tasks"]) == 1, d)
        ck("切换启用", c.patch("/api/tasks/%d" % tid, json={"enabled": 0}).json()["enabled"] is False)
        ck("删除任务", c.delete("/api/tasks/%d" % tid).status_code == 200)

        print("[8] 日志分页")
        d = c.get("/api/logs?page=1&limit=10").json()
        ck("日志返回 items/total", "items" in d and "total" in d, d)
        if d["items"]:
            ck("日志含 account/target/action/result",
               all(k in d["items"][0] for k in ("created_at", "account", "target",
                                                "action", "result")), d["items"][0])

        print("[9] 页面")
        for p in ("/tasks", "/logs"):
            ck("GET " + p, c.get(p).status_code == 200, c.get(p).status_code)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("互赞模块测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())


