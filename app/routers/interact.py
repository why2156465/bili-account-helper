"""互赞任务引擎 —— 对齐原站 /api/tasks 系列契约。

原站前端（tasks.html 内联 JS）发出的配置形状，逐字照抄：

    cfg = {
      actions: {
        like: true,
        coin:    {on:true, multiply:1, also_like:false},
        fav:     {on:true, media_id:null},
        comment: {on:true, text:"主播我爱你", emotes:["[doge]"], emote:true},
        share:   {on:true, mode:"normal|dynamic", text:"转发视频"},
        follow:  {on:true},
        play:    {on:true},
        opus:    {on:true, max:5, like:true,
                  repost:{on:true,mode:"normal|hidden",text:"转发动态"},
                  comment:{on:true,text:"好内容，支持一下",emotes:[],emote:false}}
      },
      actors: "all" | [account_id, ...],
      targets: [{uid, name, rule, max, n, date, from, to, bvids[]}, ...],
      sleep: [3, 7]
    }

targets[].rule:
    latest  该 UP 最新 max 个
    all     最近投稿，最多 max 个
    random  从最新 max 个里随机挑 n 个
    date    发布日期 = date
    range   发布时间在 from ~ to 之间（最多 max 个）
    bvids   指定 BV 号列表

接口：
    POST /api/tasks/preview  {targets}       -> {results:[{uid,count,videos:[{date,bvid,title}]}]}
    POST /api/tasks/run      cfg             -> {ok, jid}
    GET  /api/tasks/progress                 -> 进度快照（前端 1.5s 轮询）
    POST /api/tasks/pause | /stop            -> 暂停 / 停止
    POST /api/tasks/progress/dismiss         -> 收起进度条
    GET  /api/tasks                          -> {tasks:[...]}
    POST /api/tasks  {name,cron,config,...}  -> 建定时任务
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, log_op, require_user
from ..bili import BiliClient, BiliError, backoff_seconds, describe_error, parse_cookie
from ..db import db
from ..quota import PLAN_LIMITS, bump_usage, effective_plan
from ..security import decrypt_cookie
from ..tasks import client_for, load_account

router = APIRouter(prefix="/api/tasks", tags=["interact"])

# 视频范围规则说明（与前端 ruleDesc 一一对应）
RULE_DESC = {
    "latest": "该 UP 的最新 {max} 个视频",
    "all": "最近投稿，最多 {max} 个",
    "random": "从最新 {max} 个里随机挑 {n} 个",
    "date": "发布日期 = {date}",
    "range": "发布时间在 {from} ~ {to} 之间",
    "bvids": "指定 BV 号",
}

EMOTE_POOL = ["[doge]", "[大笑]", "[星星眼]", "[打call]", "[妙啊]", "[吃瓜]",
              "[惊喜]", "[点赞]", "[呲牙]", "[辣眼睛]", "[尴尬]", "[疼]"]


# ---------------------------------------------------------------- 进度状态
class RunState:
    """全局单例：同一用户同时只跑一个任务，与原站行为一致。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.running = False
        self.paused = False
        self.canceled = False
        self.started_at = 0
        self.finished_at = 0
        self.label = ""
        self.total = 0
        self.done = 0
        self.ok = 0
        self.fail = 0
        self.current = ""
        self.logs: list[dict] = []
        self.error = ""
        self.origin = ""
        self.dismissed = False
        self.user_id = 0

    def push(self, msg: str, level: str = "info") -> None:
        self.logs.append({"t": time.strftime("%H:%M:%S"), "m": msg, "lv": level})
        if len(self.logs) > 500:
            self.logs = self.logs[-500:]
        print("[task] " + msg, flush=True)

    def snapshot(self) -> dict:
        elapsed = int((self.finished_at or time.time()) - self.started_at) if self.started_at else 0
        percent = int(self.done / self.total * 100) if self.total else 0
        eta = 0
        if self.running and self.done and elapsed:
            eta = int(elapsed / self.done * (self.total - self.done))
        return {
            "running": self.running,
            "label": self.label,
            "total": self.total,
            "done": self.done,
            "current": self.current,
            "percent": percent,
            "log": [x["m"] for x in self.logs[-60:]],
            "logs": self.logs[-200:],
            "elapsed": elapsed,
            "finished_at": self.finished_at,
            "error": self.error,
            "paused": self.paused,
            "canceled": self.canceled,
            "ok": self.ok,
            "fail": self.fail,
            "eta": eta,
            "origin": self.origin,
            "plan": [],
            "steps": [],
            "counts": {"ok": self.ok, "fail": self.fail},
            "repeat": {},
            "next_run": "",
            "jid": None,
            "source": self.origin,
            "jobs": [],
            "running_count": 1 if self.running else 0,
            "uploads": [],
            "upload_running": False,
            "dismissed": self.dismissed,
        }


