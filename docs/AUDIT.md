# 审计与修复记录

这份文档记录**还原过程中实际发现并修复的每个缺陷**，以及为什么会漏掉它们。
不是"最佳实践"清单，是踩过的坑。

写它的原因：这个项目有过「948 项测试全绿，但会员购买完全不可用」的阶段。
下面每一条都是那种状态下真实存在的问题。

---

## 一、缺陷清单（按发现顺序）

### 1. `db()` 事务不可重入 → 死锁

**症状**
```
sqlite3.OperationalError: database is locked
```

**成因**：`admin_patch_user` 在 `with db() as conn:` 块里调 `admin_audit()`，
后者又 `with db()` 开一次事务抢同一把写锁。

**影响**：任何"在事务里写日志"的路径都会死锁，不只管理后台。

**修法**：`db()` 改成线程局部可重入 —— 同线程已有事务时复用外层连接。

```python
st = _local
if getattr(st, "depth", 0):
    st.depth += 1
    try:
        yield st.conn
    finally:
        st.depth -= 1
    return
```

---

### 2. `charge_traffic` 扣错字段 → 流量账目对不上

**症状**：扣费后"已用/总量"两个数永远不自洽。

**成因**：减的是 `traffic_mb`（总包），但 `quota` 是按
`traffic_mb - traffic_used_mb` 算的；会员升级还要给 `traffic_mb` 加值。

**修法**：改为累加 `traffic_used_mb`，并写 `traffic_logs` 流水。

---

### 3. `CurrentUser` 没有 `plan_tier` → 季卡/年卡按 30 秒间隔执行

**症状**：改成季卡后，`/api/vip/compare` 返回 `tier: "month"`、`op_interval: 30`，
应为 15 秒。

**成因**：`users.plan_tier` 列存在，但 `auth.py` 载入用户时没带进 `CurrentUser`，
`_tier_of()` 永远取不到值，一律兜底成 month。

**影响**：季卡和年卡用户的 AI 操作间隔实际都按月卡的 30 秒跑 —— 花钱没得到应有速度。

**修法**：`CurrentUser` 加 `plan_tier` 字段并在构造时带上。

---

### 4. `worker` 查了不存在的表 → 定时任务从未自动执行

**症状**：任务能建、能列表、显示下次执行时间，但**到点什么都不发生**。

**成因**
```python
# app/worker.py（修复前）
rows = conn.execute("SELECT id FROM tasks WHERE enabled=1 AND next_run_at>0 AND next_run_at<=?")
                                                          # ↑ tasks 表、next_run_at 列
```
真实任务在 `interact_tasks` 表，列名是 `next_run`。HTTP 层建任务写
`interact_tasks`，调度器读 `tasks` —— 两条路各走各的。

**为什么没测出来**：`tests/scheduler.py` 测 HTTP 层的增删改查，
`tests/task_config.py` 测手动触发，都绕过了 `worker.tick()`。

**修法**：worker 改读 `interact_tasks`，按 `kind` 分派（interact/upload），
每次执行写 `task_runs` 留痕。

---

### 5. `bootstrap` 把"到点待执行"当成"过期"推走 → 任务永远排不进去

**症状**：worker 起来了、日志也打印了，但任务一次都不执行。

**成因**：`bootstrap_next_runs()` 在每轮 tick **执行之前**跑，它把
`next_run < now` 的任务推进到"下一次未来时间"——而这正是刚要到点该执行的那批。
于是 `tick()` 永远捞不到它们。

**修法**：bootstrap 只处理 `next_run = 0`（从未算过）的任务；
过期任务交给 `tick()` 执行完再推进。

---

### 6. 提前返回的分支不推进 `next_run` → 每分钟无限重试

**症状**：配置写坏的定时任务，`task_runs` 每分钟多一条，永远"正在失败"。

**成因**：`_run_one()` 有两条提前 `return`（配置不是合法 JSON / 归属用户被删），
都没更新 `next_run`。收尾代码只在正常路径上。

**修法**：抽 `_advance(row, started)`，**所有**退出路径都调它。

---

### 7. `next_run` 会算到过去的时刻

**成因**：`_next_run(cron, ended)` 的基准是"执行结束时刻"，但任务是从
**过去某个 `next_run`** 触发的 —— 执行够快时 `ended` 仍早于现在，
算出来的下一次落在过去，下一轮立刻又捞到。

实测：
```
ended=16:20:17 -> next=16:21:00   过去? False（此时 16:21:47）
```

**修法**：基准改成 `now`，并加"若仍落在过去就再算一次"的兜底。

---

### 8. 单个坏 UID 拖垮整批目标

**症状**：10 个目标里 1 个 UID 写错，整个任务 502，另外 9 个也跑不了。

**成因**：`resolve_targets` 里每个目标拉取时没有 try，
`BiliError` 冒泡到调用方转 502。

**修法**：目标级容错 —— 坏目标在结果里标 `error` 并 `continue`。
注意只 catch `BiliError`，`RuntimeError` 之类仍透传（不掩盖程序 bug）。

