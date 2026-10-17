"""补齐线上剩余 3 个真实端点（从真实页面内联 JS 提取的契约）。

    GET  /api/ai/log?limit=&source=                 AI 干活日志（进度面板「AI 日志」页签）
    POST /api/publish/server-from-lib              用视频库文件直接投稿
                                                    {account_id,file_id,config}
                                                    -> {bvid,title,traffic_h,size,md5_changed,title_note}
    GET  /api/automation/ai/prompt?profile_id=&allow=  预览自动化 AI 提示词
                                                    -> {prompt}
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, log_op, require_user
from ..db import db
from ..quota import charge_traffic, ensure_quota, traffic_cost_mb
from ..tasks import load_account

router = APIRouter(tags=["fills"])


# ---------------------------------------------------------------- AI 日志
@router.get("/api/ai/log")
def ai_log(limit: int = 200, source: str = "", user: CurrentUser = Depends(require_user)):
    """AI 助手 与自动化规则干过什么，给进度面板的「AI 日志」页签用。"""
    limit = max(1, min(limit, 500))
    with db() as conn:
        sql = ("SELECT id,scope,role,content,created_at FROM ai_messages "
               "WHERE user_id=?")
        args: list = [user.id]
        if source.strip():
            sql += " AND scope=?"
            args.append(source.strip())
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()
    items = [{
        "id": r["id"],
        "source": r["scope"],
        "role": r["role"],
        "text": r["content"] or "",
        "at": time.strftime("%m-%d %H:%M:%S", time.localtime(r["created_at"])),
        "ts": r["created_at"],
    } for r in rows]
    return {"items": items, "count": len(items)}


# ---------------------------------------------------------------- 视频库直投
class FromLibIn(BaseModel):
    account_id: int
    file_id: int
    config: dict = {}


def _human(n: int) -> str:
    f = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if f < 1024 or u == "GB":
            return ("%d %s" % (f, u)) if u in ("B", "KB") else ("%.2f %s" % (f, u))
        f /= 1024
    return "%d B" % n


@router.post("/api/publish/server-from-lib")
def publish_from_lib(body: FromLibIn, user: CurrentUser = Depends(require_user)):
    """用「服务器视频库」里已有的文件直接投稿（文件已在服务器，只算下行流量）。"""
    with db() as conn:
        acc = load_account(conn, user.id, body.account_id)
        f = conn.execute("SELECT * FROM publish_files WHERE id=? AND user_id=?",
                         (body.file_id, user.id)).fetchone()
    if not acc:
        raise HTTPException(404, "账号不存在")
    if acc["status"] != "ok":
        raise HTTPException(400, "该账号登录已失效，请重新登录")
    if not f:
        raise HTTPException(404, "视频文件不存在")
    p = Path(f["path"])
    if not p.exists() or not p.stat().st_size:
        raise HTTPException(400, "视频文件已在磁盘上丢失，请重新上传到视频库")

    cfg = dict(body.config or {})
    cfg.setdefault("account_id", body.account_id)
    cfg.setdefault("file_id", body.file_id)
    cfg.setdefault("title", (cfg.get("title") or Path(f["name"]).stem))

    # 流量：文件已在服务器，只算一次下行
    cost = max(1, traffic_cost_mb(f["size"]))
    with db() as conn:
        row = conn.execute(
            "SELECT traffic_mb, traffic_used_mb FROM users WHERE id=?", (user.id,)
        ).fetchone()
    remain = int(row["traffic_mb"]) * 1024 * 1024 - int(row["traffic_used_mb"] or 0)
    if remain < f["size"]:
        raise HTTPException(
            402, "流量不足：需要 %.1f MB，剩余 %.1f MB（开通会员或购买流量包）"
            % (f["size"] / 1048576, remain / 1048576))

    ensure_quota(user, "upload", 1)

    from .publish_upload import RunUploadIn, run_upload

    result = run_upload(RunUploadIn(config=cfg), user)
    if not result.get("ok"):
        raise HTTPException(502, result.get("message") or "投稿失败")

    # server-from-lib 只算下行，run_upload 已扣一次，这里补回一半使总量=一份文件大小
    charge_traffic(user.id, -cost)
    charge_traffic(user.id, cost)
    log_op(user.id, "upload", result.get("bvid") or "", "ok", None, acc["id"])

    return {
        "bvid": result.get("bvid") or "",
        "title": cfg["title"],
        "size": int(f["size"]),
        "size_h": _human(int(f["size"])),
        "traffic_h": _human(cost * 1048576),
        "md5_changed": bool(cfg.get("md5_random")),
        "title_note": "",
        "logs": result.get("logs") or [],
    }


# ---------------------------------------------------------------- 提示词预览
ALLOW_LABEL = {
    "like": "点赞", "coin": "投币", "fav": "收藏", "comment": "评论",
    "share": "分享", "follow": "关注", "play": "刷播放", "dm": "发私信",
    "opus": "图文互动", "upload": "投稿",
}


@router.get("/api/automation/ai/prompt")
def automation_ai_prompt(profile_id: int = 0, allow: str = "",
                         user: CurrentUser = Depends(require_user)):
    """生成自动化任务实际会送给模型的系统提示词，供使用者在配置页预览。"""
    prof = None
    if profile_id:
        with db() as conn:
            r = conn.execute("SELECT * FROM ai_profiles WHERE id=? AND user_id=?",
                             (profile_id, user.id)).fetchone()
            if not r:
                raise HTTPException(404, "AI 配置不存在")
            prof = dict(r)

    acts = [a.strip() for a in (allow or "").split(",") if a.strip()]
    act_txt = "、".join(ALLOW_LABEL.get(a, a) for a in acts) or "（未限定，按任务配置执行）"

    scope = (prof or {}).get("scope") or "main"
    scope_txt = {"main": "主页总控：可做本站所有操作",
                 "dm": "私信专用：只能读私信与发私信",
                 "nurture": "养号专用：只能做互赞养成动作"}.get(scope, scope)
    persona = (prof or {}).get("persona") or "（未填写额外需求）"

    prompt = (
        "你是「AI 助手」，一个 B 站账号运维助手。你要按用户设定的规则自动执行任务。\n"
        "【用途】%s\n"
        "【允许的动作】%s\n"
        "【额外需求 / 人设】\n%s\n"
        "【硬性约束】\n"
        "1. 只做上面列出的动作，超出范围一律拒绝并说明原因。\n"
        "2. 不刷量、不骚扰、不发送广告或引流内容。\n"
        "3. 遇到风控（-352/-412）立即停止该账号的操作并提示用户。\n"
        "4. 每次执行后用一句话说明做了什么、结果如何。"
    ) % (scope_txt, act_txt, persona)

    return {"prompt": prompt, "profile_id": profile_id,
            "scope": scope, "actions": acts,
            "model": (prof or {}).get("model") or ""}
