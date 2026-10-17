"""补全缺失表 + 挂载新路由。给多个模块共用，可重复执行。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

TABLES = """
CREATE TABLE IF NOT EXISTS interact_tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'interact',
    cron       TEXT NOT NULL,
    config     TEXT NOT NULL DEFAULT '{}',
    enabled    INTEGER NOT NULL DEFAULT 1,
    last_run   INTEGER NOT NULL DEFAULT 0,
    next_run   INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'interact',
    config     TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

"""


def patch(path: Path, old: str, new: str) -> bool:
    txt = path.read_text(encoding="utf-8")
    if old not in txt:
        print("  skip:", path.name, "|", old[:50].replace("\n", " "))
        return False
    path.write_text(txt.replace(old, new, 1), encoding="utf-8")
    print("  patched:", path.name)
    return True


def main() -> None:
    db = APP / "db.py"
    if "interact_tasks" not in db.read_text(encoding="utf-8"):
        patch(db, "CREATE TABLE IF NOT EXISTS audit_admin (",
              TABLES + "CREATE TABLE IF NOT EXISTS audit_admin (")
    else:
        print("  interact_tasks 已存在")

    main_py = APP / "main.py"
    txt = main_py.read_text(encoding="utf-8")
    txt = re.sub(
        r"from \.routers import \([^)]*\)",
        "from .routers import (accounts, ai, auth_api, dashboard, interact, library,\n"
        "                      like, me_api, misc, pages, proxy, tasks_api,\n"
        "                      templates_api, vip_api)",
        txt, count=1,
    )
    txt = re.sub(
        r"for r in \([^)]*\):\n    app\.include_router\(r\)",
        "for r in (auth_api.router, accounts.router, proxy.router, interact.router,\n"
        "          templates_api.router, like.router, tasks_api.router, ai.router,\n"
        "          dashboard.router, library.router, vip_api.router, me_api.router,\n"
        "          misc.router, misc.admin_router, pages.router):\n"
        "    app.include_router(r)",
        txt, count=1,
    )
    main_py.write_text(txt, encoding="utf-8")
    print("  main.py 路由已重排")

    pages = APP / "routers" / "pages.py"
    pv = pages.read_text(encoding="utf-8")
    if '"/tasks"' not in pv:
        add = '''
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

'''
        pv = pv.replace('@router.get("/accounts")', add + '@router.get("/accounts")', 1)
        pages.write_text(pv, encoding="utf-8")
        print("  pages.py 增加 /tasks /logs")


if __name__ == "__main__":
    main()
