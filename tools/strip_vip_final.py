import re
from pathlib import Path

TPL = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\templates")


def edit(name, pairs, regex_pairs=()):
    f = TPL / name
    if not f.exists():
        return
    src = f.read_text(encoding="utf-8")
    orig = src
    for old, new in pairs:
        src = src.replace(old, new)
    for pat, new in regex_pairs:
        src = re.sub(pat, new, src, flags=re.S)
    if src != orig:
        f.write_text(src, encoding="utf-8")
        print("改:", name)


# ---- base.html：删掉整段 vip/info 轮询 ----
edit("base.html", [], [
    (r"\s*BP\.api\('/api/vip/info'\)\.then\(function\(d\)\{.*?\}\)\.catch\(function\(\)\{\}\);\s*", "\n"),
])

# ---- me.html：会员卡片整块去掉 ----
edit("me.html", [
    ('<div class="sec-t">会员与记录</div>', '<div class="sec-t">记录</div>'),
], [
    (r"\s*var v=await BP\.api\('/api/vip/info'\);.*?mVipBtn'\)\.textContent=v\.vip\.active\?'续费':'开通会员';", ""),
    (r'<div class="me-vip" id="mVip".*?</div>\s*</div>', ""),
    (r'<a class="row" href="/invite">.*?</a>', ""),
])

# ---- dashboard.html：邀请卡整块删 ----
edit("dashboard.html", [], [
    (r"\s*\(async function\(\)\{\s*var d=await BP\.api\('/api/vip/invite'\);.*?\}\)\(\);", ""),
    (r'<div class="card" style="margin:0" id="invCard">.*?</div>\s*</div>\s*</div>', "</div>"),
])

# ---- index.html：落地页去掉会员话术 ----
edit("index.html", [
    ('<a href="#vip">会员权益</a>', ''),
    ('<h2 class="lp-title">免费也能用，会员更强大</h2>',
     '<h2 class="lp-title">开箱即用，无需配置</h2>'),
    ('<p class="lp-desc">先用免费版上手，需要批量运营时再升级会员</p>',
     '<p class="lp-desc">装好就能跑，所有功能默认可用</p>'),
    ('<li>定时投稿（自动发布）<b>会员自动解锁</b></li>',
     '<li>定时投稿（自动发布）</li>'),
    ('<li>解释会员权益与额度差异</li>', '<li>排查任务执行失败的原因</li>'),
    ('严禁批量注册小号、多账号互刷或利用小号反复薅取免费额度。',
     '严禁批量注册小号、多账号互刷。'),
])

# ---- library.html：额度文案 ----
edit("library.html", [
    ('<span class="ph-sub">额度 {{ limits.video_lib_mb }} MB · 已用 <b id="libUsed">-</b></span>',
     '<span class="ph-sub">已用 <b id="libUsed">-</b></span>'),
])

# ---- messages.html：私信额度提示 ----
edit("messages.html", [
    ("? ('今日已发 '+d.used+' 条 · 会员不限量')", "? ('今日已发 '+d.used+' 条')"),
])

# ---- publish.html ----
edit("publish.html", [
    ('占用视频库容量（普通用户 200 MB / 会员 5 GB）。', '占用视频库容量。'),
], [
    (r"\s*var v=await BP\.api\('/api/vip/info'\);.*?(?=\n\s*\})", ""),
])

# ---- settings.html ----
edit("settings.html", [
    ('<p class="hint" style="margin:0 0 10px">提醒邮件（账号失效 / 流量不足 / 会员即将到期）仅会员可收到，普通用户不提醒。</p>',
     '<p class="hint" style="margin:0 0 10px">账号登录态失效时会给你发提醒邮件。</p>'),
])

# ---- tasks.html ----
edit("tasks.html", [
    ('「指定 UID」普通用户最多添加 1 个，会员不限；可操作任意 UID 的投稿与图文。',
     '「指定 UID」可添加任意个；可操作任意 UID 的投稿与图文。'),
])

print("\n完成")
