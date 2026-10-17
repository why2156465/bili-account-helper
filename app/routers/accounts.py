"""账号模块 —— 严格对齐原站契约（从 accounts.html / accounts_add.html 内联 JS 逐条读出）。

原站调用（含请求体字段名，全部照抄）：
    GET    /api/accounts                              -> {accounts:[...]}
    POST   /api/accounts/ck        {ck,note,proxy}     -> {account:{uname,...}}
    POST   /api/accounts/ckfile    multipart(file,proxy)-> {results:[{ok,uname,mid,coins,coin_ok}]}
    POST   /api/accounts/qr        {proxy}             -> {qr, sid}
    GET    /api/accounts/qr/{sid}?proxy=...            -> {status:ok|scanning|fail, account:{uname}}
    POST   /api/accounts/sms/send  {tel,cid,proxy}     -> {captcha_key,buvid3,buvid4,is_new}
    POST   /api/accounts/sms/login {tel,cid,code,...}  -> {account:{uname}}
    POST   /api/accounts/{id}/check                    -> {account:{status}}
    DELETE /api/accounts/{id}
    GET    /api/accounts/{id}/login-log                -> {list,count,proxy_ip}
    GET    /api/img/proxy?u=...                        -> 头像代理

账号对象字段（render() 直接消费，一个不能少）：
    id, mid, uname, face, fans, videos, coins, level, status,
    coin_ok, cred_source, last_check_at, proxy, alias_name, alias_mid, alias_face
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from ..auth import CurrentUser, require_user
from ..bili import BiliClient, BiliError, describe_error, looks_valid_cookie, parse_cookie
from ..db import db
from ..quota import PLAN_LIMITS, effective_plan
from .trend_api import save_snapshot
from ..security import decrypt_cookie, encrypt_cookie, mask_cookie

router = APIRouter(prefix="/api", tags=["accounts"])

PASSPORT = "https://passport.bilibili.com"


class CkIn(BaseModel):
    ck: str = ""
    note: str = ""
    proxy: str = ""


class QrIn(BaseModel):
    proxy: str = ""


class SmsSendIn(BaseModel):
    tel: str
    cid: str = "86"
    proxy: str = ""
    # B站 发短信前必须过极验：challenge/validate/seccode 三者缺一不可，
    # 少一个就返回 -105「验证码错误」
    token: str = ""
    challenge: str = ""
    geetest_validate: str = ""      # 不叫 validate：会和 BaseModel.validate 撞名
    seccode: str = ""


class SmsLoginIn(BaseModel):
    tel: str
    code: str
    cid: str = "86"
    captcha_key: str = ""
    buvid3: str = ""
    buvid4: str = ""
    proxy: str = ""


class AliasIn(BaseModel):
    alias_name: str | None = None
    alias_mid: str | None = None
    alias_face: str | None = None


# ------------------------------------------------------------------ 映射
def to_account(row: Any) -> dict:
    return {
        "id": row["id"],
        "mid": row["mid"] or "",
        "uname": row["uname"] or "",
        "face": row["face"] or "",
        "fans": row["fans"] or 0,
        "videos": row["videos"] or 0,
        "coins": row["coins"] or 0,
        "level": row["level"] or 0,
        "status": row["status"] or "ok",
        "coin_ok": bool(row["coin_ok"]),
        "cred_source": row["cred_source"] or "ck",
        "last_check_at": row["last_check"] or "",
        "proxy": row["proxy"] or "",
        "alias_name": row["alias_name"] or "",
        "alias_mid": row["alias_mid"] or "",
        "alias_face": row["alias_face"] or "",
    }


def _limit(user) -> int:
    return PLAN_LIMITS[effective_plan(user)]["max_accounts"]


def _count(conn, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) c FROM bili_accounts WHERE user_id=?",
                        (user_id,)).fetchone()["c"]


def _save_account(conn, user, cookies: dict, source: str, proxy: str = "") -> dict:
    """探活 + 落库 + 返回前端对象。"""
    limit = _limit(user)
    if limit and _count(conn, user.id) >= limit:
        raise HTTPException(400, "账号数量已达上限（%d 个），开通会员可提升额度" % limit)

    try:
        with BiliClient(cookies) as cli:
            nav = cli.nav()
            mid = str(nav.get("mid") or cookies.get("DedeUserID", ""))
            try:
                rel = cli.get("/x/relation/stat", {"vmid": mid})
                upstat = cli.get("/x/space/upstat", {"mid": mid})
            except BiliError:
                rel, upstat = {}, {}
    except BiliError as exc:
        raise HTTPException(400, describe_error(exc)[1])
    except Exception as exc:
        raise HTTPException(502, "连接 B 站失败：%s" % exc)

    if not nav.get("isLogin"):
        raise HTTPException(400, "Cookie 已失效，请重新登录")

    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO bili_accounts(user_id,mid,uname,face,fans,videos,coins,level,status,"
        "coin_ok,cred_source,last_check,proxy,cred_enc,cred_mask,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,'ok',?,?,?,?,?,?,?)",
        (user.id, mid, nav.get("uname") or "", nav.get("face") or "",
         int(rel.get("follower") or 0),
         int((upstat.get("archive") or {}).get("count") or 0),
         float(nav.get("money") or 0),
         int((nav.get("level_info") or {}).get("current_level") or 0),
         1 if cookies.get("bili_ticket") else 0,
         source, time.strftime("%Y-%m-%d %H:%M"), proxy or "",
         encrypt_cookie(json.dumps(cookies, ensure_ascii=False)),
         mask_cookie(json.dumps(cookies, ensure_ascii=False)), now),
    )
    row = conn.execute("SELECT * FROM bili_accounts WHERE id=?", (cur.lastrowid,)).fetchone()
    return to_account(row)


def _cookies_from_passport_headers(resp: httpx.Response, extra_url: str = "") -> dict:
    """从 Set-Cookie 和 B站 返回的 crossDomain url 里凑出登录 Cookie。

    坑：扫码/短信登录成功后，B站 把登录凭据放在 `data.url` 的查询串里，
    而且 **SESSDATA 是 percent-encoded 的**（值里的逗号是 %2C）。
    早先用 split('&') 手工切，拿到的 SESSDATA 带着 %2C 原样存进库，
    B站 一验就说「未登录或登录已失效」—— 扫码明明成功，加完号立刻失效。

    必须走 urllib.parse.parse_qs 自动 unquote。
    """
    from urllib.parse import parse_qs, urlparse

    jar = {c.name: c.value for c in resp.cookies.jar}
    if not extra_url:
        return jar
    try:
        q = parse_qs(urlparse(extra_url).query)
    except Exception:
        return jar
    wanted = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
              "sid", "buvid3", "buvid4", "bili_ticket")
    for k in wanted:
        if k in q and q[k] and q[k][0]:
            jar.setdefault(k, q[k][0])          # parse_qs 已 urldecode
    return jar


# ------------------------------------------------------------------ 列表
@router.get("/accounts")
def list_accounts(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM bili_accounts WHERE user_id=? ORDER BY id",
                            (user.id,)).fetchall()
    return {"accounts": [to_account(r) for r in rows]}


# ------------------------------------------------------------------ 体检 / 删除
@router.post("/accounts/{acc_id}/check")
def check_account(acc_id: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        row = conn.execute("SELECT * FROM bili_accounts WHERE id=? AND user_id=?",
                           (acc_id, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "账号不存在")
        cookies = parse_cookie(decrypt_cookie(row["cred_enc"]))
        try:
            with BiliClient(cookies) as cli:
                nav = cli.nav()
                mid = str(nav.get("mid") or row["mid"])
                rel = cli.get("/x/relation/stat", {"vmid": mid})
                upstat = cli.get("/x/space/upstat", {"mid": mid})
        except BiliError as exc:
            status, msg = describe_error(exc)
            conn.execute("UPDATE bili_accounts SET status=?, last_check=? WHERE id=?",
                         (status, time.strftime("%Y-%m-%d %H:%M"), acc_id))
            acc = conn.execute("SELECT * FROM bili_accounts WHERE id=?", (acc_id,)).fetchone()
            return {"account": to_account(acc), "ok": False, "message": msg}
        except Exception as exc:
            raise HTTPException(502, "网络错误：%s" % exc)

        # 投稿数：relation/stat 不带，要从空间稿件列表的 page.count 拿
        videos = int(row["videos"] or 0)
        try:
            arc = cli.get("/x/space/wbi/arc/search", {"mid": mid, "ps": 1, "pn": 1})
            videos = int((arc.get("page") or {}).get("count") or videos)
        except Exception:
            pass

        conn.execute(
            "UPDATE bili_accounts SET status='ok', uname=?, face=?, fans=?, videos=?, "
            "coins=?, level=?, coin_ok=?, last_check=? WHERE id=?",
            (nav.get("uname") or row["uname"], nav.get("face") or row["face"],
             int(rel.get("follower") or 0), videos,
             float(nav.get("money") or 0),
             int((nav.get("level_info") or {}).get("current_level") or 0),
             1 if cookies.get("bili_ticket") else 0,
             time.strftime("%Y-%m-%d %H:%M"), acc_id),
        )
        # 记一条当天快照，供 /api/trend 画曲线（同一天重复体检只更新，不新增）
        up_views = int((upstat.get("archive") or {}).get("view") or 0)
        up_likes = int(upstat.get("likes") or 0)
        save_snapshot(conn, user.id, acc_id, views=up_views, likes=up_likes,
                      fans=int(rel.get("follower") or 0), videos=videos)
        acc = conn.execute("SELECT * FROM bili_accounts WHERE id=?", (acc_id,)).fetchone()
    return {"account": to_account(acc), "ok": True}


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        if not conn.execute("SELECT id FROM bili_accounts WHERE id=? AND user_id=?",
                            (acc_id, user.id)).fetchone():
            raise HTTPException(404, "账号不存在")
        conn.execute("DELETE FROM bili_accounts WHERE id=?", (acc_id,))
    return {"ok": True}


# ------------------------------------------------------------------ Cookie 导入
@router.post("/accounts/ck")
def add_by_ck(body: CkIn, user: CurrentUser = Depends(require_user)):
    cookies = parse_cookie(body.ck)
    if not cookies:
        raise HTTPException(400, "没有解析到 Cookie，请检查格式")
    if not looks_valid_cookie(cookies):
        raise HTTPException(400, "缺少 SESSDATA 或 bili_jct，无法登录")
    with db() as conn:
        acc = _save_account(conn, user, cookies, "paste", body.proxy)
    return {"ok": True, "account": acc}


@router.post("/accounts/ckfile")
async def add_by_ckfile(file: UploadFile = File(...), proxy: str = Form(""),
                        user: CurrentUser = Depends(require_user)):
    """文件内含多个账号时批量导入，逐个给出结果。"""
    raw = (await file.read()).decode("utf-8", "replace")
    # cookies.json 可能是数组，多账号用换行分隔也支持
    chunks: list[str] = []
    text = raw.strip()
    if text.startswith("["):
        try:
            arr = json.loads(text)
            if isinstance(arr, list) and arr and isinstance(arr[0], dict) and "SESSDATA" in json.dumps(arr[0]):
                chunks = [json.dumps(arr, ensure_ascii=False)]
            else:
                chunks = [json.dumps(x, ensure_ascii=False) for x in arr]
        except json.JSONDecodeError:
            chunks = [text]
    else:
        chunks = [ln for ln in text.splitlines() if "SESSDATA" in ln] or [text]

    results = []
    with db() as conn:
        for c in chunks:
            cookies = parse_cookie(c)
            if not looks_valid_cookie(cookies):
                results.append({"ok": False, "error": "缺少 SESSDATA 或 bili_jct",
                                "uname": "", "mid": ""})
                continue
            try:
                acc = _save_account(conn, user, cookies, "ckfile", proxy)
                results.append({"ok": True, "uname": acc["uname"], "mid": acc["mid"],
                                "coins": acc["coins"], "coin_ok": acc["coin_ok"]})
            except HTTPException as exc:
                results.append({"ok": False, "error": exc.detail, "uname": "", "mid": ""})
            except Exception as exc:
                results.append({"ok": False, "error": str(exc), "uname": "", "mid": ""})
    return {"results": results}


# ------------------------------------------------------------------ 扫码
def _qr_data_uri(text: str) -> str:
    """把文本画成二维码 PNG 的 data URI（前端 <img src> 可直接用）。"""
    import base64
    import io

    try:
        import qrcode
    except ImportError:                       # 没装 qrcode 时退回空串，前端会提示
        return ""

    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _looks_like_qr_payload(text: str) -> bool:
    return bool(text) and ("qrcode_key" in text or text.startswith("http"))


@router.post("/accounts/qr")
def qr_create(body: QrIn, user: CurrentUser = Depends(require_user)):
    """原站返回 {qr: 二维码图片/链接, sid: 轮询句柄}。"""
    # 注意：这个接口在 passport.bilibili.com 上，不在 api.bilibili.com。
    # 用 BiliClient.get()（拼 settings.BILI_API）会 404，必须直接打 PASSPORT。
    try:
        with BiliClient({}) as cli:
            r = cli.http.get(PASSPORT + "/x/passport-login/web/qrcode/generate")
            j = r.json()
    except Exception as exc:
        raise HTTPException(502, "生成二维码失败：%s" % exc)
    if j.get("code") != 0:
        raise HTTPException(502, "生成二维码失败：%s" % j.get("message", "上游返回异常"))
    data = j.get("data") or {}
    if not data.get("qrcode_key"):
        raise HTTPException(502, "生成二维码失败：上游未返回 qrcode_key")

    # 前端是 <img src="d.qr"> 直接渲染的，所以这里必须给「能显示的图片」。
    # B站 返回的 url 是扫码落地页（account.bilibili.com/h5/...），不是图片，
    # 直接塞进 img 会显示裂图 —— 本地把它画成二维码 PNG 的 data URI。
    qr_url = data.get("url") or ""
    return {"qr": _qr_data_uri(qr_url), "sid": data.get("qrcode_key"),
            "proxy": body.proxy, "url": qr_url}


@router.get("/accounts/qr/{sid}")
def qr_poll(sid: str, proxy: str = "", user: CurrentUser = Depends(require_user)):
    try:
        with BiliClient({}) as cli:
            r = cli.http.get(PASSPORT + "/x/passport-login/web/qrcode/poll",
                             params={"qrcode_key": sid})
            j = r.json()
            data = j.get("data") or {}
    except Exception as exc:
        raise HTTPException(502, "轮询失败：%s" % exc)

    code = data.get("code")
    if code == 86101:
        return {"status": "waiting", "message": "等待扫码"}
    if code == 86090:
        return {"status": "scanning", "message": "已扫码，请确认"}
    if code != 0:
        return {"status": "waiting", "code": code, "message": data.get("message", "等待扫码")}

    jar = _cookies_from_passport_headers(r, data.get("url") or "")
    if not looks_valid_cookie(jar):
        return {"status": "fail", "message": "扫码成功但未取到有效 Cookie"}
    with db() as conn:
        acc = _save_account(conn, user, jar, "qr", proxy)
    return {"status": "ok", "account": acc}


# ------------------------------------------------------------------ 手机验证码
@router.get("/accounts/sms/captcha")
def sms_captcha(user: CurrentUser = Depends(require_user)):
    """下发 B站 极验初始化参数，前端拿它去弹滑块。

    B站 发短信的链路是：
        GET  /x/passport-login/captcha       -> token + geetest{challenge, gt}
        前端用 gt/challenge 跑极验，拿到 validate + seccode
        POST /x/passport-login/web/sms/send  -> 带 token/validate/seccode
    少这一步短信永远发不出去（返回 -105）。
    """
    try:
        with BiliClient({}) as cli:
            r = cli.http.get(PASSPORT + "/x/passport-login/captcha",
                             params={"source": "main-fe-header"})
            j = r.json()
    except Exception as exc:
        raise HTTPException(502, "获取验证参数失败：%s" % exc)
    if j.get("code") != 0:
        raise HTTPException(502, "获取验证参数失败：%s" % (j.get("message") or "上游异常"))
    d = j.get("data") or {}
    gt = d.get("geetest") or {}
    return {
        "type": d.get("type") or "geetest",
        "token": d.get("token") or "",
        "challenge": gt.get("challenge") or "",
        "gt": gt.get("gt") or "",
    }


@router.post("/accounts/sms/send")
def sms_send(body: SmsSendIn, user: CurrentUser = Depends(require_user)):
    tel = body.tel.strip()
    if len(tel) < 6:
        raise HTTPException(400, "请输入正确的手机号")
    try:
        with BiliClient({}) as cli:
            r = cli.http.post(
                PASSPORT + "/x/passport-login/web/sms/send",
                data={"cid": body.cid, "tel": tel, "source": "main-fe-header",
                      "token": body.token or "",
                      "challenge": body.challenge or "",
                      "validate": body.geetest_validate or "",
                      "seccode": body.seccode or ""},
            )
            j = r.json()
            jar = {c.name: c.value for c in r.cookies.jar}
    except Exception as exc:
        raise HTTPException(502, "发送失败：%s" % exc)
    if j.get("code") != 0:
        code = j.get("code")
        if code == -105:
            raise HTTPException(
                400, "需要先完成滑块验证。点「发送验证码」时若弹出滑动验证请先滑过；"
                     "没弹出说明浏览器拦了第三方脚本。实在不行请改用扫码或 Cookie 导入。")
        raise HTTPException(400, j.get("message") or "短信发送失败")
    d = j.get("data") or {}
    return {
        "ok": True,
        "captcha_key": d.get("captcha_key", ""),
        "buvid3": jar.get("buvid3", ""),
        "buvid4": jar.get("buvid4", ""),
        "is_new": bool(d.get("is_new")),
    }


@router.post("/accounts/sms/login")
def sms_login(body: SmsLoginIn, user: CurrentUser = Depends(require_user)):
    try:
        with BiliClient({}) as cli:
            r = cli.http.post(
                PASSPORT + "/x/passport-login/web/login/sms",
                data={"cid": body.cid, "tel": body.tel.strip(), "code": body.code.strip(),
                      "source": "main-fe-header", "captcha_key": body.captcha_key,
                      "keep_cookie": "1", "buvid3": body.buvid3},
            )
            j = r.json()
            jar = _cookies_from_passport_headers(r, (j.get("data") or {}).get("url", ""))
    except Exception as exc:
        raise HTTPException(502, "登录失败：%s" % exc)
    if j.get("code") != 0:
        raise HTTPException(400, j.get("message") or "验证码错误")
    if not looks_valid_cookie(jar):
        raise HTTPException(400, "登录成功但没拿到有效 Cookie，可能需要人工验证")
    with db() as conn:
        acc = _save_account(conn, user, jar, "sms", body.proxy)
    return {"ok": True, "account": acc}


# ------------------------------------------------------------------ 隐私资料
@router.post("/accounts/{acc_id}/alias")
def set_alias(acc_id: int, body: AliasIn, user: CurrentUser = Depends(require_user)):
    fields, values = [], []
    for col in ("alias_name", "alias_mid", "alias_face"):
        v = getattr(body, col)
        if v is not None:
            fields.append("%s=?" % col)
            values.append(v or None)
    if not fields:
        raise HTTPException(400, "没有要修改的内容")
    values += [acc_id, user.id]
    with db() as conn:
        cur = conn.execute("UPDATE bili_accounts SET %s WHERE id=? AND user_id=?" % ",".join(fields),
                           values)
        if not cur.rowcount:
            raise HTTPException(404, "账号不存在")
        row = conn.execute("SELECT * FROM bili_accounts WHERE id=?", (acc_id,)).fetchone()
    return {"ok": True, "account": to_account(row)}


_FACE_DIR = "data/faces"


@router.post("/accounts/{acc_id}/alias-face")
async def upload_alias_face(acc_id: int, file: UploadFile = File(...),
                            user: CurrentUser = Depends(require_user)):
    import os
    from ..config import PROJECT_DIR

    d = PROJECT_DIR / "data" / "faces"
    d.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(file.filename or ".png")[1] or ".png"
    name = "u%d_a%d%s" % (user.id, acc_id, ext)
    (d / name).write_bytes(await file.read())
    url = "/api/img/face/" + name
    with db() as conn:
        conn.execute("UPDATE bili_accounts SET alias_face=? WHERE id=? AND user_id=?",
                     (url, acc_id, user.id))
    return {"ok": True, "url": url}


@router.get("/img/face/{name}")
def get_face(name: str):
    from fastapi.responses import FileResponse
    from ..config import PROJECT_DIR

    p = PROJECT_DIR / "data" / "faces" / name
    if not p.exists():
        raise HTTPException(404, "不存在")
    return FileResponse(str(p))


# ------------------------------------------------------------------ B 站登录记录
@router.get("/accounts/{acc_id}/login-log")
def login_log(acc_id: int, user: CurrentUser = Depends(require_user)):
    from urllib.parse import urlparse

    with db() as conn:
        row = conn.execute("SELECT * FROM bili_accounts WHERE id=? AND user_id=?",
                           (acc_id, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "账号不存在")
        cookies = parse_cookie(decrypt_cookie(row["cred_enc"]))
        proxy = row["proxy"] or ""

    items, err = [], ""
    try:
        with BiliClient(cookies) as cli:
            data = cli.get("/x/safecenter/login/log", {"page": 1, "page_size": 20})
        for it in (data.get("list") or data.get("records") or []):
            items.append({
                "ip": it.get("ip") or it.get("loginIp") or "",
                "geo": it.get("location") or it.get("geo") or it.get("ipLocation") or "—",
                "time_at": it.get("time") or it.get("loginTime") or it.get("ctime") or "",
            })
    except BiliError as exc:
        err = describe_error(exc)[1]
    except Exception as exc:
        err = "网络错误：%s" % exc

    proxy_ip = ""
    if proxy:
        try:
            host = urlparse(proxy if "://" in proxy else "http://" + proxy).hostname
        except ValueError:
            host = ""
        if host and all(c.isdigit() or c == "." for c in host):
            proxy_ip = host
    return {"list": items, "count": len(items), "proxy_ip": proxy_ip, "error": err}


# ------------------------------------------------------------------ 头像代理
@router.get("/img/proxy")
def img_proxy(u: str):
    if not u.startswith(("http://", "https://")):
        raise HTTPException(400, "非法地址")
    try:
        r = httpx.get(u, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0",
                               "Referer": "https://www.bilibili.com/"})
    except Exception as exc:
        raise HTTPException(502, "拉取失败：%s" % exc)
    if r.status_code != 200:
        raise HTTPException(502, "上游返回 %d" % r.status_code)
    return Response(content=r.content,
                    media_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})