STATE = RunState()


# ---------------------------------------------------------------- 目标解析
def _fetch_up_videos(cli: BiliClient, uid: str, limit: int = 50) -> list[dict]:
    """拉取某个 UP 的投稿列表（order=pubdate 时间倒序）。"""
    out: list[dict] = []
    page = 1
    while len(out) < limit and page <= 5:
        data = cli.get("/x/space/wbi/arc/search",
                       {"mid": uid, "pn": page, "ps": 30, "order": "pubdate"})
        vlist = (data.get("list") or {}).get("vlist") or []
        if not vlist:
            break
        for v in vlist:
            out.append({
                "aid": v.get("aid"),
                "bvid": v.get("bvid"),
                "title": v.get("title", ""),
                "date": time.strftime("%Y-%m-%d",
                                      time.localtime(v.get("created") or v.get("pubdate") or 0)),
                "ts": v.get("created") or 0,
                "author": v.get("author", ""),
                "mid": v.get("mid"),
            })
        if len(vlist) < 30:
            break
        page += 1
    # 自己按发布时间倒序排一遍：不能只依赖上游 order=pubdate，
    # 否则 latest 规则可能拿到最旧的视频（少了这一步「最新」就不成立）
    out.sort(key=lambda v: v.get("ts") or 0, reverse=True)
    return out


def resolve_targets(cli: BiliClient, targets: list[dict]) -> list[dict]:
    """把 targets 配置解析成 [{uid, videos:[...]}]。"""
    import random as _rnd

    results = []
    for t in targets:
        uid = str(t.get("uid") or "").strip()
        rule = t.get("rule") or "latest"
        videos: list[dict] = []

        if rule == "bvids":
            for bv in (t.get("bvids") or []):
                bv = str(bv).strip()
                if not bv:
                    continue
                try:
                    info = cli.get("/x/web-interface/view", {"bvid": bv})
                    videos.append({
                        "aid": info.get("aid"), "bvid": info.get("bvid", bv),
                        "title": info.get("title", ""),
                        "date": time.strftime("%Y-%m-%d",
                                              time.localtime(info.get("pubdate") or 0)),
                        "ts": info.get("pubdate") or 0,
                        "author": (info.get("owner") or {}).get("name", ""),
                        "mid": (info.get("owner") or {}).get("mid"),
                    })
                except BiliError:
                    continue
                except Exception:
                    continue
            results.append({"uid": uid, "count": len(videos), "videos": videos})
            continue

        if not uid:
            results.append({"uid": "", "count": 0, "videos": [], "error": "缺少 UID"})
            continue

        need = int(t.get("max") or (1 if rule == "latest" else 10))
        if rule == "random":
            need = max(need, int(t.get("n") or 1), 20)
        try:
            pool = _fetch_up_videos(cli, uid, min(max(need, 10), 150))
        except BiliError as exc:
            # 单个目标拿不到（UID 不存在 / 风控）只跳过这一组，
            # 不能因为一个坏 UID 让整批任务都跑不起来
            _, emsg = describe_error(exc)
            results.append({"uid": uid, "count": 0, "videos": [],
                            "error": "拉取投稿失败：%s" % emsg})
            continue

        if rule == "latest":
            videos = pool[:int(t.get("max") or 1)]
        elif rule == "all":
            videos = pool[:int(t.get("max") or 10)]
        elif rule == "random":
            head = pool[:int(t.get("max") or 20)]
            videos = _rnd.sample(head, min(int(t.get("n") or 1), len(head))) if head else []
        elif rule == "date":
            videos = [v for v in pool if v["date"] == (t.get("date") or "")]
        elif rule == "range":
            lo, hi = (t.get("from") or ""), (t.get("to") or "")
            videos = [v for v in pool if (not lo or v["date"] >= lo) and (not hi or v["date"] <= hi)]
            videos = videos[:int(t.get("max") or 10)]
        else:
            videos = pool[:1]

        results.append({"uid": uid, "count": len(videos), "videos": videos})
    return results


