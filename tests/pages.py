"""页面与总览一致性测试：所有页面可访问、Dashboard 字段对齐、设置页与其余页正常。

运行：python tests/pages.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-pages-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "pages-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402

FAILS: list[str] = []

# 原站真实导航的 10 项 + 其余已还原页面
PAGES = {
    "/dashboard": ["总览", "账号总数", "登录有效", "硬币合计", "粉丝合计", "稿件合计",
                   "今日操作", "累计操作", "失效账号", "动作统计", "近 7 天数据趋势",
                   "账号概览", "最近操作", "去对话"],
    "/ai": ["AI 助手", "常用提问", "AI 配置", "主页总控", "私信专用", "养号专用"],
    "/accounts": ["我的账号", "添加账号", "搜索名字或 UID 数字"],
    "/accounts/add": ["添加账号", "IP 代理", "扫码登录", "手机验证码", "Cookie 导入"],
    "/publish": ["视频投稿", "本地上传", "服务器上传", "模板", "服务器视频库",
                 "封面库", "投稿记录", "开始投稿"],
    "/messages": ["私信", "自动私信", "AI 自动回复", "评论回复", "发送"],
    "/tasks": ["互赞任务", "执行账号", "操作谁的视频", "投币数量", "表情包",
               "刷播放量", "图文（点赞/转发/评论）", "保存为定时任务"],
    "/scheduler": ["AI 任务", "实时监控", "自动私信", "AI 自动回复", "评论回复",
                   "新建任务", "上次执行", "下次执行"],
    "/logs": ["操作日志", "搜索账号 / 目标 / 动作 / 结果关键词"],
    "/settings": ["我的设置", "我的代理 IP", "邮箱与通知", "换绑邮箱", "修改密码",
                  "我的模板", "我的账号", "快捷入口", "退出登录"],
    "/notices": ["消息通知", "全部标为已读"],
    "/me": ["常用功能", "账号", "记录"],
    "/templates": ["模板管理", "投稿模板", "互赞模板"],
    "/web": ["首页", "消息", "我的", "搜索视频 / BV 号 / UID", "选择账号", "正在为你推荐"],
}

DASH_KEYS = ["total", "ok", "bad", "coins", "fans", "videos", "today_ops",
             "total_ops", "stat", "trend", "accounts", "recent"]


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
        c.post("/api/auth/send_code", json={"email": "pg@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("pg@example.com", "register", "111111")
        r = c.post("/api/auth/register", json={"username": "pageuser", "password": "passw0rdX",
                                               "email": "pg@example.com", "code": "111111"})
        ck("注册", r.status_code == 200, r.text)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "pageuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("pg@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] /api/dashboard 形状（对齐线上 12 字段）")
        d = c.get("/api/dashboard").json()
        for k in DASH_KEYS:
            ck("含字段 " + k, k in d, list(d.keys()))
        ck("stat 含 8 个动作",
           set(d["stat"]) == {"like", "coin", "fav", "comment", "share", "follow",
                              "play", "dm"}, list(d["stat"]))
        ck("trend 含 dates/play/like/fans/total/points",
           {"dates", "play", "like", "fans", "total", "points"} <= set(d["trend"]),
           list(d["trend"]))
        ck("trend.dates 是 7 天", len(d["trend"]["dates"]) == 7, d["trend"]["dates"])
        ck("accounts 是数组", isinstance(d["accounts"], list), type(d["accounts"]))
        ck("recent 是数组", isinstance(d["recent"], list), type(d["recent"]))

        print("[2] 全部页面可访问 + 关键文案")
        for path, keys in PAGES.items():
            r = c.get(path)
            ck("GET " + path, r.status_code == 200, r.status_code)
            if r.status_code == 200:
                html = r.text
                missing = [k for k in keys if k not in html]
                ck(path + " 关键文案齐全", not missing, missing)

        print("[3] 导航一致性（10 个主入口）")
        home = c.get("/dashboard").text
        for label in ("总览", "AI 助手", "账号", "投稿", "私信", "互赞", "任务",
                      "日志", "设置"):
            ck("导航含「%s」" % label, label in home)

        print("[4] 未登录跳转")
        anon = TestClient(app)
        for path in ("/dashboard", "/settings", "/notices", "/me", "/invite",
                     "/templates", "/web", "/publish", "/scheduler"):
            ck("匿名访问 %s → 307" % path,
               anon.get(path, follow_redirects=False).status_code == 307)


        print("[5] /api/me/settings 真实可用（防回归：曾查不存在的 users.proxy 列）")
        r = c.get("/api/me/settings")
        ck("GET /api/me/settings 200", r.status_code == 200, r.status_code)
        if r.status_code == 200:
            d = r.json()
            ck("含 email/notify/email_verify/pool_size/proxies",
               all(k in d for k in ("email", "notify", "email_verify",
                                    "pool_size", "proxies")), list(d.keys()))
            ck("proxies 是数组", isinstance(d["proxies"], list), type(d["proxies"]))
        ck("POST /api/me/settings 保存 notify",
           c.post("/api/me/settings", json={"notify": False,
                                            "email_verify": True}).status_code == 200)
        d = c.get("/api/me/settings").json()
        ck("notify 落库为 False", d["notify"] is False, d["notify"])
        ck("空 body 被拒", c.post("/api/me/settings", json={}).status_code == 400)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("页面一致性测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
