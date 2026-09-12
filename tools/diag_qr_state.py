"""诊断账号 1926782908 进入二维码待验证态的根因：拉风控日志 + 系统日志。

密码从 vault ssh-credentials.md 读取，不进 argv/环境/日志。
"""
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
    m = re.search(r"统一密码 `(\d+)`", text)
    if not m:
        raise SystemExit("vault 中未找到统一密码条目")
    return m.group(1)


def main() -> None:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.post(f"{BASE}/login", json={"username": "mango", "password": read_panel_password()}, timeout=30)
    print(f"login: {r.status_code}")
    r.raise_for_status()
    body = r.json()
    token = body.get("token") or (body.get("data") or {}).get("token")
    if not token:
        print("login body keys:", list(body.keys()))
        raise SystemExit("login 响应未含 token")
    s.headers["Authorization"] = f"Bearer {token}"

    r = s.get(f"{BASE}/risk-control-logs", params={"cookie_id": COOKIE_ID, "limit": 15}, timeout=60)
    print(f"\n=== risk-control-logs: {r.status_code} ===")
    data = r.json()
    logs = data if isinstance(data, list) else (data.get("logs") or data.get("events") or [])
    for e in logs[:15]:
        keys = {k: e.get(k) for k in ("created_at", "event_type", "trigger_scene", "result_code", "processing_status", "description", "detail") if e.get(k)}
        print(json.dumps(keys, ensure_ascii=False)[:400])

    r = s.get(f"{BASE}/logs", params={"lines": 400}, timeout=60)
    print(f"\n=== logs: {r.status_code} ===")
    entries = r.json().get("logs") or []
    hits = [l for l in entries if COOKIE_ID in json.dumps(l, ensure_ascii=False)]
    for l in hits[-40:]:
        msg = l.get("message", l) if isinstance(l, dict) else l
        print(str(msg)[:300])


if __name__ == "__main__":
    main()
