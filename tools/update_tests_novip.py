"""把测试套件里针对「已移除的会员/额度功能」的断言改掉。

原则：不删除测试，改成验证「现在应当不限额/不限量」的新语义 ——
这样万一将来有人把额度逻辑加回来，测试会立刻提醒。
"""
import re
from pathlib import Path

T = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\tests")


def edit(name, pairs=(), regex_pairs=(), drop_blocks=()):
    f = T / name
    if not f.exists():
        return
    src = f.read_text(encoding="utf-8")
    orig = src
    for old, new in pairs:
        src = src.replace(old, new)
    for pat, new in regex_pairs:
        src = re.sub(pat, new, src, flags=re.S)
    for pat in drop_blocks:
        # 删掉一整段以某标记开头的 try 块（用于 vip 专属测试）
        src = re.sub(pat, "", src, flags=re.S)
    if src != orig:
        f.write_text(src, encoding="utf-8")
        print("改:", name)


# ---------------- contract.py：/api/vip/info 的 12 字段检查整段删 ----------------
edit("contract.py", drop_blocks=[
    r'\s*print\("\[[^\]]*\]\s*/api/vip/info[^\n]*\)\n(?:.*\n)*?(?=\s*print\("\[)',
])

# ---------------- scheduler.py：限额断言改成「不限」 ----------------
edit("scheduler.py", [
    ('ck("free 最小间隔 60", p["min_interval"] == 60, p)',
     'ck("不再按档位限制间隔", p["min_interval"] > 0, p)'),
    ('ck("超出条数上限被拒（free 只能 1 条）", r.status_code == 403, r.text)',
     'ck("不限条数（第二条也能建）", r.status_code == 200, r.text)'),
])

# ---------------- messages.py ----------------
edit("messages.py", [
    ('ck("免费用户 daily_limit=10", d["daily_limit"] == 10 and d["unlimited"] is False, d)',
     'ck("私信不限量", d["unlimited"] is True, d)'),
])

# ---------------- ai_api.py ----------------
edit("ai_api.py", [
    ('ck("免费用户 AI 额度 10", d["quota"]["limit"] == 10, d["quota"])',
     'ck("AI 对话不限量（888 为展示上限）", d["quota"]["limit"] >= 888, d["quota"])'),
    ('ck("对话后额度 used=1", j["quota"]["used"] == 1, j["quota"])',
     'ck("对话后仍可用", j["quota"]["remain"] > 0, j["quota"])'),
])

# ---------------- upload.py / fills.py：流量不再扣 ----------------
edit("upload.py", [
    ('ck("流量按 9MB 计算", pl.get("cost_mb") == 9, pl.get("cost_mb"))',
     'ck("不再计算流量消耗", pl.get("cost_mb") == 0, pl.get("cost_mb"))'),
    ('ck("扣了流量", int(u["traffic_used_mb"]) > 0, u["traffic_used_mb"])',
     'ck("流量不再扣减", int(u["traffic_used_mb"]) == 0, u["traffic_used_mb"])'),
])
edit("fills.py", [
    ('ck("扣了流量", int(u["traffic_used_mb"]) > 0, u["traffic_used_mb"])',
     'ck("流量不再扣减", int(u["traffic_used_mb"]) == 0, u["traffic_used_mb"])'),
])

# ---------------- extra.py：邀请接口已删 ----------------
edit("extra.py", [
    ('''        d = c.get("/api/vip/invite").json()
        ck("invite 含 code/invited/qualified",
           all(k in d for k in ("code", "invited", "qualified")), list(d.keys()))
        ck("有邀请码", bool(d["code"]), d)
        d = c.get("/api/vip/orders").json()
        ck("orders 返回 orders", "orders" in d and isinstance(d["orders"], list), d)
        d = c.get("/api/vip/traffic-logs").json()
        ck("traffic-logs 返回 logs/total",
           "logs" in d or "items" in d, list(d.keys()))
        ck("空兑换码被拒",
           c.post("/api/vip/redeem", json={"code": ""}).status_code == 400)''',
     '''        ck("邀请接口已移除", c.get("/api/vip/invite").status_code == 404)
        ck("订单接口已移除", c.get("/api/vip/orders").status_code == 404)
        ck("兑换码接口已移除", c.post("/api/vip/redeem",
                                     json={"code": ""}).status_code == 404)'''),
])

# ---------------- pages.py：删掉 /vip 与 /vip/compare 的期望 ----------------
edit("pages.py", regex_pairs=[
    (r'\s*"/vip": \[[^\]]*\],\n', '\n'),
    (r'\s*"/vip/compare": \[[^\]]*\],\n', '\n'),
])
edit("pages.py", [
    ('"去对话", "邀请好友赚奖励"]', '"去对话"]'),
    ('"/me": ["常用功能", "账号", "会员与记录"]', '"/me": ["常用功能", "账号", "记录"]'),
])

# ---------------- smoke.py ----------------
edit("smoke.py", [
    ('check("免费额度 10", d["quota"]["limit"] == 10, d["quota"])',
     'check("AI 可用且不限量", d["quota"]["limit"] >= 888, d["quota"])'),
], regex_pairs=[
    (r'\s*print\("\[7\] 会员"\)\n(?:.*\n)*?(?=\s*print\("\[8\])', '\n'),
])

print("\n完成")
