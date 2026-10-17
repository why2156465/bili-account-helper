"""会员 / 流量包 / 管理后台 / B站只读代理 / 私信 / 代理池。"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, admin_audit, require_admin, require_user
from ..bili import BiliClient, BiliError, describe_error, parse_cookie
from ..config import PLAN_LIMITS
from ..db import db
from ..security import decrypt_cookie
from ..tasks import client_for, load_account

router = APIRouter(prefix="/api", tags=["misc"])

PLAN_PRODUCTS = {
    "month": {"name": "月卡", "days": 30, "price": 15},
    "quarter": {"name": "季卡", "days": 90, "price": 40},
    "year": {"name": "年卡", "days": 365, "price": 128},
}
TRAFFIC_PACKS = [
    {"key": "p1", "mb": 1024, "price": 5, "note": "1GB"},
    {"key": "p5", "mb": 5120, "price": 20, "note": "5GB"},
    {"key": "p20", "mb": 20480, "price": 68, "note": "20GB"},
    {"key": "p50", "mb": 51200, "price": 150, "note": "50GB"},
]


# ---------------- B 站只读代理（网页版 B 站） ----------------
@router.get("/bili/recommend")
def recommend(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        acc = load_account(conn, user.id, None)
    if not acc:
        raise HTTPException(400, "请先添加 B 站账号")
    try:
        with client_for(acc) as cli:
            data = cli.get("/x/web-interface/wbi/index/top/feed/rcmd",
                           {"ps": 12, "fresh_type": 3})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    return _strip_videos(data)


@router.get("/bili/search")
def search(q: str, page: int = 1, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        acc = load_account(conn, user.id, None)
    if not acc:
        raise HTTPException(400, "请先添加 B 站账号")
    try:
        with client_for(acc) as cli:
            data = cli.get("/x/web-interface/wbi/search/type",
                           {"search_type": "video", "keyword": q, "page": page})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    items = data.get("result") or []
    return {"items": [{"bvid": v.get("bvid"), "aid": v.get("aid"), "title": v.get("title"),
                       "author": v.get("author"), "mid": v.get("mid"),
                       "play": v.get("play"), "pic": v.get("pic")} for v in items]}


def _strip_videos(data: dict) -> dict:
    items = data.get("item") or []
    out = []
    for v in items:
        if v.get("id"):
            out.append({"aid": v.get("id"), "bvid": v.get("bvid"), "title": v.get("title"),
                        "pic": v.get("pic"), "author": v.get("owner", {}).get("name"),
                        "mid": v.get("owner", {}).get("mid"),
                        "play": v.get("stat", {}).get("view"), "cid": v.get("cid")})
    return {"items": out}


# ---------------- 私信 ----------------
@router.get("/dm/threads")
def dm_threads(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM dm_threads WHERE user_id=? ORDER BY updated_at DESC LIMIT 50",
            (user.id,),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.get("/dm/threads/{thread_id}")
def dm_messages(thread_id: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        t = conn.execute("SELECT * FROM dm_threads WHERE id=? AND user_id=?",
                         (thread_id, user.id)).fetchone()
        if not t:
            raise HTTPException(404, "会话不存在")
        msgs = conn.execute(
            "SELECT * FROM dm_messages WHERE thread_id=? ORDER BY id LIMIT 200", (thread_id,)
        ).fetchall()
        conn.execute("UPDATE dm_threads SET unread=0 WHERE id=?", (thread_id,))
    return {"thread": dict(t), "items": [dict(m) for m in msgs]}


@router.post("/dm/threads/{thread_id}/send")
def dm_send(thread_id: int, body: dict, user: CurrentUser = Depends(require_user)):
    from ..quota import bump_usage, ensure_quota

    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "内容为空")
    ensure_quota(user, "dm", 1)
    with db() as conn:
        t = conn.execute("SELECT * FROM dm_threads WHERE id=? AND user_id=?",
                         (thread_id, user.id)).fetchone()
        if not t:
            raise HTTPException(404, "会话不存在")
        acc = load_account(conn, user.id, t["account_id"])
        if not acc:
            raise HTTPException(404, "账号不存在")
        try:
            with client_for(acc) as cli:
                cli.dm_send(t["peer_uid"], text)
        except BiliError as exc:
            raise HTTPException(502, describe_error(exc)[1])
        now = int(time.time())
        conn.execute(
            "INSERT INTO dm_messages(thread_id,direction,body,created_at) VALUES(?,'out',?,?)",
            (thread_id, text, now),
        )
        conn.execute("UPDATE dm_threads SET last_msg=?, updated_at=? WHERE id=?",
                     (text[:80], now, thread_id))
    bump_usage(user.id, "dm", 1)
    return {"ok": True}
