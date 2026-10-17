import re
from pathlib import Path

ROOT = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper")

# ---------------- main.py：摘掉 vip router ----------------
m = ROOT / "app" / "main.py"
t = m.read_text(encoding="utf-8")
t = t.replace("vip_api, vip_compare, web_api)", "web_api)")
t = t.replace("vip_compare.router, ", "")
t = t.replace("vip_api.router, ", "")
m.write_text(t, encoding="utf-8")
print("main.py: 摘掉 vip 路由")

# ---------------- pages.py：删掉 /vip 和 /vip/compare 页面 ----------------
pg = ROOT / "app" / "routers" / "pages.py"
t = pg.read_text(encoding="utf-8")

def drop_route(src: str, path: str) -> str:
    """删掉一个 @router.get("path") 修饰的整个函数。"""
    m = re.search(r'@router\.get\("%s"\)\s*\n' % re.escape(path), src)
    if not m:
        return src
    start = m.start()
    # 找到下一个 @router 或文件末尾
    nxt = src.find('@router.', m.end())
    end = nxt if nxt > 0 else len(src)
    return src[:start] + src[end:]

for path in ("/vip", "/vip/compare"):
    before = len(t)
    t = drop_route(t, path)
    print(("删除页面 %s" % path) if len(t) != before else ("未找到 %s" % path))

pg.write_text(t, encoding="utf-8")

# ---------------- base.html：去掉会员徽章与流量 chip ----------------
b = ROOT / "app" / "templates" / "base.html"
t = b.read_text(encoding="utf-8")
# 删掉 vipchip 整块
t = re.sub(r'<[^>]*id="vipchip"[^>]*>\s*</[^>]+>', "", t)
t = re.sub(r'<a[^>]*href="/vip"[^>]*>.*?</a>', "", t, flags=re.S)
t = re.sub(r'<a[^>]*href="/invite"[^>]*>.*?</a>', "", t, flags=re.S)
b.write_text(t, encoding="utf-8")
print("base.html: 去掉会员/邀请入口")

print("\n完成")
