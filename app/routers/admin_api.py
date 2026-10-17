"""管理后台 —— 对齐原站 /api/admin/* 契约。

已移除会员/积分/流量相关的接口（orders / redeem / 会员设置）。
保留：统计、用户（封禁/解封）、B站账号、日志与审计、公告。

真实接口（用普通账号探测确认存在与允许的方法）：
    GET  /api/admin/stats  users  logs  accounts  settings  notices
    POST /api/admin/settings  notices
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, admin_audit, require_admin
from ..config import settings as app_settings
from ..db import db

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _fmt(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


# ---------------------------------------------------------------- stats
@router.get("/stats")
def admin_stats(admin: CurrentUser = Depends(require_admin)):
    now = int(time.time())
    day = int(time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1)))
    with db() as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        new_today = conn.execute("SELECT COUNT(*) c FROM users WHERE created_at>=?",
                                 (day,)).fetchone()["c"]
        accs = conn.execute("SELECT COUNT(*) c FROM bili_accounts").fetchone()["c"]
        acc_ok = conn.execute("SELECT COUNT(*) c FROM bili_accounts WHERE status='ok'"
                              ).fetchone()["c"]
        tasks = conn.execute("SELECT COUNT(*) c FROM interact_tasks").fetchone()["c"]
        ops_today = conn.execute("SELECT COUNT(*) c FROM op_logs WHERE created_at>=?",
                                 (day,)).fetchone()["c"]
        ops_all = conn.execute("SELECT COUNT(*) c FROM op_logs").fetchone()["c"]
    return {
        "users": users,
        "new_today": new_today,
        "accounts": accs,
        "accounts_ok": acc_ok,
        "tasks": tasks,
        "ops_today": ops_today,
        "ops_total": ops_all,
    }


# ---------------------------------------------------------------- users
@router.get("/users")
def admin_users(q: str = "", limit: int = 100, admin: CurrentUser = Depends(require_admin)):
    limit = max(1, min(limit, 500))
    with db() as conn:
        sql = ("SELECT id,username,email,role,banned,created_at,last_login_at "
               "FROM users")
        args: list = []
        if q.strip():
            sql += " WHERE username LIKE ? OR email LIKE ?"
            args += ["%" + q.strip() + "%", "%" + q.strip() + "%"]
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()
        accs = conn.execute(
            "SELECT user_id, COUNT(*) c FROM bili_accounts GROUP BY user_id").fetchall()
    acc_map = {r["user_id"]: r["c"] for r in accs}
    return {"users": [{
        "id": r["id"],
        "username": r["username"],
        "email": r["email"],
        "role": r["role"],
        "accounts": acc_map.get(r["id"], 0),
        "banned": bool(r["banned"]),
        "created_at": _fmt(r["created_at"]),
        "last_login_at": _fmt(r["last_login_at"]),
    } for r in rows]}


class UserPatch(BaseModel):
    banned: bool | None = None


@router.post("/users/{uid}")
def admin_patch_user(uid: int, body: UserPatch, admin: CurrentUser = Depends(require_admin)):
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            raise HTTPException(404, "用户不存在")
        if body.banned is not None:
            conn.execute("UPDATE users SET banned=? WHERE id=?",
                         (1 if body.banned else 0, uid))
            if body.banned:
                conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            admin_audit(admin.id, "ban" if body.banned else "unban", str(uid))
    return {"ok": True}


# ---------------------------------------------------------------- logs
@router.get("/logs")
def admin_logs(limit: int = 200, admin: CurrentUser = Depends(require_admin)):
    limit = max(1, min(limit, 1000))
    with db() as conn:
        rows = conn.execute(
            "SELECT l.*, u.username FROM op_logs l LEFT JOIN users u ON u.id=l.user_id "
            "ORDER BY l.id DESC LIMIT ?", (limit,)).fetchall()
        audit = conn.execute(
            "SELECT a.*, u.username FROM audit_admin a LEFT JOIN users u ON u.id=a.admin_id "
            "ORDER BY a.id DESC LIMIT 100").fetchall()
    return {
        "logs": [{
            "id": r["id"], "user": r["username"] or "", "action": r["action"],
            "target": r["target"] or "", "result": r["result"],
            "code": r["code"], "created_at": _fmt(r["created_at"]),
        } for r in rows],
        "audit": [{
            "id": a["id"], "admin": a["username"] or str(a["admin_id"]),
            "action": a["action"], "target": a["target"] or "",
            "created_at": _fmt(a["created_at"]),
        } for a in audit],
    }


# ---------------------------------------------------------------- accounts
@router.get("/accounts")
def admin_accounts(limit: int = 300, admin: CurrentUser = Depends(require_admin)):
    limit = max(1, min(limit, 1000))
    with db() as conn:
        rows = conn.execute(
            "SELECT a.*, u.username FROM bili_accounts a LEFT JOIN users u ON u.id=a.user_id "
            "ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()
    return {"accounts": [{
        "id": r["id"], "owner": r["username"] or "",
        "mid": r["mid"], "uname": r["uname"], "fans": r["fans"], "videos": r["videos"],
        "coins": r["coins"], "level": r["level"], "status": r["status"],
        "cred_source": r["cred_source"], "proxy": r["proxy"] or "",
        "last_check": r["last_check"] or "",
        "created_at": _fmt(r["created_at"]),
    } for r in rows]}


# ---------------------------------------------------------------- settings
def _settings_view() -> dict:
    return {
        "app_name": app_settings.APP_NAME,
        "admin_path": app_settings.ADMIN_PATH,
        "register_open": True,
        "captcha_ttl": app_settings.CAPTCHA_TTL,
        "code_ttl": app_settings.CODE_TTL,
        "code_resend": app_settings.CODE_RESEND,
        "login_max_fail": app_settings.LOGIN_MAX_FAIL,
    }


@router.get("/settings")
def admin_get_settings(admin: CurrentUser = Depends(require_admin)):
    return {"settings": _settings_view()}


@router.post("/settings")
def admin_save_settings(body: dict, admin: CurrentUser = Depends(require_admin)):
    """运行时配置在 env 里；这里只记录管理员的操作审计。"""
    admin_audit(admin.id, "save_settings", json.dumps(body, ensure_ascii=False)[:200])
    return {"ok": True, "message": "设置已记录（运行时配置请改环境变量 .env）",
            "settings": _settings_view()}


# ---------------------------------------------------------------- notices
@router.get("/notices")
def admin_notices(limit: int = 100, admin: CurrentUser = Depends(require_admin)):
    limit = max(1, min(limit, 500))
    with db() as conn:
        rows = conn.execute(
            "SELECT n.*, u.username FROM notices n LEFT JOIN users u ON u.id=n.user_id "
            "ORDER BY n.id DESC LIMIT ?", (limit,)).fetchall()
    return {"notices": [{
        "id": r["id"], "user": r["username"] or "", "title": r["title"] or "",
        "body": r["body"] or "", "read": bool(r["read"]),
        "created_at": _fmt(r["created_at"]),
    } for r in rows]}


class NoticeIn(BaseModel):
    title: str = ""
    body: str = ""
    user_id: int | None = None       # 空 = 全体广播


@router.post("/notices")
def admin_send_notice(body: NoticeIn, admin: CurrentUser = Depends(require_admin)):
    if not body.title.strip():
        raise HTTPException(400, "请填写公告标题")
    now = int(time.time())
    with db() as conn:
        if body.user_id:
            targets = [body.user_id]
        else:
            targets = [r["id"] for r in conn.execute("SELECT id FROM users").fetchall()]
        for uid in targets:
            conn.execute(
                "INSERT INTO notices(user_id,title,body,read,created_at) VALUES(?,?,?,0,?)",
                (uid, body.title.strip(), body.body, now))
    admin_audit(admin.id, "notice:%d 人" % len(targets), body.title[:60])
    return {"ok": True, "sent": len(targets), "message": "已发送给 %d 人" % len(targets)}
