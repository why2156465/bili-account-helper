"""补齐端点测试：/api/ai/log、/api/publish/server-from-lib、/api/automation/ai/prompt、
/api/ai/templates。运行：python tests/fills.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-fills-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "fills-test"
os.environ["WORKER_ENABLED"] = "false"
os.environ["AI_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

import app.routers.publish_upload as pu  # noqa: E402
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


class FakeResp:
    status_code = 200


class FakeHttpx:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def put(self, url, content=None, params=None, headers=None):
        return FakeResp()


class FakeCli:
    ident = {"csrf": "y", "uid": "9001"}

    def post(self, path, data=None, params=None, **kw):
        if path.endswith("preupload/v2"):
            return {"url": "https://upos/x", "auth": "A", "upload_id": "U1"}
        if path.endswith("add/v3"):
            return {"bvid": "BV1lib", "aid": 99}
        return {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "f@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("f@example.com", "register", "111111")
        ck("注册", c.post("/api/auth/register", json={
            "username": "filluser", "password": "passw0rdX",
            "email": "f@example.com", "code": "111111"}).status_code == 200)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "filluser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("f@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] /api/ai/log")
        c.post("/api/ai/chat", json={"message": "你好", "history": []})
        d = c.get("/api/ai/log?limit=50").json()
        ck("返回 items", isinstance(d.get("items"), list) and d["items"], d)
        ck("每条含 source/role/text/at",
           all(k in d["items"][0] for k in ("source", "role", "text", "at")), d["items"][0])
        ck("按 source 过滤",
           len(c.get("/api/ai/log?source=main").json()["items"]) >= 1)
        ck("未知 source 返回空", c.get("/api/ai/log?source=nope").json()["items"] == [])

        print("[2] /api/ai/templates 列表")
        ck("GET templates", isinstance(c.get("/api/ai/templates").json().get("templates"), list))

        print("[3] /api/automation/ai/prompt")
        d = c.get("/api/automation/ai/prompt?allow=like,coin,comment").json()
        ck("返回 prompt", bool(d.get("prompt")), d)
        ck("包含允许动作中文名", "点赞" in d["prompt"] and "投币" in d["prompt"], d["prompt"][:80])
        ck("包含硬性约束", "风控" in d["prompt"], d["prompt"][-60:])
        r = c.post("/api/ai/profiles", json={"scope": "dm", "name": "私信AI",
                                             "persona": "简短友好"})
        pid = r.json()["id"]
        d = c.get("/api/automation/ai/prompt?profile_id=%d&allow=dm" % pid).json()
        ck("带 profile 时注入人设", "简短友好" in d["prompt"], d["prompt"][:120])
        ck("私信专用范围生效", "私信专用" in d["prompt"], d["prompt"][:80])
        ck("不存在的 profile 404",
           c.get("/api/automation/ai/prompt?profile_id=9999").status_code == 404)

        print("[4] /api/publish/server-from-lib")
        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
        vdir = Path(ROOT) / "data" / "vfiles"
        vdir.mkdir(parents=True, exist_ok=True)
        p = vdir / "lib_video.mp4"
        p.write_bytes(b"\x00" * (3 * 1024 * 1024))
        with db() as conn:
            conn.execute(
                "INSERT INTO publish_files(user_id,code,name,path,size,created_at) "
                "VALUES(1,1,'lib_video.mp4',?,?,1)", (str(p), p.stat().st_size))

        # 流量不足 → 402
        with db() as conn:
            conn.execute("UPDATE users SET traffic_mb=0, traffic_used_mb=0 WHERE id=1")
        r = c.post("/api/publish/server-from-lib", json={
            "account_id": 1, "file_id": 1, "config": {"title": "库投稿"}})
        ck("流量不足 402", r.status_code == 402 and "流量不足" in r.text, r.text)

        with db() as conn:
            conn.execute("UPDATE users SET traffic_mb=1024, traffic_used_mb=0 WHERE id=1")

        orig_cf, orig_hx = pu.client_for, pu.httpx.Client
        pu.client_for = lambda acc: FakeCli()
        pu.httpx.Client = FakeHttpx
        try:
            r = c.post("/api/publish/server-from-lib", json={
                "account_id": 1, "file_id": 1,
                "config": {"title": "库投稿", "tid": "171", "tag": "t"}})
        finally:
            pu.client_for, pu.httpx.Client = orig_cf, orig_hx

        ck("库投稿成功", r.status_code == 200, r.text)
        j = r.json()
        ck("返回 bvid/title/traffic_h/size_h",
           all(k in j for k in ("bvid", "title", "traffic_h", "size_h")), j)
        ck("bvid 正确", j["bvid"] == "BV1lib", j["bvid"])
        ck("标题用配置里的", j["title"] == "库投稿", j["title"])
        with db() as conn:
            u = conn.execute("SELECT traffic_used_mb FROM users WHERE id=1").fetchone()
        ck("流量不再扣减", int(u["traffic_used_mb"]) == 0, u["traffic_used_mb"])

        print("[5] 库投稿的错误分支")
        r = c.post("/api/publish/server-from-lib",
                   json={"account_id": 999, "file_id": 1, "config": {}})
        ck("账号不存在 404", r.status_code == 404, r.text)
        r = c.post("/api/publish/server-from-lib",
                   json={"account_id": 1, "file_id": 999, "config": {}})
        ck("文件不存在 404", r.status_code == 404, r.text)
        with db() as conn:
            conn.execute("UPDATE publish_files SET path='/nope/x.mp4' WHERE id=1")
        r = c.post("/api/publish/server-from-lib",
                   json={"account_id": 1, "file_id": 1, "config": {}})
        ck("文件丢失 400", r.status_code == 400 and "丢失" in r.text, r.text)
        p.unlink(missing_ok=True)

        print("[6] 端点全部注册")
        spec = app.openapi()["paths"]
        for path in ("/api/ai/log", "/api/ai/templates", "/api/automation/ai/prompt",
                     "/api/publish/server-from-lib"):
            ck("注册 " + path, path in spec)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("补齐端点测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
