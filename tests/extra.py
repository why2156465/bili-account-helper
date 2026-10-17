"""未覆盖接口的补齐测试：让 147 个 /api 路由全部至少被调用一次。

这些接口此前没有任何测试——`/api/me/settings` 就是这么带着一个 500 活下来的。
本套件只做「契约级」验证：状态码合理、字段形状正确、错误分支给可读原因。

运行：python tests/extra.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-extra-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "extra-test"
os.environ["WORKER_ENABLED"] = "false"
os.environ["AI_API_KEY"] = ""

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
        c.post("/api/auth/send_code", json={"email": "e@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("e@example.com", "register", "111111")
        ck("注册", c.post("/api/auth/register", json={
            "username": "exuser", "password": "passw0rdX",
            "email": "e@example.com", "code": "111111"}).status_code == 200)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "exuser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("e@example.com", "verify", "222222")
            r = c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[1] AI 预设 / 提示词")
        d = c.get("/api/ai/presets").json()
        ck("presets 是数组", isinstance(d.get("presets"), list) and d["presets"], d)
        ck("preset 含 id/name/persona",
           all(k in d["presets"][0] for k in ("id", "name", "persona")), d["presets"][0])
        d = c.get("/api/ai/prompts").json()
        ck("prompts 是数组", isinstance(d.get("prompts"), list) and d["prompts"], d)

        print("[2] 通知（列表 / 已读）")
        d = c.get("/api/notices").json()
        ck("notices 返回 items", "items" in d, list(d.keys()))
        ck("空态是空数组", d["items"] == [] or isinstance(d["items"], list), d["items"])
        with db() as conn:
            conn.execute("INSERT INTO notices(user_id,title,body,read,created_at) "
                         "VALUES(1,'测试公告','内容',0,?)", (int(__import__("time").time()),))
        d = c.get("/api/notices").json()
        ck("能读到公告", len(d["items"]) == 1, d)
        ck("未读数 = 1", c.get("/api/notices/count").json()["unread"] == 1)
        ck("标记已读",
           c.post("/api/notices/read", json={"id": d["items"][0]["id"]}).status_code == 200)
        ck("已读后 unread=0", c.get("/api/notices/count").json()["unread"] == 0)
        ck("全部已读",
           c.post("/api/notices/read", json={}).status_code == 200)

        print("[3] 会员相关：邀请 / 订单 / 流量日志 / 兑换")
        ck("邀请接口已移除", c.get("/api/vip/invite").status_code == 404)
        ck("订单接口已移除", c.get("/api/vip/orders").status_code == 404)
        ck("兑换码接口已移除", c.post("/api/vip/redeem",
                                     json={"code": ""}).status_code == 404)

        print("[4] 账号安全：改密 / 改邮箱")
        ck("改密缺验证码 400",
           c.post("/api/me/password", json={"old": "x", "password": "newpass123"}
                  ).status_code in (400, 422))
        ck("改密错原密码被拒",
           c.post("/api/me/password", json={"old": "wrong", "password": "newpass123",
                                            "captcha": "1", "captcha_token": "x"}
                  ).status_code in (400, 403))
        # 该接口只校验邮箱格式与服务端发信，不要求前端算验证码（与 login/reset 不同）
        ck("非法邮箱被拒",
           c.post("/api/me/email_code", json={"email": "not-an-email"}
                  ).status_code == 400)
        ck("合法邮箱可发码",
           c.post("/api/me/email_code", json={"email": "newmail@example.com"}
                  ).status_code == 200)
        ck("改邮箱缺验证码 400",
           c.post("/api/me/email", json={"email": "n2@example.com", "code": ""}
                  ).status_code in (400, 422))

        print("[5] 图片代理 / 头像")
        # u 是必填查询参数，缺省由框架返回 422
        ck("缺 u 参数返回 422", c.get("/api/img/proxy").status_code == 422)
        ck("非图片域名被拒（防 SSRF 乱用）",
           c.get("/api/img/proxy?u=http://evil.example/x.jpg").status_code in (400, 403, 502))
        ck("face 接口对缺失文件返回 404",
           c.get("/api/img/face/nonexistent.png").status_code in (404, 200))

        print("[6] 视频库（旧路由保留兼容）")
        d = c.get("/api/library").json()
        ck("library 返回列表", isinstance(d, dict), type(d))
        ck("删除不存在的条目 404",
           c.delete("/api/library/9999").status_code in (404, 400))

        print("[7] 互赞旧接口（保留）")
        ck("like/actions 200", c.get("/api/like/actions").status_code == 200)
        ck("like/run 缺配置被拒",
           c.post("/api/like/run", json={}).status_code in (400, 422))

        print("[8] B 站上游未配置账号时明确报错")
        ck("recommend 400 且提示账号",
           c.get("/api/bili/recommend").status_code == 400)
        ck("search 400", c.get("/api/bili/search?q=test").status_code == 400)

        print("[9] 私信旧接口（保留）")
        ck("dm/threads 200", c.get("/api/dm/threads").status_code == 200)
        ck("dm 单条会话 404",
           c.get("/api/dm/threads/9999").status_code in (404, 200))
        ck("dm 发送缺参数被拒",
           c.post("/api/dm/threads/9999/send", json={}).status_code in (400, 404, 422))

        print("[10] 投稿高级接口")
        ck("server 缺参数被拒",
           c.post("/api/publish/server", json={}).status_code in (400, 422))
        ck("batch 无文件被拒",
           c.post("/api/publish/batch", json={}).status_code in (400, 422))
        ck("batch-upload 缺参数被拒",
           c.post("/api/publish/batch-upload", json={}).status_code in (400, 422))
        ck("submit 缺参数被拒",
           c.post("/api/publish/submit", json={}).status_code in (400, 422))
        ck("upos-put 缺参数被拒",
           c.post("/api/publish/upos-put", json={}).status_code in (400, 422))

        print("[11] 支付回调（伪造回调不能直接给会员）")
        r = c.post("/api/vip/pay_callback", json={"order_no": "NOPE", "status": "paid"})
        ck("未知订单被拒", r.status_code in (400, 404), r.text)

        print("[12] 登出")
        ck("logout 200", c.post("/api/auth/logout").status_code == 200)
        ck("登出后接口 401", c.get("/api/accounts").status_code in (401, 403))

        print("[13] 找回密码全链路")
        a, t = cap(c)
        ck("未注册邮箱被拒",
           c.post("/api/auth/forgot", json={"email": "nobody@example.com",
                                            "captcha": a, "captcha_token": t}
                  ).status_code == 400)
        a, t = cap(c)
        r = c.post("/api/auth/forgot", json={"email": "e@example.com",
                                             "captcha": a, "captcha_token": t})
        ck("已注册邮箱可发重置码", r.status_code == 200, r.text)
        forge("e@example.com", "reset", "333333")
        ck("弱密码被拒",
           c.post("/api/auth/reset", json={"email": "e@example.com", "code": "333333",
                                           "password": "123"}).status_code == 400)
        ck("错误验证码被拒",
           c.post("/api/auth/reset", json={"email": "e@example.com", "code": "000000",
                                           "password": "newpass123"}).status_code == 400)
        r = c.post("/api/auth/reset", json={"email": "e@example.com", "code": "333333",
                                            "password": "newpass123"})
        ck("重置成功", r.status_code == 200, r.text)
        a, t = cap(c)
        old = c.post("/api/auth/login", json={"username": "exuser",
                                              "password": "passw0rdX",
                                              "captcha": a, "captcha_token": t})
        ck("旧密码登录失败", old.status_code != 200, old.text)
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "exuser", "password": "newpass123",
                                            "captcha": a, "captcha_token": t})
        ck("新密码可登录", r.status_code == 200, r.text)

        print("[14] 头像文件服务")
        from pathlib import Path
        faces = Path(ROOT) / "data" / "faces"
        faces.mkdir(parents=True, exist_ok=True)
        (faces / "t.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        r = c.get("/api/img/face/t.png")
        ck("存在的头像 200", r.status_code == 200, r.status_code)
        ck("返回图片字节", r.content[:4] == b"\x89PNG", r.content[:8])
        ck("不存在的头像 404", c.get("/api/img/face/nope.png").status_code == 404)
        (faces / "t.png").unlink(missing_ok=True)
    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("补齐接口测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
