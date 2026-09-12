"""诊断第二阶段：账号状态详情 + 大窗口系统日志过滤。"""
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

    # 1. 账号列表状态
    r = s.get(f"{BASE}/cookies/details", timeout=60)
    rows = r.json()
    if isinstance(rows, dict):
        rows = rows.get("cookies") or rows.get("data") or []
    for row in rows:
        if str(row.get("id")) == COOKIE_ID:
            keys = ("id", "status", "enabled", "paused", "pause_reason", "last_error",
                    "last_token_refresh_error_message", "last_token_refresh_error_until",
                    "qr_login_grace_until", "updated_at", "remark")
            slim = {k: row.get(k) for k in keys if k in row}
            print("=== 账号状态行 ===")
            print(json.dumps(slim, ensure_ascii=False, indent=1, default=str)[:2000])

    # 2. runtime-status
    r = s.get(f"{BASE}/cookies/{COOKIE_ID}/runtime-status", timeout=60)
    print("\n=== runtime-status ===")
    print(json.dumps(r.json(), ensure_ascii=False, default=str)[:2500])

    # 3. 大窗口日志过滤
    r = s.get(f"{BASE}/logs", params={"lines": 3000}, timeout=60)
    entries = r.json().get("logs") or []
    print(f"\n=== 系统日志 {len(entries)} 条，过滤关键词 ===")
    pat = re.compile(r"二维码|QR|qr_login|暂停|pause|稳定期|grace|1926782908|token.*失效|EXPIRED|风控|验证")
    shown = 0
    for l in reversed(entries):
        msg = l.get("message", "") if isinstance(l, dict) else str(l)
        ts = l.get("timestamp", "") if isinstance(l, dict) else ""
        if pat.search(msg):
            shown += 1
            print(f"[{ts}] {msg[:260]}")
            if shown >= 60:
                break


if __name__ == "__main__":
    main()
