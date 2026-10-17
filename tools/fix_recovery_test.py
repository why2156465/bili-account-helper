from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\tests\worker_recovery.py")
t = p.read_text(encoding="utf-8")

# 修 [2]：每轮之前把 next_run 拨回过去，模拟"每分钟到点"
old = '''        for i in range(3):
            wk.tick()
            with db() as conn:
                row = conn.execute("SELECT next_run FROM interact_tasks WHERE id=?",
                                   (bad_tid,)).fetchone()
            ck("第 %d 轮后 next_run 仍推进" % (i + 1), row["next_run"] > time.time(),
               row["next_run"])
            with db() as conn:
                n = conn.execute("SELECT COUNT(*) c FROM task_runs WHERE task_id=?",
                                 (bad_tid,)).fetchone()["c"]
            ck("第 %d 轮写了执行记录" % (i + 1), n >= i + 1, n)
            time.sleep(0.05)'''

new = '''        for i in range(3):
            # 每轮先把它拨回过去，模拟"到点了该执行"
            with db() as conn:
                conn.execute("UPDATE interact_tasks SET next_run=? WHERE id=?",
                             (int(time.time()) - 5, bad_tid))
            wk.tick()
            with db() as conn:
                row = conn.execute("SELECT next_run FROM interact_tasks WHERE id=?",
                                   (bad_tid,)).fetchone()
            ck("第 %d 轮后 next_run 推进到未来" % (i + 1),
               row["next_run"] > time.time(), row["next_run"])
            with db() as conn:
                n = conn.execute("SELECT COUNT(*) c FROM task_runs WHERE task_id=?",
                                 (bad_tid,)).fetchone()["c"]
            ck("第 %d 轮写了执行记录" % (i + 1), n == i + 1, n)'''

assert old in t, "loop block not found"
t = t.replace(old, new)
p.write_text(t, encoding="utf-8")
print("worker_recovery.py: [2] 每轮重置 next_run")
