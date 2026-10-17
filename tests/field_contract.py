"""字段名交叉检查：前端读取的 `x.field` 必须在后端真的有这个字段。

教训来源：accounts.html 写的是 `a.name`，但 /api/accounts 返回的是 `uname`。
后果是「只要没设别名，账号页昵称就是空白」——732 项测试全绿，页面也不报错。

做法（两层，尽量少误报）：
  1. 动态：起测试客户端真调主要 GET 接口，收集响应里出现过的所有 key；
  2. 静态：扫后端源码里 `"field":` 形式的字典字面量 key，补上动态调不到的
     （需管理员 / 需上游 / 需登录态的分支）。
  3. 扫模板里 `ident.field` 的读取，凡两层都没出现的才报。

白名单：DOM 属性、JS 内置、以及"前端自己构造的请求体字段"。

运行：python tests/field_contract.py
"""
from __future__ import annotations

import glob
import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="bp-field-")
os.environ["DB_PATH"] = os.path.join(TMP, "app.db")
os.environ["SECRET_KEY"] = "field-test"
os.environ["WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings as st  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402

# 第三方 SDK 与 JS 内置，不属于后端字段契约
NON_DATA = set()

TPL = os.path.join(ROOT, "app", "templates")

# 只有这些前缀的变量才当作"后端数据对象"
DATA_PREFIX = {
    "a", "acc", "d", "t", "r", "s", "u", "p", "v", "o", "it", "row",
    "item", "rec", "obj", "res", "data", "acct", "task", "tpl", "usr",
    "prof", "info", "job", "run", "series", "msg", "sess", "peer",
}


# DOM 属性 / JS 内置方法：肯定不是后端字段

# 第三方 SDK 的返回值字段，不属于本项目后端契约
NON_DATA_EXTRA = ({
    "captcha_output", "gen_time", "lot_number", "pass_token",   # 极验 geetest v4
    "onload", "onerror", "src",
})

NON_DATA = {
    "length", "style", "classList", "dataset", "innerHTML", "textContent", "value",
    "checked", "disabled", "children", "parentNode", "parentElement", "scrollTop",
    "scrollHeight", "clientHeight", "clientWidth", "scrollWidth", "offsetWidth",
    "onclick", "onchange", "oninput", "onkeydown", "onerror", "href", "src", "id",
    "className", "title", "alt", "files", "target", "method", "headers", "body",
    "then", "catch", "forEach", "map", "filter", "push", "join", "split", "slice",
    "indexOf", "toFixed", "replace", "trim", "toUpperCase", "toLowerCase",
    "getElementById", "querySelector", "querySelectorAll", "focus", "blur",
    "click", "submit", "reset", "preventDefault", "stopPropagation",
    "toString", "valueOf", "hasOwnProperty", "call", "apply", "bind",
    "type", "key", "size", "width", "height", "top", "left", "right",
    "bottom", "position", "display", "color", "background", "border", "margin",
    "padding", "font", "opacity", "zIndex", "transform", "transition", "cursor",
    "getPropertyValue", "getAttribute", "setAttribute", "removeAttribute",
    "insertBefore", "appendChild", "removeChild", "addEventListener",
    "removeEventListener", "contains", "closest", "matches", "previousSibling",
    "nextSibling", "firstChild", "lastChild", "nodeName", "nodeType", "tagName",
    "json", "remove", "includes", "shift", "unshift", "splice", "sort", "reverse",
    "concat", "every", "some", "reduce", "find", "findIndex", "entries", "assign",
    "getDate", "getHours", "getMinutes", "getMonth", "getFullYear", "getSeconds",
    "toDateString", "toISOString", "toLocaleString", "toLocaleDateString",
    "toLocaleTimeString", "getTime", "setTimeout", "clearTimeout", "reload",
}

NON_DATA |= NON_DATA_EXTRA

# 任务配置（config JSON）里的字段，前端读的是自己存的结构，不来自响应
CONFIG_FIELDS = {"from", "to", "n", "on", "max", "rule", "uid", "bvids", "opus", "date"}


def collect_keys(obj, out: set[str]) -> None:
    if isinstance(obj, dict):
        for k, val in obj.items():
            out.add(k)
            collect_keys(val, out)
    elif isinstance(obj, list):
        for val in obj[:8]:
            collect_keys(val, out)


def static_keys() -> set[str]:
    """从后端源码里挖出所有字典字面量的字符串 key。"""
    keys: set[str] = set()
    for f in glob.glob(os.path.join(ROOT, "app", "**", "*.py"), recursive=True):
        src = open(f, encoding="utf-8").read()
        keys |= set(re.findall(r'"([a-zA-Z_][\w]*)"\s*:', src))
        keys |= set(re.findall(r"'([a-zA-Z_][\w]*)'\s*:", src))
        # dict(a=1, b=2) 形式
        for m in re.finditer(r"\bdict\(([^)]*)\)", src):
            keys |= set(re.findall(r"([a-zA-Z_]\w*)\s*=", m.group(1)))
        # out["field"] = ... 形式
        keys |= set(re.findall(r'\w+\["([a-zA-Z_]\w*)"\]\s*=', src))
        keys |= set(re.findall(r"\w+\['([a-zA-Z_]\w*)'\]\s*=", src))
    return keys


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


GETS = [
    "/api/accounts", "/api/tasks", "/api/templates?kind=interact", "/api/logs",
    "/api/dashboard", "/api/trend?days=7", "/api/vip/info", "/api/vip/compare",
    "/api/vip/orders", "/api/vip/invite", "/api/vip/traffic-logs",
    "/api/proxy/list", "/api/notices", "/api/notices/count", "/api/me/settings",
    "/api/me/notice", "/api/ai/info", "/api/ai/history", "/api/ai/log",
    "/api/ai/profiles", "/api/ai/assets", "/api/ai/presets", "/api/ai/prompts",
    "/api/messages/accounts", "/api/messages/policy", "/api/messages/sessions?account_id=1",
    "/api/publish/files", "/api/publish/covers", "/api/publish/records",
    "/api/publish/sections", "/api/publish/opts", "/api/publish/sessions",
    "/api/automation", "/api/automation/policy", "/api/automation/ai/prompt",
    "/api/web/feed?account_id=1", "/api/web/space?account_id=1",
    "/api/web/history?account_id=1", "/api/web/likes?account_id=1",
    "/api/web/follows?account_id=1", "/api/web/mine?account_id=1",
]


def main() -> int:
    dyn: set[str] = set()
    with TestClient(app) as c:
        a, t = cap(c)
        c.post("/api/auth/send_code", json={"email": "f@example.com", "scene": "register",
                                            "captcha": a, "captcha_token": t})
        forge("f@example.com", "register", "111111")
        c.post("/api/auth/register", json={"username": "fielduser", "password": "passw0rdX",
                                           "email": "f@example.com", "code": "111111"})
        a, t = cap(c)
        r = c.post("/api/auth/login", json={"username": "fielduser", "password": "passw0rdX",
                                            "captcha": a, "captcha_token": t})
        if r.json().get("need_verify"):
            tk = r.json()["ticket"]
            a2, t2 = cap(c)
            c.post("/api/auth/login_verify/resend",
                   json={"ticket": tk, "captcha": a2, "captcha_token": t2})
            forge("f@example.com", "verify", "222222")
            c.post("/api/auth/login_verify", json={"ticket": tk, "code": "222222"})

        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok,fans,videos,coins,level,last_check) "
                "VALUES(1,'9001','主号','',?,'ok','qr',1,'',1,10,2,5.0,3,'2026-01-01 00:00')",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
            conn.execute(
                "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,created_at,"
                "next_run) VALUES(1,'任务','interact','0 9 * * *','{}',1,1,0)")
            conn.execute("INSERT INTO notices(user_id,title,body,read,created_at) "
                         "VALUES(1,'t','b',0,1)")

        for p in GETS:
            try:
                rr = c.get(p)
                if rr.status_code == 200:
                    collect_keys(rr.json(), dyn)
            except Exception:
                pass

    stat = static_keys()
    known = dyn | stat
    print("动态收集字段:", len(dyn), "| 静态源码字段:", len(stat), "| 合计:", len(known))
    print()

    reads: dict[str, set[str]] = {}
    for dp, _dirs, names in os.walk(TPL):
        for nm in sorted(names):
            if not nm.endswith(".html"):
                continue
            rel = os.path.relpath(os.path.join(dp, nm), TPL)
            src = open(os.path.join(dp, nm), encoding="utf-8").read()
            code = "\n".join(re.findall(r"<script[^>]*>([\s\S]*?)</script>", src))
            got = set()
            for m in re.finditer(r"\b([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)", code):
                if m.group(1) in DATA_PREFIX:
                    got.add(m.group(2))
            if got:
                reads[rel] = got

    total = sum(len(v) for v in reads.values())
    print("模板里读取的数据字段:", total)

    suspects: list[tuple[str, str]] = []
    for rel, fields in sorted(reads.items()):
        for f in sorted(fields):
            if f in NON_DATA or f in CONFIG_FIELDS:
                continue
            if f not in known:
                suspects.append((rel, f))

    if suspects:
        print()
        print("以下 %d 个字段在接口响应和后端源码里都找不到：" % len(suspects))
        by_file: dict[str, list[str]] = {}
        for rel, f in suspects:
            by_file.setdefault(rel, []).append(f)
        for rel, fs in by_file.items():
            print("  ✗ %-22s %s" % (rel, ", ".join(fs)))
        return 1

    print()
    print("模板读取的数据字段全部有来源")
    return 0


if __name__ == "__main__":
    sys.exit(main())