"""Startup, health-check, and lifespan regression tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


def test_reply_server_imports_from_arbitrary_working_directory(tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["DB_PATH"] = ":memory:"
    env["PYTHONPATH"] = str(project_root)

    result = subprocess.run(
        [sys.executable, "-c", "import reply_server"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "realtime.log").exists()


def test_file_log_collector_places_new_realtime_log_under_logs(tmp_path):
    from file_log_collector import FileLogCollector

    collector = FileLogCollector(root=tmp_path)

    assert collector.log_file == str(tmp_path / "logs" / "realtime.log")


def test_file_log_collector_reuses_writable_legacy_realtime_log(tmp_path):
    from file_log_collector import FileLogCollector

    legacy_log = tmp_path / "realtime.log"
    legacy_log.write_text("legacy log\n", encoding="utf-8")

    collector = FileLogCollector(root=tmp_path)

    assert collector.log_file == str(legacy_log)


def test_file_log_collector_ignores_unwritable_legacy_realtime_log(tmp_path):
    from file_log_collector import FileLogCollector

    legacy_log = tmp_path / "realtime.log"
    legacy_log.write_text("legacy log\n", encoding="utf-8")
    legacy_log.chmod(0o444)

    collector = FileLogCollector(root=tmp_path)

    assert collector.log_file == str(tmp_path / "logs" / "realtime.log")


def test_health_check_does_not_block_for_cpu_sampling(client):
    started_at = time.perf_counter()

    response = client.get("/health")

    elapsed = time.perf_counter() - started_at
    assert response.status_code == 200
    assert elapsed < 0.2


def test_liveness_remains_available_during_database_maintenance(client):
    import reply_server

    reply_server.app.state.maintenance_mode = True
    try:
        response = client.get("/health/live")
    finally:
        reply_server.app.state.maintenance_mode = False

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_lifespan_owns_and_cancels_scheduled_task():
    import reply_server

    with TestClient(reply_server.app):
        task = reply_server.app.state.scheduled_task
        assert task.done() is False

    assert task.cancelled() is True
