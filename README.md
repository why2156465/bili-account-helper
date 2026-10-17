# B站账号管理助手 · 开源重写版

> ## ⚠️ 素材版权声明
>
> `app/static/img/` 下的 `bili_logo.svg`、`mascot_2233_door.png`、`m22_idea.webp`、`nodata.png`
> 是**哔哩哔哩（bilibili）的商标与官方形象素材**，版权归哔哩哔哩所有。
> 它们在本仓库中**仅作为界面还原研究的引用样本保留**，不在本项目的 MIT 许可范围内，
> 不可视为可自由使用的自有资产。
>
> **商用、二次分发或对外部署前，请自行替换为你拥有合法权利的素材**，并移除任何暗示与哔哩哔哩官方
> 存在关联的表述。替换方式见下方「素材替换」。因使用上述素材产生的任何权利纠纷与本项目作者无关。

B 站多账号运营后台的开源实现：扫码 / 验证码 / Cookie 三种加号方式，一键三连、投稿、评论、私信，
定时任务、33娘 AI 助手、数据看板、会员额度、管理后台。后端 FastAPI + SQLite，前端 Jinja2 + 原生 JS，
没有前端构建步骤，`pip install` 后一条命令即可跑起来。

## 这个仓库怎么来的

原站 `bili.mssss.cn` 公开可见的是 4 个页面 + 2 个 CSS + 4 张图；登录后还有 16 个功能页。
本仓库用真实账号登录抓取了全部 16 个页面（约 1.1MB HTML + 内联 JS），逐模块还原。
**逐页还原状态见 [docs/STATUS.md](docs/STATUS.md)**；
**审计与踩坑记录见 [docs/AUDIT.md](docs/AUDIT.md)**（18 个真实缺陷的成因与修法）：24 个页面路由、147 个 API，
23 个页面按实测 HTML + 内联 JS 重建，1 个（管理后台）是接口反推（页面 307 抓不到，
但 10 个 `/api/admin/*` 用 403/405 探明确认了路径与方法）。
真实前端引用的 92 个端点、19 个站内链接，现在全部有对应实现。
本仓库做了两件事：

1. **还原前端**：抓下来的页面、样式、图片全部归档在 `_archive/`，页面结构和文案按原站 1:1 重建，
   CSS 直接沿用抓到的 `app.css` / `client.css`（仅补齐原页面引用了但样式表里缺失的类）。
2. **从零实现后端**：原站是闭源的，所以按前端暴露的 API 契约重写了一套可运行的实现
   （见 `docs/API.md` 的契约表格）。所有 `/api/*` 端点、请求体字段、错误文案都与前端调用一致。

> 说明：`blog`/业务数据、支付通道、真实的 B 站接口调用逻辑属于原站私有部分，本仓库给的是
> **结构完全对齐的参考实现 + 真实 B 站上游接口封装**，你需要填自己的 SMTP、AI Key 和支付回调。

## 快速开始

