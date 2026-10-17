"""认证相关：计算验证码、邮箱验证码、注册、登录、异地登录验证、找回密码。

与原站前端契约保持一致：
POST /api/auth/login   {username,password,captcha,captcha_token} ->
    成功 {role,uid} / 需要异地验证 {need_verify:true,ticket,email,message}
"""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import client_ip, create_session, destroy_session
from ..config import settings
from ..db import db
from ..mailer import send_code
from ..security import (
    code_fingerprint,
    hash_password,
    make_captcha,
    make_email_code,
    new_invite_code,
    new_token,
    password_problem,
    verify_captcha_token,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{6,20}$")


# ---------------- 请求体 ----------------
class CaptchaIn(BaseModel):
    captcha: str = ""
    captcha_token: str = ""


class SendCodeIn(CaptchaIn):
    email: str
    scene: str = "register"


class RegisterIn(BaseModel):
    username: str
    password: str
    email: str
    code: str
    invite: str = ""


class LoginIn(CaptchaIn):
    username: str
    password: str


class VerifyIn(BaseModel):
    ticket: str
    code: str


class ResetIn(BaseModel):
    email: str
    code: str
    password: str


# ---------------- 工具 ----------------
def issue_captcha(conn) -> dict:
    q, ans = make_captcha()
    token = new_token(24)
    now = int(time.time())
    conn.execute(
        "INSERT INTO captchas(token,answer,expires_at,used) VALUES(?,?,?,0)",
        (token, ans, now + settings.CAPTCHA_TTL),
    )
    conn.execute("DELETE FROM captchas WHERE expires_at < ?", (now - 3600,))
    return {"q": q, "token": token}


def consume_code(conn, email: str, scene: str, code: str) -> str | None:
    """校验邮箱验证码，成功返回 None，失败返回错误文案。"""
    now = int(time.time())
    row = conn.execute(
        "SELECT * FROM email_codes WHERE email=? AND scene=? AND used=0 AND expires_at>? "
        "ORDER BY id DESC LIMIT 1",
        (email, scene, now),
    ).fetchone()
    if not row:
        return "验证码已过期或不存在，请重新获取"
    if code_fingerprint((code or "").strip(), email) != row["code_hash"]:
        return "验证码错误"
    conn.execute("UPDATE email_codes SET used=1 WHERE id=?", (row["id"],))
    return None


def recent_ip_known(conn, user_id: int, ip: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM known_ips WHERE user_id=? AND ip=?", (user_id, ip)
    ).fetchone() is not None


def remember_ip(conn, user_id: int, ip: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO known_ips(user_id,ip,created_at) VALUES(?,?,?)",
        (user_id, ip, int(time.time())),
    )


def profile(conn, user_id: int) -> dict:
    u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return {
        "id": u["id"], "username": u["username"], "email": u["email"],
        "role": u["role"], "plan": u["plan"], "plan_until": u["plan_until"],
        "traffic_mb": u["traffic_mb"], "invite_code": u["invite_code"],
    }


# ---------------- 验证码 ----------------
@router.get("/captcha/needed")
def captcha_needed(request: Request):
    """登录连续成功 N 次后可以要求前端隐藏计算题（原站固定返回 need:true）。"""
    fails = int(request.cookies.get("bp_capfail", "0") or 0)
    return {"need": True if fails >= 0 else False}


@router.get("/captcha")
def captcha():
    with db() as conn:
        return issue_captcha(conn)


# ---------------- 邮箱验证码 ----------------
@router.post("/send_code")
def send_register_code(body: SendCodeIn):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "邮箱格式不正确")
    with db() as conn:
        ok, err = verify_captcha_token(conn, body.captcha_token, body.captcha)
        if not ok:
            raise HTTPException(400, err)

        now = int(time.time())
        last = conn.execute(
            "SELECT created_at FROM email_codes WHERE email=? AND scene=? ORDER BY id DESC LIMIT 1",
            (email, body.scene),
        ).fetchone()
        if last and now - last["created_at"] < settings.CODE_RESEND:
            raise HTTPException(429, "发送太频繁，请 %d 秒后再试"
                                % (settings.CODE_RESEND - (now - last["created_at"])))

        if body.scene == "register" and conn.execute(
            "SELECT 1 FROM users WHERE email=?", (email,)
        ).fetchone():
            raise HTTPException(400, "该邮箱已注册，请直接登录")

        code = make_email_code()
        conn.execute(
            "INSERT INTO email_codes(email,scene,code_hash,expires_at,used,created_at) "
            "VALUES(?,?,?,?,0,?)",
            (email, body.scene, code_fingerprint(code, email),
             now + settings.CODE_TTL, now),
        )
    sent, info = send_code(email, code, body.scene)
    if not sent:
        raise HTTPException(500, info)
    return {"ok": True, "message": "验证码已发送到邮箱"}


