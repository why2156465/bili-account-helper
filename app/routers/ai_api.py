"""AI 助手 AI 模块 —— 对齐原站 /api/ai/* 契约。

    GET  /api/ai/info          -> {enabled,ready,models,default_model,welcome,quota,
                                   tokens_per_mb,token_quota,traffic,scopes,guard_words}
    GET  /api/ai/quota?_=ts    -> {quota:{tier,limit,used,remain}, token_quota, traffic}
    GET  /api/ai/history       -> {messages:[{role,content,created_at}]}
    POST /api/ai/chat {message,model,history}      -> {reply,actions:[...],quota}
    POST /api/ai/clear                              -> {ok}
    POST /api/ai/apply {type,payload}               -> {message,detail,open_prog}
    GET  /api/ai/assets        -> {images,videos,accounts}          编号快捷插入
    GET  /api/ai/presets       -> {presets}
    GET  /api/ai/prompts       -> {prompts}
    POST /api/ai/profiles {scope,account_id,name,model,persona}
    GET  /api/ai/profiles      -> {profiles,accounts,models}
    DELETE /api/ai/profiles/{id}
    POST /api/ai/profiles/{id}/template          存为 AI 模板
    POST /api/ai/profiles/from-template {template_id,account_id}
    DELETE /api/ai/templates/{id}

chat 返回的 actions 是「可一键执行的方案」，结构与 apply 的 type 对应：
    create_task / create_interact / create_dm / create_template / create_ai_task
"""
from __future__ import annotations

import json
import re
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, require_user
from ..config import PLAN_LIMITS, settings
from ..db import db
from ..quota import bump_usage, effective_plan, used_today

router = APIRouter(prefix="/api/ai", tags=["ai"])

PERSONA = "AI 助手"
GUARD_WORDS = ["加微信", "微信号", "加我微信", "加qq", "加我qq", "私聊我", "薇信",
               "微亻言", "刷单", "代刷", "兼职", "日结", "博彩", "赌博", "彩票",
               "外围", "色情", "约炮", "元楼", "贷款", "发票", "洗钱", "跑分",
               "接码", "引流", "推广费"]

SCOPES = [
    {"id": "main", "name": "主页总控"},
    {"id": "dm", "name": "私信专用"},
    {"id": "nurture", "name": "养号专用"},
]

MODELS = [
    {"id": "deepseek-flash", "name": "DeepSeek Flash", "ver": "V4.1"},
]

WELCOME = ("…你来了。我是 AI 助手，本站的看板娘兼运维机娘。账号、Cookie、投稿、互赞、"
           "定时任务、风控排障，都归我管。把需求说清楚——直接说你要什么、哪个账号、"
           "什么时候做。我会给你可执行的方案，点头就能落地。…无关的事不必问。")

QUICK = [
    "帮我写一个每天 20 点给关注的人点赞的定时任务",
    "投稿老是不通过怎么办",
    "投币提示需要重扫码是什么意思",
    "怎么给某个关键词下的 UP 主批量私信",
]

LOCAL_KB = [
    (("验证码", "风控", "-352", "-412", "限流"),
     "…是，账号触发了风控校验。先停掉这个号的高频操作，等 3~7 天，期间别异地登录、"
     "别频繁改密码，一般会自动解除。想稳妥点，先换「本地上传」慢慢发。"),
    (("投稿", "不通过", "审核"),
     "投稿过不了通常是三个原因：重复稿件、标题/封面违规、分区选错。"
     "先改标题去掉夸张词，封面别带第三方 logo，分区按内容主题选一级分区。"),
    (("投币", "bili_ticket"),
     "投币需要网页端同源凭证，bili_ticket 约 3 天过期。账号卡片会标注「需重扫码投币」，"
     "重新扫码即可；点赞、收藏、评论、分享不受影响。"),
    (("私信", "找客户", "关键词"),
     "可以。按关键词搜最新视频，翻评论区找意向用户，再逐个私信。"
     "同一账号 30 天内不会重复私信同一个人。"),
    (("点赞", "定时", "每天", "cron"),
     "可以。定时表达式 0 9 * * *，任务已生成，点「一键创建」就行。"
     "以后这种小事不用问，说清楚 UP 主 UID 即可。"),
    (("会员", "额度", "流量"),
     "普通用户：每天互赞 5 次、投稿 1 个、2 个账号、AI 对话 10 次、私信 10 条。"
     "会员：888 次互赞、88 个投稿、50 个账号、不限定时任务、AI 对话 888 次。"),
]


