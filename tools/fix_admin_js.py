from pathlib import Path

p = Path(r"C:\Users\w1994\Documents\deepseek-harness\default-workspace\bili-account-helper\app\templates\admin\overview.html")
t = p.read_text(encoding="utf-8")

# 1) 删掉孤立残骸（会员/流量的三段死代码）
junk = [
    "            BP.toast('已开通 30 天'); loadUsers(); loadStats();\n      };\n    });\n",
    "            BP.toast('已加 1GB'); loadUsers();\n      };\n    });\n",
]
for j in junk:
    if j in t:
        t = t.replace(j, "")
        print("删掉残骸:", j.strip().splitlines()[0][:40])

# 2) 用户表：去掉"流量"列（已无流量概念）
t = t.replace("+'<td>'+u.traffic_mb+' MB</td><td>'+u.accounts+'</td>'",
              "+'<td>'+u.accounts+'</td>'")
t = t.replace("colspan=\"9\" class=\"empty\">没有用户", "colspan=\"7\" class=\"empty\">没有用户")

# 3) 表头去掉「流量」与「会员」列
t = t.replace("<th>流量</th>", "")
t = t.replace("<th>会员</th>", "")

p.write_text(t, encoding="utf-8")
print("\nadmin/overview.html 已修")
