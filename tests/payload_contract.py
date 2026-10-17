"""写操作字段对齐检查：前端发往 POST/PUT 的字段必须被后端模型接收。

教训来源（三个都是这一轮修的）：
  · /api/vip/order   前端发 {kind, key}，后端读 body.id     → 下单永远 400
  · 订单落库时写 body.id（空串），_grant 找不到商品         → 支付 500
  · 前端读 r.pay_url，后端只返回 url                        → 支付链接 undefined

这三处都属于"字段名对不上"，静态字段扫描抓不到（key 和 id 都是合法字段），
只能靠"前端发了什么" vs "后端收什么"直接对账。

做法：
  1. 扫后端：每个写接口用了哪个 Pydantic 模型、模型有哪些字段（dict 类型则跳过）；
  2. 扫前端：每个 BP.api(url,{method:'POST', body:JSON.stringify({...})}) 的顶层 key；
  3. 前端发了但模型没有的字段 → 报错（这才是会静默失效的那类）。

运行：python tests/payload_contract.py
"""
from __future__ import annotations

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "app", "templates")
ROUTERS = os.path.join(ROOT, "app", "routers")


def backend_models() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in glob.glob(os.path.join(ROUTERS, "*.py")):
        src = open(f, encoding="utf-8").read()
        for m in re.finditer(r"class\s+(\w+)\s*\(BaseModel\):(.*?)(?=\nclass |\ndef |\Z)",
                             src, re.S):
            out[m.group(1)] = re.findall(r"^\s{4}(\w+)\s*:", m.group(2), re.M)
    return out


def write_routes(models: dict[str, list[str]]) -> dict[str, tuple[str, list[str] | None]]:
    """{路径: (方法, 模型字段列表或 None 表示收 dict / 无 body)}"""
    out: dict[str, tuple[str, list[str] | None]] = {}
    for f in glob.glob(os.path.join(ROUTERS, "*.py")):
        src = open(f, encoding="utf-8").read()
        prefix = ""
        pm = re.search(r'APIRouter\(prefix="([^"]*)"', src)
        if pm:
            prefix = pm.group(1)
        for m in re.finditer(
                r'@router\.(post|put|patch)\("([^"]*)"[^)]*\)\s*\ndef\s+\w+\(([^)]*)\)',
                src, re.S):
            meth, path, args = m.group(1).upper(), m.group(2), m.group(3)
            full = (prefix + path).replace("//", "/")
            mod = re.search(r"\bbody\s*:\s*(\w+)", args)
            if mod and mod.group(1) in models:
                out[full] = (meth, models[mod.group(1)])
            else:
                out[full] = (meth, None)      # 收 dict / 无 body，跳过校验
    return out


def norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.rstrip("/"))


def frontend_payloads() -> list[tuple[str, str, str, list[str]]]:
    """[(文件, 方法, 路径, 顶层字段)]"""
    out = []
    call = re.compile(r"BP\.api\(\s*([^,]+?)\s*,\s*\{", re.S)
    for nm in sorted(os.listdir(TPL)):
        if not nm.endswith(".html"):
            continue
        src = open(os.path.join(TPL, nm), encoding="utf-8").read()
        for cm in call.finditer(src):
            url_expr = cm.group(1)
            # 只处理字面量路径（拼接的先跳过）
            lm = re.match(r"""^['"`](/api/[^'"`?]+)""", url_expr.strip())
            if not lm:
                continue
            path = lm.group(1)
            # 取这个调用的完整参数块：从 BP.api( 的左括号开始做括号配平
            i = src.rfind("(", 0, cm.end())
            depth = 0
            j = i
            while j < len(src):
                ch = src[j]
                if ch in "({[":
                    depth += 1
                elif ch in ")}]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            opts = src[i:j + 1]
            meth = re.search(r"method\s*:\s*['\"](\w+)", opts)
            if not meth or meth.group(1).upper() == "GET":
                continue
            bm = re.search(r"body\s*:\s*JSON\.stringify\(\s*\{", opts)
            fields: list[str] = []
            if bm:
                # 找到 JSON.stringify( 之后的对象字面量
                k = opts.find("{", bm.start() + bm.group(0).__len__() - 1)
                d = 0
                pp = k
                while pp < len(opts):
                    if opts[pp] in "({[":
                        d += 1
                    elif opts[pp] in ")}]":
                        d -= 1
                        if d == 0:
                            break
                    pp += 1
                obj = opts[k + 1:pp]
                # 顶层 key：按逗号切，忽略嵌套
                depth2 = 0
                cur = ""
                parts = []
                for ch in obj:
                    if ch in "{[(":
                        depth2 += 1
                    elif ch in "}])":
                        depth2 -= 1
                    if ch == "," and depth2 == 0:
                        parts.append(cur)
                        cur = ""
                    else:
                        cur += ch
                parts.append(cur)
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    # 两种写法：`name: value` 和 ES6 简写 `{kind, key}`
                    km = re.match(r"['\"]?(\w+)['\"]?\s*:", part)
                    if km:
                        fields.append(km.group(1))
                    elif re.fullmatch(r"[A-Za-z_$][\w$]*", part):
                        fields.append(part)
            out.append((nm, meth.group(1).upper(), path, fields))
    return out


def main() -> int:
    models = backend_models()
    routes = {norm(k): v for k, v in write_routes(models).items()}
    payloads = frontend_payloads()
    print("后端请求体模型:", len(models), "| 写接口:", len(routes))
    print("前端写调用（字面量路径）:", len(payloads))
    print()

    problems: list[str] = []
    checked = 0
    for fn, meth, path, fields in payloads:
        key = norm(path)
        if key not in routes:
            continue                     # 路径本身由 contract_static 负责
        rmeth, mfields = routes[key]
        if mfields is None:
            continue                     # 收 dict，无强约束
        checked += 1
        extra = [f for f in fields if f not in mfields]
        if extra:
            problems.append("%s: %s %s 发了后端模型没有的字段 %s（模型只有 %s）"
                            % (fn, meth, path, extra, mfields))

    print("可比对的调用:", checked)
    print()
    if problems:
        print("发现 %d 处前后端字段不一致：" % len(problems))
        for p in problems:
            print("  ✗ " + p)
        return 1

    print("前端发送的字段全部被后端模型接收")
    return 0


if __name__ == "__main__":
    sys.exit(main())
