"""Logging hygiene: probe silencing, level filter, memory sink, DB log retention."""
import glob
import time

from fastapi.testclient import TestClient
from loguru import logger

import reply_server


def _auth_headers(user_id: int, username: str = "log_user", is_admin: bool = True) -> dict:
    token = f"log-hygiene-token-{user_id}-{username}"
    reply_server.SESSION_TOKENS[token] = {
        "user_id": user_id,
        "username": username,
        "is_admin": is_admin,
        "timestamp": time.time(),
    }
    return {"Authorization": f"Bearer {token}"}


def _capture_loguru_records():
    """loguru 的 Message 是 str 子类，取 .record 才能拿到结构化字段。"""
    records = []
    sink_id = logger.add(lambda msg: records.append(msg.record), level="DEBUG")
    return records, sink_id


def test_health_probe_requests_are_not_logged(client):
    records, sink_id = _capture_loguru_records()
    try:
        assert client.get("/health").status_code == 200
    finally:
        logger.remove(sink_id)

    middleware_messages = [r["message"] for r in records if "GET /health -" in r["message"]]
    assert middleware_messages == [], f"health probes must stay silent, got: {middleware_messages}"


def test_failed_requests_log_at_warning(client):
    records, sink_id = _capture_loguru_records()
    try:
        resp = client.get("/definitely-not-a-real-path-xyz")
    finally:
        logger.remove(sink_id)

    assert resp.status_code == 404
    warnings = [r for r in records if r["level"].name == "WARNING" and "GET /definitely-not-a-real-path-xyz" in r["message"]]
    assert warnings, "failed requests must surface as WARNING"


def test_admin_logs_level_filter_matches_padded_level_format(tmp_path, client, monkeypatch):
    """按日 sink 的 level 左对齐 8 字符（"| ERROR    |"），过滤必须兼容，不能只匹配 "| ERROR |"。"""
    log_file = tmp_path / "xianyu_2026-09-13.log"
    log_file.write_text(
        "2026-09-13 04:00:00.000 | INFO     | mod:fn:1 - info line\n"
        "2026-09-13 04:00:01.000 | ERROR    | mod:fn:2 - error line\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(glob, "glob", lambda pattern: [str(log_file)])
    monkeypatch.chdir(tmp_path)

    headers = _auth_headers(user_id=1)
    resp = client.get("/admin/logs", params={"lines": 100, "level": "ERROR"}, headers=headers)
    body = resp.json()

    assert body["success"] is True
    assert len(body["logs"]) == 1
    assert "error line" in body["logs"][0]
    assert "info line" not in body["logs"][0]


def test_admin_logs_reads_tail_without_loading_whole_file(tmp_path, client, monkeypatch):
    log_file = tmp_path / "xianyu_2026-09-13.log"
    log_file.write_text(
        "\n".join(f"2026-09-13 04:00:{s:02d}.000 | INFO     | mod:fn:1 - line {s}" for s in range(60)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(glob, "glob", lambda pattern: [str(log_file)])
    monkeypatch.chdir(tmp_path)

    resp = client.get("/admin/logs", params={"lines": 5}, headers=_auth_headers(user_id=1))
    body = resp.json()

    assert body["success"] is True
    assert len(body["logs"]) == 5
    assert "line 59" in body["logs"][-1]
    assert "line 55" in body["logs"][0]


def test_file_log_collector_memory_sink_captures_structured_entries(tmp_path):
    from file_log_collector import FileLogCollector

    collector = FileLogCollector(root=tmp_path)
    logger.info("collector sink probe message")

    entries = collector.get_logs(50)
    assert any("collector sink probe message" in e["message"] for e in entries)

    entry = next(e for e in entries if "collector sink probe message" in e["message"])
    assert entry["level"] == "INFO"
    assert entry["source"]
    assert entry["timestamp"]

    stats = collector.get_stats()
    assert stats["total_logs"] >= 1
    assert stats["level_counts"].get("INFO", 0) >= 1


def test_read_log_tail_clamps_non_positive_line_counts(tmp_path):
    """max_lines=0 会触发 lst[-0:] 返回全量的切片陷阱，helper 必须自钳制。"""
    import reply_server  # 先于 adminops 导入，避免子模块先行的循环导入
    from app.api.routers.adminops import _read_log_tail

    log_file = tmp_path / "t.log"
    log_file.write_text("a\nb\nc\n", encoding="utf-8")

    assert _read_log_tail(str(log_file), 0) == ["c"]
    assert _read_log_tail(str(log_file), -3) == ["c"]
    assert _read_log_tail(str(log_file), 10) == ["a", "b", "c"]


def test_cleanup_old_data_removes_stale_task_and_delivery_logs(_db):
    db = reply_server.db_manager

    with db.lock:
        cur = db.conn.cursor()
        cur.execute(
            """
            INSERT INTO scheduled_rate_logs (batch_id, cookie_id, status, message, created_at)
            VALUES ('b-old', 'c-1', 'success', 'old', datetime('now', '-40 days'))
            """
        )
        cur.execute(
            """
            INSERT INTO scheduled_rate_logs (batch_id, cookie_id, status, message, created_at)
            VALUES ('b-new', 'c-1', 'success', 'new', datetime('now'))
            """
        )
        cur.execute(
            """
            INSERT INTO scheduled_task_logs (batch_id, task_type, cookie_id, status, message, created_at)
            VALUES ('t-old', 'item_polish', 'c-1', 'success', 'old', datetime('now', '-40 days'))
            """
        )
        cur.execute(
            """
            INSERT INTO delivery_logs (cookie_id, order_id, status, reason, created_at)
            VALUES ('c-1', 'o-1', 'success', 'old', datetime('now', '-120 days'))
            """
        )
        db.conn.commit()

    stats = db.cleanup_old_data(days=90)

    assert stats["scheduled_rate_logs"] >= 1
    assert stats["scheduled_task_logs"] >= 1
    assert stats["delivery_logs"] >= 1
    # 小清理不应触发 VACUUM 全库重建（freelist 门控）
    assert stats.get("vacuum_executed") is False

    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM scheduled_rate_logs")
        assert cur.fetchone()[0] == 1  # 新记录保留
        cur.execute("SELECT COUNT(*) FROM scheduled_task_logs")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM delivery_logs")
        assert cur.fetchone()[0] == 0
