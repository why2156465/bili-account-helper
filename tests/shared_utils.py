"""公共工具去重检查：页面不应重复定义 common.js 已提供的函数。

为什么：同名的 `esc` / `fmtN` / `maskMid` / `accName` / `accFace` / `accMid`
在 10 个页面里各写了一遍。一旦其中一份写错（转义漏了引号、万/亿算错），
就只有那个页面出问题，很难发现。

规则：模板内联脚本里不得再定义这些名字；要用就走 BP.*。

运行：python tests/shared_utils.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "app", "templates")
COMMON_JS = os.path.join(ROOT, "app", "static", "js", "common.js")

SCRIPT = re.compile(r"<script[^>]*>([\s\S]*?)</script>", re.I)


def exported_from_common() -> set[str]:
    """从 common.js 的 w.BP = {...} 里读出实际导出的名字。"""
    src = open(COMMON_JS, encoding="utf-8").read()
    m = re.search(r"w\.BP\s*=\s*\{([^}]*)\}", src, re.S)
    if not m:
        return set()
    body = m.group(1)
    names = set()
    for piece in body.split(","):
        piece = piece.strip()
        if not piece:
            continue
        # 形如 `api` 或 `get token()` 或 `load`
        name = re.match(r"([A-Za-z_$][\w$]*)", piece)
        if name:
            names.add(name.group(1))
    return names


def main() -> int:
    common = exported_from_common()
    print("common.js 导出:", len(common), "个")
    print("  ", ", ".join(sorted(common)))
    print()

    # 只检查"纯工具"类，状态/异步类（api/toast/bindCaptcha）重复定义无伤但也没必要
    watch = common - {"state"}

    offenders: list[tuple[str, str]] = []
    for dp, _dirs, names in os.walk(TPL):
        for nm in sorted(names):
            if not nm.endswith(".html"):
                continue
            rel = os.path.relpath(os.path.join(dp, nm), TPL)
            src = open(os.path.join(dp, nm), encoding="utf-8").read()
            code = "\n".join(SCRIPT.findall(src))
            # 顶层函数声明（缩进 0）才算重复实现；行内嵌套的同名变量不算
            decls = re.findall(r"^(?:function\s+([A-Za-z_$][\w$]*)\s*\(|"
                               r"(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*function)",
                               code, re.M)
            for a, b in decls:
                name = a or b
                if name in watch:
                    offenders.append((rel, name))

    if offenders:
        print("以下页面重复实现了 common.js 已提供的工具（应改用 BP.*）：")
        by_file: dict[str, list[str]] = {}
        for f, n in offenders:
            by_file.setdefault(f, []).append(n)
        for f, ns in sorted(by_file.items()):
            print("  ✗ %-22s %s" % (f, ", ".join(sorted(set(ns)))))
        print()
        print("共 %d 处" % len(offenders))
        return 1

    print("没有页面重复实现公共工具")
    return 0


if __name__ == "__main__":
    sys.exit(main())
