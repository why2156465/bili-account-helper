"""视频库：上传 / 列表 / 删除。文件落盘到 data/uploads/{user_id}/。"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..auth import CurrentUser, require_user
from ..config import PLAN_LIMITS, PROJECT_DIR
from ..db import db
from ..quota import effective_plan

router = APIRouter(prefix="/api/library", tags=["library"])
UPLOAD_ROOT = os.path.join(PROJECT_DIR, "data", "uploads")
MAX_CHUNK = 4 * 1024 * 1024


class _File:
    """把 video_files 表结构内置在这里，路由自洽。"""


def _init() -> None:
    os.makedirs(UPLOAD_ROOT, exist_ok=True)
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS video_files(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER NOT NULL,
                 name TEXT NOT NULL,
                 path TEXT NOT NULL,
                 size_bytes INTEGER NOT NULL,
                 created_at INTEGER NOT NULL)"""
        )


def used_bytes(user_id: int) -> int:
    _init()
    with db() as conn:
        row = conn.execute("SELECT COALESCE(SUM(size_bytes),0) s FROM video_files WHERE user_id=?",
                           (user_id,)).fetchone()
    return int(row["s"])


@router.get("")
def list_files(user: CurrentUser = Depends(require_user)):
    _init()
    cap = PLAN_LIMITS[effective_plan(user)]["video_lib_mb"]
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM video_files WHERE user_id=? ORDER BY id DESC", (user.id,)
        ).fetchall()
    return {
        "cap_mb": cap,
        "used_mb": round(used_bytes(user.id) / 1024 / 1024, 2),
        "items": [{"id": r["id"], "name": r["name"],
                   "size_mb": round(r["size_bytes"] / 1024 / 1024, 2),
                   "created_at": r["created_at"]} for r in rows],
    }


@router.post("/upload")
async def upload(file: UploadFile = File(...), user: CurrentUser = Depends(require_user)):
    _init()
    cap_mb = PLAN_LIMITS[effective_plan(user)]["video_lib_mb"]
    used = used_bytes(user.id)
    d = os.path.join(UPLOAD_ROOT, str(user.id))
    os.makedirs(d, exist_ok=True)
    safe = os.path.basename(file.filename or "video.mp4").replace("..", "_")
    path = os.path.join(d, "%d_%s" % (int(time.time()), safe))

    size = 0
    try:
        with open(path, "wb") as fh:
            while True:
                chunk = await file.read(MAX_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if used + size > cap_mb * 1024 * 1024:
                    fh.close()
                    os.remove(path)
                    raise HTTPException(413, "超出视频库额度（%d MB）" % cap_mb)
                fh.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, "写入失败：%s" % exc)

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO video_files(user_id,name,path,size_bytes,created_at) VALUES(?,?,?,?,?)",
            (user.id, safe, path, size, int(time.time())),
        )
    return {"ok": True, "id": cur.lastrowid, "name": safe,
            "size_mb": round(size / 1024 / 1024, 2)}


@router.delete("/{file_id}")
def delete_file(file_id: int, user: CurrentUser = Depends(require_user)):
    _init()
    with db() as conn:
        row = conn.execute("SELECT * FROM video_files WHERE id=? AND user_id=?",
                           (file_id, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "文件不存在")
        conn.execute("DELETE FROM video_files WHERE id=?", (file_id,))
    try:
        os.remove(row["path"])
    except OSError:
        pass
    return {"ok": True}