```bash
python -m pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"   # Windows: set SECRET_KEY=...
python -m app.cli create-admin admin you@example.com     # 交互式输入密码
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 <http://127.0.0.1:8000>。没配 SMTP 时验证码会打印到控制台（`[MAIL-DEV]` 前缀），
所以本地不做任何配置也能完整走通注册 → 登录 → 异地验证。

跑测试（全部用临时库，不碰真实数据，不访问 B 站）：

```bash
python tests/run_all.py       # 33 个套件，895 项检查
```

其中 8 个是**静态交叉检查**，专治「测试全绿但功能其实是坏的」：

| 检查器 | 防的是什么 |
|---|---|
| `contract_static.py` | 页面调用了后端不存在的接口 |
| `schema_check.py` | SQL 查了表里不存在的列 |
| `endpoint_coverage.py` | 接口注册了但没有任何测试打过它 |
| `style_coverage.py` | 模板用了没有 CSS 定义的 class（页面裸奔） |
| `inline_js.py` | 页面内联脚本有语法错误 |
| `shared_utils.py` | 各页面重复实现公共工具函数导致行为漂移 |
| `field_contract.py` | 前端读的字段后端不返回 |
| `payload_contract.py` | 前端发的字段后端模型不收 |

这些检查器不是摆设——它们分别抓出过真实缺陷：账号页昵称恒为空（读 `a.name`
但接口给的是 `uname`）、`/api/me/settings` 查不存在的列导致 500、
会员购买全链路不可用（前端发 `key` 后端读 `id`）。

## 目录结构

```
app/
  main.py            FastAPI 装配、安全响应头、404 分流
  config.py          全部配置走环境变量；会员额度表 PLAN_LIMITS
  db.py              SQLite 建表 + 每请求事务（BEGIN IMMEDIATE，避免 database is locked）
  security.py        PBKDF2 口令、计算验证码、邮箱码散列、Cookie 的 Fernet 加解密、邀请码
  auth.py            会话、当前用户、页面/API 守卫、操作日志
  quota.py           会员额度判定（互赞/投稿/账号数/任务数/AI/私信/流量）
  bili.py            Cookie 解析（粘贴串/cookies.json/ck_xxx.json）、B 站上游封装、风控识别
  cron.py            五段 cron 解析与 next_run（纯标准库）
  tasks.py           上游客户端封装与单动作执行
  worker.py          调度进程：扫 interact_tasks，按 kind 分派到互赞/投稿执行器
  mailer.py          SMTP 发信，未配置则降级为控制台输出
  cli.py             建管理员、重置密码
  routers/           22 个模块：pages、auth_api、accounts、interact、scheduler_api、
                     publish、publish_upload、messages、ai_api、web_api、trend_api、
                     vip_api、vip_compare、dashboard_api、logs_api、templates_api、
                     me_api、proxy、admin_api、fills、library、misc
  templates/         24 个页面模板（含 admin/ 子目录）
  static/            css/{app,client}.css 沿用原站；css/custom.css 是本项目补的组件样式；
                     js/common.js 公共工具；img/ 原站素材（见上方版权声明）
