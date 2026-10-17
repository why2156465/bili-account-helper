from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\README.md")
t = p.read_text(encoding="utf-8")

# 1) 还原规模说明
t = t.replace(
"""**逐页还原状态见 [docs/STATUS.md](docs/STATUS.md)**：22 个路由，21 个按实测 HTML 重建，1 个（管理后台）是接口反推。""",
"""**逐页还原状态见 [docs/STATUS.md](docs/STATUS.md)**：24 个页面路由、147 个 API，
23 个页面按实测 HTML + 内联 JS 重建，1 个（管理后台）是接口反推（页面 307 抓不到，
但 10 个 `/api/admin/*` 用 403/405 探明确认了路径与方法）。
真实前端引用的 92 个端点、19 个站内链接，现在全部有对应实现。""")

# 2) 测试说明
t = t.replace(
"""跑一遍冒烟测试（临时库，不碰真实数据，不访问 B 站）：

```bash
python tests/smoke.py     # 59 项检查，全部 PASS 才算好
```
""",
"""跑测试（全部用临时库，不碰真实数据，不访问 B 站）：

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
""")

# 3) 目录结构
t = t.replace(
"""  tasks.py           任务执行器：互赞 / 投稿 / 私信 / 监控 / AI
  worker.py          调度进程，扫到点任务""",
"""  tasks.py           上游客户端封装与单动作执行
  worker.py          调度进程：扫 interact_tasks，按 kind 分派到互赞/投稿执行器""")

t = t.replace(
"""  routers/           pages, auth_api, accounts, like, tasks_api, ai, dashboard, library, misc(含 admin)
  templates/         index / login / register / base / dashboard / vip / settings / library / admin
  static/            css（沿用原站）、js/common.js、img（原站素材）
_archive/            原站抓取快照（HTML/CSS/图片），用于对照还原
docs/                还原报告、API 契约、架构说明
tests/smoke.py       端到端冒烟测试""",
"""  routers/           22 个模块：pages、auth_api、accounts、interact、scheduler_api、
                     publish、publish_upload、messages、ai_api、web_api、trend_api、
                     vip_api、vip_compare、dashboard_api、logs_api、templates_api、
                     me_api、proxy、admin_api、fills、library、misc
  templates/         24 个页面模板（含 admin/ 子目录）
  static/            css/{app,client}.css 沿用原站；css/custom.css 是本项目补的组件样式；
                     js/common.js 公共工具；img/ 原站素材（见上方版权声明）
_archive/            原站抓取快照（HTML/CSS/图片），用于对照还原
docs/                STATUS.md（逐页还原状态）、API 契约、架构说明、侦查记录
tests/               33 个套件：23 个功能测试 + 8 个静态交叉检查 + run_all 汇总""")

p.write_text(t, encoding="utf-8")
print("README 已更新")
