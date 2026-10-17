# 还原状态总表

逐页对照 `bili.mssss.cn`。**「依据」一列说明这一页是怎么来的**——这决定了它的可信度。

| 依据标记 | 含义 |
|---|---|
| **实测抓取** | 用真实账号登录后抓到的完整 HTML + 内联 JS，DOM 与交互按原文重建 |
| **契约反推** | 页面抓不到（需管理员等），但通过 403/405 探测确认了接口存在与方法 |
| **结构对齐** | 接口与数据字段已对齐，布局按原站 CSS 中已有的类组合还原 |

## 页面清单（24 个路由）

| # | 路径 | 页面 | 依据 | 对应接口 | 测试 |
|---|---|---|---|---|---|
| 1 | `/` | 落地页 | 实测抓取 | 无（纯静态） | pages |
| 2 | `/login` | 登录 / 异地验证 / 找回密码 | 实测抓取 | `/api/auth/*` | contract |
| 3 | `/register` | 注册 + 计算验证码 | 实测抓取 | `/api/auth/register` | contract |
| 4 | `/dashboard` | 总览 | 实测抓取 | `GET /api/dashboard` | pages |
| 5 | `/ai` | 33娘 | 实测抓取 | `/api/ai/*`（12 个） | ai_api |
| 6 | `/accounts` | 我的账号 | 实测抓取 | `/api/accounts`、`/api/img/proxy` | contract |
| 7 | `/accounts/add` | 添加账号 | 实测抓取 | `/api/accounts/{qr,sms,ck,ckfile}` | contract |
| 8 | `/publish` | 视频投稿 | 实测抓取 | `/api/publish/*`（18 个） | publish |
| 9 | `/messages` | 私信 | 实测抓取 | `/api/messages/*`（6 个） | messages |
| 10 | `/tasks` | 互赞任务 | 实测抓取 | `/api/tasks/{run,preview,progress}` | interact |
| 11 | `/scheduler` | 定时任务 | 实测抓取 | `/api/tasks`、`/api/automation/*` | scheduler |
| 12 | `/vip` | 会员与流量 | 实测抓取 | `/api/vip/*` | contract |
| 12b | `/vip/compare` | 权益对比 | 实测抓取 | `GET /api/vip/compare` | vip_compare |
| 13 | `/logs` | 操作日志 + 进度 | 实测抓取 | `/api/logs`、`/api/tasks/progress` | interact |
| 14 | `/settings` | 我的设置 | 实测抓取 | `/api/me/*`、`/api/proxy/*` | pages |
| 15 | `/notices` | 消息通知 | 实测抓取 | `/api/notices/*` | pages |
| 16 | `/me` | 我的 | 实测抓取 | `/api/vip/info`、`/api/dashboard` | pages |
| 17 | `/invite` | 邀请好友 | 实测抓取 | `/api/vip/invite` | pages |
| 18 | `/templates` | 模板管理 | 实测抓取 | `/api/templates/*` | pages |
| 19 | `/web` | 网页版 B 站 | 实测抓取 | `/api/web/*`（15 个） | web |
| 20 | `/trend` | 账号趋势 | 实测抓取 | `GET /api/trend?days=` | trend |
| 21 | `/library` | 视频库（旧路由，保留兼容） | 结构对齐 | `/api/library/*` | 冒烟 |
| 22 | `/admin234` | 管理后台 | **契约反推** | `/api/admin/*`（10 个） | admin |
| 23 | `/healthz` | 健康检查 | — | — | 冒烟 |

## 管理后台的还原程度（重点说明）

这一页**抓不到**——`cxd666` 是普通用户，`/admin234` 返回 307。
用普通账号探测后确认了 10 个真实接口存在：

```
GET  /api/admin/stats      GET  /api/admin/users     GET  /api/admin/orders
GET  /api/admin/logs       GET  /api/admin/accounts  GET  /api/admin/settings
GET  /api/admin/notices    GET  /api/admin/redeem
POST /api/admin/settings   POST /api/admin/notices
                            ↑ 由 405 Method Not Allowed 反推
```

全部返回 `403 {"detail":"需要管理员权限"}` —— 这本身就证明了路由存在、权限检查有效。

**所以**：接口路径、方法、权限行为是 1:1 的；页面布局是按这些接口能提供的数据设计的，
不是原站的 DOM。这是全项目唯一一处「接口真、页面推断」的地方。

## 未还原 / 已知差异

| 项 | 状态 | 说明 |
|---|---|---|

| ~~内部消息（站内信）~~ | **不存在** | 21 个归档页面全文搜索 `站内信`/`内部消息`/`admin_msg` 零命中，属早先臆测，已删除 |
| 定时投稿的执行 | **已实现** | `POST /api/publish/run-upload`：preupload → 分片 → add/v3 全链路，带预检与流量扣除（tests/upload.py 覆盖） |
| 定时任务自动执行 | **已修复** | worker 原先查不存在的 `tasks` 表，从未自动跑过；现改走 `interact_tasks` + `task_runs` 留痕（tests/worker_run.py 覆盖） |
| 上游 B 站接口 | 真实调用 | 无账号时所有依赖 B 站的功能会返回明确错误，不是假数据 |
| 管理后台页面布局 | 推断 | 见上一节 |

