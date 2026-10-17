from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\routers\accounts.py")
t = p.read_text(encoding="utf-8")

old = '''    data = j.get("data") or {}
    if not data.get("qrcode_key"):
        raise HTTPException(502, "生成二维码失败：上游未返回 qrcode_key")
    return {"qr": data.get("url"), "sid": data.get("qrcode_key"), "proxy": body.proxy}'''

new = '''    data = j.get("data") or {}
    if not data.get("qrcode_key"):
        raise HTTPException(502, "生成二维码失败：上游未返回 qrcode_key")

    # 前端是 <img src="d.qr"> 直接渲染的，所以这里必须给「能显示的图片」。
    # B站 返回的 url 是扫码落地页（account.bilibili.com/h5/...），不是图片，
    # 直接塞进 img 会显示裂图 —— 本地把它画成二维码 PNG 的 data URI。
    qr_url = data.get("url") or ""
    return {"qr": _qr_data_uri(qr_url), "sid": data.get("qrcode_key"),
            "proxy": body.proxy, "url": qr_url}'''

assert old in t, "qr return not found"
t = t.replace(old, new, 1)

# 加工具函数
helper = '''def _qr_data_uri(text: str) -> str:
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


@router.post("/accounts/qr")'''

old_def = '@router.post("/accounts/qr")'
assert old_def in t
t = t.replace(old_def, helper, 1)

p.write_text(t, encoding="utf-8")
print("accounts.py: 二维码改为本地生成 data URI")
