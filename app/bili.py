"""cookie 解析、上游 B 站接口封装、风控识别。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from .config import settings

NEED_TICKET_ACTIONS = ("coin",)  # 投币要求网页端同源凭证


class BiliError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_cookie(raw: str) -> dict[str, str]:
    """支持三种来源：粘贴串、cookies.json、ck_xxx.json（bilibili cookie 插件导出）。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{") or raw.startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        items = data if isinstance(data, list) else data.get("cookies", [])
        out: dict[str, str] = {}
        if isinstance(items, dict):
            return {str(k): str(v) for k, v in items.items()}
        for it in items:
            if isinstance(it, dict) and "name" in it:
                out[str(it["name"])] = str(it.get("value", ""))
        return out
    out = {}
    for part in re.split(r"[;\n]", raw):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join("%s=%s" % (k, v) for k, v in cookies.items() if v)


def cookie_identity(cookies: dict[str, str]) -> dict[str, str]:
    return {
        "uid": cookies.get("DedeUserID", ""),
        "sessdata": cookies.get("SESSDATA", ""),
        "csrf": cookies.get("bili_jct", ""),
    }


def looks_valid_cookie(cookies: dict[str, str]) -> bool:
    ident = cookie_identity(cookies)
    return bool(ident["sessdata"] and ident["csrf"])


class BiliClient:
    """无状态封装；每个账号一个实例，共享连接池由调用方传入。"""

    def __init__(self, cookies: dict[str, str], *, proxy: str | None = None,
                 client: httpx.Client | None = None):
        self.cookies = cookies
        self.ident = cookie_identity(cookies)
        self._own = client is None
        self.http = client or httpx.Client(
            timeout=15,
            follow_redirects=True,
            proxy=proxy,
            headers={
                "User-Agent": settings.BILI_UA,
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com",
                "Cookie": cookie_header(cookies),
            },
        )

    # --- 生命周期 ---
    def close(self) -> None:
        if self._own:
            self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- 底层请求 ---
    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        r = self.http.get(settings.BILI_API + path, params=params or {})
        return self._unwrap(r)

    def post(self, path: str, data: dict[str, Any] | None = None,
             params: dict[str, Any] | None = None) -> dict:
        body = dict(data or {})
        body["csrf"] = self.ident["csrf"]
        r = self.http.post(settings.BILI_API + path, params=params or {}, data=body)
        return self._unwrap(r)

    @staticmethod
    def _unwrap(r: httpx.Response) -> dict:
        try:
            j = r.json()
        except ValueError:
            raise BiliError(-1, "上游返回非 JSON（HTTP %d）" % r.status_code)
        code = int(j.get("code", 0))
        if code != 0:
            raise BiliError(code, j.get("message") or j.get("msg") or "上游错误")
        return j.get("data") or {}

    # --- 账号信息 ---
    def nav(self) -> dict:
        return self.get("/x/web-interface/nav")

    def check(self) -> dict:
        """探活：返回 uid/nickname/avatar/is_login/coin_ready。"""
        data = self.nav()
        return {
            "is_login": bool(data.get("isLogin")),
            "uid": str(data.get("mid") or self.ident["uid"]),
            "nickname": data.get("uname", ""),
            "avatar": data.get("face", ""),
            "coin": data.get("money", 0),
            "level": data.get("level_info", {}).get("current_level"),
            "has_ticket": bool(self.cookies.get("bili_ticket")),
        }

    # --- 三连类动作 ---
    def like(self, aid: str | int) -> dict:
        return self.post("/x/web-interface/archive/like", {"aid": aid, "like": 1})

    def coin(self, aid: str | int, num: int = 1, select_like: int = 1) -> dict:
        if not self.ident["csrf"]:
            raise BiliError(61000, "缺少 bili_jct，无法投币")
        if not self.cookies.get("bili_ticket"):
            raise BiliError(61001, "bili_ticket 已过期，需重新扫码后投币")
        return self.post("/x/web-interface/coin/add",
                         {"aid": aid, "multiply": num, "select_like": select_like})

    def fav(self, aid: str | int, add: bool = True) -> dict:
        mid = self.ident["uid"]
        return self.post("/x/v3/fav/resource/deal",
                         {"rid": aid, "type": 2, "add_media_ids": "" if add else "",
                          "del_media_ids": "", "platform": "web"},
                         params={"csrf": self.ident["csrf"]})

    def share(self, aid: str | int) -> dict:
        return self.post("/x/web-interface/share/add", {"aid": aid})

    def follow(self, fid: str | int) -> dict:
        return self.post("/x/relation/modify", {"fid": fid, "act": 1, "re_src": 11})

    def dm_send(self, receiver_uid: str, content: str) -> dict:
        return self.post("/x/im/web/msg_send", {"receiver_id": receiver_uid,
                                                "receiver_type": 1, "msg_type": 1,
                                                "content": content})

    # --- 查询 ---
    def video_info(self, aid: str | int) -> dict:
        return self.get("/x/web-interface/view", {"aid": aid})

    def publish_info(self, page: int = 1) -> dict:
        return self.get("/x/space/wbi/arc/search",
                        {"mid": self.ident["uid"], "pn": page, "ps": 20})


# ---------------- 风控识别 ----------------
RISK_CODES = {
    -412: "被风控（-412），需降低频率或稍后重试",
    -352: "风控校验（-352），需滑块/验证码",
    61001: "凭证过期",
    61000: "缺少 CSRF",
    -400: "请求参数被拒",
    -101: "未登录或登录已失效",
}


def describe_error(exc: BiliError) -> tuple[str, str]:
    """返回 (状态, 人类可读文案)。状态给前端打标签：ok/expired/captcha。"""
    if exc.code in (-101, 61001):
        return "expired", RISK_CODES.get(exc.code, exc.message)
    if exc.code in (-352, -412):
        return "captcha", RISK_CODES.get(exc.code, exc.message)
    return "fail", RISK_CODES.get(exc.code, exc.message)


def backoff_seconds(coeff: float, base: float = 3.0, cap: float = 180.0) -> float:
    """操作间随机间隔：随风控系数放大，封顶。"""
    import random

    return min(cap, base * coeff * (0.6 + random.random()))


def ticket_expired(issued_at: int) -> bool:
    return (time.time() - issued_at) > settings.BILI_TICKET_TTL
