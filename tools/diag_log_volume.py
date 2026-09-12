"""诊断：实测生产日志文件体量 + 最新日志 Top 重复消息分布。"""
import json
import re
from collections import Counter

import requests

VAULT = r"E:\profile\note\note\Note\infra\ssh-credentials.md"
BASE = "https://xianyu.mangoqwq.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Codex/1.0"


def read_panel_password() -> str:
    with open(VAULT, encoding="utf-8") as f:
        text = f.read()
    return re.search(r"统一密码 `(\d+)`", text).group(1)


def main() -> None:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.post(f"{BASE}/login", json={"username": "mango", "password": read_panel_password()}, timeout=30)
    r.raise_for_status()
    body = r.json()
    token = body.get("token") or (body.get("data") or {}).get("token")
    s.headers["Authorization"] = f"Bearer {token}"

    r = s.get(f"{BASE}/admin/log-files", timeout=60)
    files = (r.json() or {}).get("files") or []
    print("=== 生产日志文件 ===")
    for f in files[:10]:
        print(f"{f['name']:36s} {f['size']/1024/1024:8.2f} MB  {f['modified_at']}")

    r = s.get(f"{BASE}/admin/logs", params={"lines": 5000}, timeout=120)
    data = r.json()
    lines = data.get("logs") or []
    print(f"\n=== 最新 {len(lines)} 行样本（{data.get('log_file')}） ===")
    pat = re.compile(r"^(\S+ \S+) \|\s*(\w+)\s*\| ([^-]+)- (.*)$")
    level_counter = Counter()
    msg_counter = Counter()

    def normalize(msg: str) -> str:
        msg = re.sub(r"\d{1,3}(?:\.\d{1,3}){3}", "<IP>", msg)
        msg = re.sub(r"\b\d{6,}\b", "<NUM>", msg)
        msg = re.sub(r"剩余[\d.]+秒", "剩余<N>秒", msg)
        msg = re.sub(r"\bID:[a-zA-Z0-9]+", "ID:<X>", msg)
        msg = re.sub(r"0x[0-9a-fA-F]+", "<HEX>", msg)
        return msg[:120]

    for line in lines:
        m = pat.match(line)
        if not m:
            msg_counter[f"<UNPARSED> {line[:100]}"] += 1
            continue
        _, level, src, msg = m.groups()
        level_counter[level] += 1
        msg_counter[f"[{level}] {normalize(msg)}"] += 1

    print(f"\n--- 级别分布 ---")
    for lv, n in level_counter.most_common():
        print(f"{lv:10s} {n:6d} ({n*100//max(1,len(lines))}%)")

    print(f"\n--- Top 25 重复消息 ---")
    for msg, n in msg_counter.most_common(25):
        print(f"{n:6d}  {msg}")


if __name__ == "__main__":
    main()
