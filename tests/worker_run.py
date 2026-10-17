"""定时任务自动执行测试：worker 真的会把到点任务跑起来。

这一条是回归防线：worker 原本查的是不存在的 `tasks` 表，
导致「定时任务从来没自动执行过」，而当时 600+ 项测试全绿。

运行：python tests/worker_run.py
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

TMP = tempfile.mkdtemp(prefix="bp-worker-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "worker-test"
os.environ["WORKER_ENABLED"] = "false"     # 手动调 tick()，不启后台线程

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
        c.post("/api/auth/send_code", json={"email": "w@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("w@example.com", "register", "111111")
        ck("注册", c.post("/api/auth/register", json={
            "username": "wrkuser", "password": "passw0rdX",
            "email": "w@example.com", "code": "111111"}).status_code == 200)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "wrkuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("w@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] worker 读的是 interact_tasks（不是不存在的 tasks）")
        r = c.post("/api/tasks", json={"name": "每晚互赞", "cron": "0 20 * * *",
                                       "kind": "interact",
                                       "config": {"actions": {"like": True},
                                                  "actors": "all",
                                                  "targets": [{"uid": "123456",
                                                               "rule": "latest",
                                                               "max": 1}]}})
        ck("建任务", r.status_code == 200, r.text)
        tid = r.json()["id"]
        with db() as conn:
            row = conn.execute("SELECT * FROM interact_tasks WHERE id=?", (tid,)).fetchone()
        ck("任务在 interact_tasks 里", row is not None, row)

        print("[2] next_run 未到点时不执行")
        ck("due_tasks 空", wk.due_tasks(int(time.time())) == [])
        ck("tick 空跑", wk.tick() == [])

        print("[3] 到点后 due_tasks 能捞出来")
        past = int(time.time()) - 60
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET next_run=? WHERE id=?", (past, tid))
        due = wk.due_tasks()
        ck("捞到 1 条", len(due) == 1, due)
        ck("带 user_id/kind/config",
           all(k in due[0] for k in ("user_id", "kind", "config")), due[0])

        print("[4] 缺配置的任务被跳过并留痕")
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET config=? WHERE id=?",
                         (json.dumps({"cron": "0 20 * * *"}), tid))
        wk.tick()
        with db() as conn:
            runs = conn.execute("SELECT * FROM task_runs WHERE task_id=?", (tid,)).fetchall()
        ck("写了 task_runs", len(runs) >= 1, len(runs))
        ck("状态为 skip", runs[-1]["status"] == "skip", dict(runs[-1]))
        ck("说明缺动作", "动作" in (runs[-1]["message"] or ""), runs[-1]["message"])

        print("[5] 执行后 next_run 推进到未来、last_run 更新")
        with db() as conn:
            row = conn.execute("SELECT * FROM interact_tasks WHERE id=?", (tid,)).fetchone()
        ck("next_run 在未来", row["next_run"] > time.time(), row["next_run"])
        ck("last_run 已更新", row["last_run"] > 0, row["last_run"])

        print("[6] 定时投稿任务走投稿执行器")
        from app.security import encrypt_cookie
        from pathlib import Path
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
        vdir = Path(ROOT) / "data" / "vfiles"
        vdir.mkdir(parents=True, exist_ok=True)
        vp = vdir / "wk_video.mp4"
        vp.write_bytes(b"\x00" * (1024 * 1024))
        with db() as conn:
            conn.execute("INSERT INTO publish_files(user_id,code,name,path,size,created_at) "
                         "VALUES(1,1,'wk_video.mp4',?,?,1)", (str(vp), vp.stat().st_size))
            cur = conn.execute(
                "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,created_at,"
                "next_run) VALUES(1,'定时投稿','upload','0 10 * * *',?,1,?,?)",
                (json.dumps({"account_id": 1, "file_id": 1, "title": "自动投稿",
                             "tid": "171"}), int(time.time()), int(time.time()) - 30))
        up_tid = cur.lastrowid

        # 上游不可达 → 失败但要有明确原因，不能静默
        wk.tick()
        with db() as conn:
            run = conn.execute("SELECT * FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                               (up_tid,)).fetchone()
        ck("投稿任务被执行", run is not None, run)
        ck("失败有原因", run["status"] in ("fail", "ok"),
           dict(run) if run else None)
        ck("记录了 message", bool(run["message"]) if run else False,
           run["message"] if run else None)

        print("[7] 停用的任务不会被捞")
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET enabled=0, next_run=? WHERE id=?",
                         (int(time.time()) - 10, tid))
        ck("停用后 due 里没有它",
           all(d["id"] != tid for d in wk.due_tasks()), wk.due_tasks())

        print("[8] bootstrap 能补算缺失的 next_run")
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET enabled=1, next_run=0 WHERE id=?", (tid,))
        n = wk.bootstrap_next_runs()
        ck("补算了至少 1 条", n >= 1, n)
        with db() as conn:
            row = conn.execute("SELECT next_run FROM interact_tasks WHERE id=?", (tid,)).fetchone()
        ck("next_run 已填", row["next_run"] > 0, row["next_run"])

        print("[9] bootstrap 不能吃掉「已到点待执行」的任务")
        # 曾经的 bug：bootstrap 每轮先把 next_run<now 的任务推到下一分钟，
        # 导致 tick() 永远捞不到它们，定时任务形同虚设。
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET next_run=? WHERE id=?",
                         (int(time.time()) - 60, tid))
        before = len(wk.due_tasks())
        wk.bootstrap_next_runs()
        after = len(wk.due_tasks())
        ck("bootstrap 之后仍在 due 里", after == before and after == 1, (before, after))
        wk.tick()
        with db() as conn:
            row = conn.execute("SELECT next_run FROM interact_tasks WHERE id=?", (tid,)).fetchone()
        ck("执行后才推进到未来", row["next_run"] > time.time(), row["next_run"])

        vp.unlink(missing_ok=True)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("定时任务自动执行测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
