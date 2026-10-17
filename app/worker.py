"""后台调度进程：每 WORKER_TICK 秒扫一次到点的任务。

历史教训：这个文件原来查的是 `tasks` / `task_runs` 两张表 —— 那是早期设计的遗留，
真实的任务表是 `interact_tasks`（web 层建、web 层读）。结果就是**定时任务从来没有
自动跑过**，只有手动点「执行」才动。现在改为操作唯一真实来源 interact_tasks。

任务类型与执行器：
    interact  互赞        -> interact.start_from_cfg(user, cfg)
    upload    定时投稿    -> publish_upload.run_upload(...)

启动：python -m app.worker
"""
from __future__ import annotations

import json
import time

from .config import settings
from .db import db, init_db
from .quota import effective_plan


def _next_run(expr: str, after: int | None = None) -> int:
    from .cron import next_run

    try:
        return int(next_run(expr, after) or 0)
    except Exception:
        return 0


def bootstrap_next_runs() -> int:
    """只给「启用但从未算过时间」的任务补 next_run。

    注意：绝不能在这里处理 next_run < now 的任务 —— 那正是「到点待执行」的标记，
    提前把它推到下一分钟会让任务永远排不进去（曾经就是这么吃掉的）。
    过期任务由 tick() 执行完后自行推进。
    """
    fixed = 0
    with db() as conn:
        rows = conn.execute(
            "SELECT id, cron FROM interact_tasks "
            "WHERE enabled=1 AND COALESCE(cron,'')<>'' AND COALESCE(next_run,0)=0"
        ).fetchall()
        for r in rows:
            nr = _next_run(r["cron"])
            if nr:
                conn.execute("UPDATE interact_tasks SET next_run=? WHERE id=?", (nr, r["id"]))
                fixed += 1
    return fixed


def due_tasks(now: int | None = None) -> list[dict]:
    now = now or int(time.time())
    with db() as conn:
        rows = conn.execute(
            "SELECT id, user_id, name, kind, cron, config FROM interact_tasks "
            "WHERE enabled=1 AND next_run>0 AND next_run<=? ORDER BY next_run",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def _log_run(task_id: int, user_id: int, status: str, message: str,
             started: int, ended: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO task_runs(task_id,user_id,status,message,started_at,ended_at) "
            "VALUES(?,?,?,?,?,?)",
            (task_id, user_id, status, message[:500], started, ended),
        )


def _advance(row: dict, started: int) -> None:
    """推进 next_run。**所有**退出路径都必须调它。

    早期版本只在"执行完之后"推进，于是配置损坏 / 用户被删这类提前 return
    的分支会把 next_run 永远留在过去 —— 每分钟重试一次，日志刷屏。
    """
    with db() as conn:
        nxt = 0
        if row.get("cron"):
            now = int(time.time())
            nxt = _next_run(row["cron"], now) or 0
            if nxt and nxt <= now:                      # 兜底：绝不能落在过去
                nxt = _next_run(row["cron"], now + 1) or 0
        conn.execute("UPDATE interact_tasks SET last_run=?, next_run=? WHERE id=?",
                     (started, nxt, row["id"]))


def _run_one(row: dict) -> str:
    """执行一条到点任务，返回可读结果。异常不外抛，避免拖垮整个循环。"""
    task_id = row["id"]
    kind = row["kind"] or "interact"
    started = int(time.time())
    try:
        cfg = json.loads(row["config"] or "{}")
    except json.JSONDecodeError:
        _log_run(task_id, row["user_id"], "fail", "配置不是合法 JSON", started, started)
        _advance(row, started)
        return "配置损坏"

    class _U:
        """执行器只需要 user.id / plan 相关字段，这里按任务归属重建一个最小用户对象。"""

        def __init__(self, uid: int):
            self.id = uid
            self.username = ""
            self.email = ""
            self.role = "user"
            self.plan = "free"
            self.plan_until = 0
            self.traffic_mb = 0
            self.sid = ""
            self.plan_tier = ""
            self.is_vip = False       # 普通属性，后面按库里实际档位覆盖

    user = _U(row["user_id"])
    with db() as conn:
        u = conn.execute(
            "SELECT id, username, email, role, plan, plan_until, traffic_mb, plan_tier "
            "FROM users WHERE id=?", (row["user_id"],)).fetchone()
    if not u:
        _log_run(task_id, row["user_id"], "fail", "任务归属用户已不存在", started, started)
        _advance(row, started)
        return "用户不存在"
    user.username = u["username"]
    user.email = u["email"]
    user.role = u["role"]
    user.plan = u["plan"]
    user.plan_until = u["plan_until"]
    user.traffic_mb = u["traffic_mb"]
    user.plan_tier = u["plan_tier"] or ""
    user.is_vip = (u["plan"] == "vip"
                   and (not u["plan_until"] or u["plan_until"] > int(time.time())))

    status, message = "ok", ""
    try:
        if kind == "upload":
            from .routers.publish_upload import RunUploadIn, run_upload

            res = run_upload(RunUploadIn(task_id=task_id, config=cfg), user)
            if res.get("ok"):
                message = "投稿已提交：%s" % (res.get("bvid") or "待审核")
            else:
                status = "fail"
                message = res.get("message") or "投稿失败"
        elif kind == "interact":
            if not (cfg.get("actions") or {}):
                status, message = "skip", "没有配置动作"
            elif not (cfg.get("targets") or []):
                status, message = "skip", "没有配置目标"
            else:
                from .routers.interact import start_from_cfg

                res = start_from_cfg(user, cfg)
                message = (res or {}).get("message") or "已开始执行"
        else:
            status, message = "skip", "未知任务类型：%s" % kind
    except Exception as exc:
        status, message = "fail", "%s: %s" % (type(exc).__name__, exc)

    ended = int(time.time())
    _log_run(task_id, row["user_id"], status, message, started, ended)
    _advance(row, started)
    print("[worker] 任务 %s (%s) -> %s %s" % (task_id, kind, status, message), flush=True)
    return message


def tick(now: int | None = None) -> list[str]:
    """跑一轮；返回每条的执行结果，方便测试直接调用而不启线程。"""
    out = []
    for row in due_tasks(now):
        try:
            out.append(_run_one(row))
        except Exception as exc:
            print("[worker] 任务 %s 崩了：%s" % (row["id"], exc), flush=True)
            out.append("error: %s" % exc)
    return out


def main() -> None:
    init_db()
    print("[worker] 启动，tick=%ss" % settings.WORKER_TICK, flush=True)
    while True:
        try:
            n = bootstrap_next_runs()
            if n:
                print("[worker] 补算了 %d 个任务的下次执行时间" % n, flush=True)
            tick()
        except Exception as exc:
            print("[worker] 循环异常：%s" % exc, flush=True)
        time.sleep(settings.WORKER_TICK)


if __name__ == "__main__":
    main()
