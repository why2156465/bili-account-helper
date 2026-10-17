from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\routers\accounts.py")
t = p.read_text(encoding="utf-8")

# ---- 修 1：扫码生成走 passport 域名，不是 api 域名 ----
old = '''    try:
        with BiliClient({}) as cli:
            data = cli.get("/x/passport-login/web/qrcode/generate")
    except Exception as exc:
        raise HTTPException(502, "生成二维码失败：%s" % exc)
    return {"qr": data.get("url"), "sid": data.get("qrcode_key"), "proxy": body.proxy}'''

new = '''    # 注意：这个接口在 passport.bilibili.com 上，不在 api.bilibili.com。
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
    return {"qr": data.get("url"), "sid": data.get("qrcode_key"), "proxy": body.proxy}'''

assert old in t, "qr_create not found"
t = t.replace(old, new, 1)

p.write_text(t, encoding="utf-8")
print("accounts.py: 扫码生成改走 PASSPORT 域名")
