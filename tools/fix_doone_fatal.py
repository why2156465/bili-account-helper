from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\routers\interact.py")
t = p.read_text(encoding="utf-8")

# ---- 1) do_one：凭据类错误立刻停止后续动作，并回报状态 ----
old_head = '''def do_one(cli: BiliClient, video: dict, actions: dict, accounts_uid: str) -> tuple[int, int, list[str]]:
    """对单个视频执行一组动作。返回 (成功数, 失败数, 消息)。"""
    aid = video.get("aid")
    like_uid = video.get("mid")
    ok = fail = 0
    msgs: list[str] = []
'''

new_head = '''# 这些错误意味着「这个账号不能再干活了」，继续跑剩下的动作纯属浪费请求
FATAL_CODES = {-101, -111, 61001, -352, -412}


def do_one(cli: BiliClient, video: dict, actions: dict,
           accounts_uid: str) -> tuple[int, int, list[str]]:
    """对单个视频执行一组动作。返回 (成功数, 失败数, 消息)。

    任一动作拿到 FATAL_CODES（未登录/失效/风控）时立即停止剩余动作 —— 否则
    一个已失效的账号会对同一个视频把 8 个动作全试一遍，全部失败，还白白
    触发更多风控。fatal 通过 msgs 里带 "FATAL:" 前缀回传给调用方。
    """
    aid = video.get("aid")
    like_uid = video.get("mid")
    ok = fail = 0
    msgs: list[str] = []

    def fatal(e) -> bool:
        code = getattr(e, "code", None)
        if code in FATAL_CODES:
            msgs.append("FATAL:%s" % (getattr(e, "message", "") or {}))
            return True
        return False
'''

assert old_head in t, "do_one head not found"
t = t.replace(old_head, new_head, 1)

# 每个 except BiliError 分支里补一句 fatal 判断 + 提前返回
pairs = [
    ('''        except BiliError as e:
            fail += 1
            msgs.append("点赞失败：%s" % e.message)''',
     '''        except BiliError as e:
            fail += 1
            msgs.append("点赞失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs'''),
    ('''        except BiliError as e:
            fail += 1
            msgs.append("投币失败：%s" % e.message)''',
     '''        except BiliError as e:
            fail += 1
            msgs.append("投币失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs'''),
    ('''        except BiliError as e:
            fail += 1
            msgs.append("收藏失败：%s" % e.message)''',
     '''        except BiliError as e:
            fail += 1
            msgs.append("收藏失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs'''),
    ('''        except BiliError as e:
            fail += 1
            msgs.append("评论失败：%s" % e.message)''',
     '''        except BiliError as e:
            fail += 1
            msgs.append("评论失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs'''),
    ('''        except BiliError as e:
            fail += 1
            msgs.append("分享失败：%s" % e.message)''',
     '''        except BiliError as e:
            fail += 1
            msgs.append("分享失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs'''),
    ('''        except BiliError as e:
            fail += 1
            msgs.append("关注失败：%s" % e.message)''',
     '''        except BiliError as e:
            fail += 1
            msgs.append("关注失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs'''),
    ('''        except BiliError as e:
            fail += 1
            msgs.append("刷播放失败：%s" % e.message)''',
     '''        except BiliError as e:
            fail += 1
            msgs.append("刷播放失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs'''),
    ('''        except BiliError as e:
            fail += 1
            msgs.append("图文操作失败：%s" % e.message)''',
     '''        except BiliError as e:
            fail += 1
            msgs.append("图文操作失败：%s" % e.message)
            if fatal(e):
                return ok, fail, msgs'''),
]
for old, new in pairs:
    assert old in t, "branch not found: %s" % old[:40]
    t = t.replace(old, new, 1)

p.write_text(t, encoding="utf-8")
print("do_one: 遇到 FATAL 立即停止剩余动作")
