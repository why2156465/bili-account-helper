"""进度状态机健壮性测试：任务异常时 STATE 不能卡在 running=True。

原缺陷：_run_worker 的收尾代码写在 for 循环之后，循环体一抛未捕获异常，
`running` 就永远是 True —— 界面上「正在执行」再也不消失，
之后所有任务都被 409「已有任务在运行中」挡住，只能重启进程。

运行：python tests/state_machine.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-state-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "state-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.interact import STATE, _run_worker  # noqa: E402

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


def reset():
    with STATE.lock:
        STATE.running = False
        STATE.paused = False
        STATE.canceled = False
        STATE.done = 0
        STATE.total = 0
        STATE.ok = STATE.fail = 0
        STATE.error = ""
        STATE.finished_at = 0
        STATE.current = ""
        STATE.user_id = None


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "s@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("s@example.com", "register", "111111")
        c.post("/api/auth/register", json={"username": "stuser", "password": "passw0rdX",
                                           "email": "s@example.com", "code": "111111"})
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "stuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("s@example.com", "verify", "222222")
            c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)
        reset()

        print("[1] 坏 job 数据不会卡住 running")
        _run_worker(1, {"sleep": [1, 1]}, [1], [(1, None)])
        ck("running 已复位", STATE.running is False, STATE.running)
        ck("finished_at 已写入", STATE.finished_at > 0, STATE.finished_at)
        ck("记了失败", STATE.fail >= 1, STATE.fail)
        ck("有说明", any("异常" in str(x) for x in (STATE.logs or [])), (STATE.logs or [])[-3:])

        print("[2] 正常结束也会复位")
        reset()
        _run_worker(1, {"sleep": [1, 1]}, [1], [])
        ck("空 jobs 立即结束", STATE.running is False, STATE.running)
        ck("finished_at 有值", STATE.finished_at > 0, STATE.finished_at)

        print("[3] 卡住时 /api/tasks/run 被 409 挡住；复位后放行")
        reset()
        with STATE.lock:
            STATE.running = True
            STATE.user_id = 1
        r = c.post("/api/tasks/run", json={"actions": {"like": True},
                                           "targets": [{"uid": "1", "rule": "latest"}]})
        ck("卡住时 409", r.status_code == 409, r.text)
        reset()
        r = c.post("/api/tasks/run", json={"actions": {}, "targets": []})
        ck("复位后不再是 409（变成配置 400）", r.status_code == 400, r.text)

        print("[4] progress 接口反映真实状态")
        reset()
        d = c.get("/api/tasks/progress").json()
        ck("running=false", d["running"] is False, d["running"])
        STATE.running = True
        STATE.total = 10
        STATE.done = 3
        d = c.get("/api/tasks/progress").json()
        ck("running=true", d["running"] is True, d["running"])
        ck("percent 30", d["percent"] == 30, d["percent"])
        reset()

        print("[5] 线程里抛异常也不会污染全局 STATE")
        reset()
        th = threading.Thread(target=_run_worker,
                              args=(1, {"sleep": [1, 1]}, [1], [(1, "不是dict")]))
        th.start()
        th.join(timeout=5)
        ck("线程已结束", not th.is_alive(), th.is_alive())
        ck("running 已复位", STATE.running is False, STATE.running)

        print("[6] 停止指令能被响应")
        reset()
        # 起一个空 job 列表的线程，确认 cancel 流程不卡
        with STATE.lock:
            STATE.canceled = True
        _run_worker(1, {"sleep": [1, 1]}, [1], [])
        ck("canceled 后仍正常结束", STATE.running is False, STATE.running)
        reset()

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("进度状态机测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
