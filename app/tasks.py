"""任务执行器：互赞 / 投稿 / 私信 / AI / 监控。

任务用 config JSON 描述，worker 或手动触发都走同一个 run_task()。
"""
from __future__ import annotations

import json
import random
import time
from typing import Any, Callable

from .bili import BiliClient, BiliError, backoff_seconds, describe_error, parse_cookie
from .db import db
from .quota import bump_usage, ensure_quota
from .security import decrypt_cookie

ACTIONS = {
    "like": "点赞",
    "coin": "投币",
    "fav": "收藏",
    "share": "分享",
    "follow": "关注",
    "play": "刷播放",
    "comment": "评论",
}


def load_account(conn, user_id: int, account_id: int | None):
    if account_id:
        row = conn.execute(
            "SELECT * FROM bili_accounts WHERE id=? AND user_id=?", (account_id, user_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM bili_accounts WHERE user_id=? ORDER BY id LIMIT 1", (user_id,)
        ).fetchone()
    return row


def client_for(account_row) -> BiliClient:
    raw = decrypt_cookie(account_row["cred_enc"])
    return BiliClient(parse_cookie(raw))


def run_action(cli: BiliClient, action: str, aid: str | int, extra: dict | None = None) -> dict:
    extra = extra or {}
    if action == "like":
        return cli.like(aid)
    if action == "coin":
        return cli.coin(aid, int(extra.get("num", 1)))
    if action == "fav":
        return cli.fav(aid, bool(extra.get("add", True)))
    if action == "share":
        return cli.share(aid)
    if action == "follow":
        return cli.follow(aid)
    if action == "comment":
        return cli.post("/x/v2/reply/add",
                        {"oid": aid, "type": 1, "message": extra.get("text", ""),
                         "plat": 1})
    if action == "play":
        return cli.post("/x/click-interface/web/heartbeat",
                        {"aid": aid, "played_time": extra.get("seconds", 30)})
    raise ValueError("未知动作 %s" % action)


