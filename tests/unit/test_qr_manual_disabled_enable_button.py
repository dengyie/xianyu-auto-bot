"""手动禁用账号扫码成功后，弹窗里的「启用账号并启动任务」按钮必须留得住。

回归背景（2026-09-19 线上）：账号 `1926782908` 手动停用后扫码，真实 Cookie
已落库、任务按设计保持停止，前端也渲染了一键启用按钮——但 `handleQRCodeSuccess`
末尾无条件 `closeQRCodeLoginModal(3000)`，3 秒后模态框连同按钮一起被销毁，
用户根本没机会点，账号一直停在 enabled=0。

所以手动停用分支必须跳过自动关窗；关窗时机交给用户点「启用」成功后
（`enableAccountFromQRLogin` 里的 `closeQRCodeLoginModal(1500)`）或用户自己关。
"""
from pathlib import Path

import pytest

APP_AUTH_JS = Path("static/js/app-auth.js")


@pytest.fixture(scope="module")
def auth_js():
    return APP_AUTH_JS.read_text(encoding="utf-8")


def test_manual_disabled_branch_skips_auto_close(auth_js):
    """手动停用分支的 3 秒自动关窗必须被 `manual_disabled_skip_restart` 守卫。"""
    assert (
        "if (manual_disabled_skip_restart !== true) {\n"
        "        closeQRCodeLoginModal(3000);"
    ) in auth_js, "手动停用分支缺少关窗守卫：按钮会被 3 秒后的自动关窗销毁"


def test_enable_button_renderer_targets_status_box(auth_js):
    """按钮渲染进 #qrCodeStatus，且清掉旧按钮避免重复堆叠。"""
    start = auth_js.index("function renderManualDisabledEnableAction(")
    body = auth_js[start:start + 1200]

    assert "getElementById('qrCodeStatus')" in body
    assert "clearQRManualDisabledAction()" in body
    assert "启用账号并启动任务" in body
    assert "enableAccountFromQRLogin(accountId, button)" in body


def test_enable_button_closes_modal_on_success(auth_js):
    """成功启用后要关窗（1500ms）并清掉按钮，失败则恢复按钮可再点。"""
    start = auth_js.index("async function enableAccountFromQRLogin(")
    body = auth_js[start:start + 1600]

    assert "closeQRCodeLoginModal(1500);" in body
    assert "clearQRManualDisabledAction();" in body
    # 失败路径：按钮必须恢复可用，否则用户被卡死在这一步
    assert "button.disabled = false;" in body
    assert "button.innerHTML = originalHtml;" in body


def test_enable_call_clears_manual_disabled_action_on_reset(auth_js):
    """重开/重置扫码状态时要清掉旧按钮，避免跨账号残留。"""
    start = auth_js.index("function resetQRCodeVerificationState(")
    body = auth_js[start:start + 900]

    assert "clearQRManualDisabledAction()" in body