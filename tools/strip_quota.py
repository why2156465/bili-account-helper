from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\quota.py")
t = p.read_text(encoding="utf-8")

header = '''"""额度相关的兼容层。

本项目已移除「会员 / 积分 / 流量额度」功能：所有用户都是同一档，无限制、不扣费。
保留这些函数名只是为了让既有调用点不必大改 —— 它们现在都是空操作或返回不限值。

如果将来要恢复计费，改这里即可，调用方不用动。
"""
from __future__ import annotations

import time

from fastapi import HTTPException

from .config import PLAN_LIMITS
from .db import db


def limits_for(plan: str) -> dict:
    return PLAN_LIMITS.get(plan) or PLAN_LIMITS["vip"]


def effective_plan(user) -> str:
    """所有人一律按最高档（原来用来区分免费/会员）。"""
    return "vip"


def today() -> str:
    return time.strftime("%Y-%m-%d")


def used_today(user_id: int, kind: str) -> int:
    return 0


def bump_usage(user_id: int, kind: str, n: int = 1) -> None:
    return None


def ensure_quota(user, kind: str, n: int = 1) -> dict:
    return limits_for("vip")


def ensure_account_slots(user) -> int:
    return 0


def ensure_task_slots(user, kind: str) -> None:
    return None


def monitor_interval(user, plan_key: str = "month") -> int:
    return 1


def traffic_cost_mb(size_bytes: int) -> int:
    return 0


def charge_traffic(user_id: int, mb: int) -> None:
    return None
'''
p.write_text(header, encoding="utf-8")
print("quota.py 已改写为无限制兼容层")
