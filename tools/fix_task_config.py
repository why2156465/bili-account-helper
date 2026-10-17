from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\tests\task_config.py")
t = p.read_text(encoding="utf-8")

old_flat = '''        # latest
        got = resolve_targets(cli, [{"uid": "1", "rule": "latest", "max": 1}])
        ck("latest 取 1 个", len(got) == 1, got)
        ck("latest 取最新那条", got[0].get("aid") == 333, got)
        # all
        got = resolve_targets(cli, [{"uid": "1", "rule": "all", "max": 10}])
        ck("all 取全部", len(got) == 3, len(got))
        # random
        got = resolve_targets(cli, [{"uid": "1", "rule": "random", "max": 10, "n": 2}])
        ck("random 取 2 个", len(got) == 2, len(got))
        # bvids 直填
        got = resolve_targets(cli, [{"rule": "bvids",
                                     "bvids": ["BV1aa", "BV1bb"]}])
        ck("bvids 直填 2 个", len(got) == 2, got)'''

new_flat = '''        # resolve_targets 返回 [{uid,count,videos:[...]}]，每个目标一组
        def flat(groups):
            return [v for g in groups for v in (g.get("videos") or [])]

        got = resolve_targets(cli, [{"uid": "1", "rule": "latest", "max": 1}])
        ck("latest 返回 1 组", len(got) == 1, got)
        ck("latest 组内 count=1", got[0].get("count") == 1, got)
        ck("latest 取最新那条", flat(got)[0].get("aid") == 333, flat(got))
        got = resolve_targets(cli, [{"uid": "1", "rule": "all", "max": 10}])
        ck("all 取全部 3 个", len(flat(got)) == 3, flat(got))
        got = resolve_targets(cli, [{"uid": "1", "rule": "random", "max": 10, "n": 2}])
        ck("random 取 2 个", len(flat(got)) == 2, flat(got))
        got = resolve_targets(cli, [{"rule": "bvids", "bvids": ["BV1aa", "BV1bb"]}])
        ck("bvids 直填 2 个", len(flat(got)) == 2, flat(got))'''

assert old_flat in t, "flat block not found"
t = t.replace(old_flat, new_flat)

old_run = '''        print("[1] 完整 config 被接受（不报未知动作）")
        STATE.running = False
        r = c.post("/api/tasks/run", json=FULL_CFG)
        ck("run 返回 200", r.status_code == 200, r.text)
        STATE.running = False'''

new_run = '''        print("[0] 先加一个账号（run 需要执行者）")
        from app.security import encrypt_cookie
        with db() as conn:
            conn.execute(
                "INSERT INTO bili_accounts(user_id,mid,uname,face,cred_enc,status,cred_source,"
                "created_at,proxy,coin_ok) VALUES(1,'9001','主号','',?,'ok','qr',1,'',1)",
                (encrypt_cookie('{"SESSDATA":"x","bili_jct":"y","DedeUserID":"9001"}'),))
        ck("账号已加", len(c.get("/api/accounts").json()["accounts"]) == 1)

        print("[1] 完整 config 被接受（不报未知动作）")
        STATE.running = False
        r = c.post("/api/tasks/run", json=FULL_CFG)
        ck("run 返回 200", r.status_code == 200, r.text)
        STATE.running = False'''

assert old_run in t, "run block not found"
t = t.replace(old_run, new_run)
p.write_text(t, encoding="utf-8")
print("task_config.py 修正完成")
