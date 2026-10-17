"""定时投稿执行器 —— 把 kind=upload 的任务真正跑起来。

原站配置形状（scheduler.html 里 box-upload 读出来的字段）：
    {account_id, file_id, cover, title, tid, tag, desc,
     no_danmu, no_reply, selected, no_reprint, dtime}

执行链路（与 /api/publish 手动投稿完全一致的三步）：
    1. POST /x/vu/web/preupload/v2  拿 UPOS 上传地址与 auth
    2. PUT  分片到 upload_url        按 chunk_size 切分逐片推
    3. POST /x/vu/web/add/v3         提交稿件，拿 bvid

与手动投稿的区别只有一个：文件来自「服务器视频库」而不是浏览器，
所以必须按大小扣流量（手动「本地上传」不扣）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, log_op, require_user
from ..bili import BiliError, describe_error
from ..db import db
from ..quota import charge_traffic, ensure_quota, traffic_cost_mb
from ..tasks import client_for, load_account

router = APIRouter(prefix="/api/publish", tags=["publish-upload"])

CHUNK = 8 * 1024 * 1024


class RunUploadIn(BaseModel):
    task_id: int | None = None
    config: dict = {}
    dry_run: bool = False       # True 只校验不真传，给前端「预检」用


def _preflight(user, cfg: dict) -> dict:
    """校验配置与资源，返回执行所需的上下文；任何一步不满足都给出可读原因。"""
    errors: list[str] = []

    account_id = cfg.get("account_id")
    file_id = cfg.get("file_id")
    title = (cfg.get("title") or "").strip()

    with db() as conn:
        acc = load_account(conn, user.id, account_id) if account_id else None
        f = conn.execute("SELECT * FROM publish_files WHERE id=? AND user_id=?",
                         (file_id, user.id)).fetchone() if file_id else None

    if not account_id:
        errors.append("没有指定投稿账号")
    elif not acc:
        errors.append("投稿账号不存在（id=%s）" % account_id)
    elif acc["status"] != "ok":
        errors.append("账号登录已失效，请先在「账号」页重新登录")
    elif not acc["coin_ok"] and not acc["cred_enc"]:
        errors.append("账号缺少有效 Cookie")

    if not file_id:
        errors.append("没有选择视频文件")
    elif not f:
        errors.append("视频文件不存在（id=%s）" % file_id)
    elif not Path(f["path"]).exists():
        errors.append("视频文件已在磁盘上丢失，请重新上传到视频库")
    elif not Path(f["path"]).stat().st_size:
        errors.append("视频文件是空的")

    if not title and not f:
        errors.append("没有填标题，且无法用文件名兜底")

    size_mb = traffic_cost_mb(f["size"]) if f else 0
    if f and not errors:
        with db() as conn:
            row = conn.execute("SELECT traffic_mb, traffic_used_mb FROM users WHERE id=?",
                               (user.id,)).fetchone()
        remain = int(row["traffic_mb"]) * 1024 * 1024 - int(row["traffic_used_mb"] or 0)
        if remain < f["size"]:
            errors.append("流量不足：需要 %.1f MB，剩余 %.1f MB"
                          % (f["size"] / 1048576, remain / 1048576))

    return {
        "ok": not errors,
        "errors": errors,
        "account": acc,
        "file": f,
        "title": title or (Path(f["name"]).stem if f else ""),
        "cost_mb": size_mb,
        "size": int(f["size"]) if f else 0,
    }


def _upload_and_submit(user, cfg: dict, pre: dict, log) -> dict:
    """真正执行：preupload → 分片 → submit。"""
    acc = pre["account"]
    f = pre["file"]
    path = Path(f["path"])
    size = int(f["size"])
    title = pre["title"]
    tid = str(cfg.get("tid") or "171")

    log("申请上传凭证…")
    try:
        with client_for(acc) as cli:
            data = cli.post("/x/vu/web/preupload/v2", {
                "name": f["name"], "size": size, "profile": "ugcfx/bup"})
            upload_url = data.get("url") or data.get("endpoint") or ""
            auth = data.get("auth") or ""
    except BiliError as exc:
        return {"ok": False, "error": "申请上传凭证失败：%s" % describe_error(exc)[1]}
    except Exception as exc:
        return {"ok": False, "error": "申请上传凭证失败：%s" % exc}

    if not upload_url:
        return {"ok": False, "error": "B 站没有返回上传地址（preupload 字段为空）"}

    # 分片上传
    total = max(1, (size + CHUNK - 1) // CHUNK)
    sent = 0
    try:
        with path.open("rb") as fh:
            with httpx.Client(timeout=180) as hc:
                for i in range(total):
                    blob = fh.read(CHUNK)
                    if not blob:
                        break
                    start, end = sent, sent + len(blob) - 1
                    r = hc.put(
                        upload_url,
                        content=blob,
                        params={"partNumber": i + 1, "uploadId": data.get("upload_id", ""),
                                "chunk": i, "chunks": total, "size": len(blob),
                                "start": start, "end": end},
                        headers={"Authorization": auth} if auth else None,
                    )
                    if r.status_code >= 300:
                        return {"ok": False, "error": "分片 %d/%d 上传失败 HTTP %d"
                                % (i + 1, total, r.status_code)}
                    sent += len(blob)
                    log("上传 %d/%d（%d%%）" % (i + 1, total, int(sent / size * 100)))
    except Exception as exc:
        return {"ok": False, "error": "分片上传异常：%s" % exc}

    # 提交稿件
    log("提交稿件…")
    payload = {
        "cover": cfg.get("cover") or "",
        "title": title,
        "tid": tid,
        "tag": cfg.get("tag") or "",
        "desc_format_id": 0,
        "desc": cfg.get("desc") or "",
        "source": "",
        "videos": json.dumps([{
            "filename": f["name"], "title": "P1", "desc": "", "cid": 0,
            "size": size, "upload_id": data.get("upload_id", ""), "chunks": total,
        }], ensure_ascii=False),
        "no_reprint": 1 if cfg.get("no_reprint") else 0,
        "open_elec": 0,
        "copyright": int(cfg.get("copyright") or 1),
        "dtime": int(cfg.get("dtime") or 0),
        "dynamic": cfg.get("dynamic") or "",
        "mission_id": 0,
        "interactive": 0,
        "up_close_danmu": 1 if cfg.get("no_danmu") else 0,
        "up_close_reply": 1 if cfg.get("no_reply") else 0,
        "up_selection_reply": 1 if cfg.get("selected") else 0,
    }
    try:
        with client_for(acc) as cli:
            out = cli.post("/x/vu/web/add/v3", payload,
                           params={"t": str(int(time.time() * 1000)),
                                   "csrf": cli.ident["csrf"]})
    except BiliError as exc:
        return {"ok": False, "error": "提交失败：%s" % describe_error(exc)[1]}
    except Exception as exc:
        return {"ok": False, "error": "提交失败：%s" % exc}

    bvid = out.get("bvid") or ""
    charge_traffic(user.id, pre["cost_mb"])
    with db() as conn:
        conn.execute(
            "INSERT INTO publish_records(user_id,account_id,title,bvid,aid,tid,cover,status,"
            "created_at) VALUES(?,?,?,?,?,?,?,'ok',?)",
            (user.id, acc["id"], title, bvid, out.get("aid") or "", tid,
             cfg.get("cover") or "", int(time.time())))
    log("已提交：%s" % (bvid or "待审核"))
    return {"ok": True, "bvid": bvid, "aid": out.get("aid") or "", "title": title}


@router.post("/run-upload")
def run_upload(body: RunUploadIn, user: CurrentUser = Depends(require_user)):
    """立即执行一次定时投稿（预检 / 真跑二合一）。"""
    cfg = body.config or {}
    if body.task_id and not cfg:
        with db() as conn:
            row = conn.execute("SELECT * FROM interact_tasks WHERE id=? AND user_id=?",
                               (body.task_id, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        if row["kind"] != "upload":
            raise HTTPException(400, "这不是投稿类型的任务")
        cfg = json.loads(row["config"] or "{}")

    pre = _preflight(user, cfg)
    if not pre["ok"]:
        return {"ok": False, "preflight": True, "errors": pre["errors"],
                "message": "；".join(pre["errors"])}
    if body.dry_run:
        return {"ok": True, "preflight": True, "planned": {
            "account": pre["account"]["uname"] or pre["account"]["mid"],
            "file": pre["file"]["name"],
            "size_h": round(pre["size"] / 1048576, 2),
            "cost_mb": pre["cost_mb"],
            "title": pre["title"],
        }, "message": "预检通过，可以执行"}

    ensure_quota(user, "upload", 1)
    logs: list[str] = []

    def log(m: str) -> None:
        logs.append(m)
        print("[upload] " + m, flush=True)

    result = _upload_and_submit(user, cfg, pre, log)
    with db() as conn:
        if body.task_id:
            conn.execute("UPDATE interact_tasks SET last_run=? WHERE id=?",
                         (int(time.time()), body.task_id))
    if result.get("ok"):
        log_op(user.id, "upload", result.get("bvid") or "", "ok", None, pre["account"]["id"])
        return {"ok": True, "bvid": result["bvid"], "logs": logs,
                "message": "投稿已提交：%s" % (result["bvid"] or "待审核")}
    log_op(user.id, "upload", "", "fail", None, pre["account"]["id"])
    return {"ok": False, "error": result.get("error"), "logs": logs,
            "message": result.get("error") or "投稿失败"}
