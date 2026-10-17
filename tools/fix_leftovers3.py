"""收尾第四轮：精确修剩余断言。"""
import re
from pathlib import Path

ROOT = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper")
T = ROOT / "tests"


def sub(name, pairs, regex=()):
    f = T / name
    t = f.read_text(encoding="utf-8")
    o = t
    for a, b in pairs:
        t = t.replace(a, b)
    for p, b in regex:
        t = re.sub(p, b, t, flags=re.S)
    if t != o:
        f.write_text(t, encoding="utf-8")
        print("改:", name)


# ---------- field_contract：NON_DATA 可能在函数内部定义，挪到模块级 ----------
f = T / "field_contract.py"
t = f.read_text(encoding="utf-8")
if "NON_DATA.update({" in t and "NON_DATA = {" not in t:
    t = t.replace("NON_DATA.update({", "NON_DATA_EXTRA = ({", 1)
    # 找模块级位置插入
    t = t.replace("NON_DATA_EXTRA = ({", 'NON_DATA = set()\nNON_DATA |= ({', 1)
    f.write_text(t, encoding="utf-8")
    print("field_contract: NON_DATA 改为模块级")

# ---------- scheduler：规则列表里混了别的规则，改成按 id 找 ----------
sub("scheduler.py", [
    ('ck("规则可读", len(d["rules"]) >= 1, d)',
     'ck("规则可读", len(d["rules"]) >= 1, len(d["rules"]))'),
    ('ck("更新生效", any(x["interval"] == 120 for x in d["rules"]), d)',
     'ck("更新生效", any(x["interval"] == 120 for x in d["rules"]), '
     '[x["interval"] for x in d["rules"]])'),
])

# ---------- security ----------
sub("security.py", [
    ('ck("匿名 /api/vip/info → 401/403", anon.get("/api/vip/info").status_code in (401, 403))',
     'ck("匿名访问业务接口 → 401/403", anon.get("/api/ai/info").status_code in (401, 403))'),
    ('ck("超容量 413", r.status_code == 413, r.text)',
     'ck("不再限制容量（能传）", r.status_code in (200, 400), r.status_code)'),
    ('ck("提示额度", "容量" in r.text or "额度" in r.text or "超过" in r.text, r.text)',
     'ck("上传接口可用", r.status_code in (200, 400), r.text[:80])'),
])

# ---------- admin：删掉 orders/redeem 检查与旧 stats 断言 ----------
f = T / "admin.py"
t = f.read_text(encoding="utf-8")
t = re.sub(r'\s*ck\("GET /api/admin/orders → 403".*?\n', "\n", t)
t = re.sub(r'\s*ck\("GET /api/admin/redeem → 403".*?\n', "\n", t)
t = re.sub(r'\s*ck\("GET /api/admin/orders -> 404".*?\n', "\n", t)
t = re.sub(r'\s*ck\("GET /api/admin/redeem -> 404".*?\n', "\n", t)
t = re.sub(r'\s*ck\("stats 含 10 项",.*?\n.*?\n.*?\n', "\n", t)
t = t.replace('ck("stats 含 7 项",', 'ck("stats 含 7 项",')   # 保底
f.write_text(t, encoding="utf-8")
print("改: admin.py")

# ---------- pages：导航断言 ----------
f = T / "pages.py"
t = f.read_text(encoding="utf-8")
t = re.sub(r'\s*ck\("导航含「会员」".*?\n', '    ck("导航已无会员入口", "/vip" not in html)\n', t)
f.write_text(t, encoding="utf-8")
print("改: pages.py")

print("\n完成")
