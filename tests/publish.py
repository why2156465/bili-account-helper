"""投稿模块测试：分区/参数、视频库、封面库、会话、记录、模板。

运行：python tests/publish.py
上传用内存文件，不访问 B 站（preupload 会失败，属预期，单独断言）。
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-pub-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "pub-test"
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
    app.state.storage = os.path.join(TMP, "data")

    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "p@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("p@example.com", "register", "111111")
        r = c.post("/api/auth/register", json={"username": "pubuser", "password": "passw0rdX",
                                               "email": "p@example.com", "code": "111111"})
        ck("注册", r.status_code == 200, r.text)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "pubuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("p@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] 分区与参数")
        d = c.get("/api/publish/sections").json()
        ck("sections 非空", len(d["sections"]) > 5, len(d.get("sections", [])))
        ck("sections 字段 tid/name",
           "tid" in d["sections"][0] and "name" in d["sections"][0], d["sections"][0])
        d = c.get("/api/publish/opts").json()
        ck("opts 含 md5_random/ai_title/interval",
           all(k in d for k in ("md5_random", "ai_title", "interval_min")), list(d.keys()))

        print("[2] 服务器视频库")
        ck("空库", c.get("/api/publish/files").json()["files"] == [])
        fake_video = io.BytesIO(b"\x00" * (1024 * 512))
        r = c.post("/api/publish/files", files={"file": ("demo.mp4", fake_video, "video/mp4")})
        ck("上传视频", r.status_code == 200 and r.json().get("code") == 1, r.text)
        fid = r.json()["id"]
        d = c.get("/api/publish/files").json()
        ck("列表 1 条", len(d["files"]) == 1, d)
        ck("含 code/name/size_h",
           all(k in d["files"][0] for k in ("code", "name", "size_h", "url")), d["files"][0])
        ck("原始文件可下载", c.get("/api/publish/files/%d/raw" % fid).status_code == 200)

        print("[3] 封面库")
        fake_img = io.BytesIO(b"\xff\xd8\xff\xe0fakejpeg")
        r = c.post("/api/publish/covers",
                   files={"file": ("c.jpg", fake_img, "image/jpeg")}, data={"ratio": "16:9"})
        ck("上传封面", r.status_code == 200 and r.json().get("code") == 1, r.text)
        cid = r.json()["id"]
        d = c.get("/api/publish/covers").json()
        ck("封面列表", len(d["covers"]) == 1 and d["covers"][0]["ratio"] == "16:9", d)
        ck("封面可读", c.get("/api/publish/covers/%d/raw" % cid).status_code == 200)

        print("[4] 上传会话（断点续传）")
        r = c.post("/api/publish/session", json={"kind": "local", "name": "demo.mp4",
                                                 "size": 1024, "account_id": 1,
                                                 "info": {"config": {"title": "t"}}})
        ck("建会话", r.status_code == 200 and r.json().get("id"), r.text)
        sid = r.json()["id"]
        ck("更新进度", c.post("/api/publish/session/%d" % sid,
                              json={"pct": 50, "message": "上传中"}).status_code == 200)
        d = c.get("/api/publish/sessions").json()
        ck("会话可读且 pct=50", d["sessions"][0]["pct"] == 50, d)

        print("[5] 投稿记录")
        with db() as conn:
            conn.execute(
                "INSERT INTO publish_records(user_id,account_id,title,bvid,aid,tid,status,"
                "created_at) VALUES(1,1,'测试稿件','BV1xx411c7mD','123','171','ok',1)")
        d = c.get("/api/publish/records").json()
        ck("记录可读", len(d["records"]) == 1, d)
        ck("记录字段齐全",
           all(k in d["records"][0] for k in ("title", "bvid", "status", "created_at",
                                             "account")), d["records"][0])
        ck("删除记录", c.post("/api/publish/records/%d/delete" % d["records"][0]["id"]).status_code == 200)

        print("[6] 模板（kind=publish）")
        r = c.post("/api/templates", json={"name": "投稿模板1", "kind": "publish",
                                           "config": {"title": "标题A", "tid": "21",
                                                      "tag": "vlog"}})
        ck("存模板", r.status_code == 200, r.text)
        d = c.get("/api/templates?kind=publish").json()
        ck("按 kind 过滤", len(d["templates"]) == 1, d)
        ck("config 回读", d["templates"][0]["config"]["title"] == "标题A", d)

        print("[7] 无账号时投稿被拒（预期）")
        r = c.post("/api/publish/preupload", json={"account_id": 999,
                                                   "filename": "x.mp4", "size": 100})
        ck("不存在的账号 404", r.status_code == 404, r.text)

        print("[8] 页面")
        r = c.get("/publish")
        ck("GET /publish", r.status_code == 200, r.status_code)
        html = r.text
        for k in ("视频投稿", "本地上传", "服务器上传", "模板", "服务器视频库", "封面库",
                  "投稿记录", "从封面库选择", "开始投稿", "加入定时任务", "修改视频 MD5"):
            ck("页面含「%s」" % k, k in html)

        print("[9] 清理")
        ck("删除视频", c.post("/api/publish/files/%d/delete" % fid).status_code == 200)
        ck("删除封面", c.post("/api/publish/covers/%d/delete" % cid).status_code == 200)
        ck("库已空", c.get("/api/publish/files").json()["files"] == [])

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("投稿模块测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
