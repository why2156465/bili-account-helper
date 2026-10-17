from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\worker.py")
t = p.read_text(encoding="utf-8")

# 抽一个「推进下一次」的小工具，所有返回路径都必须调用
helper = '''def _advance(row: dict, started: int) -> None:
    """推进 next_run。**所有**退出路径都必须调它。

    早期版本只在"执行完之后"推进，于是配置损坏 / 用户被删这类提前 return
    的分支会把 next_run 永远留在过去 —— 每分钟重试一次，日志刷屏。
    """
    with db() as conn:
        nxt = 0
        if row.get("cron"):
            now = int(time.time())
            nxt = _next_run(row["cron"], now) or 0
            if nxt and nxt <= now:                      # 兜底：绝不能落在过去
                nxt = _next_run(row["cron"], now + 1) or 0
        conn.execute("UPDATE interact_tasks SET last_run=?, next_run=? WHERE id=?",
                     (started, nxt, row["id"]))


def _run_one(row: dict) -> str:'''

old_def = 'def _run_one(row: dict) -> str:'
assert old_def in t
t = t.replace(old_def, helper, 1)

# 提前返回路径补推进
t = t.replace(
'''    except json.JSONDecodeError:
        _log_run(task_id, row["user_id"], "fail", "配置不是合法 JSON", started, started)
        return "配置损坏"''',
'''    except json.JSONDecodeError:
        _log_run(task_id, row["user_id"], "fail", "配置不是合法 JSON", started, started)
        _advance(row, started)
        return "配置损坏"''')

t = t.replace(
'''    if not u:
        _log_run(task_id, row["user_id"], "fail", "任务归属用户已不存在", started, started)
        return "用户不存在"''',
'''    if not u:
        _log_run(task_id, row["user_id"], "fail", "任务归属用户已不存在", started, started)
        _advance(row, started)
        return "用户不存在"''')

# 正常路径改调 _advance
old_ok = '''    ended = int(time.time())
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
new_ok = '''    ended = int(time.time())
    _log_run(task_id, row["user_id"], status, message, started, ended)
    _advance(row, started)'''
assert old_ok in t, "normal path not found"
t = t.replace(old_ok, new_ok)

p.write_text(t, encoding="utf-8")
print("worker: 所有退出路径都推进 next_run")
