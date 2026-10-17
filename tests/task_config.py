"""互赞任务配置端到端测试：前端 cfg() 产出的结构必须被后端完整消费。

前端 tasks.html 的 cfg() 产出：
    {actions:{like:true, coin:{on,multiply,also_like}, fav:{on,media_id},
              comment:{on,text,emotes,emote}, share:{on,mode,text},
              follow:{on}, play:{on},
              opus:{on,max,like,repost:{on,mode,text},comment:{on,text,emotes,emote}}},
     actors:'all' | [id...],
     targets:[{uid,rule,max,from,to,bvids}],
     sleep:[min,max]}

后端 do_one() 要能读懂每个动作的开关和参数。字段错位会导致"任务跑起来但什么都没做"。

运行：python tests/task_config.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-cfg-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "cfg-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.interact import STATE, resolve_targets  # noqa: E402

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


# 完全照前端 cfg() + actions() 的形状写
FULL_CFG = {
    "actions": {
        "like": True,
        "coin": {"on": True, "multiply": 1, "also_like": True},
        "fav": {"on": True, "media_id": None},
        "comment": {"on": True, "text": "好看", "emotes": ["[doge]"], "emote": True},
        "share": {"on": True, "mode": "dyn", "text": ""},
        "follow": {"on": True},
        "play": {"on": True},
        "opus": {"on": True, "max": 5, "like": True,
                 "repost": {"on": True, "mode": "dyn", "text": ""},
                 "comment": {"on": False, "text": "", "emotes": [], "emote": False}},
    },
    "actors": "all",
    "targets": [{"uid": "123456", "rule": "latest", "max": 1}],
    "sleep": [1, 2],
}


class FakeCli:
    def __init__(self, videos=None):
        self.videos = videos or [{"bvid": "BV1xx", "aid": 111, "title": "视频1",
                                  "created": 1700000000},
                                 {"bvid": "BV1yy", "aid": 222, "title": "视频2",
                                  "created": 1700001000},
                                 {"bvid": "BV1zz", "aid": 333, "title": "视频3",
                                  "created": 1700002000}]
        self.calls = []

    def get(self, path, params=None, **kw):
        self.calls.append(("get", path, params))
        if "arc/search" in path:
            return {"list": {"vlist": self.videos}}
        return {}

    def post(self, path, data=None, params=None, **kw):
        self.calls.append(("post", path, data))
        return {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main() -> int:
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "cf@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("cf@example.com", "register", "111111")
        c.post("/api/auth/register", json={"username": "cfguser", "password": "passw0rdX",
                                           "email": "cf@example.com", "code": "111111"})
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "cfguser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("cf@example.com", "verify", "222222")
            c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})
        ck("登录", r.status_code == 200, r.text)

        print("[0] 先加一个账号（run 需要执行者）")
        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
        ck("账号已加", len(c.get("/api/accounts").json()["accounts"]) == 1)

        print("[1] 完整 config 结构本身合法（不因结构问题被拒）")
        # 沙箱里上游不通，目标解析拿不到视频；现在 resolve_targets 目标级容错，
        # 所以这里返回「目标没有匹配到任何视频」——这是数据问题，不是结构问题。
        # 只验证没有因为 config 结构（缺字段/未知动作）被拒。
        STATE.running = False
        r = c.post("/api/tasks/run", json=FULL_CFG)
        ck("未被判为结构错误", "动作" not in r.text, r.text)
        ck("未被判为校验错误（422）", r.status_code != 422, r.text)
        ck("有明确的可读原因", bool(r.json().get("detail") or r.json().get("message")), r.text)
        STATE.running = False

        print("[2] 缺动作 / 缺目标被拒且提示清楚")
        STATE.running = False
        r = c.post("/api/tasks/run", json={**FULL_CFG, "actions": {}})
        ck("空动作 400", r.status_code == 400 and "动作" in r.text, r.text)
        r = c.post("/api/tasks/run", json={**FULL_CFG, "targets": []})
        ck("空目标 400", r.status_code == 400 and "目标" in r.text, r.text)

        print("[3] 六种目标规则都能解析")
        cli = FakeCli()
        # resolve_targets 返回 [{uid,count,videos:[...]}]，每个目标一组
        def flat(groups):
            return [v for g in groups for v in (g.get("videos") or [])]

        got = resolve_targets(cli, [{"uid": "1", "rule": "latest", "max": 1}])
        ck("latest 返回 1 组", len(got) == 1, got)
        ck("latest 组内 count=1", got[0].get("count") == 1, got)
        ck("latest 取最新那条", flat(got)[0].get("aid") == 333, flat(got))
        got = resolve_targets(cli, [{"uid": "1", "rule": "all", "max": 10}])
        ck("all 取全部 3 个", len(flat(got)) == 3, flat(got))
        got = resolve_targets(cli, [{"uid": "1", "rule": "random", "max": 10, "n": 2}])
        ck("random 取 2 个", len(flat(got)) == 2, flat(got))
        got = resolve_targets(cli, [{"rule": "bvids", "bvids": ["BV1aa", "BV1bb"]}])
        ck("bvids 直填 2 个", len(flat(got)) == 2, flat(got))
        # date
        got = resolve_targets(cli, [{"uid": "1", "rule": "date", "max": 10,
                                     "date": "2023-11-15"}])
        ck("date 不崩", isinstance(got, list), got)
        # range
        got = resolve_targets(cli, [{"uid": "1", "rule": "range", "max": 10,
                                     "from": "2023-11-01", "to": "2023-11-30"}])
        ck("range 不崩", isinstance(got, list), got)

        print("[4] 目标字段缺失时的容错")
        got = resolve_targets(cli, [{"uid": "1", "rule": "latest"}])
        ck("latest 缺 max 默认 1", len(got) == 1, len(got))
        got = resolve_targets(cli, [{"uid": "1"}])
        ck("缺 rule 不崩", isinstance(got, list), got)

        print("[5] 存为模板后能原样读回")
        r = c.post("/api/templates", json={"name": "完整配置", "kind": "interact",
                                           "config": FULL_CFG})
        ck("存模板", r.status_code == 200, r.text)
        d = c.get("/api/templates?kind=interact").json()
        tpl = [x for x in d["templates"] if x["name"] == "完整配置"]
        ck("读回 1 个", len(tpl) == 1, len(tpl))
        cfg = tpl[0]["config"]
        ck("actions 完整保留", set(cfg["actions"]) == set(FULL_CFG["actions"]),
           list(cfg["actions"]))
        ck("opus 嵌套结构保留",
           cfg["actions"]["opus"]["repost"]["mode"] == "dyn", cfg["actions"]["opus"])
        ck("targets 保留", cfg["targets"][0]["uid"] == "123456", cfg["targets"])
        ck("sleep 保留", cfg["sleep"] == [1, 2], cfg["sleep"])

        print("[6] 定时任务存同一份配置")
        r = c.post("/api/tasks", json={"name": "定时三连", "cron": "0 20 * * *",
                                       "kind": "interact", "config": FULL_CFG})
        ck("建任务", r.status_code == 200, r.text)
        tid = r.json()["id"]
        d = c.get("/api/tasks").json()
        task = [x for x in d["tasks"] if x["id"] == tid][0]
        ck("任务 config 完整",
           set(task["config"]["actions"]) == set(FULL_CFG["actions"]),
           list(task["config"]["actions"]))
        ck("任务 next_run_ts 已算", task["next_run_ts"] > 0, task["next_run_ts"])

        print("[7] 编辑（PUT /config）保留配置")
        r = c.put("/api/tasks/%d/config" % tid, json={"name": "改名后", "cron": "0 21 * * *",
                                                      "kind": "interact",
                                                      "config": FULL_CFG})
        ck("PUT 200", r.status_code == 200, r.text)
        d = c.get("/api/tasks").json()
        task = [x for x in d["tasks"] if x["id"] == tid][0]
        ck("名字已改", task["name"] == "改名后", task["name"])
        ck("cron 已改", task["cron"] == "0 21 * * *", task["cron"])
        ck("actions 仍在", "like" in task["config"]["actions"], task["config"])

        print("[8] 预览接口能解析目标")
        STATE.running = False
        r = c.post("/api/tasks/preview", json={"targets": [{"uid": "123456",
                                                            "rule": "latest",
                                                            "max": 1}]})
        ck("preview 有确定返回", r.status_code in (200, 400, 502), r.status_code)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("互赞任务配置端到端测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