# ---------------------------------------------------------------- 工具
def _quota_view(user) -> dict:
    plan = effective_plan(user)
    lim = PLAN_LIMITS[plan]["ai_chat_per_day"]
    used = used_today(user.id, "ai_chat")
    return {"tier": plan, "limit": lim, "used": used,
            "remain": max(0, lim - used) if lim >= 0 else -1}


def _traffic_view(user) -> dict:
    with db() as conn:
        r = conn.execute("SELECT traffic_mb, traffic_used_mb FROM users WHERE id=?",
                         (user.id,)).fetchone()
    total = int(r["traffic_mb"]) * 1024 * 1024
    used = int(r["traffic_used_mb"] or 0)
    remain = max(0, total - used)

    def h(n):
        f = float(n)
        for u in ("B", "KB", "MB", "GB"):
            if f < 1024 or u == "GB":
                return ("%d %s" % (f, u)) if u in ("B", "KB") else ("%.2f %s" % (f, u))
            f /= 1024
        return "%d B" % n
    return {"total": total, "used": used, "remain": remain,
            "total_h": h(total), "used_h": h(used), "remain_h": h(remain)}


def _check_guard(text: str) -> str | None:
    low = (text or "").lower()
    for w in GUARD_WORDS:
        if w.lower() in low:
            return w
    return None


def _local_reply(text: str):
    low = text.lower()
    for keys, ans in LOCAL_KB:
        if any(k.lower() in low for k in keys):
            return ans, _maybe_action(text)
    return ("…说清楚你要做什么：点赞谁、投稿什么、还是找客户私信。"
            "给 UID 或关键词，我直接建任务。", None)


def _maybe_action(text: str):
    """本地规则也要能生成可执行方案，结构与 LLM 输出一致。"""
    m = re.search(r"(?:每天|每日)?\s*(\d{1,2})\s*[点:：时]", text)
    hour = int(m.group(1)) if m else 20
    uid = re.search(r"\b(\d{5,12})\b", text)

    if any(k in text for k in ("点赞", "互赞", "三连")) and uid:
        return {
            "type": "create_interact",
            "label": "创建互赞定时任务",
            "name": "每天 %d 点给 UP 主 %s 点赞" % (hour, uid.group(1)),
            "cron": "0 %d * * *" % hour,
            "config": {"actions": {"like": True},
                       "actors": "all",
                       "targets": [{"uid": uid.group(1), "rule": "latest", "max": 1}],
                       "sleep": [3, 7]},
        }
    if "监控" in text and uid:
        return {"type": "create_task", "label": "创建监控任务",
                "name": "监控 UP 主 %s 新投稿" % uid.group(1),
                "kind": "monitor", "cron": "*/50 * * * *",
                "config": {"up_mid": uid.group(1)}}
    if "私信" in text and uid:
        return {"type": "create_dm", "label": "创建私信任务",
                "name": "给 UID %s 发私信" % uid.group(1),
                "config": {"targets": [uid.group(1)], "text": "你好"},
                "cron": ""}
    return None


