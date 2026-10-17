"""FastAPI 应用装配 + 安全响应头。"""
from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, settings
from .db import init_db
from .routers import (accounts, admin_api, ai_api, auth_api, dashboard_api, interact, library,
                      like, logs_api, me_api, messages, misc, pages, proxy, publish, publish_upload,
                      scheduler_api,
                      fills, templates_api, trend_api, web_api)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.WORKER_ENABLED:
        from .worker import main as worker_main

        threading.Thread(target=worker_main, daemon=True, name="bp-worker").start()
    yield


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("X-XSS-Protection", "1; mode=block")
    h.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store, no-cache, must-revalidate")
    return resp


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/") or "." in request.url.path.rsplit("/", 1)[-1]:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/", status_code=307)


for r in (auth_api.router, accounts.router, proxy.router, interact.router,
          scheduler_api.router, templates_api.router, publish.router, publish_upload.router,
          messages.router, like.router,
          logs_api.router, ai_api.router,
          dashboard_api.router, library.router, me_api.router,
          misc.router, admin_api.router, web_api.router, trend_api.router, fills.router, pages.router):
    app.include_router(r)


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": int(time.time())}
