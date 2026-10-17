"""开源前侦察：把归档快照里所有外部引用抽干净，并做本地可得的风险判定。

用法：python tools/recon.py
产出：docs/recon_hosts.json（结构化）+ 控制台表格
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCH = ROOT / "_archive"
OUT = ROOT / "docs" / "recon_hosts.json"

TEXT_EXT = {".html", ".css", ".js", ".json", ".svg", ".txt", ".webmanifest"}

URL_RE = re.compile(r'(?:https?:)?//([A-Za-z0-9._-]+\.[A-Za-z]{2,})')
ATTR_RE = re.compile(r'(?:href|src|action|data-src)\s*=\s*["\']([^"\']+)["\']')
CALL_RE = re.compile(r'(?:fetch|\.open|axios\.(?:get|post)|XMLHttpRequest)\s*\(\s*["\']([^"\']+)["\']')
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')

# 已知良性基础设施：出现在 SVG 命名空间或公共文档里，不代表供应链依赖
BENIGN = {
    "www.w3.org": "SVG/XML 命名空间，非网络请求",
}


def text_files() -> list[Path]:
    return [p for p in ARCH.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_EXT]


def classify(host: str, samples: list[str]) -> dict:
    h = host.lower().lstrip(".")
    entry = {"host": h, "kind": "third-party", "risk": "info", "note": ""}

    if h in BENIGN:
        entry["kind"] = "namespace"
        entry["note"] = BENIGN[h]
        return entry
    if h == "mssss.cn" or h.endswith(".mssss.cn"):
        entry["kind"] = "self"
        entry["note"] = "目标站点自身域名"
        return entry
    if any(s in h for s in ("bilibili.com", "hdslb.com", "biliapi.net", "bilivideo.com")):
        entry["kind"] = "upstream"
        entry["note"] = "上游业务接口/静态资源"
        return entry
    if any(s in h for s in ("dnspod", "dnspod.net")):
        entry["kind"] = "dns"
        entry["note"] = "DNS 权威服务器（解析层，不在页面里加载）"
        return entry
    if h.endswith(".126.com") or h == "126.com":
        entry["kind"] = "mail"
        entry["note"] = "邮箱 MX 域名，仅出现在 mailto 与文案里"
        return entry
    if any(s in h for s in ("cdn", "jsdelivr", "unpkg", "googleapis", "gstatic", "cdnjs")):
        entry["risk"] = "medium"
        entry["note"] = "第三方 CDN —— 供应链风险，需确认 SRI 或自托管"
        return entry
    if re.match(r'^\d+\.\d+\.\d+\.\d+$', h):
        entry["risk"] = "medium"
        entry["note"] = "裸 IP 引用，需确认用途"
        return entry
    entry["risk"] = "low"
    entry["note"] = "未归类的外部域名，需人工确认"
    return entry


def _lookup(host: str) -> dict:
    """本地 DNS 解析（不访问任何情报 API），用于判断域名是否存在、归属哪家 CDN。"""
    info: dict = {"a": [], "cname": [], "ns": []}
    try:
        out = subprocess.run(["nslookup", host], capture_output=True, text=True, timeout=8)
        txt = out.stdout + out.stderr
        info["a"] = sorted(set(re.findall(r"Address:\s*((?:\d{1,3}\.){3}\d{1,3})", txt)) - {"0.0.0.0"})
        info["cname"] = sorted(set(re.findall(r"canonical name\s*=\s*(\S+)", txt)))
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def main() -> int:
    files = text_files()
    print(f"扫描归档文件 {len(files)} 个：{[p.name for p in files]}")

    hosts: dict[str, dict] = {}
    for p in files:
        txt = p.read_text(encoding="utf-8", errors="replace")
        samples: list[str] = []
        samples += URL_RE.findall(txt)
        for attr in ATTR_RE.findall(txt):
            m = URL_RE.match(attr)
            if m:
                samples.append(m.group(1))
            elif attr.startswith("//"):
                samples.append(attr[2:].split("/")[0])
        for h in samples + EMAIL_RE.findall(txt):
            key = h.lower().lstrip(".")
            hosts.setdefault(key, {"files": set(), "samples": set()})
            hosts[key]["files"].add(p.name)
            hosts[key]["samples"].add(h)

    # 显式补上从响应头/重定向里观测到的解析层域名（不在页面文本里）
    for extra, note in (("mssss.cn", "页面 Host"), ):
        hosts.setdefault(extra, {"files": set(), "samples": set()})
        hosts[extra]["samples"].add(note)

    rows = []
    for host, v in sorted(hosts.items()):
        entry = classify(host, sorted(v["samples"]))
        entry["files"] = sorted(v["files"])
        entry["references"] = len(v["samples"])
        entry["dns"] = _lookup(host) if entry["kind"] in ("third-party", "upstream", "self") else {}
        rows.append(entry)

    payload = {"target": "bili.mssss.cn", "archived_files": [p.name for p in files], "hosts": rows}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{'HOST':<28}{'类别':<12}{'风险':<9}说明")
    print("-" * 96)
    for r in rows:
        print(f"{r['host']:<28}{r['kind']:<12}{r['risk']:<9}{r['note']}")
    print(f"\n共 {len(rows)} 个外部引用点 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