def _llm_chat(user, message: str, history: list, model: str) -> tuple[str, list]:
    if not settings.AI_API_KEY:
        reply, action = _local_reply(message)
        return reply, ([action] if action else [])

    msgs = [{"role": "system", "content": (
        "你是「%s」，B站账号管理助手的看板娘兼运维机娘。说话冷淡、话少、直接给结论。\n"
        "你熟悉：B站风控（-412/-352）、投稿审核、互赞三连、私信自动化、定时任务 cron。\n"
        "当用户描述需求时，除了文字回答，还要给出可执行方案。如需创建任务，在回复末尾"
        "追加一行 JSON（不要代码块）：\n"
        '{"type":"create_interact","label":"创建互赞定时任务","name":"...",'
        '"cron":"0 20 * * *","config":{"actions":{"like":true},"actors":"all",'
        '"targets":[{"uid":"123","rule":"latest","max":1}],"sleep":[3,7]}}\n'
        "不确定就只回答，不输出 JSON。") % PERSONA}]
    msgs += [h for h in history[-12:] if h.get("role") in ("user", "assistant")]
    msgs.append({"role": "user", "content": message})
    try:
        r = httpx.post(settings.AI_API_BASE.rstrip("/") + "/chat/completions",
                       headers={"Authorization": "Bearer %s" % settings.AI_API_KEY,
                                "Content-Type": "application/json"},
                       json={"model": model or settings.AI_MODEL, "messages": msgs,
                             "temperature": 0.6},
                       timeout=60)
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        reply, action = _local_reply(message)
        return reply + "\n（AI 服务不可用：%s，已用本地规则回答）" % type(exc).__name__, \
               ([action] if action else [])

    actions = []
    m = re.search(r'\{[^{}]*"type"\s*:\s*"[a-z_]+".*\}', content, re.S)
    if m:
        try:
            actions = [json.loads(m.group(0))]
            content = content.replace(m.group(0), "").strip()
        except json.JSONDecodeError:
            actions = []
    return content, actions


# ---------------------------------------------------------------- 接口
@router.get("/info")
def ai_info(user: CurrentUser = Depends(require_user)):
    return {
        "enabled": True,
        "ready": True,
        "models": MODELS,
        "default_model": MODELS[0]["id"],
        "welcome": WELCOME,
        "quota": _quota_view(user),
        "tokens_per_mb": 800,
        "token_quota": None,
        "traffic": _traffic_view(user),
        "scopes": SCOPES,
        "guard_words": GUARD_WORDS,
        "quick": QUICK,
    }


@router.get("/quota")
def ai_quota(user: CurrentUser = Depends(require_user)):
    return {"quota": _quota_view(user), "token_quota": None,
            "traffic": _traffic_view(user)}


@router.get("/history")
def ai_history(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT role,content,created_at FROM ai_messages WHERE user_id=? "
            "AND scope='main' ORDER BY id LIMIT 200", (user.id,)).fetchall()
    return {"messages": [dict(r) for r in rows]}


class ChatIn(BaseModel):
    message: str = ""
    model: str = ""
    history: list[dict] = []


@router.post("/chat")
def ai_chat(body: ChatIn, user: CurrentUser = Depends(require_user)):
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(400, "消息为空")
    hit = _check_guard(msg)
    if hit:
        raise HTTPException(400, "这句话涉及平台禁止的内容（命中「%s」），我不能接。" % hit)

    q = _quota_view(user)
    if q["limit"] >= 0 and q["used"] >= q["limit"]:
        raise HTTPException(403, "今天的 AI 对话次数用完了（%d/%d），开通会员可提升到 888 次"
                            % (q["used"], q["limit"]))

    reply, actions = _llm_chat(user, msg, body.history, body.model)
    bump_usage(user.id, "ai_chat", 1)

    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO ai_messages(user_id,scope,role,content,created_at) "
            "VALUES(?,'main','user',?,?)", (user.id, msg, now))
        conn.execute(
            "INSERT INTO ai_messages(user_id,scope,role,content,created_at) "
            "VALUES(?,'main','assistant',?,?)", (user.id, reply, now))
    return {"reply": reply, "actions": actions, "quota": _quota_view(user)}


