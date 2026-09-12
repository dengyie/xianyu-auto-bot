"""诊断第三阶段：完整账号行 + 风控日志原始条目 + 审计日志找停用原因。"""
import json
import re

import requests

VAULT = r"E:\profile\note\note\Note\infra\ssh-credentials.md"
BASE = "https://xianyu.mangoqwq.com"
COOKIE_ID = "1926782908"
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

    # 1. 完整账号行
    r = s.get(f"{BASE}/cookies/details", timeout=60)
    rows = r.json()
    if isinstance(rows, dict):
        rows = rows.get("cookies") or rows.get("data") or []
    for row in rows:
        if str(row.get("id")) == COOKIE_ID:
            slim = {k: v for k, v in row.items() if k not in ("cookies_str", "password")}
            print("=== 完整账号行（脱敏） ===")
            print(json.dumps(slim, ensure_ascii=False, indent=1, default=str)[:3500])

    # 2. 风控日志原始条目
    r = s.get(f"{BASE}/risk-control-logs", params={"limit": 8}, timeout=60)
    data = r.json()
    logs = data if isinstance(data, list) else (data.get("logs") or data.get("events") or [])
    print(f"\n=== 风控日志原始 {len(logs)} 条 ===")
    for e in logs[:8]:
        print(json.dumps(e, ensure_ascii=False, default=str)[:500])

    # 3. 审计日志找停用动作
    r = s.get(f"{BASE}/admin/audit-logs", params={"limit": 50}, timeout=60)
    print(f"\n=== audit-logs: {r.status_code} ===")
    if r.status_code == 200:
        data = r.json()
        entries = data if isinstance(data, list) else (data.get("logs") or data.get("entries") or [])
        for e in entries[:50]:
            msg = json.dumps(e, ensure_ascii=False, default=str)
            if re.search(r"1926782908|disable|enabled|停用|暂停|toggle", msg, re.I):
                print(msg[:400])


if __name__ == "__main__":
    main()
