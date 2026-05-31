import json
from datetime import datetime
from loguru import logger
from typing import Any, Dict, List
from .base import DBBase

class DBOrdersMixin:
    """orders"""

    def create_delivery_rule(self, keyword: str, card_id: int, delivery_count: int = 1,
                           enabled: bool = True, description: str = None, user_id: int = None):
        """创建发货规则"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                if user_id is not None and card_id is not None:
                    self._execute_sql(cursor, '''
                    SELECT 1 FROM cards WHERE id = ? AND user_id = ?
                    ''', (card_id, user_id))
                    if not cursor.fetchone():
                        raise ValueError(f"卡券不存在或无权限访问: {card_id}")

                cursor.execute('''
                INSERT INTO delivery_rules (keyword, card_id, delivery_count, enabled, description, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (keyword, card_id, delivery_count, enabled, description, user_id))
                self.conn.commit()
                rule_id = cursor.lastrowid
                logger.info(f"创建发货规则成功: {keyword} -> 卡券ID {card_id} (规则ID: {rule_id})")
                return rule_id
            except Exception as e:
                logger.error(f"创建发货规则失败: {e}")
                raise
    def get_all_delivery_rules(self, user_id: int = None):
        """获取所有发货规则"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute('''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times, dr.created_at, dr.updated_at,
                           c.name as card_name, c.type as card_type,
                           c.is_multi_spec, c.spec_name, c.spec_value,
                           c.spec_name_2, c.spec_value_2
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.user_id = ?
                    ORDER BY dr.created_at DESC
                    ''', (user_id,))
                else:
                    cursor.execute('''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times, dr.created_at, dr.updated_at,
                           c.name as card_name, c.type as card_type,
                           c.is_multi_spec, c.spec_name, c.spec_value,
                           c.spec_name_2, c.spec_value_2
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    ORDER BY dr.created_at DESC
                    ''')

                rules = []
                for row in cursor.fetchall():
                    rules.append({
                        'id': row[0],
                        'keyword': row[1],
                        'card_id': row[2],
                        'delivery_count': row[3],
                        'enabled': bool(row[4]),
                        'description': row[5],
                        'delivery_times': row[6],
                        'created_at': row[7],
                        'updated_at': row[8],
                        'card_name': row[9],
                        'card_type': row[10],
                        'is_multi_spec': bool(row[11]) if row[11] is not None else False,
                        'spec_name': row[12],
                        'spec_value': row[13],
                        'spec_name_2': row[14],
                        'spec_value_2': row[15]
                    })

                return rules
            except Exception as e:
                logger.error(f"获取发货规则列表失败: {e}")
                return []
    def get_delivery_rules_by_keyword(self, keyword: str, user_id: int = None, only_non_multi_spec: bool = False):
        """根据关键字获取匹配的发货规则

        Args:
            keyword: 搜索关键字（商品标题）
            user_id: 用户ID，用于过滤只属于该用户的发货规则
            only_non_multi_spec: 是否仅返回普通卡券规则（排除多规格卡券）
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                non_multi_filter = "AND (c.is_multi_spec = 0 OR c.is_multi_spec IS NULL)" if only_non_multi_spec else ""
                # 使用更灵活的匹配方式：既支持商品内容包含关键字，也支持关键字包含在商品内容中
                if user_id is not None:
                    cursor.execute(f'''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times,
                           c.name as card_name, c.type as card_type, c.api_config,
                           c.text_content, c.data_content, c.image_url, c.enabled as card_enabled, c.description as card_description,
                           c.delay_seconds as card_delay_seconds,
                           c.is_multi_spec, c.spec_name, c.spec_value, c.spec_name_2, c.spec_value_2
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.enabled = 1 AND c.enabled = 1 AND dr.user_id = ?
                    AND (? LIKE '%' || dr.keyword || '%' OR dr.keyword LIKE '%' || ? || '%')
                    {non_multi_filter}
                    ORDER BY
                        CASE
                            WHEN ? LIKE '%' || dr.keyword || '%' THEN LENGTH(dr.keyword)
                            ELSE LENGTH(dr.keyword) / 2
                        END DESC,
                        dr.id ASC
                    ''', (user_id, keyword, keyword, keyword))
                else:
                    cursor.execute(f'''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times,
                           c.name as card_name, c.type as card_type, c.api_config,
                           c.text_content, c.data_content, c.image_url, c.enabled as card_enabled, c.description as card_description,
                           c.delay_seconds as card_delay_seconds,
                           c.is_multi_spec, c.spec_name, c.spec_value, c.spec_name_2, c.spec_value_2
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.enabled = 1 AND c.enabled = 1
                    AND (? LIKE '%' || dr.keyword || '%' OR dr.keyword LIKE '%' || ? || '%')
                    {non_multi_filter}
                    ORDER BY
                        CASE
                            WHEN ? LIKE '%' || dr.keyword || '%' THEN LENGTH(dr.keyword)
                            ELSE LENGTH(dr.keyword) / 2
                        END DESC,
                        dr.id ASC
                    ''', (keyword, keyword, keyword))

                rules = []
                for row in cursor.fetchall():
                    # 解析api_config JSON字符串
                    api_config = row[9]
                    if api_config:
                        try:
                            import json
                            api_config = json.loads(api_config)
                        except (json.JSONDecodeError, TypeError):
                            # 如果解析失败，保持原始字符串
                            pass

                    rules.append({
                        'id': row[0],
                        'keyword': row[1],
                        'card_id': row[2],
                        'delivery_count': row[3],
                        'enabled': bool(row[4]),
                        'description': row[5],
                        'delivery_times': row[6],
                        'card_name': row[7],
                        'card_type': row[8],
                        'api_config': api_config,  # 修复字段名
                        'text_content': row[10],
                        'data_content': row[11],
                        'image_url': row[12],
                        'card_enabled': bool(row[13]),
                        'card_description': row[14],  # 卡券备注信息
                        'card_delay_seconds': row[15] or 0,  # 延时秒数
                        'is_multi_spec': bool(row[16]) if row[16] is not None else False,
                        'spec_name': row[17],
                        'spec_value': row[18],
                        'spec_name_2': row[19],
                        'spec_value_2': row[20]
                    })

                return rules
            except Exception as e:
                logger.error(f"根据关键字获取发货规则失败: {e}")
                return []
    def get_delivery_rule_by_id(self, rule_id: int, user_id: int = None):
        """根据ID获取发货规则（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, '''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times, dr.created_at, dr.updated_at,
                           c.name as card_name, c.type as card_type,
                           c.is_multi_spec, c.spec_name, c.spec_value,
                           c.spec_name_2, c.spec_value_2
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.id = ? AND dr.user_id = ?
                    ''', (rule_id, user_id))
                else:
                    self._execute_sql(cursor, '''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times, dr.created_at, dr.updated_at,
                           c.name as card_name, c.type as card_type,
                           c.is_multi_spec, c.spec_name, c.spec_value,
                           c.spec_name_2, c.spec_value_2
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.id = ?
                    ''', (rule_id,))

                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'keyword': row[1],
                        'card_id': row[2],
                        'delivery_count': row[3],
                        'enabled': bool(row[4]),
                        'description': row[5],
                        'delivery_times': row[6],
                        'created_at': row[7],
                        'updated_at': row[8],
                        'card_name': row[9],
                        'card_type': row[10],
                        'is_multi_spec': bool(row[11]) if row[11] is not None else False,
                        'spec_name': row[12],
                        'spec_value': row[13],
                        'spec_name_2': row[14],
                        'spec_value_2': row[15]
                    }
                return None
            except Exception as e:
                logger.error(f"获取发货规则失败: {e}")
                return None
    def update_delivery_rule(self, rule_id: int, keyword: str = None, card_id: int = None,
                           delivery_count: int = None, enabled: bool = None,
                           description: str = None, user_id: int = None):
        """更新发货规则（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                if user_id is not None and card_id is not None:
                    self._execute_sql(cursor, '''
                    SELECT 1 FROM cards WHERE id = ? AND user_id = ?
                    ''', (card_id, user_id))
                    if not cursor.fetchone():
                        raise ValueError(f"卡券不存在或无权限访问: {card_id}")

                # 构建更新语句
                update_fields = []
                params = []

                if keyword is not None:
                    update_fields.append("keyword = ?")
                    params.append(keyword)
                if card_id is not None:
                    update_fields.append("card_id = ?")
                    params.append(card_id)
                if delivery_count is not None:
                    update_fields.append("delivery_count = ?")
                    params.append(delivery_count)
                if enabled is not None:
                    update_fields.append("enabled = ?")
                    params.append(enabled)
                if description is not None:
                    update_fields.append("description = ?")
                    params.append(description)

                if not update_fields:
                    return True  # 没有需要更新的字段

                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                params.append(rule_id)

                if user_id is not None:
                    params.append(user_id)
                    sql = f"UPDATE delivery_rules SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
                else:
                    sql = f"UPDATE delivery_rules SET {', '.join(update_fields)} WHERE id = ?"

                self._execute_sql(cursor, sql, params)

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新发货规则成功: ID {rule_id}")
                    return True
                else:
                    return False  # 没有找到对应的记录

            except Exception as e:
                logger.error(f"更新发货规则失败: {e}")
                self.conn.rollback()
                raise
    def increment_delivery_times(self, rule_id: int):
        """增加发货次数（同时更新今日发货次数）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                today = datetime.now().strftime('%Y-%m-%d')

                # 先查询当前规则的最后发货日期
                cursor.execute('SELECT last_delivery_date FROM delivery_rules WHERE id = ?', (rule_id,))
                row = cursor.fetchone()
                last_date = row[0] if row else None

                if last_date == today:
                    # 今天已有发货记录，增加今日发货次数
                    cursor.execute('''
                    UPDATE delivery_rules
                    SET delivery_times = delivery_times + 1,
                        today_delivery_times = today_delivery_times + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''', (rule_id,))
                else:
                    # 新的一天，重置今日发货次数为1
                    cursor.execute('''
                    UPDATE delivery_rules
                    SET delivery_times = delivery_times + 1,
                        last_delivery_date = ?,
                        today_delivery_times = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''', (today, rule_id))

                self.conn.commit()
                logger.debug(f"发货规则 {rule_id} 发货次数已增加")
            except Exception as e:
                logger.error(f"更新发货次数失败: {e}")
    def get_today_delivery_count(self, user_id: int = None):
        """获取今日发货总数"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                today = datetime.now().strftime('%Y-%m-%d')

                if user_id is not None:
                    cursor.execute('''
                    SELECT COALESCE(SUM(today_delivery_times), 0)
                    FROM delivery_rules
                    WHERE last_delivery_date = ? AND user_id = ?
                    ''', (today, user_id))
                else:
                    cursor.execute('''
                    SELECT COALESCE(SUM(today_delivery_times), 0)
                    FROM delivery_rules
                    WHERE last_delivery_date = ?
                    ''', (today,))

                row = cursor.fetchone()
                return row[0] if row else 0
            except Exception as e:
                logger.error(f"获取今日发货统计失败: {e}")
                return 0
    def create_delivery_log(self, user_id: int = None, cookie_id: str = None, order_id: str = None,
                            item_id: str = None, buyer_id: str = None, buyer_nick: str = None,
                            rule_id: int = None, rule_keyword: str = None, card_type: str = None,
                            match_mode: str = None, channel: str = 'auto', status: str = 'failed',
                            reason: str = None):
        """记录一次真实发货尝试日志（成功/失败）。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT INTO delivery_logs (
                    user_id, cookie_id, order_id, item_id, buyer_id, buyer_nick,
                    rule_id, rule_keyword, card_type, match_mode, channel, status, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id if user_id is not None else 1,
                    cookie_id,
                    order_id,
                    item_id,
                    buyer_id,
                    buyer_nick,
                    rule_id,
                    rule_keyword,
                    card_type,
                    match_mode,
                    (channel or 'auto'),
                    (status or 'failed'),
                    reason
                ))
                self.conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"记录发货日志失败: {e}")
                self.conn.rollback()
                return None
    def upsert_delivery_finalization_state(self, order_id: str, unit_index: int = 1, cookie_id: str = None,
                                           item_id: str = None, buyer_id: str = None, channel: str = 'auto',
                                           status: str = 'sent', delivery_meta: Dict[str, Any] = None,
                                           last_error: str = None):
        """记录发货消息已发送但仍需 finalize 的状态。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                delivery_meta_json = json.dumps(delivery_meta or {}, ensure_ascii=False)
                sent_at_value = 'CURRENT_TIMESTAMP' if status == 'sent' else 'sent_at'
                finalized_at_value = 'CURRENT_TIMESTAMP' if status == 'finalized' else 'NULL'

                self._execute_sql(cursor, f'''
                INSERT INTO delivery_finalization_states (
                    order_id, unit_index, cookie_id, item_id, buyer_id, channel, status, delivery_meta, last_error, sent_at, finalized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, {finalized_at_value})
                ON CONFLICT(order_id, unit_index) DO UPDATE SET
                    cookie_id = excluded.cookie_id,
                    item_id = excluded.item_id,
                    buyer_id = excluded.buyer_id,
                    channel = excluded.channel,
                    status = excluded.status,
                    delivery_meta = excluded.delivery_meta,
                    last_error = excluded.last_error,
                    sent_at = CASE WHEN excluded.status = 'sent' THEN CURRENT_TIMESTAMP ELSE delivery_finalization_states.sent_at END,
                    finalized_at = CASE WHEN excluded.status = 'finalized' THEN CURRENT_TIMESTAMP ELSE delivery_finalization_states.finalized_at END,
                    updated_at = CURRENT_TIMESTAMP
                ''', (order_id, unit_index, cookie_id, item_id, buyer_id, channel, status, delivery_meta_json, last_error))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"更新发货 finalize 状态失败: {e}")
                self.conn.rollback()
                return False
    def get_delivery_finalization_state(self, order_id: str, unit_index: int = 1):
        """获取订单某个发货单元的 finalize 状态。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, '''
                SELECT order_id, unit_index, cookie_id, item_id, buyer_id, channel, status,
                       delivery_meta, last_error, sent_at, finalized_at, created_at, updated_at
                FROM delivery_finalization_states
                WHERE order_id = ? AND unit_index = ?
                ''', (order_id, unit_index))
                row = cursor.fetchone()
                if not row:
                    return None

                return {
                    'order_id': row[0],
                    'unit_index': row[1],
                    'cookie_id': row[2],
                    'item_id': row[3],
                    'buyer_id': row[4],
                    'channel': row[5],
                    'status': row[6],
                    'delivery_meta': json.loads(row[7] or '{}'),
                    'last_error': row[8],
                    'sent_at': row[9],
                    'finalized_at': row[10],
                    'created_at': row[11],
                    'updated_at': row[12],
                }
            except Exception as e:
                logger.error(f"获取发货 finalize 状态失败: {e}")
                return None
    def get_delivery_finalization_states(self, order_id: str):
        """获取订单全部发货单元的 finalize 状态。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, '''
                SELECT order_id, unit_index, cookie_id, item_id, buyer_id, channel, status,
                       delivery_meta, last_error, sent_at, finalized_at, created_at, updated_at
                FROM delivery_finalization_states
                WHERE order_id = ?
                ORDER BY unit_index ASC
                ''', (order_id,))

                states = []
                for row in cursor.fetchall():
                    states.append({
                        'order_id': row[0],
                        'unit_index': row[1],
                        'cookie_id': row[2],
                        'item_id': row[3],
                        'buyer_id': row[4],
                        'channel': row[5],
                        'status': row[6],
                        'delivery_meta': json.loads(row[7] or '{}'),
                        'last_error': row[8],
                        'sent_at': row[9],
                        'finalized_at': row[10],
                        'created_at': row[11],
                        'updated_at': row[12],
                    })
                return states
            except Exception as e:
                logger.error(f"获取订单全部发货 finalize 状态失败: {e}")
                return []
    def get_delivery_progress_summary(self, order_id: str, expected_quantity: int = 1):
        """汇总订单的多数量发货进度。"""
        try:
            expected = max(1, int(expected_quantity or 1))
        except (TypeError, ValueError):
            expected = 1

        states = self.get_delivery_finalization_states(order_id)
        state_by_unit = {}
        for state in states:
            try:
                unit_index = max(1, int(state.get('unit_index') or 1))
            except (TypeError, ValueError):
                unit_index = 1
            state_by_unit[unit_index] = state

        finalized_unit_indexes = []
        pending_finalize_unit_indexes = []
        remaining_unit_indexes = []

        for unit_index in range(1, expected + 1):
            status = (state_by_unit.get(unit_index) or {}).get('status')
            if status == 'finalized':
                finalized_unit_indexes.append(unit_index)
            elif status == 'sent':
                pending_finalize_unit_indexes.append(unit_index)
            else:
                remaining_unit_indexes.append(unit_index)

        if pending_finalize_unit_indexes:
            aggregate_status = 'partial_pending_finalize'
        elif len(finalized_unit_indexes) >= expected:
            aggregate_status = 'shipped'
        elif finalized_unit_indexes:
            aggregate_status = 'partial_success'
        else:
            aggregate_status = 'pending_ship'

        return {
            'order_id': order_id,
            'expected_quantity': expected,
            'state_count': len(states),
            'finalized_count': len(finalized_unit_indexes),
            'pending_finalize_count': len(pending_finalize_unit_indexes),
            'remaining_count': len(remaining_unit_indexes),
            'finalized_unit_indexes': finalized_unit_indexes,
            'pending_finalize_unit_indexes': pending_finalize_unit_indexes,
            'remaining_unit_indexes': remaining_unit_indexes,
            'aggregate_status': aggregate_status,
            'states': states,
        }
    def get_recent_delivery_logs(self, user_id: int, limit: int = 20):
        """获取最近发货日志（按用户隔离）。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                safe_limit = max(1, min(int(limit), 200))
                cursor.execute('''
                SELECT id, user_id, cookie_id, order_id, item_id, buyer_id, buyer_nick,
                       rule_id, rule_keyword, card_type, match_mode, channel, status, reason, created_at
                FROM delivery_logs
                WHERE user_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                ''', (user_id, safe_limit))

                logs = []
                for row in cursor.fetchall():
                    logs.append({
                        'id': row[0],
                        'user_id': row[1],
                        'cookie_id': row[2],
                        'order_id': row[3],
                        'item_id': row[4],
                        'buyer_id': row[5],
                        'buyer_nick': row[6],
                        'rule_id': row[7],
                        'rule_keyword': row[8],
                        'card_type': row[9],
                        'match_mode': row[10],
                        'channel': row[11],
                        'status': row[12],
                        'reason': row[13],
                        'created_at': row[14]
                    })
                return logs
            except Exception as e:
                logger.error(f"获取最近发货日志失败: {e}")
                return []
    def get_delivery_rules_by_keyword_and_spec(self, keyword: str, spec_name: str = None, spec_value: str = None,
                                               spec_name_2: str = None, spec_value_2: str = None, user_id: int = None,
                                               expected_mode: str = None):
        """根据关键字和规格信息获取匹配的发货规则（支持双规格）

        Args:
            keyword: 搜索关键字（商品标题）
            spec_name: 规格1名称
            spec_value: 规格1值
            spec_name_2: 规格2名称
            spec_value_2: 规格2值
            user_id: 用户ID，用于过滤只属于该用户的发货规则
            expected_mode: 期望规则模式，可选 one_spec 或 two_spec
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 构建user_id过滤条件
                user_filter = "AND dr.user_id = ?" if user_id is not None else ""

                def _normalize_spec_for_match(value: str) -> str:
                    """规格匹配标准化：忽略大小写、前后空白、半角/全角空格差异。"""
                    if value is None:
                        return ''
                    return str(value).strip().lower().replace(' ', '').replace('　', '')

                normalized_spec_name = _normalize_spec_for_match(spec_name)
                normalized_spec_value = _normalize_spec_for_match(spec_value)
                normalized_spec_name_2 = _normalize_spec_for_match(spec_name_2)
                normalized_spec_value_2 = _normalize_spec_for_match(spec_value_2)

                if not normalized_spec_name or not normalized_spec_value:
                    logger.info(f"规格参数不完整，跳过规格匹配: {keyword}")
                    return []

                if expected_mode is None:
                    expected_mode = 'two_spec' if (normalized_spec_name_2 and normalized_spec_value_2) else 'one_spec'

                if expected_mode not in {'one_spec', 'two_spec'}:
                    logger.warning(f"未知的规格匹配模式: {expected_mode}")
                    return []

                if expected_mode == 'two_spec':
                    if not (normalized_spec_name_2 and normalized_spec_value_2):
                        logger.info(f"期望两组规格匹配但订单规格不完整: {keyword}")
                        return []

                    sql = f'''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times,
                           c.name as card_name, c.type as card_type, c.api_config,
                           c.text_content, c.data_content, c.enabled as card_enabled,
                           c.description as card_description, c.delay_seconds as card_delay_seconds,
                           c.is_multi_spec, c.spec_name, c.spec_value, c.spec_name_2, c.spec_value_2
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.enabled = 1 AND c.enabled = 1 {user_filter}
                    AND (? LIKE '%' || dr.keyword || '%' OR dr.keyword LIKE '%' || ? || '%')
                    AND c.is_multi_spec = 1
                    AND REPLACE(REPLACE(LOWER(TRIM(COALESCE(c.spec_name, ''))), ' ', ''), '　', '') = ?
                    AND REPLACE(REPLACE(LOWER(TRIM(COALESCE(c.spec_value, ''))), ' ', ''), '　', '') = ?
                    AND REPLACE(REPLACE(LOWER(TRIM(COALESCE(c.spec_name_2, ''))), ' ', ''), '　', '') = ?
                    AND REPLACE(REPLACE(LOWER(TRIM(COALESCE(c.spec_value_2, ''))), ' ', ''), '　', '') = ?
                    ORDER BY
                        CASE
                            WHEN ? LIKE '%' || dr.keyword || '%' THEN LENGTH(dr.keyword)
                            ELSE LENGTH(dr.keyword) / 2
                        END DESC,
                        dr.delivery_times ASC
                    '''
                    if user_id is not None:
                        params = [user_id, keyword, keyword, normalized_spec_name, normalized_spec_value,
                                  normalized_spec_name_2, normalized_spec_value_2, keyword]
                    else:
                        params = [keyword, keyword, normalized_spec_name, normalized_spec_value,
                                  normalized_spec_name_2, normalized_spec_value_2, keyword]
                else:
                    sql = f'''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times,
                           c.name as card_name, c.type as card_type, c.api_config,
                           c.text_content, c.data_content, c.enabled as card_enabled,
                           c.description as card_description, c.delay_seconds as card_delay_seconds,
                           c.is_multi_spec, c.spec_name, c.spec_value, c.spec_name_2, c.spec_value_2
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.enabled = 1 AND c.enabled = 1 {user_filter}
                    AND (? LIKE '%' || dr.keyword || '%' OR dr.keyword LIKE '%' || ? || '%')
                    AND c.is_multi_spec = 1
                    AND REPLACE(REPLACE(LOWER(TRIM(COALESCE(c.spec_name, ''))), ' ', ''), '　', '') = ?
                    AND REPLACE(REPLACE(LOWER(TRIM(COALESCE(c.spec_value, ''))), ' ', ''), '　', '') = ?
                    AND TRIM(COALESCE(c.spec_name_2, '')) = ''
                    AND TRIM(COALESCE(c.spec_value_2, '')) = ''
                    ORDER BY
                        CASE
                            WHEN ? LIKE '%' || dr.keyword || '%' THEN LENGTH(dr.keyword)
                            ELSE LENGTH(dr.keyword) / 2
                        END DESC,
                        dr.delivery_times ASC
                    '''
                    if user_id is not None:
                        params = [user_id, keyword, keyword, normalized_spec_name, normalized_spec_value, keyword]
                    else:
                        params = [keyword, keyword, normalized_spec_name, normalized_spec_value, keyword]

                cursor.execute(sql, params)

                rules = []
                for row in cursor.fetchall():
                    # 解析api_config JSON字符串
                    api_config = row[9]
                    if api_config:
                        try:
                            import json
                            api_config = json.loads(api_config)
                        except (json.JSONDecodeError, TypeError):
                            # 如果解析失败，保持原始字符串
                            pass

                    rules.append({
                        'id': row[0],
                        'keyword': row[1],
                        'card_id': row[2],
                        'delivery_count': row[3],
                        'enabled': bool(row[4]),
                        'description': row[5],
                        'delivery_times': row[6] or 0,
                        'card_name': row[7],
                        'card_type': row[8],
                        'api_config': api_config,
                        'text_content': row[10],
                        'data_content': row[11],
                        'card_enabled': bool(row[12]),
                        'card_description': row[13],
                        'card_delay_seconds': row[14] or 0,
                        'is_multi_spec': bool(row[15]) if row[15] is not None else False,
                        'spec_name': row[16],
                        'spec_value': row[17],
                        'spec_name_2': row[18],
                        'spec_value_2': row[19]
                    })

                if rules:
                    if expected_mode == 'two_spec':
                        logger.info(f"找到两组规格匹配规则: {keyword} - {spec_name}:{spec_value}, {spec_name_2}:{spec_value_2}")
                    else:
                        logger.info(f"找到一组规格匹配规则: {keyword} - {spec_name}:{spec_value}")
                else:
                    if expected_mode == 'two_spec':
                        logger.info(f"未找到两组规格匹配规则: {keyword}")
                    else:
                        logger.info(f"未找到一组规格匹配规则: {keyword}")

                return rules

            except Exception as e:
                logger.error(f"获取发货规则失败: {e}")
                return []
    def delete_delivery_rule(self, rule_id: int, user_id: int = None):
        """删除发货规则（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, "DELETE FROM delivery_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
                else:
                    self._execute_sql(cursor, "DELETE FROM delivery_rules WHERE id = ?", (rule_id,))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除发货规则成功: ID {rule_id} (用户ID: {user_id})")
                    return True
                else:
                    return False  # 没有找到对应的记录

            except Exception as e:
                logger.error(f"删除发货规则失败: {e}")
                self.conn.rollback()
                raise
    def reserve_batch_data(self, card_id: int, order_id: str, unit_index: int = 1,
                           cookie_id: str = None, buyer_id: str = None, ttl_minutes: int = 30):
        """原子预占一条批量数据，避免并发订单读取到同一条卡密。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, '''
                SELECT id, card_id, order_id, cookie_id, buyer_id, unit_index, reserved_content, status,
                       last_error, created_at, updated_at, sent_at, finalized_at, released_at, expires_at
                FROM data_card_reservations
                WHERE card_id = ? AND order_id = ? AND unit_index = ?
                  AND status IN ('reserved', 'sent', 'consumed')
                ORDER BY id DESC LIMIT 1
                ''', (card_id, order_id, unit_index))
                existing = cursor.fetchone()
                if existing:
                    logger.info(f"复用批量数据预占记录: card_id={card_id}, order_id={order_id}, unit_index={unit_index}, status={existing[7]}")
                    return {
                        'id': existing[0],
                        'card_id': existing[1],
                        'order_id': existing[2],
                        'cookie_id': existing[3],
                        'buyer_id': existing[4],
                        'unit_index': existing[5],
                        'reserved_content': existing[6],
                        'status': existing[7],
                        'last_error': existing[8],
                        'created_at': existing[9],
                        'updated_at': existing[10],
                        'sent_at': existing[11],
                        'finalized_at': existing[12],
                        'released_at': existing[13],
                        'expires_at': existing[14],
                    }

                self._execute_sql(cursor, "SELECT data_content FROM cards WHERE id = ? AND type = 'data'", (card_id,))
                result = cursor.fetchone()
                if not result or not result[0]:
                    logger.warning(f"卡券 {card_id} 没有可预占的批量数据")
                    return None

                lines = [line.strip() for line in str(result[0]).split('\n') if line.strip()]
                if not lines:
                    logger.warning(f"卡券 {card_id} 批量数据为空，无法预占")
                    return None

                reserved_content = lines.pop(0)
                remaining_content = '\n'.join(lines)

                self._execute_sql(cursor, '''
                UPDATE cards
                SET data_content = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''', (remaining_content, card_id))

                self._execute_sql(cursor, '''
                INSERT INTO data_card_reservations (
                    card_id, order_id, cookie_id, buyer_id, unit_index, reserved_content, status, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'reserved', datetime('now', ?))
                ''', (card_id, order_id, cookie_id, buyer_id, unit_index, reserved_content, f'+{int(ttl_minutes)} minutes'))

                reservation_id = cursor.lastrowid
                self.conn.commit()
                logger.info(f"批量数据预占成功: card_id={card_id}, order_id={order_id}, unit_index={unit_index}, reservation_id={reservation_id}")
                return {
                    'id': reservation_id,
                    'card_id': card_id,
                    'order_id': order_id,
                    'cookie_id': cookie_id,
                    'buyer_id': buyer_id,
                    'unit_index': unit_index,
                    'reserved_content': reserved_content,
                    'status': 'reserved',
                }
            except Exception as e:
                logger.error(f"预占批量数据失败: card_id={card_id}, order_id={order_id}, error={e}")
                self.conn.rollback()
                return None
    def mark_batch_data_reservation_sent(self, reservation_id: int):
        """标记预占卡密已发送成功。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT status FROM data_card_reservations WHERE id = ?", (reservation_id,))
                result = cursor.fetchone()
                if not result:
                    return False

                current_status = result[0]
                if current_status in ('sent', 'consumed'):
                    return True
                if current_status != 'reserved':
                    logger.warning(f"批量数据预占状态不允许标记为已发送: reservation_id={reservation_id}, status={current_status}")
                    return False

                self._execute_sql(cursor, '''
                UPDATE data_card_reservations
                SET status = 'sent', sent_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, expires_at = NULL
                WHERE id = ?
                ''', (reservation_id,))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"标记批量数据预占已发送失败: reservation_id={reservation_id}, error={e}")
                self.conn.rollback()
                return False
    def finalize_batch_data_reservation(self, reservation_id: int):
        """完成批量数据预占，进入 consumed 状态。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT status FROM data_card_reservations WHERE id = ?", (reservation_id,))
                result = cursor.fetchone()
                if not result:
                    return {'success': False, 'already_finalized': False}

                current_status = result[0]
                if current_status == 'consumed':
                    return {'success': True, 'already_finalized': True}
                if current_status not in ('reserved', 'sent'):
                    logger.warning(f"批量数据预占状态不允许 finalize: reservation_id={reservation_id}, status={current_status}")
                    return {'success': False, 'already_finalized': False}

                self._execute_sql(cursor, '''
                UPDATE data_card_reservations
                SET status = 'consumed', finalized_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, expires_at = NULL
                WHERE id = ?
                ''', (reservation_id,))
                self.conn.commit()
                return {'success': True, 'already_finalized': False}
            except Exception as e:
                logger.error(f"完成批量数据预占失败: reservation_id={reservation_id}, error={e}")
                self.conn.rollback()
                return {'success': False, 'already_finalized': False}
    def release_batch_data_reservation(self, reservation_id: int, error: str = None, expired: bool = False):
        """释放未发送成功的预占卡密并回滚到卡池头部。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, '''
                SELECT card_id, reserved_content, status
                FROM data_card_reservations
                WHERE id = ?
                ''', (reservation_id,))
                result = cursor.fetchone()
                if not result:
                    return False

                card_id, reserved_content, current_status = result
                if current_status in ('released', 'expired'):
                    return True
                if current_status in ('sent', 'consumed'):
                    logger.warning(f"批量数据预占已发送或已完成，不能释放: reservation_id={reservation_id}, status={current_status}")
                    return False

                self._execute_sql(cursor, "SELECT data_content FROM cards WHERE id = ? AND type = 'data'", (card_id,))
                card_row = cursor.fetchone()
                current_content = card_row[0] if card_row and card_row[0] else ''
                new_content = reserved_content if not current_content else f"{reserved_content}\n{current_content}"

                self._execute_sql(cursor, '''
                UPDATE cards
                SET data_content = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''', (new_content, card_id))

                next_status = 'expired' if expired else 'released'
                self._execute_sql(cursor, '''
                UPDATE data_card_reservations
                SET status = ?, last_error = ?, released_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, expires_at = NULL
                WHERE id = ?
                ''', (next_status, error, reservation_id))
                self.conn.commit()
                logger.info(f"释放批量数据预占成功: reservation_id={reservation_id}, status={next_status}")
                return True
            except Exception as e:
                logger.error(f"释放批量数据预占失败: reservation_id={reservation_id}, error={e}")
                self.conn.rollback()
                return False
    def recover_stale_batch_data_reservations(self, ttl_minutes: int = 30):
        """恢复超时未发送的批量数据预占。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, '''
                SELECT id FROM data_card_reservations
                WHERE status = 'reserved'
                  AND datetime(created_at) <= datetime('now', ?)
                ORDER BY id ASC
                ''', (f'-{int(ttl_minutes)} minutes',))
                stale_ids = [row[0] for row in cursor.fetchall()]

                recovered = 0
                for reservation_id in stale_ids:
                    if self.release_batch_data_reservation(reservation_id, error='预占超时自动回收', expired=True):
                        recovered += 1

                if recovered:
                    logger.info(f"恢复超时批量数据预占完成: {recovered} 条")
                return recovered
            except Exception as e:
                logger.error(f"恢复超时批量数据预占失败: {e}")
                return 0
    def peek_batch_data(self, card_id: int, line_index: int = 0):
        """预览批量数据指定位置的记录，不执行消费。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT data_content FROM cards WHERE id = ? AND type = 'data'", (card_id,))
                result = cursor.fetchone()

                if not result or not result[0]:
                    logger.warning(f"卡券 {card_id} 没有批量数据")
                    return None

                data_content = result[0]
                lines = [line.strip() for line in data_content.split('\n') if line.strip()]
                if not lines:
                    logger.warning(f"卡券 {card_id} 批量数据为空")
                    return None

                if line_index < 0 or line_index >= len(lines):
                    logger.warning(f"卡券 {card_id} 预览索引越界: index={line_index}, total={len(lines)}")
                    return None

                logger.info(f"预览批量数据成功: 卡券ID={card_id}, index={line_index}, 剩余={len(lines)}条")
                return lines[line_index]
            except Exception as e:
                logger.error(f"预览批量数据失败: {e}")
                return None
    def consume_specific_batch_data(self, card_id: int, expected_line: str):
        """仅当第一条记录与预期一致时消费批量数据，避免误删其他卡密。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT data_content FROM cards WHERE id = ? AND type = 'data'", (card_id,))
                result = cursor.fetchone()

                if not result or not result[0]:
                    logger.warning(f"卡券 {card_id} 没有批量数据，无法消费指定记录")
                    return False

                data_content = result[0]
                lines = [line.strip() for line in data_content.split('\n') if line.strip()]
                if not lines:
                    logger.warning(f"卡券 {card_id} 批量数据为空，无法消费指定记录")
                    return False

                first_line = lines[0]
                expected_line = (expected_line or '').strip()
                if not expected_line:
                    logger.warning(f"卡券 {card_id} 缺少预期批量数据内容，拒绝消费")
                    return False

                if first_line != expected_line:
                    logger.warning(
                        f"卡券 {card_id} 批量数据首条与预期不一致，拒绝消费: "
                        f"expected={expected_line!r}, actual={first_line!r}"
                    )
                    return False

                remaining_lines = lines[1:]
                new_data_content = '\n'.join(remaining_lines)

                self._execute_sql(cursor, '''
                UPDATE cards
                SET data_content = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''', (new_data_content, card_id))

                self.conn.commit()
                logger.info(f"消费指定批量数据成功: 卡券ID={card_id}, 剩余={len(remaining_lines)}条")
                return True
            except Exception as e:
                logger.error(f"消费指定批量数据失败: {e}")
                self.conn.rollback()
                return False
    def consume_batch_data(self, card_id: int):
        """消费批量数据的第一条记录（线程安全）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 获取卡券的批量数据
                self._execute_sql(cursor, "SELECT data_content FROM cards WHERE id = ? AND type = 'data'", (card_id,))
                result = cursor.fetchone()

                if not result or not result[0]:
                    logger.warning(f"卡券 {card_id} 没有批量数据")
                    return None

                data_content = result[0]
                lines = [line.strip() for line in data_content.split('\n') if line.strip()]

                if not lines:
                    logger.warning(f"卡券 {card_id} 批量数据为空")
                    return None

                # 获取第一条数据
                first_line = lines[0]

                # 移除第一条数据，更新数据库
                remaining_lines = lines[1:]
                new_data_content = '\n'.join(remaining_lines)

                cursor.execute('''
                UPDATE cards
                SET data_content = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''', (new_data_content, card_id))

                self.conn.commit()

                logger.info(f"消费批量数据成功: 卡券ID={card_id}, 剩余={len(remaining_lines)}条")
                return first_line

            except Exception as e:
                logger.error(f"消费批量数据失败: {e}")
                self.conn.rollback()
                return None
    def insert_or_update_order(self, order_id: str, item_id: str = None, buyer_id: str = None,
                              spec_name: str = None, spec_value: str = None, quantity: str = None,
                              amount: str = None, order_status: str = None, cookie_id: str = None,
                              sid: str = None, spec_name_2: str = None, spec_value_2: str = None,
                              buyer_nick: str = None, pre_refund_status=..., clear_pre_refund_status: bool = False,
                              bargain_flow_detected=..., bargain_success_detected=...,
                              platform_created_at: str = None, platform_paid_at: str = None,
                              platform_completed_at: str = None):
        """插入或更新订单信息

        Args:
            order_id: 订单ID
            item_id: 商品ID
            buyer_id: 买家ID
            buyer_nick: 买家昵称
            spec_name: 规格名称
            spec_value: 规格值
            spec_name_2: 规格2名称
            spec_value_2: 规格2值
            quantity: 数量
            amount: 金额
            order_status: 订单状态
            cookie_id: Cookie ID
            sid: 会话ID（如 56226853668@goofish 或 56226853668），用于简化消息匹配订单
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                normalized_order_status = self._normalize_order_status(order_status)
                has_pre_refund_status = pre_refund_status is not ...
                normalized_pre_refund_status = None
                if has_pre_refund_status:
                    normalized_pre_refund_status = self._normalize_order_status(pre_refund_status)

                # 检查cookie_id是否在cookies表中存在（如果提供了cookie_id）
                if cookie_id:
                    cursor.execute("SELECT id FROM cookies WHERE id = ?", (cookie_id,))
                    cookie_exists = cursor.fetchone()
                    if not cookie_exists:
                        logger.warning(f"Cookie ID {cookie_id} 不存在于cookies表中，拒绝插入订单 {order_id}")
                        return False

                # 检查订单是否已存在
                cursor.execute("SELECT order_id, buyer_nick FROM orders WHERE order_id = ?", (order_id,))
                existing = cursor.fetchone()
                existing_buyer_nick = existing[1] if existing else None
                resolved_buyer_nick = self._resolve_order_buyer_nick_for_write(order_id, buyer_nick, existing_buyer_nick)

                if existing:
                    # 更新现有订单
                    update_fields = []
                    update_values = []

                    if item_id is not None:
                        update_fields.append("item_id = ?")
                        update_values.append(item_id)
                    if buyer_id is not None:
                        if self._is_valid_buyer_id(buyer_id):
                            update_fields.append("buyer_id = ?")
                            update_values.append(buyer_id)
                        else:
                            logger.debug(f"跳过无效buyer_id覆盖: order_id={order_id}, invalid_buyer_id={buyer_id}")
                    if buyer_nick is not None:
                        if resolved_buyer_nick is not None:
                            update_fields.append("buyer_nick = ?")
                            update_values.append(resolved_buyer_nick)
                        elif existing_buyer_nick and self._sanitize_order_buyer_nick(existing_buyer_nick) is None:
                            update_fields.append("buyer_nick = NULL")
                    if sid is not None:
                        update_fields.append("sid = ?")
                        update_values.append(sid)
                    if spec_name is not None:
                        update_fields.append("spec_name = ?")
                        update_values.append(spec_name)
                    if spec_value is not None:
                        update_fields.append("spec_value = ?")
                        update_values.append(spec_value)
                    if spec_name_2 is not None:
                        update_fields.append("spec_name_2 = ?")
                        update_values.append(spec_name_2)
                    if spec_value_2 is not None:
                        update_fields.append("spec_value_2 = ?")
                        update_values.append(spec_value_2)
                    if quantity is not None:
                        update_fields.append("quantity = ?")
                        update_values.append(quantity)
                    if amount is not None:
                        update_fields.append("amount = ?")
                        update_values.append(amount)
                    if bargain_flow_detected is not ...:
                        update_fields.append("bargain_flow_detected = ?")
                        update_values.append(1 if bargain_flow_detected else 0)
                    if bargain_success_detected is not ...:
                        update_fields.append("bargain_success_detected = ?")
                        update_values.append(1 if bargain_success_detected else 0)
                    if order_status is not None:
                        update_fields.append("order_status = ?")
                        update_values.append(normalized_order_status or 'unknown')
                    if clear_pre_refund_status:
                        update_fields.append("pre_refund_status = NULL")
                    elif has_pre_refund_status:
                        update_fields.append("pre_refund_status = ?")
                        update_values.append(normalized_pre_refund_status)
                    if cookie_id is not None:
                        update_fields.append("cookie_id = ?")
                        update_values.append(cookie_id)
                    if platform_created_at is not None:
                        update_fields.append("platform_created_at = ?")
                        update_values.append(platform_created_at)
                    if platform_paid_at is not None:
                        update_fields.append("platform_paid_at = ?")
                        update_values.append(platform_paid_at)
                    if platform_completed_at is not None:
                        update_fields.append("platform_completed_at = ?")
                        update_values.append(platform_completed_at)

                    if update_fields:
                        update_fields.append("updated_at = CURRENT_TIMESTAMP")
                        update_values.append(order_id)

                        sql = f"UPDATE orders SET {', '.join(update_fields)} WHERE order_id = ?"
                        cursor.execute(sql, update_values)
                        logger.info(f"更新订单信息: {order_id}")
                else:
                    # 插入新订单时，净化无效 buyer_id
                    sanitized_buyer_id = buyer_id if self._is_valid_buyer_id(buyer_id) else None
                    insert_fields = [
                        'order_id', 'item_id', 'buyer_id', 'buyer_nick', 'sid', 'spec_name', 'spec_value',
                        'spec_name_2', 'spec_value_2', 'quantity', 'amount', 'order_status', 'cookie_id'
                    ]
                    insert_values = [
                        order_id, item_id, sanitized_buyer_id, resolved_buyer_nick, sid, spec_name, spec_value,
                        spec_name_2, spec_value_2, quantity, amount, normalized_order_status or 'unknown', cookie_id
                    ]

                    if bargain_flow_detected is not ...:
                        insert_fields.append('bargain_flow_detected')
                        insert_values.append(1 if bargain_flow_detected else 0)
                    if bargain_success_detected is not ...:
                        insert_fields.append('bargain_success_detected')
                        insert_values.append(1 if bargain_success_detected else 0)
                    if platform_created_at is not None:
                        insert_fields.append('platform_created_at')
                        insert_values.append(platform_created_at)
                    if platform_paid_at is not None:
                        insert_fields.append('platform_paid_at')
                        insert_values.append(platform_paid_at)
                    if platform_completed_at is not None:
                        insert_fields.append('platform_completed_at')
                        insert_values.append(platform_completed_at)

                    if has_pre_refund_status and not clear_pre_refund_status:
                        insert_fields.append('pre_refund_status')
                        insert_values.append(normalized_pre_refund_status)

                    insert_placeholders = ', '.join(['?'] * len(insert_fields))
                    sql = f"INSERT INTO orders ({', '.join(insert_fields)}) VALUES ({insert_placeholders})"
                    cursor.execute(sql, insert_values)
                    logger.info(f"插入新订单: {order_id}")

                self.conn.commit()
                return True

            except Exception as e:
                logger.error(f"插入或更新订单失败: {order_id} - {e}")
                self.conn.rollback()
                return False
    def get_order_by_id(self, order_id: str):
        """根据订单ID获取订单信息"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT order_id, item_id, buyer_id, buyer_nick, sid, spec_name, spec_value,
                       spec_name_2, spec_value_2, quantity, amount, bargain_flow_detected, bargain_success_detected,
                       order_status, pre_refund_status, cookie_id, platform_created_at, platform_paid_at,
                       platform_completed_at, created_at, updated_at
                FROM orders WHERE order_id = ?
                ''', (order_id,))

                row = cursor.fetchone()
                if row:
                    return {
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'buyer_nick': row[3],
                        'sid': row[4],
                        'spec_name': row[5],
                        'spec_value': row[6],
                        'spec_name_2': row[7],
                        'spec_value_2': row[8],
                        'quantity': row[9],
                        'amount': row[10],
                        'bargain_flow_detected': bool(row[11]),
                        'bargain_success_detected': bool(row[12]),
                        'order_status': row[13],
                        'pre_refund_status': row[14],
                        'cookie_id': row[15],
                        'platform_created_at': row[16],
                        'platform_paid_at': row[17],
                        'platform_completed_at': row[18],
                        'created_at': row[19],
                        'updated_at': row[20]
                    }
                return None

            except Exception as e:
                logger.error(f"获取订单信息失败: {order_id} - {e}")
                return None
    def get_order_pre_refund_status(self, order_id: str) -> str:
        """获取订单退款前状态，用于退款撤销时跨重启回退。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("SELECT pre_refund_status FROM orders WHERE order_id = ?", (order_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return self._normalize_order_status(row[0]) if row[0] else None
            except Exception as e:
                logger.error(f"获取订单退款前状态失败: {order_id} - {e}")
                return None
    def _lookup_buyer_nick_from_chat_messages(self, cookie_id: str, sid: str = None, buyer_id: str = None) -> str:
        chat_id = str(sid or '').strip().split('@')[0]
        normalized_buyer_id = str(buyer_id or '').strip()
        if not chat_id:
            return None

        try:
            cursor = self.conn.cursor()
            params = [cookie_id, chat_id]
            buyer_filter = ''
            if normalized_buyer_id:
                buyer_filter = ' AND sender_id = ?'
                params.append(normalized_buyer_id)

            cursor.execute(f'''
                SELECT sender_name
                FROM chat_messages
                WHERE cookie_id = ? AND chat_id = ? AND direction = 2
                  AND sender_name IS NOT NULL AND sender_name != ''{buyer_filter}
                ORDER BY id DESC
                LIMIT 80
            ''', params)
            for row in cursor.fetchall():
                buyer_nick = self._sanitize_order_buyer_nick(row[0])
                if buyer_nick:
                    return buyer_nick
        except Exception as e:
            logger.debug(f"从聊天记录兜底买家昵称失败: cookie_id={cookie_id}, sid={sid}, buyer_id={buyer_id}, error={e}")

        return None
    def get_orders_by_cookie(self, cookie_id: str, limit: int = 100):
        """根据Cookie ID获取订单列表"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT order_id, item_id, buyer_id, buyer_nick, sid, spec_name, spec_value,
                       spec_name_2, spec_value_2, quantity, amount, order_status,
                       platform_created_at, platform_paid_at, platform_completed_at, created_at, updated_at
                FROM orders WHERE cookie_id = ?
                ORDER BY created_at DESC LIMIT ?
                ''', (cookie_id, limit))

                orders = []
                for row in cursor.fetchall():
                    buyer_nick = self._sanitize_order_buyer_nick(row[3])
                    if not buyer_nick:
                        buyer_nick = self._lookup_buyer_nick_from_chat_messages(cookie_id, row[4], row[2])
                    orders.append({
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'buyer_nick': buyer_nick,
                        'sid': row[4],
                        'spec_name': row[5],
                        'spec_value': row[6],
                        'spec_name_2': row[7],
                        'spec_value_2': row[8],
                        'quantity': row[9],
                        'amount': row[10],
                        'order_status': row[11],
                        'platform_created_at': row[12],
                        'platform_paid_at': row[13],
                        'platform_completed_at': row[14],
                        'created_at': row[15],
                        'updated_at': row[16]
                    })

                return orders

            except Exception as e:
                logger.error(f"获取Cookie订单列表失败: {cookie_id} - {e}")
                return []
    def delete_order(self, order_id: str, cookie_id: str = None) -> bool:
        """删除订单，可选限定所属账号。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if cookie_id is not None:
                    cursor.execute("DELETE FROM orders WHERE order_id = ? AND cookie_id = ?", (order_id, cookie_id))
                else:
                    cursor.execute("DELETE FROM orders WHERE order_id = ?", (order_id,))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除订单成功: {order_id}")
                    return True

                logger.warning(f"删除订单失败，订单不存在或无权限: {order_id}")
                return False
            except Exception as e:
                logger.error(f"删除订单失败: {order_id} - {e}")
                self.conn.rollback()
                return False
    def update_buyer_nick_by_buyer_id(self, buyer_id: str, buyer_nick: str, cookie_id: str = None):
        """根据买家ID更新所有相关订单的买家昵称

        当收到买家消息时调用此方法，自动更新该买家所有订单的昵称
        允许覆盖已有昵称，以便使用更准确的昵称替换可能不准确的值

        Args:
            buyer_id: 买家用户ID
            buyer_nick: 买家昵称
            cookie_id: Cookie ID（可选，用于限定账号）

        Returns:
            int: 更新的订单数量
        """
        if not buyer_id or not buyer_nick:
            return 0

        sanitized_buyer_nick = self._sanitize_order_buyer_nick(buyer_nick)
        if not sanitized_buyer_nick:
            return 0

        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 更新该买家所有订单的昵称（允许覆盖已有值）
                if cookie_id:
                    cursor.execute('''
                    UPDATE orders SET buyer_nick = ?
                    WHERE buyer_id = ? AND cookie_id = ?
                    ''', (sanitized_buyer_nick, buyer_id, cookie_id))
                else:
                    cursor.execute('''
                    UPDATE orders SET buyer_nick = ?
                    WHERE buyer_id = ?
                    ''', (sanitized_buyer_nick, buyer_id))

                updated_count = cursor.rowcount
                self.conn.commit()

                if updated_count > 0:
                    logger.info(f"已更新买家 {buyer_id} 的 {updated_count} 个订单昵称为: {sanitized_buyer_nick}")

                return updated_count

            except Exception as e:
                logger.error(f"更新买家昵称失败: buyer_id={buyer_id} - {e}")
                self.conn.rollback()
                return 0
    def get_recent_order_by_buyer_id(self, buyer_id: str, cookie_id: str = None, status: str = None, minutes: int = 10):
        """根据买家ID获取最近的订单信息
        
        Args:
            buyer_id: 买家用户ID
            cookie_id: Cookie ID（可选，用于限定账号）
            status: 订单状态过滤（可选，如'processing'）
            minutes: 查询最近多少分钟内的订单，默认10分钟
        
        Returns:
            Dict: 订单信息，包含order_id, item_id等
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 构建查询条件
                conditions = ["buyer_id = ?"]
                params = [buyer_id]
                
                if cookie_id:
                    conditions.append("cookie_id = ?")
                    params.append(cookie_id)
                
                if status:
                    normalized_status = self._normalize_order_status(status) or status
                    # 兼容历史数据：待发货状态可能仍保留为 pending_delivery
                    if normalized_status == 'pending_ship':
                        conditions.append("(order_status = ? OR order_status = ? OR order_status = ? OR order_status = ?)")
                        params.extend(['pending_ship', 'pending_delivery', 'partial_success', 'partial_pending_finalize'])
                    else:
                        conditions.append("order_status = ?")
                        params.append(normalized_status)
                
                # 添加时间限制
                conditions.append("datetime(created_at) >= datetime('now', ?)")
                params.append(f'-{minutes} minutes')
                
                where_clause = " AND ".join(conditions)
                
                cursor.execute(f'''
                SELECT order_id, item_id, buyer_id, buyer_nick, sid, spec_name, spec_value,
                       spec_name_2, spec_value_2, quantity, amount, order_status, cookie_id, created_at, updated_at
                FROM orders
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT 1
                ''', params)

                row = cursor.fetchone()
                if row:
                    logger.info(f"根据买家ID找到最近订单: buyer_id={buyer_id}, order_id={row[0]}, item_id={row[1]}")
                    return {
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'buyer_nick': row[3],
                        'sid': row[4],
                        'spec_name': row[5],
                        'spec_value': row[6],
                        'spec_name_2': row[7],
                        'spec_value_2': row[8],
                        'quantity': row[9],
                        'amount': row[10],
                        'order_status': row[11],
                        'cookie_id': row[12],
                        'created_at': row[13],
                        'updated_at': row[14]
                    }
                
                logger.warning(f"未找到买家 {buyer_id} 的最近订单 (cookie_id={cookie_id}, status={status}, minutes={minutes})")
                return None
                
            except Exception as e:
                logger.error(f"根据买家ID获取订单失败: buyer_id={buyer_id} - {e}")
                return None
    def get_recent_order_by_sid(self, sid: str, cookie_id: str = None, status: str = None, minutes: int = 10):
        """根据会话ID(sid)获取最近的订单信息
        
        用于简化消息场景：当ws消息只包含sid（如56226853668@goofish）而无法获取buyer_id时，
        通过sid查找对应的订单。
        
        Args:
            sid: 会话ID（如 56226853668@goofish 或 56226853668）
            cookie_id: Cookie ID（可选，用于限定账号）
            status: 订单状态过滤（可选，如'pending_ship'）
            minutes: 查询最近多少分钟内的订单，默认10分钟
        
        Returns:
            Dict: 订单信息，包含order_id, item_id, sid等
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 处理sid格式：可能是 "56226853668@goofish" 或 "56226853668"
                # 数据库中存储的可能是完整格式或纯数字格式，需要同时匹配
                sid_clean = sid.split('@')[0] if '@' in sid else sid
                
                # 构建查询条件：同时匹配完整sid和纯数字sid
                conditions = ["(sid = ? OR sid = ? OR sid LIKE ?)"]
                params = [sid, sid_clean, f"{sid_clean}@%"]
                
                if cookie_id:
                    conditions.append("cookie_id = ?")
                    params.append(cookie_id)
                
                if status:
                    normalized_status = self._normalize_order_status(status) or status
                    if normalized_status == 'pending_ship':
                        conditions.append("(order_status = ? OR order_status = ? OR order_status = ? OR order_status = ?)")
                        params.extend(['pending_ship', 'pending_delivery', 'partial_success', 'partial_pending_finalize'])
                    else:
                        conditions.append("order_status = ?")
                        params.append(normalized_status)
                
                # 添加时间限制
                conditions.append("datetime(COALESCE(updated_at, created_at)) >= datetime('now', ?)")
                params.append(f'-{minutes} minutes')
                
                where_clause = " AND ".join(conditions)
                
                sql = f'''
                SELECT order_id, item_id, buyer_id, buyer_nick, sid, spec_name, spec_value,
                       spec_name_2, spec_value_2, quantity, amount, order_status, cookie_id, created_at, updated_at
                FROM orders
                WHERE {where_clause}
                ORDER BY datetime(COALESCE(updated_at, created_at)) DESC
                LIMIT 1
                '''

                # 打印可直接执行的完整SQL语句，方便调试
                debug_sql = sql
                for param in params:
                    if param is None:
                        debug_sql = debug_sql.replace('?', 'NULL', 1)
                    elif isinstance(param, str):
                        debug_sql = debug_sql.replace('?', f"'{param}'", 1)
                    else:
                        debug_sql = debug_sql.replace('?', str(param), 1)
                logger.info(f"[get_recent_order_by_sid] 可执行SQL: {debug_sql.strip()}")

                cursor.execute(sql, params)

                row = cursor.fetchone()
                if row:
                    logger.info(f"根据sid找到最近订单: sid={sid}, order_id={row[0]}, item_id={row[1]}")
                    return {
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'buyer_nick': row[3],
                        'sid': row[4],
                        'spec_name': row[5],
                        'spec_value': row[6],
                        'spec_name_2': row[7],
                        'spec_value_2': row[8],
                        'quantity': row[9],
                        'amount': row[10],
                        'order_status': row[11],
                        'cookie_id': row[12],
                        'created_at': row[13],
                        'updated_at': row[14]
                    }
                
                logger.warning(f"未找到sid {sid} 的最近订单 (cookie_id={cookie_id}, status={status}, minutes={minutes})")
                return None
                
            except Exception as e:
                logger.error(f"根据sid获取订单失败: sid={sid} - {e}")
                return None
    def find_recent_orders_by_match_context(self, sid: str = None, buyer_id: str = None, item_id: str = None,
                                            cookie_id: str = None, statuses: List[str] = None,
                                            exclude_order_id: str = None, minutes: int = 30, limit: int = 10):
        """根据会话/买家/商品匹配键获取最近订单列表。

        主要用于同一 sid 下短时间连续产生多个订单号时，做更稳妥的状态回填。
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()

                conditions = []
                params = []

                if sid:
                    sid_clean = sid.split('@')[0] if '@' in sid else sid
                    conditions.append("(sid = ? OR sid = ? OR sid LIKE ?)")
                    params.extend([sid, sid_clean, f"{sid_clean}@%"])

                if buyer_id:
                    conditions.append("buyer_id = ?")
                    params.append(buyer_id)

                if item_id:
                    conditions.append("item_id = ?")
                    params.append(item_id)

                if cookie_id:
                    conditions.append("cookie_id = ?")
                    params.append(cookie_id)

                if exclude_order_id:
                    conditions.append("order_id != ?")
                    params.append(exclude_order_id)

                if statuses:
                    normalized_statuses = []
                    for status in statuses:
                        normalized_status = self._normalize_order_status(status) or status
                        if normalized_status not in normalized_statuses:
                            normalized_statuses.append(normalized_status)

                    if normalized_statuses:
                        placeholders = ",".join(["?"] * len(normalized_statuses))
                        conditions.append(f"order_status IN ({placeholders})")
                        params.extend(normalized_statuses)

                if not conditions:
                    logger.warning("find_recent_orders_by_match_context 缺少有效查询条件，拒绝全表扫描")
                    return []

                conditions.append("datetime(COALESCE(updated_at, created_at)) >= datetime('now', ?)")
                params.append(f'-{minutes} minutes')

                sql = f'''
                SELECT order_id, item_id, buyer_id, buyer_nick, sid, spec_name, spec_value,
                       spec_name_2, spec_value_2, quantity, amount, bargain_flow_detected, bargain_success_detected, order_status, cookie_id, created_at, updated_at
                FROM orders
                WHERE {" AND ".join(conditions)}
                ORDER BY datetime(COALESCE(updated_at, created_at)) DESC, created_at DESC
                LIMIT ?
                '''
                params.append(limit)

                cursor.execute(sql, params)
                rows = cursor.fetchall()
                if not rows:
                    logger.info(
                        "根据匹配键未找到最近订单: "
                        f"sid={sid}, buyer_id={buyer_id}, item_id={item_id}, "
                        f"cookie_id={cookie_id}, statuses={statuses}, minutes={minutes}"
                    )
                    return []

                logger.info(
                    "根据匹配键找到最近订单: "
                    f"sid={sid}, buyer_id={buyer_id}, item_id={item_id}, "
                    f"count={len(rows)}, statuses={statuses}, minutes={minutes}"
                )

                orders = []
                for row in rows:
                    orders.append({
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'buyer_nick': row[3],
                        'sid': row[4],
                        'spec_name': row[5],
                        'spec_value': row[6],
                        'spec_name_2': row[7],
                        'spec_value_2': row[8],
                        'quantity': row[9],
                        'amount': row[10],
                        'bargain_flow_detected': bool(row[11]),
                        'bargain_success_detected': bool(row[12]),
                        'order_status': row[13],
                        'cookie_id': row[14],
                        'created_at': row[15],
                        'updated_at': row[16],
                    })

                return orders

            except Exception as e:
                logger.error(
                    "根据匹配键获取最近订单失败: "
                    f"sid={sid}, buyer_id={buyer_id}, item_id={item_id}, error={e}"
                )
                return []
    def update_order_yifan_status(self, order_id: str, yifan_orderno: str = None,
                                  delivery_status: str = None, callback_data: str = None):
        """
        更新订单的亦凡API状态
        
        Args:
            order_id: 订单ID（用户订单号）
            yifan_orderno: 亦凡平台订单号
            delivery_status: 发货状态（delivered/processing/failed等）
            callback_data: 回调原始数据（JSON字符串）
        
        Returns:
            bool: 是否更新成功
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 首先检查订单是否存在
                cursor.execute("SELECT order_id, order_status FROM orders WHERE order_id = ?", (order_id,))
                existing_order = cursor.fetchone()
                if not existing_order:
                    logger.warning(f"订单不存在: {order_id}")
                    return False
                current_order_status = existing_order[1] if len(existing_order) > 1 else None
                
                # 检查是否存在yifan相关字段，如果不存在则添加
                try:
                    cursor.execute("SELECT yifan_orderno FROM orders LIMIT 1")
                except:
                    # 字段不存在，需要添加
                    logger.info("为orders表添加亦凡回调相关字段...")
                    cursor.execute("ALTER TABLE orders ADD COLUMN yifan_orderno TEXT")
                    cursor.execute("ALTER TABLE orders ADD COLUMN delivery_status TEXT")
                    cursor.execute("ALTER TABLE orders ADD COLUMN callback_data TEXT")
                    cursor.execute("ALTER TABLE orders ADD COLUMN chat_id TEXT")
                    self.conn.commit()
                    logger.info("亦凡回调字段添加完成")
                
                # 构建更新语句
                update_fields = []
                update_values = []
                
                if yifan_orderno is not None:
                    update_fields.append("yifan_orderno = ?")
                    update_values.append(yifan_orderno)
                
                if delivery_status is not None:
                    update_fields.append("delivery_status = ?")
                    update_values.append(delivery_status)

                    merged_order_status = self.resolve_external_order_status(
                        current_order_status,
                        delivery_status,
                        source='yifan_status'
                    )
                    normalized_current_status = self._normalize_order_status(current_order_status)
                    if merged_order_status and merged_order_status != normalized_current_status:
                        update_fields.append("order_status = ?")
                        update_values.append(merged_order_status)
                
                if callback_data is not None:
                    update_fields.append("callback_data = ?")
                    update_values.append(callback_data)
                
                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                update_values.append(order_id)
                
                # 执行更新
                sql = f"UPDATE orders SET {', '.join(update_fields)} WHERE order_id = ?"
                cursor.execute(sql, update_values)
                
                self.conn.commit()
                logger.info(f"更新订单亦凡状态成功: {order_id} -> {delivery_status}")
                return True
                
            except Exception as e:
                logger.error(f"更新订单亦凡状态失败: {order_id} - {e}")
                self.conn.rollback()
                return False
    def get_order_info(self, order_id: str):
        """
        获取订单完整信息（包括亦凡回调相关信息）
        
        Args:
            order_id: 订单ID
        
        Returns:
            Dict: 订单信息
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 检查是否存在yifan相关字段
                has_yifan_fields = False
                try:
                    cursor.execute("SELECT yifan_orderno FROM orders LIMIT 1")
                    has_yifan_fields = True
                except:
                    pass
                
                if has_yifan_fields:
                    cursor.execute('''
                    SELECT order_id, item_id, buyer_id, spec_name, spec_value,
                           quantity, amount, order_status, cookie_id,
                           platform_created_at, platform_paid_at, platform_completed_at,
                           created_at, updated_at,
                           yifan_orderno, delivery_status, callback_data, chat_id
                    FROM orders WHERE order_id = ?
                    ''', (order_id,))
                    
                    row = cursor.fetchone()
                    if row:
                        return {
                            'order_id': row[0],
                            'item_id': row[1],
                            'buyer_id': row[2],
                            'spec_name': row[3],
                            'spec_value': row[4],
                            'quantity': row[5],
                            'amount': row[6],
                            'order_status': row[7],
                            'cookie_id': row[8],
                            'platform_created_at': row[9],
                            'platform_paid_at': row[10],
                            'platform_completed_at': row[11],
                            'created_at': row[12],
                            'updated_at': row[13],
                            'yifan_orderno': row[14],
                            'delivery_status': row[15],
                            'callback_data': row[16],
                            'chat_id': row[17]
                        }
                else:
                    # 使用旧的查询方式
                    cursor.execute('''
                    SELECT order_id, item_id, buyer_id, spec_name, spec_value,
                           quantity, amount, order_status, cookie_id,
                           platform_created_at, platform_paid_at, platform_completed_at,
                           created_at, updated_at
                    FROM orders WHERE order_id = ?
                    ''', (order_id,))
                    
                    row = cursor.fetchone()
                    if row:
                        return {
                            'order_id': row[0],
                            'item_id': row[1],
                            'buyer_id': row[2],
                            'spec_name': row[3],
                            'spec_value': row[4],
                            'quantity': row[5],
                            'amount': row[6],
                            'order_status': row[7],
                            'cookie_id': row[8],
                            'platform_created_at': row[9],
                            'platform_paid_at': row[10],
                            'platform_completed_at': row[11],
                            'created_at': row[12],
                            'updated_at': row[13]
                        }
                
                return None
                
            except Exception as e:
                logger.error(f"获取订单信息失败: {order_id} - {e}")
                return None
    def get_order_by_yifan_orderno(self, yifan_orderno: str):
        """
        根据亦凡订单号查找订单信息
        
        Args:
            yifan_orderno: 亦凡平台订单号
        
        Returns:
            Dict: 订单信息，如果未找到返回None
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 检查是否存在yifan相关字段
                try:
                    cursor.execute("SELECT yifan_orderno FROM orders LIMIT 1")
                except:
                    logger.warning("orders表不包含yifan_orderno字段")
                    return None
                
                cursor.execute('''
                SELECT order_id, item_id, buyer_id, spec_name, spec_value,
                       quantity, amount, order_status, cookie_id, created_at, updated_at,
                       yifan_orderno, delivery_status, callback_data, chat_id
                FROM orders WHERE yifan_orderno = ?
                ''', (yifan_orderno,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'spec_name': row[3],
                        'spec_value': row[4],
                        'quantity': row[5],
                        'amount': row[6],
                        'order_status': row[7],
                        'cookie_id': row[8],
                        'created_at': row[9],
                        'updated_at': row[10],
                        'yifan_orderno': row[11],
                        'delivery_status': row[12],
                        'callback_data': row[13],
                        'chat_id': row[14]
                    }
                
                return None
                
            except Exception as e:
                logger.error(f"根据亦凡订单号查找订单失败: {yifan_orderno} - {e}")
                return None
    def update_order_chat_id(self, order_id: str, chat_id: str):
        """
        更新订单的chat_id（用于后续回调通知）
        
        Args:
            order_id: 订单ID
            chat_id: 聊天ID
        
        Returns:
            bool: 是否更新成功
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 检查是否存在chat_id字段，如果不存在则添加
                try:
                    cursor.execute("SELECT chat_id FROM orders LIMIT 1")
                except:
                    logger.info("为orders表添加chat_id字段...")
                    cursor.execute("ALTER TABLE orders ADD COLUMN chat_id TEXT")
                    self.conn.commit()
                
                cursor.execute("UPDATE orders SET chat_id = ? WHERE order_id = ?", (chat_id, order_id))
                self.conn.commit()
                return True
                
            except Exception as e:
                logger.error(f"更新订单chat_id失败: {order_id} - {e}")
                return False