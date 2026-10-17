"""账号趋势 —— 对齐原站 /api/trend?days= 契约（从 trend.html 内联 JS 提取）。

响应形状：
{
  "days": 7,
  "dates": ["10-11", ...],                  # 长度 = days，最后一个是今天
  "accounts": [{
      "id", "name", "mid", "face", "status", "fans", "videos",
      "views", "likes",                           # 当前累计值
      "series": {"play": [...], "like": [...], "fans": [...]}   # 每天新增量
  }]
}

曲线的语义（原站页面上的原话）："曲线是当天比上一条快照新增的量，累计值显示在卡片右上角"。
所以 series 存的是**每日增量**，不是绝对值。

数据来源：account_snapshots 表。每天 10:00 由 worker 记一次，
点账号「体检」也会补记一次（见 accounts.py 的 check 接口）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ..auth import CurrentUser, require_user
from ..db import db

router = APIRouter(prefix="/api", tags=["trend"])


def save_snapshot(conn, user_id: int, account_id: int, *, views: int, likes: int,
                  fans: int, videos: int) -> None:
    """记一条当天快照。同一天重复调用只更新，不新增（UNIQUE(account_id, day)）。

    /api/trend 的曲线就是相邻两天快照的差值，所以这里必须保证每天至多一条。
    """
    day = time.strftime("%Y-%m-%d", time.localtime())
    conn.execute(
        "INSERT INTO account_snapshots(user_id,account_id,day,views,likes,fans,videos,"
        "created_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(account_id,day) DO UPDATE SET views=excluded.views, "
        "likes=excluded.likes, fans=excluded.fans, videos=excluded.videos, "
        "created_at=excluded.created_at",
        (user_id, account_id, day, int(views), int(likes), int(fans), int(videos),
         int(time.time())))


def _day_list(days: int) -> tuple[list[str], list[str]]:
    """返回 (显示用的 MM-DD 列表, 查询用的 YYYY-MM-DD 列表)。"""
    now = int(time.time())
    shown, keys = [], []
    for d in range(days - 1, -1, -1):
        ts = now - d * 86400
        lt = time.localtime(ts)
        shown.append(time.strftime("%m-%d", lt))
        keys.append(time.strftime("%Y-%m-%d", lt))
    return shown, keys


@router.get("/trend")
def trend(days: int = 7, user: CurrentUser = Depends(require_user)):
    days = max(2, min(days, 90))
    shown, keys = _day_list(days)

    with db() as conn:
        accs = conn.execute(
            "SELECT id,mid,uname,face,status,fans,videos,coins,level,alias_name,"
            "alias_mid,alias_face FROM bili_accounts WHERE user_id=? ORDER BY id",
            (user.id,)).fetchall()

        # 拉该时间窗内的所有快照
        since = int(time.time()) - days * 86400
        snaps = conn.execute(
            "SELECT account_id, day, views, likes, fans FROM account_snapshots "
            "WHERE user_id=? AND day>=?", (user.id, keys[0])).fetchall()

    # {account_id: {day: (views, likes, fans)}}
    by_acc: dict[int, dict[str, tuple]] = {}
    for s in snaps:
        by_acc.setdefault(s["account_id"], {})[s["day"]] = (
            int(s["views"] or 0), int(s["likes"] or 0), int(s["fans"] or 0))

    out = []
    for a in accs:
        rows = by_acc.get(a["id"], {})
        play, like, fans = [], [], []
        prev = None
        for k in keys:
            if k in rows:
                cur = rows[k]
                if prev is None:
                    play.append(0)
                    like.append(0)
                    fans.append(0)
                else:
                    play.append(cur[0] - prev[0])
                    like.append(cur[1] - prev[1])
                    fans.append(cur[2] - prev[2])
                prev = cur
            else:
                play.append(0)
                like.append(0)
                fans.append(0)
        # 累计值取最后一条快照，没有快照则用账号当前值
        last = prev or (0, 0, 0)
        out.append({
            "id": a["id"],
            "name": a["alias_name"] or a["uname"] or "",
            "mid": a["alias_mid"] or a["mid"] or "",
            "face": a["alias_face"] or a["face"] or "",
            "status": a["status"],
            "fans": int(a["fans"] or last[2] or 0),
            "videos": int(a["videos"] or 0),
            "views": int(last[0]),
            "likes": int(last[1]),
            "series": {"play": play, "like": like, "fans": fans},
        })

    return {"days": days, "dates": shown, "accounts": out}
