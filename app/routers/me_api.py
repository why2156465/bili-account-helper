"""通知 / 公告 / 我的 —— 对齐原站顶栏轮询的接口。

    GET /api/notices/count   -> {"unread": N}       顶栏红点，每 60 秒轮询
    GET /api/me/notice       -> {"enabled","title","text","qq_group","qq_url"}  首次进入弹公告
    GET /api/me/settings     -> 设置页
    POST /api/me/password    -> 改密
    POST /api/me/email_code  -> 发邮箱验证码
    POST /api/me/email       -> 改邮箱
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import settings

from ..auth import CurrentUser, require_user
from ..db import db
from ..mailer import send_code
from ..security import (
    code_fingerprint, hash_password, make_email_code, password_problem,
    verify_captcha_token, verify_password,
)

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/notices/count")
def notices_count(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM notices WHERE user_id=? AND read=0",
                         (user.id,)).fetchone()["c"]
    return {"unread": int(n)}


@router.get("/notices")
def notices_list(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT id,title,body,read,created_at FROM notices WHERE user_id=? "
            "ORDER BY id DESC LIMIT 100", (user.id,)).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/notices/read")
def notices_read(body: dict, user: CurrentUser = Depends(require_user)):
    nid = body.get("id")
    with db() as conn:
        if nid:
            conn.execute("UPDATE notices SET read=1 WHERE id=? AND user_id=?", (nid, user.id))
        else:
            conn.execute("UPDATE notices SET read=1 WHERE user_id=?", (user.id,))
    return {"ok": True}


@router.get("/me/notice")
def me_notice(user: CurrentUser = Depends(require_user)):
    """原站首次进入会在 sessionStorage 记 notice_ok，然后请求这个接口决定是否弹窗。"""
    with db() as conn:
        row = conn.execute(
            "SELECT title,body FROM notices WHERE user_id=? AND read=0 ORDER BY id DESC LIMIT 1",
            (user.id,)).fetchone()
    if not row:
        return {"enabled": False, "title": "", "text": "", "qq_group": "", "qq_url": ""}
    return {"enabled": True, "title": row["title"] or "通知",
            "text": row["body"] or "", "qq_group": "", "qq_url": ""}


@router.get("/me/settings")
def me_settings(user: CurrentUser = Depends(require_user)):
    """设置页所需的全部状态。

    注意：代理不挂在 users 表上——原站是「我的代理列表」(proxy_pool) +
    每个账号各自指定（bili_accounts.proxy）。所以这里返回列表与分配情况，
    不再去读一个根本不存在的 users.proxy 列。
    """
    with db() as conn:
        row = conn.execute(
            "SELECT email, verify_login, email_verify FROM users WHERE id=?", (user.id,)
        ).fetchone()
        proxies = conn.execute(
            "SELECT id,name,url FROM proxy_pool WHERE user_id=? ORDER BY id", (user.id,)
        ).fetchall()
        assigned = conn.execute(
            "SELECT COUNT(*) c FROM bili_accounts WHERE user_id=? AND COALESCE(proxy,'')<>''",
            (user.id,)).fetchone()["c"]
        total_acc = conn.execute(
            "SELECT COUNT(*) c FROM bili_accounts WHERE user_id=?", (user.id,)).fetchone()["c"]

    keys = row.keys()
    return {
        "email": row["email"] or "",
        "notify": bool(row["email_verify"]) if "email_verify" in keys else True,
        "email_verify": bool(row["verify_login"]) if "verify_login" in keys else True,
        "policy": "auto" if proxies else "off",
        "pool_size": len(proxies),
        "proxies": [{"id": p["id"], "name": p["name"] or p["url"], "url": p["url"]}
                    for p in proxies],
        "assigned_accounts": int(assigned),
        "total_accounts": int(total_acc),
        "mail_enabled": not settings.MAIL_DEV_PRINT or bool(settings.SMTP_HOST),
    }


@router.post("/me/settings")
def save_me_settings(body: dict, user: CurrentUser = Depends(require_user)):
    """通知偏好落库（原站设置页底部的「保存」按钮）。

    页面上的两个开关对应两列：
      notify       -> email_verify   （账号失效时给我发邮件）
      email_verify -> verify_login   （异地登录邮箱安全验证）
    """
    fields, values = [], []
    if "notify" in body:
        fields.append("email_verify=?")
        values.append(1 if body.get("notify") else 0)
    if "email_verify" in body:
        fields.append("verify_login=?")
        values.append(1 if body.get("email_verify") else 0)
    if not fields:
        raise HTTPException(400, "没有要保存的选项")
    values.append(user.id)
    with db() as conn:
        conn.execute("UPDATE users SET %s WHERE id=?" % ",".join(fields), values)
    return {"ok": True, "message": "已保存"}


class PwIn(BaseModel):
    old: str = ""
    password: str = ""
    captcha: str = ""
    captcha_token: str = ""


@router.post("/me/password")
def change_password(body: PwIn, user: CurrentUser = Depends(require_user)):
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(400, problem)
    with db() as conn:
        ok, err = verify_captcha_token(conn, body.captcha_token, body.captcha)
        if not ok:
            raise HTTPException(400, err)
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user.id,)).fetchone()
        if not verify_password(body.old, row["password_hash"]):
            raise HTTPException(400, "原密码不正确")
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (hash_password(body.password), user.id))
        # 改密后踢掉其它会话
        conn.execute("DELETE FROM sessions WHERE user_id=? AND sid<>?", (user.id, user.sid))
    return {"ok": True, "message": "密码已修改"}


class EmailIn(BaseModel):
    email: str = ""
    code: str = ""


@router.post("/me/email_code")
def email_code(body: dict, user: CurrentUser = Depends(require_user)):
    email = str(body.get("email", "")).strip().lower()
    if "@" not in email:
        raise HTTPException(400, "邮箱格式不正确")
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=? AND id<>?",
                        (email, user.id)).fetchone():
            raise HTTPException(400, "该邮箱已被其它账号使用")
        now = int(time.time())
        code = make_email_code()
        conn.execute(
            "INSERT INTO email_codes(email,scene,code_hash,expires_at,used,created_at) "
            "VALUES(?,?,?,?,0,?)",
            (email, "change", code_fingerprint(code, email), now + 600, now),
        )
    sent, info = send_code(email, code, "reset")
    if not sent:
        raise HTTPException(500, info)
    return {"ok": True, "message": "验证码已发送"}


@router.post("/me/email")
def change_email(body: EmailIn, user: CurrentUser = Depends(require_user)):
    email = body.email.strip().lower()
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM email_codes WHERE email=? AND scene='change' AND used=0 "
            "AND expires_at>? AND code_hash=? ORDER BY id DESC LIMIT 1",
            (email, int(time.time()), code_fingerprint(body.code.strip(), email)),
        ).fetchone()
        if not row:
            raise HTTPException(400, "验证码错误或已过期")
        conn.execute("UPDATE email_codes SET used=1 WHERE id=?", (row["id"],))
        conn.execute("UPDATE users SET email=? WHERE id=?", (email, user.id))
    return {"ok": True, "message": "邮箱已更新"}