## 数据真实性说明

项目**不含任何 mock 数据**：

- 加号、体检、互赞、投稿、私信全部真实请求 `api.bilibili.com`；
- 无 B 站账号时，页面显示「请先添加账号」而不是伪造内容；
- 33娘在没配 `AI_API_KEY` 时走**本地规则引擎**（不是假回复，是确定性规则），
  配了 Key 就走真模型，两条路输出的 `actions` 结构一致；
- IP 归属地走 `ip-api.com` 真实查询（实测返回「中国 浙江 台州」）。

## 测试矩阵

| 套件 | 检查数 | 覆盖 |
|---|---|---|
| `tests/contract.py` | 44 | 接口契约与线上字段名对齐 |
| `tests/interact.py` | 28 | 6 种目标规则 + 执行状态机 |
| `tests/scheduler.py` | 41 | 任务 CRUD（PUT/PATCH）+ 自动化规则 |
| `tests/publish.py` | 38 | 分区/视频库/封面库/会话/记录 |
| `tests/messages.py` | 33 | 私信全链路 |
| `tests/ai_api.py` | 59 | 33娘 12 接口 + 方案执行 + 屏蔽词 |
| `tests/pages.py` | 70 | 16 个页面 + 导航一致性 + 匿名跳转 |
| \
`tests/admin.py` | 56 | 8 个 GET + 权限隔离 + 写操作 |
| `tests/upload.py` | 22 | 预检 + 分片序列 + 流量扣除 + 任务触发 |
| `tests/web.py` | 58 | 15 个 /api/web/* 契约 + 假上游参数校验 |
| `tests/fills.py` | 26 | ai/log、server-from-lib、ai/prompt、ai/templates |
| `tests/trend.py` | 34 | 快照差值语义 + 天数窗口 + 页面 |
| `tests/contract_static.py` | 172 个调用 | 静态检查：页面里每个 api() 都能解析到路由 |
| `tests/vip_compare.py` | 59 | 四档差异 + 间隔 30/30/15/5 + 过期回落 |
| `tests/schema_check.py` | 122 个列引用 | 静态校验：SELECT 的列必须真的在表里 |
| `tests/extra.py` | 52 | 补齐 32 个此前零覆盖的接口 |
| `tests/endpoint_coverage.py` | 147 个路由 | 静态校验：每个已注册接口都必须被测试调用过 |
| `tests/style_coverage.py` | 765 个类名 | 静态校验：模板用到的每个 class 都必须有 CSS 定义 |
| `tests/common_js.py` | 18 | node + vm 真跑 common.js 的工具函数（esc/fmtN/maskMid/accName…） |
| `tests/inline_js.py` | 23 个 script 块 | node --check 解析每个页面内联 JS |
| `tests/shared_utils.py` | 20 个导出 | 静态校验：页面不得重复实现公共工具 |
| `tests/field_contract.py` | 408 个字段读取 | 交叉校验：前端读的字段必须在后端响应/源码里有 |
| `tests/vip_buy.py` | 39 | 会员购买全链路：下单 → 支付 → 生效 |
| `tests/payload_contract.py` | 50 个写调用 | 前后端请求体字段对账（含 ES6 简写） |
| `tests/task_config.py` | 30 | 互赞 config 全结构 + 6 种目标规则 + 模板/任务往返 |
| `tests/target_edge.py` | 22 | 目标解析边界：空池/max<n/坏 UID/未知规则 |
| `tests/worker_recovery.py` | 25 | 崩溃恢复：所有退出路径都推进 next_run，不无限重试 |
| `tests/state_machine.py` | 15 | 执行线程异常时 STATE 不卡在 running=True |
| `tests/action_failure.py` | 29 | 凭据失效时立即停止剩余动作并中止任务 |
| `tests/smoke.py` | 53 | 端到端冒烟：注册→登录→建任务→AI→买会员→后台→退出 |
| `tests/worker_run.py` | 21 | 定时任务真的会被 worker 跑起来 |
| `tests/concurrency.py` | 19 | RunState 单例互斥 + 双任务同时到点不丢时间 |
| `tests/security.py` | 40 | 会话过期/封禁/跨用户隔离/额度上限/上传边界 |
| **合计** | **948** | 全部通过（33 个套件全部 exit=0，用 tests/run_all.py 一键跑） |

## 安全边界（tests/security.py 实证）

| 边界 | 行为 |
|---|---|
| 会话过期 | `sessions.expires_at` 到期后接口 401，且会话行从表里删除 |
| 账号封禁 | `users.banned=1` 后已登录会话立即 401（每次请求都查库） |
| 跨用户隔离 | B 访问 A 的账号 → 404；B 的任务列表为空 |
| 免费额度 | AI 对话第 11 次返回 403 并提示开通会员；账号数超限被拒 |
| 上传容量 | 超出 `video_lib_mb` 返回 413；文件名 `../../evil.mp4` 被清洗为不含 `..` 和 `/` |
| 匿名访问 | 全部业务接口 401/403 |
| 登录探测 | 不存在的用户与错误密码返回同一类响应，不泄露账号是否存在 |