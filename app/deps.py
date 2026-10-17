"""Jinja2 环境与公共上下文。"""
from __future__ import annotations

from fastapi.templating import Jinja2Templates

from .config import BASE_DIR, settings

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["APP_NAME"] = settings.APP_NAME
templates.env.globals["ADMIN_PATH"] = settings.ADMIN_PATH
templates.env.globals["CSS_V"] = "20261018d"  # 与原站 /static/app.css?v= 版本号对齐
templates.env.filters["mb"] = lambda n: ("%g MB" % (n / 1024 if n >= 1024 else n)) if n >= 0 else "不限"


def render(request, name: str, **ctx):
    """Starlette>=0.29 的签名是 TemplateResponse(request, name, context)。"""
    ctx.setdefault("request", request)
    return templates.TemplateResponse(request, name, ctx)
