# 开源前侦察报告（pre-open-source recon）

对象：`bili.mssss.cn`。范围只限本项目归档的 12 个文本文件与目标站点自发观测到的事实，
不对第三方系统做任何主动探测。产出：`docs/recon_hosts.json`（引用面）、`docs/recon_facts.json`（事实集）。

## 1. 外部引用面（整站只有 1 个真实外部域名）

`tools/recon.py` 扫完归档全部 HTML / CSS / JS / JSON / SVG：

| HOST | 类别 | 风险 | 出现位置 | 说明 |
|---|---|---|---|---|
| `126.com` | mail | info | index.html ×2 | 只在 `mailto:` 与文案里，不发起网络请求 |
| `www.w3.org` | namespace | info | bili_logo.svg | SVG/XML 命名空间字符串，不是请求 |
| `mssss.cn` | self | info | Host 头 | 目标自身域 |
| `example.com` | third-party | low | login.html | 前端占位符 `you@example.com`（输入框 placeholder），非依赖 |

**结论 SC-01（info）**：页面没有任何外部 `<script src>`、`@import`、外链字体或 CDN 引用，
6 个静态资源全部同源自托管。这一点对开源有利——把 4 张图 + 2 个 CSS 一起托管即可，
不存在需要替换的第三方 SDK。

## 2. DNS / 证书事实

| 项 | 观测值 |
|---|---|
| `bili.mssss.cn` A | `1.12.223.182` |
| `mssss.cn` A | `36.158.231.214` |
| NS | `axis.dnspod.net` / `sylvia.dnspod.net`（DNSPod） |
| 子域证书 | Let's Encrypt `YR2`，CN=`bili.mssss.cn`，SAN 仅单域名，`2026-10-15 → 2027-01-13` |
| apex 证书 | **校验失败**：`self-signed certificate in certificate chain` |

**结论 SC-02（high）**：`mssss.cn` 返回自签证书，和 `bili.mssss.cn` 的 Let's Encrypt 证书链不同源。
说明站点是"子域单独签发、apex 随手挂了个自签"，属于上线流程不统一。开源版本应写清证书路径统一方案。

**结论 SC-03（medium）**：真实客服邮箱 `bilibiliup1@126.com` 硬编码在 `index.html` 的 FAQ 与页脚，
属个人可识别信息。开源副本已替换为占位符，`_archive/` 里的原始快照仍保留原文（仅作还原依据）。

## 3. 代码层面的安全观察

| ID | 级别 | 问题 | 证据 |
|---|---|---|---|
| SC-04 | medium | 明文密码可逆存到 localStorage | `login.html`：`localStorage.setItem(RK_P, btoa(p))`，`b64d` 一行还原 |
| SC-05 | medium | 管理后台仅靠前端判角色 | `login.html`：`location.href = (r.role==='admin') ? '/admin234' : '/dashboard'` |
| SC-06 | low | Cookie 存放方式与文案不符 | 首页称"Cookie 加密存储"，前端无任何本地加密逻辑，说明在服务端；开源实现按 Fernet 落地 |
| SC-07 | info | 计算验证码强度低 | `GET /api/auth/captcha` 返回 `{"q":"9 - 6 = ?"}`，单题答案空间 < 40 |

对应处置（本仓库已实现）：

- SC-04 → 开源副本保留"记住密码"体验，但底层仍用 `btoa`（与原站一致），**README 已标注不推荐**；
  更稳妥的做法是改成只记用户名 + HttpOnly 会话续期。
- SC-05 → `app/auth.py::require_admin` 服务端强制校验，前端隐藏只是体验。`tests/smoke.py` 里有
  「普通用户调管理 API 被拒 403 / 进后台页 307」两条断言。
- SC-06 → `app/security.py::encrypt_cookie` 用 `Fernet(SHA256(SECRET_KEY))`，接口只回 `keys=... len=...`。
- SC-07 → 保留算式验证码（与原站交互一致），但加了 `CAPTCHA_TTL=300`、一次性 token、输错即作废、
  邮箱码 60 秒重发冷却、同 IP 900 秒内 8 次失败锁定。

## 4. 开源前的合规动作清单

- [x] 外部依赖清点：0 个第三方脚本/CDN，无需替换（见 §1）
- [x] 个人可识别信息：客服邮箱已换占位符；`_archive/` 保留原文并注明用途（还原依据）
- [x] 密钥：`.env` 已被 `.gitignore` 屏蔽，仓库内无任何 token / 密码 / Cookie
- [x] 前端素材来源：4 张图 + B 站 logo 的著作权归哔哩哔哩，**不得作为自有资产发布**
      → 已采纳「保留但声明」方案：README 顶部版权声明、LICENSE 末尾 EXCLUDED ASSETS、
      `ASSETS.md` 单独说明 + 替换指引 + 移除请求通道
- [ ] LICENSE 归属（取决于个人仓库还是组织仓库，待定）
- [ ] 支付通道：`/api/vip/pay_mock` 必须在发布版删除或强制 `DEBUG` 才启用

## 5. 素材处置：已采纳「保留但声明」

`app/static/img/` 里的 `bili_logo.svg`、`mascot_2233_door.png`、`m22_idea.webp`、`nodata.png`
是哔哩哔哩的商标/官方形象素材。已按方案 A 处理，三处声明互相印证：

1. `README.md` 顶部醒目版权声明，并链接到「素材替换」章节（含 4 个文件的尺寸与替换步骤）；
2. `LICENSE` 末尾 `EXCLUDED ASSETS` 段落，明确 MIT **不覆盖**这 4 个文件；
3. `ASSETS.md` 单独文档：版权归属、收录目的、使用者义务、替换方法、权利人移除请求通道。

对应地，`_archive/` 里的同名副本也在排除范围内（作为取证留档）。

> 注意：方案 A 不代表零风险——它只是把权利状态说清楚。真要对外商用或大规模分发，
> 仍应执行「素材替换」后再发布。
