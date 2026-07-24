"""Product materials and publish logs."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from loguru import logger


class DBProductPublishMixin:
    def _ensure_product_publish_tables(self, cursor):
        """创建商品发布素材和发布日志表。"""
        self._execute_sql(cursor, '''
        CREATE TABLE IF NOT EXISTS product_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            price REAL,
            original_price REAL,
            category TEXT,
            images TEXT,
            delivery_method TEXT DEFAULT '包邮',
            postage REAL DEFAULT 0,
            can_self_pickup INTEGER DEFAULT 0,
            brand TEXT,
            condition TEXT DEFAULT '全新',
            remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        ''')
        self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_product_materials_user_time ON product_materials(user_id, created_at DESC)")

        self._execute_sql(cursor, '''
        CREATE TABLE IF NOT EXISTS publish_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            account_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            price TEXT,
            material_id INTEGER,
            batch_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            item_url TEXT,
            item_id TEXT,
            error_message TEXT,
            sync_status TEXT,
            sync_message TEXT,
            sync_total_count INTEGER DEFAULT 0,
            sync_saved_count INTEGER DEFAULT 0,
            raw_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (account_id) REFERENCES cookies(id) ON DELETE CASCADE,
            FOREIGN KEY (material_id) REFERENCES product_materials(id) ON DELETE SET NULL
        )
        ''')
        self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_publish_logs_user_time ON publish_logs(user_id, created_at DESC)")
        self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_publish_logs_batch ON publish_logs(batch_id)")
        self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_publish_logs_account_status ON publish_logs(account_id, status, created_at DESC)")

    # ==================== 商品发布素材与日志 ====================

    @staticmethod
    def _json_dumps_safe(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

    @staticmethod
    def _json_loads_safe(value: Any, default: Any = None) -> Any:
        if value in (None, ''):
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return default

    def add_product_material(self, user_id: int, data: Dict[str, Any]) -> Optional[int]:
        """新增商品发布素材。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                images_text = self._json_dumps_safe(data.get('images') or [])
                cursor.execute('''
                    INSERT INTO product_materials (
                        user_id, title, description, price, original_price, category, images,
                        delivery_method, postage, can_self_pickup, brand, condition, remark
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id,
                    str(data.get('title') or '').strip(),
                    str(data.get('description') or '').strip(),
                    data.get('price'),
                    data.get('original_price'),
                    data.get('category'),
                    images_text,
                    data.get('delivery_method') or '包邮',
                    data.get('postage') or 0,
                    1 if data.get('can_self_pickup') else 0,
                    data.get('brand'),
                    data.get('condition') or '全新',
                    data.get('remark'),
                ))
                material_id = cursor.lastrowid
                self.conn.commit()
                return material_id
            except Exception as e:
                logger.error(f"新增商品发布素材失败: {e}")
                self.conn.rollback()
                return None

    def _row_to_product_material(self, row) -> Dict[str, Any]:
        return {
            'id': row[0],
            'user_id': row[1],
            'title': row[2],
            'description': row[3],
            'price': row[4],
            'original_price': row[5],
            'category': row[6],
            'images': self._json_loads_safe(row[7], []),
            'delivery_method': row[8],
            'postage': row[9],
            'can_self_pickup': bool(row[10]),
            'brand': row[11],
            'condition': row[12],
            'remark': row[13],
            'created_at': row[14],
            'updated_at': row[15],
        }

    def get_product_material(self, material_id: int, user_id: int = None) -> Optional[Dict[str, Any]]:
        """获取单条商品发布素材。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                params = [material_id]
                sql = '''
                    SELECT id, user_id, title, description, price, original_price, category, images,
                           delivery_method, postage, can_self_pickup, brand, condition, remark,
                           created_at, updated_at
                    FROM product_materials
                    WHERE id = ?
                '''
                if user_id is not None:
                    sql += ' AND user_id = ?'
                    params.append(user_id)
                cursor.execute(sql, tuple(params))
                row = cursor.fetchone()
                return self._row_to_product_material(row) if row else None
            except Exception as e:
                logger.error(f"获取商品发布素材失败: {e}")
                return None

    def list_product_materials(self, user_id: int = None, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """分页查询商品发布素材。"""
        with self.lock:
            try:
                safe_page = max(1, int(page or 1))
                safe_page_size = max(1, min(int(page_size or 20), 100))
                offset = (safe_page - 1) * safe_page_size
                cursor = self.conn.cursor()
                conditions = []
                params = []
                if user_id is not None:
                    conditions.append('user_id = ?')
                    params.append(user_id)
                where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ''
                cursor.execute(f"SELECT COUNT(*) FROM product_materials {where_sql}", tuple(params))
                total = int(cursor.fetchone()[0] or 0)
                cursor.execute(f'''
                    SELECT id, user_id, title, description, price, original_price, category, images,
                           delivery_method, postage, can_self_pickup, brand, condition, remark,
                           created_at, updated_at
                    FROM product_materials
                    {where_sql}
                    ORDER BY datetime(created_at) DESC, id DESC
                    LIMIT ? OFFSET ?
                ''', tuple(params + [safe_page_size, offset]))
                rows = [self._row_to_product_material(row) for row in cursor.fetchall()]
                return {
                    'list': rows,
                    'total': total,
                    'page': safe_page,
                    'page_size': safe_page_size,
                    'total_pages': (total + safe_page_size - 1) // safe_page_size if total else 0,
                }
            except Exception as e:
                logger.error(f"查询商品发布素材失败: {e}")
                return {'list': [], 'total': 0, 'page': page, 'page_size': page_size, 'total_pages': 0}

    def list_product_materials_by_ids(self, material_ids: List[int], user_id: int = None) -> List[Dict[str, Any]]:
        """按ID列表查询素材。"""
        ids = []
        for material_id in material_ids or []:
            try:
                mid = int(material_id)
            except Exception:
                continue
            if mid not in ids:
                ids.append(mid)
        if not ids:
            return []
        with self.lock:
            try:
                cursor = self.conn.cursor()
                placeholders = ','.join(['?'] * len(ids))
                params: List[Any] = list(ids)
                sql = f'''
                    SELECT id, user_id, title, description, price, original_price, category, images,
                           delivery_method, postage, can_self_pickup, brand, condition, remark,
                           created_at, updated_at
                    FROM product_materials
                    WHERE id IN ({placeholders})
                '''
                if user_id is not None:
                    sql += ' AND user_id = ?'
                    params.append(user_id)
                cursor.execute(sql, tuple(params))
                material_map = {row[0]: self._row_to_product_material(row) for row in cursor.fetchall()}
                return [material_map[mid] for mid in ids if mid in material_map]
            except Exception as e:
                logger.error(f"按ID查询商品发布素材失败: {e}")
                return []

    def update_product_material(self, material_id: int, user_id: int, data: Dict[str, Any]) -> bool:
        """更新商品发布素材。"""
        allowed_fields = {
            'title', 'description', 'price', 'original_price', 'category', 'images',
            'delivery_method', 'postage', 'can_self_pickup', 'brand', 'condition', 'remark'
        }
        update_fields = []
        params = []
        for key, value in (data or {}).items():
            if key not in allowed_fields:
                continue
            if key == 'images':
                value = self._json_dumps_safe(value or [])
            elif key == 'can_self_pickup':
                value = 1 if value else 0
            update_fields.append(f"{key} = ?")
            params.append(value)
        if not update_fields:
            return False
        with self.lock:
            try:
                cursor = self.conn.cursor()
                update_fields.append('updated_at = CURRENT_TIMESTAMP')
                params.extend([material_id, user_id])
                cursor.execute(
                    f"UPDATE product_materials SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?",
                    tuple(params),
                )
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"更新商品发布素材失败: {e}")
                self.conn.rollback()
                return False

    def delete_product_material(self, material_id: int, user_id: int) -> bool:
        """删除商品发布素材。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("DELETE FROM product_materials WHERE id = ? AND user_id = ?", (material_id, user_id))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"删除商品发布素材失败: {e}")
                self.conn.rollback()
                return False

    def add_publish_log(self, user_id: int, account_id: str, title: str, description: str = None,
                        price: str = None, material_id: int = None, batch_id: str = None,
                        status: str = 'pending', error_message: str = None,
                        raw_response: Any = None) -> Optional[int]:
        """创建商品发布日志。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    INSERT INTO publish_logs (
                        user_id, account_id, title, description, price, material_id,
                        batch_id, status, error_message, raw_response
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, account_id, title, description, price, material_id, batch_id,
                    status, str(error_message)[:1000] if error_message else None,
                    self._json_dumps_safe(raw_response),
                ))
                log_id = cursor.lastrowid
                self.conn.commit()
                return log_id
            except Exception as e:
                logger.error(f"创建商品发布日志失败: {e}")
                self.conn.rollback()
                return None

    def update_publish_log(self, log_id: int, status: str = None, item_url: str = None,
                           item_id: str = None, error_message: str = None,
                           sync_status: str = None, sync_message: str = None,
                           sync_total_count: int = None, sync_saved_count: int = None,
                           raw_response: Any = None) -> bool:
        """更新商品发布日志。"""
        update_fields = []
        params = []
        for key, value in {
            'status': status,
            'item_url': item_url,
            'item_id': item_id,
            'error_message': str(error_message)[:1000] if error_message is not None else None,
            'sync_status': sync_status,
            'sync_message': sync_message,
            'sync_total_count': sync_total_count,
            'sync_saved_count': sync_saved_count,
            'raw_response': self._json_dumps_safe(raw_response) if raw_response is not None else None,
        }.items():
            if value is not None:
                update_fields.append(f"{key} = ?")
                params.append(value)
        if not update_fields:
            return False
        with self.lock:
            try:
                cursor = self.conn.cursor()
                update_fields.append('updated_at = CURRENT_TIMESTAMP')
                params.append(log_id)
                cursor.execute(f"UPDATE publish_logs SET {', '.join(update_fields)} WHERE id = ?", tuple(params))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"更新商品发布日志失败: {e}")
                self.conn.rollback()
                return False

    def _row_to_publish_log(self, row) -> Dict[str, Any]:
        return {
            'id': row[0],
            'user_id': row[1],
            'account_id': row[2],
            'title': row[3],
            'description': row[4],
            'price': row[5],
            'material_id': row[6],
            'batch_id': row[7],
            'status': row[8],
            'item_url': row[9],
            'item_id': row[10],
            'error_message': row[11],
            'sync_status': row[12],
            'sync_message': row[13],
            'sync_total_count': row[14],
            'sync_saved_count': row[15],
            'raw_response': self._json_loads_safe(row[16], row[16]),
            'created_at': row[17],
            'updated_at': row[18],
        }

    def list_publish_logs(self, user_id: int = None, account_id: str = None, status: str = None,
                          batch_id: str = None, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """分页查询商品发布日志。"""
        with self.lock:
            try:
                safe_page = max(1, int(page or 1))
                safe_page_size = max(1, min(int(page_size or 20), 100))
                offset = (safe_page - 1) * safe_page_size
                conditions = []
                params = []
                if user_id is not None:
                    conditions.append('user_id = ?')
                    params.append(user_id)
                if account_id:
                    conditions.append('account_id = ?')
                    params.append(account_id)
                if status:
                    conditions.append('status = ?')
                    params.append(status)
                if batch_id:
                    conditions.append('batch_id = ?')
                    params.append(batch_id)
                where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ''
                cursor = self.conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM publish_logs {where_sql}", tuple(params))
                total = int(cursor.fetchone()[0] or 0)
                cursor.execute(f'''
                    SELECT id, user_id, account_id, title, description, price, material_id, batch_id,
                           status, item_url, item_id, error_message, sync_status, sync_message,
                           sync_total_count, sync_saved_count, raw_response, created_at, updated_at
                    FROM publish_logs
                    {where_sql}
                    ORDER BY datetime(created_at) DESC, id DESC
                    LIMIT ? OFFSET ?
                ''', tuple(params + [safe_page_size, offset]))
                logs = [self._row_to_publish_log(row) for row in cursor.fetchall()]
                return {
                    'list': logs,
                    'total': total,
                    'page': safe_page,
                    'page_size': safe_page_size,
                    'total_pages': (total + safe_page_size - 1) // safe_page_size if total else 0,
                }
            except Exception as e:
                logger.error(f"查询商品发布日志失败: {e}")
                return {'list': [], 'total': 0, 'page': page, 'page_size': page_size, 'total_pages': 0}

    def get_publish_batch_status(self, batch_id: str, user_id: int) -> Dict[str, Any]:
        """查询批量发布状态。"""
        logs_data = self.list_publish_logs(user_id=user_id, batch_id=batch_id, page=1, page_size=100)
        logs = logs_data.get('list') or []
        counts = {'success': 0, 'failed': 0, 'publishing': 0, 'pending': 0}
        account_map: Dict[str, Dict[str, int]] = {}
        for log in logs:
            status = log.get('status') or 'pending'
            counts[status] = counts.get(status, 0) + 1
            account_id = log.get('account_id') or ''
            account_counts = account_map.setdefault(account_id, {'success': 0, 'failed': 0, 'publishing': 0, 'pending': 0, 'total': 0})
            account_counts[status] = account_counts.get(status, 0) + 1
            account_counts['total'] += 1
        total = len(logs)
        return {
            'batch_id': batch_id,
            'total': total,
            'success': counts.get('success', 0),
            'failed': counts.get('failed', 0),
            'publishing': counts.get('publishing', 0),
            'pending': counts.get('pending', 0),
            'finished': total > 0 and (counts.get('publishing', 0) + counts.get('pending', 0)) == 0,
            'account_statuses': [
                {
                    'account_id': account_id,
                    **status_map,
                }
                for account_id, status_map in account_map.items()
            ],
            'logs': logs,
        }

    def clear_old_publish_logs(self, user_id: int, days: int = 30) -> int:
        """清理指定用户N天前的发布日志。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    DELETE FROM publish_logs
                    WHERE user_id = ? AND datetime(created_at) < datetime('now', ?)
                ''', (user_id, f'-{max(1, int(days or 30))} days'))
                deleted = cursor.rowcount
                self.conn.commit()
                return deleted
            except Exception as e:
                logger.error(f"清理商品发布日志失败: {e}")
                self.conn.rollback()
                return 0

