"""收尾第二轮：删管理后台的订单/兑换码 UI，修 dashboard 与 accounts_add 残留。"""
import re
from pathlib import Path

ROOT = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper")
TPL = ROOT / "app" / "templates"

# ---------- 1) admin/overview.html：删掉订单页签、兑换码页签及相关面板 ----------
f = TPL / "admin" / "overview.html"
t = f.read_text(encoding="utf-8")

# 页签按钮
t = re.sub(r'\s*<button id="t-orders"[^>]*>订单</button>', "", t)
t = re.sub(r'\s*<button id="t-redeem"[^>]*>兑换码</button>', "", t)

# 面板整块
def drop_pane(src, pane_id):
    m = re.search(r'<div id="pane-%s"[^>]*>' % pane_id, src)
    if not m:
        return src
    start = m.start()
    i = src.index(">", start) + 1
    depth = 1
    p = i
    while p < len(src) and depth:
        if src.startswith("<div", p):
            depth += 1
            p += 4
        elif src.startswith("</div", p):
            depth -= 1
            p += 6
        else:
            p += 1
    return src[:start] + src[p:]

for pane in ("orders", "redeem"):
    t = drop_pane(t, pane)

# tab() 函数里的键列表
t = t.replace("['users','orders','accounts','logs','notices','redeem']",
              "['users','accounts','logs','notices']")
t = t.replace("{users:loadUsers,orders:loadOrders,accounts:loadAccounts,logs:loadLogs,\n    notices:loadNotices,redeem:loadRedeem}[v]",
              "{users:loadUsers,accounts:loadAccounts,logs:loadLogs,notices:loadNotices}[v]")

# 删掉 loadOrders / loadRedeem 函数
for fn in ("loadOrders", "loadRedeem"):
    t = re.sub(r'\s*async function %s\(\)\{.*?\n\}\n' % fn, "\n", t, flags=re.S)
    t = re.sub(r'\s*async function %s\(\)\{[^}]*?(?:\{[^}]*\}[^}]*?)*\}\n' % fn, "\n", t, flags=re.S)

# 删掉 stats 里的会员/收入卡片
t = re.sub(r'\s*<div class="stat"><div class="k">会员数</div><div class="v" id="s-vip">–</div></div>', "", t)
t = re.sub(r'\s*<div class="stat"><div class="k">收入（已支付）</div><div class="v" id="s-rev">–</div></div>', "", t)
t = re.sub(r"\s*document\.getElementById\('s-vip'\)\.textContent=d\.vip;", "", t)
t = re.sub(r"\s*document\.getElementById\('s-rev'\)\.innerHTML='¥'\+d\.revenue;", "", t)

# 用户表：删掉会员列与相关按钮
t = re.sub(r'<th>会员</th>', '', t)
t = re.sub(r"\+'<td>'\+\(u\.vip\?.*?</td>'", "", t, flags=re.S)
t = re.sub(r"\+'<button class=\"btn ghost sm\" data-vip=\"'\+u\.id\+'\">\+30天会员</button> '", "", t)
t = re.sub(r"\+'<button class=\"btn ghost sm\" data-tr=\"'\+u\.id\+'\">\+1GB流量</button>", "", t)
t = re.sub(r"tb\.querySelectorAll\('\[data-vip\]'\)\.forEach\(function\(b\)\{.*?\}\);\n", "", t, flags=re.S)
t = re.sub(r"tb\.querySelectorAll\('\[data-tr\]'\)\.forEach\(function\(b\)\{.*?\}\);\n", "", t, flags=re.S)

# 删掉兑换码生成按钮的绑定
t = re.sub(r"\s*document\.getElementById\('rd-gen'\)\.onclick=async function\(\)\{.*?\n\};\n", "\n", t, flags=re.S)

f.write_text(t, encoding="utf-8")
print("admin/overview.html 已清理")

# ---------- 2) dashboard.html：检查残留语法 ----------
f = TPL / "dashboard.html"
t = f.read_text(encoding="utf-8")
# 之前误删可能留下孤立括号，做保守修补：删掉空的 (async function(){ })();
t = re.sub(r"\(async function\(\)\{\s*\}\)\(\);", "", t)
t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
f.write_text(t, encoding="utf-8")
print("dashboard.html 已修补")

print("\n完成")
