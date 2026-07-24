"""Personal / platform blacklist persistence helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from loguru import logger


class DBBlacklistMixin:
    def _normalize_blacklist_scope_value(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _normalize_blacklist_buyer_ids(self, buyer_ids: Any) -> List[str]:
        if buyer_ids is None:
            return []
        if isinstance(buyer_ids, str):
            raw_values = re.split(r'[\n,，]+', buyer_ids)
        else:
            raw_values = list(buyer_ids)

        normalized = []
        seen = set()
        for raw_value in raw_values:
            buyer_id = str(raw_value or '').strip()
            if not buyer_id or buyer_id in seen:
                continue
            normalized.append(buyer_id)
            seen.add(buyer_id)
        return normalized

    def _personal_blacklist_row_to_dict(self, row: tuple, columns: List[str]) -> Dict[str, Any]:
        record = dict(zip(columns, row))
        record['is_enabled'] = bool(record.get('is_enabled'))
        scope = 'user'
        if self._normalize_blacklist_scope_value(record.get('item_id')):
            scope = 'item'
        elif self._normalize_blacklist_scope_value(record.get('cookie_id')):
            scope = 'account'
        record['scope'] = scope
        return record

    def create_personal_blacklist(
        self,
        user_id: int,
        buyer_ids: List[str],
        cookie_id: str = None,
        item_id: str = None,
        reason: str = "",
        is_enabled: bool = True,
        buyer_nick: str = "",
    ) -> Dict[str, Any]:
        """创建个人黑名单记录，重复 scope 会跳过。"""
        normalized_buyer_ids = self._normalize_blacklist_buyer_ids(buyer_ids)
        normalized_cookie_id = self._normalize_blacklist_scope_value(cookie_id)
        normalized_item_id = self._normalize_blacklist_scope_value(item_id)
        normalized_reason = str(reason or '').strip()
        normalized_buyer_nick = str(buyer_nick or '').strip()

        result = {
            'created': 0,
            'skipped': 0,
            'records': [],
            'skipped_buyer_ids': [],
        }
        if not user_id or not normalized_buyer_ids:
            return result

        with self.lock:
            cursor = self.conn.cursor()
            try:
                if normalized_cookie_id:
                    self._execute_sql(cursor, "SELECT user_id FROM cookies WHERE id = ?", (normalized_cookie_id,))
                    cookie_owner = cursor.fetchone()
                    if not cookie_owner or int(cookie_owner[0]) != int(user_id):
                        result['skipped'] = len(normalized_buyer_ids)
                        result['skipped_buyer_ids'] = normalized_buyer_ids
                        return result

                for buyer_id in normalized_buyer_ids:
                    self._execute_sql(cursor, """
                        SELECT id FROM xy_personal_blacklist
                        WHERE user_id = ?
                          AND buyer_id = ?
                          AND COALESCE(cookie_id, '') = ?
                          AND COALESCE(item_id, '') = ?
                        LIMIT 1
                    """, (
                        user_id,
                        buyer_id,
                        normalized_cookie_id or '',
                        normalized_item_id or '',
                    ))
                    if cursor.fetchone():
                        result['skipped'] += 1
                        result['skipped_buyer_ids'].append(buyer_id)
                        continue

                    self._execute_sql(cursor, """
                        INSERT INTO xy_personal_blacklist
                            (user_id, cookie_id, buyer_id, buyer_nick, item_id, reason, is_enabled, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        user_id,
                        normalized_cookie_id,
                        buyer_id,
                        normalized_buyer_nick,
                        normalized_item_id,
                        normalized_reason,
                        1 if is_enabled else 0,
                    ))
                    record_id = cursor.lastrowid
                    self._execute_sql(cursor, """
                        SELECT * FROM xy_personal_blacklist WHERE id = ?
                    """, (record_id,))
                    columns = [desc[0] for desc in cursor.description]
                    row = cursor.fetchone()
                    if row:
                        result['records'].append(self._personal_blacklist_row_to_dict(row, columns))
                    result['created'] += 1

                self.conn.commit()
                return result
            except Exception as e:
                self.conn.rollback()
                logger.error(f"创建个人黑名单失败: {e}")
                return result

    def list_personal_blacklist(
        self,
        user_id: int,
        buyer_id: str = None,
        buyer_nick: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页查询个人黑名单。"""
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 20), 1), 200)
        offset = (safe_page - 1) * safe_page_size
        where_clauses = ["user_id = ?"]
        params: List[Any] = [user_id]

        normalized_buyer_id = self._normalize_blacklist_scope_value(buyer_id)
        if normalized_buyer_id:
            where_clauses.append("buyer_id LIKE ?")
            params.append(f"%{normalized_buyer_id}%")

        normalized_buyer_nick = self._normalize_blacklist_scope_value(buyer_nick)
        if normalized_buyer_nick:
            where_clauses.append("buyer_nick LIKE ?")
            params.append(f"%{normalized_buyer_nick}%")

        where_sql = " AND ".join(where_clauses)

        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, f"SELECT COUNT(*) FROM xy_personal_blacklist WHERE {where_sql}", tuple(params))
                total = int(cursor.fetchone()[0] or 0)

                self._execute_sql(cursor, f"""
                    SELECT * FROM xy_personal_blacklist
                    WHERE {where_sql}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                """, tuple(params + [safe_page_size, offset]))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                data = [self._personal_blacklist_row_to_dict(row, columns) for row in rows]
                return {
                    'data': data,
                    'total': total,
                    'page': safe_page,
                    'page_size': safe_page_size,
                }
            except Exception as e:
                logger.error(f"查询个人黑名单失败: {e}")
                return {'data': [], 'total': 0, 'page': safe_page, 'page_size': safe_page_size}

    def delete_personal_blacklist(self, record_id: int, user_id: int) -> bool:
        """删除单条个人黑名单。"""
        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, "DELETE FROM xy_personal_blacklist WHERE id = ? AND user_id = ?", (record_id, user_id))
                deleted = cursor.rowcount > 0
                self.conn.commit()
                return deleted
            except Exception as e:
                self.conn.rollback()
                logger.error(f"删除个人黑名单失败: {e}")
                return False

    def batch_delete_personal_blacklist(self, ids: List[int], user_id: int) -> int:
        """批量删除个人黑名单，返回删除数量。"""
        safe_ids = []
        for raw_id in ids or []:
            try:
                record_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if record_id > 0 and record_id not in safe_ids:
                safe_ids.append(record_id)

        if not safe_ids:
            return 0

        placeholders = ','.join(['?'] * len(safe_ids))
        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(
                    cursor,
                    f"DELETE FROM xy_personal_blacklist WHERE user_id = ? AND id IN ({placeholders})",
                    tuple([user_id] + safe_ids),
                )
                deleted = cursor.rowcount
                self.conn.commit()
                return deleted
            except Exception as e:
                self.conn.rollback()
                logger.error(f"批量删除个人黑名单失败: {e}")
                return 0

    def toggle_personal_blacklist(self, record_id: int, user_id: int, is_enabled: bool) -> bool:
        """启用或禁用个人黑名单。"""
        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, """
                    UPDATE xy_personal_blacklist
                    SET is_enabled = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND user_id = ?
                """, (1 if is_enabled else 0, record_id, user_id))
                updated = cursor.rowcount > 0
                self.conn.commit()
                return updated
            except Exception as e:
                self.conn.rollback()
                logger.error(f"更新个人黑名单状态失败: {e}")
                return False

    def is_buyer_blacklisted(
        self,
        user_id: int,
        buyer_id: str,
        cookie_id: str = None,
        item_id: str = None,
    ) -> Optional[Dict[str, Any]]:
        """按商品级 > 账号级 > 用户级匹配个人黑名单。"""
        normalized_buyer_id = self._normalize_blacklist_scope_value(buyer_id)
        if not user_id or not normalized_buyer_id:
            return None

        normalized_cookie_id = self._normalize_blacklist_scope_value(cookie_id)
        normalized_item_id = self._normalize_blacklist_scope_value(item_id)

        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, """
                    SELECT * FROM xy_personal_blacklist
                    WHERE user_id = ?
                      AND buyer_id = ?
                      AND is_enabled = 1
                      AND (
                        (COALESCE(cookie_id, '') = '' AND COALESCE(item_id, '') = '')
                        OR (COALESCE(cookie_id, '') = ? AND COALESCE(item_id, '') = '')
                        OR (COALESCE(item_id, '') = ? AND (COALESCE(cookie_id, '') = '' OR COALESCE(cookie_id, '') = ?))
                      )
                    ORDER BY
                      CASE
                        WHEN COALESCE(item_id, '') != '' THEN 3
                        WHEN COALESCE(cookie_id, '') != '' THEN 2
                        ELSE 1
                      END DESC,
                      updated_at DESC,
                      id DESC
                    LIMIT 1
                """, (
                    user_id,
                    normalized_buyer_id,
                    normalized_cookie_id or '',
                    normalized_item_id or '',
                    normalized_cookie_id or '',
                ))
                row = cursor.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cursor.description]
                return self._personal_blacklist_row_to_dict(row, columns)
            except Exception as e:
                logger.error(f"匹配个人黑名单失败: {e}")
                return None

    def list_platform_blacklist(self, user_id: int, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """分页查询平台黑名单（当前仅预留展示）。"""
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 20), 1), 200)
        offset = (safe_page - 1) * safe_page_size

        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(cursor, "SELECT COUNT(*) FROM xy_platform_blacklist WHERE user_id = ?", (user_id,))
                total = int(cursor.fetchone()[0] or 0)
                self._execute_sql(cursor, """
                    SELECT id, user_id, buyer_id, buyer_nick, created_at, updated_at
                    FROM xy_platform_blacklist
                    WHERE user_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                """, (user_id, safe_page_size, offset))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return {
                    'data': [dict(zip(columns, row)) for row in rows],
                    'total': total,
                    'page': safe_page,
                    'page_size': safe_page_size,
                }
            except Exception as e:
                logger.error(f"查询平台黑名单失败: {e}")
                return {'data': [], 'total': 0, 'page': safe_page, 'page_size': safe_page_size}
