from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\routers\proxy.py")
t = p.read_text(encoding="utf-8")

# ---- 1) normalize 加协议白名单 ----
old = '''def normalize(url: str) -> str:
    """允许 ip:port / user:pass@ip:port / socks5://ip:port 三种写法。"""
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    return u'''

new = '''ALLOWED_SCHEMES = {"http", "https", "socks5", "socks5h", "socks4"}


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
    return scheme + cred + "@" + tail'''

assert old in t, "normalize not found"
t = t.replace(old, new, 1)

# ---- 2) 列表接口掩码密码 ----
t = t.replace(
'''    return {
        "proxies": [dict(r) for r in rows],''',
'''    return {
        "proxies": [{"id": r["id"], "name": r["name"], "url": mask_proxy(r["url"]),
                     "raw": r["url"], "enabled": r["enabled"],
                     "created_at": r["created_at"]} for r in rows],''')

t = t.replace(
'''        "accounts": [{"id": a["id"], "uname": a["uname"], "proxy": a["proxy"]} for a in accs],''',
'''        "accounts": [{"id": a["id"], "uname": a["uname"],
                      "proxy": mask_proxy(a["proxy"])} for a in accs],''')

p.write_text(t, encoding="utf-8")
print("proxy.py: 协议白名单 + 密码掩码")
