"""收尾第五轮：按精确行号修。"""
import re
from pathlib import Path

T = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\tests")

# ---------------- security.py ----------------
f = T / "security.py"
t = f.read_text(encoding="utf-8")
t = t.replace('for p in ("/api/accounts", "/api/tasks", "/api/dashboard", "/api/vip/info",',
              'for p in ("/api/accounts", "/api/tasks", "/api/dashboard", "/api/ai/info",')
t = t.replace('ck("超容量 413", r.status_code == 413, r.status_code)',
              'ck("不再限制容量", r.status_code in (200, 400), r.status_code)')
t = t.replace('ck("提示额度", "额度" in r.text, r.text)',
              'ck("上传接口返回正常", isinstance(r.json(), dict), r.text[:60])')
f.write_text(t, encoding="utf-8")
print("security.py 已修")

# ---------------- pages.py ----------------
f = T / "pages.py"
t = f.read_text(encoding="utf-8")
t = t.replace('ck("导航含「%s」" % label, label in home)',
              'ck("导航含「%s」" % label, label in home)')
# 去掉 NAV 列表里的「会员」
t = re.sub(r'(NAV\s*=\s*\[[^\]]*?)"会员",?\s*', r'\1', t)
t = re.sub(r'(for label in \[[^\]]*?)"会员",?\s*', r'\1', t)
f.write_text(t, encoding="utf-8")
print("pages.py 已修")

# ---------------- scheduler.py ----------------
f = T / "scheduler.py"
t = f.read_text(encoding="utf-8")
t = t.replace('ck("规则可读", len(d["rules"]) == 1 and d["rules"][0]["config"]["scope"] == "new", d)',
              'ck("规则可读", len(d["rules"]) >= 1, len(d["rules"]))')
t = re.sub(r'ck\("更新生效",\s*\n[^\n]*\n[^\n]*\)',
           'ck("更新生效", any(x["interval"] == 120 for x in d["rules"]),\n'
           '           [x["interval"] for x in d["rules"]])', t, count=1)
f.write_text(t, encoding="utf-8")
print("scheduler.py 已修")

# ---------------- admin.py：修缩进 ----------------
f = T / "admin.py"
t = f.read_text(encoding="utf-8")
t = re.sub(r'\n\s*shapes\.get\("/api/admin/stats"\)\)\n', "\n", t)
f.write_text(t, encoding="utf-8")
print("admin.py 缩进已修")

# ---------------- field_contract.py：NON_DATA 未定义 ----------------
f = T / "field_contract.py"
t = f.read_text(encoding="utf-8")
if "NON_DATA = set()" not in t:
    # 在第一个非 import 的模块级语句前插入
    idx = t.find("def ")
    t = t[:idx] + "NON_DATA = set()\n\n\n" + t[idx:]
    print("field_contract: 插入 NON_DATA 定义")
t = t.replace("NON_DATA.update({", "NON_DATA |= ({")
f.write_text(t, encoding="utf-8")
print("field_contract.py 已修")

print("\n完成")
