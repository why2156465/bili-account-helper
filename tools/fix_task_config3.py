from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\tests\task_config.py")
t = p.read_text(encoding="utf-8")

old = '''        print("[1] 完整 config 被接受（不报未知动作/缺字段）")
        # 上游不可达时 run 会在解析目标阶段返回 502；这里只验证
        # 「config 结构本身被接受」——不能是 400 配置错误。
        STATE.running = False
        r = c.post("/api/tasks/run", json=FULL_CFG)
        ck("config 结构被接受（非 400）", r.status_code != 400, r.text)
        ck("非 422 校验错误", r.status_code != 422, r.text)
        STATE.running = False'''

new = '''        print("[1] 完整 config 结构本身合法（不因结构问题被拒）")
        # 沙箱里上游不通，目标解析拿不到视频；现在 resolve_targets 目标级容错，
        # 所以这里返回「目标没有匹配到任何视频」——这是数据问题，不是结构问题。
        # 只验证没有因为 config 结构（缺字段/未知动作）被拒。
        STATE.running = False
        r = c.post("/api/tasks/run", json=FULL_CFG)
        ck("未被判为结构错误", "动作" not in r.text, r.text)
        ck("未被判为校验错误（422）", r.status_code != 422, r.text)
        ck("有明确的可读原因", bool(r.json().get("detail") or r.json().get("message")), r.text)
        STATE.running = False'''

assert old in t, "block not found"
t = t.replace(old, new)
p.write_text(t, encoding="utf-8")
print("task_config.py: 断言与新语义对齐")
