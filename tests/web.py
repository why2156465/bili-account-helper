"""站内浏览模块测试：15 个 /api/web/* 接口契约 + 页面结构。

运行：python tests/web.py
真实上游用假 BiliClient 拦截，验证参数与调用路径。
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

TMP = tempfile.mkdtemp(prefix="bp-web-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "web-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import app.routers.web_api as wa  # noqa: E402
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


CALLS: list[tuple] = []


class FakeCli:
    ident = {"csrf": "CSRF", "uid": "9001"}

    def get(self, path, params=None, **kw):
        CALLS.append(("get", path, dict(params or {})))
        if path.endswith("rcmd"):
            return {"item": [{"bvid": "BV1aaa", "aid": 1, "title": "推荐视频",
                              "pic": "http://pic/1.jpg", "duration": 100,
                              "owner": {"name": "UP主", "mid": 42},
                              "stat": {"view": 5000, "danmaku": 12}}]}
        if path.endswith("/card"):
            return {"card": {"mid": params["mid"], "name": "某人", "face": "http://f/a.jpg",
                             "sign": "签名", "level_info": {"current_level": 5},
                             "archive_count": 7},
                    "follower": 100, "following": 20,
                    "relation": {"attribute": 2}}
        if "arc/search" in path:
            return {"list": {"vlist": [{"bvid": "BV1bbb", "aid": 2, "title": "投稿",
                                        "pic": "http://p/2.jpg", "author": "某人",
                                        "mid": 42, "play": 88, "created": 1700000000,
                                        "length": "03:00"}]},
                    "page": {"count": 1}}
        if path.endswith("/view"):
            return {"bvid": params["bvid"], "aid": 1, "title": "视频标题",
                    "pic": "http://p/1.jpg", "desc": "简介",
                    "owner": {"mid": 42, "name": "UP主"},
                    "stat": {"view": 5000, "like": 100, "coin": 20,
                             "favorite": 30, "share": 5, "danmaku": 8},
                    "pages": [{}]}
        if "reply/wbi/main" in path:
            return {"replies": [{"rpid": 111, "root": 111,
                                 "content": {"message": "这是一级评论"},
                                 "member": {"uname": "观众", "avatar": "http://a/x.jpg",
                                            "mid": 7},
                                 "like": 3, "ctime": 1700000001, "rcount": 2}],
                    "page": {"count": 1}}
        if "reply/reply" in path:
            return {"replies": [{"rpid": 222, "content": {"message": "子评论"},
                                 "member": {"uname": "回复者", "avatar": "", "mid": 8},
                                 "ctime": 1700000002}]}
        if "history/cursor" in path:
            return {"list": [{"title": "看过", "cover": "http://c/1.jpg",
                              "author_name": "UP", "author_mid": 42,
                              "progress": 30, "duration": 200,
                              "history": {"bvid": "BV1ccc", "oid": 3}}]}
        if "relation/followings" in path:
            return {"list": [{"mid": 42, "uname": "关注的人",
                              "face": "http://f/42.jpg", "sign": "s", "fans": 9}],
                    "total": 1}
        return {}

    def post(self, path, data=None, params=None, **kw):
        CALLS.append(("post", path, dict(data or {})))
        return {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "w@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("w@example.com", "register", "111111")
        ck("注册", c.post("/api/auth/register", json={
            "username": "webuser", "password": "passw0rdX",
            "email": "w@example.com", "code": "111111"}).status_code == 200)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "webuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("w@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] 无账号时明确报错")
        r = c.get("/api/web/feed?account_id=1&ps=10")
        ck("feed 无账号 400", r.status_code == 400 and "账号" in r.text, r.text)

        print("[2] 准备账号")
        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))

        orig = wa.client_for
        wa.client_for = lambda acc: FakeCli()
        try:
            print("[3] /feed 推荐流")
            d = c.get("/api/web/feed?account_id=1&ps=10").json()
            ck("feed 返回 videos", isinstance(d.get("videos"), list) and d["videos"], d)
            v = d["videos"][0]
            ck("视频字段 bvid/title/author/mid",
               all(k in v for k in ("bvid", "title", "author", "mid")), v)
            ck("调用了 rcmd 接口", any("rcmd" in x[1] for x in CALLS), CALLS[-3:])

            print("[4] /space 空间")
            d = c.get("/api/web/space?account_id=1&mid=42").json()
            ck("space 返回 info/relation/videos",
               all(k in d for k in ("info", "relation", "videos")), list(d.keys()))
            ck("info 含 name/fans/level",
               all(k in d["info"] for k in ("name", "fans", "level")), d["info"])
            ck("relation.follow=true", d["relation"]["follow"] is True, d["relation"])

            print("[5] /space/follow 关注")
            r = c.post("/api/web/space/follow", json={"account_id": 1, "mid": 42, "act": 1})
            ck("关注 200", r.status_code == 200, r.text)
            ck("调 relation/modify 且 act=1",
               any(x[1].endswith("relation/modify") and x[2].get("act") == 1 for x in CALLS),
               [x for x in CALLS if "relation" in x[1]][-1:])

            print("[6] /info 视频详情")
            d = c.get("/api/web/info?account_id=1&bvid=BV1aaa").json()
            ck("info 含 owner/stat/like/coin",
               all(k in d for k in ("owner", "stat", "like", "coin", "favorite")), d)
            ck("owner.mid=42", d["owner"]["mid"] == 42, d["owner"])

            print("[7] /act 互动")
            for act in ("like", "coin", "fav", "share"):
                r = c.post("/api/web/act", json={"account_id": 1, "bvid": "BV1aaa",
                                                 "aid": 1, "act": act})
                ck("act " + act, r.status_code == 200, r.text)
            r = c.post("/api/web/act", json={"account_id": 1, "bvid": "BV1aaa",
                                             "aid": 1, "act": "follow", "mid": 42})
            ck("act follow", r.status_code == 200, r.text)
            ck("不支持的 act 被拒",
               c.post("/api/web/act", json={"account_id": 1, "act": "nope"}).status_code == 400)

            print("[8] 评论相关")
            d = c.get("/api/web/replies?account_id=1&bvid=BV1aaa&aid=1").json()
            ck("replies 返回 replies/count",
               "replies" in d and "count" in d, list(d.keys()))
            ck("评论字段 rpid/text/uname",
               all(k in d["replies"][0] for k in ("rpid", "text", "uname")), d["replies"][0])
            d = c.get("/api/web/replies/sub?account_id=1&aid=1&root=111").json()
            ck("子评论", len(d.get("replies", [])) == 1, d)
            ck("发评论",
               c.post("/api/web/reply/add", json={"account_id": 1, "bvid": "BV1aaa",
                                                  "aid": 1, "text": "好看"}).status_code == 200)
            ck("空评论被拒",
               c.post("/api/web/reply/add", json={"account_id": 1, "text": ""}).status_code == 400)
            ck("回复评论",
               c.post("/api/web/reply/to", json={"account_id": 1, "bvid": "BV1aaa", "aid": 1,
                                                 "rpid": 111, "root": 111,
                                                 "text": "同感"}).status_code == 200)
            ck("删评论",
               c.post("/api/web/reply/delete", json={"account_id": 1, "bvid": "BV1aaa",
                                                     "aid": 1, "rpid": 111}).status_code == 200)

            print("[9] 我的：投稿/点赞/关注/观看记录")
            d = c.get("/api/web/mine?account_id=1&pn=1&ps=24").json()
            ck("mine 返回 videos/total", "videos" in d and "total" in d, list(d.keys()))
            d = c.get("/api/web/likes?account_id=1&ps=20&pn=1").json()
            ck("likes 返回 videos/total", "videos" in d and "total" in d, list(d.keys()))
            d = c.get("/api/web/follows?account_id=1&ps=30&pn=1").json()
            ck("follows 返回 ups/total",
               "ups" in d and "total" in d and d["ups"][0]["mid"] == 42, d)
            d = c.get("/api/web/history?account_id=1&ps=20").json()
            ck("history 返回 videos 且带 progress",
               d["videos"] and "progress" in d["videos"][0], d)

            print("[10] 删除稿件")
            ck("archive/delete",
               c.post("/api/web/archive/delete",
                      json={"account_id": 1, "aid": 2}).status_code == 200)
        finally:
            wa.client_for = orig

        print("[11] 页面结构")
        r = c.get("/web")
        ck("GET /web", r.status_code == 200, r.status_code)
        html = r.text
        for k in ("首页", "消息", "我的", "搜索视频 / BV 号 / UID", "选择账号",
                  "正在为你推荐", "发一条友善的评论", "投稿", "点赞", "关注", "观看记录"):
            ck("页面含「%s」" % k, k in html)

        print("[12] 15 个接口全部注册")
        spec = app.openapi()["paths"]
        for p in ("/api/web/feed", "/api/web/space", "/api/web/space/follow",
                  "/api/web/info", "/api/web/act", "/api/web/replies",
                  "/api/web/replies/sub", "/api/web/reply/add", "/api/web/reply/to",
                  "/api/web/reply/delete", "/api/web/history", "/api/web/likes",
                  "/api/web/follows", "/api/web/mine", "/api/web/archive/delete"):
            ck("注册 " + p, p in spec)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("站内浏览模块测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
