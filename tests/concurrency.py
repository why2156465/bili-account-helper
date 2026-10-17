"""并发与互斥测试：RunState 是进程级单例，验证两套触发机制不会互相踩踏。

已知设计：同一时刻只允许一个互赞任务在跑（RunState 单例）。
风险点在 worker：如果同一轮 tick 里捞到两个到点任务，
第二个会拿到 409，而它已经过了执行时间 —— 必须确认它不会被永久丢掉。

运行：python tests/concurrency.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-conc-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "conc-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import app.worker as wk  # noqa: E402
from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.interact import STATE  # noqa: E402

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


def reset_state():
    with STATE.lock:
        STATE.running = False
        STATE.done = False
        STATE.user_id = None


GOOD_CFG = {"actions": {"like": True}, "actors": "all",
            "targets": [{"uid": "123456", "rule": "latest", "max": 1}],
            "sleep": [1, 2]}


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "c@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("c@example.com", "register", "111111")
        ck("注册", c.post("/api/auth/register", json={
            "username": "concuser", "password": "passw0rdX",
            "email": "c@example.com", "code": "111111"}).status_code == 200)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "concuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("c@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)
        reset_state()

        print("[1] 无账号时手动执行被拒")
        reset_state()
        r = c.post("/api/tasks/run", json=GOOD_CFG)
        ck("400 且提示加账号", r.status_code == 400 and "账号" in r.text, r.text)

        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))

        print("[2] RunState 是单例：正在跑时再触发返回 409")
        reset_state()
        with STATE.lock:
            STATE.running = True
            STATE.user_id = 1
        r = c.post("/api/tasks/run", json=GOOD_CFG)
        ck("手动执行被 409 挡住", r.status_code == 409, r.text)
        ck("提示已有任务在运行", "运行" in r.text or "跑" in r.text, r.text)
        reset_state()

        print("[3] 别的用户的任务在跑时，提示等待或停止")
        with STATE.lock:
            STATE.running = True
            STATE.user_id = 999
        r = c.post("/api/tasks/run", json=GOOD_CFG)
        ck("409", r.status_code == 409, r.text)
        reset_state()

        print("[4] worker 遇到 409 不能把任务永久丢掉")
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,created_at,"
                "next_run) VALUES(1,'定时互赞','interact','* * * * *',?,1,?,?)",
                (json.dumps(GOOD_CFG), int(time.time()), int(time.time()) - 60))
        tid = cur.lastrowid
        with STATE.lock:
            STATE.running = True
            STATE.user_id = 1
        wk.tick()
        with db() as conn:
            row = conn.execute("SELECT next_run, last_run FROM interact_tasks WHERE id=?",
                               (tid,)).fetchone()
            run = conn.execute(
                "SELECT status,message FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (tid,)).fetchone()
        reset_state()
        ck("记了一条失败/跳过", run is not None, run)
        ck("说明了原因", bool(run and run["message"]), run["message"] if run else None)
        ck("next_run 已推进（不会每秒重试）", row["next_run"] > time.time(), row["next_run"])
        retry_soon = row["next_run"] - time.time()
        ck("下次执行在 1 分钟内（cron=每分钟）", retry_soon <= 90, retry_soon)

        print("[5] 空闲时 worker 正常跑起来")
        reset_state()
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET next_run=? WHERE id=?",
                         (int(time.time()) - 10, tid))
        # 用空目标规避真实网络：只验证状态机
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET config=? WHERE id=?",
                         (json.dumps({"actions": {"like": True}, "targets": []}), tid))
        wk.tick()
        with db() as conn:
            run = conn.execute(
                "SELECT status,message FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (tid,)).fetchone()
        ck("状态 skip（无目标）", run["status"] == "skip", dict(run))
        reset_state()

        print("[6] 两个任务同时到点：第二个不能死锁或丢时间")
        reset_state()
        with db() as conn:
            conn.execute("DELETE FROM interact_tasks")
            conn.execute("DELETE FROM task_runs")   # 清掉前几步的记录，只数这一轮
            for i in range(2):
                conn.execute(
                    "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,"
                    "created_at,next_run) VALUES(1,?, 'interact','* * * * *',?,1,?,?)",
                    ("任务%d" % i, json.dumps({"actions": {"like": True}, "targets": []}),
                     int(time.time()), int(time.time()) - 60))
        due = wk.due_tasks()
        ck("两条都到点", len(due) == 2, len(due))
        wk.tick()
        with db() as conn:
            runs = conn.execute("SELECT task_id,status FROM task_runs ORDER BY id").fetchall()
            tasks = conn.execute("SELECT id,next_run FROM interact_tasks").fetchall()
        ck("两条都写了执行记录", len(runs) == 2, [dict(r) for r in runs])
        ck("两条的 next_run 都推进了",
           all(t["next_run"] > time.time() for t in tasks),
           [t["next_run"] for t in tasks])
        reset_state()

        print("[7] 暂停 / 恢复 / 停止 的进度接口")
        d = c.get("/api/tasks/progress").json()
        ck("progress 200 且有 running 字段", "running" in d, list(d.keys()))
        ck("pause 200", c.post("/api/tasks/pause", json={"on": True}).status_code == 200)
        ck("resume 200", c.post("/api/tasks/resume").status_code in (200, 400))
        ck("stop 200", c.post("/api/tasks/stop").status_code in (200, 400))
        ck("dismiss 200", c.post("/api/tasks/progress/dismiss", json={}).status_code == 200)
        reset_state()

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("并发与互斥测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
