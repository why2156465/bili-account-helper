"""模板内联 JS 语法检查：每个页面的 <script> 块都必须能被 JS 引擎解析。

为什么需要：24 个页面各写了一段内联脚本，但它们从来只在"真浏览器打开"时才解析。
Python 测试看不到语法错误；`tests/pages.py` 只验证 HTML 里有几个关键词。
一个多余的括号就能让整页的交互全废，而所有测试照样全绿。

做法：把每个模板里所有非 JSON 的 <script> 抽出来，交给 node --check 解析。
模板里的 Jinja 变量先在可控范围内替换成 JS 合法字面量。

运行：python tests/inline_js.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "app", "templates")

SCRIPT = re.compile(r"<script([^>]*)>([\s\S]*?)</script>", re.I)


def extract_jinja_exprs(src: str) -> list[str]:
    return re.findall(r"\{\{([^{}]*)\}\}", src)


def stub_jinja(body: str) -> str:
    """把 Jinja 语法替换成 JS 合法值，只为让语法检查能跑。

    {{ user.username }} → "JINJA"
    {% if x %} ... {% endif %}  → 保留中间语句（去掉标签行）
    """
    # 字符串里的 {{ }} 先处理
    body = re.sub(r"\{\{\s*[^{}]+\s*\}\}", '"JINJA"', body)
    # 独立的 Jinja 控制行整行去掉（if/for/endif/endfor/else/set）
    body = re.sub(r"^[ \t]*\{%.*?%\}[ \t]*$", "", body, flags=re.M)
    # 行内残留的 {%%}
    body = re.sub(r"\{%.*?%\}", "", body)
    return body


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("跳过：本机没有 node，无法做 JS 语法检查")
        return 0

    files = []
    for dp, _dirs, names in os.walk(TPL):
        for nm in names:
            if nm.endswith(".html"):
                files.append(os.path.join(dp, nm))

    tmpdir = tempfile.mkdtemp(prefix="bp-js-")
    checked = 0
    failures: list[tuple[str, str]] = []

    for full in sorted(files):
        rel = os.path.relpath(full, TPL)
        src = open(full, encoding="utf-8").read()
        blocks = SCRIPT.findall(src)
        for idx, (attrs, body) in enumerate(blocks):
            if "src=" in attrs.lower():
                continue                    # 外链脚本不检查
            if 'type="application/json"' in attrs.lower() or "type='application/json'" in attrs.lower():
                continue
            code = stub_jinja(body)
            if not code.strip():
                continue
            out = os.path.join(tmpdir, "%s_%d.js" % (re.sub(r"\W", "_", rel), idx))
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(code)
            r = subprocess.run([node, "--check", out], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            checked += 1
            if r.returncode != 0:
                err = (r.stderr or "").strip().splitlines()
                # 只留最有用的那几行
                msg = "\n".join(err[:6])
                failures.append(("%s <script#%d>" % (rel, idx), msg))

    print("模板文件:", len(files))
    print("检查的内联 <script> 块:", checked)
    print()

    if failures:
        print("发现 %d 处语法错误：" % len(failures))
        for name, msg in failures:
            print("  ✗ %s" % name)
            for line in msg.splitlines():
                print("      " + line)
        return 1

    print("所有内联脚本语法检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
