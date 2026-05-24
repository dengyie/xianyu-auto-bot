#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NoCaptcha ???? Demo
??: ??Cookie -> ????URL -> SliderSolver?? -> ?????
"""

import asyncio, os, sys, sqlite3, argparse, time
from pathlib import Path
from datetime import datetime
from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def decrypt_cookie(cookie_id: str) -> str:
    """???????????Cookie"""
    key_path = PROJECT_ROOT / "data" / ".secret_encryption.key"
    with open(key_path, "rb") as f:
        key = f.read().strip()
    fernet = Fernet(key)

    db_path = PROJECT_ROOT / "data" / "xianyu_data.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT id, value FROM cookies WHERE id = ?", (cookie_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise ValueError(f"Cookie {cookie_id} not found in DB")

    encrypted = row[1]
    if not encrypted or not encrypted.startswith("enc$"):
        return encrypted or ""

    token = encrypted[4:]
    return fernet.decrypt(token.encode()).decode()


def get_verification_url(cookie_text: str) -> str:
    """????????????URL"""
    from utils.xianyu_slider_stealth import resolve_verification_url_from_cookie

    proxy = {
        "proxy_type": os.environ.get("PROXY_TYPE", "none"),
        "proxy_host": os.environ.get("PROXY_HOST", ""),
        "proxy_port": int(os.environ.get("PROXY_PORT", "0") or "0"),
    }

    print(f"  [pre] probing verification...")
    url = resolve_verification_url_from_cookie(cookie_text, proxy=proxy)
    print(f"  [pre] verification_url = {url}")
    return url


async def run_single_round(cookie_id: str, cookies_str: str, round_num: int, headless: bool) -> dict:
    """????????"""
    from utils.slider_solver import SliderSolver

    proxy = {
        "proxy_type": os.environ.get("PROXY_TYPE", "none"),
        "proxy_host": os.environ.get("PROXY_HOST", ""),
        "proxy_port": int(os.environ.get("PROXY_PORT", "0") or "0"),
    }

    # ????URL
    try:
        verify_url = get_verification_url(cookies_str)
    except RuntimeError as e:
        msg = str(e)
        if "cookie_valid" in msg.lower() or "cookie??" in msg or "????" in msg:
            return {"round": round_num, "status": "cookie_valid", "error": msg[:100]}
        raise

    # ??????
    solver = SliderSolver(
        cookie_id=cookie_id,
        cookies_str=cookies_str,
        headless=headless,
        proxy=proxy,
    )

    print(f"  [{round_num}] solving slider...")
    start = time.time()
    success, cookies = await solver.solve(verify_url)
    elapsed = time.time() - start

    return {
        "round": round_num,
        "status": "pass" if success else "fail",
        "elapsed": round(elapsed, 1),
        "cookies_count": len(cookies) if cookies else 0,
        "verify_url": verify_url,
    }


async def main():
    parser = argparse.ArgumentParser(description="NoCaptcha Slider Demo")
    parser.add_argument("--cookie-id", default="1926782908", help="Cookie ID from DB")
    parser.add_argument("--rounds", type=int, default=1, help="Test rounds (1-30)")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    args = parser.parse_args()

    rounds = max(1, min(args.rounds, 30))
    print(f"=== NoCaptcha Slider Demo ===")
    print(f"  cookie_id: {args.cookie_id}")
    print(f"  rounds: {rounds}")
    print(f"  headless: {args.headless}")
    print()

    # ??Cookie
    print("[0] decrypting cookie...")
    cookies_str = decrypt_cookie(args.cookie_id)
    print(f"  cookie decrypted: {len(cookies_str)} chars")
    print()

    # ????
    results = []
    for r in range(1, rounds + 1):
        print(f"--- Round {r}/{rounds} ---")
        try:
            result = await run_single_round(args.cookie_id, cookies_str, r, args.headless)
            results.append(result)
        except Exception as e:
            print(f"  [{r}] ERROR: {e}")
            results.append({"round": r, "status": "error", "error": str(e)[:200]})

        status = results[-1]["status"]
        elapsed = results[-1].get("elapsed", 0)
        print(f"  [{r}] result: {status} ({elapsed}s)")
        print()

        # ????
        if r < rounds:
            wait = 3
            print(f"  cooling {wait}s...")
            await asyncio.sleep(wait)

    # ??
    total = len(results)
    passes = sum(1 for r in results if r["status"] == "pass")
    fails = sum(1 for r in results if r["status"] == "fail")
    cookies_valid = sum(1 for r in results if r["status"] == "cookie_valid")
    errors = sum(1 for r in results if r["status"] == "error")

    print("=" * 50)
    print(f"SUMMARY: {passes}/{total} passed ({passes/total*100:.1f}%)" if total > 0 else "SUMMARY: no results")
    print(f"  pass: {passes}, fail: {fails}, cookie_valid: {cookies_valid}, error: {errors}")
    if passes + fails > 0:
        actual = passes + fails
        print(f"  effective pass rate: {passes}/{actual} = {passes/actual*100:.1f}%")
    print("=" * 50)

    # ????
    log_dir = PROJECT_ROOT / "logs" / "slider_demo"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"demo_{args.cookie_id}_{ts}.txt"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"cookie_id: {args.cookie_id}\n")
        f.write(f"rounds: {rounds}\n")
        f.write(f"headless: {args.headless}\n")
        f.write(f"time: {ts}\n\n")
        for r in results:
            f.write(f"round={r['round']} status={r['status']} error={r.get('error','')} elapsed={r.get('elapsed','')}\n")
        f.write(f"\nSUMMARY: {passes}/{total} passed\n")
    print(f"log saved: {log_path}")


if __name__ == "__main__":
    asyncio.run(main())
