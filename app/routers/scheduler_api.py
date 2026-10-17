"""定时任务（scheduler）—— 对齐原站 /api/tasks + /api/automation 契约。

任务 CRUD（从 scheduler.html 内联 JS 读出）：
    GET    /api/tasks                     -> {tasks:[{id,name,kind,cron,config,enabled,last_run_at,next_run_at}]}
    POST   /api/tasks  {name,cron,kind,config}
    PUT    /api/tasks/{id}/config {name,cron,kind,config}   ← 编辑时走 PUT 而不是 PATCH
    PATCH  /api/tasks/{id} {enabled:0|1}                    ← 启停
    DELETE /api/tasks/{id}

自动化规则（自动私信 / AI 自动回复 / 评论回复）：
    GET  /api/automation             -> {rules:[...]}
    GET  /api/automation/policy      -> {policy:{autodm,autoreply,autocomment,...},is_vip,op_interval,...}
    POST /api/automation             -> 新建规则
    PUT  /api/automation/{id}
    DELETE /api/automation/{id}
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, require_user
from ..config import PLAN_LIMITS
from ..cron import CronError, next_run
from ..db import db
from ..quota import effective_plan

router = APIRouter(prefix="/api", tags=["scheduler"])


# ---------------------------------------------------------------- 任务
class TaskIn(BaseModel):
    name: str = ""
    cron: str = ""
    kind: str = "interact"
    config: dict = {}
    enabled: int = 1


def _task_view(r) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "kind": r["kind"],
        "cron": r["cron"],
        "config": json.loads(r["config"] or "{}"),
        "enabled": bool(r["enabled"]),
        "last_run_at": r["last_run"] or "",
        "next_run_at": (time.strftime("%Y-%m-%d %H:%M", time.localtime(r["next_run"]))
                        if r["next_run"] else ""),
        "next_run_ts": r["next_run"],
        "created_at": r["created_at"],
    }


@router.get("/tasks")
def list_tasks(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM interact_tasks WHERE user_id=? ORDER BY id DESC", (user.id,)
        ).fetchall()
    return {"tasks": [_task_view(r) for r in rows]}


@router.post("/tasks")
def create_task(body: TaskIn, user: CurrentUser = Depends(require_user)):
    if not body.name.strip():
        raise HTTPException(400, "请填写任务名称")
    if not body.cron.strip():
        raise HTTPException(400, "请设置执行频率")
    try:
        nr = next_run(body.cron)
    except (CronError, ValueError) as exc:
        raise HTTPException(400, "定时表达式无效：%s" % exc)
    if not nr:
        raise HTTPException(400, "该表达式一年内不会触发")

    limit = PLAN_LIMITS[effective_plan(user)]["cron_tasks"]
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM interact_tasks WHERE user_id=?",
                         (user.id,)).fetchone()["c"]
        if limit >= 0 and n >= limit:
            raise HTTPException(403, "定时任务数量已达上限（%d 个），开通会员可解锁更多" % limit)
        cur = conn.execute(
            "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,created_at,next_run) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (user.id, body.name.strip(), body.kind, body.cron.strip(),
             json.dumps(body.config, ensure_ascii=False), 1 if body.enabled else 0,
             int(time.time()), nr),
        )
    return {"ok": True, "id": cur.lastrowid, "next_run_ts": nr}


@router.put("/tasks/{task_id}/config")
def update_task(task_id: int, body: TaskIn, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        row = conn.execute("SELECT id FROM interact_tasks WHERE id=? AND user_id=?",
                           (task_id, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        nr = 0
        if body.cron.strip():
            try:
                nr = next_run(body.cron)
            except (CronError, ValueError) as exc:
                raise HTTPException(400, "定时表达式无效：%s" % exc)
        conn.execute(
            "UPDATE interact_tasks SET name=?, kind=?, cron=?, config=?, next_run=? WHERE id=?",
            (body.name.strip() or "未命名任务", body.kind, body.cron.strip(),
             json.dumps(body.config, ensure_ascii=False), nr, task_id),
        )
    return {"ok": True, "next_run_ts": nr}


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: dict, user: CurrentUser = Depends(require_user)):
    """原站启停只用 PATCH {enabled}。"""
    if "enabled" not in body:
        raise HTTPException(400, "缺少 enabled")
    enabled = 1 if body.get("enabled") else 0
    with db() as conn:
        row = conn.execute("SELECT cron FROM interact_tasks WHERE id=? AND user_id=?",
                           (task_id, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        nr = next_run(row["cron"]) if (enabled and row["cron"]) else 0
        conn.execute("UPDATE interact_tasks SET enabled=?, next_run=? WHERE id=?",
                     (enabled, nr, task_id))
    return {"ok": True, "enabled": bool(enabled)}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        conn.execute("DELETE FROM interact_tasks WHERE id=? AND user_id=?", (task_id, user.id))
    return {"ok": True}


@router.post("/tasks/{task_id}/run")
def run_task_now(task_id: int, user: CurrentUser = Depends(require_user)):
    """手动触发一个定时任务：投稿走投稿执行器，互赞走互赞执行器。"""
    from .interact import start_from_cfg

    with db() as conn:
        row = conn.execute("SELECT * FROM interact_tasks WHERE id=? AND user_id=?",
                           (task_id, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        cfg = json.loads(row["config"] or "{}")
        conn.execute("UPDATE interact_tasks SET last_run=? WHERE id=?",
                     (int(time.time()), task_id))

    if row["kind"] == "upload":
        from .publish_upload import RunUploadIn, run_upload

        return run_upload(RunUploadIn(task_id=task_id, config=cfg), user)

    if row["kind"] != "interact":
        raise HTTPException(400, "该类型任务暂不支持手动触发")

    # 互赞任务必须同时有 actions 和 targets，否则给出可读原因而不是让执行器崩
    if not (cfg.get("actions") or {}):
        raise HTTPException(400, "这个任务没有配置动作，去「编辑」里勾上至少一个")
    if not (cfg.get("targets") or []):
        raise HTTPException(400, "这个任务没有配置目标，去「编辑」里加上至少一个 UID")
    return start_from_cfg(user, cfg)


# ---------------------------------------------------------------- 自动化规则
POLICY_LABEL = {
    "autodm": "自动私信",
    "autoreply": "AI 自动回复",
    "autocomment": "评论自动回复",
}


def _policy_for(user, kind: str) -> dict:
    plan = effective_plan(user)
    lim = PLAN_LIMITS[plan]
    is_vip = plan == "vip"
    interval = 60 if not is_vip else (50 if kind != "autoreply" else 30)
    return {
        "tier": plan,
        "min_interval": interval,
        "min_interval_sec": 30,
        "max_count": 1 if not is_vip else 20,
        "allowed": True,
        "label": POLICY_LABEL.get(kind, kind),
    }


@router.get("/automation/policy")
def automation_policy(user: CurrentUser = Depends(require_user)):
    plan = effective_plan(user)
    lim = PLAN_LIMITS[plan]
    return {
        "policy": {k: _policy_for(user, k) for k in POLICY_LABEL},
        "is_vip": plan == "vip",
        "op_interval": lim["ai_action_interval"],
        "unlock_interval": plan == "vip",
        "ai_concurrent": lim["ai_task_max_minutes"] and (1 if plan != "vip" else 10),
        "caps": {
            "ai_daily": lim["ai_runs_per_day"],
            "ai_task_min": lim["ai_task_max_minutes"],
            "ai_conc": 1 if plan != "vip" else 10,
            "mon_min": lim["monitor_interval_min"],
            "op_sec": lim["ai_action_interval"],
        },
    }


class RuleIn(BaseModel):
    kind: str = "autodm"
    account_id: int | None = None
    interval: int = 60
    config: dict = {}
    enabled: int = 1


def _rule_view(r) -> dict:
    return {
        "id": r["id"],
        "kind": r["kind"],
        "account_id": r["account_id"],
        "interval": r["interval"],
        "config": json.loads(r["config"] or "{}"),
        "enabled": bool(r["enabled"]),
        "created_at": r["created_at"],
        "last_run": r["last_run"] or "",
    }


@router.get("/automation")
def list_rules(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM automation_rules WHERE user_id=? ORDER BY id DESC", (user.id,)
        ).fetchall()
    return {"rules": [_rule_view(r) for r in rows]}


@router.post("/automation")
def create_rule(body: RuleIn, user: CurrentUser = Depends(require_user)):
    if body.kind not in POLICY_LABEL:
        raise HTTPException(400, "未知的自动化类型")
    pol = _policy_for(user, body.kind)
    if body.interval < pol["min_interval"]:
        raise HTTPException(400, "%s 的最小间隔是 %d 分钟"
                            % (pol["label"], pol["min_interval"]))
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM automation_rules WHERE user_id=? AND kind=?",
                         (user.id, body.kind)).fetchone()["c"]
        if pol["max_count"] and n >= pol["max_count"]:
            raise HTTPException(403, "%s 最多只能建 %d 条规则" % (pol["label"], pol["max_count"]))
        cur = conn.execute(
            "INSERT INTO automation_rules(user_id,kind,account_id,interval,config,enabled,"
            "created_at) VALUES(?,?,?,?,?,?,?)",
            (user.id, body.kind, body.account_id, body.interval,
             json.dumps(body.config, ensure_ascii=False), 1 if body.enabled else 0,
             int(time.time())),
        )
    return {"ok": True, "id": cur.lastrowid}


@router.put("/automation/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        row = conn.execute("SELECT id FROM automation_rules WHERE id=? AND user_id=?",
                           (rule_id, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "规则不存在")
        conn.execute(
            "UPDATE automation_rules SET account_id=?, interval=?, config=?, enabled=? WHERE id=?",
            (body.account_id, body.interval, json.dumps(body.config, ensure_ascii=False),
             1 if body.enabled else 0, rule_id),
        )
    return {"ok": True}


@router.delete("/automation/{rule_id}")
def delete_rule(rule_id: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        conn.execute("DELETE FROM automation_rules WHERE id=? AND user_id=?", (rule_id, user.id))
    return {"ok": True}
