"""账号趋势测试：/api/trend?days= 契约 + 快照差值语义 + 页面。

运行：python tests/trend.py
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

TMP = tempfile.mkdtemp(prefix="bp-trend-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "trend-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.trend_api import save_snapshot  # noqa: E402

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


def day_key(offset: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(int(time.time()) - offset * 86400))


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "tr@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("tr@example.com", "register", "111111")
        ck("注册", c.post("/api/auth/register", json={
            "username": "trenduser", "password": "passw0rdX",
            "email": "tr@example.com", "code": "111111"}).status_code == 200)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "trenduser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("tr@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] 无账号时的空态")
        d = c.get("/api/trend?days=7").json()
        ck("dates 长度=7", len(d["dates"]) == 7, d["dates"])
        ck("accounts 为空", d["accounts"] == [], d["accounts"])
        ck("days 回显", d["days"] == 7, d["days"])

        print("[2] 天数参数")
        for n in (2, 14, 30, 90):
            d = c.get("/api/trend?days=%d" % n).json()
            ck("days=%d 返回 %d 个日期" % (n, n), len(d["dates"]) == n, len(d["dates"]))
        ck("days=1 被抬到 2", len(c.get("/api/trend?days=1").json()["dates"]) == 2)
        ck("days=999 被压到 90", len(c.get("/api/trend?days=999").json()["dates"]) == 90)

        print("[3] 准备账号 + 三天快照")
        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,status,cred_enc,cred_source,"
                "created_at,proxy,coin_ok,fans,videos) "
                "VALUES(1,'9001','主号','', 'ok',?,'qr',1,'',1,100,5)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
            # 第 3 天前 → 第 1 天前 → 今天，播放 1000→1500→2200，点赞 100→180→260
            save_snapshot(conn, 1, 1, views=1000, likes=100, fans=100, videos=5)
            conn.execute("UPDATE account_snapshots SET day=? WHERE account_id=1",
                         (day_key(2),))
            save_snapshot(conn, 1, 1, views=1500, likes=180, fans=110, videos=5)
            conn.execute("UPDATE account_snapshots SET day=? WHERE account_id=1 AND day=?",
                         (day_key(1), day_key(0)))
            save_snapshot(conn, 1, 1, views=2200, likes=260, fans=130, videos=6)

        print("[4] 曲线 = 相邻两天差值")
        d = c.get("/api/trend?days=7").json()
        ck("有 1 个账号", len(d["accounts"]) == 1, len(d["accounts"]))
        acc = d["accounts"][0]
        ck("账号字段齐全",
           all(k in acc for k in ("id", "name", "mid", "status", "fans", "videos",
                                  "views", "likes", "series")), list(acc.keys()))
        ck("series 含 play/like/fans",
           set(acc["series"]) == {"play", "like", "fans"}, list(acc["series"]))
        ck("series 长度=7", len(acc["series"]["play"]) == 7, len(acc["series"]["play"]))
        # 最后两个位置：第 1 天前 +500，今天 +700
        play = acc["series"]["play"]
        ck("倒数第 2 天 = +500", play[-2] == 500, play)
        ck("今天 = +700", play[-1] == 700, play)
        ck("like 今天 = +80", acc["series"]["like"][-1] == 80, acc["series"]["like"])
        ck("fans 今天 = +20", acc["series"]["fans"][-1] == 20, acc["series"]["fans"])
        ck("累计 views 取最后快照 = 2200", acc["views"] == 2200, acc["views"])
        ck("累计 likes = 260", acc["likes"] == 260, acc["likes"])
        ck("fans 用账号当前值 100", acc["fans"] == 100, acc["fans"])

        print("[5] 同一天重复快照只更新不新增")
        with db() as conn:
            save_snapshot(conn, 1, 1, views=2300, likes=270, fans=135, videos=6)
        with db() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM account_snapshots WHERE account_id=1"
                             ).fetchone()["c"]
        ck("仍是 3 条快照", n == 3, n)
        d = c.get("/api/trend?days=7").json()
        ck("今天增量更新为 +800", d["accounts"][0]["series"]["play"][-1] == 800,
           d["accounts"][0]["series"]["play"])

        print("[6] 短窗口只返回窗口内数据")
        d = c.get("/api/trend?days=2").json()
        ck("days=2 时首日无前值记 0",
           d["accounts"][0]["series"]["play"][0] == 0, d["accounts"][0]["series"]["play"])

        print("[7] 页面")
        r = c.get("/trend")
        ck("GET /trend", r.status_code == 200, r.status_code)
        html = r.text
        for k in ("账号趋势", "近 7 天", "近 14 天", "近 30 天", "返回总览",
                  "曲线是", "每天 10:00"):
            ck("页面含「%s」" % k, k in html)

        print("[8] 总览页的链接有落点")
        ck("dashboard 仍链到 /trend", "/trend" in c.get("/dashboard").text)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("账号趋势测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
