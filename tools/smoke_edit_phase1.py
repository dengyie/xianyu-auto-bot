"""改价烟测阶段1：登录 → 取当前在售商品 → editDetail 探真实价格字段结构。

密码从 vault ssh-credentials.md 读取，不进 argv/环境/日志。
用法: python tools/smoke_edit_phase1.py [base_url]
"""
import json
import re
import sys

import requests

VAULT = r"E:\profile\note\note\Note\infra\ssh-credentials.md"
BASE = sys.argv[1] if len(sys.argv) > 1 else "https://xianyu.mangoqwq.com"
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
    s.headers["Authorization"] = f"Bearer {token}"

    r = s.post(f"{BASE}/items/get-all-from-account", json={"cookie_id": COOKIE_ID}, timeout=60)
    print(f"get-all-from-account: {r.status_code}")
    r.raise_for_status()
    payload = r.json()
    items = payload.get("items") or payload.get("data", {}).get("items") or []
    print(f"在售商品数: {len(items)}")
    if not items:
        print(json.dumps(payload, ensure_ascii=False)[:800])
        return
    item = items[0]
    item_id = str(item.get("id") or item.get("itemId") or "")
    print(f"目标商品: {item_id}  标题: {item.get('title', '')[:40]}  价格: {item.get('price', item.get('soldPrice', '?'))}")

    r = s.get(f"{BASE}/items/{COOKIE_ID}/{item_id}/edit-detail", timeout=60)
    print(f"edit-detail: {r.status_code}")
    r.raise_for_status()
    detail = r.json()
    data = detail.get("data") or detail.get("data_original") or {}
    if not data:
        print(json.dumps(detail, ensure_ascii=False)[:1500])
        return

    # 找价格相关字段
    price_hits = {}
    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{path}.{k}" if path else k
                if re.search(r"price|amount|orig", k, re.I):
                    price_hits[p] = v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)[:200]
                walk(v, p)
        elif isinstance(node, list):
            for i, v in enumerate(node[:3]):
                walk(v, f"{path}[{i}]")
    walk(data, "")
    print("--- 价格相关字段 ---")
    print(json.dumps(price_hits, ensure_ascii=False, indent=1))
    print("--- 顶层键 ---")
    print(sorted(data.keys()))
    with open(r"C:\Users\mango\AppData\Local\Temp\xianyu-auto-bot\edit_detail_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False, indent=1)
    print("已存快照 edit_detail_snapshot.json")


if __name__ == "__main__":
    main()
