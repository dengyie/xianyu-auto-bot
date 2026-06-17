import json
from loguru import logger
from typing import Any, Dict, List, Optional
from .base import DBBase

class DBItemsMixin:
    """items"""

    def create_card(self, name: str, card_type: str, api_config=None,
                   text_content: str = None, data_content: str = None, image_url: str = None,
                   description: str = None, enabled: bool = True, delay_seconds: int = 0,
                   is_multi_spec: bool = False, spec_name: str = None, spec_value: str = None,
                   spec_name_2: str = None, spec_value_2: str = None, user_id: int = None):
        """创建新卡券（支持双规格）"""
        # 调试日志
        logger.info(f"[DEBUG DB] create_card 被调用 - name: {name}")
        logger.info(f"[DEBUG DB] is_multi_spec: {is_multi_spec}, type: {type(is_multi_spec)}")
        logger.info(f"[DEBUG DB] spec_name: {spec_name}, spec_value: {spec_value}")
        logger.info(f"[DEBUG DB] spec_name_2: {spec_name_2}, type: {type(spec_name_2)}")
        logger.info(f"[DEBUG DB] spec_value_2: {spec_value_2}, type: {type(spec_value_2)}")

        with self.lock:
            try:
                # 验证多规格参数
                if is_multi_spec:
                    if not spec_name or not spec_value:
                        raise ValueError("多规格卡券必须提供规格名称和规格值")

                    # 检查唯一性：卡券名称+规格名称+规格值
                    cursor = self.conn.cursor()
                    cursor.execute('''
                    SELECT COUNT(*) FROM cards
                    WHERE name = ? AND spec_name = ? AND spec_value = ? AND user_id = ?
                    ''', (name, spec_name, spec_value, user_id))

                    if cursor.fetchone()[0] > 0:
                        raise ValueError(f"卡券已存在：{name} - {spec_name}:{spec_value}")
                else:
                    # 检查唯一性：仅卡券名称
                    cursor = self.conn.cursor()
                    cursor.execute('''
                    SELECT COUNT(*) FROM cards
                    WHERE name = ? AND (is_multi_spec = 0 OR is_multi_spec IS NULL) AND user_id = ?
                    ''', (name, user_id))

                    if cursor.fetchone()[0] > 0:
                        raise ValueError(f"卡券名称已存在：{name}")

                # 处理api_config参数 - 如果是字典则转换为JSON字符串
                api_config_str = None
                if api_config is not None:
                    if isinstance(api_config, dict):
                        import json
                        api_config_str = json.dumps(api_config)
                    else:
                        api_config_str = str(api_config)

                cursor.execute('''
                INSERT INTO cards (name, type, api_config, text_content, data_content, image_url,
                                 description, enabled, delay_seconds, is_multi_spec,
                                 spec_name, spec_value, spec_name_2, spec_value_2, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (name, card_type, api_config_str, text_content, data_content, image_url,
                      description, enabled, delay_seconds, is_multi_spec,
                      spec_name, spec_value, spec_name_2, spec_value_2, user_id))
                self.conn.commit()
                card_id = cursor.lastrowid

                if is_multi_spec:
                    logger.info(f"创建多规格卡券成功: {name} - {spec_name}:{spec_value} (ID: {card_id})")
                else:
                    logger.info(f"创建卡券成功: {name} (ID: {card_id})")
                return card_id
            except Exception as e:
                logger.error(f"创建卡券失败: {e}")
                raise
    def get_all_cards(self, user_id: int = None):
        """获取所有卡券（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute('''
                    SELECT id, name, type, api_config, text_content, data_content, image_url,
                           description, enabled, delay_seconds, is_multi_spec,
                           spec_name, spec_value, spec_name_2, spec_value_2, created_at, updated_at
                    FROM cards
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    ''', (user_id,))
                else:
                    cursor.execute('''
                    SELECT id, name, type, api_config, text_content, data_content, image_url,
                           description, enabled, delay_seconds, is_multi_spec,
                           spec_name, spec_value, spec_name_2, spec_value_2, created_at, updated_at
                    FROM cards
                    ORDER BY created_at DESC
                    ''')

                cards = []
                for row in cursor.fetchall():
                    # 解析api_config JSON字符串
                    api_config = row[3]
                    if api_config:
                        try:
                            import json
                            api_config = json.loads(api_config)
                        except (json.JSONDecodeError, TypeError):
                            # 如果解析失败，保持原始字符串
                            pass

                    cards.append({
                        'id': row[0],
                        'name': row[1],
                        'type': row[2],
                        'api_config': api_config,
                        'text_content': row[4],
                        'data_content': row[5],
                        'image_url': row[6],
                        'description': row[7],
                        'enabled': bool(row[8]),
                        'delay_seconds': row[9] or 0,
                        'is_multi_spec': bool(row[10]) if row[10] is not None else False,
                        'spec_name': row[11],
                        'spec_value': row[12],
                        'spec_name_2': row[13],
                        'spec_value_2': row[14],
                        'created_at': row[15],
                        'updated_at': row[16]
                    })

                return cards
            except Exception as e:
                logger.error(f"获取卡券列表失败: {e}")
                return []
    def get_card_by_id(self, card_id: int, user_id: int = None):
        """根据ID获取卡券（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute('''
                    SELECT id, name, type, api_config, text_content, data_content, image_url,
                           description, enabled, delay_seconds, is_multi_spec,
                           spec_name, spec_value, spec_name_2, spec_value_2, created_at, updated_at
                    FROM cards WHERE id = ? AND user_id = ?
                    ''', (card_id, user_id))
                else:
                    cursor.execute('''
                    SELECT id, name, type, api_config, text_content, data_content, image_url,
                           description, enabled, delay_seconds, is_multi_spec,
                           spec_name, spec_value, spec_name_2, spec_value_2, created_at, updated_at
                    FROM cards WHERE id = ?
                    ''', (card_id,))

                row = cursor.fetchone()
                if row:
                    # 解析api_config JSON字符串
                    api_config = row[3]
                    if api_config:
                        try:
                            import json
                            api_config = json.loads(api_config)
                        except (json.JSONDecodeError, TypeError):
                            # 如果解析失败，保持原始字符串
                            pass

                    return {
                        'id': row[0],
                        'name': row[1],
                        'type': row[2],
                        'api_config': api_config,
                        'text_content': row[4],
                        'data_content': row[5],
                        'image_url': row[6],
                        'description': row[7],
                        'enabled': bool(row[8]),
                        'delay_seconds': row[9] or 0,
                        'is_multi_spec': bool(row[10]) if row[10] is not None else False,
                        'spec_name': row[11],
                        'spec_value': row[12],
                        'spec_name_2': row[13],
                        'spec_value_2': row[14],
                        'created_at': row[15],
                        'updated_at': row[16]
                    }
                return None
            except Exception as e:
                logger.error(f"获取卡券失败: {e}")
                return None
    def update_card(self, card_id: int, name: str = None, card_type: str = None,
                   api_config=None, text_content: str = None, data_content: str = None,
                   image_url: str = None, description: str = None, enabled: bool = None,
                   delay_seconds: int = None, is_multi_spec: bool = None, spec_name: str = None,
                   spec_value: str = None, spec_name_2: str = None, spec_value_2: str = None,
                   user_id: int = None):
        """更新卡券（支持用户隔离）"""
        # 调试日志
        logger.info(f"[DEBUG DB] update_card 被调用 - card_id: {card_id}")
        logger.info(f"[DEBUG DB] is_multi_spec: {is_multi_spec}, type: {type(is_multi_spec)}")
        logger.info(f"[DEBUG DB] spec_name: {spec_name}, spec_value: {spec_value}")
        logger.info(f"[DEBUG DB] spec_name_2: {spec_name_2}, type: {type(spec_name_2)}")
        logger.info(f"[DEBUG DB] spec_value_2: {spec_value_2}, type: {type(spec_value_2)}")

        with self.lock:
            try:
                # 处理api_config参数
                api_config_str = None
                if api_config is not None:
                    if isinstance(api_config, dict):
                        import json
                        api_config_str = json.dumps(api_config)
                    else:
                        api_config_str = str(api_config)

                cursor = self.conn.cursor()

                # 构建更新语句
                update_fields = []
                params = []

                if name is not None:
                    update_fields.append("name = ?")
                    params.append(name)
                if card_type is not None:
                    update_fields.append("type = ?")
                    params.append(card_type)
                if api_config_str is not None:
                    update_fields.append("api_config = ?")
                    params.append(api_config_str)
                if text_content is not None:
                    update_fields.append("text_content = ?")
                    params.append(text_content)
                if data_content is not None:
                    update_fields.append("data_content = ?")
                    params.append(data_content)
                if image_url is not None:
                    update_fields.append("image_url = ?")
                    params.append(image_url)
                if description is not None:
                    update_fields.append("description = ?")
                    params.append(description)
                if enabled is not None:
                    update_fields.append("enabled = ?")
                    params.append(enabled)
                if delay_seconds is not None:
                    update_fields.append("delay_seconds = ?")
                    params.append(delay_seconds)
                if is_multi_spec is not None:
                    update_fields.append("is_multi_spec = ?")
                    params.append(is_multi_spec)
                if spec_name is not None:
                    update_fields.append("spec_name = ?")
                    params.append(spec_name)
                if spec_value is not None:
                    update_fields.append("spec_value = ?")
                    params.append(spec_value)
                if spec_name_2 is not None:
                    update_fields.append("spec_name_2 = ?")
                    params.append(spec_name_2)
                if spec_value_2 is not None:
                    update_fields.append("spec_value_2 = ?")
                    params.append(spec_value_2)

                if not update_fields:
                    return True  # 没有需要更新的字段

                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                params.append(card_id)

                if user_id is not None:
                    params.append(user_id)
                    sql = f"UPDATE cards SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
                else:
                    sql = f"UPDATE cards SET {', '.join(update_fields)} WHERE id = ?"

                logger.info(f"[DEBUG DB] 执行SQL: {sql}")
                logger.info(f"[DEBUG DB] 参数: {params}")
                self._execute_sql(cursor, sql, params)

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新卡券成功: ID {card_id}")
                    return True
                else:
                    return False  # 没有找到对应的记录

            except Exception as e:
                logger.error(f"更新卡券失败: {e}")
                self.conn.rollback()
                raise
    def update_card_image_url(self, card_id: int, new_image_url: str) -> bool:
        """更新卡券的图片URL"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 更新图片URL
                self._execute_sql(cursor,
                    "UPDATE cards SET image_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND type = 'image'",
                    (new_image_url, card_id))

                self.conn.commit()

                # 检查是否有行被更新
                if cursor.rowcount > 0:
                    logger.info(f"卡券图片URL更新成功: 卡券ID: {card_id}, 新URL: {new_image_url}")
                    return True
                else:
                    logger.warning(f"未找到匹配的图片卡券: 卡券ID: {card_id}")
                    return False

            except Exception as e:
                logger.error(f"更新卡券图片URL失败: {e}")
                self.conn.rollback()
                return False
    def delete_card(self, card_id: int, user_id: int = None):
        """删除卡券（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, "DELETE FROM cards WHERE id = ? AND user_id = ?", (card_id, user_id))
                else:
                    self._execute_sql(cursor, "DELETE FROM cards WHERE id = ?", (card_id,))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除卡券成功: ID {card_id} (用户ID: {user_id})")
                    return True
                else:
                    return False  # 没有找到对应的记录

            except Exception as e:
                logger.error(f"删除卡券失败: {e}")
                self.conn.rollback()
                raise
    def save_item_basic_info(self, cookie_id: str, item_id: str, item_title: str = None,
                            item_description: str = None, item_category: str = None,
                            item_price: str = None, item_detail: str = None) -> bool:
        """保存或更新商品基本信息，使用原子操作避免并发问题

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID
            item_title: 商品标题
            item_description: 商品描述
            item_category: 商品分类
            item_price: 商品价格
            item_detail: 商品详情JSON

        Returns:
            bool: 操作是否成功
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()

                # 使用 INSERT OR IGNORE + UPDATE 的原子操作模式
                # 首先尝试插入，如果已存在则忽略
                cursor.execute('''
                INSERT OR IGNORE INTO item_info (cookie_id, item_id, item_title, item_description,
                                               item_category, item_price, item_detail, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ''', (cookie_id, item_id, item_title or '', item_description or '',
                      item_category or '', item_price or '', item_detail or ''))

                # 如果是新插入的记录，直接返回成功
                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"新增商品基本信息: {item_id} - {item_title}")
                    return True

                # 记录已存在，使用原子UPDATE操作，只更新非空字段且不覆盖现有非空值
                update_parts = []
                params = []

                # 使用 CASE WHEN 语句进行条件更新，避免覆盖现有数据
                if item_title:
                    update_parts.append("item_title = CASE WHEN (item_title IS NULL OR item_title = '') THEN ? ELSE item_title END")
                    params.append(item_title)

                if item_description:
                    update_parts.append("item_description = CASE WHEN (item_description IS NULL OR item_description = '') THEN ? ELSE item_description END")
                    params.append(item_description)

                if item_category:
                    update_parts.append("item_category = CASE WHEN (item_category IS NULL OR item_category = '') THEN ? ELSE item_category END")
                    params.append(item_category)

                if item_price:
                    update_parts.append("item_price = CASE WHEN (item_price IS NULL OR item_price = '') THEN ? ELSE item_price END")
                    params.append(item_price)

                # 对于item_detail，只有在现有值为空时才更新
                if item_detail:
                    update_parts.append("item_detail = CASE WHEN (item_detail IS NULL OR item_detail = '' OR TRIM(item_detail) = '') THEN ? ELSE item_detail END")
                    params.append(item_detail)

                if update_parts:
                    update_parts.append("updated_at = CURRENT_TIMESTAMP")
                    params.extend([cookie_id, item_id])

                    sql = f"UPDATE item_info SET {', '.join(update_parts)} WHERE cookie_id = ? AND item_id = ?"
                    self._execute_sql(cursor, sql, params)

                    if cursor.rowcount > 0:
                        logger.info(f"更新商品基本信息: {item_id} - {item_title}")
                    else:
                        logger.debug(f"商品信息无需更新: {item_id}")

                self.conn.commit()
                return True

        except Exception as e:
            logger.error(f"保存商品基本信息失败: {e}")
            self.conn.rollback()
            return False
    def save_item_info(self, cookie_id: str, item_id: str, item_data = None) -> bool:
        """保存或更新商品信息

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID
            item_data: 商品详情数据，可以是字符串或字典，也可以为None

        Returns:
            bool: 操作是否成功
        """
        try:
            # 验证：如果只有商品ID，没有商品详情数据，则不插入数据库
            if not item_data:
                logger.debug(f"跳过保存商品信息：缺少商品详情数据 - {item_id}")
                return False

            # 如果是字典类型，检查是否有标题信息
            if isinstance(item_data, dict):
                title = item_data.get('title', '').strip()
                if not title:
                    logger.debug(f"跳过保存商品信息：缺少商品标题 - {item_id}")
                    return False

            # 如果是字符串类型，检查是否为空
            if isinstance(item_data, str) and not item_data.strip():
                logger.debug(f"跳过保存商品信息：商品详情为空 - {item_id}")
                return False

            with self.lock:
                cursor = self.conn.cursor()

                # 检查商品是否已存在
                cursor.execute('''
                SELECT id, item_detail FROM item_info
                WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                existing = cursor.fetchone()

                if existing:
                    # 如果传入的商品详情有值，则用最新数据覆盖
                    if item_data is not None and item_data:
                        # 处理字符串类型的详情数据
                        if isinstance(item_data, str):
                            cursor.execute('''
                            UPDATE item_info SET
                                item_detail = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE cookie_id = ? AND item_id = ?
                            ''', (item_data, cookie_id, item_id))
                        else:
                            # 处理字典类型的详情数据（向后兼容）
                            cursor.execute('''
                            UPDATE item_info SET
                                item_title = ?, item_description = ?, item_category = ?,
                                item_price = ?, item_detail = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE cookie_id = ? AND item_id = ?
                            ''', (
                                item_data.get('title', ''),
                                item_data.get('description', ''),
                                item_data.get('category', ''),
                                item_data.get('price', ''),
                                json.dumps(item_data, ensure_ascii=False),
                                cookie_id, item_id
                            ))
                        logger.info(f"更新商品信息（覆盖）: {item_id}")
                    else:
                        # 如果商品详情没有数据，则不更新，只记录存在
                        logger.debug(f"商品信息已存在，无新数据，跳过更新: {item_id}")
                        return True
                else:
                    # 新增商品信息
                    if isinstance(item_data, str):
                        # 直接保存字符串详情
                        cursor.execute('''
                        INSERT INTO item_info (cookie_id, item_id, item_detail)
                        VALUES (?, ?, ?)
                        ''', (cookie_id, item_id, item_data))
                    else:
                        # 处理字典类型的详情数据（向后兼容）
                        cursor.execute('''
                        INSERT INTO item_info (cookie_id, item_id, item_title, item_description,
                                             item_category, item_price, item_detail)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            cookie_id, item_id,
                            item_data.get('title', '') if item_data else '',
                            item_data.get('description', '') if item_data else '',
                            item_data.get('category', '') if item_data else '',
                            item_data.get('price', '') if item_data else '',
                            json.dumps(item_data, ensure_ascii=False) if item_data else ''
                        ))
                    logger.info(f"新增商品信息: {item_id}")

                self.conn.commit()
                return True

        except Exception as e:
            logger.error(f"保存商品信息失败: {e}")
            self.conn.rollback()
            return False
    def get_item_info(self, cookie_id: str, item_id: str) -> Optional[Dict]:
        """获取商品信息

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID

        Returns:
            Dict: 商品信息，如果不存在返回None
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT * FROM item_info
                WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                row = cursor.fetchone()
                if row:
                    columns = [description[0] for description in cursor.description]
                    item_info = dict(zip(columns, row))

                    # 解析item_detail JSON
                    if item_info.get('item_detail'):
                        try:
                            item_info['item_detail_parsed'] = json.loads(item_info['item_detail'])
                        except:
                            item_info['item_detail_parsed'] = {}
                    logger.info(f"item_info: {item_info}")
                    return item_info
                return None

        except Exception as e:
            logger.error(f"获取商品信息失败: {e}")
            return None
    def update_item_multi_spec_status(self, cookie_id: str, item_id: str, is_multi_spec: bool) -> bool:
        """更新商品的多规格状态"""
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                UPDATE item_info
                SET is_multi_spec = ?, updated_at = CURRENT_TIMESTAMP
                WHERE cookie_id = ? AND item_id = ?
                ''', (is_multi_spec, cookie_id, item_id))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新商品多规格状态成功: {item_id} -> {is_multi_spec}")
                    return True
                else:
                    logger.warning(f"商品不存在，无法更新多规格状态: {item_id}")
                    return False

        except Exception as e:
            logger.error(f"更新商品多规格状态失败: {e}")
            self.conn.rollback()
            return False
    def get_item_multi_spec_status(self, cookie_id: str, item_id: str) -> bool:
        """获取商品的多规格状态"""
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT is_multi_spec FROM item_info
                WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                row = cursor.fetchone()
                if row:
                    return bool(row[0]) if row[0] is not None else False
                return False

        except Exception as e:
            logger.error(f"获取商品多规格状态失败: {e}")
            return False
    def update_item_multi_quantity_delivery_status(self, cookie_id: str, item_id: str, multi_quantity_delivery: bool) -> bool:
        """更新商品的多数量发货状态"""
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                UPDATE item_info
                SET multi_quantity_delivery = ?, updated_at = CURRENT_TIMESTAMP
                WHERE cookie_id = ? AND item_id = ?
                ''', (multi_quantity_delivery, cookie_id, item_id))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新商品多数量发货状态成功: {item_id} -> {multi_quantity_delivery}")
                    return True
                else:
                    logger.warning(f"未找到要更新的商品: {item_id}")
                    return False

        except Exception as e:
            logger.error(f"更新商品多数量发货状态失败: {e}")
            self.conn.rollback()
            return False
    def get_item_multi_quantity_delivery_status(self, cookie_id: str, item_id: str) -> bool:
        """获取商品的多数量发货状态"""
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT multi_quantity_delivery FROM item_info
                WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                row = cursor.fetchone()
                if row:
                    return bool(row[0]) if row[0] is not None else False
                return False

        except Exception as e:
            logger.error(f"获取商品多数量发货状态失败: {e}")
            return False
    def get_items_by_cookie(self, cookie_id: str) -> List[Dict]:
        """获取指定Cookie的所有商品信息

        Args:
            cookie_id: Cookie ID

        Returns:
            List[Dict]: 商品信息列表
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT * FROM item_info
                WHERE cookie_id = ?
                ORDER BY updated_at DESC
                ''', (cookie_id,))

                columns = [description[0] for description in cursor.description]
                items = []

                for row in cursor.fetchall():
                    item_info = dict(zip(columns, row))

                    # 解析item_detail JSON
                    if item_info.get('item_detail'):
                        try:
                            item_info['item_detail_parsed'] = json.loads(item_info['item_detail'])
                        except:
                            item_info['item_detail_parsed'] = {}

                    items.append(item_info)

                return items

        except Exception as e:
            logger.error(f"获取Cookie商品信息失败: {e}")
            return []
    def get_all_items(self) -> List[Dict]:
        """获取所有商品信息

        Returns:
            List[Dict]: 所有商品信息列表
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT * FROM item_info
                ORDER BY updated_at DESC
                ''')

                columns = [description[0] for description in cursor.description]
                items = []

                for row in cursor.fetchall():
                    item_info = dict(zip(columns, row))

                    # 解析item_detail JSON
                    if item_info.get('item_detail'):
                        try:
                            item_info['item_detail_parsed'] = json.loads(item_info['item_detail'])
                        except:
                            item_info['item_detail_parsed'] = {}

                    items.append(item_info)

                return items

        except Exception as e:
            logger.error(f"获取所有商品信息失败: {e}")
            return []
    def update_item_detail(self, cookie_id: str, item_id: str, item_detail: str) -> bool:
        """更新商品详情（不覆盖商品标题等基本信息）

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID
            item_detail: 商品详情JSON字符串

        Returns:
            bool: 操作是否成功
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                # 只更新item_detail字段，不影响其他字段
                cursor.execute('''
                UPDATE item_info SET
                    item_detail = ?, updated_at = CURRENT_TIMESTAMP
                WHERE cookie_id = ? AND item_id = ?
                ''', (item_detail, cookie_id, item_id))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新商品详情成功: {item_id}")
                    return True
                else:
                    logger.warning(f"未找到要更新的商品: {item_id}")
                    return False

        except Exception as e:
            logger.error(f"更新商品详情失败: {e}")
            self.conn.rollback()
            return False
    def update_item_title_only(self, cookie_id: str, item_id: str, item_title: str) -> bool:
        """仅更新商品标题（并发安全）

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID
            item_title: 商品标题

        Returns:
            bool: 操作是否成功
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                # 使用 INSERT OR REPLACE 确保记录存在，但只更新标题字段
                cursor.execute('''
                INSERT INTO item_info (cookie_id, item_id, item_title, item_description,
                                     item_category, item_price, item_detail, created_at, updated_at)
                VALUES (?, ?, ?,
                       COALESCE((SELECT item_description FROM item_info WHERE cookie_id = ? AND item_id = ?), ''),
                       COALESCE((SELECT item_category FROM item_info WHERE cookie_id = ? AND item_id = ?), ''),
                       COALESCE((SELECT item_price FROM item_info WHERE cookie_id = ? AND item_id = ?), ''),
                       COALESCE((SELECT item_detail FROM item_info WHERE cookie_id = ? AND item_id = ?), ''),
                       COALESCE((SELECT created_at FROM item_info WHERE cookie_id = ? AND item_id = ?), CURRENT_TIMESTAMP),
                       CURRENT_TIMESTAMP)
                ON CONFLICT(cookie_id, item_id) DO UPDATE SET
                    item_title = excluded.item_title,
                    updated_at = CURRENT_TIMESTAMP
                ''', (cookie_id, item_id, item_title,
                      cookie_id, item_id, cookie_id, item_id, cookie_id, item_id,
                      cookie_id, item_id, cookie_id, item_id))

                self.conn.commit()
                logger.info(f"更新商品标题成功: {item_id} - {item_title}")
                return True

        except Exception as e:
            logger.error(f"更新商品标题失败: {e}")
            self.conn.rollback()
            return False
    def batch_save_item_basic_info(self, items_data: list) -> int:
        """批量保存商品基本信息（并发安全）

        Args:
            items_data: 商品数据列表，每个元素包含 cookie_id, item_id, item_title 等字段

        Returns:
            int: 成功保存的商品数量
        """
        if not items_data:
            return 0

        success_count = 0
        try:
            with self.lock:
                cursor = self.conn.cursor()

                # 使用事务批量处理
                cursor.execute('BEGIN TRANSACTION')

                for item_data in items_data:
                    try:
                        cookie_id = item_data.get('cookie_id')
                        item_id = item_data.get('item_id')
                        item_title = item_data.get('item_title', '')
                        item_description = item_data.get('item_description', '')
                        item_category = item_data.get('item_category', '')
                        item_price = item_data.get('item_price', '')
                        item_detail = item_data.get('item_detail', '')

                        if not cookie_id or not item_id:
                            continue

                        # 验证：如果没有商品标题，则跳过保存
                        if not item_title or not item_title.strip():
                            logger.debug(f"跳过批量保存商品信息：缺少商品标题 - {item_id}")
                            continue

                        # 使用 INSERT OR IGNORE + UPDATE 模式
                        cursor.execute('''
                        INSERT OR IGNORE INTO item_info (cookie_id, item_id, item_title, item_description,
                                                       item_category, item_price, item_detail, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        ''', (cookie_id, item_id, item_title, item_description,
                              item_category, item_price, item_detail))

                        if cursor.rowcount == 0:
                            # 记录已存在，进行条件更新
                            update_sql = '''
                            UPDATE item_info SET
                                item_title = CASE WHEN (item_title IS NULL OR item_title = '') AND ? != '' THEN ? ELSE item_title END,
                                item_description = CASE WHEN (item_description IS NULL OR item_description = '') AND ? != '' THEN ? ELSE item_description END,
                                item_category = CASE WHEN (item_category IS NULL OR item_category = '') AND ? != '' THEN ? ELSE item_category END,
                                item_price = CASE WHEN (item_price IS NULL OR item_price = '') AND ? != '' THEN ? ELSE item_price END,
                                item_detail = CASE WHEN (item_detail IS NULL OR item_detail = '' OR TRIM(item_detail) = '') AND ? != '' THEN ? ELSE item_detail END,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE cookie_id = ? AND item_id = ?
                            '''
                            self._execute_sql(cursor, update_sql, (
                                item_title, item_title,
                                item_description, item_description,
                                item_category, item_category,
                                item_price, item_price,
                                item_detail, item_detail,
                                cookie_id, item_id
                            ))

                        success_count += 1

                    except Exception as item_e:
                        logger.warning(f"批量保存单个商品失败 {item_data.get('item_id', 'unknown')}: {item_e}")
                        continue

                cursor.execute('COMMIT')
                logger.info(f"批量保存商品信息完成: {success_count}/{len(items_data)} 个商品")
                return success_count

        except Exception as e:
            logger.error(f"批量保存商品信息失败: {e}")
            try:
                cursor.execute('ROLLBACK')
            except:
                pass
            return success_count
    def batch_update_item_title_price(self, items_data: list) -> int:
        """批量更新商品标题和价格（不更新商品详情）
        
        Args:
            items_data: 商品数据列表，每个元素包含 cookie_id, item_id, item_title, item_price
        
        Returns:
            int: 成功更新的商品数量
        """
        if not items_data:
            return 0
        
        success_count = 0
        try:
            with self.lock:
                cursor = self.conn.cursor()
                
                # 使用事务批量处理
                cursor.execute('BEGIN TRANSACTION')
                
                for item_data in items_data:
                    try:
                        cookie_id = item_data.get('cookie_id')
                        item_id = item_data.get('item_id')
                        item_title = item_data.get('item_title', '')
                        item_price = item_data.get('item_price', '')
                        item_category = item_data.get('item_category', '')
                        
                        if not cookie_id or not item_id:
                            continue
                        
                        # 只更新标题、价格和分类，不更新商品详情
                        update_sql = '''
                        UPDATE item_info SET
                            item_title = ?,
                            item_price = ?,
                            item_category = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE cookie_id = ? AND item_id = ?
                        '''
                        cursor.execute(update_sql, (
                            item_title,
                            item_price,
                            item_category,
                            cookie_id,
                            item_id
                        ))
                        
                        if cursor.rowcount > 0:
                            success_count += 1
                    
                    except Exception as item_e:
                        logger.warning(f"批量更新单个商品失败 {item_data.get('item_id', 'unknown')}: {item_e}")
                        continue
                
                cursor.execute('COMMIT')
                logger.info(f"批量更新商品标题和价格完成: {success_count}/{len(items_data)} 个商品")
                return success_count
        
        except Exception as e:
            logger.error(f"批量更新商品标题和价格失败: {e}")
            try:
                cursor.execute('ROLLBACK')
            except:
                pass
            return success_count
    def delete_item_info(self, cookie_id: str, item_id: str) -> bool:
        """删除商品信息

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID

        Returns:
            bool: 操作是否成功
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('DELETE FROM item_info WHERE cookie_id = ? AND item_id = ?',
                             (cookie_id, item_id))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除商品信息成功: {cookie_id} - {item_id}")
                    return True
                else:
                    logger.warning(f"未找到要删除的商品信息: {cookie_id} - {item_id}")
                    return False

        except Exception as e:
            logger.error(f"删除商品信息失败: {e}")
            self.conn.rollback()
            return False
    def batch_delete_item_info(self, items_to_delete: list) -> int:
        """批量删除商品信息

        Args:
            items_to_delete: 要删除的商品列表，每个元素包含 cookie_id 和 item_id

        Returns:
            int: 成功删除的商品数量
        """
        if not items_to_delete:
            return 0

        success_count = 0
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('BEGIN TRANSACTION')

                for item_data in items_to_delete:
                    try:
                        cookie_id = item_data.get('cookie_id')
                        item_id = item_data.get('item_id')

                        if not cookie_id or not item_id:
                            continue

                        cursor.execute('DELETE FROM item_info WHERE cookie_id = ? AND item_id = ?',
                                     (cookie_id, item_id))

                        if cursor.rowcount > 0:
                            success_count += 1
                            logger.debug(f"删除商品信息: {cookie_id} - {item_id}")

                    except Exception as item_e:
                        logger.warning(f"删除单个商品失败 {item_data.get('item_id', 'unknown')}: {item_e}")
                        continue

                cursor.execute('COMMIT')
                logger.info(f"批量删除商品信息完成: {success_count}/{len(items_to_delete)} 个商品")
                return success_count

        except Exception as e:
            logger.error(f"批量删除商品信息失败: {e}")
            try:
                cursor.execute('ROLLBACK')
            except:
                pass
            return success_count
    def get_item_replay(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        根据商品ID获取商品回复信息，并返回统一格式

        Args:
            item_id (str): 商品ID

        Returns:
            Optional[Dict[str, Any]]: 商品回复信息字典（统一格式），找不到返回 None
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                    SELECT reply_content FROM item_replay
                    WHERE item_id = ?
                ''', (item_id,))

                row = cursor.fetchone()
                if row:
                    (reply_content,) = row
                    return {
                        'reply_content': reply_content or ''
                    }
                return None
        except Exception as e:
            logger.error(f"获取商品回复失败: {e}")
            return None
    def get_item_reply(self, cookie_id: str, item_id: str) -> Optional[Dict[str, Any]]:
        """
        获取指定账号和商品的回复内容

        Args:
            cookie_id (str): 账号ID
            item_id (str): 商品ID

        Returns:
            Dict: 包含回复内容的字典，如果不存在返回None
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                    SELECT reply_content, created_at, updated_at
                    FROM item_replay
                    WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                row = cursor.fetchone()
                if row:
                    return {
                        'reply_content': row[0] or '',
                        'created_at': row[1],
                        'updated_at': row[2]
                    }
                return None
        except Exception as e:
            logger.error(f"获取指定商品回复失败: {e}")
            return None
    def update_item_reply(self, cookie_id: str, item_id: str, reply_content: str) -> bool:
        """
        更新指定cookie和item的回复内容及更新时间

        Args:
            cookie_id (str): 账号ID
            item_id (str): 商品ID
            reply_content (str): 回复内容

        Returns:
            bool: 更新成功返回True，失败返回False
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                    UPDATE item_replay
                    SET reply_content = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE cookie_id = ? AND item_id = ?
                ''', (reply_content, cookie_id, item_id))

                if cursor.rowcount == 0:
                    # 如果没更新到，说明该条记录不存在，可以考虑插入
                    cursor.execute('''
                        INSERT INTO item_replay (item_id, cookie_id, reply_content, created_at, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ''', (item_id, cookie_id, reply_content))

                self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"更新商品回复失败: {e}")
            return False
    def get_itemReplays_by_cookie(self, cookie_id: str) -> List[Dict]:
        """获取指定Cookie的所有商品信息

        Args:
            cookie_id: Cookie ID

        Returns:
            List[Dict]: 商品信息列表
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT r.item_id, r.cookie_id, r.reply_content, r.created_at, r.updated_at, i.item_title, i.item_detail
                    FROM item_replay r
                    LEFT JOIN item_info i ON i.cookie_id = r.cookie_id AND i.item_id = r.item_id
                    WHERE r.cookie_id = ?
                    ORDER BY r.updated_at DESC
                ''', (cookie_id,))

                columns = [description[0] for description in cursor.description]
                items = []

                for row in cursor.fetchall():
                    item_info = dict(zip(columns, row))

                    items.append(item_info)

                return items

        except Exception as e:
            logger.error(f"获取Cookie商品信息失败: {e}")
            return []
    def delete_item_reply(self, cookie_id: str, item_id: str) -> bool:
        """
        删除指定 cookie_id 和 item_id 的商品回复

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID

        Returns:
            bool: 删除成功返回 True，失败返回 False
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                    DELETE FROM item_replay
                    WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))
                self.conn.commit()
                # 判断是否有删除行
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除商品回复失败: {e}")
            return False
    def batch_delete_item_replies(self, items: List[Dict[str, str]]) -> Dict[str, int]:
        """
        批量删除商品回复

        Args:
            items: List[Dict] 每个字典包含 cookie_id 和 item_id

        Returns:
            Dict[str, int]: 返回成功和失败的数量，例如 {"success_count": 3, "failed_count": 1}
        """
        success_count = 0
        failed_count = 0

        try:
            with self.lock:
                cursor = self.conn.cursor()
                for item in items:
                    cookie_id = item.get('cookie_id')
                    item_id = item.get('item_id')
                    if not cookie_id or not item_id:
                        failed_count += 1
                        continue
                    cursor.execute('''
                        DELETE FROM item_replay
                        WHERE cookie_id = ? AND item_id = ?
                    ''', (cookie_id, item_id))
                    if cursor.rowcount > 0:
                        success_count += 1
                    else:
                        failed_count += 1
                self.conn.commit()
        except Exception as e:
            logger.error(f"批量删除商品回复失败: {e}")
            # 整体失败则视为全部失败
            return {"success_count": 0, "failed_count": len(items)}

        return {"success_count": success_count, "failed_count": failed_count}
