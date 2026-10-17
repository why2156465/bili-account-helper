"""样式覆盖检查：模板里用到的每个 class 都必须能在某个 CSS 里找到定义。

教训来源：/web、/vip/compare、/trend、/invite 这些页面大量使用 wb-* / cmp-* / tr-* / iv-*
类名，但线上 app.css 里根本没有这些类 —— 页面"能渲染"却是一堆无样式裸 HTML。
tests/pages.py 只验证关键词，发现不了这个。

规则：
  · 类名出现在 app.css / client.css  → 来自原站，OK
  · 类名出现在 custom.css           → 我们自己补的，OK
  · 两个地方都没有                  → 报错

运行：python tests/style_coverage.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "app", "templates")
CSS_DIR = os.path.join(ROOT, "app", "static", "css")

# 不是 CSS 类名的伪 token（模板语法、JS 变量）
IGNORE = {
    "", "if", "endif", "else", "for", "endfor", "nav==", "txt",
}


def classes_in_css(path: str) -> set[str]:
    src = open(path, encoding="utf-8", errors="replace").read()
    # 去掉注释，避免把注释里的文字当类名
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return set(re.findall(r"\.([a-zA-Z][\w-]*)", src))


def classes_in_templates() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for dp, _dirs, names in os.walk(TPL):
        for nm in names:
            if not nm.endswith(".html"):
                continue
            full = os.path.join(dp, nm)
            src = open(full, encoding="utf-8").read()
            got = set()
            for m in re.findall(r'class="([^"]*)"', src):
                for token in m.split():
                    token = token.split("'")[0].split("{")[0].strip()
                    # 去掉 onclick/JS 拼接残留
                    if not re.fullmatch(r"[a-zA-Z][\w-]*", token or ""):
                        continue
                    got.add(token)
            if got:
                used[os.path.relpath(full, TPL)] = got
    return used


def main() -> int:
    known: set[str] = set()
    for nm in ("app.css", "client.css", "custom.css"):
        p = os.path.join(CSS_DIR, nm)
        if os.path.exists(p):
            known |= classes_in_css(p)
    print("CSS 里已定义的类名:", len(known))

    used = classes_in_templates()
    total = sum(len(v) for v in used.values())
    print("模板里用到的类名:", total, "（分布在 %d 个文件）" % len(used))
    print()

    bad: list[tuple[str, str]] = []
    for fn, got in sorted(used.items()):
        for c in sorted(got):
            if c in IGNORE or c in known:
                continue
            bad.append((fn, c))

    if bad:
        print("以下 %d 个类名没有任何样式定义（页面会裸奔）：" % len(bad))
        by_file: dict[str, list[str]] = {}
        for fn, c in bad:
            by_file.setdefault(fn, []).append(c)
        for fn, cs in by_file.items():
            print("  ✗ %-22s %s" % (fn, ", ".join(cs)))
        return 1

    print("模板用到的所有类名都有样式定义")
    return 0


if __name__ == "__main__":
    sys.exit(main())
