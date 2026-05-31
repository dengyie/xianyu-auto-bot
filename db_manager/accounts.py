from loguru import logger
from typing import Dict, List, Optional
from .base import DBBase

class DBAccountsMixin:
    """accounts"""

    def save_cookie(self, cookie_id: str, cookie_value: str, user_id: int = None) -> bool:
        """保存Cookie到数据库；已有记录仅更新Cookie值和用户绑定，保留其他账号字段"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                self._execute_sql(cursor, "SELECT user_id FROM cookies WHERE id = ?", (cookie_id,))
                existing = cursor.fetchone()

                # 如果没有提供user_id，优先沿用现有绑定，否则回落到admin用户
                if user_id is None:
                    if existing:
                        user_id = existing[0]
                    else:
                        self._execute_sql(cursor, "SELECT id FROM users WHERE username = 'admin'")
                        admin_user = cursor.fetchone()
                        user_id = admin_user[0] if admin_user else 1

                encrypted_cookie_value = self._encrypt_secret(cookie_value)
                if existing:
                    self._execute_sql(cursor,
                        "UPDATE cookies SET value = ?, user_id = ? WHERE id = ?",
                        (encrypted_cookie_value, user_id, cookie_id)
                    )
                    action = "更新"
                else:
                    self._execute_sql(cursor,
                        "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                        (cookie_id, encrypted_cookie_value, user_id)
                    )
                    action = "创建"

                self.conn.commit()
                logger.info(f"Cookie{action}成功: {cookie_id} (用户ID: {user_id})")

                # 验证保存结果
                self._execute_sql(cursor, "SELECT user_id FROM cookies WHERE id = ?", (cookie_id,))
                saved_user_id = cursor.fetchone()
                if saved_user_id:
                    logger.info(f"Cookie保存验证: {cookie_id} 实际绑定到用户ID: {saved_user_id[0]}")
                else:
                    logger.error(f"Cookie保存验证失败: {cookie_id} 未找到记录")
                return True
            except Exception as e:
                logger.error(f"Cookie保存失败: {e}")
                self.conn.rollback()
                return False
    def delete_cookie(self, cookie_id: str) -> bool:
        """从数据库删除Cookie及其关键字"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                # 删除关联的关键字
                self._execute_sql(cursor, "DELETE FROM keywords WHERE cookie_id = ?", (cookie_id,))
                # 删除Cookie
                self._execute_sql(cursor, "DELETE FROM cookies WHERE id = ?", (cookie_id,))
                self.conn.commit()
                logger.debug(f"Cookie删除成功: {cookie_id}")
                return True
            except Exception as e:
                logger.error(f"Cookie删除失败: {e}")
                self.conn.rollback()
                return False
    def get_cookie(self, cookie_id: str) -> Optional[str]:
        """获取指定Cookie值"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT value FROM cookies WHERE id = ?", (cookie_id,))
                result = cursor.fetchone()
                return self._decrypt_secret(result[0]) if result else None
            except Exception as e:
                logger.error(f"获取Cookie失败: {e}")
                return None
    def get_all_cookies(self, user_id: int = None) -> Dict[str, str]:
        """获取所有Cookie（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, "SELECT id, value FROM cookies WHERE user_id = ?", (user_id,))
                else:
                    self._execute_sql(cursor, "SELECT id, value FROM cookies")
                return {row[0]: self._decrypt_secret(row[1]) for row in cursor.fetchall()}
            except Exception as e:
                logger.error(f"获取所有Cookie失败: {e}")
                return {}
    def get_cookie_by_id(self, cookie_id: str) -> Optional[Dict[str, str]]:
        """根据ID获取Cookie信息

        Args:
            cookie_id: Cookie ID

        Returns:
            Dict包含cookie信息，包括cookies_str字段，如果不存在返回None
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT id, value, created_at FROM cookies WHERE id = ?", (cookie_id,))
                result = cursor.fetchone()
                if result:
                    cookie_value = self._decrypt_secret(result[1])
                    return {
                        'id': result[0],
                        'cookies_str': cookie_value,  # 使用cookies_str字段名以匹配调用方期望
                        'value': cookie_value,        # 保持向后兼容
                        'created_at': result[2]
                    }
                return None
            except Exception as e:
                logger.error(f"根据ID获取Cookie失败: {e}")
                return None
    def get_cookie_details(self, cookie_id: str) -> Optional[Dict[str, any]]:
        """获取Cookie的详细信息，包括备注、状态文案、暂停时间、账号信息和代理配置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, """
                    SELECT id, value, user_id, auto_confirm, remark, status_note,
                           qr_login_grace_until, pause_duration, username, password, show_browser, created_at,
                           proxy_type, proxy_host, proxy_port, proxy_user, proxy_pass
                    FROM cookies WHERE id = ?
                """, (cookie_id,))
                result = cursor.fetchone()
                if result:
                    cookie_value = self._decrypt_secret(result[1])
                    password = self._decrypt_secret(result[9])
                    proxy_pass = self._decrypt_secret(result[16])
                    return {
                        'id': result[0],
                        'value': cookie_value,
                        'user_id': result[2],
                        'auto_confirm': bool(result[3]),
                        'remark': result[4] or '',
                        'status_note': result[5] or '',
                        'qr_login_grace_until': int(result[6] or 0),
                        'pause_duration': result[7] if result[7] is not None else 10,  # 0是有效值，表示不暂停
                        'username': result[8] or '',
                        'password': password,
                        'show_browser': bool(result[10]) if result[10] is not None else False,
                        'created_at': result[11],
                        # 代理配置
                        'proxy_type': result[12] or 'none',
                        'proxy_host': result[13] or '',
                        'proxy_port': result[14] or 0,
                        'proxy_user': result[15] or '',
                        'proxy_pass': proxy_pass
                    }
                return None
            except Exception as e:
                logger.error(f"获取Cookie详细信息失败: {e}")
                return None
    def update_auto_confirm(self, cookie_id: str, auto_confirm: bool) -> bool:
        """更新Cookie的自动确认发货设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "UPDATE cookies SET auto_confirm = ? WHERE id = ?", (int(auto_confirm), cookie_id))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 自动确认发货设置: {'开启' if auto_confirm else '关闭'}")
                return True
            except Exception as e:
                logger.error(f"更新自动确认发货设置失败: {e}")
                return False
    def update_cookie_remark(self, cookie_id: str, remark: str) -> bool:
        """更新Cookie的备注"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "UPDATE cookies SET remark = ? WHERE id = ?", (remark, cookie_id))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 备注: {remark}")
                return True
            except Exception as e:
                logger.error(f"更新账号备注失败: {e}")
                return False
    def update_cookie_status_note(self, cookie_id: str, status_note: str) -> bool:
        """更新Cookie的状态说明文案"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "UPDATE cookies SET status_note = ? WHERE id = ?", (status_note, cookie_id))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 状态文案: {status_note}")
                return True
            except Exception as e:
                logger.error(f"更新账号状态文案失败: {e}")
                return False
    def set_cookie_qr_login_grace_until(self, cookie_id: str, grace_until: int) -> bool:
        """更新账号扫码登录稳定期截止时间"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "UPDATE cookies SET qr_login_grace_until = ? WHERE id = ?", (int(grace_until or 0), cookie_id))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 扫码稳定期截止时间: {int(grace_until or 0)}")
                return True
            except Exception as e:
                logger.error(f"更新账号扫码稳定期失败: {e}")
                return False
    def update_cookie_pause_duration(self, cookie_id: str, pause_duration: int) -> bool:
        """更新Cookie的自动回复暂停时间"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "UPDATE cookies SET pause_duration = ? WHERE id = ?", (pause_duration, cookie_id))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 自动回复暂停时间: {pause_duration}分钟")
                return True
            except Exception as e:
                logger.error(f"更新账号自动回复暂停时间失败: {e}")
                return False
    def get_cookie_pause_duration(self, cookie_id: str) -> int:
        """获取Cookie的自动回复暂停时间"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT pause_duration FROM cookies WHERE id = ?", (cookie_id,))
                result = cursor.fetchone()
                if result:
                    if result[0] is None:
                        logger.warning(f"账号 {cookie_id} 的pause_duration为NULL，使用默认值10分钟并修复数据库")
                        # 修复数据库中的NULL值
                        self._execute_sql(cursor, "UPDATE cookies SET pause_duration = 10 WHERE id = ?", (cookie_id,))
                        self.conn.commit()
                        return 10
                    return result[0]  # 返回实际值，包括0（0表示不暂停）
                else:
                    logger.warning(f"账号 {cookie_id} 未找到记录，使用默认值10分钟")
                    return 10
            except Exception as e:
                logger.error(f"获取账号自动回复暂停时间失败: {e}")
                return 10
    def update_cookie_account_info(self, cookie_id: str, cookie_value: str = None, username: str = None, password: str = None, show_browser: bool = None, user_id: int = None) -> bool:
        """更新Cookie的账号信息（包括cookie值、用户名、密码和显示浏览器设置）
        如果记录不存在，会先创建记录（需要提供cookie_value和user_id）
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 检查记录是否存在
                self._execute_sql(cursor, "SELECT id FROM cookies WHERE id = ?", (cookie_id,))
                exists = cursor.fetchone() is not None
                
                if not exists:
                    # 记录不存在，需要创建新记录
                    if cookie_value is None:
                        logger.warning(f"账号 {cookie_id} 不存在，且未提供cookie_value，无法创建新记录")
                        return False
                    
                    # 如果没有提供user_id，尝试从现有记录获取，否则使用admin用户ID
                    if user_id is None:
                        # 获取admin用户ID作为默认值
                        self._execute_sql(cursor, "SELECT id FROM users WHERE username = 'admin'")
                        admin_user = cursor.fetchone()
                        user_id = admin_user[0] if admin_user else 1
                    
                    # 构建插入语句
                    insert_fields = ['id', 'value', 'user_id']
                    insert_values = [cookie_id, self._encrypt_secret(cookie_value), user_id]
                    insert_placeholders = ['?', '?', '?']
                    
                    if username is not None:
                        insert_fields.append('username')
                        insert_values.append(username)
                        insert_placeholders.append('?')
                    
                    if password is not None:
                        insert_fields.append('password')
                        insert_values.append(self._encrypt_secret(password))
                        insert_placeholders.append('?')
                    
                    if show_browser is not None:
                        insert_fields.append('show_browser')
                        insert_values.append(1 if show_browser else 0)
                        insert_placeholders.append('?')
                    
                    sql = f"INSERT INTO cookies ({', '.join(insert_fields)}) VALUES ({', '.join(insert_placeholders)})"
                    self._execute_sql(cursor, sql, tuple(insert_values))
                    self.conn.commit()
                    logger.info(f"创建新账号 {cookie_id} 并保存信息成功: {insert_fields}")
                    return True
                else:
                    # 记录存在，执行更新
                    # 构建动态SQL更新语句
                    update_fields = []
                    params = []
                    
                    if cookie_value is not None:
                        update_fields.append("value = ?")
                        params.append(self._encrypt_secret(cookie_value))
                    
                    if username is not None:
                        update_fields.append("username = ?")
                        params.append(username)
                    
                    if password is not None:
                        update_fields.append("password = ?")
                        params.append(self._encrypt_secret(password))
                    
                    if show_browser is not None:
                        update_fields.append("show_browser = ?")
                        params.append(1 if show_browser else 0)
                    
                    if not update_fields:
                        logger.warning(f"更新账号 {cookie_id} 信息时没有提供任何更新字段")
                        return False
                    
                    params.append(cookie_id)
                    sql = f"UPDATE cookies SET {', '.join(update_fields)} WHERE id = ?"
                    
                    self._execute_sql(cursor, sql, tuple(params))
                    self.conn.commit()
                    logger.info(f"更新账号 {cookie_id} 信息成功: {update_fields}")
                    return True
            except Exception as e:
                logger.error(f"更新账号信息失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self.conn.rollback()
                return False
    def update_cookie_proxy_config(self, cookie_id: str, proxy_type: str = None, proxy_host: str = None, 
                                     proxy_port: int = None, proxy_user: str = None, proxy_pass: str = None) -> bool:
        """更新Cookie的代理配置
        
        Args:
            cookie_id: Cookie ID
            proxy_type: 代理类型 (none/http/https/socks5)
            proxy_host: 代理服务器地址
            proxy_port: 代理端口
            proxy_user: 代理认证用户名（可选）
            proxy_pass: 代理认证密码（可选）
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 检查记录是否存在
                self._execute_sql(cursor, "SELECT id FROM cookies WHERE id = ?", (cookie_id,))
                if not cursor.fetchone():
                    logger.warning(f"账号 {cookie_id} 不存在，无法更新代理配置")
                    return False
                
                # 构建动态SQL更新语句
                update_fields = []
                params = []
                
                if proxy_type is not None:
                    update_fields.append("proxy_type = ?")
                    params.append(proxy_type)
                
                if proxy_host is not None:
                    update_fields.append("proxy_host = ?")
                    params.append(proxy_host)
                
                if proxy_port is not None:
                    update_fields.append("proxy_port = ?")
                    params.append(proxy_port)
                
                if proxy_user is not None:
                    update_fields.append("proxy_user = ?")
                    params.append(proxy_user)
                
                if proxy_pass is not None:
                    update_fields.append("proxy_pass = ?")
                    params.append(self._encrypt_secret(proxy_pass))
                
                if not update_fields:
                    logger.warning(f"更新账号 {cookie_id} 代理配置时没有提供任何更新字段")
                    return False
                
                params.append(cookie_id)
                sql = f"UPDATE cookies SET {', '.join(update_fields)} WHERE id = ?"
                
                self._execute_sql(cursor, sql, tuple(params))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 代理配置成功: type={proxy_type}, host={proxy_host}, port={proxy_port}")
                return True
            except Exception as e:
                logger.error(f"更新代理配置失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self.conn.rollback()
                return False
    def get_cookie_proxy_config(self, cookie_id: str) -> Dict[str, any]:
        """获取Cookie的代理配置
        
        Returns:
            包含代理配置的字典，如果账号不存在则返回默认配置
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, """
                    SELECT proxy_type, proxy_host, proxy_port, proxy_user, proxy_pass
                    FROM cookies WHERE id = ?
                """, (cookie_id,))
                result = cursor.fetchone()
                if result:
                    return {
                        'proxy_type': result[0] or 'none',
                        'proxy_host': result[1] or '',
                        'proxy_port': result[2] or 0,
                        'proxy_user': result[3] or '',
                        'proxy_pass': self._decrypt_secret(result[4])
                    }
                # 返回默认配置
                return {
                    'proxy_type': 'none',
                    'proxy_host': '',
                    'proxy_port': 0,
                    'proxy_user': '',
                    'proxy_pass': ''
                }
            except Exception as e:
                logger.error(f"获取代理配置失败: {e}")
                return {
                    'proxy_type': 'none',
                    'proxy_host': '',
                    'proxy_port': 0,
                    'proxy_user': '',
                    'proxy_pass': ''
                }
    def get_auto_confirm(self, cookie_id: str) -> bool:
        """获取Cookie的自动确认发货设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT auto_confirm FROM cookies WHERE id = ?", (cookie_id,))
                result = cursor.fetchone()
                if result:
                    return bool(result[0])
                return True  # 默认开启
            except Exception as e:
                logger.error(f"获取自动确认发货设置失败: {e}")
                return True  # 出错时默认开启
    def get_auto_comment(self, cookie_id: str) -> bool:
        """获取Cookie的自动好评设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT auto_comment FROM cookies WHERE id = ?", (cookie_id,))
                result = cursor.fetchone()
                if result and result[0] is not None:
                    return bool(result[0])
                return False  # 默认关闭
            except Exception as e:
                logger.error(f"获取自动好评设置失败: {e}")
                return False  # 出错时默认关闭
    def update_auto_comment(self, cookie_id: str, auto_comment: bool) -> bool:
        """更新Cookie的自动好评设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "UPDATE cookies SET auto_comment = ? WHERE id = ?", (int(auto_comment), cookie_id))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 自动好评设置: {'开启' if auto_comment else '关闭'}")
                return True
            except Exception as e:
                logger.error(f"更新自动好评设置失败: {e}")
                return False
    def get_comment_templates(self, cookie_id: str) -> List[Dict]:
        """获取账号的好评模板列表"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, """
                    SELECT id, name, content, is_active, sort_order, created_at, updated_at 
                    FROM comment_templates 
                    WHERE cookie_id = ? 
                    ORDER BY sort_order, id
                """, (cookie_id,))
                results = cursor.fetchall()
                templates = []
                for row in results:
                    templates.append({
                        'id': row[0],
                        'name': row[1],
                        'content': row[2],
                        'is_active': bool(row[3]),
                        'sort_order': row[4],
                        'created_at': row[5],
                        'updated_at': row[6]
                    })
                return templates
            except Exception as e:
                logger.error(f"获取好评模板列表失败: {e}")
                return []
    def get_active_comment_template(self, cookie_id: str) -> Optional[Dict]:
        """获取账号当前激活的好评模板"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, """
                    SELECT id, name, content, is_active, sort_order, created_at, updated_at 
                    FROM comment_templates 
                    WHERE cookie_id = ? AND is_active = 1 
                    LIMIT 1
                """, (cookie_id,))
                result = cursor.fetchone()
                if result:
                    return {
                        'id': result[0],
                        'name': result[1],
                        'content': result[2],
                        'is_active': bool(result[3]),
                        'sort_order': result[4],
                        'created_at': result[5],
                        'updated_at': result[6]
                    }
                return None
            except Exception as e:
                logger.error(f"获取激活的好评模板失败: {e}")
                return None
    def add_comment_template(self, cookie_id: str, name: str, content: str, is_active: bool = False) -> Optional[int]:
        """添加好评模板"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 如果设置为激活状态，先将其他模板设为非激活
                if is_active:
                    self._execute_sql(cursor, "UPDATE comment_templates SET is_active = 0 WHERE cookie_id = ?", (cookie_id,))
                
                # 获取最大排序号
                self._execute_sql(cursor, "SELECT MAX(sort_order) FROM comment_templates WHERE cookie_id = ?", (cookie_id,))
                max_order = cursor.fetchone()[0]
                sort_order = (max_order or 0) + 1
                
                self._execute_sql(cursor, """
                    INSERT INTO comment_templates (cookie_id, name, content, is_active, sort_order) 
                    VALUES (?, ?, ?, ?, ?)
                """, (cookie_id, name, content, int(is_active), sort_order))
                
                template_id = cursor.lastrowid
                self.conn.commit()
                logger.info(f"添加好评模板成功: cookie_id={cookie_id}, name={name}, id={template_id}")
                return template_id
            except Exception as e:
                logger.error(f"添加好评模板失败: {e}")
                self.conn.rollback()
                return None
    def update_comment_template(self, template_id: int, name: str = None, content: str = None, is_active: bool = None) -> bool:
        """更新好评模板"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 获取模板所属的cookie_id
                self._execute_sql(cursor, "SELECT cookie_id FROM comment_templates WHERE id = ?", (template_id,))
                result = cursor.fetchone()
                if not result:
                    logger.warning(f"好评模板不存在: id={template_id}")
                    return False
                cookie_id = result[0]
                
                # 如果设置为激活状态，先将其他模板设为非激活
                if is_active:
                    self._execute_sql(cursor, "UPDATE comment_templates SET is_active = 0 WHERE cookie_id = ?", (cookie_id,))
                
                # 构建动态更新语句
                update_fields = []
                params = []
                
                if name is not None:
                    update_fields.append("name = ?")
                    params.append(name)
                
                if content is not None:
                    update_fields.append("content = ?")
                    params.append(content)
                
                if is_active is not None:
                    update_fields.append("is_active = ?")
                    params.append(int(is_active))
                
                if not update_fields:
                    return True
                
                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                params.append(template_id)
                
                sql = f"UPDATE comment_templates SET {', '.join(update_fields)} WHERE id = ?"
                self._execute_sql(cursor, sql, tuple(params))
                self.conn.commit()
                logger.info(f"更新好评模板成功: id={template_id}")
                return True
            except Exception as e:
                logger.error(f"更新好评模板失败: {e}")
                self.conn.rollback()
                return False
    def delete_comment_template(self, template_id: int) -> bool:
        """删除好评模板"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "DELETE FROM comment_templates WHERE id = ?", (template_id,))
                self.conn.commit()
                logger.info(f"删除好评模板成功: id={template_id}")
                return True
            except Exception as e:
                logger.error(f"删除好评模板失败: {e}")
                self.conn.rollback()
                return False
    def set_active_comment_template(self, cookie_id: str, template_id: int) -> bool:
        """设置激活的好评模板"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                # 先将所有模板设为非激活
                self._execute_sql(cursor, "UPDATE comment_templates SET is_active = 0 WHERE cookie_id = ?", (cookie_id,))
                # 设置指定模板为激活
                self._execute_sql(cursor, "UPDATE comment_templates SET is_active = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND cookie_id = ?", (template_id, cookie_id))
                self.conn.commit()
                logger.info(f"设置激活好评模板: cookie_id={cookie_id}, template_id={template_id}")
                return True
            except Exception as e:
                logger.error(f"设置激活好评模板失败: {e}")
                self.conn.rollback()
                return False
    def save_cookie_status(self, cookie_id: str, enabled: bool):
        """保存Cookie的启用状态"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO cookie_status (cookie_id, enabled, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ''', (cookie_id, enabled))
                self.conn.commit()
                logger.debug(f"保存Cookie状态: {cookie_id} -> {'启用' if enabled else '禁用'}")
            except Exception as e:
                logger.error(f"保存Cookie状态失败: {e}")
                raise
    def get_cookie_status(self, cookie_id: str) -> bool:
        """获取Cookie的启用状态"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('SELECT enabled FROM cookie_status WHERE cookie_id = ?', (cookie_id,))
                result = cursor.fetchone()
                return bool(result[0]) if result else True  # 默认启用
            except Exception as e:
                logger.error(f"获取Cookie状态失败: {e}")
                return True  # 出错时默认启用
    def get_all_cookie_status(self) -> Dict[str, bool]:
        """获取所有Cookie的启用状态"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('SELECT cookie_id, enabled FROM cookie_status')

                result = {}
                for row in cursor.fetchall():
                    cookie_id, enabled = row
                    result[cookie_id] = bool(enabled)

                return result
            except Exception as e:
                logger.error(f"获取所有Cookie状态失败: {e}")
                return {}