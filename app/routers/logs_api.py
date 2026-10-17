"""操作日志 —— 对齐原站「操作日志」页的分页与字段。

    GET /api/logs?page=1&limit=50
    -> {items:[{id,created_at,account,target,action,video,result,code}], total, page}
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ..auth import CurrentUser, require_user
from ..db import db

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
def logs(page: int = 1, limit: int = 50, user: CurrentUser = Depends(require_user)):
    limit = max(1, min(limit, 200))
    offset = (max(1, page) - 1) * limit
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM op_logs WHERE user_id=?",
                             (user.id,)).fetchone()["c"]
        rows = conn.execute(
            "SELECT l.id,l.created_at,l.action,l.target,l.result,l.code,l.account_id,"
            "       a.uname AS account_name, a.mid AS account_mid "
            "FROM op_logs l LEFT JOIN bili_accounts a ON a.id=l.account_id "
            "WHERE l.user_id=? ORDER BY l.id DESC LIMIT ? OFFSET ?",
            (user.id, limit, offset),
        ).fetchall()
    items = []
    for r in rows:
        ts = time.strftime("%m-%d %H:%M:%S", time.localtime(r["created_at"])) \
            if isinstance(r["created_at"], int) else str(r["created_at"])
        items.append({
            "id": r["id"],
            "created_at": ts,
            "account": r["account_name"] or (("UID " + str(r["account_mid"])) if r["account_mid"] else "-"),
            "target": r["target"] or "",
            "action": r["action"],
            "video": r["target"] if str(r["action"]) in ("interact", "like", "coin",
                                                         "fav", "comment", "share",
                                                         "follow", "play", "opus") else "",
            "result": r["result"] or "ok",
            "code": r["code"],
        })
    return {"items": items, "total": total, "page": page}