---

### 9. `latest` 依赖上游排序 → 可能拿到最旧的视频

**成因**：`_fetch_up_videos()` 信任 B站 `order=pubdate` 的返回顺序，
自己从不排序。一旦上游改了或降级，`pool[:max]` 就是错的。

**影响**：用户以为给新视频点赞，实际点了几年前的。

**修法**：拉取后显式 `out.sort(key=lambda v: v.get("ts") or 0, reverse=True)`。

---

### 10. 执行线程抛异常 → "正在执行"永远不消失

**症状**：进度面板卡在"正在执行"，点停止无效；之后所有任务被
`409 已有任务在运行中` 挡住。**除非重启进程，互赞功能瘫痪。**

**成因**：`_run_worker` 的收尾代码（`st.running = False`）写在 for 循环之后，
循环体一抛未捕获异常就永远执行不到。

复现：
```
初始 running: False
AttributeError: 'NoneType' object has no attribute 'get'
→ running 一直是 True
```

**修法**：循环体抽成 `_run_loop()`，外层 `try/except/finally`，
`finally` 里一定重置状态；坏数据直接跳过而不是抛。

---

### 11. 凭据失效仍跑完 8 个动作 → 白白触发风控

**症状**：账号 `-101`（未登录）时对同一个视频依次尝试
点赞/投币/收藏/评论/分享/关注/播放/图文，**8 次全部失败**。

实测：
```
凭据失效时：ok=0 fail=8
```

**影响**：50 个视频 × 8 动作 = 400 次无效请求，还加重风控。

**修法**：定义 `FATAL_CODES = {-101, -111, 61001, -352, -412}`，
命中即停止该视频剩余动作并回报 `FATAL:`；`_run_loop` 收到后写回账号状态
（expired/captcha）并中止整个任务。

修复后 `fail` 从 8 降到 1。

---

### 12. 会员购买全链路不可用（三个 bug 叠加）

这一组是**同一轮**发现的，都是"前端发 A、后端读 B"。

**12.1 下单永远 400**
```js
BP.api('/api/vip/order', {body: JSON.stringify({kind, key})})    // 前端发 key
```
```python
class OrderIn(BaseModel):
    id: str = ""        # 后端读 id
```
→ `400 {"detail":"商品不存在"}`

**12.2 支付 500**
建单时写库用的是 `body.id`（空串），`_grant()` 里
`next((p for p in PLANS if p["id"] == o["item_id"]))` 得到 `None` →
`plan["days"]` → `TypeError`。

**12.3 支付链接 undefined**
前端读 `r.pay_url`，后端只返回 `url`。

**修法**：`OrderIn` 同时接 `id`/`key`；落库写 `item["id"]`；
响应同时给 `pay_url` 和 `url`；`_grant` 找不到商品时按名字回查并给可读错误。

**附带**：订单号精确到秒，同秒两单撞 `UNIQUE` 约束 → 加 4 位随机后缀。

---

### 13. 账号页昵称恒为空

```js
// accounts.html（修复前）
function accName(a){return (a.alias_name||a.name||'');}   // 读 a.name
```
`/api/accounts` 返回的字段是 `uname`，没有 `name`。**只要没设别名，用户名那一格就是空白。**

---

### 14. `/api/me/settings` 一直 500

```
sqlite3.OperationalError: no such column: proxy
```

查了 `users.proxy` 和 `users.notify` 两列，两列都不存在。
代理实际是 `proxy_pool` 表 + `bili_accounts.proxy` 列。

**为什么没测出来**：没有任何测试调用过这个接口。我给它写的前端保存按钮调的是
`POST`，`GET` 从未被测、也从未被真打开。

---

### 15. 31 个类名在线上 CSS 里不存在 → 页面裸奔

`/web`、`/vip/compare`、`/trend`、`/invite` 用了大量 `wb-*` `cmp-*` `tr-*` `iv-*`
类名，这些是我还原时自己起的，线上 `app.css` 里根本没有。

后果：HTML 结构对、JS 逻辑对，但渲染出来是无样式裸元素。

**修法**：新建 `custom.css` 放这些样式，不污染 113KB 的原站样式表
（方便日后整体替换）。

---

### 16. 公共工具被复制 19 次

`esc` 3 个版本、`faceUrl` 2 个版本、`accName`/`accFace`/`accMid`/`fmtN`/`maskMid`
散落在 9 个页面里。第 13 条那个 `a.name` 错误正是这么来的 —— 复制的其中一份写错了。

**修法**：实现只留 `common.js` 一份，页面直接用（导出到 `window`）。

---

### 17. `/web` 页 15 个接口完全不存在

页面能渲染、按钮能点，但那 15 个 `/api/web/*` 后端一个都没有。
`tests/pages.py` 当时"通过"——它只验证 HTML 里有几个关键词。

**修法**：补齐 15 个接口；并写 `contract_static.py` 从根上防住这类问题。

