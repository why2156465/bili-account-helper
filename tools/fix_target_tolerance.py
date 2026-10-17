from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\routers\interact.py")
t = p.read_text(encoding="utf-8")

# 目标级容错：单个 UID 拉取失败只记该组 error，不拖垮整个任务
old = '''        need = int(t.get("max") or (1 if rule == "latest" else 10))
        if rule == "random":
            need = max(need, int(t.get("n") or 1), 20)
        pool = _fetch_up_videos(cli, uid, min(max(need, 10), 150))'''

new = '''        need = int(t.get("max") or (1 if rule == "latest" else 10))
        if rule == "random":
            need = max(need, int(t.get("n") or 1), 20)
        try:
            pool = _fetch_up_videos(cli, uid, min(max(need, 10), 150))
        except BiliError as exc:
            # 单个目标拿不到（UID 不存在 / 风控）只跳过这一组，
            # 不能因为一个坏 UID 让整批任务都跑不起来
            _, emsg = describe_error(exc)
            results.append({"uid": uid, "count": 0, "videos": [],
                            "error": "拉取投稿失败：%s" % emsg})
            continue'''

assert old in t, "block not found"
t = t.replace(old, new)

# bvids 分支同样要容错（原来只 catch BiliError，其实已经有了；补上通用异常）
old2 = '''                except BiliError:
                    continue'''
new2 = '''                except BiliError:
                    continue
                except Exception:
                    continue'''
if old2 in t:
    t = t.replace(old2, new2, 1)

p.write_text(t, encoding="utf-8")
print("resolve_targets: 目标级容错")
