"""前端工具函数测试：用 node + vm 在无浏览器环境下验证 common.js 的真实行为。

为什么需要：common.js 是纯前端，Python 测试完全覆盖不到。
`esc` 转义、`fmtN` 万/亿缩写、`maskMid` 打码这些一旦错，页面显示就是错的。

运行：python tests/common_js.py（需要 node）
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common_js.js")
TARGET = os.path.join(ROOT, "app", "static", "js", "common.js")


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("跳过：本机没有 node，无法验证纯前端逻辑")
        return 0
    r = subprocess.run([node, SCRIPT, TARGET], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out.rstrip())
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())