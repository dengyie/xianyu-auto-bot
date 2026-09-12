"""默认有头浏览器（show_browser）回归测试。

2026-09 生产实证：无头指纹是密码链路滑块被阿里硬拒（2KZwAc）的风险信号之一，
全系统默认改为有头（服务器容器内置 Xvfb 虚拟显示）。
钉住的契约：未显式配置（NULL/缺省）一律视为有头；显式 0/false 才是无头。
"""
from app.api.models import ManualCookieImportRequest
from db_manager import db_manager


class TestShowBrowserDefaults:
    def test_manual_cookie_import_request_defaults_headed(self):
        assert ManualCookieImportRequest(account_id="a", cookie="unb=x").show_browser is True

    def test_get_cookie_details_null_show_browser_means_headed(self):
        """历史账号该字段为 NULL 时，读取默认应为有头。"""
        db_manager.save_cookie("9900000001", "unb=t", user_id=1)
        details = db_manager.get_cookie_details("9900000001")
        assert details["show_browser"] is True

    def test_explicit_headless_respected(self):
        """显式关闭有头的账号保持无头。"""
        db_manager.save_cookie("9900000002", "unb=t", user_id=1)
        assert db_manager.update_cookie_account_info("9900000002", show_browser=False) is True
        assert db_manager.get_cookie_details("9900000002")["show_browser"] is False

    def test_explicit_headed_persists(self):
        db_manager.save_cookie("9900000003", "unb=t", user_id=1)
        assert db_manager.update_cookie_account_info("9900000003", show_browser=True) is True
        assert db_manager.get_cookie_details("9900000003")["show_browser"] is True