_archive/            原站抓取快照（HTML/CSS/图片），用于对照还原
docs/                STATUS.md（逐页还原状态）、API 契约、架构说明、侦查记录
tests/               33 个套件：23 个功能测试 + 8 个静态交叉检查 + run_all 汇总
```

## 功能对照

| 能力 | 前端入口 | 后端 |
|---|---|---|
| 邮箱注册（一人一号）+ 邀请码奖励 | `/register?ref=CODE` | `POST /api/auth/register` |
| 计算验证码 → 邮箱验证码 | 注册/找回密码/异地登录 | `GET /api/auth/captcha`、`POST /api/auth/send_code` |
| 异地登录邮箱二次验证 | `/login` 第 2 屏 | `POST /api/auth/login_verify` |
| 找回密码 | `/login` 第 3 屏 | `POST /api/auth/forgot`、`/api/auth/reset` |
| Cookie 批量导入 + 探活 | 看板「导入 Cookie 账号」 | `POST /api/accounts/import` |
| 扫码登录 | `GET /api/accounts/qr/start` + `/qr/poll` | 拿到 Cookie 后落库并探活 |
| 一键三连（点赞/投币/收藏/评论/分享/关注/刷播放） | 看板「一键互赞」 | `POST /api/like/run` |
| 定时任务（cron，含「0 9 * * *」） | 看板「定时任务」 | `POST /api/tasks` |
| 33娘 AI（对话 + 一键生成任务） | 右下角浮窗 | `POST /api/ai/chat` |
| 数据看板 / 7 天趋势 / 操作记录 | `/dashboard` | `GET /api/dashboard/stats` |
| 会员 / 流量包 / 权益对比 | `/vip` | `POST /api/vip/order` |
| 视频库（投稿用，本地上传不扣流量） | `/library` | `POST /api/library/upload` |
| 自定义 IP 代理 | `/settings` | `/api/proxy` |
| 管理后台（封禁/改会员/加流量/审计） | `/{ADMIN_PATH}` | `/api/admin/*` |

## 配置项

| 变量 | 说明 | 默认 |
|---|---|---|
| `SECRET_KEY` | 会话签名 + Cookie 加密主密钥。**生产必须换**，改了旧 Cookie 会解不开 | `dev-only-change-me` |
| `DB_PATH` | SQLite 路径 | `data/app.db` |
| `ADMIN_PATH` | 管理后台路径（原站是 `admin234`） | `admin234` |
| `SMTP_HOST/PORT/USER/PASS/FROM` | 发信；不配则只打印控制台 | 空 |
| `AI_API_BASE/AI_API_KEY/AI_MODEL` | 33娘走 OpenAI 兼容接口；不配则用本地规则引擎 | 空 / `gpt-4o-mini` |
| `WORKER_ENABLED` | 是否在 web 进程里起调度线程 | `true` |
| `CAPTCHA_TTL` / `CODE_TTL` / `CODE_RESEND` | 计算题 300s，邮箱码 600s，重发冷却 60s | 见 `config.py` |
| `LOGIN_MAX_FAIL` / `LOGIN_FAIL_WINDOW` | 同 IP 900 秒内失败 8 次即锁 | 8 / 900 |

部署要点：反代后记得传 `X-Forwarded-For`（异地登录判定和失败计数都按真实 IP）；
`DEBUG=false` 时会话 Cookie 自动加 `Secure`。

## 安全设计

- 口令：PBKDF2-HMAC-SHA256，12 万轮，随机盐。
- Cookie 留资：`SESSDATA/bili_jct` 等整串用 `Fernet(SHA256(SECRET_KEY))` 加密后入库，
  接口只返回键名和长度；解密只发生在与 B 站交互的那一刻。
- 邮箱验证码：只存 `SHA256(SECRET_KEY + code + email)`，明文不落库；一次性消费 + 过期 + 重发冷却。
- 计算验证码：服务端出题、一次性 token、输错即作废（防止穷举）。
- 会话：服务端 sessions 表 + HttpOnly Cookie；封禁用户时其会话立即失效。
- 风控：上游 `-352/-412` 会把账号标记为 `captcha` 并把 `coeff` 放大 1.5 倍，
  后续动作间隔按 `coeff` 指数退避；`-101/61001` 标记为 `expired`。

## 素材替换

`app/static/img/` 里的素材按上方的版权声明处理。替换时保持文件名与目录不变即可，无需改代码：

| 文件 | 用途 | 建议尺寸 |
|---|---|---|
| `bili_logo.svg` | 顶栏 / 页脚 logo（`<img height:20-26px>`） | 宽 140 × 高 64，建议用你自己的横版 logo |
| `mascot/m22_idea.webp` | 首页 33娘 区插图（`.lp-idea`） | 宽 ≤ 660，比例随意 |
| `mascot_2233_door.png` | 首页底部 CTA 图（`.final-art`，宽 190） | 建议 380×380 以上透明底 PNG |
| `nodata.png` | favicon + 空状态图（`.empty img`） | 512×512 PNG |

替换后刷新页面即可生效，CSS 里没有任何硬编码的图片尺寸依赖。若你要彻底移除，
把对应 `<img>` 换成纯色 div 也不会破坏布局。

> 附带提醒：`app/templates/` 里的文案（「哔哩哔哩」「B 站」「22娘 / 33娘」等）同样带有指向性，
> 对外发布时建议一并改为中性描述，避免被误认为官方或授权产品。

## 与原站的差异

- 原站闭源，后端是按前端契约重建的，**不是**反编译产物。
- 支付：本站不内置真实支付通道，`/api/vip/order` 只建单，`/api/vip/pay_mock` 是开发用模拟到账
  （生产请删掉它、只留带签名校验的 `/api/vip/pay_callback`）。
- 33娘：有 `AI_API_KEY` 就走大模型，没有则走内置规则库（风控文案、投稿规则、投币凭证、会员额度），
  两种方式输出的 JSON 方案结构一致，前端「一键创建」都能用。
- 私信图片、Web 端 B 站播放器、评论区挖掘等重功能留了接口位但未实现完整 UI，见 `docs/ARCHITECTURE.md`。
- 前站点的合规审查结论（外部引用面、证书、安全观察）见 `docs/RECON.md`。

## 免责

仅供学习研究与自有账号运营。使用本工具发起的任何请求都由你自行承担平台规则与法律后果；
Cookie 属于高敏凭证，请勿把它交给任何第三方服务。

`app/static/img/` 中的品牌素材版权归哔哩哔哩所有，本项目仅作界面还原研究引用，**不在 MIT 许可范围内**。