@router.post("/clear")
def ai_clear(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        conn.execute("DELETE FROM ai_messages WHERE user_id=? AND scope='main'", (user.id,))
    return {"ok": True}


class ApplyIn(BaseModel):
    type: str
    payload: dict = {}


@router.post("/apply")
def ai_apply(body: ApplyIn, user: CurrentUser = Depends(require_user)):
    """把 AI 助手 给出的方案落成真实的配置/任务。"""
    p = body.payload or {}
    t = body.type

    if t in ("create_interact", "create_template", "create_ai_task", "create_task"):
        cfg = p.get("config") or {}
        kind = p.get("kind") or ("interact" if t == "create_interact" else t)
        if t == "create_interact" and not p.get("cron"):
            raise HTTPException(400, "缺少定时表达式")

        if t == "create_template":
            with db() as conn:
                cur = conn.execute(
                    "INSERT INTO templates(user_id,name,kind,config,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (user.id, p.get("name") or "AI 模板", "interact",
                     json.dumps(cfg, ensure_ascii=False), int(time.time())))
            return {"message": "已创建模板", "detail": "模板 ID %d" % cur.lastrowid}

        with db() as conn:
            cur = conn.execute(
                "INSERT INTO interact_tasks(user_id,name,kind,cron,config,enabled,created_at,"
                "next_run) VALUES(?,?,?,?,?,1,?,0)",
                (user.id, p.get("name") or "AI 任务", "interact" if t == "create_interact" else kind,
                 p.get("cron") or "", json.dumps(cfg, ensure_ascii=False), int(time.time())))
        return {"message": "已创建任务", "detail": "任务 ID %d，去「任务」页可以改"
                % cur.lastrowid, "open_prog": False}

    if t == "create_dm":
        cfg = p.get("config") or {}
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO automation_rules(user_id,kind,account_id,interval,config,enabled,"
                "created_at) VALUES(?,'autodm',NULL,60,?,1,?)",
                (user.id, json.dumps(cfg, ensure_ascii=False), int(time.time())))
        return {"message": "已创建自动私信规则", "detail": "规则 ID %d" % cur.lastrowid}

    raise HTTPException(400, "未知的方案类型：%s" % t)


@router.get("/assets")
def ai_assets(user: CurrentUser = Depends(require_user)):
    """编号快捷插入：图片库 / 视频库 / 账号。"""
    with db() as conn:
        covers = conn.execute(
            "SELECT id,code FROM publish_covers WHERE user_id=? ORDER BY code",
            (user.id,)).fetchall()
        files = conn.execute(
            "SELECT id,code,name FROM publish_files WHERE user_id=? ORDER BY code",
            (user.id,)).fetchall()
        accs = conn.execute(
            "SELECT id,mid,uname FROM bili_accounts WHERE user_id=? ORDER BY id",
            (user.id,)).fetchall()
    return {
        "images": [{"code": c["code"], "url": "/api/publish/covers/%d/raw" % c["id"]}
                   for c in covers],
        "videos": [{"code": f["code"], "name": f["name"]} for f in files],
        "accounts": [{"id": a["id"], "mid": a["mid"], "uname": a["uname"]} for a in accs],
    }


@router.get("/presets")
def ai_presets(user: CurrentUser = Depends(require_user)):
    return {"presets": [
        {"id": "gm", "name": "游戏区 UP 主", "persona": "我是游戏区 up 主，回复要简短、带点梗；不接广告"},
        {"id": "study", "name": "学习区", "persona": "学习区账号，回复温和鼓励型，不接广告"},
        {"id": "vlog", "name": "生活 vlog", "persona": "生活区 vlog，回复亲切自然，偶尔用表情"},
    ]}


@router.get("/prompts")
def ai_prompts(user: CurrentUser = Depends(require_user)):
    return {"prompts": QUICK}


