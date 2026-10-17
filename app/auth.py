"""会话 / 登录态 / 角色守卫。所有鉴权错误统一返回 JSON。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request

from .config import settings
from .db import db
from .security import new_token


@dataclass
class CurrentUser:
    id: int
    username: str
    email: str
    role: str
    plan: str
    plan_until: int
    traffic_mb: int
    sid: str
    plan_tier: str = ""          # month | quarter | year（决定 AI 操作间隔）

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_vip(self) -> bool:
        if self.plan != "vip":
            return False
        return self.plan_until == 0 or self.plan_until > int(time.time())


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def create_session(user_id: int, ip: str, ua: str = "") -> str:
    sid = new_token()
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions(sid,user_id,ip,ua,created_at,expires_at) VALUES(?,?,?,?,?,?)",
            (sid, user_id, ip, ua[:255], now, now + settings.SESSION_MAX_AGE),
        )
    return sid


def destroy_session(sid: str) -> None:
    if not sid:
        return
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE sid=?", (sid,))


def _load_user(sid: str | None) -> CurrentUser | None:
    if not sid:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT s.sid, s.expires_at, u.* FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.sid=?", (sid,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < int(time.time()):
            conn.execute("DELETE FROM sessions WHERE sid=?", (sid,))
            return None
        if row["banned"]:
            return None
        return CurrentUser(
            id=row["id"], username=row["username"], email=row["email"], role=row["role"],
            plan=row["plan"], plan_until=row["plan_until"], traffic_mb=row["traffic_mb"],
            sid=sid,
            plan_tier=(row["plan_tier"] if "plan_tier" in row.keys() else "") or "",
        )


def current_user_optional(request: Request) -> CurrentUser | None:
    return _load_user(request.cookies.get(settings.SESSION_COOKIE))


def require_user(request: Request) -> CurrentUser:
    user = _load_user(request.cookies.get(settings.SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="无权限")
    return user


def page_guard(admin_only: bool = False) -> Callable[[Request], CurrentUser | None]:
    """页面级守卫：模板页未登录直接 307 到 /login，而不是弹 JSON。"""
    from fastapi.responses import RedirectResponse

    def dep(request: Request):
        user = _load_user(request.cookies.get(settings.SESSION_COOKIE))
        if user is None:
            return RedirectResponse("/login", status_code=307)
        if admin_only and not user.is_admin:
            return RedirectResponse("/", status_code=307)
        return user

    return dep


def log_op(user_id: int, action: str, target: str = "", result: str = "ok",
           code: int | None = None, account_id: int | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO op_logs(user_id,account_id,action,target,result,code,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user_id, account_id, action, target, result, code, int(time.time())),
        )


def admin_audit(admin_id: int, action: str, target: str = "") -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_admin(admin_id,action,target,created_at) VALUES(?,?,?,?)",
            (admin_id, action, target, int(time.time())),
        )
