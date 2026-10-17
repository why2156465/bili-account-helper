from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\routers\accounts.py")
t = p.read_text(encoding="utf-8")

old = '''def _cookies_from_passport_headers(resp: httpx.Response, extra_url: str = "") -> dict:
    jar = {c.name: c.value for c in resp.cookies.jar}
    for part in (extra_url or "").split("?"):
        for kv in part.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                if k in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
                         "sid", "buvid3", "buvid4"):
                    jar.setdefault(k, v)
    return jar'''

new = '''def _cookies_from_passport_headers(resp: httpx.Response, extra_url: str = "") -> dict:
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
    return jar'''

assert old in t, "cookie helper not found"
t = t.replace(old, new, 1)

# 体检时把投稿数也落库（原来 videos 永远是 0）
old_upd = '''        conn.execute(
            "UPDATE bili_accounts SET status='ok', uname=?, face=?, fans=?, coins=?, level=?, "
            "coin_ok=?, last_check=? WHERE id=?",
            (nav.get("uname") or row["uname"], nav.get("face") or row["face"],
             int(rel.get("follower") or 0), float(nav.get("money") or 0),
             int((nav.get("level_info") or {}).get("current_level") or 0),
             1 if cookies.get("bili_ticket") else 0,
             time.strftime("%Y-%m-%d %H:%M"), acc_id),
        )'''

new_upd = '''        # 投稿数：relation/stat 不带，要从空间稿件列表的 page.count 拿
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
        )'''

assert old_upd in t, "update block not found"
t = t.replace(old_upd, new_upd, 1)

# 快照也记真实投稿数
t = t.replace(
'''        save_snapshot(conn, user.id, acc_id, views=up_views, likes=up_likes,
                      fans=int(rel.get("follower") or 0),
                      videos=int(row["videos"] or 0))''',
'''        save_snapshot(conn, user.id, acc_id, views=up_views, likes=up_likes,
                      fans=int(rel.get("follower") or 0), videos=videos)''')

p.write_text(t, encoding="utf-8")
print("accounts.py: 修 urldecode + 补 videos 落库")
