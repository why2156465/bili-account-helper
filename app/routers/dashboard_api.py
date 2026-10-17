"""总览看板 —— 对齐原站 /api/dashboard 契约。

真实响应形状（从线上 /api/dashboard 抓取）：
{
  "total":0, "ok":0, "bad":0, "coins":0.0, "fans":0, "videos":0,
  "today_ops":0, "total_ops":0,
  "stat": {"like":0,"coin":0,"fav":0,"comment":0,"share":0,"follow":0,"play":0,"dm":0},
  "trend": {"dates":["10-11",...],"play":[],"like":[],"fans":[],
            "total":{"play":0,"like":0,"fans":0},"points":0},
  "accounts": [...],           # 与 /api/accounts 同结构
  "recent": [{"time","actor","target","action","code","message"}]
}
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ..auth import CurrentUser, require_user
from ..db import db

router = APIRouter(prefix="/api", tags=["dashboard"])

ACTION_KEYS = ["like", "coin", "fav", "comment", "share", "follow", "play", "dm"]


def _day(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


@router.get("/dashboard")
def dashboard(user: CurrentUser = Depends(require_user)):
    now = int(time.time())
    today_start = int(time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1)))

    with db() as conn:
        accs = conn.execute("SELECT * FROM bili_accounts WHERE user_id=? ORDER BY id",
                            (user.id,)).fetchall()
        total_ops = conn.execute("SELECT COUNT(*) c FROM op_logs WHERE user_id=?",
                                 (user.id,)).fetchone()["c"]
        today_ops = conn.execute(
            "SELECT COUNT(*) c FROM op_logs WHERE user_id=? AND created_at>=?",
            (user.id, today_start)).fetchone()["c"]
        # 动作统计：累计成功
        rows = conn.execute(
            "SELECT action, COUNT(*) c FROM op_logs WHERE user_id=? AND result='ok' "
            "GROUP BY action", (user.id,)).fetchall()
        stat = {k: 0 for k in ACTION_KEYS}
        for r in rows:
            a = str(r["action"])
            if a in stat:
                stat[a] += int(r["c"])
            elif a == "interact":
                # interact 是合称，按 like 计入
                stat["like"] += int(r["c"])

        # 近 7 天趋势：每天的互动次数（play/like/fans 用操作数近似，粉丝数取快照）
        dates, play, like, fans = [], [], [], []
        for d in range(6, -1, -1):
            ts = now - d * 86400
            day = _day(ts)
            dates.append(day[5:])
            n = conn.execute(
                "SELECT COUNT(*) c FROM op_logs WHERE user_id=? AND result='ok' "
                "AND date(created_at,'unixepoch','localtime')=?", (user.id, day)
            ).fetchone()["c"]
            like.append(int(n))
            play.append(int(n * 3))       # 播放量按互动数估算（无上游播放数据）
            fans.append(0)

        # 数据点：有操作的天数
        pts = conn.execute(
            "SELECT COUNT(DISTINCT date(created_at,'unixepoch','localtime')) c "
            "FROM op_logs WHERE user_id=?", (user.id,)).fetchone()["c"]

        recent_rows = conn.execute(
            "SELECT l.*, a.uname AS aname, a.mid AS amid FROM op_logs l "
            "LEFT JOIN bili_accounts a ON a.id=l.account_id "
            "WHERE l.user_id=? ORDER BY l.id DESC LIMIT 20", (user.id,)).fetchall()

    accounts = [{
        "id": a["id"], "mid": a["mid"], "uname": a["uname"], "face": a["face"],
        "fans": a["fans"] or 0, "videos": a["videos"] or 0, "coins": a["coins"] or 0,
        "level": a["level"] or 0, "status": a["status"],
        "coin_ok": bool(a["coin_ok"]), "cred_source": a["cred_source"],
        "last_check_at": a["last_check"] or "未体检",
        "proxy": a["proxy"] or "",
        "alias_name": a["alias_name"] or "", "alias_mid": a["alias_mid"] or "",
        "alias_face": a["alias_face"] or "",
    } for a in accs]

    ok_n = sum(1 for a in accounts if a["status"] == "ok")
    recent = [{
        "time": time.strftime("%m-%d %H:%M", time.localtime(r["created_at"])),
        "actor": r["aname"] or (("UID " + str(r["amid"])) if r["amid"] else "-"),
        "target": r["target"] or "",
        "action": r["action"],
        "code": 0 if r["result"] == "ok" else (r["code"] or -1),
        "message": "" if r["result"] == "ok" else (r["result"] or ""),
    } for r in recent_rows]

    return {
        "total": len(accounts),
        "ok": ok_n,
        "bad": len(accounts) - ok_n,
        "coins": round(sum(float(a["coins"] or 0) for a in accounts), 1),
        "fans": sum(int(a["fans"] or 0) for a in accounts),
        "videos": sum(int(a["videos"] or 0) for a in accounts),
        "today_ops": int(today_ops),
        "total_ops": int(total_ops),
        "stat": stat,
        "trend": {
            "dates": dates,
            "play": play,
            "like": like,
            "fans": fans,
            "total": {"play": sum(play), "like": sum(like), "fans": sum(fans)},
            "points": int(pts),
        },
        "accounts": accounts,
        "recent": recent,
    }
