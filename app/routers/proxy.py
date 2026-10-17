"""IP 代理模块 —— 对齐原站 /api/proxy 契约。

    GET    /api/proxy/geo?proxy=...        -> {ok, via, proxy, exit:{ok,ip,country,province,city,isp}}
    GET    /api/proxy/geo/me?city=...      -> {me:{ok,...}, same_city}
    GET    /api/proxy/list                 -> {proxies, policy, user_proxy, pool, accounts, note}
    POST   /api/proxy   {url,name}         -> {proxies:[...]}
    DELETE /api/proxy/{id}
    POST   /api/proxy/test {url|id}        -> {ok, ip, ms, error}
    POST   /api/proxy/account/{id} {url|proxy_id|clear} -> {proxy}
    POST   /api/proxy/account/{id}/verify  -> {ip, country, isp, proxy, direct_ip, same_as_direct}
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, client_ip, require_user
from ..db import db

router = APIRouter(prefix="/api/proxy", tags=["proxy"])

# 用于检测出口 IP 的轻量公共接口，按顺序尝试
IP_ECHO = (
    "https://api.ipify.org?format=json",
    "http://ip-api.com/json/?lang=zh-CN",
    "https://ipinfo.io/json",
)


def _lookup_geo(ip: str) -> dict:
    """归属地查询。ip-api 支持 IPv4/IPv6 且返回中文地名，作为主用。"""
    out = {"country": "", "province": "", "city": "", "isp": ""}
    try:
        with httpx.Client(timeout=8, headers={"User-Agent": "Mozilla/5.0"}) as c:
            d = c.get("http://ip-api.com/json/%s" % ip, params={"lang": "zh-CN"}).json()
        if d.get("status") == "success":
            out.update({
                "country": d.get("country", ""),
                "province": d.get("regionName", ""),
                "city": d.get("city", ""),
                "isp": d.get("isp", "") or d.get("org", ""),
            })
            return out
    except Exception:
        pass
    try:
        with httpx.Client(timeout=8) as c:
            d = c.get("https://ipinfo.io/%s/json" % ip).json()
        if d.get("ip"):
            out.update({
                "country": d.get("country", ""),
                "province": d.get("region", ""),
                "city": d.get("city", ""),
                "isp": d.get("org", ""),
            })
    except Exception:
        pass
    return out

GEO_NOTE = ("账号独立代理优先级最高（不受平台策略影响）；未设账号代理时按平台策略走。")


class ProxyIn(BaseModel):
    url: str
    name: str = ""


class TestIn(BaseModel):
    url: str = ""
    id: int | None = None


class AccountProxyIn(BaseModel):
    url: str = ""
    proxy_id: int | None = None
    clear: bool = False


ALLOWED_SCHEMES = {"http", "https", "socks5", "socks5h", "socks4"}


def normalize(url: str) -> str:
    """允许 ip:port / user:pass@ip:port / socks5://ip:port 三种写法。

    不带协议的按 http 补全；协议不在白名单里的直接判非法（返回空串，
    由调用方报 400）—— 早先只补不校验，`socks9://bad` 也能存进去，
    等到真去连的时候才炸，错误信息还看不出是代理写错了。
    """
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    scheme = u.split("://", 1)[0].lower()
    if scheme not in ALLOWED_SCHEMES:
        return ""
    # 必须有主机名
    rest = u.split("://", 1)[1]
    host = rest.split("@")[-1].split("/")[0]
    if not host or ":" not in host and not host.count("."):
        return ""
    return u


def mask_proxy(url: str) -> str:
    """把 user:pass@ 里的密码换成 ***，列表接口不能明文吐密码。"""
    if not url or "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if "://" in head:
        scheme, cred = head.split("://", 1)
        scheme += "://"
    else:
        scheme, cred = "", head
    if ":" in cred:
        user = cred.split(":", 1)[0]
        cred = "%s:***" % user
    return scheme + cred + "@" + tail


def _client(proxy: str | None) -> httpx.Client:
    return httpx.Client(timeout=12, proxy=proxy, follow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0"})


def probe_exit(proxy: str | None) -> dict:
    """通过（或不通过）代理取出口 IP 与归属地。"""
    p = normalize(proxy) if proxy else None
    last_err = ""
    try:
        with _client(p) as c:
            for url in IP_ECHO:
                try:
                    j = c.get(url).json()
                except Exception as exc:
                    last_err = "%s: %s" % (urlparse(url).hostname, exc)
                    continue
                ip = j.get("ip") or j.get("query") or j.get("ip_addr") or ""
                if not ip:
                    continue
                out = {"ok": True, "ip": ip, "country": "", "province": "",
                       "city": "", "isp": ""}
                out.update(_lookup_geo(ip))
                return out
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": False, "error": last_err or "所有探测接口都失败"}


@router.get("/geo")
def geo(proxy: str = "", user: CurrentUser = Depends(require_user)):
    ex = probe_exit(proxy)
    return {"ok": bool(ex.get("ok")), "via": "proxy" if proxy else "direct",
            "proxy": proxy, "exit": ex}


@router.get("/geo/me")
def geo_me(city: str = "", user: CurrentUser = Depends(require_user)):
    """看「使用者自己」在哪，用来判断代理和自己是否同城。"""
    me = probe_exit(None)
    same = None
    if city and me.get("ok"):
        mine = (me.get("city") or me.get("province") or "").strip()
        if mine:
            same = (mine == city)
    return {"me": me, "same_city": same}


@router.get("/list")
def list_proxy(user: CurrentUser = Depends(require_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT id,name,url,enabled,created_at FROM proxy_pool WHERE user_id=? ORDER BY id",
            (user.id,)).fetchall()
        accs = conn.execute(
            "SELECT id,uname,proxy FROM bili_accounts WHERE user_id=? AND proxy<>''",
            (user.id,)).fetchall()
    return {
        "proxies": [{"id": r["id"], "name": r["name"], "url": mask_proxy(r["url"]),
                     "raw": r["url"], "enabled": r["enabled"],
                     "created_at": r["created_at"]} for r in rows],
        "policy": "off",
        "user_proxy": "",
        "pool": [],
        "accounts": [{"id": a["id"], "uname": a["uname"],
                      "proxy": mask_proxy(a["proxy"])} for a in accs],
        "note": GEO_NOTE,
    }


@router.post("")
def add_proxy(body: ProxyIn, user: CurrentUser = Depends(require_user)):
    url = normalize(body.url)
    if not urlparse(url).hostname:
        raise HTTPException(400, "代理地址格式不对")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO proxy_pool(user_id,name,url,enabled,created_at) VALUES(?,?,?,1,?)",
            (user.id, body.name or "", url, int(time.time())),
        )
        new_id = cur.lastrowid
        rows = conn.execute(
            "SELECT id,name,url,enabled,created_at FROM proxy_pool WHERE user_id=? ORDER BY id",
            (user.id,)).fetchall()
    return {"ok": True, "id": new_id,
            "proxies": [{"id": r["id"], "name": r["name"], "url": mask_proxy(r["url"]),
                         "raw": r["url"], "enabled": r["enabled"],
                         "created_at": r["created_at"]} for r in rows]}


@router.delete("/{pid}")
def del_proxy(pid: int, user: CurrentUser = Depends(require_user)):
    with db() as conn:
        conn.execute("DELETE FROM proxy_pool WHERE id=? AND user_id=?", (pid, user.id))
        rows = conn.execute(
            "SELECT id,name,url FROM proxy_pool WHERE user_id=? ORDER BY id", (user.id,)).fetchall()
    return {"ok": True, "proxies": [dict(r) for r in rows]}


@router.post("/test")
def test_proxy(body: TestIn, user: CurrentUser = Depends(require_user)):
    url = normalize(body.url)
    if not url and body.id:
        with db() as conn:
            row = conn.execute("SELECT url FROM proxy_pool WHERE id=? AND user_id=?",
                               (body.id, user.id)).fetchone()
        url = normalize(row["url"]) if row else ""
    if not url:
        raise HTTPException(400, "缺少代理地址")
    t0 = time.time()
    ex = probe_exit(url)
    ms = int((time.time() - t0) * 1000)
    if ex.get("ok"):
        return {"ok": True, "ip": ex.get("ip"), "ms": ms}
    return {"ok": False, "error": ex.get("error", "不通"), "ms": ms}


@router.post("/account/{acc_id}")
def set_account_proxy(acc_id: int, body: AccountProxyIn,
                      user: CurrentUser = Depends(require_user)):
    with db() as conn:
        acc = conn.execute("SELECT id FROM bili_accounts WHERE id=? AND user_id=?",
                           (acc_id, user.id)).fetchone()
        if not acc:
            raise HTTPException(404, "账号不存在")
        if body.clear:
            proxy = ""
        elif body.proxy_id:
            row = conn.execute("SELECT url FROM proxy_pool WHERE id=? AND user_id=?",
                               (body.proxy_id, user.id)).fetchone()
            if not row:
                raise HTTPException(404, "代理不存在")
            proxy = normalize(row["url"])
        else:
            proxy = normalize(body.url)
            if proxy and not urlparse(proxy).hostname:
                raise HTTPException(400, "代理地址格式不对")
        conn.execute("UPDATE bili_accounts SET proxy=? WHERE id=?", (proxy, acc_id))
    return {"ok": True, "proxy": proxy}


@router.post("/account/{acc_id}/verify")
def verify_account_proxy(acc_id: int, user: CurrentUser = Depends(require_user)):
    """用这个账号自己的代理去访问外网，看真实出口 IP 是否等于服务器直连。"""
    with db() as conn:
        row = conn.execute("SELECT proxy FROM bili_accounts WHERE id=? AND user_id=?",
                           (acc_id, user.id)).fetchone()
        if not row:
            raise HTTPException(404, "账号不存在")
        proxy = row["proxy"] or ""

    direct = probe_exit(None)
    via = probe_exit(proxy) if proxy else direct
    same = bool(proxy and direct.get("ok") and via.get("ok") and direct["ip"] == via["ip"])
    return {
        "ip": via.get("ip", ""),
        "country": via.get("country", ""),
        "isp": via.get("isp", ""),
        "proxy": proxy,
        "direct_ip": direct.get("ip", ""),
        "same_as_direct": same,
        "ok": bool(via.get("ok")),
        "error": via.get("error", ""),
    }
