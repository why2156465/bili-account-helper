from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\routers\interact.py")
t = p.read_text(encoding="utf-8")

old = '''            st.ok += ok
            st.fail += fail
            if ok:
                bump_usage(user_id, "like", ok)
            log_op(user_id, "interact", video.get("bvid") or str(video.get("aid")),
                   "ok" if not fail else "partial", None, acc["id"])
            for m in msgs:
                st.push(m, "err")
            st.push("[%s] %s 完成 %d 项，失败 %d 项"
                    % (acc["name"] or acc["mid"], title, ok, fail),
                    "ok" if not fail else "warn")
'''

new = '''            st.ok += ok
            st.fail += fail
            if ok:
                bump_usage(user_id, "like", ok)
            log_op(user_id, "interact", video.get("bvid") or str(video.get("aid")),
                   "ok" if not fail else "partial", None, acc["id"])

            # do_one 里遇到凭据/风控类致命错误会用 "FATAL:" 前缀回报。
            # 此时账号已经不能干活了，必须把状态写回库并中止整个任务，
            # 否则剩余几十个视频会一个一个全试一遍全部失败。
            fatal_msg = next((m for m in msgs if m.startswith("FATAL:")), "")
            for m in msgs:
                st.push(m.replace("FATAL:", "账号不可用："), "err")
            if fatal_msg:
                status = "expired" if ("-101" in fatal_msg or "-111" in fatal_msg
                                       or "61001" in fatal_msg) else "captcha"
                with db() as conn:
                    conn.execute(
                        "UPDATE bili_accounts SET status=?, coeff=coeff*1.5 WHERE id=?",
                        (status, acc["id"]))
                st.error = fatal_msg.replace("FATAL:", "")
                st.push("账号已失效/被风控，中止任务。去「账号」页重新登录后再跑。", "err")
                break
            st.push("[%s] %s 完成 %d 项，失败 %d 项"
                    % (acc["name"] or acc["mid"], title, ok, fail),
                    "ok" if not fail else "warn")
'''

assert old in t, "loop body not found"
t = t.replace(old, new, 1)
p.write_text(t, encoding="utf-8")
print("_run_loop: 收到 FATAL 时写回账号状态并中止任务")
