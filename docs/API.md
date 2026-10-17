# API 契约

所有接口返回 JSON；失败统一 `{"detail": "中文文案"}`，HTTP 状态码：
`400` 参数/业务错误、`401` 未登录、`403` 无权限或额度不足、`404` 不存在、
`413` 超出视频库额度、`429` 频率限制、`502` 上游 B 站错误。

会话用 Cookie `bp_session`（HttpOnly, SameSite=Lax）。前端 `static/js/common.js` 的 `BP.api()`
遇到 401 会自动跳 `/login`。

## 认证

| 方法 | 路径 | 请求体 | 返回 |
|---|---|---|---|
| GET | `/api/auth/captcha/needed` | - | `{"need":true}` |
| GET | `/api/auth/captcha` | - | `{"q":"9 - 6 = ?","token":"..."}` |
| POST | `/api/auth/send_code` | `{email,scene,captcha,captcha_token}` | `{ok:true,message}` |
| POST | `/api/auth/register` | `{username,password,email,code,invite}` | `{ok:true,id,invite_code,message}` |
| POST | `/api/auth/login` | `{username,password,captcha,captcha_token}` | 成功 `{ok,role,uid,profile}`；需异地验证 `{need_verify:true,ticket,email,message}` |
| POST | `/api/auth/login_verify/resend` | `{ticket,captcha,captcha_token}` | `{ok:true,email,message}` |
| POST | `/api/auth/login_verify` | `{ticket,code}` | `{ok:true,role,uid,profile}` |
| POST | `/api/auth/forgot` | `{email,scene:"reset",captcha,captcha_token}` | `{ok:true,message}` |
| POST | `/api/auth/reset` | `{email,code,password}` | `{ok:true,message}` |
| POST | `/api/auth/logout` | - | `{ok:true}` |

约束（与原站前端校验一致）：
- 用户名 6–20 位，字母/数字/下划线/中文；
- 密码 8–20 位且至少一个字母；
- 同邮箱只能注册一个账号；邀请码不存在则拒绝注册；
- 注册送 300MB，被邀请人用邀请码注册后邀请人 +30MB。

## B 站账号

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/accounts` | `{items:[{id,uid,nickname,raw_nickname,avatar,status,has_ticket,coeff,checked_at}]}` |
| POST | `/api/accounts/import` | `{raw}` 或 `{items:[...]}`，逐条探活，返回 `{items:[{ok,message,...}],ok_count}` |
| POST | `/api/accounts/{id}/check` | 重新探活，返回 `{ok,status,...}` |
| PATCH | `/api/accounts/{id}` | `{display_nm,uid,avatar}` 自定义展示 |
| DELETE | `/api/accounts/{id}` | 删除 |
| GET | `/api/accounts/{id}/cookie_mask` | `{mask:"keys=SESSDATA|bili_jct len=234"}` |
| GET | `/api/accounts/qr/start` | `{url,qrcode_key}` |
| GET | `/api/accounts/qr/poll?key=` | `status: pending|ok|fail`，`ok` 时 Cookie 已落库 |

`raw` 支持三种格式：`SESSDATA=x; bili_jct=y`、`cookies.json` 数组、`ck_xxx.json`。

## 互赞与任务

| 方法 | 路径 | 请求体 |
|---|---|---|
| POST | `/api/like/run` | `{account_id,targets:["123"],actions:["like","coin"],comment_text}` |
| GET | `/api/like/actions` | 动作目录（含 `need_ticket` 标记） |
| GET/POST | `/api/tasks` | `{kind,title,account_id,config,cron,enabled}` |
| PATCH/DELETE | `/api/tasks/{id}` | 同上 / 删除 |
| POST | `/api/tasks/{id}/toggle` | 启停 |
| POST | `/api/tasks/{id}/run` | 立即执行，返回 `{ok,status,message,logs}` |
| GET | `/api/tasks/{id}/runs` | 最近执行记录 |

`kind`：`like` / `upload` / `dm` / `monitor` / `ai`。
`cron` 五段式，非法表达式返回 400（先校验表达式，再校验额度）。

`/api/like/run` 遇到 `-352/-412` 会：把账号置为 `captcha`、`coeff *= 1.5`、立刻中止本任务并返回
`{aborted:true,message}`，剩余目标不再尝试。

## 33娘

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ai/quota` | `{used,cap,plan}` |
| POST | `/api/ai/chat` | `{message,history:[{role,content}]}` → `{ok,reply,plan,quota}` |

`plan` 结构（前端据此渲染「一键创建」）：

```json
{"action":"create_task","kind":"like","title":"每天 9 点给 UP 主 123456 点赞",
 "cron":"0 9 * * *","config":{"actions":["like"],"targets":["123456"]}}
```

## 看板 / 会员 / 视频库 / 代理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/dashboard/stats` | 账号数/上限、在线、任务、额度、7 天趋势、最近执行与操作 |
| GET | `/api/logs?limit=50` | 操作流水 |
| GET | `/api/vip/products` | 套餐、流量包、当前状态、额度表 |
| POST | `/api/vip/order` | `{kind:"plan"|"pack",key}` → `{order_no,amount,pay_url}` |
| GET | `/api/vip/pay_mock?order=` | 开发用模拟到账（生产删除） |
| POST | `/api/vip/pay_callback` | 支付回调 `{order_no}`（生产需自加签名校验） |
| GET | `/api/library` | `{cap_mb,used_mb,items}` |
| POST | `/api/library/upload` | multipart `file`，超额度返回 413 |
| DELETE | `/api/library/{id}` | 删除文件与记录 |
| GET/POST/DELETE | `/api/proxy` | 代理池增删查 |
| GET | `/api/dm/threads`、`/api/dm/threads/{id}` | 私信会话与消息 |
| POST | `/api/dm/threads/{id}/send` | `{text}`，走真实私信接口并计额度 |
| GET | `/api/bili/recommend`、`/api/bili/search?q=` | 网页版 B 站只读数据 |

## 管理后台

前缀 `/api/admin`，仅 `role=admin` 可访问，普通用户 403。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/overview` | 用户数、会员数、账号数、任务数、24h 执行/失败、最近 30 用户 |
| POST | `/users/{id}/ban` | `{banned:true}` 封禁并踢掉会话 |
| POST | `/users/{id}/plan` | `{plan:"vip",days:30}` |
| POST | `/users/{id}/traffic` | `{mb:1024}` 增减流量 |
| GET | `/tasks?limit=50` | 全站任务 |
| GET | `/audit?limit=100` | 管理操作审计 |

## 额度表（`app/config.py` 的 `PLAN_LIMITS`）

| 项 | 免费 | 会员 |
|---|---|---|
| 每天互赞 | 5 | 888 |
| 每天投稿 | 1 | 88 |
| 账号数 | 2 | 50 |
| 定时任务 | 1（每天 1 次） | 不限 |
| 指定 UID 目标 | 1 | 不限 |
| 每天 AI 对话 | 10 | 888 |
| AI 任务数 | 1 | 不限 |
| 主动私信/账号/天 | 10 | 不限 |
| 视频库 | 200MB | 5GB |

监控间隔：免费 60 分钟；会员月卡 50 / 季卡 30 / 年卡 1 分钟（`MONITOR_INTERVAL_BY_PLAN`）。
