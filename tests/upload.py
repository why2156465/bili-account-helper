"""定时投稿执行测试：预检、鉴权、配置校验、真实上传路径。

运行：python tests/upload.py
不联网：用假的 BiliClient 拦截上游，验证分片与提交的调用序列。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-upload-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "upload-test"
os.environ["WORKER_ENABLED"] = "false"

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


CFG = {"account_id": 1, "file_id": 1, "cover": "", "title": "定时投稿测试",
       "tid": "171", "tag": "测试", "desc": "desc",
       "no_danmu": False, "no_reply": True, "selected": False, "no_reprint": True,
       "dtime": 0}


def main() -> int:
    with TestClient(app) as c:
        # 注册 + 登录
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "u@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("u@example.com", "register", "111111")
        ck("注册", c.post("/api/auth/register", json={
            "username": "upuser", "password": "passw0rdX",
            "email": "u@example.com", "code": "111111"}).status_code == 200)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "upuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("u@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] 预检：缺账号")
        d = c.post("/api/publish/run-upload", json={"config": {"file_id": 1},
                                                    "dry_run": True}).json()
        ck("返回 preflight 失败", d["ok"] is False and d["preflight"] is True, d)
        ck("明确说没指定账号", any("账号" in e for e in d["errors"]), d["errors"])

        print("[2] 准备账号与视频文件")
        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
        # 写一个真实文件到视频库
        vdir = Path(ROOT) / "data" / "vfiles"
        vdir.mkdir(parents=True, exist_ok=True)
        p = vdir / "test_video.mp4"
        p.write_bytes(b"\x00" * (9 * 1024 * 1024))     # 9MB → 2 个分片
        with db() as conn:
            conn.execute(
                "INSERT INTO publish_files(user_id,code,name,path,size,created_at) "
                "VALUES(1,1,'test_video.mp4',?,?,1)", (str(p), p.stat().st_size))

        print("[3] 预检：通过")
        d = c.post("/api/publish/run-upload", json={"config": CFG, "dry_run": True}).json()
        ck("预检通过", d["ok"] is True, d)
        pl = d.get("planned", {})
        ck("planned 含账号/文件/标题/流量",
           all(k in pl for k in ("account", "file", "size_h", "cost_mb", "title")), pl)
        ck("不再计算流量消耗", pl.get("cost_mb") == 0, pl.get("cost_mb"))

        print("[4] 缺文件的预检")
        with db() as conn:
            conn.execute("UPDATE publish_files SET path='/nonexistent/x.mp4' WHERE id=1")
        d = c.post("/api/publish/run-upload", json={"config": CFG, "dry_run": True}).json()
        ck("磁盘丢失被识别", any("丢失" in e for e in d["errors"]), d["errors"])
        with db() as conn:
            conn.execute("UPDATE publish_files SET path=? WHERE id=1", (str(p),))

        print("[5] 真实执行：用假上游验证调用序列")
        calls = []

        class FakeResp:
            status_code = 200

            def json(self):
                return {}

        class FakeHttpx:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def put(self, url, content=None, params=None, headers=None):
                calls.append(("put", params.get("partNumber"), len(content or b"")))
                return FakeResp()

        class FakeCli:
            ident = {"csrf": "y", "uid": "9001"}

            def post(self, path, data=None, params=None, **kw):
                calls.append(("post", path))
                if path.endswith("preupload/v2"):
                    return {"url": "https://upos.example/upload", "auth": "AUTH",
                            "upload_id": "UID123"}
                if path.endswith("add/v3"):
                    return {"bvid": "BV1test", "aid": 12345}
                return {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        orig_client_for = pu.client_for
        orig_httpx = pu.httpx.Client
        pu.client_for = lambda acc: FakeCli()
        pu.httpx.Client = FakeHttpx
        try:
            d = c.post("/api/publish/run-upload", json={"config": CFG}).json()
        finally:
            pu.client_for = orig_client_for
            pu.httpx.Client = orig_httpx

        ck("执行成功", d.get("ok") is True, d)
        ck("返回 bvid", d.get("bvid") == "BV1test", d.get("bvid"))
        posts = [x[1] for x in calls if x[0] == "post"]
        ck("先 preupload", any("preupload" in x for x in posts), posts)
        ck("再 add/v3 提交", any("add/v3" in x for x in posts), posts)
        puts = [x for x in calls if x[0] == "put"]
        ck("9MB 切成 2 片", len(puts) == 2, puts)
        ck("分片号从 1 开始", [x[1] for x in puts] == [1, 2], puts)
        ck("返回 logs", isinstance(d.get("logs"), list) and d["logs"], d.get("logs"))

        print("[6] 执行后副作用")
        with db() as conn:
            rec = conn.execute("SELECT * FROM publish_records WHERE user_id=1").fetchall()
            u = conn.execute("SELECT traffic_used_mb FROM users WHERE id=1").fetchone()
        ck("写入了投稿记录", len(rec) == 1 and rec[0]["bvid"] == "BV1test", [dict(r) for r in rec])
        ck("流量不再扣减", int(u["traffic_used_mb"]) == 0, u["traffic_used_mb"])

        print("[7] 投稿型定时任务可被 /run 触发")
        r = c.post("/api/tasks", json={"name": "定时投稿", "cron": "0 9 * * *",
                                       "kind": "upload", "config": CFG})
        ck("建投稿任务", r.status_code == 200, r.text)
        tid = r.json()["id"]
        # 触发时上游仍用假实现
        pu.client_for = lambda acc: FakeCli()
        pu.httpx.Client = FakeHttpx
        try:
            d = c.post("/api/tasks/%d/run" % tid).json()
        finally:
            pu.client_for = orig_client_for
            pu.httpx.Client = orig_httpx
        ck("任务 /run 走投稿执行器", "bvid" in d or "message" in d, d)

        print("[8] 非投稿任务的 /run 仍走互赞执行器")
        with db() as conn:
            conn.execute("UPDATE interact_tasks SET kind='interact', "
                         "config=json_set(config,'$.cron','0 9 * * *') WHERE id=?", (tid,))
        # 上传配置缺 actions/targets，应给出可读的 400 而不是崩
        d = c.post("/api/tasks/%d/run" % tid)
        ck("上传配置走互赞被明确拒绝", d.status_code == 400 and "动作" in d.text, d.text)

        print("[9] 页面按钮已接上")
        html = c.get("/scheduler").text
        ck("scheduler 含 run-upload", "run-upload" in html)
        ck("scheduler 含预检提示", "不能执行" in html)

        # 清理
        p.unlink(missing_ok=True)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("定时投稿执行测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
