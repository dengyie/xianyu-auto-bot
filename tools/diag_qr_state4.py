"""诊断第四阶段：无过滤拉最新风控日志 + 找二维码验证事件时间戳。"""
import json
import re

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

    r = s.get(f"{BASE}/risk-control-logs", params={"limit": 12}, timeout=60)
    data = r.json()
    logs = data if isinstance(data, list) else (data.get("logs") or data.get("events") or [])
    print(f"=== 最新风控日志 {len(logs)} 条 ===")
    for e in logs[:12]:
        slim = {k: e.get(k) for k in ("id", "created_at", "cookie_id", "event_type", "trigger_scene", "result_code", "processing_status", "processing_result", "description") if e.get(k) is not None}
        print(json.dumps(slim, ensure_ascii=False)[:450])

    # 二维码相关事件（近 3 天）
    r = s.get(f"{BASE}/risk-control-logs", params={"limit": 20, "event_type": "qr_verify"}, timeout=60)
    data = r.json()
    logs = data if isinstance(data, list) else (data.get("logs") or [])
    print(f"\n=== qr_verify 事件 {len(logs)} 条 ===")
    for e in logs[:20]:
        slim = {k: e.get(k) for k in ("id", "created_at", "cookie_id", "trigger_scene", "processing_result") if e.get(k) is not None}
        print(json.dumps(slim, ensure_ascii=False)[:350])


if __name__ == "__main__":
    main()
