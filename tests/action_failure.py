"""动作级失败策略测试：凭据失效时不能把 8 个动作全试一遍。

原缺陷：do_one 对每个动作各自 try/except，账号已失效（-101）时
仍然对同一个视频依次尝试点赞/投币/收藏/评论/分享/关注/播放/图文，
8 次全部失败，还额外触发风控；然后对剩余几十个视频重复同样的浪费。

修好后：命中 FATAL_CODES 立即停止该视频的剩余动作，回报 "FATAL:"；
_run_loop 收到后写回账号状态（expired/captcha）并中止整个任务。

运行：python tests/action_failure.py
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

TMP = tempfile.mkdtemp(prefix="bp-act-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "act-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.bili import BiliError  # noqa: E402
from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.interact import FATAL_CODES, STATE, _run_worker, do_one  # noqa: E402

FAILS: list[str] = []

ALL_ACTIONS = {
    "like": True,
    "coin": {"on": True, "multiply": 1, "also_like": False},
    "fav": {"on": True, "media_id": None},
    "comment": {"on": True, "text": "hi", "emotes": [], "emote": False},
    "share": {"on": True, "mode": "dyn", "text": ""},
    "follow": {"on": True},
    "play": {"on": True},
    "opus": {"on": True, "max": 1, "like": True,
             "repost": {"on": False}, "comment": {"on": False}},
}


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


class Cli:
    """可配置：前 N 个动作成功，之后按 code 抛错。"""

    def __init__(self, code=None, succeed_before=0):
        self.code = code
        self.left = succeed_before
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            return
        if self.code is not None:
            raise BiliError(self.code, "模拟错误 %d" % self.code)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def like(self, *a, **k): self._maybe_fail()
    def coin(self, *a, **k): self._maybe_fail()
    def fav(self, *a, **k): self._maybe_fail()
    def share(self, *a, **k): self._maybe_fail()
    def follow(self, *a, **k): self._maybe_fail()
    def get(self, *a, **k): self._maybe_fail(); return {"items": []}
    def post(self, *a, **k): self._maybe_fail()


VIDEO = {"aid": 111, "bvid": "BV1xx", "title": "视频", "mid": 42}


def main() -> int:
    print("[1] 全部成功")
    c = Cli()
    ok, fail, msgs = do_one(c, VIDEO, ALL_ACTIONS, "42")
    ck("ok>0", ok > 0, ok)
    ck("fail=0", fail == 0, fail)
    ck("没有 FATAL", not any(m.startswith("FATAL:") for m in msgs), msgs)

    print("[2] -101（未登录）立刻停：只失败 1 次")
    c = Cli(code=-101)
    ok, fail, msgs = do_one(c, VIDEO, ALL_ACTIONS, "42")
    ck("fail=1", fail == 1, fail)
    ck("ok=0", ok == 0, ok)
    ck("有 FATAL 标记", any(m.startswith("FATAL:") for m in msgs), msgs)
    ck("只调了一次接口", c.calls == 1, c.calls)

    print("[3] -352（风控）同样立刻停")
    c = Cli(code=-352)
    ok, fail, msgs = do_one(c, VIDEO, ALL_ACTIONS, "42")
    ck("fail=1", fail == 1, fail)
    ck("有 FATAL", any(m.startswith("FATAL:") for m in msgs), msgs)
    ck("只调了一次", c.calls == 1, c.calls)

    print("[4] 非致命错误（如 -400 参数错）继续跑完")
    c = Cli(code=-400)
    ok, fail, msgs = do_one(c, VIDEO, ALL_ACTIONS, "42")
    ck("fail>1（继续尝试）", fail > 1, fail)
    ck("没有 FATAL", not any(m.startswith("FATAL:") for m in msgs), msgs)

    print("[5] FATAL_CODES 覆盖关键错误码")
    for code in (-101, -111, 61001, -352, -412):
        ck("含 %d" % code, code in FATAL_CODES, sorted(FATAL_CODES))
    ck("不含 -400", -400 not in FATAL_CODES, sorted(FATAL_CODES))

    print("[6] 部分成功后失效：保留已成功的计数")
    c = Cli(code=-101, succeed_before=2)
    ok, fail, msgs = do_one(c, VIDEO, ALL_ACTIONS, "42")
    ck("ok=2", ok == 2, ok)
    ck("fail=1", fail == 1, fail)
    ck("有 FATAL", any(m.startswith("FATAL:") for m in msgs), msgs)

    with TestClient(app) as c_:
        a, t = cap(c_)
        c_.post("/api/auth/send_code", json={"email": "af@example.com", "scene": "register",
                                             "captcha": a, "captcha_token": t})
        forge("af@example.com", "register", "111111")
        c_.post("/api/auth/register", json={"username": "afuser", "password": "passw0rdX",
                                            "email": "af@example.com", "code": "111111"})
        a, t = cap(c_)
        r = c_.post("/api/auth/login", json={"username": "afuser", "password": "passw0rdX",
                                             "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c_)
            c_.post("/api/auth/login_verify/resend",
                    json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("af@example.com", "verify", "222222")
            c_.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[7] 任务循环收到 FATAL 后写回账号状态并中止")
        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok,coeff) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1,1.0)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
        # 让 do_one 真的拿到 -101：替换 client_for
        import app.routers.interact as ia
        orig = ia.client_for
        ia.client_for = lambda acc: Cli(code=-101)
        try:
            jobs = [(1, {"aid": i, "bvid": "BV%d" % i, "title": "视频%d" % i, "mid": 42})
                    for i in range(1, 6)]
            _run_worker(1, {"actions": ALL_ACTIONS, "sleep": [1, 1]}, [1], jobs)
        finally:
            ia.client_for = orig

        ck("running 已复位", STATE.running is False, STATE.running)
        with db() as conn:
            acc = conn.execute("SELECT status, coeff FROM bili_accounts WHERE id=1").fetchone()
        ck("账号状态写回 expired", acc["status"] == "expired", acc["status"])
        ck("coeff 增大（退避）", acc["coeff"] > 1.0, acc["coeff"])
        ck("只跑了 1 个视频就中止", STATE.done == 1, STATE.done)
        ck("总任务数仍是 5", STATE.total == 5, STATE.total)
        ck("error 有内容", bool(STATE.error), STATE.error)
        ck("日志提示重新登录",
           any("重新登录" in str(x) for x in (STATE.logs or [])), (STATE.logs or [])[-3:])

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("动作级失败策略测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