# ---------------------------------------------------------------- 动作执行
# 这些错误意味着「这个账号不能再干活了」，继续跑剩下的动作纯属浪费请求
FATAL_CODES = {-101, -111, 61001, -352, -412}


def do_one(cli: BiliClient, video: dict, actions: dict,
           accounts_uid: str) -> tuple[int, int, list[str]]:
    """对单个视频执行一组动作。返回 (成功数, 失败数, 消息)。

    任一动作拿到 FATAL_CODES（未登录/失效/风控）时立即停止剩余动作 —— 否则
    一个已失效的账号会对同一个视频把 8 个动作全试一遍，全部失败，还白白
    触发更多风控。fatal 通过 msgs 里带 "FATAL:" 前缀回传给调用方。
    """
    aid = video.get("aid")
    like_uid = video.get("mid")
    ok = fail = 0
    msgs: list[str] = []

    def fatal(e) -> bool:
        code = getattr(e, "code", None)
        if code in FATAL_CODES:
            msgs.append("FATAL:%s" % (getattr(e, "message", "") or {}))
            return True
        return False

    if actions.get("like"):
        try:
            cli.like(aid)
            ok += 1
        except BiliError as e:
            fail += 1
            msgs.append("点赞失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs

    coin = actions.get("coin")
    if isinstance(coin, dict) and coin.get("on"):
        try:
            cli.coin(aid, int(coin.get("multiply") or 1),
                     1 if coin.get("also_like") else 0)
            ok += 1
        except BiliError as e:
            fail += 1
            msgs.append("投币失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs

    fav = actions.get("fav")
    if isinstance(fav, dict) and fav.get("on"):
        try:
            cli.fav(aid, fav.get("media_id"))
            ok += 1
        except BiliError as e:
            fail += 1
            msgs.append("收藏失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs

    cmt = actions.get("comment")
    if isinstance(cmt, dict) and cmt.get("on"):
        text = cmt.get("text") or ""
        emotes = cmt.get("emotes") or []
        if emotes:
            text += " " + " ".join(emotes)
        elif cmt.get("emote"):
            import random as _rnd
            text += " " + _rnd.choice(EMOTE_POOL)
        try:
            cli.post("/x/v2/reply/add", {"oid": aid, "type": 1, "message": text, "plat": 1})
            ok += 1
        except BiliError as e:
            fail += 1
            msgs.append("评论失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs

    share = actions.get("share")
    if isinstance(share, dict) and share.get("on"):
        try:
            if share.get("mode") == "dynamic":
                cli.post("/x/web-interface/dynamic/feed/create", {
                    "aid": aid,
                    "text": share.get("text") or "转发视频",
                })
            else:
                cli.share(aid)
            ok += 1
        except BiliError as e:
            fail += 1
            msgs.append("分享失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs

    if actions.get("follow") and like_uid:
        try:
            cli.follow(like_uid)
            ok += 1
        except BiliError as e:
            fail += 1
            msgs.append("关注失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs

    if actions.get("play"):
        try:
            cli.post("/x/click-interface/web/heartbeat",
                     {"aid": aid, "played_time": 30})
            ok += 1
        except BiliError as e:
            fail += 1
            msgs.append("刷播放失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs

    opus = actions.get("opus")
    if isinstance(opus, dict) and opus.get("on"):
        try:
            dyn = cli.get("/x/polymer/web-dynamic/v1/feed/space", {"host_mid": like_uid})
            items = (dyn.get("items") or [])[:int(opus.get("max") or 5)]
            for it in items:
                did = (it.get("id_str") or "")
                if opus.get("like") and did:
                    try:
                        cli.post("/x/dynamic/feed/dyn/like", {"dyn_id_str": did, "op_type": 1})
                        ok += 1
                    except BiliError:
                        fail += 1
                rep = opus.get("repost") or {}
                if rep.get("on") and did:
                    try:
                        cli.post("/x/dynamic/feed/dyn/repost", {
                            "dyn_id_str": did,
                            "content": rep.get("text") or "转发动态",
                        })
                        ok += 1
                    except BiliError:
                        fail += 1
        except BiliError as e:
            fail += 1
            msgs.append("图文操作失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs

    return ok, fail, msgs


# ---------------------------------------------------------------- 后台执行
def _run_worker(user_id: int, cfg: dict, account_ids: list[int], jobs: list[tuple[int, dict]]) -> None:
    st = STATE
    with st.lock:
        st.running = True
        st.paused = False
        st.canceled = False
        st.started_at = int(time.time())
        st.finished_at = 0
        st.ok = st.fail = st.done = 0
        st.total = len(jobs)
        st.error = ""
        st.user_id = user_id
        st.label = "互赞任务"

    sleep_range = cfg.get("sleep") or [3, 7]
    try:
        smin = max(1, int(sleep_range[0]))
        smax = max(smin, int(sleep_range[1]))
    except (ValueError, TypeError, IndexError):
        smin, smax = 3, 7

    try:
        _run_loop(st, user_id, cfg, jobs, smin, smax)
    except Exception as exc:
        # 兜底：任何未预期异常都不能让 STATE 卡在 running=True
        st.push("任务异常中止：%s: %s" % (type(exc).__name__, exc), "err")
        st.error = str(exc)
        print("[interact] 任务异常：%s" % exc, flush=True)
    finally:
        with st.lock:
            st.running = False
            st.finished_at = int(time.time())
            st.current = ""
        st.push("任务结束：成功 %d，失败 %d" % (st.ok, st.fail),
                "ok" if not st.error else "warn")


def _run_loop(st, user_id: int, cfg: dict, jobs: list, smin: int, smax: int) -> None:
    """真正跑任务循环。异常向上抛给 _run_worker 的兜底处理。"""
    for idx, (acc_id, video) in enumerate(jobs, 1):
        if st.canceled:
            st.push("已被用户停止", "warn")
            break
        while st.paused and not st.canceled:
            time.sleep(1)
        if st.canceled:
            break

        if not isinstance(video, dict):
            st.fail += 1
            st.push("目标数据异常，跳过（%r）" % (video,), "err")
            st.done = idx
            continue
        title = (video.get("title") or video.get("bvid") or "")[:28]
        st.current = "%d/%d %s" % (idx, len(jobs), title)

        with db() as conn:
            acc = load_account(conn, user_id, acc_id)
        if not acc:
            st.fail += 1
            st.done = idx
            st.push("账号 #%d 不存在，跳过" % acc_id, "err")
            continue

        try:
            with client_for(acc) as cli:
                ok, fail, msgs = do_one(cli, video, cfg.get("actions") or {},
                                        str(acc["mid"] or ""))
            st.ok += ok
            st.fail += fail
            if ok:
                bump_usage(user_id, "like", ok)
            log_op(user_id, "interact", video.get("bvid") or str(video.get("aid")),
                   "ok" if not fail else "partial", None, acc["id"])

            # do_one 里遇到凭据/风控类致命错误会用 "FATAL:" 前缀回报。
            # 此时账号已经不能干活了，必须把状态写回库并中止整个任务，
            # 否则剩余几十个视频会一个一个全试一遍全部失败。
            fatal_msg = next((m for m in msgs if m.startswith("FATAL:")), "")
            for m in msgs:
                st.push(m.replace("FATAL:", "账号不可用："), "err")
            if fatal_msg:
                status = "expired" if ("-101" in fatal_msg or "-111" in fatal_msg
                                       or "61001" in fatal_msg) else "captcha"
                with db() as conn:
                    conn.execute(
                        "UPDATE bili_accounts SET status=?, coeff=coeff*1.5 WHERE id=?",
                        (status, acc["id"]))
                st.error = fatal_msg.replace("FATAL:", "")
                st.done = idx          # 中止也要记下「跑到第几个」
                st.push("账号已失效/被风控，中止任务。去「账号」页重新登录后再跑。", "err")
                break
            st.push("[%s] %s 完成 %d 项，失败 %d 项"
                    % (acc["name"] or acc["mid"], title, ok, fail),
                    "ok" if not fail else "warn")
        except BiliError as exc:
            status, msg = describe_error(exc)
            st.fail += 1
            st.push("风控中断：%s" % msg, "err")
            with db() as conn:
                conn.execute("UPDATE bili_accounts SET status=?, coeff=coeff*1.5 WHERE id=?",
                             (status, acc["id"]))
            if status in ("expired", "captcha"):
                st.error = msg
                break
        except Exception as exc:
            st.fail += 1
            st.push("异常：%s" % exc, "err")

        st.done = idx
        if idx < len(jobs):
            time.sleep(random_sleep(smin, smax))


def random_sleep(lo: int, hi: int) -> float:
    import random

    return float(random.randint(lo, max(lo, hi)))


# ---------------------------------------------------------------- 接口
class PreviewIn(BaseModel):
    targets: list[dict] = []


@router.post("/preview")
def preview(body: PreviewIn, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        acc = load_account(conn, user.id, None)
    if not acc:
        raise HTTPException(400, "请先添加 B 站账号")
    if not body.targets:
        return {"results": []}
    try:
        with client_for(acc) as cli:
            results = resolve_targets(cli, body.targets)
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])
    return {"results": results}


@router.post("/run")
def run(cfg: dict, user: CurrentUser = Depends(require_user)):
    if STATE.running and STATE.user_id != user.id:
        raise HTTPException(409, "已有任务在跑，请先等待或停止")
    if STATE.running:
        raise HTTPException(409, "已有任务在运行中")

    actions = cfg.get("actions") or {}
    if not actions:
        raise HTTPException(400, "请至少勾选一个动作")
    targets = cfg.get("targets") or []
    if not targets:
        raise HTTPException(400, "请至少添加一个目标")

    with db() as conn:
        rows = conn.execute("SELECT id,mid FROM bili_accounts WHERE user_id=?",
                            (user.id,)).fetchall()
    if not rows:
        raise HTTPException(400, "请先添加 B 站账号")

    actors = cfg.get("actors")
    if actors == "all" or actors is None:
        acc_ids = [r["id"] for r in rows]
    else:
        acc_ids = [int(x) for x in actors if any(r["id"] == int(x) for r in rows)]
    if not acc_ids:
        raise HTTPException(400, "没有可用的执行账号")

    # 额度：目标数 × 目标上限 作为预估
    limit = PLAN_LIMITS[effective_plan(user)]["like_per_day"]
    est = 0
    for t in targets:
        est += int(t.get("max") or (int(t.get("n") or 1) if t.get("rule") == "random" else 1))
    est = max(1, est) * len(acc_ids)
    from ..quota import used_today

    if limit >= 0 and used_today(user.id, "like") + est > limit:
        raise HTTPException(403, "今日互动额度不足（已用 %d / 上限 %d）"
                            % (used_today(user.id, "like"), limit))

    # 预先解析目标视频，再进入线程
    with db() as conn:
        acc = load_account(conn, user.id, acc_ids[0])
    try:
        with client_for(acc) as cli:
            resolved = resolve_targets(cli, targets)
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])

    jobs: list[tuple[int, dict]] = []
    for r in resolved:
        for v in r.get("videos") or []:
            for aid_acc in acc_ids:
                jobs.append((aid_acc, v))
    if not jobs:
        raise HTTPException(400, "目标没有匹配到任何视频")

    STATE.reset()
    STATE.dismissed = False
    STATE.origin = "interact"
    threading.Thread(target=_run_worker, args=(user.id, cfg, acc_ids, jobs),
                     daemon=True, name="interact-worker").start()
    return {"ok": True, "jid": int(time.time() * 1000), "total": len(jobs),
            "message": "已开始执行，去「日志」看进度"}


def start_from_cfg(user, cfg: dict) -> dict:
    """给 scheduler 手动触发复用：校验 + 解析目标 + 起线程。"""
    actions = cfg.get("actions") or {}
    targets = cfg.get("targets") or []
    if not actions:
        raise HTTPException(400, "配置里没有动作")
    if not targets:
        raise HTTPException(400, "配置里没有目标")
    if STATE.running:
        raise HTTPException(409, "已有任务在运行中")

    with db() as conn:
        rows = conn.execute("SELECT id FROM bili_accounts WHERE user_id=?",
                            (user.id,)).fetchall()
        if not rows:
            raise HTTPException(400, "请先添加 B 站账号")
        actors = cfg.get("actors")
        if actors == "all" or actors is None:
            acc_ids = [r["id"] for r in rows]
        else:
            acc_ids = [int(x) for x in actors if any(r["id"] == int(x) for r in rows)]
        if not acc_ids:
            raise HTTPException(400, "没有可用的执行账号")
        acc = load_account(conn, user.id, acc_ids[0])

    try:
        with client_for(acc) as cli:
            resolved = resolve_targets(cli, targets)
    except BiliError as exc:
        raise HTTPException(502, describe_error(exc)[1])

    jobs: list[tuple[int, dict]] = []
    for r in resolved:
        for v in r.get("videos") or []:
            for aid_acc in acc_ids:
                jobs.append((aid_acc, v))
    if not jobs:
        raise HTTPException(400, "目标没有匹配到任何视频")

    STATE.reset()
    STATE.dismissed = False
    STATE.origin = "scheduler"
    threading.Thread(target=_run_worker, args=(user.id, cfg, acc_ids, jobs),
                     daemon=True, name="interact-worker").start()
    return {"ok": True, "total": len(jobs), "message": "已开始执行"}


@router.get("/progress")
def progress(user: CurrentUser = Depends(require_user)):
    snap = STATE.snapshot()
    if STATE.user_id and STATE.user_id != user.id:
        # 别人的任务不展示内容
        snap["label"] = ""
        snap["logs"] = []
        snap["log"] = []
    return snap


@router.post("/pause")
def pause(user: CurrentUser = Depends(require_user)):
    STATE.paused = True
    STATE.push("已暂停", "warn")
    return {"ok": True, "paused": True}


@router.post("/resume")
def resume(user: CurrentUser = Depends(require_user)):
    STATE.paused = False
    STATE.push("已继续")
    return {"ok": True, "paused": False}


@router.post("/stop")
def stop(user: CurrentUser = Depends(require_user)):
    STATE.canceled = True
    STATE.paused = False
    STATE.push("正在停止…", "warn")
    return {"ok": True, "canceled": True}


@router.post("/progress/dismiss")
def dismiss(user: CurrentUser = Depends(require_user)):
    STATE.dismissed = True
    return {"ok": True}
