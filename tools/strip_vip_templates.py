import re
from pathlib import Path

ROOT = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper")
TPL = ROOT / "app" / "templates"

# ---------- 1) 删掉整两个 VIP 页面模板 ----------
for name in ("vip.html", "vip_compare.html", "invite.html"):
    f = TPL / name
    if f.exists():
        f.unlink()
        print("删除模板:", name)

# ---------- 2) base.html：删掉 vipchip 那段轮询 ----------
b = TPL / "base.html"
t = b.read_text(encoding="utf-8")
# 从 `var vc=document.getElementById('vipchip');` 到它的 catch 结束
t = re.sub(
    r"var vc=document\.getElementById\('vipchip'\);.*?\}\)\.catch\(function\(\)\{\}\);\}\n",
    "", t, flags=re.S)
b.write_text(t, encoding="utf-8")
print("base.html: 删掉 vipchip 轮询")

# ---------- 3) 各页面删掉指向 /vip 、/invite 的入口 ----------
TARGETS = ["dashboard.html", "me.html", "settings.html", "messages.html",
           "publish.html", "tasks.html", "library.html", "index.html"]
for name in TARGETS:
    f = TPL / name
    if not f.exists():
        continue
    src = f.read_text(encoding="utf-8")
    orig = src
    # 删掉 <a ... href="/vip..." ...>...</a> 与 /invite
    src = re.sub(r'<a\b[^>]*href="/(vip|invite)[^"]*"[^>]*>.*?</a>', "", src, flags=re.S)
    # 删掉含 /vip 或 /invite 的整个「快捷入口」按钮
    src = re.sub(r'<a class="btn[^"]*"[^>]*href="/(vip|invite)[^"]*"[^>]*>[^<]*</a>', "",
                 src, flags=re.S)
    if src != orig:
        f.write_text(src, encoding="utf-8")
        print("清理入口:", name)

print("\n完成")
