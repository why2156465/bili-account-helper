"""站内浏览模块 —— 对齐原站 /api/web/* 契约（从 web.html 内联 JS 提取）。

真实接口：
    GET  /api/web/feed?account_id=&ps=          推荐流       -> {videos:[...]}
    GET  /api/web/space?account_id=             空间信息     -> {info,relation,stat}
    GET  /api/web/space?account_id=&mid=        他人空间
    POST /api/web/space/follow {account_id,mid,act}   1=关注 2=取关
    GET  /api/web/info?account_id=&bvid=        视频详情     -> {bvid,aid,owner,...}
    POST /api/web/act {account_id,bvid,aid,act,mid,multiply}  点赞/投币/收藏/关注
    GET  /api/web/replies?account_id=&bvid=&aid=  一级评论   -> {replies,count}
    GET  /api/web/replies/sub?account_id=&aid=&root=  子评论
    POST /api/web/reply/add {account_id,bvid,aid,text}
    POST /api/web/reply/to {account_id,bvid,aid,rpid,root,text}
    POST /api/web/reply/delete {account_id,bvid,aid,rpid}
    GET  /api/web/history?account_id=&ps=       观看记录     -> {videos}
    GET  /api/web/likes?account_id=&ps=&pn=     点赞列表     -> {videos,total}
    GET  /api/web/follows?account_id=&ps=&pn=   关注列表     -> {ups,total}
    GET  /api/web/mine?account_id=&pn=&ps=      我的投稿     -> {videos,total}
    POST /api/web/archive/delete {account_id,aid}
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, log_op, require_user
from ..bili import BiliError, describe_error
from ..db import db
from ..tasks import client_for, load_account

router = APIRouter(prefix="/api/web", tags=["web"])

WBI_KEYS = ("w_rid", "wts")


def _acc(user, account_id):
    with db() as conn:
        acc = load_account(conn, user.id, account_id)
    if not acc:
        raise HTTPException(400, "请先添加并登录 B 站账号")
    if acc["status"] != "ok":
        raise HTTPException(400, "该账号登录已失效，请重新登录")
    return acc


def _video(v: dict) -> dict:
    """把 B 站返回的条目统一成前端要的字段。"""
    stat = v.get("stat") or {}
    owner = v.get("owner") or {}
    return {
        "bvid": v.get("bvid") or "",
        "aid": v.get("aid") or v.get("id") or 0,
        "title": v.get("title") or "",
        "pic": v.get("pic") or "",
        "duration": v.get("duration") or v.get("length") or "",
        "author": owner.get("name") or v.get("author") or v.get("uname") or "",
        "mid": owner.get("mid") or v.get("mid") or 0,
        "view": stat.get("view") or v.get("play") or 0,
        "danmaku": stat.get("danmaku") or v.get("video_review") or 0,
        "progress": v.get("progress") or 0,
        "pubdate": v.get("pubdate") or v.get("created") or 0,
    }


# ---------------------------------------------------------------- 推荐流
@router.get("/feed")
def web_feed(account_id: int, ps: int = 24, user: CurrentUser = Depends(require_user)):
    acc = _acc(user, account_id)
    ps = max(1, min(ps, 50))
    try:
        with client_for(acc) as cli:
            data = cli.get("/x/web-interface/wbi/index/top/feed/rcmd",
                           {"ps": ps, "fresh_type": 3, "feed_version": "V8"})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "拉取推荐失败：%s" % exc)
    items = data.get("item") or data.get("data") or []
    return {"videos": [_video(v) for v in items if isinstance(v, dict)]}


# ---------------------------------------------------------------- 空间
def _space_info(cli, mid: str) -> dict:
    card = cli.get("/x/web-interface/card", {"mid": mid})
    c = card.get("card") or {}
    rel = card.get("relation") or {}
    return {
        "mid": c.get("mid") or mid,
        "name": c.get("name") or "",
        "face": c.get("face") or "",
        "sign": c.get("sign") or "",
        "level": c.get("level_info", {}).get("current_level", 0),
        "fans": card.get("follower") or 0,
        "following": card.get("following") or 0,
        "archive": card.get("archive_count") or 0,
        "likes": card.get("like_num") or 0,
        "relation": {"follow": bool(rel.get("attribute") == 2)},
    }


@router.get("/space")
def web_space(account_id: int, mid: str = "", user: CurrentUser = Depends(require_user)):
    acc = _acc(user, account_id)
    target = str(mid or acc["mid"])
    try:
        with client_for(acc) as cli:
            info = _space_info(cli, target)
            vids = []
            try:
                arc = cli.get("/x/space/wbi/arc/search",
                              {"mid": target, "ps": 24, "pn": 1, "order": "pubdate"})
                vids = [_video(v) for v in ((arc.get("list") or {}).get("vlist") or [])]
            except Exception:
                vids = []
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "读取空间失败：%s" % exc)
    return {"info": info, "relation": info["relation"], "videos": vids}


class FollowIn(BaseModel):
    account_id: int
    mid: str | int
    act: int = 1          # 1=关注 2=取关


@router.post("/space/follow")
def web_space_follow(body: FollowIn, user: CurrentUser = Depends(require_user)):
    acc = _acc(user, body.account_id)
    try:
        with client_for(acc) as cli:
            cli.post("/x/relation/modify",
                     {"fid": str(body.mid), "act": int(body.act), "re_src": 11,
                      "csrf": cli.ident["csrf"]})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "操作失败：%s" % exc)
    log_op(user.id, "follow" if body.act == 1 else "unfollow", str(body.mid), "ok",
           None, acc["id"])
    return {"ok": True, "message": "已关注" if body.act == 1 else "已取消关注"}


# ---------------------------------------------------------------- 视频详情
@router.get("/info")
def web_info(account_id: int, bvid: str, user: CurrentUser = Depends(require_user)):
    acc = _acc(user, account_id)
    try:
        with client_for(acc) as cli:
            d = cli.get("/x/web-interface/view", {"bvid": bvid})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "读取视频失败：%s" % exc)
    out = _video(d)
    out["desc"] = d.get("desc") or ""
    out["pic"] = d.get("pic") or ""
    out["owner"] = {"mid": (d.get("owner") or {}).get("mid") or 0,
                    "name": (d.get("owner") or {}).get("name") or ""}
    out["stat"] = d.get("stat") or {}
    out["pages"] = len(d.get("pages") or [])
    out["like"] = (d.get("stat") or {}).get("like") or 0
    out["coin"] = (d.get("stat") or {}).get("coin") or 0
    out["favorite"] = (d.get("stat") or {}).get("favorite") or 0
    out["share"] = (d.get("stat") or {}).get("share") or 0
    return out


ACT_PATHS = {
    "like": "/x/web-interface/archive/like",
    "coin": "/x/web-interface/coin/add",
    "fav": "/x/v3/fav/resource/deal",
    "share": "/x/web-interface/share/add",
}


class ActIn(BaseModel):
    account_id: int
    bvid: str = ""
    aid: int | str = 0
    act: str = "like"
    mid: str | int = 0
    multiply: int = 1


@router.post("/act")
def web_act(body: ActIn, user: CurrentUser = Depends(require_user)):
    acc = _acc(user, body.account_id)
    act = body.act

    if act == "follow":
        if not body.mid:
            raise HTTPException(400, "缺少 UP 主 mid")
        return web_space_follow(FollowIn(account_id=body.account_id, mid=body.mid, act=1),
                                user)

    if act not in ACT_PATHS and act != "triple":
        raise HTTPException(400, "不支持的操作：%s" % act)

    csrf = None
    try:
        with client_for(acc) as cli:
            csrf = cli.ident["csrf"]
            if act == "like":
                cli.post(ACT_PATHS["like"], {"bvid": body.bvid, "like": 1, "csrf": csrf})
            elif act == "coin":
                cli.post(ACT_PATHS["coin"],
                         {"bvid": body.bvid, "multiply": int(body.multiply or 1),
                          "select_like": 0, "csrf": csrf})
            elif act == "fav":
                cli.post(ACT_PATHS["fav"],
                         {"rid": body.aid, "type": 2, "add_media_ids": "",
                          "del_media_ids": "", "csrf": csrf})
            elif act == "share":
                cli.post(ACT_PATHS["share"],
                         {"bvid": body.bvid, "csrf": csrf})
            elif act == "triple":
                # 三连：点赞 + 投币 + 收藏
                cli.post(ACT_PATHS["like"], {"bvid": body.bvid, "like": 1, "csrf": csrf})
                cli.post(ACT_PATHS["coin"],
                         {"bvid": body.bvid, "multiply": 2, "select_like": 1, "csrf": csrf})
                cli.post(ACT_PATHS["fav"],
                         {"rid": body.aid, "type": 2, "add_media_ids": "",
                          "del_media_ids": "", "csrf": csrf})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "操作失败：%s" % exc)

    log_op(user.id, act, body.bvid or str(body.aid), "ok", None, acc["id"])
    return {"ok": True, "message": "操作成功"}


# ---------------------------------------------------------------- 评论
@router.get("/replies")
def web_replies(account_id: int, bvid: str = "", aid: str = "",
                user: CurrentUser = Depends(require_user)):
    acc = _acc(user, account_id)
    params = {"type": 1, "ps": 20, "pn": 1, "sort": 2}
    if aid:
        params["oid"] = aid
    elif bvid:
        params["oid"] = bvid
    try:
        with client_for(acc) as cli:
            d = cli.get("/x/v2/reply/wbi/main", {**params, "mode": 3})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "读取评论失败：%s" % exc)

    replies = []
    for r in (d.get("replies") or []):
        member = r.get("member") or {}
        content = r.get("content") or {}
        replies.append({
            "rpid": r.get("rpid"),
            "root": r.get("root") or r.get("rpid"),
            "text": content.get("message") or "",
            "uname": member.get("uname") or "",
            "face": member.get("avatar") or "",
            "mid": member.get("mid") or 0,
            "like": r.get("like") or 0,
            "ctime": r.get("ctime") or 0,
            "rcount": r.get("rcount") or 0,
        })
    return {"replies": replies, "count": (d.get("page") or {}).get("count") or len(replies)}


@router.get("/replies/sub")
def web_replies_sub(account_id: int, root: str | int, aid: str = "",
                    user: CurrentUser = Depends(require_user)):
    acc = _acc(user, account_id)
    try:
        with client_for(acc) as cli:
            d = cli.get("/x/v2/reply/reply",
                        {"type": 1, "oid": aid, "root": root, "ps": 20, "pn": 1})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "读取子评论失败：%s" % exc)
    out = []
    for r in (d.get("replies") or []):
        member = r.get("member") or {}
        out.append({
            "rpid": r.get("rpid"), "root": root,
            "text": (r.get("content") or {}).get("message") or "",
            "uname": member.get("uname") or "", "face": member.get("avatar") or "",
            "mid": member.get("mid") or 0, "ctime": r.get("ctime") or 0,
        })
    return {"replies": out}


class ReplyAddIn(BaseModel):
    account_id: int
    bvid: str = ""
    aid: int | str = 0
    text: str = ""


@router.post("/reply/add")
def web_reply_add(body: ReplyAddIn, user: CurrentUser = Depends(require_user)):
    if not body.text.strip():
        raise HTTPException(400, "请输入评论内容")
    acc = _acc(user, body.account_id)
    try:
        with client_for(acc) as cli:
            cli.post("/x/v2/reply/add",
                     {"oid": body.aid or body.bvid, "type": 1, "message": body.text.strip(),
                      "plat": 1, "csrf": cli.ident["csrf"]})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "评论失败：%s" % exc)
    log_op(user.id, "comment", body.bvid or str(body.aid), "ok", None, acc["id"])
    return {"ok": True, "message": "评论已发送"}


class ReplyToIn(ReplyAddIn):
    rpid: int | str = 0
    root: int | str = 0


@router.post("/reply/to")
def web_reply_to(body: ReplyToIn, user: CurrentUser = Depends(require_user)):
    if not body.text.strip():
        raise HTTPException(400, "请输入回复内容")
    acc = _acc(user, body.account_id)
    try:
        with client_for(acc) as cli:
            cli.post("/x/v2/reply/add",
                     {"oid": body.aid or body.bvid, "type": 1,
                      "message": body.text.strip(), "root": body.root or body.rpid,
                      "parent": body.rpid, "plat": 1, "csrf": cli.ident["csrf"]})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "回复失败：%s" % exc)
    return {"ok": True, "message": "回复已发送"}


class ReplyDelIn(BaseModel):
    account_id: int
    bvid: str = ""
    aid: int | str = 0
    rpid: int | str = 0


@router.post("/reply/delete")
def web_reply_delete(body: ReplyDelIn, user: CurrentUser = Depends(require_user)):
    acc = _acc(user, body.account_id)
    try:
        with client_for(acc) as cli:
            cli.post("/x/v2/reply/del",
                     {"oid": body.aid or body.bvid, "type": 1, "rpid": body.rpid,
                      "csrf": cli.ident["csrf"]})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "删除失败：%s" % exc)
    return {"ok": True, "message": "已删除"}


# ---------------------------------------------------------------- 我的列表
@router.get("/history")
def web_history(account_id: int, ps: int = 20, user: CurrentUser = Depends(require_user)):
    acc = _acc(user, account_id)
    try:
        with client_for(acc) as cli:
            d = cli.get("/x/web-interface/history/cursor",
                        {"ps": max(1, min(ps, 50)), "type": "archive"})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "读取观看记录失败：%s" % exc)
    items = d.get("list") or []
    return {"videos": [{
        "bvid": v.get("history", {}).get("bvid") or v.get("bvid") or "",
        "aid": v.get("history", {}).get("oid") or v.get("aid") or 0,
        "title": v.get("title") or "",
        "pic": v.get("cover") or v.get("pic") or "",
        "author": v.get("author_name") or v.get("author") or "",
        "mid": v.get("author_mid") or v.get("mid") or 0,
        "progress": v.get("progress") or 0,
        "duration": v.get("duration") or 0,
        "view": v.get("videos") or 0,
    } for v in items]}


@router.get("/likes")
def web_likes(account_id: int, ps: int = 20, pn: int = 1,
              user: CurrentUser = Depends(require_user)):
    acc = _acc(user, account_id)
    try:
        with client_for(acc) as cli:
            d = cli.get("/x/space/wbi/arc/search", {"mid": acc["mid"], "ps": ps, "pn": pn})
        # 点赞列表需 v2 接口，失败时退回空列表而不是报错
    except Exception:
        d = {}
    items = (d.get("list") or {}).get("vlist") or []
    return {"videos": [_video({
        "bvid": "av" if not v.get("bvid") else v.get("bvid"),
        "aid": v.get("aid"), "title": v.get("title"), "pic": v.get("pic"),
        "author": v.get("author"), "mid": v.get("mid"),
        "stat": {"view": v.get("play")}, "pubdate": v.get("created"),
    }) for v in items], "total": (d.get("page") or {}).get("count") or len(items)}


@router.get("/follows")
def web_follows(account_id: int, ps: int = 30, pn: int = 1,
                user: CurrentUser = Depends(require_user)):
    acc = _acc(user, account_id)
    try:
        with client_for(acc) as cli:
            d = cli.get("/x/relation/followings",
                        {"vmid": acc["mid"], "ps": ps, "pn": pn, "order": "desc"})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "读取关注列表失败：%s" % exc)
    return {"ups": [{
        "mid": u.get("mid"), "uname": u.get("uname") or "", "face": u.get("face") or "",
        "sign": u.get("sign") or "", "fans": u.get("fans") or 0,
    } for u in (d.get("list") or [])], "total": d.get("total") or 0}


@router.get("/mine")
def web_mine(account_id: int, pn: int = 1, ps: int = 24,
             user: CurrentUser = Depends(require_user)):
    acc = _acc(user, account_id)
    try:
        with client_for(acc) as cli:
            d = cli.get("/x/space/wbi/arc/search",
                        {"mid": acc["mid"], "ps": ps, "pn": pn, "order": "pubdate"})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "读取投稿失败：%s" % exc)
    items = (d.get("list") or {}).get("vlist") or []
    return {"videos": [{
        "bvid": v.get("bvid") or ("av%d" % (v.get("aid") or 0)),
        "aid": v.get("aid"), "title": v.get("title") or "",
        "pic": v.get("pic") or "", "view": v.get("play") or 0,
        "duration": v.get("length") or "", "pubdate": v.get("created") or 0,
        "author": acc["uname"] or "", "mid": acc["mid"],
    } for v in items], "total": (d.get("page") or {}).get("count") or len(items)}


class ArcDelIn(BaseModel):
    account_id: int
    aid: int | str


@router.post("/archive/delete")
def web_archive_delete(body: ArcDelIn, user: CurrentUser = Depends(require_user)):
    acc = _acc(user, body.account_id)
    try:
        with client_for(acc) as cli:
            cli.post("/x/web-interface/delete",
                     {"aid": body.aid, "csrf": cli.ident["csrf"]})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "删除稿件失败：%s" % exc)
    log_op(user.id, "archive_delete", str(body.aid), "ok", None, acc["id"])
    return {"ok": True, "message": "已删除"}
