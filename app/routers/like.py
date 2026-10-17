"""一键互赞：单次执行多动作 + 自定义动作组合。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, log_op, require_user
from ..bili import BiliClient, BiliError, backoff_seconds, describe_error, parse_cookie
from ..db import db
from ..quota import bump_usage, ensure_quota
from ..security import decrypt_cookie
from ..tasks import run_action

router = APIRouter(prefix="/api/like", tags=["like"])


class LikeIn(BaseModel):
    account_id: int
    targets: list[str]
    actions: list[str] = ["like"]
    comment_text: str = ""


@router.post("/run")
def run_like(body: LikeIn, user: CurrentUser = Depends(require_user)):
    targets = [str(t).strip() for t in body.targets if str(t).strip()]
    actions = [a for a in body.actions if a]
    if not targets:
        raise HTTPException(400, "请填写目标 UID 或 AV 号")
    if not actions:
        raise HTTPException(400, "请至少勾选一个动作")

    ensure_quota(user, "like", len(targets) * len(actions))

    with db() as conn:
        acc = conn.execute(
            "SELECT * FROM bili_accounts WHERE id=? AND user_id=?", (body.account_id, user.id)
        ).fetchone()
        if not acc:
            raise HTTPException(404, "账号不存在")
        cookies = parse_cookie(decrypt_cookie(acc["cred_enc"]))

    results, ok_n = [], 0
    with BiliClient(cookies) as cli:
        for tgt in targets:
            for act in actions:
                t0 = time.time()
                try:
                    run_action(cli, act, tgt, {"text": body.comment_text})
                    ok_n += 1
                    bump_usage(user.id, "like", 1)
                    log_op(user.id, act, tgt, "ok", account_id=acc["id"])
                    results.append({"target": tgt, "action": act, "ok": True,
                                    "ms": int((time.time() - t0) * 1000)})
                except BiliError as exc:
                    status, msg = describe_error(exc)
                    log_op(user.id, act, tgt, "fail", exc.code, acc["id"])
                    results.append({"target": tgt, "action": act, "ok": False,
                                    "status": status, "message": msg, "code": exc.code})
                    if status in ("expired", "captcha"):
                        with db() as conn:
                            conn.execute(
                                "UPDATE bili_accounts SET status=?, coeff=coeff*1.5 WHERE id=?",
                                (status, acc["id"]))
                        return {"ok": False, "aborted": True, "message": msg,
                                "results": results}
                except Exception as exc:
                    results.append({"target": tgt, "action": act, "ok": False,
                                    "message": "网络错误：%s" % exc})
                time.sleep(backoff_seconds(acc["coeff"]))
    return {"ok": ok_n > 0, "ok_count": ok_n, "total": len(targets) * len(actions),
            "results": results}


@router.get("/actions")
def action_catalog():
    return {"items": [
        {"key": "like", "label": "点赞"},
        {"key": "coin", "label": "投币", "need_ticket": True},
        {"key": "fav", "label": "收藏"},
        {"key": "comment", "label": "评论"},
        {"key": "share", "label": "分享"},
        {"key": "follow", "label": "关注"},
        {"key": "play", "label": "刷播放"},
    ]}
