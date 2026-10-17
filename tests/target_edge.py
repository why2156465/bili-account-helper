"""目标解析边界测试：坏目标只跳过自己，不拖垮整批任务。

原行为：一个 UID 拉不到（不存在 / 风控 / 私密），resolve_targets 直接抛 BiliError，
调用方转成 502 —— 导致「10 个目标里 1 个坏了，另外 9 个也跑不了」。
修好后：坏目标在结果里标 error，好目标照常返回。

运行：python tests/target_edge.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("DB_PATH", "/tmp/__target_edge.db")
os.environ.setdefault("SECRET_KEY", "edge-test")

from app.bili import BiliError  # noqa: E402
from app.routers.interact import resolve_targets  # noqa: E402

FAILS: list[str] = []


def ck(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name +
          ((" -> " + str(extra)[:220]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


class Cli:
    """uid=1 正常有 3 个视频；uid=404 拉取报错；uid=500 抛非 BiliError 异常。"""

    def __init__(self):
        self.calls = []

    def get(self, path, params=None, **kw):
        params = params or {}
        self.calls.append(path)
        if "arc/search" in path:
            mid = str(params.get("mid"))
            if mid == "404":
                raise BiliError(-404, "啥都没找到")
            if mid == "500":
                raise RuntimeError("模拟网络炸了")
            if mid == "1":
                return {"list": {"vlist": [
                    {"aid": 1, "bvid": "BV1", "title": "旧", "created": 1700000000},
                    {"aid": 3, "bvid": "BV3", "title": "新", "created": 1700003000},
                    {"aid": 2, "bvid": "BV2", "title": "中", "created": 1700002000},
                ]}}
            return {"list": {"vlist": []}}
        if "web-interface/view" in path:
            bv = params.get("bvid")
            if bv == "BV999":
                raise BiliError(-404, "视频不存在")
            return {"aid": 9, "bvid": bv, "title": "详情", "pubdate": 1700000000,
                    "owner": {"name": "UP", "mid": 1}}
        return {}

    def post(self, *a, **k):
        return {}


def main() -> int:
    cli = Cli()

    print("[1] 空投稿池不报错")
    got = resolve_targets(cli, [{"uid": "2", "rule": "latest", "max": 1}])
    ck("返回 1 组", len(got) == 1, got)
    ck("count=0", got[0]["count"] == 0, got[0])
    ck("videos 为空数组", got[0]["videos"] == [], got[0])

    print("[2] random 的 max<n 不会抛 ValueError")
    got = resolve_targets(cli, [{"uid": "1", "rule": "random", "max": 2, "n": 10}])
    ck("不抛异常", isinstance(got, list), got)
    ck("取到个数不超过池子", got[0]["count"] <= 3, got[0]["count"])

    print("[3] pool 为空时 random 也不炸")
    got = resolve_targets(cli, [{"uid": "2", "rule": "random", "max": 20, "n": 5}])
    ck("空池 random count=0", got[0]["count"] == 0, got)

    print("[4] 坏 UID 只跳过自己")
    try:
        got = resolve_targets(cli, [{"uid": "404", "rule": "latest", "max": 1}])
        raised = None
    except Exception as e:
        got, raised = None, e
    ck("不再抛异常", raised is None, raised)
    if got:
        ck("该组 count=0", got[0]["count"] == 0, got[0])
        ck("带 error 说明", bool(got[0].get("error")), got[0])
        ck("error 提到拉取失败", "拉取" in got[0].get("error", ""), got[0].get("error"))

    print("[5] 好坏目标混在一起：好的照常返回")
    got = resolve_targets(cli, [
        {"uid": "404", "rule": "latest", "max": 1},
        {"uid": "1", "rule": "latest", "max": 2},
    ])
    ck("返回 2 组", len(got) == 2, len(got))
    ck("第一组是坏目标(count=0)", got[0]["count"] == 0, got[0])
    ck("第二组仍拿到 2 个", got[1]["count"] == 2, got[1])
    ck("第二组按时间倒序（新在前）",
       [v["aid"] for v in got[1]["videos"]] == [3, 2],
       [v["aid"] for v in got[1]["videos"]])

    print("[6] 非 BiliError 的异常仍向上抛（不吞掉程序 bug）")
    try:
        resolve_targets(cli, [{"uid": "500", "rule": "latest", "max": 1}])
        raised = None
    except RuntimeError as e:
        raised = e
    ck("RuntimeError 透传", raised is not None, raised)

    print("[7] bvids 直填：坏 bv 跳过，好的保留")
    got = resolve_targets(cli, [{"rule": "bvids", "bvids": ["BV999", "BV1ok", ""]}])
    ck("返回 1 组", len(got) == 1, got)
    ck("只保留 1 个有效", got[0]["count"] == 1, got[0])
    ck("是那个有效的", got[0]["videos"][0]["bvid"] == "BV1ok", got[0]["videos"])

    print("[8] 其他边界")
    ck("空 targets → []", resolve_targets(cli, []) == [])
    got = resolve_targets(cli, [{"rule": "latest"}])
    ck("缺 uid 标 error", got[0].get("error") == "缺少 UID", got[0])
    ck("bvids 空数组不报错",
       resolve_targets(cli, [{"rule": "bvids", "bvids": []}])[0]["count"] == 0)
    got = resolve_targets(cli, [{"uid": "1", "rule": "不存在的规则", "max": 5}])
    ck("未知规则兜底取 1 个", got[0]["count"] == 1, got[0])

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("目标解析边界测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
