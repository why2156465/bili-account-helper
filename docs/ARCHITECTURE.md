# 架构说明

## 请求分层

```
浏览器 ── Cookie bp_session ──> FastAPI
                                ├─ middleware: 安全头 + /api/* 禁缓存
                                ├─ routers/pages.py     页面（未登录 307 → /login）
                                ├─ routers/*_api.py     JSON API（未登录 401）
                                │    ├─ auth.require_user / require_admin   身份
                                │    ├─ quota.ensure_*                      额度
                                │    └─ bili.BiliClient                     上游
                                └─ templates/ Jinja2 + static/ 原生 JS
```

页面守卫和 API 守卫是分开的：页面用 `RedirectResponse(307)`，API 用 `HTTPException(401)`。
这样前端 `BP.api()` 的「401 就跳登录」不会和页面跳转互相打架。

## 数据流：一次「一键三连」

1. `POST /api/like/run {account_id,targets,actions}`
2. `quota.ensure_quota(user,"like",len(targets)*len(actions))` —— 超限 403；
3. 从 `bili_accounts.cookie_enc` 取出密文，`security.decrypt_cookie()` 得到原始 Cookie 串，
   `bili.parse_cookie()` 解析成字典；
4. `BiliClient` 按动作调上游接口，每次都带 `csrf = bili_jct`：
   - 点赞 `POST /x/web-interface/archive/like`
   - 投币 `POST /x/web-interface/coin/add`（缺 `bili_ticket` 直接抛 61001，前端标「需重扫码投币」）
   - 收藏 `POST /x/v3/fav/resource/deal`
   - 分享 `POST /x/web-interface/share/add`
   - 关注 `POST /x/relation/modify`
   - 评论 `POST /x/v2/reply/add`
   - 刷播放 `POST /x/click-interface/web/heartbeat`
5. 每次成功 `bump_usage(user,"like")` 并写 `op_logs`；每次失败按 `describe_error()` 分类：
   - `-101/61001` → `expired`，账号标记失效；
   - `-352/-412` → `captcha`，`coeff *= 1.5`，**立刻中止**整个任务；
6. 动作之间 `time.sleep(backoff_seconds(coeff))`，即 `3 * coeff * (0.6+rand)` 秒，封顶 180 秒。

## 数据流：定时任务

```
tasks.next_run_at <= now
  → app.worker (每 WORKER_TICK 秒扫一次)
    → tasks.run_task(id)
      → 写 task_runs(status=running)
      → 按 kind 选 RUNNERS[kind]
      → 完成后写 status/message/ended_at
      → cron 任务重算 next_run_at；一次性任务自动 enabled=0
```

`cron.next_run()` 是逐分钟推进的纯 Python 实现，支持 `*` `*/n` `a-b` `a-b/n` 和逗号列表，
一年内无触发点则返回 0（建任务时就直接 400）。

## 额度模型

`quota_usage(user_id, kind, day, n)` 按天累计，`kind ∈ {like, upload, ai_chat, dm, ai_runs}`。
`PLAN_LIMITS` 里 `-1` 表示不限。判断顺序固定为：**先参数校验，再额度校验**
（否则用户填了非法 cron 会先被告知「任务数已满」，容易误以为要开会员）。

## 33娘 AI

```
POST /api/ai/chat
  ├─ AI_API_KEY 存在 → httpx 调 {AI_API_BASE}/chat/completions，system prompt 说明人格
  │                    回复末尾若带 {"action":"create_task",...} 则剥离成 plan 字段
  └─ 不存在         → local_reply(): 关键词命中 6 组知识库，并用正则从需求里抽
                       「几点 / UID」生成同样的 plan
```

两种路径输出的 `plan` 结构一致，前端渲染同一个「一键创建」按钮，调用 `POST /api/tasks`。

## 会话与安全

- `sessions(sid, user_id, ip, ua, expires_at)`，sid 为 32 字节 urlsafe token；
- 封禁：`UPDATE users SET banned=1` 并 `DELETE FROM sessions WHERE user_id=?`，旧 Cookie 立刻 401；
- 改密码：同样清空该用户全部会话；
- 登录失败计数落在 `op_logs(action='login_fail', target=ip)`，900 秒内 8 次即 429；
- 异地登录：`known_ips(user_id, ip)` 命中即免验证，否则发 6 位邮箱码，10 分钟有效。

## 还缺什么（想做完可以照这个顺序）

1. **投稿上传**：`run_upload_task()` 只做了 `/x/vu/web/add/v3` 的提交；
   完整流程需要补 `preupload` → 分片上传 → `web/complete`，并把 `video_files` 里的文件读成分片。
2. **私信会话同步**：`dm_threads/dm_messages` 表已建，拉取上游私信列表的定时同步没写。
3. **评论区挖掘**：`/x/v2/reply` 分页 + 关键词过滤，结果直接喂给 `dm` 任务的 `targets`。
4. **Web 端 B 站播放器**：`client.css` 里 `.wb-*` 那套样式已经在了，需要接 `x/player/playurl`。
5. **兑换码**：`/api/vip/redeem` 目前 501，`op_logs(action='order')` 的结构可直接复用。