# ---------------- 注册 ----------------
@router.post("/register")
def register(body: RegisterIn):
    username = body.username.strip()
    email = body.email.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "账号至少 6 位，仅限字母、数字、下划线或中文")
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(400, problem)
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "邮箱格式不正确")

    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise HTTPException(400, "用户名已被占用")
        # 一人一号：同一邮箱只能注册一次
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(400, "该邮箱已注册，每人只能注册一个账号")

        err = consume_code(conn, email, "register", body.code.strip())
        if err:
            raise HTTPException(400, err)

        invite = (body.invite or "").strip()
        inviter = None
        if invite:
            inviter = conn.execute(
                "SELECT id, traffic_mb FROM users WHERE invite_code=?", (invite,)
            ).fetchone()
            if not inviter:
                raise HTTPException(400, "邀请码不存在")

        bonus = settings.INVITE_SELF_BONUS_MB
        now = int(time.time())
        my_code = None
        for _ in range(6):  # 极小概率碰撞，重试几次
            cand = new_invite_code()
            if not conn.execute("SELECT 1 FROM users WHERE invite_code=?", (cand,)).fetchone():
                my_code = cand
                break
        cur = conn.execute(
            "INSERT INTO users(username,email,password_hash,role,plan,plan_until,traffic_mb,"
            "invite_code,invited_by,banned,created_at) VALUES(?,?,?,'user','free',0,?,?,?,0,?)",
            (username, email, hash_password(body.password), bonus, my_code, invite or None, now),
        )
        user_id = cur.lastrowid
        if inviter:
            conn.execute("UPDATE users SET traffic_mb=traffic_mb+? WHERE id=?",
                         (settings.INVITE_REF_BONUS_MB, inviter["id"]))
        return {"ok": True, "id": user_id, "invite_code": my_code,
                "message": "注册成功"}


# ---------------- 登录 ----------------
@router.post("/login")
def login(body: LoginIn, request: Request):
    ip = client_ip(request)
    ident = body.username.strip()
    email_like = ident.lower() if EMAIL_RE.match(ident) else None

    with db() as conn:
        u = None
        if email_like:
            u = conn.execute("SELECT * FROM users WHERE email=?", (email_like,)).fetchone()
        if u is None:
            u = conn.execute(
                "SELECT * FROM users WHERE username=?", (ident,)
            ).fetchone()
        if u is None:
            raise HTTPException(400, "用户名或密码错误")
        if u["banned"]:
            raise HTTPException(403, "账号已被封禁")

        # 失败计数：同 IP 过多则强制要求验证码
        fails = conn.execute(
            "SELECT COUNT(*) c FROM op_logs WHERE action='login_fail' AND target=? AND created_at>?",
            (ip, int(time.time()) - settings.LOGIN_FAIL_WINDOW),
        ).fetchone()["c"]
        if fails >= settings.LOGIN_MAX_FAIL:
            raise HTTPException(429, "登录失败次数过多，请稍后再试")

        if not verify_password(body.password, u["password_hash"]):
            conn.execute(
                "INSERT INTO op_logs(user_id,action,target,result,created_at) "
                "VALUES(?, 'login_fail', ?, 'fail', ?)",
                (u["id"], ip, int(time.time())),
            )
            raise HTTPException(400, "用户名或密码错误")

        ok, err = verify_captcha_token(conn, body.captcha_token, body.captcha)
        if not ok:
            raise HTTPException(400, err)

        # 异地登录：新 IP 需要邮箱验证（先出响应，会话在验证通过后创建）
        if not recent_ip_known(conn, u["id"], ip):
            ticket = new_token(24)
            conn.execute(
                "INSERT INTO login_tickets(ticket,user_id,ip,expires_at,verified) "
                "VALUES(?,?,?,?,0)",
                (ticket, u["id"], ip, int(time.time()) + 600),
            )
            masked = u["email"][:2] + "***" + u["email"][u["email"].find("@"):] \
                if u["email"] else "你的邮箱"
            return {
                "need_verify": True,
                "ticket": ticket,
                "email": masked,
                "message": "检测到新 IP 登录，请点击「发送验证码」完成邮箱验证",
            }

        remember_ip(conn, u["id"], ip)
        conn.execute("UPDATE users SET last_login_at=?, last_login_ip=? WHERE id=?",
                     (int(time.time()), ip, u["id"]))
        prof = profile(conn, u["id"])
        role = u["role"]

    # 事务外再建会话，避免长事务内二次开库
    sid = create_session(u["id"], ip, request.headers.get("user-agent", ""))
    return _with_cookie({"ok": True, "role": role, "uid": u["id"], "profile": prof}, sid)


