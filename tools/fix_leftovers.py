"""收尾：删掉 pages.py 里指向已删模板的路由 + 修 scheduler.py 语法。"""
import re
from pathlib import Path

ROOT = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper")

# ---------- pages.py：/invite 还在引用已删模板 ----------
p = ROOT / "app" / "routers" / "pages.py"
t = p.read_text(encoding="utf-8")


def drop_route(src, path):
    m = re.search(r'@router\.get\("%s"\)\s*\n' % re.escape(path), src)
    if not m:
        return src, False
    start = m.start()
    nxt = src.find("@router.", m.end())
    end = nxt if nxt > 0 else len(src)
    return src[:start] + src[end:], True


for path in ("/invite", "/vip", "/vip/compare"):
    t, ok = drop_route(t, path)
    print(("删除路由 %s" % path) if ok else ("%s 已不存在" % path))

p.write_text(t, encoding="utf-8")

# ---------- scheduler.py：粘连行 ----------
f = ROOT / "tests" / "scheduler.py"
t = f.read_text(encoding="utf-8")
t = re.sub(r'(ck\("policy 含 is_vip / caps", "is_vip" in d and "caps" in d, list\(d\.keys\(\)\)\))\s+ck\("不再按档位限制间隔"',
           r'\1\n        ck("不再按档位限制间隔"', t)
f.write_text(t, encoding="utf-8")
print("scheduler.py 粘连行已修")

print("\n完成")
