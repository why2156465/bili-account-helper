"""接口覆盖检查：每个注册的 /api/* 路由都必须被至少一个测试真正调用过。

第三类盲区（前两类已由 contract_static / schema_check 覆盖）：
接口注册了、路由能匹配、但没有任何测试打过它 —— 于是 GET 里查错列、
返回 500 这种问题能一路绿灯活到线上。/api/me/settings 就是这么漏的。

做法：扫 tests/ 下所有 .py 源码，收集出现过的路由路径字面量，
再和 app.openapi() 的路由表比对。路径参数统一归一化。
运行：python tests/endpoint_coverage.py
"""
from __future__ import annotations

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("SECRET_KEY", "cov-check")
os.environ.setdefault("DB_PATH", "/tmp/__cov_check.db")

from app.main import app  # noqa: E402

TESTS = os.path.join(ROOT, "tests")

# 明确豁免：这些路由由框架/浏览器直接访问，或属于运行时探活
EXEMPT = {
    "/healthz",           # 探活，不需要业务测试
}


def norm(path: str) -> str:
    """把 {id} / {acc_id} / {tid} 统一成 X，便于跨命名匹配。"""
    return re.sub(r"\{[^}]+\}", "X", path.rstrip("/"))


def registered() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p, ops in app.openapi()["paths"].items():
        if not p.startswith("/api"):
            continue
        out[norm(p)] = sorted(m.upper() for m in ops)
    return out


def called_paths() -> set[str]:
    """测试源码里出现过的 /api/... 路径字面量（含拼接）。"""
    seen: set[str] = set()
    for f in glob.glob(os.path.join(TESTS, "*.py")):
        src = open(f, encoding="utf-8").read()
        for m in re.finditer(r"[\"'](/api/[^\"'\s]*)[\"']", src):
            raw = m.group(1)
            raw = raw.split("?")[0]
            # 处理 f-string / % 拼接：留下 /api/xxx/ 前缀即可参与前缀匹配
            raw = re.sub(r"%[sdr].*$", "", raw)
            seen.add(raw.rstrip("/"))
    return seen


def main() -> int:
    reg = registered()
    calls = called_paths()
    print("注册的 /api 路由:", len(reg))
    print("测试里出现的路径字面量:", len(calls))
    print()

    covered, missing = [], []
    for path in sorted(reg):
        if path in EXEMPT:
            continue
        # 精确匹配，或测试里用了 f-string 前缀（只写了前半段）
        # 三种匹配：完全相同 / 测试是 f-string 前缀 / 路由含参数且测试写了具体值
        hits = [c for c in calls
                if c == path
                or (c and path.startswith(c + "/"))
                or ("X" in path and c and re.fullmatch(
                    re.escape(path).replace(re.escape("X"), "[^/]+"), c))]
        if hits:
            covered.append(path)
        else:
            missing.append(path)

    print("已覆盖:", len(covered), "/", len(covered) + len(missing))
    if missing:
        print()
        print("以下 %d 个路由没有任何测试调用：" % len(missing))
        for p in missing:
            print("  ✗ %-46s %s" % (p, ",".join(reg[p])))
        return 1
    print()
    print("所有已注册接口都有测试覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
