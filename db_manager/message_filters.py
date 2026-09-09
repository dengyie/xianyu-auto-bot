"""消息过滤规则 CRUD（移植上游 #110，db_manager 拆分 Mixin）。"""
import json
from typing import Any, Dict, List, Optional

from loguru import logger


class DBMessageFiltersMixin:
    """xy_message_filter_rules 表的增删改查与上下文匹配读取。"""

    def _message_filter_row_to_dict(self, row: tuple, columns: List[str]) -> Dict[str, Any]:
        record = dict(zip(columns, row))
        for field_name in ['is_enabled', 'action_skip_auto_reply', 'action_skip_ai_reply', 'action_notify']:
            record[field_name] = bool(record.get(field_name))
        try:
            patterns = json.loads(record.get('patterns') or '[]')
        except Exception:
            patterns = []
        if not isinstance(patterns, list):
            patterns = []
        record['patterns'] = [str(pattern or '').strip() for pattern in patterns if str(pattern or '').strip()]
        record['patterns_text'] = '\n'.join(record['patterns'])
        scope = 'user'
        if self._normalize_blacklist_scope_value(record.get('item_id')):
            scope = 'item'
        elif self._normalize_blacklist_scope_value(record.get('cookie_id')):
            scope = 'account'
        record['scope'] = scope
        try:
            record['action_pause_minutes'] = max(0, int(record.get('action_pause_minutes') or 0))
        except (TypeError, ValueError):
            record['action_pause_minutes'] = 0
        return record

    def _ensure_message_filter_cookie_owner(self, cookie_id: Optional[str], user_id: int) -> bool:
        normalized_cookie_id = self._normalize_blacklist_scope_value(cookie_id)
        if not normalized_cookie_id:
            return True
        cursor = self.conn.cursor()
        self._execute_sql(cursor, "SELECT user_id FROM cookies WHERE id = ?", (normalized_cookie_id,))
        row = cursor.fetchone()
        return bool(row and int(row[0]) == int(user_id))

    def create_message_filter_rule(
        self,
        user_id: int,
        name: str,
        patterns: str,
        cookie_id: str = None,
        item_id: str = None,
        match_type: str = 'contains',
        message_source: str = 'user',
        is_enabled: bool = True,
        action_skip_auto_reply: bool = True,
        action_skip_ai_reply: bool = False,
        action_pause_minutes: int = 0,
        action_notify: bool = False,
    ) -> Dict[str, Any]:
        """创建消息过滤规则。"""
        normalized_cookie_id = self._normalize_blacklist_scope_value(cookie_id)
        normalized_item_id = self._normalize_blacklist_scope_value(item_id)
        with self.lock:
            cursor = self.conn.cursor()
            try:
                if not self._ensure_message_filter_cookie_owner(normalized_cookie_id, user_id):
                    raise ValueError('无权限操作该账号')
                self._execute_sql(cursor, """
                    INSERT INTO xy_message_filter_rules
                        (user_id, cookie_id, item_id, name, match_type, patterns, message_source,
                         is_enabled, action_skip_auto_reply, action_skip_ai_reply,
                         action_pause_minutes, action_notify, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    user_id,
                    normalized_cookie_id,
                    normalized_item_id,
                    str(name or '').strip(),
                    str(match_type or 'contains').strip(),
                    patterns,
                    str(message_source or 'user').strip(),
                    1 if is_enabled else 0,
                    1 if action_skip_auto_reply else 0,
                    1 if action_skip_ai_reply else 0,
                    max(0, int(action_pause_minutes or 0)),
                    1 if action_notify else 0,
                ))
                record_id = cursor.lastrowid
                self._execute_sql(cursor, "SELECT * FROM xy_message_filter_rules WHERE id = ? AND user_id = ?", (record_id, user_id))
                row = cursor.fetchone()
                columns = [desc[0] for desc in cursor.description]
                record = self._message_filter_row_to_dict(row, columns) if row else {}
                self.conn.commit()
                return record
            except Exception as e:
                self.conn.rollback()
                logger.error(f"创建消息过滤规则失败: {e}")
                raise

    def update_message_filter_rule(
        self,
        rule_id: int,
        user_id: int,
        name: str,
        patterns: str,
        cookie_id: str = None,
        item_id: str = None,
        match_type: str = 'contains',
        message_source: str = 'user',
        is_enabled: bool = True,
        action_skip_auto_reply: bool = True,
        action_skip_ai_reply: bool = False,
        action_pause_minutes: int = 0,
        action_notify: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """更新消息过滤规则。"""
        normalized_cookie_id = self._normalize_blacklist_scope_value(cookie_id)
        normalized_item_id = self._normalize_blacklist_scope_value(item_id)
        with self.lock:
            cursor = self.conn.cursor()
            try:
                if not self._ensure_message_filter_cookie_owner(normalized_cookie_id, user_id):
                    raise ValueError('无权限操作该账号')
                self._execute_sql(cursor, """
                    UPDATE xy_message_filter_rules
                    SET cookie_id = ?, item_id = ?, name = ?, match_type = ?, patterns = ?,
                        message_source = ?, is_enabled = ?, action_skip_auto_reply = ?,
                        action_skip_ai_reply = ?, action_pause_minutes = ?, action_notify = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND user_id = ?
                """, (
                    normalized_cookie_id,
                    normalized_item_id,
                    str(name or '').strip(),
                    str(match_type or 'contains').strip(),
                    patterns,
                    str(message_source or 'user').strip(),
                    1 if is_enabled else 0,
                    1 if action_skip_auto_reply else 0,
                    1 if action_skip_ai_reply else 0,
                    max(0, int(action_pause_minutes or 0)),
                    1 if action_notify else 0,
                    rule_id,
                    user_id,
                ))
                if cursor.rowcount <= 0:
                    self.conn.rollback()
                    return None
                self._execute_sql(cursor, "SELECT * FROM xy_message_filter_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
                row = cursor.fetchone()
                columns = [desc[0] for desc in cursor.description]
                record = self._message_filter_row_to_dict(row, columns) if row else None
                self.conn.commit()
                return record
            except Exception as e:
                self.conn.rollback()
                logger.error(f"更新消息过滤规则失败: {e}")
                raise

    def get_message_filter_rule(self, rule_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """按 ID 查询消息过滤规则。"""
        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, "SELECT * FROM xy_message_filter_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
                row = cursor.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cursor.description]
                return self._message_filter_row_to_dict(row, columns)
            except Exception as e:
                logger.error(f"查询消息过滤规则失败: {e}")
                return None

    def list_message_filter_rules(
        self,
        user_id: int,
        keyword: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页查询消息过滤规则。"""
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 20), 1), 200)
        offset = (safe_page - 1) * safe_page_size
        where_clauses = ["user_id = ?"]
        params: List[Any] = [user_id]
        normalized_keyword = self._normalize_blacklist_scope_value(keyword)
        if normalized_keyword:
            where_clauses.append("(name LIKE ? OR patterns LIKE ? OR item_id LIKE ? OR cookie_id LIKE ?)")
            like_value = f"%{normalized_keyword}%"
            params.extend([like_value, like_value, like_value, like_value])
        where_sql = " AND ".join(where_clauses)

        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, f"SELECT COUNT(*) FROM xy_message_filter_rules WHERE {where_sql}", tuple(params))
                total = int(cursor.fetchone()[0] or 0)
                self._execute_sql(cursor, f"""
                    SELECT * FROM xy_message_filter_rules
                    WHERE {where_sql}
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ? OFFSET ?
                """, tuple(params + [safe_page_size, offset]))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return {
                    'data': [self._message_filter_row_to_dict(row, columns) for row in rows],
                    'total': total,
                    'page': safe_page,
                    'page_size': safe_page_size,
                }
            except Exception as e:
                logger.error(f"查询消息过滤规则列表失败: {e}")
                return {'data': [], 'total': 0, 'page': safe_page, 'page_size': safe_page_size}

    def delete_message_filter_rule(self, rule_id: int, user_id: int) -> bool:
        """删除消息过滤规则。"""
        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, "DELETE FROM xy_message_filter_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
                deleted = cursor.rowcount > 0
                self.conn.commit()
                return deleted
            except Exception as e:
                self.conn.rollback()
                logger.error(f"删除消息过滤规则失败: {e}")
                return False

    def toggle_message_filter_rule(self, rule_id: int, user_id: int, is_enabled: bool) -> bool:
        """启用或禁用消息过滤规则。"""
        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, """
                    UPDATE xy_message_filter_rules
                    SET is_enabled = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND user_id = ?
                """, (1 if is_enabled else 0, rule_id, user_id))
                updated = cursor.rowcount > 0
                self.conn.commit()
                return updated
            except Exception as e:
                self.conn.rollback()
                logger.error(f"更新消息过滤规则状态失败: {e}")
                return False

    def get_message_filter_rules_for_context(
        self,
        user_id: int,
        cookie_id: str = None,
        item_id: str = None,
    ) -> List[Dict[str, Any]]:
        """按用户/账号/商品上下文读取启用的过滤规则。"""
        normalized_cookie_id = self._normalize_blacklist_scope_value(cookie_id) or ''
        normalized_item_id = self._normalize_blacklist_scope_value(item_id) or ''
        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, """
                    SELECT * FROM xy_message_filter_rules
                    WHERE user_id = ?
                      AND is_enabled = 1
                      AND (
                        (COALESCE(cookie_id, '') = '' AND COALESCE(item_id, '') = '')
                        OR (? != '' AND COALESCE(cookie_id, '') = ? AND COALESCE(item_id, '') = '')
                        OR (? != '' AND COALESCE(item_id, '') = ? AND (COALESCE(cookie_id, '') = '' OR COALESCE(cookie_id, '') = ?))
                      )
                    ORDER BY
                      CASE
                        WHEN COALESCE(item_id, '') != '' THEN 3
                        WHEN COALESCE(cookie_id, '') != '' THEN 2
                        ELSE 1
                      END DESC,
                      updated_at DESC,
                      id DESC
                """, (
                    user_id,
                    normalized_cookie_id,
                    normalized_cookie_id,
                    normalized_item_id,
                    normalized_item_id,
                    normalized_cookie_id,
                ))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [self._message_filter_row_to_dict(row, columns) for row in rows]
            except Exception as e:
                logger.error(f"查询上下文消息过滤规则失败: {e}")
                return []