---

### 18. `tests/smoke.py` 长期红着没人管

它对着五轮前删掉的 `/api/dashboard/stats`、`{"items":[...]}`、
`POST /toggle` 写断言。因为**不在任何套件清单里**，一直没人发现。

**修法**：重写成端到端主干流程（53 项）；并写 `run_all.py`
自动扫目录发现套件，不再依赖手工清单。

---

## 二、为什么这些缺陷测不出来

### 根因一：手工维护的套件清单会漏

`smoke.py` 坏了很久没人知道，因为跑测试时用的是自己记的清单。
`run_all.py` 改成 `os.listdir` 扫目录后立刻暴露。

### 根因二：只验证"能渲染"/"能返回"，不验证"对"

| 当时通过的理由 | 实际状态 |
|---|---|
| `pages.py`：HTML 里有「我的账号」四个字 | 账号页昵称一列全空 |
| `pages.py`：`/web` 返回 200 | 15 个接口不存在 |
| 没有任何测试 | `/api/me/settings` 一直 500 |
| 没有任何测试 | 会员页买不了任何东西 |

### 根因三：异常路径没人走

第 8、10、11、12 都是"主路径正确，异常路径靠运气"。

---

## 三、8 个静态交叉检查

它们存在的唯一理由，就是覆盖上面那些盲区。

| 检查器 | 防的是什么 | 规模 | 抓出过的真实缺陷 |
|---|---|---|---|
| `contract_static.py` | 页面调用的接口后端不存在 | 172 调用 | `/web` 的 15 个接口 |
| `schema_check.py` | SQL 查了表里不存在的列 | 125 列 | `users.proxy` 的 500 |
| `endpoint_coverage.py` | 接口注册了但没测试打过 | 147 路由 | 32 个零覆盖接口 |
| `style_coverage.py` | 模板用了没有 CSS 的 class | 765 类 | 31 个裸奔类名 |
| `inline_js.py` | 内联脚本语法错误 | 23 块 | —（预防） |
| `shared_utils.py` | 公共工具重复实现 | 20 导出 | 19 处复制 |
| `field_contract.py` | 前端读的字段后端不返回 | 408 读取 | 账号页昵称恒空 |
| `payload_contract.py` | 前端发的字段后端不收 | 50 写调用 | 会员下单 400 |

### 写检查器时的教训

**检查器报大量问题时，先怀疑检查器。**

- `field_contract` 第一版报 147 处误报 → 只做动态调用，没覆盖鉴权接口
- 第二版报 50 处 → 白名单丢了，把 `innerHTML`/`slice` 当字段
- `payload_contract` 报 6 处 `['body']` → 括号配平起点错了
- 注入 bug 后仍报"全部一致" → 没识别 ES6 简写属性 `{kind, key}`

每一次都是解析器不够聪明，不是代码有问题。**唯一的判定方法是负向验证**：
把已知的 bug 重新注入，看检查器能不能抓到。

```bash
# 摘掉 OrderIn.key，payload_contract 必须报错
# 恢复后必须通过
```

---

## 四、还原依据的诚实分类

| 类别 | 数量 | 说明 |
|---|---|---|
| 实测 HTML + 内联 JS 重建 | 23 个页面 | 真实账号登录抓取，DOM 与交互按原文 |
| 接口反推 | 1 个（`/admin234`） | 页面 307 抓不到；10 个 `/api/admin/*` 用 403/405 探明确认 |

`/admin234` 是唯一一处"接口真、页面推断"。原站 CSS 里查不到任何
`adm-*` 专用类，说明它复用现有组件（`.card`/`.tbl-scroll`/`.stat`），
推断有依据但不是实测。

### 一条被证伪的"需求"

`STATUS.md` 里曾写"内部站内信未做"。后来对 21 个归档页面全文搜索
`站内信`/`内部消息`/`admin_msg` —— **零命中**，5 个可疑文件全是 22 字节的
`{"detail":"Not Found"}` 占位页。这是我早先的臆测，已删除。

---

## 五、当前状态

```
33 个套件 / 948 项检查 / 0 失败
24 个页面路由 / 147 个 API
真实前端引用的 92 个端点、19 个站内链接：全覆盖
```

运行：

```bash
python -m pip install -r requirements.txt
python -m app.cli create-admin admin you@example.com
python tests/run_all.py           # 948 项
python run_local.py               # http://127.0.0.1:8000
```

## 六、已知限制

| 项 | 说明 |
|---|---|
| `/admin234` 布局 | 接口 1:1，DOM 推断 |
| 上游真实链路 | 无 B 站账号时所有依赖上游的功能返回明确错误，不是假数据 |
| 风控/限流/验证码 | 只能靠真实账号触发，测试里用假客户端验证调用序列 |
| 支付 | 只有 `pay_mock`；生产需接自己的回调 |
| 定时投稿 | UPOS 全链路已实现（preupload → 分片 → add/v3），但未经真实投稿验证 |
