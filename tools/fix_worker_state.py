from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\routers\interact.py")
t = p.read_text(encoding="utf-8")

# 把整个执行体套进 try/finally，收尾状态一定被重置。
# 原来收尾代码在 for 循环之后，循环体一抛未捕获异常就永远 running=True，
# 界面上的"正在执行"再也不消失，后续任务全部被 409 挡住。
old = '''    sleep_range = cfg.get("sleep") or [3, 7]
    try:
        smin = max(1, int(sleep_range[0]))
        smax = max(smin, int(sleep_range[1]))
    except (ValueError, TypeError, IndexError):
        smin, smax = 3, 7

    for idx, (acc_id, video) in enumerate(jobs, 1):'''

new = '''    sleep_range = cfg.get("sleep") or [3, 7]
    try:
        smin = max(1, int(sleep_range[0]))
        smax = max(smin, int(sleep_range[1]))
    except (ValueError, TypeError, IndexError):
        smin, smax = 3, 7

    try:
        _run_loop(st, user_id, cfg, jobs, smin, smax)
    except Exception as exc:
        # 兜底：任何未预期异常都不能让 STATE 卡在 running=True
        st.push("任务异常中止：%s: %s" % (type(exc).__name__, exc), "err")
        st.error = str(exc)
        print("[interact] 任务异常：%s" % exc, flush=True)
    finally:
        with st.lock:
            st.running = False
            st.finished_at = int(time.time())
            st.current = ""
        st.push("任务结束：成功 %d，失败 %d" % (st.ok, st.fail),
                "ok" if not st.error else "warn")


def _run_loop(st, user_id: int, cfg: dict, jobs: list, smin: int, smax: int) -> None:
    """真正跑任务循环。异常向上抛给 _run_worker 的兜底处理。"""
    for idx, (acc_id, video) in enumerate(jobs, 1):'''

assert old in t, "header not found"
t = t.replace(old, new, 1)

# 删掉原来 for 循环之后的收尾代码块（已移到 finally）
old_tail = '''        st.done = idx
        if idx < len(jobs):
            time.sleep(random_sleep(smin, smax))

    with st.lock:
        st.running = False
        st.finished_at = int(time.time())
        st.current = ""
        st.push("任务结束：成功 %d，失败 %d" % (st.ok, st.fail),
                "ok" if not st.error else "warn")'''

new_tail = '''        st.done = idx
        if idx < len(jobs):
            time.sleep(random_sleep(smin, smax))'''

assert old_tail in t, "tail not found"
t = t.replace(old_tail, new_tail, 1)

# video 可能不是 dict，取值前先防一手
t = t.replace(
'''        title = (video.get("title") or video.get("bvid") or "")[:28]''',
'''        if not isinstance(video, dict):
            st.fail += 1
            st.push("目标数据异常，跳过（%r）" % (video,), "err")
            st.done = idx
            continue
        title = (video.get("title") or video.get("bvid") or "")[:28]''')

p.write_text(t, encoding="utf-8")
print("interact: 执行体包进 try/finally，异常不再卡住 STATE")
