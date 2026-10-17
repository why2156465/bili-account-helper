"""把「33娘」相关文案改为「AI 助手」。

只改用户可见文案和默认配置，不动内部标识符（nav 值仍是 ai、路由仍是 /ai），
避免破坏既有链接与测试。
"""
import re
from pathlib import Path

ROOT = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper")

# 精确替换表：按出现语境改成自然中文，不是无脑横扫
PAIRS = [
    # ---- 标题与导航 ----
    ('{% block title %}33娘{% endblock %}', '{% block title %}AI 助手{% endblock %}'),
    ('<h1 class="ph-title">33娘</h1>', '<h1 class="ph-title">AI 助手</h1>'),
    ('<div class="who"><span class="ava">33</span>33娘</div>',
     '<div class="who"><span class="ava">AI</span>AI 助手</div>'),
    ('title="33娘（AI 助手）"', 'title="AI 助手"'),
    ('<a href="#ai">33娘</a>', '<a href="#ai">AI 助手</a>'),
    ('<div style="font-weight:700">33娘</div>',
     '<div style="font-weight:700">AI 助手</div>'),

    # ---- 正文描述 ----
    ('33娘 会答疑并给出可执行方案', 'AI 助手会答疑并给出可执行方案'),
    ('<h3>33娘 · AI 运营助手</h3><p>本站看板娘兼运维机娘，说话冷冰冰。把需求说给她，'
     '自动生成互赞 / 投稿 / 私信方案与模板，确认后一键创建执行。</p>',
     '<h3>AI 运营助手</h3><p>把你的需求说清楚，自动生成互赞 / 投稿 / 私信方案与模板，'
     '确认后一键创建执行。</p>'),
    ('<h2 class="lp-title">遇到问题，先问 33娘</h2>',
     '<h2 class="lp-title">遇到问题，先问 AI 助手</h2>'),
    ('想让 33娘 ', '想让 AI 助手 '),
    ('<summary>33娘（AI 助手）怎么用？</summary><p>登录后点顶栏的「33娘」（或右下角按钮）即可对话。'
     '她说话偏冷淡、话少，',
     '<summary>AI 助手怎么用？</summary><p>登录后点顶栏的「AI 助手」（或右下角按钮）即可对话。'),
    ('<span class="r-t">33娘 · AI 运营助手</span>',
     '<span class="r-t">AI 运营助手</span>'),
    ('alt="22娘 &amp; 33娘"', 'alt="B站形象素材"'),
]

# 导航链接单独处理：凡是 <a ...>33娘</a> 形式的
NAV_RE = re.compile(r'(<a\b[^>]*>)33娘(</a>)')

changed = []
for f in sorted((ROOT / "app" / "templates").rglob("*.html")):
    t = f.read_text(encoding="utf-8")
    o = t
    for a, b in PAIRS:
        t = t.replace(a, b)
    t = NAV_RE.sub(r"\1AI 助手\2", t)
    if t != o:
        f.write_text(t, encoding="utf-8")
        changed.append(f.name)
print("改过的模板:", ", ".join(changed) or "无")

# ---- 后端文案 ----
BACK = [
    ('"33娘"', '"AI 助手"'),
    ("'33娘'", "'AI 助手'"),
    ('33娘', 'AI 助手'),
]
for name in ("routers/ai_api.py", "routers/fills.py", "config.py"):
    f = ROOT / "app" / name
    if not f.exists():
        continue
    t = f.read_text(encoding="utf-8")
    o = t
    for a, b in BACK:
        t = t.replace(a, b)
    if t != o:
        f.write_text(t, encoding="utf-8")
        print("改过后端:", name)

print("\n完成")
