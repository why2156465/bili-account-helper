# 抓取与还原报告

目标：`bili.mssss.cn`（IP `1.12.223.182`，nginx，HTTP/1.1 + TLSv1.3，DNSPod 托管）。
抓取时间以响应头 `Date` 为准。原始文件全部保存在 `_archive/`，未做任何修改。

## 1. 抓取过程与遇到的问题

本机 Windows schannel 与目标 TLS 栈握手失败（`SEC_E_NO_CREDENTIALS` / curl 35），
`requests`、`curl.exe`、`Invoke-WebRequest` 全部走不通。改用 Python 原生 socket + OpenSSL 3.0.16
手工发 HTTP/1.1 请求、自己做 chunked 解码，握手正常（`TLS_AES_256_GCM_SHA384`）。

```
TLS OK TLSv1.3 ('TLS_AES_256_GCM_SHA384', 'TLSv1.3', 256)
HTTP/1.1 200 OK
Content-Length: 16470
```

## 2. 站点真实结构

| 路径 | 状态 | 大小 | 说明 |
|---|---|---|---|
| `/`、`/?home=1` | 200 | 16470 | 落地页（同一份内容） |
| `/login` | 200 | 12896 | 登录 / 异地验证 / 找回密码，三屏在一个页面里切换 |
| `/register` | 200 | 7797 | 注册 + 计算验证码弹层 + 邀请码自动填写 |
| `/vip` | 307 → `/` | 0 | 需要登录 |
| `/dashboard` | 307 → `/login` | 0 | 需要登录 |
| `/admin234` | 307 → `/login` | 0 | 管理后台（路径藏在页面 JS 里：`r.role==='admin' ? '/admin234'`） |
| `/static/app.css?v=20261018d` | 200 | 111794 | 全站样式 |
| `/static/client.css?v=20261018d` | 200 | 22034 | 客户端式界面样式 |
| `/static/img/bili_logo.svg` | 200 | 15942 | B 站 logo |
| `/static/img/mascot/m22_idea.webp` | 200 | 33874 | 22娘 |
| `/static/img/mascot_2233_door.png` | 200 | 13446 | 22娘 & 33娘 |
| `/static/img/nodata.png` | 200 | 54144 | 空状态图 |
| `/api/auth/captcha` | 200 | 92 | `{"q":"9 - 6 = ?","token":"eyJhIjozLCJfZSI6MTc5MjIxNjUxMH0.a6f53..."}` |
| `/api/auth/captcha/needed` | 200 | 13 | `{"need":true}` |
| `/robots.txt` | 200 | 26 | `text/plain` |
| `/favicon.ico`、`/manifest.json`、`/sw.js`、`/static/` | 404 | 22 | JSON 404，说明后端是统一 JSON 错误处理 |

其他特征：
- 响应头带 `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet`，不打算被收录；
- 全套安全头由服务端下发（`X-Content-Type-Options`、`X-Frame-Options= SAMEORIGIN`、
  `Referrer-Policy`、`Permissions-Policy`、`COOP: same-origin`、HSTS 1 年）；
- 页面里的 API 路径共 10 条：`captcha`、`captcha/needed`、`send_code`、`register`、`login`、
  `login_verify`、`login_verify/resend`、`forgot`、`reset`。

## 3. 从代码里还原出的业务规则

注册页 `<script>` 里的原话（保留在 `app/templates/register.html`）：

```js
var BONUS='300', REWARD='30';            // 被邀请人 300MB，邀请人 30MB
var m = location.search.match(/[?&]ref=([^&#]+)/);
```

登录页：

```js
location.href = (r.role==='admin') ? '/admin234' : '/dashboard';
var RK_U='bp_rem_u', RK_P='bp_rem_p';     // 「记住密码」存 localStorage，密码只做 btoa
```

- 异地登录：`/api/auth/login` 返回 `{need_verify:true, ticket, email}`，
  前端切到第 2 屏，**必须先算验证码再点「发送验证码」**（`/login_verify/resend`），
  验证码 60 秒冷却，验证通过后该 IP 被记住；
- 密码规则：8–20 位、至少一个字母（前后端都校验）；
- 用户名规则：至少 6 位；
- 一人一号：同邮箱只能注册一次，多开小号封 IP。

## 4. 前端还原策略

| 原文件 | 本项目 | 处理 |
|---|---|---|
| `index.html` | `app/templates/index.html` | 结构 1:1 重排，文本逐条比对；把内联的「33娘 对话示例」提成 `.lp-ai-mock` 样式块 |
| `login.html` | `app/templates/login.html` | 内联 JS 保留原逻辑，只把 `api()`/`toast()` 换成 `BP.api`/`BP.toast`，重发倒计时改用 `BP.cd` |
| `register.html` | `app/templates/register.html` | 同上；`?ref=CODE` 由服务端注入 `preset`，并保留前端兜底解析 |
| `app.css` / `client.css` | `app/static/css/` | **原样沿用**，仅在 `app.css` 末尾追加缺类（见下） |
| 四张图片 | `app/static/img/` | 原样 |

原页面引用了但原 CSS 里缺失的类，本仓库在 `app.css` 末尾补齐（否则首页英雄区右侧、会员对比区
会塌成无样式裸文本）：`.lp-eyebrow` `.lp-scrollhint` `.lp-cmp`（含 `.col/.vip/.vip-flag`）
`.lp-ai` `.lp-ai-mock`，另外补了通用的 `.empty` `.pg`。

## 5. 已知缺口

- `/dashboard`、`/vip`、`/admin234` 需要登录，抓不到，所以这三页的**布局是重建的**：
  沿用原站 CSS 里已有的类（`.topbar` `.mainnav` `.stat` `.card` `.plancard` `.cmp-t` `.vip-badge`
  `.quota-chip` `.ai-shell` 等 300 多个类名），配色变量取自原 `app.css` 的 `:root`；
- 原站后端闭源，业务数据（B 站接口调用、支付、邮件模板）无法还原，本仓库按前端契约重写。

## 6. 复现抓取（如需更新快照）

```python
import ssl, socket
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
ctx.set_alpn_protocols(["http/1.1"])
s = ctx.wrap_socket(socket.create_connection(("bili.mssss.cn", 443), timeout=15), server_hostname="bili.mssss.cn")
s.sendall(b"GET / HTTP/1.1\r\nHost: bili.mssss.cn\r\nUser-Agent: Mozilla/5.0\r\n"
          b"Accept-Encoding: identity\r\nConnection: close\r\n\r\n")
buf = b""
while (d := s.recv(65536)):
    buf += d
open("index.html", "wb").write(buf.split(b"\r\n\r\n", 1)[1])
```
