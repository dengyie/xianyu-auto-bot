#!/usr/bin/env python3
"""xianyu-auto-bot 每日自动备份（腾讯生产机 tencent-lh）

由 systemd timer (xianyu-backup.timer) 每日 04:30 触发：
- 用 sqlite3 backup API 在线快照 data/xianyu_data.db（一致性，不锁业务）
- quick_check 校验快照后 gzip 落 backups/auto/
- 同时备份 .env / global_config.yml（.env 保持 0600）
- 保留 14 天，超期自动删除
- 失败非零退出（journalctl -u xianyu-backup 可查）
"""
import gzip
import os
import shutil
import sqlite3
import sys
import time

BASE = "/opt/xianyu-auto-bot"
SRC_DB = f"{BASE}/data/xianyu_data.db"
DEST_DIR = f"{BASE}/backups/auto"
KEEP_DAYS = 14
LOG = f"{DEST_DIR}/backup.log"

os.makedirs(DEST_DIR, exist_ok=True)


def log(msg: str) -> None:
    # 简单自轮转：超 5MB 直接截断重开
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 5 * 1024 * 1024:
            os.truncate(LOG, 0)
    except OSError:
        pass
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def main() -> int:
    ts = time.strftime("%Y%m%d_%H%M%S")
    tmp = f"{DEST_DIR}/.snap_{ts}.db"
    final = f"{DEST_DIR}/xianyu_data_{ts}.db.gz"
    try:
        # 在线一致性快照
        src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        src.close()
        # 校验后再落盘
        check = sqlite3.connect(tmp).execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            log(f"FAIL quick_check={check}")
            return 1
        with open(tmp, "rb") as fin, gzip.open(final, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
        os.remove(tmp)
        # 附属配置（.env 含密钥，必须 0600）
        env_dst = f"{DEST_DIR}/env_{ts}.bak"
        shutil.copy2(f"{BASE}/.env", env_dst)
        os.chmod(env_dst, 0o600)
        shutil.copy2(f"{BASE}/global_config.yml", f"{DEST_DIR}/global_config_{ts}.yml")
        # 对齐挂载卷属主
        for p in (final, env_dst, f"{DEST_DIR}/global_config_{ts}.yml"):
            try:
                os.chown(p, 1000, 1000)
            except PermissionError:
                pass
        # 保留期清理
        cutoff = time.time() - KEEP_DAYS * 86400
        removed = 0
        for name in os.listdir(DEST_DIR):
            fp = os.path.join(DEST_DIR, name)
            if os.path.isfile(fp) and not name.startswith(".") \
                    and name != "backup.log" and os.path.getmtime(fp) < cutoff:
                os.remove(fp)
                removed += 1
        log(f"OK {os.path.basename(final)} size={os.path.getsize(final)//1024//1024}MB removed={removed}")
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"FAIL {type(exc).__name__}: {exc}")
        for p in (tmp,):
            if os.path.exists(p):
                os.remove(p)
        return 1


if __name__ == "__main__":
    sys.exit(main())
