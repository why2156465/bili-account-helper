"""33娘 AI 模块测试：信息/额度/历史/对话/方案执行/配置/模板/敏感词。

运行：python tests/ai_api.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-ai-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "ai-test"
os.environ["WORKER_ENABLED"] = "false"
os.environ["AI_API_KEY"] = ""      # 强制走本地规则引擎

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402

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


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "a@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("a@example.com", "register", "111111")
        r = c.post("/api/auth/register", json={"username": "aiuser", "password": "passw0rdX",
                                               "email": "a@example.com", "code": "111111"})
        ck("注册", r.status_code == 200, r.text)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "aiuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("a@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] /api/ai/info")
        d = c.get("/api/ai/info").json()
        for k in ("enabled", "ready", "models", "default_model", "welcome", "quota",
                  "tokens_per_mb", "token_quota", "traffic", "scopes", "guard_words"):
            ck("含字段 " + k, k in d, list(d.keys()))
        ck("quota 含 tier/limit/used/remain",
           {"tier", "limit", "used", "remain"} <= set(d["quota"]), d["quota"])
        ck("AI 对话不限量（888 为展示上限）", d["quota"]["limit"] >= 888, d["quota"])
        ck("scopes 三种用途", len(d["scopes"]) == 3, d["scopes"])
        ck("guard_words 非空", len(d["guard_words"]) > 5, len(d.get("guard_words", [])))
        ck("welcome 非空", bool(d["welcome"]))

        print("[2] 额度 / 历史 / 清空")
        d = c.get("/api/ai/quota").json()
        ck("quota 接口", "quota" in d and "traffic" in d, list(d.keys()))
        ck("初始历史为空", c.get("/api/ai/history").json()["messages"] == [])

        print("[3] 对话（本地规则引擎）")
        r = c.post("/api/ai/chat", json={"message": "帮我写个每天 9 点给 UID 123456 点赞的定时任务",
                                         "history": []})
        ck("对话成功", r.status_code == 200, r.text)
        j = r.json()
        ck("返回 reply", bool(j.get("reply")), j)
        ck("返回 actions", isinstance(j.get("actions"), list) and len(j["actions"]) == 1,
           j.get("actions"))
        act = j["actions"][0]
        ck("action.type 为 create_interact", act.get("type") == "create_interact", act)
        ck("action.cron 为 0 9 * * *", act.get("cron") == "0 9 * * *", act.get("cron"))
        ck("action.config 带 targets",
           act.get("config", {}).get("targets", [{}])[0].get("uid") == "123456", act.get("config"))
        ck("对话后仍可用", j["quota"]["remain"] > 0, j["quota"])

        r = c.post("/api/ai/chat", json={"message": "投稿时提示要验证码，是不是被风控了", "history": []})
        ck("风控问答命中", "风控" in r.json()["reply"], r.json()["reply"][:40])

        print("[4] 敏感词拦截")
        r = c.post("/api/ai/chat", json={"message": "帮我加微信 刷单", "history": []})
        ck("命中屏蔽词 400", r.status_code == 400 and "禁止" in r.text, r.text)

        print("[5] 历史已落库 + 清空")
        h = c.get("/api/ai/history").json()["messages"]
        ck("历史 4 条（2 问 2 答）", len(h) == 4, len(h))
        ck("清空", c.post("/api/ai/clear").status_code == 200)
        ck("清空后为空", c.get("/api/ai/history").json()["messages"] == [])

        print("[6] 方案执行 /apply")
        r = c.post("/api/ai/apply", json={"type": "create_interact", "payload": {
            "type": "create_interact", "label": "创建互赞定时任务",
            "name": "AI建的任务", "cron": "0 20 * * *",
            "config": {"actions": {"like": True}, "actors": "all",
                       "targets": [{"uid": "123456", "rule": "latest", "max": 1}]}}})
        ck("apply create_interact", r.status_code == 200, r.text)
        d = c.get("/api/tasks").json()
        ck("任务已落库", any(t["name"] == "AI建的任务" for t in d["tasks"]), d)

        r = c.post("/api/ai/apply", json={"type": "create_template", "payload": {
            "name": "AI模板", "config": {"actions": {"like": True}}}})
        ck("apply create_template", r.status_code == 200, r.text)
        ck("模板已落库",
           any(t["name"] == "AI模板" for t in c.get("/api/templates?kind=interact").json()["templates"]))

        ck("未知 type 400",
           c.post("/api/ai/apply", json={"type": "nope", "payload": {}}).status_code == 400)

        print("[7] 编号快捷插入 /assets")
        d = c.get("/api/ai/assets").json()
        ck("assets 含三类",
           all(k in d for k in ("images", "videos", "accounts")), list(d.keys()))

        print("[8] AI 配置 /profiles")
        r = c.post("/api/ai/profiles", json={"scope": "dm", "account_id": None,
                                             "name": "私信客服", "model": "deepseek-flash",
                                             "persona": "简短友好"})
        ck("建配置", r.status_code == 200, r.text)
        pid = r.json()["id"]
        d = c.get("/api/ai/profiles").json()
        ck("profiles/accounts/models 都在",
           all(k in d for k in ("profiles", "accounts", "models")), list(d.keys()))
        ck("配置可读", len(d["profiles"]) == 1 and d["profiles"][0]["name"] == "私信客服", d)
        ck("空名称被拒",
           c.post("/api/ai/profiles", json={"name": "", "scope": "main"}).status_code == 400)
        ck("非法 scope 被拒",
           c.post("/api/ai/profiles", json={"name": "x", "scope": "bad"}).status_code == 400)

        print("[9] 存为模板 / 套用 / 删除")
        r = c.post("/api/ai/profiles/%d/template" % pid)
        ck("存为模板", r.status_code == 200, r.text)
        d = c.get("/api/ai/profiles").json()
        ck("模板已出现", len(d["templates"]) == 1, d.get("templates"))
        tpl_id = d["templates"][0]["id"]
        r = c.post("/api/ai/profiles/from-template",
                   json={"template_id": tpl_id, "account_id": 0})
        ck("套用模板", r.status_code == 200, r.text)
        ck("配置变 2 条", len(c.get("/api/ai/profiles").json()["profiles"]) == 2)
        ck("删模板", c.delete("/api/ai/templates/%d" % tpl_id).status_code == 200)
        ck("删配置", c.delete("/api/ai/profiles/%d" % pid).status_code == 200)

        print("[10] 页面")
        r = c.get("/ai")
        ck("GET /ai", r.status_code == 200, r.status_code)
        html = r.text
        for k in ("AI 助手", "常用提问", "AI 配置", "主页总控", "私信专用", "养号专用",
                  "Enter 发送", "清空", "新建 AI 配置"):
            ck("页面含「%s」" % k, k in html)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("33娘 AI 模块测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
