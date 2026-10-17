"""模板管理 —— 对齐原站 /api/templates。

    GET    /api/templates?kind=interact  -> {templates:[{id,name,kind,config,created_at}]}
    POST   /api/templates {name,kind,config}
    DELETE /api/templates/{id}
    POST   /api/templates/batch-delete {ids:[]}
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, require_user
from ..db import db

router = APIRouter(prefix="/api/templates", tags=["templates"])


class TplIn(BaseModel):
    name: str = ""
    kind: str = "interact"
    config: dict = {}


def _row(r) -> dict:
    return {"id": r["id"], "name": r["name"], "kind": r["kind"],
            "config": json.loads(r["config"] or "{}"), "created_at": r["created_at"]}


@router.get("")
def list_templates(kind: str = "", user: CurrentUser = Depends(require_user)):
    with db() as conn:
        sql = "SELECT * FROM templates WHERE user_id=?"
        args: list = [user.id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        rows = conn.execute(sql + " ORDER BY id DESC", args).fetchall()
    return {"templates": [_row(r) for r in rows]}


@router.post("")
def create_template(body: TplIn, user: CurrentUser = Depends(require_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "请填写模板名称")
    if not body.config:
        raise HTTPException(400, "模板内容为空")
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM templates WHERE user_id=?",
                         (user.id,)).fetchone()["c"]
        if n >= 50:
            raise HTTPException(403, "模板数量已达上限（50 个）")
        cur = conn.execute(
            "INSERT INTO templates(user_id,name,kind,config,created_at) VALUES(?,?,?,?,?)",
            (user.id, name[:40], body.kind,
             json.dumps(body.config, ensure_ascii=False), int(time.time())),
        )
    return {"ok": True, "id": cur.lastrowid}


@router.delete("/{tpl_id}")
def delete_template(tpl_id: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        conn.execute("DELETE FROM templates WHERE id=? AND user_id=?", (tpl_id, user.id))
    return {"ok": True}


@router.post("/batch-delete")
def batch_delete(body: dict, user: CurrentUser = Depends(require_user)):
    ids = [int(x) for x in (body.get("ids") or [])]
    if not ids:
        raise HTTPException(400, "没有选中模板")
    with db() as conn:
        conn.executemany("DELETE FROM templates WHERE id=? AND user_id=?",
                         [(i, user.id) for i in ids])
    return {"ok": True, "deleted": len(ids)}
