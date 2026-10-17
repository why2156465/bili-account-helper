from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\routers\accounts.py")
t = p.read_text(encoding="utf-8")

# 1) 请求体加上极验三要素
old_in = '''class SmsSendIn(BaseModel):
    tel: str
    cid: str = "86"
    proxy: str = ""'''
new_in = '''class SmsSendIn(BaseModel):
    tel: str
    cid: str = "86"
    proxy: str = ""
    # B站 发短信前必须过极验：challenge/validate/seccode 三者缺一不可，
    # 少一个就返回 -105「验证码错误」
    token: str = ""
    challenge: str = ""
    validate: str = ""
    seccode: str = ""'''
assert old_in in t
t = t.replace(old_in, new_in, 1)

# 2) 发短信时带上极验参数
old_send = '''            r = cli.http.post(
                PASSPORT + "/x/passport-login/web/sms/send",
                data={"cid": body.cid, "tel": tel, "source": "main-fe-header",
                      "token": "", "challenge": "", "validate": "", "seccode": ""},
            )'''
new_send = '''            r = cli.http.post(
                PASSPORT + "/x/passport-login/web/sms/send",
                data={"cid": body.cid, "tel": tel, "source": "main-fe-header",
                      "token": body.token or "",
                      "challenge": body.challenge or "",
                      "validate": body.validate or "",
                      "seccode": body.seccode or ""},
            )'''
assert old_send in t
t = t.replace(old_send, new_send, 1)

# 3) 错误提示更明确
old_err = '''    if j.get("code") != 0:
        raise HTTPException(400, j.get("message") or "短信发送失败")'''
new_err = '''    if j.get("code") != 0:
        code = j.get("code")
        if code == -105:
            raise HTTPException(
                400, "需要先完成滑块验证。点「发送验证码」时若弹出滑动验证请先滑过；"
                     "没弹出说明浏览器拦了第三方脚本。实在不行请改用扫码或 Cookie 导入。")
        raise HTTPException(400, j.get("message") or "短信发送失败")'''
assert old_err in t
t = t.replace(old_err, new_err, 1)

# 4) 新增：下发极验初始化参数
helper = '''@router.get("/accounts/sms/captcha")
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


@router.post("/accounts/sms/send")'''

old_def = '@router.post("/accounts/sms/send")'
assert old_def in t
t = t.replace(old_def, helper, 1)

p.write_text(t, encoding="utf-8")
print("accounts.py: 短信补齐极验链路")
