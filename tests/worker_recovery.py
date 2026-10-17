"""worker 崩溃/中断恢复测试：进程被杀后任务不能永久卡死。

风险点：_run_one() 是「先执行、执行完才推进 next_run」。
如果任务执行中进程被 kill（或被 OOM 杀掉、或 run_upload 卡在网络超时），
next_run 会一直停在过去 —— 下次启动时 due_tasks 又能捞到它，看似没事；
但如果 cron 是每分钟，就会形成「反复重试同一个坏任务」的死循环。

更要命的场景：next_run 停在过去 + 任务被禁用再启用，
或者 cron 表达式被改成空字符串，此时 next_run 永远为 0 → 再也不执行。

运行：python tests/worker_recovery.py
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

TMP = tempfile.mkdtemp(prefix="bp-recover-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "recover-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import app.worker as wk  # noqa: E402
from app.config import settings as st  # noqa: E402
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


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "r@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("r@example.com", "register", "111111")
        c.post("/api/auth/register", json={"username": "recuser", "password": "passw0rdX",
                                           "email": "r@example.com", "code": "111111"})
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "recuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("r@example.com", "verify", "222222")
            c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))

        def mk_task(cron, cfg=None, next_run=None):
            with db() as conn:
                cur = conn.execute(
                    "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,"
                    "created_at,next_run) VALUES(1,'t','interact',?,?,1,?,?)",
                    (cron, json.dumps(cfg or {"actions": {"like": True}, "targets": []}),
                     int(time.time()), next_run if next_run is not None else 0))
            return cur.lastrowid

        print("[1] 模拟「执行到一半进程被杀」：next_run 停在过去")
        tid = mk_task("* * * * *", next_run=int(time.time()) - 120)
        with db() as conn:
            row = conn.execute("SELECT next_run FROM interact_tasks WHERE id=?",
                               (tid,)).fetchone()
        ck("起始 next_run 在过去", row["next_run"] < time.time(), row["next_run"])
        # 这一轮 tick 应该能捞到并执行
        due = wk.due_tasks()
        ck("due 捞出该任务", any(d["id"] == tid for d in due), [d["id"] for d in due])
        wk.tick()
        with db() as conn:
            row = conn.execute("SELECT next_run, last_run FROM interact_tasks WHERE id=?",
                               (tid,)).fetchone()
        ck("执行后 next_run 推进到未来", row["next_run"] > time.time(), row["next_run"])
        ck("last_run 已记录", row["last_run"] > 0, row["last_run"])

        print("[2] 反复失败的任务不会卡死：每轮都重试、每轮都推进")
        bad_tid = mk_task("* * * * *", cfg={"actions": {}, "targets": []},
                          next_run=int(time.time()) - 30)
        for i in range(3):
            # 每轮先把它拨回过去，模拟"到点了该执行"
            with db() as conn:
                conn.execute("UPDATE interact_tasks SET next_run=? WHERE id=?",
                             (int(time.time()) - 5, bad_tid))
            wk.tick()
            with db() as conn:
                row = conn.execute("SELECT next_run FROM interact_tasks WHERE id=?",
                                   (bad_tid,)).fetchone()
            ck("第 %d 轮后 next_run 推进到未来" % (i + 1),
               row["next_run"] > time.time(), row["next_run"])
            with db() as conn:
                n = conn.execute("SELECT COUNT(*) c FROM task_runs WHERE task_id=?",
                                 (bad_tid,)).fetchone()["c"]
            ck("第 %d 轮写了执行记录" % (i + 1), n == i + 1, n)

        print("[3] next_run=0 且从未执行过的任务：启动时补算")
        zero_tid = mk_task("0 9 * * *", next_run=0)
        n = wk.bootstrap_next_runs()
        ck("补算了至少 1 条", n >= 1, n)
        with db() as conn:
            row = conn.execute("SELECT next_run FROM interact_tasks WHERE id=?",
                               (zero_tid,)).fetchone()
        ck("next_run 已填", row["next_run"] > 0, row["next_run"])

        print("[4] cron 为空的启用任务：不会永远卡在 0")
        empty_tid = mk_task("", next_run=0)
        wk.bootstrap_next_runs()
        with db() as conn:
            row = conn.execute("SELECT next_run, enabled FROM interact_tasks WHERE id=?",
                               (empty_tid,)).fetchone()
        ck("cron 空时 next_run 保持 0（不执行）", row["next_run"] == 0, row["next_run"])
        ck("但任务仍启用", row["enabled"] == 1, row["enabled"])
        # 补上 cron 后应能被 bootstrap 捞到
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET cron='0 8 * * *' WHERE id=?", (empty_tid,))
        wk.bootstrap_next_runs()
        with db() as conn:
            row = conn.execute("SELECT next_run FROM interact_tasks WHERE id=?",
                               (empty_tid,)).fetchone()
        ck("补 cron 后 next_run 被填上", row["next_run"] > 0, row["next_run"])

        print("[5] 禁用再启用：next_run 已过期时能被重新捞到")
        dis_tid = mk_task("0 9 * * *", next_run=int(time.time()) - 3600)
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET enabled=0 WHERE id=?", (dis_tid,))
        ck("禁用时 due 里没有它",
           all(d["id"] != dis_tid for d in wk.due_tasks()))
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET enabled=1 WHERE id=?", (dis_tid,))
        ck("启用后 due 里又出现",
           any(d["id"] == dis_tid for d in wk.due_tasks()))
        wk.tick()
        with db() as conn:
            row = conn.execute("SELECT next_run FROM interact_tasks WHERE id=?",
                               (dis_tid,)).fetchone()
        ck("执行后推进", row["next_run"] > time.time(), row["next_run"])

        print("[6] 单条任务崩溃不影响同轮其他任务")
        with db() as conn:
            conn.execute("DELETE FROM interact_tasks")
            conn.execute("DELETE FROM task_runs")
        # 第一条：配置是坏 JSON，执行时必然失败
        with db() as conn:
            conn.execute(
                "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,"
                "created_at,next_run) VALUES(1,'坏配置','interact','* * * * *','{bad json',1,?,?)",
                (int(time.time()), int(time.time()) - 60))
            conn.execute(
                "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,"
                "created_at,next_run) VALUES(1,'好配置','interact','* * * * *',?,1,?,?)",
                (json.dumps({"actions": {"like": True}, "targets": []}),
                 int(time.time()), int(time.time()) - 60))
        due = wk.due_tasks()
        ck("两条都到点", len(due) == 2, len(due))
        wk.tick()
        with db() as conn:
            runs = conn.execute("SELECT task_id,status,message FROM task_runs "
                                "ORDER BY id").fetchall()
            tasks = conn.execute("SELECT id,next_run FROM interact_tasks").fetchall()
        ck("两条都写了记录", len(runs) == 2, [dict(r) for r in runs])
        ck("两条 next_run 都不在过去（不会无限重试）",
           all(t["next_run"] > time.time() for t in tasks),
           [t["next_run"] for t in tasks])
        ck("坏配置那条说明是 JSON 问题",
           any("JSON" in (r["message"] or "") for r in runs),
           [(r["message"] or "")[:40] for r in runs])

        print("[7] 任务归属用户被删：跳过而不是崩")
        with db() as conn:
            conn.execute("DELETE FROM interact_tasks")
            conn.execute("DELETE FROM task_runs")
            conn.execute(
                "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,"
                "created_at,next_run) VALUES(999,'孤儿任务','interact','* * * * *',?,1,?,?)",
                (json.dumps({"actions": {"like": True}, "targets": []}),
                 int(time.time()), int(time.time()) - 60))
        wk.tick()
        with db() as conn:
            runs = conn.execute("SELECT status,message FROM task_runs").fetchall()
        ck("写了 fail 记录", len(runs) == 1 and runs[0]["status"] == "fail",
           [dict(r) for r in runs])
        ck("说明用户不存在", "用户" in (runs[0]["message"] or ""), runs[0]["message"])

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("worker 崩溃恢复测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
