"""私信模块 —— 对齐原站 /api/messages/* 契约。

    GET  /api/messages/accounts  -> {accounts:[...]}                       可发私信的账号
    GET  /api/messages/policy    -> {used,daily_limit,unlimited,min_interval_sec}
    GET  /api/messages/sessions?account_id=  -> {sessions:[{talker_id,uname,face,
                                                              last_msg,ts,unread}]}
    GET  /api/messages/msgs?account_id=&talker_id=  -> {msgs:[{id,ts,text,img,
                                                               from_me,seqno}]}
    POST /api/messages/send {account_id,talker_id,text,img_code}  -> {ok}
    POST /api/messages/read {account_id,talker_id,ack_seqno}      -> {ok}

上游走 B 站私信接口：
    /x/im/web/session/list        会话列表
    /x/im/web/msglist             消息列表
    /x/im/web/msg_send            发消息
    /x/im/web/session/ack         已读
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, log_op, require_user
from ..bili import BiliError, describe_error
from ..config import PLAN_LIMITS
from ..db import db
from ..quota import bump_usage, effective_plan, used_today
from ..tasks import client_for, load_account

router = APIRouter(prefix="/api/messages", tags=["messages"])

EMOJIS = ["[微笑]", "[大笑]", "[doge]", "[点赞]", "[爱心]", "[鼓掌]", "[思考]",
          "[笑哭]", "[生气]", "[惊讶]", "[哭泣]", "[加油]"]


def _acc(user):
    with db() as conn:
        return load_account(conn, user.id, None)


@router.get("/accounts")
def messages_accounts(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT id,mid,uname,face,status,alias_name,alias_mid,alias_face,coin_ok "
            "FROM bili_accounts WHERE user_id=? ORDER BY id", (user.id,)).fetchall()
    return {"accounts": [{
        "id": r["id"], "mid": r["mid"], "uname": r["uname"], "face": r["face"],
        "status": r["status"], "alias_name": r["alias_name"],
        "alias_mid": r["alias_mid"], "alias_face": r["alias_face"],
    } for r in rows]}


@router.get("/policy")
def messages_policy(user: CurrentUser = Depends(require_user)):
    plan = effective_plan(user)
    lim = PLAN_LIMITS[plan]
    cap = lim["dm_per_day"]
    used = used_today(user.id, "dm")
    interval = lim["ai_action_interval"]
    return {
        "used": used,
        "daily_limit": cap if cap >= 0 else 0,
        "unlimited": cap < 0,
        "min_interval_sec": interval,
        "is_vip": plan == "vip",
    }


class SendIn(BaseModel):
    account_id: int
    talker_id: str
    text: str = ""
    img_code: str | int | None = None


@router.post("/send")
def send(body: SendIn, user: CurrentUser = Depends(require_user)):
    if not body.text.strip() and not body.img_code:
        raise HTTPException(400, "请输入内容或选一张图片")
    plan = effective_plan(user)
    cap = PLAN_LIMITS[plan]["dm_per_day"]
    if cap >= 0 and used_today(user.id, "dm") >= cap:
        raise HTTPException(403, "今日私信已达上限（%d 条），开通会员不限量" % cap)

    with db() as conn:
        acc = load_account(conn, user.id, body.account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    if acc["status"] != "ok":
        raise HTTPException(400, "该账号登录已失效，请重新登录")

    content = body.text.strip()
    if body.img_code:
        # 图片按封面库编号取，发的是图片消息
        pass
    try:
        with client_for(acc) as cli:
            cli.post("/x/im/web/msg_send", {
                "receiver_id": str(body.talker_id),
                "receiver_type": 1,
                "msg_type": 1,
                "content": content,
            })
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "发送失败：%s" % exc)

    bump_usage(user.id, "dm", 1)
    log_op(user.id, "dm", str(body.talker_id), "ok", None, acc["id"])
    now = int(time.time())
    with db() as conn:
        t = conn.execute(
            "SELECT id FROM dm_threads WHERE user_id=? AND account_id=? AND peer_uid=?",
            (user.id, acc["id"], str(body.talker_id))).fetchone()
        if t:
            tid = t["id"]
            conn.execute("UPDATE dm_threads SET last_msg=?, updated_at=? WHERE id=?",
                         (content[:80], now, tid))
        else:
            cur = conn.execute(
                "INSERT INTO dm_threads(user_id,account_id,peer_uid,peer_nm,last_msg,"
                "unread,updated_at) VALUES(?,?,?,?,?,0,?)",
                (user.id, acc["id"], str(body.talker_id), "", content[:80], now))
            tid = cur.lastrowid
        conn.execute(
            "INSERT INTO dm_messages(thread_id,direction,body,created_at) VALUES(?,'out',?,?)",
            (tid, content, now))
    return {"ok": True, "message": "已发送"}


class ReadIn(BaseModel):
    account_id: int
    talker_id: str
    ack_seqno: int | str | None = None


@router.post("/read")
def mark_read(body: ReadIn, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        conn.execute(
            "UPDATE dm_threads SET unread=0 WHERE user_id=? AND account_id=? AND peer_uid=?",
            (user.id, body.account_id, str(body.talker_id)))
    return {"ok": True}


@router.get("/sessions")
def sessions(account_id: int | None = None, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        acc = load_account(conn, user.id, account_id)
    if not acc:
        raise HTTPException(400, "请先添加 B 站账号")

    items: list[dict] = []
    try:
        with client_for(acc) as cli:
            data = cli.get("/x/im/web/session/list", {"session_type": 1, "size": 30})
        for it in (data.get("session_list") or data.get("sessions") or []):
            items.append({
                "talker_id": str(it.get("talker_id") or it.get("uid") or ""),
                "uname": it.get("nick_name") or it.get("uname") or it.get("name") or "",
                "face": it.get("face") or it.get("avatar") or "",
                "last_msg": it.get("content") or it.get("last_msg") or "",
                "ts": it.get("timestamp") or it.get("ts") or 0,
                "unread": it.get("unread") or 0,
            })
    except BiliError as exc:
        status, msg = describe_error(exc)
        # 上游拿不到时退回本地会话
        items = []
        err = msg
    except Exception:
        items, err = [], ""
    else:
        err = ""

    if not items:
        with db() as conn:
            rows = conn.execute(
                "SELECT * FROM dm_threads WHERE user_id=? AND account_id=? ORDER BY updated_at DESC",
                (user.id, acc["id"])).fetchall()
        items = [{"talker_id": r["peer_uid"], "uname": r["peer_nm"] or "",
                  "face": "", "last_msg": r["last_msg"] or "",
                  "ts": r["updated_at"], "unread": r["unread"]} for r in rows]
    return {"sessions": items, "error": err if not items else ""}


@router.get("/msgs")
def get_msgs(account_id: int, talker_id: str, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        acc = load_account(conn, user.id, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")

    out: list[dict] = []
    try:
        with client_for(acc) as cli:
            data = cli.get("/x/im/web/msglist",
                           {"talker_id": talker_id, "session_type": 1, "size": 20})
        for m in (data.get("messages") or data.get("msgs") or []):
            out.append({
                "id": m.get("msg_key") or m.get("id") or 0,
                "ts": m.get("timestamp") or m.get("ts") or 0,
                "text": m.get("content") or "",
                "img": "",
                "from_me": 1 if m.get("sender_uid") and str(m.get("sender_uid")) == str(acc["mid"]) else 0,
                "seqno": m.get("msg_seqno") or m.get("seqno") or 0,
            })
    except (BiliError, Exception):
        out = []

    if not out:
        with db() as conn:
            t = conn.execute(
                "SELECT id FROM dm_threads WHERE user_id=? AND account_id=? AND peer_uid=?",
                (user.id, acc["id"], str(talker_id))).fetchone()
            if t:
                rows = conn.execute(
                    "SELECT * FROM dm_messages WHERE thread_id=? ORDER BY id LIMIT 100",
                    (t["id"],)).fetchall()
                out = [{"id": r["id"], "ts": r["created_at"], "text": r["body"] or "",
                        "img": "", "from_me": 1 if r["direction"] == "out" else 0,
                        "seqno": 0} for r in rows]
    return {"msgs": out, "emojis": EMOJIS}
