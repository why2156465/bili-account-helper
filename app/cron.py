"""五段 cron 解析器（纯标准库）。支持 *, */n, a-b, a-b/n, 逗号列表。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("dom", 1, 31),
    ("month", 1, 12),
    ("dow", 0, 6),  # 0=周日，与 cron 习惯一致
)


class CronError(ValueError):
    pass


def _parse_field(expr: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise CronError("空字段")
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step <= 0:
                raise CronError("步长必须为正")
        if part == "*":
            rng = range(lo, hi + 1)
        elif "-" in part:
            a, b = part.split("-", 1)
            rng = range(int(a), int(b) + 1)
        else:
            v = int(part)
            rng = range(v, hi + 1) if step > 1 and "/" in expr else range(v, v + 1)
        for v in rng:
            if v < lo or v > hi:
                raise CronError("越界: %s" % v)
            if (v - lo) % step == 0 or part == "*":
                out.add(v)
    return out


def parse(expr: str) -> dict[str, set[int]]:
    parts = (expr or "").split()
    if len(parts) != 5:
        raise CronError("需要 5 段：分 时 日 月 周")
    return {name: _parse_field(parts[i], lo, hi) for i, (name, lo, hi) in enumerate(FIELDS)}


def next_run(expr: str, after: int | None = None) -> int:
    """返回下一次触发的时间戳；一年内找不到则返回 0。"""
    sched = parse(expr)
    t = datetime.fromtimestamp(after or time.time()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = t + timedelta(days=366)
    while t < limit:
        if (t.minute in sched["minute"] and t.hour in sched["hour"]
                and t.day in sched["dom"] and t.month in sched["month"]
                and (t.weekday() + 1) % 7 in sched["dow"]):
            return int(t.timestamp())
        t += timedelta(minutes=1)
    return 0


def describe(expr: str) -> str:
    """给前端展示的短描述，与原站「0 9 * * *」风格呼应。"""
    try:
        parse(expr)
    except CronError:
        return "表达式无效"
    p = (expr or "").split()
    if p[:2] == ["0", "9"] and p[2:] == ["*", "*", "*"]:
        return "每天 9 点"
    if p[2:] == ["*", "*", "*"]:
        return "每天 %02d:%02d" % (int(p[1]) if p[1].isdigit() else 0,
                                  int(p[0]) if p[0].isdigit() else 0)
    return expr