# ---------------------------------------------------------------- AI 配置
class ProfileIn(BaseModel):
    scope: str = "main"
    account_id: int | None = None
    name: str = ""
    model: str = ""
    persona: str = ""


@router.get("/profiles")
def list_profiles(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_profiles WHERE user_id=? ORDER BY id DESC", (user.id,)).fetchall()
        accs = conn.execute(
            "SELECT id,mid,uname FROM bili_accounts WHERE user_id=?", (user.id,)).fetchall()
        tpls = conn.execute(
            "SELECT id,name,created_at FROM ai_templates WHERE user_id=? ORDER BY id DESC",
            (user.id,)).fetchall()
    return {
        "profiles": [{"id": r["id"], "scope": r["scope"], "account_id": r["account_id"],
                      "name": r["name"], "model": r["model"], "persona": r["persona"],
                      "created_at": r["created_at"]} for r in rows],
        "accounts": [{"id": a["id"], "mid": a["mid"], "uname": a["uname"]} for a in accs],
        "templates": [{"id": t["id"], "name": t["name"]} for t in tpls],
        "models": MODELS,
        "scopes": SCOPES,
    }


@router.post("/profiles")
def save_profile(body: ProfileIn, user: CurrentUser = Depends(require_user)):
    if body.scope not in ("main", "dm", "nurture"):
        raise HTTPException(400, "未知的用途")
    if not body.name.strip():
        raise HTTPException(400, "请填写配置名称")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO ai_profiles(user_id,name,persona,scope,model,account_id,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user.id, body.name.strip(), body.persona, body.scope,
             body.model or MODELS[0]["id"], body.account_id, int(time.time())))
    return {"ok": True, "id": cur.lastrowid}


@router.delete("/profiles/{pid}")
def del_profile(pid: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        conn.execute("DELETE FROM ai_profiles WHERE id=? AND user_id=?", (pid, user.id))
    return {"ok": True}


@router.post("/profiles/{pid}/template")
def profile_to_template(pid: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        r = conn.execute("SELECT * FROM ai_profiles WHERE id=? AND user_id=?",
                         (pid, user.id)).fetchone()
        if not r:
            raise HTTPException(404, "配置不存在")
        cur = conn.execute(
            "INSERT INTO ai_templates(user_id,name,scope,model,persona,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (user.id, r["name"], r["scope"], r["model"], r["persona"], int(time.time())))
    return {"ok": True, "message": "已存为模板", "id": cur.lastrowid}


class FromTplIn(BaseModel):
    template_id: int
    account_id: int = 0


@router.post("/profiles/from-template")
def profile_from_template(body: FromTplIn, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        t = conn.execute("SELECT * FROM ai_templates WHERE id=? AND user_id=?",
                         (body.template_id, user.id)).fetchone()
        if not t:
            raise HTTPException(404, "模板不存在")
        cur = conn.execute(
            "INSERT INTO ai_profiles(user_id,name,persona,scope,model,account_id,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user.id, t["name"], t["persona"], t["scope"], t["model"],
             body.account_id or None, int(time.time())))
    return {"ok": True, "id": cur.lastrowid, "message": "已套用模板"}



@router.get("/templates")
def list_ai_templates(user: CurrentUser = Depends(require_user)):
    """AI 模板列表（原站 /api/ai/templates 的 GET 侧；DELETE 见 /templates/{tid}）。"""
    with db() as conn:
        rows = conn.execute(
            "SELECT id,name,scope,model,persona,created_at FROM ai_templates "
            "WHERE user_id=? ORDER BY id DESC", (user.id,)).fetchall()
    return {"templates": [dict(r) for r in rows]}


@router.delete("/templates/{tid}")
def del_template(tid: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        conn.execute("DELETE FROM ai_templates WHERE id=? AND user_id=?", (tid, user.id))
    return {"ok": True, "message": "已删除"}