def _with_cookie(payload: dict, sid: str):
    from fastapi.responses import JSONResponse

    r = JSONResponse(payload)
    r.set_cookie(
        settings.SESSION_COOKIE, sid,
        max_age=settings.SESSION_MAX_AGE, httponly=True, samesite="lax",
        secure=not settings.DEBUG, path="/",
    )
    return r


@router.post("/login_verify/resend")
def login_verify_resend(body: dict):
    """重发异地登录验证码。前端 body: {ticket, captcha, captcha_token}。"""
    ticket = str(body.get("ticket", "")).strip()
    if not ticket:
        raise HTTPException(400, "缺少登录票据，请重新登录")
    cap = str(body.get("captcha", "")).strip()
    tok = str(body.get("captcha_token", "")).strip()
    with db() as conn:
        t = conn.execute("SELECT * FROM login_tickets WHERE ticket=?", (ticket,)).fetchone()
        if not t or t["expires_at"] < int(time.time()):
            raise HTTPException(400, "登录票据已过期，请重新登录")
        ok, err = verify_captcha_token(conn, tok, cap)
        if not ok:
            raise HTTPException(400, err)
        u = conn.execute("SELECT id,email FROM users WHERE id=?", (t["user_id"],)).fetchone()
        code = make_email_code()
        now = int(time.time())
        conn.execute(
            "INSERT INTO email_codes(email,scene,code_hash,expires_at,used,created_at) "
            "VALUES(?,?,?,?,0,?)",
            (u["email"], "verify", code_fingerprint(code, u["email"]),
             now + settings.CODE_TTL, now),
        )
    sent, info = send_code(u["email"], code, "verify")
    if not sent:
        raise HTTPException(500, info)
    masked = u["email"][:2] + "***" + u["email"][u["email"].find("@"):]
    return {"ok": True, "email": masked, "message": "验证码已发送到 %s" % masked}


@router.post("/login_verify")
def login_verify(body: dict, request: Request):
    ticket = str(body.get("ticket", "")).strip()
    code = str(body.get("code", "")).strip()
    if not ticket:
        raise HTTPException(400, "缺少登录票据")
    ip = client_ip(request)
    with db() as conn:
        t = conn.execute("SELECT * FROM login_tickets WHERE ticket=?", (ticket,)).fetchone()
        if not t or t["expires_at"] < int(time.time()):
            raise HTTPException(400, "登录票据已过期，请重新登录")
        u = conn.execute("SELECT * FROM users WHERE id=?", (t["user_id"],)).fetchone()
        err = consume_code(conn, u["email"], "verify", code)
        if err:
            raise HTTPException(400, err)
        conn.execute("UPDATE login_tickets SET verified=1 WHERE ticket=?", (ticket,))
        remember_ip(conn, u["id"], ip)
        prof = profile(conn, u["id"])
        role = u["role"]
    sid = create_session(u["id"], ip, request.headers.get("user-agent", ""))
    return _with_cookie({"ok": True, "role": role, "uid": u["id"], "profile": prof}, sid)


# ---------------- 找回密码 ----------------
@router.post("/forgot")
def forgot(body: SendCodeIn):
    email = body.email.strip().lower()
    with db() as conn:
        ok, err = verify_captcha_token(conn, body.captcha_token, body.captcha)
        if not ok:
            raise HTTPException(400, err)
        if not conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(400, "该邮箱未注册")
        code = make_email_code()
        now = int(time.time())
        conn.execute(
            "INSERT INTO email_codes(email,scene,code_hash,expires_at,used,created_at) "
            "VALUES(?,?,?,?,0,?)",
            (email, "reset", code_fingerprint(code, email), now + settings.CODE_TTL, now),
        )
    sent, info = send_code(email, code, "reset")
    if not sent:
        raise HTTPException(500, info)
    return {"ok": True, "message": "验证码已发送到邮箱"}


@router.post("/reset")
def reset(body: ResetIn):
    email = body.email.strip().lower()
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(400, problem)
    with db() as conn:
        err = consume_code(conn, email, "reset", body.code.strip())
        if err:
            raise HTTPException(400, err)
        conn.execute("UPDATE users SET password_hash=? WHERE email=?",
                     (hash_password(body.password), email))
        conn.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE email=?)",
                     (email,))
    return {"ok": True, "message": "密码已重置，请用新密码登录"}


# ---------------- 退出 / 当前用户 ----------------
@router.post("/logout")
def logout(request: Request):
    from fastapi.responses import JSONResponse

    sid = request.cookies.get(settings.SESSION_COOKIE, "")
    destroy_session(sid)
    r = JSONResponse({"ok": True})
    r.delete_cookie(settings.SESSION_COOKIE, path="/")
    return r
