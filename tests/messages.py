"""私信模块测试：账号/策略/会话/消息/发送/已读 + 页面。

运行：python tests/messages.py
不访问 B 站（上游失败时自动退回本地会话存储）。
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-msg-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "msg-test"
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


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "m@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("m@example.com", "register", "111111")
        r = c.post("/api/auth/register", json={"username": "msguser", "password": "passw0rdX",
                                               "email": "m@example.com", "code": "111111"})
        ck("注册", r.status_code == 200, r.text)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "msguser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("m@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] 账号与策略")
        ck("无账号时 accounts 为空", c.get("/api/messages/accounts").json()["accounts"] == [])
        d = c.get("/api/messages/policy").json()
        ck("policy 字段齐全",
           all(k in d for k in ("used", "daily_limit", "unlimited", "min_interval_sec")), d)
        ck("私信不限量", d["unlimited"] is True, d)

        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy) VALUES(1,'9001','主号','',?,'ok','qr',1,'')",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
        d = c.get("/api/messages/accounts").json()
        ck("accounts 返回 1 个", len(d["accounts"]) == 1, d)
        ck("account 含 id/uname/status",
           all(k in d["accounts"][0] for k in ("id", "uname", "status")), d["accounts"][0])

        print("[2] 会话 / 消息（上游不可用时退回本地）")
        d = c.get("/api/messages/sessions?account_id=1").json()
        ck("sessions 返回 sessions 数组", isinstance(d.get("sessions"), list), d)
        d = c.get("/api/messages/msgs?account_id=1&talker_id=555").json()
        ck("msgs 返回 msgs 数组", isinstance(d.get("msgs"), list), d)
        ck("msgs 带 emojis", "emojis" in d and len(d["emojis"]) > 0, d.get("emojis"))

        print("[3] 发送校验")
        r = c.post("/api/messages/send", json={"account_id": 1, "talker_id": "555", "text": "  "})
        ck("空内容被拒", r.status_code == 400, r.text)
        r = c.post("/api/messages/send", json={"account_id": 999, "talker_id": "555", "text": "hi"})
        ck("账号不存在 404", r.status_code == 404, r.text)

        print("[4] 发送成功（上游会是 502 或写入本地）")
        r = c.post("/api/messages/send", json={"account_id": 1, "talker_id": "555",
                                               "text": "你好呀"})
        ck("发送接口有确定返回", r.status_code in (200, 502), r.status_code)
        if r.status_code == 502:
            print("     （上游 B 站不可达，返回 502，属预期）")

        print("[5] 已读")
        ck("mark read 200",
           c.post("/api/messages/read", json={"account_id": 1, "talker_id": "555",
                                              "ack_seqno": 10}).status_code == 200)

        print("[6] 页面")
        r = c.get("/messages")
        ck("GET /messages", r.status_code == 200, r.status_code)
        html = r.text
        for k in ("私信", "自动私信", "AI 自动回复", "评论回复", "搜索会话 / 输入 UID 发起私信",
                  "表情", "发送", "输入消息"):
            ck("页面含「%s」" % k, k in html)

        print("[7] 导航完整性")
        for p in ("/dashboard", "/ai", "/accounts", "/publish", "/messages",
                  "/tasks", "/scheduler", "/vip", "/logs", "/settings"):
            ck("GET " + p, c.get(p).status_code in (200, 307), c.get(p).status_code)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("私信模块测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
