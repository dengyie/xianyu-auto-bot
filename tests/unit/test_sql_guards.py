"""db_manager SQL 安全护栏的拒绝/放行路径单元测试。

背景：safe_join_sql 与 validate_table_name 是动态 SQL 的注入护栏，
其"拒绝分支"此前零覆盖（373 项测试只走 happy path）。
"""
import os

os.environ.setdefault("DB_PATH", ":memory:")

import pytest

from db_manager.base import safe_join_sql, validate_table_name


class TestSafeJoinSql:
    @pytest.mark.parametrize("fragment", [
        "id = ?; DROP TABLE users",
        "id = ? -- comment",
        "/*block*/ id = ?",
    ])
    def test_rejects_statement_level_markers(self, fragment):
        with pytest.raises(ValueError):
            safe_join_sql([fragment])

    def test_rejects_any_part_with_marker(self):
        with pytest.raises(ValueError):
            safe_join_sql(["a = ?", "b = ?; DELETE FROM cookies"])

    def test_happy_path_set_clause(self):
        assert safe_join_sql(["a = ?", "b = ?"]) == "a = ?, b = ?"

    def test_happy_path_where_conditions(self):
        assert safe_join_sql(["cookie_id = ?", "user_id = ?"], sep=" AND ") == "cookie_id = ? AND user_id = ?"

    def test_empty_parts(self):
        assert safe_join_sql([]) == ""


class TestValidateTableName:
    @pytest.mark.parametrize("bad", [
        "users; DROP TABLE users",
        "sqlite_master",
        "unknown_table",
        "",
    ])
    def test_rejects_unknown_or_malicious(self, bad):
        with pytest.raises(ValueError):
            validate_table_name(bad)

    @pytest.mark.parametrize("ok", [
        "cookies", "orders", "keywords", "item_info", "system_settings",
    ])
    def test_accepts_whitelisted(self, ok):
        assert validate_table_name(ok) == ok
