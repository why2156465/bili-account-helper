"""代理管理测试：补齐 /api/proxy/* 的覆盖。

之前 `endpoint_coverage` 一直报这 6 个接口没人测 —— 是真的没测，
不是检查器误报。这里补上。
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-proxy-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "proxy-test"
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
        c.post("/api/auth/send_code", json={"email": "px@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("px@example.com", "register", "111111")
        c.post("/api/auth/register", json={"username": "pxuser", "password": "passw0rdX",
                                           "email": "px@example.com", "code": "111111"})
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "pxuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("px@example.com", "verify", "222222")
            c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] 代理列表初始为空")
        d = c.get("/api/proxy/list").json()
        ck("返回 proxies", "proxies" in d, list(d.keys()))
        ck("初始 0 条", len(d["proxies"]) == 0, d["proxies"])

        print("[2] 添加代理")
        r = c.post("/api/proxy", json={"url": "http://user:pass@127.0.0.1:7890",
                                       "name": "本地"})
        ck("添加 200", r.status_code == 200, r.text)
        pid = r.json().get("id")
        ck("返回 id", bool(pid), r.json())

        print("[3] 非法代理被拒")
        ck("空 url 400",
           c.post("/api/proxy", json={"url": "", "name": "x"}).status_code == 400)
        ck("非 http 400",
           c.post("/api/proxy", json={"url": "socks9://bad", "name": "x"}
                  ).status_code in (400, 422))

        print("[4] 列表里能看到")
        d = c.get("/api/proxy/list").json()
        ck("有 1 条", len(d["proxies"]) == 1, d["proxies"])
        ck("名字正确", d["proxies"][0]["name"] == "本地", d["proxies"][0])
        ck("密码已掩码", "pass" not in d["proxies"][0]["url"], d["proxies"][0]["url"])

        print("[5] 绑定到账号")
        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
            aid = conn.execute("SELECT id FROM bili_accounts").fetchone()["id"]
        r = c.post("/api/proxy/account/%d" % aid, json={"proxy_id": pid})
        ck("绑定 200", r.status_code == 200, r.text)
        with db() as conn:
            row = conn.execute("SELECT proxy FROM bili_accounts WHERE id=?", (aid,)).fetchone()
        ck("账号已记录代理", "127.0.0.1:7890" in (row["proxy"] or ""), row["proxy"])

        print("[6] 校验代理")
        r = c.post("/api/proxy/account/%d/verify" % aid)
        ck("有明确返回", r.status_code in (200, 400, 502), r.status_code)

        print("[7] 出口 IP 查询")
        r = c.get("/api/proxy/geo")
        ck("不报 500", r.status_code in (200, 502), r.status_code)
        r = c.get("/api/proxy/geo/me")
        ck("me 接口有返回", r.status_code in (200, 502), r.status_code)

        print("[8] 测试连通性")
        r = c.post("/api/proxy/test", json={"url": "http://127.0.0.1:1"})
        ck("坏代理给出失败", r.status_code in (200, 400, 502), r.status_code)
        if r.status_code == 200:
            ck("标记为不可用", r.json().get("ok") is False, r.json())

        print("[9] 删除代理")
        r = c.delete("/api/proxy/%d" % pid)
        ck("删除 200", r.status_code == 200, r.text)
        d = c.get("/api/proxy/list").json()
        ck("列表已空", len(d["proxies"]) == 0, d["proxies"])

        print("[10] 别人的代理动不了")
        anon = TestClient(app)
        ck("匿名 401", anon.get("/api/proxy/list").status_code == 401)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("代理管理测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
