"""跑全部测试套件，输出一张汇总表。

用法：
    python tests/run_all.py            # 全部
    python tests/run_all.py -v         # 顺便打印每个套件的完整输出
    python tests/run_all.py api worker # 只跑名字含 api 或 worker 的套件

退出码：全部通过为 0，有失败为 1。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# 单一结论型检查器：不数 PASS，只看退出码
STATIC = {
    "contract_static", "schema_check", "endpoint_coverage", "style_coverage",
    "inline_js", "shared_utils", "field_contract", "payload_contract",
}

# 顺序：先功能，再静态检查，最后跑得慢的
ORDER = [
    "contract", "interact", "scheduler", "publish", "messages", "ai_api",
    "web", "trend", "vip_compare", "vip_buy", "task_config", "target_edge",
    "worker_run", "worker_recovery", "state_machine", "action_failure",
    "concurrency", "security", "admin", "upload", "fills", "extra",
    "pages", "proxy_api", "common_js",
    "contract_static", "schema_check", "endpoint_coverage", "style_coverage",
    "inline_js", "shared_utils", "field_contract", "payload_contract",
]

PASS_RE = re.compile(r"^\s+PASS\s", re.M)
FAIL_RE = re.compile(r"^\s+FAIL\s|✗", re.M)


def discover() -> list[str]:
    have = {f[:-3] for f in os.listdir(TESTS)
            if f.endswith(".py") and f not in ("run_all.py",)}
    ordered = [n for n in ORDER if n in have]
    rest = sorted(have - set(ordered))
    return ordered + rest


def run(name: str, verbose: bool) -> tuple[str, int, int, int, float, str]:
    path = os.path.join(TESTS, name + ".py")
    t0 = time.time()
    r = subprocess.run([sys.executable, path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT)
    dt = time.time() - t0
    out = (r.stdout or "") + (r.stderr or "")
    if verbose:
        print("=" * 70)
        print("### " + name)
        print(out.rstrip())
        print()
    passed = len(PASS_RE.findall(out))
    failed = len(FAIL_RE.findall(out))
    if name in STATIC and r.returncode == 0:
        passed = 1
        failed = 0
    if r.returncode != 0 and failed == 0:
        failed = 1               # 崩了但没打印 FAIL
    return name, passed, failed, r.returncode, dt, out


def main(argv: list[str]) -> int:
    verbose = "-v" in argv or "--verbose" in argv
    filters = [a for a in argv if not a.startswith("-")]

    names = discover()
    if filters:
        names = [n for n in names if any(f in n for f in filters)]

    print("跑 %d 个套件%s" % (len(names), "（verbose）" if verbose else ""))
    print()
    print("%-20s %6s %6s %8s" % ("套件", "PASS", "FAIL", "耗时"))
    print("-" * 44)

    total_p = total_f = 0
    bad: list[str] = []
    for n in names:
        name, p, f, code, dt, _out = run(n, verbose)
        total_p += p
        total_f += f
        if code != 0:
            bad.append(name)
        mark = "" if code == 0 else "  <<< 失败"
        print("%-20s %6d %6d %7.1fs%s" % (name, p, f, dt, mark))

    print("-" * 44)
    print("%-20s %6d %6d" % ("合计", total_p, total_f))
    print()
    if bad:
        print("失败套件：%s" % ", ".join(bad))
        print("单独看某个：python tests/%s.py" % bad[0])
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
