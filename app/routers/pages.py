"""页面路由。模板与原站一一对应，未登录跳 /login。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import page_guard
from ..config import PLAN_LIMITS, settings
from ..deps import render
from ..quota import effective_plan

router = APIRouter(tags=["pages"])


@router.get("/")
def landing(request: Request):
    return render(request, "index.html")


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@router.get("/register")
def register_page(request: Request):
    ref = request.query_params.get("ref", "")
    return render(request, "register.html", ref=ref)







@router.get("/ai")
def ai_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "ai.html", user=user, plan=effective_plan(user))

@router.get("/messages")
def messages_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "messages.html", user=user, plan=effective_plan(user))

@router.get("/publish")
def publish_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    plan = effective_plan(user)
    return render(request, "publish.html", user=user, plan=plan, limits=PLAN_LIMITS[plan])

@router.get("/scheduler")
def scheduler_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    plan = effective_plan(user)
    return render(request, "scheduler.html", user=user, plan=plan, limits=PLAN_LIMITS[plan])

@router.get("/tasks")
def tasks_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    plan = effective_plan(user)
    return render(request, "tasks.html", user=user, plan=plan, limits=PLAN_LIMITS[plan])


@router.get("/logs")
def logs_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "logs.html", user=user, plan=effective_plan(user))

@router.get("/accounts")
def accounts_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "accounts.html", user=user, plan=effective_plan(user))


@router.get("/accounts/add")
def accounts_add_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "accounts_add.html", user=user, plan=effective_plan(user))


@router.get("/notices")
def notices_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "notices.html", user=user, plan=effective_plan(user))


@router.get("/me")
def me_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "me.html", user=user, plan=effective_plan(user))


@router.get("/trend")
def trend_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "trend.html", user=user, plan=effective_plan(user))

@router.get("/templates")
def templates_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "templates.html", user=user, plan=effective_plan(user))


@router.get("/web")
def web_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "web.html", user=user, plan=effective_plan(user))


@router.get("/dashboard")
def dashboard_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "dashboard.html", user=user, plan=effective_plan(user),
                  limits=PLAN_LIMITS[effective_plan(user)])



@router.get("/settings")
def settings_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "settings.html", user=user, plan=effective_plan(user))


@router.get("/library")
def library_page(request: Request, user=Depends(page_guard())):
    if isinstance(user, RedirectResponse):
        return user
    plan = effective_plan(user)
    return render(request, "library.html", user=user, plan=plan, limits=PLAN_LIMITS[plan])


@router.get("/" + settings.ADMIN_PATH, name="admin_page")
def admin_page(request: Request, user=Depends(page_guard(admin_only=True))):
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "admin/overview.html", user=user)
