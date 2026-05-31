import sqlite3
from loguru import logger
from typing import Dict, List, Tuple
from .base import DBBase

class DBKeywordsMixin:
    """keywords"""

    def save_keywords(self, cookie_id: str, keywords: List[Tuple[str, str]]) -> bool:
        """保存关键字列表，先删除旧数据再插入新数据（向后兼容方法）"""
        # 转换为新格式（不包含item_id）
        keywords_with_item_id = [(keyword, reply, None) for keyword, reply in keywords]
        return self.save_keywords_with_item_id(cookie_id, keywords_with_item_id)
    def save_keywords_with_item_id(self, cookie_id: str, keywords: List[Tuple[str, str, str]]) -> bool:
        """保存关键字列表（包含商品ID），先删除旧数据再插入新数据"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 先删除该cookie_id的所有关键字
                self._execute_sql(cursor, "DELETE FROM keywords WHERE cookie_id = ?", (cookie_id,))

                # 插入新关键字，使用INSERT OR REPLACE来处理可能的唯一约束冲突
                for keyword, reply, item_id in keywords:
                    # 标准化item_id：空字符串转为NULL
                    normalized_item_id = item_id if item_id and item_id.strip() else None

                    try:
                        self._execute_sql(cursor,
                            "INSERT INTO keywords (cookie_id, keyword, reply, item_id) VALUES (?, ?, ?, ?)",
                            (cookie_id, keyword, reply, normalized_item_id))
                    except sqlite3.IntegrityError as ie:
                        # 如果遇到唯一约束冲突，记录详细错误信息
                        item_desc = f"商品ID: {normalized_item_id}" if normalized_item_id else "通用关键词"
                        logger.error(f"关键词唯一约束冲突: Cookie={cookie_id}, 关键词='{keyword}', {item_desc}")
                        raise ie

                self.conn.commit()
                logger.info(f"关键字保存成功: {cookie_id}, {len(keywords)}条")
                return True
            except Exception as e:
                logger.error(f"关键字保存失败: {e}")
                self.conn.rollback()
                return False
    def save_text_keywords_only(self, cookie_id: str, keywords: List[Tuple[str, str, str]]) -> bool:
        """保存文本关键字列表，只删除文本类型的关键词，保留图片关键词"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 检查是否与现有图片关键词冲突
                for keyword, reply, item_id in keywords:
                    normalized_item_id = item_id if item_id and item_id.strip() else None

                    # 检查是否存在同名的图片关键词
                    if normalized_item_id:
                        # 有商品ID的情况：检查 (cookie_id, keyword, item_id) 是否存在图片关键词
                        self._execute_sql(cursor,
                            "SELECT type FROM keywords WHERE cookie_id = ? AND keyword = ? AND item_id = ? AND type = 'image'",
                            (cookie_id, keyword, normalized_item_id))
                    else:
                        # 通用关键词的情况：检查 (cookie_id, keyword) 是否存在图片关键词
                        self._execute_sql(cursor,
                            "SELECT type FROM keywords WHERE cookie_id = ? AND keyword = ? AND (item_id IS NULL OR item_id = '') AND type = 'image'",
                            (cookie_id, keyword))

                    if cursor.fetchone():
                        # 存在同名图片关键词，抛出友好的错误信息
                        item_desc = f"商品ID: {normalized_item_id}" if normalized_item_id else "通用关键词"
                        error_msg = f"关键词 '{keyword}' （{item_desc}） 已存在（图片关键词），无法保存为文本关键词"
                        logger.warning(f"文本关键词与图片关键词冲突: Cookie={cookie_id}, 关键词='{keyword}', {item_desc}")
                        raise ValueError(error_msg)

                # 只删除该cookie_id的文本类型关键字，保留图片关键词
                self._execute_sql(cursor,
                    "DELETE FROM keywords WHERE cookie_id = ? AND (type IS NULL OR type = 'text')",
                    (cookie_id,))

                # 插入新的文本关键字
                for keyword, reply, item_id in keywords:
                    # 标准化item_id：空字符串转为NULL
                    normalized_item_id = item_id if item_id and item_id.strip() else None

                    self._execute_sql(cursor,
                        "INSERT INTO keywords (cookie_id, keyword, reply, item_id, type) VALUES (?, ?, ?, ?, 'text')",
                        (cookie_id, keyword, reply, normalized_item_id))

                self.conn.commit()
                logger.info(f"文本关键字保存成功: {cookie_id}, {len(keywords)}条，图片关键词已保留")
                return True
            except ValueError:
                # 重新抛出友好的错误信息
                raise
            except Exception as e:
                logger.error(f"文本关键字保存失败: {e}")
                self.conn.rollback()
                return False
    def get_keywords(self, cookie_id: str) -> List[Tuple[str, str]]:
        """获取指定Cookie的关键字列表（向后兼容方法）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT keyword, reply FROM keywords WHERE cookie_id = ?", (cookie_id,))
                return [(row[0], row[1]) for row in cursor.fetchall()]
            except Exception as e:
                logger.error(f"获取关键字失败: {e}")
                return []
    def get_keywords_with_item_id(self, cookie_id: str) -> List[Tuple[str, str, str]]:
        """获取指定Cookie的关键字列表（包含商品ID）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT keyword, reply, item_id FROM keywords WHERE cookie_id = ?", (cookie_id,))
                return [(row[0], row[1], row[2]) for row in cursor.fetchall()]
            except Exception as e:
                logger.error(f"获取关键字失败: {e}")
                return []
    def check_keyword_duplicate(self, cookie_id: str, keyword: str, item_id: str = None) -> bool:
        """检查关键词是否重复"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if item_id:
                    # 如果有商品ID，检查相同cookie_id、keyword、item_id的组合
                    self._execute_sql(cursor,
                        "SELECT COUNT(*) FROM keywords WHERE cookie_id = ? AND keyword = ? AND item_id = ?",
                        (cookie_id, keyword, item_id))
                else:
                    # 如果没有商品ID，检查相同cookie_id、keyword且item_id为空的组合
                    self._execute_sql(cursor,
                        "SELECT COUNT(*) FROM keywords WHERE cookie_id = ? AND keyword = ? AND (item_id IS NULL OR item_id = '')",
                        (cookie_id, keyword))

                count = cursor.fetchone()[0]
                return count > 0
            except Exception as e:
                logger.error(f"检查关键词重复失败: {e}")
                return False
    def save_image_keyword(self, cookie_id: str, keyword: str, image_url: str, item_id: str = None) -> bool:
        """保存图片关键词（调用前应先检查重复）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 标准化item_id：空字符串转为NULL
                normalized_item_id = item_id if item_id and item_id.strip() else None

                # 直接插入图片关键词（重复检查应在调用前完成）
                self._execute_sql(cursor,
                    "INSERT INTO keywords (cookie_id, keyword, reply, item_id, type, image_url) VALUES (?, ?, ?, ?, ?, ?)",
                    (cookie_id, keyword, '', normalized_item_id, 'image', image_url))

                self.conn.commit()
                logger.info(f"图片关键词保存成功: {cookie_id}, 关键词: {keyword}, 图片: {image_url}")
                return True
            except Exception as e:
                logger.error(f"图片关键词保存失败: {e}")
                self.conn.rollback()
                return False
    def get_keywords_with_type(self, cookie_id: str) -> List[Dict[str, any]]:
        """获取指定Cookie的关键字列表（包含类型信息和商品名称）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                # 关联查询商品信息表，获取商品名称
                self._execute_sql(cursor,
                    """SELECT k.keyword, k.reply, k.item_id, k.type, k.image_url, i.item_title 
                    FROM keywords k 
                    LEFT JOIN item_info i ON k.item_id = i.item_id AND k.cookie_id = i.cookie_id 
                    WHERE k.cookie_id = ?""",
                    (cookie_id,))

                results = []
                for row in cursor.fetchall():
                    keyword_data = {
                        'keyword': row[0],
                        'reply': row[1],
                        'item_id': row[2],
                        'type': row[3] or 'text',  # 默认为text类型
                        'image_url': row[4],
                        'item_title': row[5]  # 添加商品名称
                    }
                    results.append(keyword_data)

                return results
            except Exception as e:
                logger.error(f"获取关键字失败: {e}")
                return []
    def update_keyword_image_url(self, cookie_id: str, keyword: str, new_image_url: str) -> bool:
        """更新关键词的图片URL"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 更新图片URL
                self._execute_sql(cursor,
                    "UPDATE keywords SET image_url = ? WHERE cookie_id = ? AND keyword = ? AND type = 'image'",
                    (new_image_url, cookie_id, keyword))

                self.conn.commit()

                # 检查是否有行被更新
                if cursor.rowcount > 0:
                    logger.info(f"关键词图片URL更新成功: {cookie_id}, 关键词: {keyword}, 新URL: {new_image_url}")
                    return True
                else:
                    logger.warning(f"未找到匹配的图片关键词: {cookie_id}, 关键词: {keyword}")
                    return False

            except Exception as e:
                logger.error(f"更新关键词图片URL失败: {e}")
                self.conn.rollback()
                return False
    def delete_keyword_by_index(self, cookie_id: str, index: int) -> bool:
        """根据索引删除关键词"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 先获取所有关键词
                self._execute_sql(cursor,
                    "SELECT rowid FROM keywords WHERE cookie_id = ? ORDER BY rowid",
                    (cookie_id,))
                rows = cursor.fetchall()

                if 0 <= index < len(rows):
                    rowid = rows[index][0]
                    self._execute_sql(cursor, "DELETE FROM keywords WHERE rowid = ?", (rowid,))
                    self.conn.commit()
                    logger.info(f"删除关键词成功: {cookie_id}, 索引: {index}")
                    return True
                else:
                    logger.warning(f"关键词索引超出范围: {index}")
                    return False

            except Exception as e:
                logger.error(f"删除关键词失败: {e}")
                self.conn.rollback()
                return False
    def get_all_keywords(self, user_id: int = None) -> Dict[str, List[Tuple[str, str]]]:
        """获取所有Cookie的关键字（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute("""
                    SELECT k.cookie_id, k.keyword, k.reply
                    FROM keywords k
                    JOIN cookies c ON k.cookie_id = c.id
                    WHERE c.user_id = ?
                    """, (user_id,))
                else:
                    self._execute_sql(cursor, "SELECT cookie_id, keyword, reply FROM keywords")

                result = {}
                for row in cursor.fetchall():
                    cookie_id, keyword, reply = row
                    if cookie_id not in result:
                        result[cookie_id] = []
                    result[cookie_id].append((keyword, reply))

                return result
            except Exception as e:
                logger.error(f"获取所有关键字失败: {e}")
                return {}
    def get_keywords_by_item_id(self, cookie_id: str, item_id: str) -> list:
        """获取指定商品的关键词列表"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if item_id:
                    self._execute_sql(cursor, """
                        SELECT k.keyword, k.reply, k.item_id, k.type, k.image_url,
                               i.item_title
                        FROM keywords k
                        LEFT JOIN item_info i ON k.item_id = i.item_id AND k.cookie_id = i.cookie_id
                        WHERE k.cookie_id = ? AND k.item_id = ?
                        ORDER BY k.rowid
                    """, (cookie_id, item_id))
                else:
                    self._execute_sql(cursor, """
                        SELECT k.keyword, k.reply, k.item_id, k.type, k.image_url,
                               NULL as item_title
                        FROM keywords k
                        WHERE k.cookie_id = ? AND (k.item_id IS NULL OR k.item_id = '')
                        ORDER BY k.rowid
                    """, (cookie_id,))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
            except Exception as e:
                logger.error(f"获取商品关键词失败: {e}")
                return []
    def save_keywords_for_item(self, cookie_id: str, item_id: str, keywords: list) -> bool:
        """保存指定商品的关键词（仅影响该 item_id 的记录）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if item_id:
                    self._execute_sql(cursor,
                        "DELETE FROM keywords WHERE cookie_id = ? AND item_id = ?",
                        (cookie_id, item_id))
                else:
                    self._execute_sql(cursor,
                        "DELETE FROM keywords WHERE cookie_id = ? AND (item_id IS NULL OR item_id = '')",
                        (cookie_id,))

                for kw in keywords:
                    kw_type = kw.get('type', 'text')
                    self._execute_sql(cursor, """
                        INSERT INTO keywords (cookie_id, keyword, reply, item_id, type, image_url)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (cookie_id, kw['keyword'], kw.get('reply', ''),
                          item_id or None, kw_type, kw.get('image_url')))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"保存商品关键词失败: {e}")
                self.conn.rollback()
                return False
    def copy_keywords_to_item(self, cookie_id: str, source_item_id: str, target_item_id: str) -> int:
        """将源商品的关键词复制到目标商品（覆盖目标商品已有关键词）"""
        try:
            source_kws = self.get_keywords_by_item_id(cookie_id, source_item_id)
            if not source_kws:
                return 0
            kw_list = [{
                'keyword': kw['keyword'],
                'reply': kw.get('reply', ''),
                'type': kw.get('type', 'text'),
                'image_url': kw.get('image_url'),
            } for kw in source_kws]
            self.save_keywords_for_item(cookie_id, target_item_id, kw_list)
            return len(kw_list)
        except Exception as e:
            logger.error(f"复制关键词失败: {e}")
            return 0