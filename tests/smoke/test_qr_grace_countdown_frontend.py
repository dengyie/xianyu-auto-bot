"""扫码稳定期倒计时前端心跳契约。

服务端每次请求都按 qr_grace_until（绝对截止时间）现算剩余秒数，但账号管理列表
只在手动操作时 loadCookies()，不会自动重拉——徽章会冻结在最后一次渲染的秒数
（"倒计时不主动下降"）。前端改为把绝对截止时间写进 DOM，本地每秒重算。
"""

from pathlib import Path


def _accounts_js() -> str:
    return Path("static/js/app-accounts.js").read_text(encoding="utf-8")


def test_badge_carries_absolute_deadline_for_local_countdown():
    js = _accounts_js()
    # 绝对截止时间由服务端 _build_qr_grace_display 提供
    assert "qr_grace_until" in js
    assert "function getQrGraceUntil" in js
    assert "data-qr-grace-until" in js
    assert "qr-grace-remaining" in js


def test_local_ticker_recomputes_every_second_without_refetch():
    js = _accounts_js()
    assert "function refreshQrGraceCountdowns" in js
    assert "function startQrGraceCountdownTicker" in js
    assert "setInterval(refreshQrGraceCountdowns, 1000)" in js
    assert "function getQrGraceRemainingSeconds" in js


def test_render_account_runtime_badge_uses_deadline():
    js = _accounts_js()
    start = js.index("function renderAccountRuntimeBadge")
    end = js.index("function buildAboutMetaCard", start)
    body = js[start:end]
    assert "getQrGraceUntil(runtimeStatus)" in body
    assert "data-qr-grace-until" in body
    assert "startQrGraceCountdownTicker()" in body


def test_about_account_options_tick_too():
    js = _accounts_js()
    start = js.index("function populateAboutAccountOptions")
    end = js.index("async function loadAboutRuntimeStatus", start)
    body = js[start:end]
    assert "data-qr-grace-until" in body
    assert "data-qr-grace-prefix" in body
