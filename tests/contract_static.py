"""静态契约检查：模板里每个 BP.api() 调用都必须能解析到已注册路由。

教训来源：/web 页曾"测试通过"——pages.py 只验证了页面能渲染和几个关键词，
但那一页 15 个 /api/web/* 调用在后端根本不存在。渲染成功 ≠ 功能可用。

这个脚本把模板里所有 api 调用抽出来，按方法+路径去匹配 FastAPI 路由表，
匹配不上就报出来。运行：python tests/contract_static.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("SECRET_KEY", "static-check")
os.environ.setdefault("DB_PATH", os.path.join(ROOT, "data", "static_check.db"))

from app.main import app  # noqa: E402

TPL = os.path.join(ROOT, "app", "templates")

# 页面里可能出现的调用写法：
#   BP.api('/api/xxx')
#   api('/api/xxx',{method:'POST',...})
#   BP.api('/api/xxx/'+id+'/y')
CALL = re.compile(r"""(?:BP\.api|(?<![\w.])api)\(\s*(['"`])(/api/[^'"`]*)\1""")
METHOD = re.compile(r"""method\s*:\s*(['"])([A-Za-z]+)\1""")

FAILS: list[str] = []


def route_map():
    """{路径模板: {方法集合}}"""
    out: dict[str, set[str]] = {}
    for path, ops in app.openapi()["paths"].items():
        if not path.startswith("/api"):
            continue
        out[path] = {m.upper() for m in ops}
    return out


def to_regex(path: str) -> re.Pattern:
    """把 '/api/x/{id}/y' 变成可匹配具体 URL 的正则。"""
    parts = re.split(r"(\{[^}]+\})", path)
    out = ""
    for p in parts:
        if p.startswith("{") and p.endswith("}"):
            out += r"[^/]+"
        else:
            out += re.escape(p)
    return re.compile("^" + out + "$")


def concrete(call: str) -> str:
    """把前端拼接的调用串规整成可匹配的路径。

    '/api/publish/files/'+id+'/raw'   -> /api/publish/files/X/raw
    '/api/tasks?limit=10'             -> /api/tasks
    '/api/ai/quota?_='+Date.now()     -> /api/ai/quota
    """
    p = call.split("?")[0]
    p = re.sub(r"['\"`]\s*\+\s*[\w.$()]+\s*\+\s*['\"`]", "X", p)
    p = re.sub(r"['\"`]\s*\+\s*[\w.$()]+$", "X", p)
    p = re.sub(r"^(.*?)\s*\+\s*[\w.$()]+\s*\+\s*", r"\1", p)
    return p


def scan():
    routes = route_map()
    matchers = [(p, to_regex(p), ms) for p, ms in routes.items()]
    checked = 0
    files = []
    for dirpath, _dirs, names in os.walk(TPL):
        for nm in names:
            if nm.endswith(".html"):
                files.append(os.path.join(dirpath, nm))
    for full in sorted(files):
        fn = os.path.relpath(full, TPL)
        src = open(full, encoding="utf-8").read()
        for m in CALL.finditer(src):
            call = m.group(2)
            # 只在同一个调用表达式内找 method，不能跨到下一行调用
            tail = src[m.end():m.end() + 220]
            close = tail.find(")")
            tail = tail if close < 0 else tail[:close]
            mm = METHOD.search(tail)
            method = mm.group(2).upper() if mm else "GET"
            path = concrete(call)
            checked += 1

            # 路径里带 ${} 或未闭合拼接时只做前缀校验
            dynamic = "X" in path or "${" in path
            hit = [p for p, rx, ms in matchers
                   if (rx.match(path) if not dynamic else path.startswith(rx.pattern[:0]) or
                       rx.match(path))]
            if not hit:
                # 退一步：前缀匹配（前端拼字符串时无法静态还原完整路径）
                hit = [p for p, rx, ms in matchers
                       if path.rstrip("/").startswith(p.split("{")[0].rstrip("/"))
                       and p.split("{")[0].count("/") >= 2]
            if not hit:
                FAILS.append("%s: %s %s -> 没有对应路由" % (fn, method, call))
                continue
            if not any(method in routes[p] for p in hit):
                allowed = sorted({x for p in hit for x in routes[p]})
                FAILS.append("%s: %s %s -> 路由只接受 %s" % (fn, method, call, allowed))
    return checked


def main() -> int:
    print("扫描模板：", TPL)
    n = scan()
    print("共检查 %d 个 api 调用" % n)
    print()
    if FAILS:
        print("发现 %d 处问题：" % len(FAILS))
        for f in FAILS:
            print("  ✗", f)
        return 1
    print("模板调用与后端路由全部匹配")
    return 0


if __name__ == "__main__":
    sys.exit(main())
