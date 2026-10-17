from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\worker.py")
t = p.read_text(encoding="utf-8")

old = '''    ended = int(time.time())
    _log_run(task_id, row["user_id"], status, message, started, ended)
    with db() as conn:
        nxt = _next_run(row["cron"], ended) if row["cron"] else 0
        conn.execute("UPDATE interact_tasks SET last_run=?, next_run=? WHERE id=?",
                     (started, nxt, task_id))'''

new = '''    ended = int(time.time())
    _log_run(task_id, row["user_id"], status, message, started, ended)
    with db() as conn:
        # 基准必须是「现在」，不能是 ended —— 任务是从过去某点触发的，
        # ended 可能仍早于当前时刻，算出来的 next_run 会落在过去，
        # 下一轮 tick 立刻又捞到它，形成无限重试。
        nxt = _next_run(row["cron"], ended) if row["cron"] else 0
        if nxt and nxt <= int(time.time()):
            nxt = _next_run(row["cron"], int(time.time())) or 0
        conn.execute("UPDATE interact_tasks SET last_run=?, next_run=? WHERE id=?",
                     (started, nxt, task_id))'''

assert old in t, "worker block not found"
t = t.replace(old, new)
p.write_text(t, encoding="utf-8")
print("worker: next_run 基准改为当前时间")
