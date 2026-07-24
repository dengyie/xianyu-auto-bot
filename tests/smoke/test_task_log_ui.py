"""Task log center frontend contract (modular static assets)."""

from pathlib import Path


def test_task_log_ui_markup_and_scripts():
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert 'id="logs-section"' in html
    assert 'id="systemLogsPane"' in html
    assert 'id="taskLogsPane"' in html
    assert 'id="taskLogsTab"' in html
    assert 'id="taskLogTypeFilter"' in html
    assert 'id="taskLogsTableBody"' in html
    assert "auto_red_flower" not in html  # local backend未接入求小红花

    js = Path("static/js/app-logs.js").read_text(encoding="utf-8")
    assert "function switchLogCenterTab" in js
    assert "function loadTaskLogs" in js
    assert "function renderTaskLogs" in js
    assert "/api/task-logs" in js
    assert "taskLogCookieOptionsLoaded" in js

    core = Path("static/js/app-core.js").read_text(encoding="utf-8")
    assert "switchLogCenterTab" in core

    css = Path("static/css/logs.css").read_text(encoding="utf-8")
    assert ".log-center-tabs" in css
    assert ".task-log-stat-card" in css
    assert ".task-type-badge" in css
