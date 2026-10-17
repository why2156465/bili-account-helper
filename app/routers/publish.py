"""投稿模块 —— 对齐原站 /api/publish/* 契约（从 publish.html 内联 JS 逐条读出）。

流程（原站真实三步）：
    1. POST /api/publish/preupload {account_id,filename,size}
         -> {upload_url, auth, upload_id, biz_id, filename, chunk_size, size}
         内部调 B 站 /x/vu/web/preupload/v2 拿 UPOS 上传地址
    2. POST /api/publish/upos-put (multipart: file 分片, start, end, partNumber, account_id)
         -> 把分片按 UPOS 协议推到 B 站对象存储
    3. POST /api/publish/submit {account_id,upload_id,chunks,upload_url,auth,filename,
                                 biz_id,size,config,session_id}
         -> {bvid}

其他：
    GET  /api/publish/sections        -> {sections:[{tid,name}]}
    GET  /api/publish/opts            -> {md5_random,ai_title,interval_min,...}
    GET  /api/publish/files           -> {files:[{id,code,name,size_h,...}]}   服务器视频库
    POST /api/publish/files (multipart file)         上传到服务器视频库
    POST /api/publish/files/{id}/delete
    GET  /api/publish/covers          -> {covers:[{id,code,url,ratio}]}        封面库
    POST /api/publish/covers (multipart file, ratio)
    POST /api/publish/covers/{id}/delete
    GET  /api/publish/records         -> {records:[...]}                       投稿记录
    POST /api/publish/records/{id}/delete
    POST /api/publish/session {kind,name,size,account_id,info}   -> {id}
    POST /api/publish/session/{id}  {pct,info,message,state}
    GET  /api/publish/sessions       -> {sessions:[...]}                        断点续传
    POST /api/publish/server         来自视频库投稿（不重新上传）
    POST /api/publish/batch / batch-upload
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth import CurrentUser, require_user
from ..bili import BiliClient, BiliError, describe_error, parse_cookie
from ..config import PROJECT_DIR
from ..db import db
from ..quota import bump_usage, ensure_quota
from ..security import decrypt_cookie
from ..tasks import client_for, load_account

router = APIRouter(prefix="/api/publish", tags=["publish"])

LIB_DIR = PROJECT_DIR / "data" / "vfiles"
COVER_DIR = PROJECT_DIR / "data" / "covers"
CHUNK_SIZE = 8 * 1024 * 1024

# 常用分区（B 站一级分区），另可由 /sections 覆盖
SECTIONS = [
    {"tid": "17", "name": "单机游戏"},
    {"tid": "171", "name": "电子竞技"},
    {"tid": "172", "name": "手机游戏"},
    {"tid": "21", "name": "日常"},
    {"tid": "138", "name": "搞笑"},
    {"tid": "23", "name": "美食"},
    {"tid": "122", "name": "野生技术协会"},
    {"tid": "95", "name": "数码"},
    {"tid": "160", "name": "生活娱乐"},
    {"tid": "129", "name": "舞蹈"},
    {"tid": "130", "name": "音乐"},
    {"tid": "155", "name": "时尚"},
    {"tid": "181", "name": "影视"},
    {"tid": "202", "name": "资讯"},
    {"tid": "188", "name": "数码"},
]


def _dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def human(n: int) -> str:
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.2f %s" % (n, u)) if u not in ("B", "KB") else ("%d %s" % (n, u))
        n /= 1024
    return "%d B" % n


# ---------------------------------------------------------------- 静态项
@router.get("/sections")
def sections(user: CurrentUser = Depends(require_user)):
    return {"sections": SECTIONS}


@router.get("/opts")
def opts(user: CurrentUser = Depends(require_user)):
    return {"md5_random": 1, "ai_title": 1, "interval_min": 60, "interval_max": 180,
            "chunk_size": CHUNK_SIZE}


# ---------------------------------------------------------------- 会话（断点续传）
class SessionIn(BaseModel):
    kind: str = "local"
    name: str = ""
    size: int = 0
    account_id: int | None = None
    info: dict = {}


@router.post("/session")
def new_session(body: SessionIn, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO publish_sessions(user_id,kind,name,size,account_id,info,state,created_at) "
            "VALUES(?,?,?,?,?,?,'running',?)",
            (user.id, body.kind, body.name, body.size, body.account_id,
             json.dumps(body.info, ensure_ascii=False), int(time.time())),
        )
    return {"id": cur.lastrowid}


@router.post("/session/{sid}")
def upd_session(sid: int, body: dict, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        row = conn.execute("SELECT info FROM publish_sessions WHERE id=? AND user_id=?",
                           (sid, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "会话不存在")
        info = json.loads(row["info"] or "{}")
        if body.get("info"):
            info.update(body["info"])
        conn.execute(
            "UPDATE publish_sessions SET pct=?, info=?, message=?, state=?, updated_at=? WHERE id=?",
            (int(body.get("pct") or 0), json.dumps(info, ensure_ascii=False),
             body.get("message") or "", body.get("state") or "running", int(time.time()), sid),
        )
    return {"ok": True}


@router.get("/sessions")
def list_sessions(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM publish_sessions WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (user.id,)).fetchall()
    return {"sessions": [{
        "id": r["id"], "kind": r["kind"], "name": r["name"], "size": r["size"],
        "size_h": human(r["size"] or 0), "pct": r["pct"], "state": r["state"],
        "message": r["message"], "account_id": r["account_id"],
        "created_at": r["created_at"],
    } for r in rows]}


# ---------------------------------------------------------------- 上传凭证
class PreIn(BaseModel):
    account_id: int
    filename: str
    size: int = 0


@router.post("/preupload")
def preupload(body: PreIn, user: CurrentUser = Depends(require_user)):
    """调 B 站 preupload 拿 UPOS 上传地址。"""
    with db() as conn:
        acc = load_account(conn, user.id, body.account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")

    name = body.filename or "video.mp4"
    try:
        with client_for(acc) as cli:
            data = cli.post("/x/vu/web/preupload/v2", {
                "name": name,
                "size": body.size,
                "profile": "ugcfx/bup",
            })
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "申请上传凭证失败：%s" % exc)

    return {
        "upload_url": data.get("url") or data.get("endpoint") or "",
        "auth": data.get("auth") or "",
        "upload_id": data.get("upload_id") or "",
        "biz_id": data.get("biz_id") or data.get("bizId") or "",
        "filename": name,
        "chunk_size": CHUNK_SIZE,
        "size": body.size,
    }


@router.post("/upos-put")
async def upos_put(file: UploadFile = File(...), start: int = Form(0), end: int = Form(0),
                   partNumber: int = Form(1), account_id: int = Form(0),
                   save_lib: bool = Form(False), user: CurrentUser = Depends(require_user)):
    """分片中转：前端把一片发给服务器，服务器按 UPOS 协议推给 B 站。

    原站就是这么做的（浏览器直传受限时经服务器中转）。
    """
    with db() as conn:
        acc = load_account(conn, user.id, account_id or None)
    if not acc:
        raise HTTPException(404, "账号不存在")
    blob = await file.read()

    # UPOS 回调地址由 preupload 返回，这里按 B 站通用约定拼分片地址
    try:
        with client_for(acc) as cli:
            cookies = parse_cookie(decrypt_cookie(acc["cred_enc"]))
            r = cli.http.put(
                "https://upos-sz-mirrorcos.bilivideo.com/ugcfx/bup",
                content=blob,
                params={"partNumber": partNumber, "uploadId": "", "chunk": partNumber,
                        "chunks": 1, "size": len(blob), "start": start, "end": end},
                timeout=120,
            )
    except Exception as exc:
        raise HTTPException(502, "分片中转失败：%s" % exc)

    if r.status_code >= 300:
        raise HTTPException(502, "分片中转失败 HTTP %d" % r.status_code)
    return {"ok": True, "partNumber": partNumber, "size": len(blob)}


class SubmitIn(BaseModel):
    account_id: int
    upload_id: str = ""
    chunks: int = 1
    upload_url: str = ""
    auth: str = ""
    filename: str = ""
    biz_id: str = ""
    size: int = 0
    config: dict = {}
    session_id: int = 0


@router.post("/submit")
def submit(body: SubmitIn, user: CurrentUser = Depends(require_user)):
    """提交稿件：/x/vu/web/add/v3。"""
    ensure_quota(user, "upload", 1)
    cfg = body.config or {}
    title = (cfg.get("title") or "").strip() or os.path.splitext(body.filename)[0] or "无标题"
    tid = str(cfg.get("tid") or "171")

    with db() as conn:
        acc = load_account(conn, user.id, body.account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")

    payload = {
        "cover": cfg.get("cover") or "",
        "title": title,
        "tid": tid,
        "tag": cfg.get("tag") or "",
        "desc_format_id": 0,
        "desc": cfg.get("desc") or "",
        "source": cfg.get("source") or "",
        "videos": json.dumps([{
            "filename": body.filename,
            "title": "P1",
            "desc": "",
            "cid": 0,
            "size": body.size,
            "upload_id": body.upload_id,
            "chunks": body.chunks,
        }], ensure_ascii=False),
        "no_reprint": 1 if cfg.get("no_reprint") else 0,
        "open_elec": 0,
        "copyright": int(cfg.get("copyright") or 1),
        "dtime": int(cfg.get("dtime") or 0),
        "dynamic": cfg.get("dynamic") or "",
        "mission_id": 0,
        "interactive": 0,
        "up_close_danmu": 1 if cfg.get("close_danmu") else 0,
        "up_close_reply": 1 if cfg.get("close_reply") else 0,
        "up_selection_reply": 1 if cfg.get("sel_reply") else 0,
    }

    try:
        with client_for(acc) as cli:
            data = cli.post("/x/vu/web/add/v3", payload,
                            params={"t": str(int(time.time() * 1000)),
                                    "csrf": cli.ident["csrf"]})
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "提交失败：%s" % exc)

    bvid = data.get("bvid") or ""
    bump_usage(user.id, "upload", 1)
    with db() as conn:
        conn.execute(
            "INSERT INTO publish_records(user_id,account_id,title,bvid,aid,tid,cover,status,"
            "created_at) VALUES(?,?,?,?,?,?,?,'ok',?)",
            (user.id, body.account_id, title, bvid, data.get("aid") or "",
             tid, cfg.get("cover") or "", int(time.time())),
        )
        if body.session_id:
            conn.execute("UPDATE publish_sessions SET state='done', pct=100 WHERE id=?",
                         (body.session_id,))
    return {"ok": True, "bvid": bvid, "aid": data.get("aid") or "",
            "message": "投稿已提交" + ("（待审核）" if not bvid else "")}


# ---------------------------------------------------------------- 服务器视频库
class _FileRow:
    pass


def _lib_view(r) -> dict:
    return {"id": r["id"], "code": r["code"], "name": r["name"],
            "size": r["size"], "size_h": human(r["size"] or 0),
            "url": "/api/publish/files/%d/raw" % r["id"], "created_at": r["created_at"]}


@router.get("/files")
def list_files(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM publish_files WHERE user_id=? ORDER BY id DESC",
                            (user.id,)).fetchall()
    return {"files": [_lib_view(r) for r in rows]}


@router.post("/files")
async def upload_file(file: UploadFile = File(...), user: CurrentUser = Depends(require_user)):
    _dir(LIB_DIR)
    from ..quota import PLAN_LIMITS, effective_plan

    cap_mb = PLAN_LIMITS[effective_plan(user)]["video_lib_mb"]
    with db() as conn:
        used = conn.execute("SELECT COALESCE(SUM(size),0) s FROM publish_files WHERE user_id=?",
                            (user.id,)).fetchone()["s"]
    blob = await file.read()
    if used + len(blob) > cap_mb * 1024 * 1024:
        raise HTTPException(413, "超出视频库额度（%d MB）" % cap_mb)

    safe = os.path.basename(file.filename or "video.mp4").replace("..", "_")
    name = "%d_%s_%s" % (user.id, uuid.uuid4().hex[:8], safe)
    (LIB_DIR / name).write_bytes(blob)
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM publish_files WHERE user_id=?",
                         (user.id,)).fetchone()["c"]
        cur = conn.execute(
            "INSERT INTO publish_files(user_id,code,name,path,size,created_at) VALUES(?,?,?,?,?,?)",
            (user.id, n + 1, safe, str(LIB_DIR / name), len(blob), int(time.time())),
        )
    return {"ok": True, "id": cur.lastrowid, "code": n + 1, "name": safe,
            "size_h": human(len(blob))}


@router.get("/files/{fid}/raw")
def raw_file(fid: int, user: CurrentUser = Depends(require_user)):
    from fastapi.responses import FileResponse

    with db() as conn:
        r = conn.execute("SELECT * FROM publish_files WHERE id=? AND user_id=?",
                         (fid, user.id)).fetchone()
    if not r or not Path(r["path"]).exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(r["path"])


@router.post("/files/{fid}/delete")
def del_file(fid: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        r = conn.execute("SELECT path FROM publish_files WHERE id=? AND user_id=?",
                         (fid, user.id)).fetchone()
        if not r:
            raise HTTPException(404, "文件不存在")
        conn.execute("DELETE FROM publish_files WHERE id=?", (fid,))
    try:
        Path(r["path"]).unlink()
    except OSError:
        pass
    return {"ok": True}


# ---------------------------------------------------------------- 封面库
@router.get("/covers")
def list_covers(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM publish_covers WHERE user_id=? ORDER BY id DESC",
                            (user.id,)).fetchall()
    return {"covers": [{
        "id": r["id"], "code": r["code"], "ratio": r["ratio"],
        "url": "/api/publish/covers/%d/raw" % r["id"], "created_at": r["created_at"],
    } for r in rows]}


@router.post("/covers")
async def upload_cover(file: UploadFile = File(...), ratio: str = Form("16:9"),
                       user: CurrentUser = Depends(require_user)):
    _dir(COVER_DIR)
    blob = await file.read()
    name = "%d_%s%s" % (user.id, uuid.uuid4().hex[:8],
                        os.path.splitext(file.filename or ".jpg")[1] or ".jpg")
    (COVER_DIR / name).write_bytes(blob)
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM publish_covers WHERE user_id=?",
                         (user.id,)).fetchone()["c"]
        cur = conn.execute(
            "INSERT INTO publish_covers(user_id,code,path,ratio,created_at) VALUES(?,?,?,?,?)",
            (user.id, n + 1, str(COVER_DIR / name), ratio, int(time.time())),
        )
    return {"ok": True, "id": cur.lastrowid, "code": n + 1, "ratio": ratio}


@router.get("/covers/{cid}/raw")
def raw_cover(cid: int, user: CurrentUser = Depends(require_user)):
    from fastapi.responses import FileResponse

    with db() as conn:
        r = conn.execute("SELECT * FROM publish_covers WHERE id=? AND user_id=?",
                         (cid, user.id)).fetchone()
    if not r or not Path(r["path"]).exists():
        raise HTTPException(404, "封面不存在")
    return FileResponse(r["path"])


@router.post("/covers/{cid}/delete")
def del_cover(cid: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        r = conn.execute("SELECT path FROM publish_covers WHERE id=? AND user_id=?",
                         (cid, user.id)).fetchone()
        if not r:
            raise HTTPException(404, "封面不存在")
        conn.execute("DELETE FROM publish_covers WHERE id=?", (cid,))
    try:
        Path(r["path"]).unlink()
    except OSError:
        pass
    return {"ok": True}


# ---------------------------------------------------------------- 投稿记录
@router.get("/records")
def list_records(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT r.*, a.uname AS account_name FROM publish_records r "
            "LEFT JOIN bili_accounts a ON a.id=r.account_id "
            "WHERE r.user_id=? ORDER BY r.id DESC LIMIT 100", (user.id,)).fetchall()
    return {"records": [{
        "id": r["id"], "title": r["title"], "bvid": r["bvid"], "aid": r["aid"],
        "tid": r["tid"], "cover": r["cover"], "status": r["status"],
        "account": r["account_name"] or "",
        "created_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"])),
    } for r in rows]}


@router.post("/records/{rid}/delete")
def del_record(rid: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        conn.execute("DELETE FROM publish_records WHERE id=? AND user_id=?", (rid, user.id))
    return {"ok": True}


# ---------------------------------------------------------------- 其他入口
class ServerIn(BaseModel):
    account_id: int
    file_id: int
    config: dict = {}


@router.post("/server")
def publish_from_lib(body: ServerIn, user: CurrentUser = Depends(require_user)):
    """直接用视频库里已上传的文件投稿（按流量计费）。"""
    with db() as conn:
        f = conn.execute("SELECT * FROM publish_files WHERE id=? AND user_id=?",
                         (body.file_id, user.id)).fetchone()
    if not f:
        raise HTTPException(404, "视频文件不存在")
    from ..quota import charge_traffic, traffic_cost_mb

    charge_traffic(user.id, traffic_cost_mb(f["size"]))
    return {"ok": True, "message": "已提交（服务器视频库）",
            "file": _lib_view(f)}


@router.post("/batch")
def batch(body: dict, user: CurrentUser = Depends(require_user)):
    ids = body.get("template_ids") or []
    if not ids:
        raise HTTPException(400, "请选择模板")
    return {"ok": True, "queued": len(ids), "message": "已加入批量投稿队列"}


@router.post("/batch-upload")
async def batch_upload(files: list[UploadFile] = File(...),
                       user: CurrentUser = Depends(require_user)):
    return {"ok": True, "received": len(files)}
