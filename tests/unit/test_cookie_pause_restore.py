"""restore_cookie_from_pause 原子恢复的回归测试。

enabled（cookie_status 表）与 status_note（cookies 表）跨两张表，
恢复语义要求"启用 + 清标记"同时生效或同时不变。
"""
from db_manager import db_manager


class TestRestoreCookieFromPause:
    def test_restores_enabled_and_clears_note(self):
        db_manager.save_cookie("9800000001", "unb=t", user_id=1)
        db_manager.save_cookie_status("9800000001", False)
        db_manager.update_cookie_status_note("9800000001", "待二维码验证")

        assert db_manager.restore_cookie_from_pause("9800000001") is True
        assert db_manager.get_cookie_status("9800000001") is True
        assert db_manager.get_cookie_details("9800000001")["status_note"] == ""

    def test_enabled_account_without_note_is_noop_success(self):
        db_manager.save_cookie("9800000002", "unb=t", user_id=1)
        assert db_manager.restore_cookie_from_pause("9800000002") is True
        assert db_manager.get_cookie_status("9800000002") is True
        assert db_manager.get_cookie_details("9800000002")["status_note"] == ""
