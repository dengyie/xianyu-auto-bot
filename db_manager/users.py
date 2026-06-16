import hashlib
import time
import sqlite3
import random
import string
import io
import base64
from PIL import Image, ImageDraw, ImageFont
from loguru import logger
from typing import Any, Dict, Optional, Tuple
from .base import DBBase
from .security import hash_user_password, is_legacy_sha256_hash, verify_password_hash


class DBUsersMixin:
    """users"""

    def export_backup(self, user_id: int = None) -> Dict[str, any]:
        """导出系统备份数据（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                backup_data = {
                    'version': '1.0',
                    'timestamp': time.time(),
                    'user_id': user_id,
                    'data': {}
                }

                if user_id is not None:
                    # 用户级备份：只备份该用户的数据
                    # 备份用户的cookies
                    self._execute_sql(cursor, "SELECT * FROM cookies WHERE user_id = ?", (user_id,))
                    columns = [description[0] for description in cursor.description]
                    rows = cursor.fetchall()
                    backup_data['data']['cookies'] = {
                        'columns': columns,
                        'rows': [list(row) for row in rows]
                    }

                    # 备份用户cookies相关的其他数据
                    user_cookie_ids = [row[0] for row in rows]  # 获取用户的cookie_id列表

                    if user_cookie_ids:
                        placeholders = ','.join(['?' for _ in user_cookie_ids])

                        # 备份关键字
                        cursor.execute(f"SELECT * FROM keywords WHERE cookie_id IN ({placeholders})", user_cookie_ids)
                        columns = [description[0] for description in cursor.description]
                        rows = cursor.fetchall()
                        backup_data['data']['keywords'] = {
                            'columns': columns,
                            'rows': [list(row) for row in rows]
                        }

                        # 备份其他相关表
                        related_tables = ['cookie_status', 'default_replies', 'message_notifications',
                                        'item_info', 'ai_reply_settings', 'ai_conversations']

                        for table in related_tables:
                            cursor.execute(f"SELECT * FROM {table} WHERE cookie_id IN ({placeholders})", user_cookie_ids)
                            columns = [description[0] for description in cursor.description]
                            rows = cursor.fetchall()
                            backup_data['data'][table] = {
                                'columns': columns,
                                'rows': [list(row) for row in rows]
                            }
                else:
                    # 系统级备份：备份所有数据
                    tables = [
                        'cookies', 'keywords', 'cookie_status', 'cards',
                        'delivery_rules', 'default_replies', 'notification_channels',
                        'message_notifications', 'system_settings', 'item_info',
                        'ai_reply_settings', 'ai_conversations', 'ai_item_cache'
                    ]

                    for table in tables:
                        cursor.execute(f"SELECT * FROM {table}")
                        columns = [description[0] for description in cursor.description]
                        rows = cursor.fetchall()

                        backup_data['data'][table] = {
                            'columns': columns,
                            'rows': [list(row) for row in rows]
                        }

                logger.info(f"导出备份成功，用户ID: {user_id}")
                return backup_data

            except Exception as e:
                logger.error(f"导出备份失败: {e}")
                raise
    def import_backup(self, backup_data: Dict[str, any], user_id: int = None) -> bool:
        """导入系统备份数据（支持用户隔离）"""
        with self.lock:
            try:
                # 验证备份数据格式
                if not isinstance(backup_data, dict) or 'data' not in backup_data:
                    raise ValueError("备份数据格式无效")

                # 开始事务
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "BEGIN TRANSACTION")

                if user_id is not None:
                    # 用户级导入：只清空该用户的数据
                    # 获取用户的cookie_id列表
                    self._execute_sql(cursor, "SELECT id FROM cookies WHERE user_id = ?", (user_id,))
                    user_cookie_ids = [row[0] for row in cursor.fetchall()]

                    if user_cookie_ids:
                        placeholders = ','.join(['?' for _ in user_cookie_ids])

                        # 删除用户相关数据
                        related_tables = ['message_notifications', 'default_replies', 'item_info',
                                        'cookie_status', 'keywords', 'ai_conversations', 'ai_reply_settings']

                        for table in related_tables:
                            cursor.execute(f"DELETE FROM {table} WHERE cookie_id IN ({placeholders})", user_cookie_ids)

                        # 删除用户的cookies
                        self._execute_sql(cursor, "DELETE FROM cookies WHERE user_id = ?", (user_id,))
                else:
                    # 系统级导入：清空所有数据（除了用户和管理员密码）
                    tables = [
                        'message_notifications', 'notification_channels', 'default_replies',
                        'delivery_rules', 'cards', 'item_info', 'cookie_status', 'keywords',
                        'ai_conversations', 'ai_reply_settings', 'ai_item_cache', 'cookies'
                    ]

                    for table in tables:
                        cursor.execute(f"DELETE FROM {table}")

                    # 清空系统设置（保留管理员密码）
                    self._execute_sql(cursor, "DELETE FROM system_settings WHERE key != 'admin_password_hash'")

                # 导入数据
                data = backup_data['data']
                for table_name, table_data in data.items():
                    if table_name not in ['cookies', 'keywords', 'cookie_status', 'cards',
                                        'delivery_rules', 'default_replies', 'notification_channels',
                                        'message_notifications', 'system_settings', 'item_info',
                                        'ai_reply_settings', 'ai_conversations', 'ai_item_cache']:
                        continue

                    columns = table_data['columns']
                    rows = table_data['rows']

                    if not rows:
                        continue

                    # 如果是用户级导入，需要确保cookies表的user_id正确
                    if user_id is not None and table_name == 'cookies':
                        # 更新所有导入的cookies的user_id
                        updated_rows = []
                        for row in rows:
                            row_dict = dict(zip(columns, row))
                            row_dict['user_id'] = user_id
                            updated_rows.append([row_dict[col] for col in columns])
                        rows = updated_rows

                    # 构建插入语句
                    placeholders = ','.join(['?' for _ in columns])

                    if table_name == 'system_settings':
                        # 系统设置需要特殊处理，避免覆盖管理员密码
                        for row in rows:
                            if len(row) >= 1 and row[0] != 'admin_password_hash':
                                cursor.execute(f"INSERT INTO {table_name} ({','.join(columns)}) VALUES ({placeholders})", row)
                    else:
                        cursor.executemany(f"INSERT INTO {table_name} ({','.join(columns)}) VALUES ({placeholders})", rows)

                # 提交事务
                self.conn.commit()
                logger.info("导入备份成功")
                return True

            except Exception as e:
                logger.error(f"导入备份失败: {e}")
                self.conn.rollback()
                return False
    def get_system_setting(self, key: str) -> Optional[str]:
        """获取系统设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT value FROM system_settings WHERE key = ?", (key,))
                result = cursor.fetchone()
                return result[0] if result else None
            except Exception as e:
                logger.error(f"获取系统设置失败: {e}")
                return None
    def set_system_setting(self, key: str, value: str, description: str = None) -> bool:
        """设置系统设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO system_settings (key, value, description, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ''', (key, value, description))
                self.conn.commit()
                logger.debug(f"设置系统设置: {key}")
                return True
            except Exception as e:
                logger.error(f"设置系统设置失败: {e}")
                self.conn.rollback()
                return False
    def get_all_system_settings(self) -> Dict[str, str]:
        """获取所有系统设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT key, value FROM system_settings")

                settings = {}
                for row in cursor.fetchall():
                    settings[row[0]] = row[1]

                return settings
            except Exception as e:
                logger.error(f"获取所有系统设置失败: {e}")
                return {}
    def create_user(self, username: str, email: str, password: str) -> bool:
        """创建新用户"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                password_hash = hash_user_password(password)

                cursor.execute('''
                INSERT INTO users (username, email, password_hash)
                VALUES (?, ?, ?)
                ''', (username, email, password_hash))

                self.conn.commit()
                logger.info(f"创建用户成功: {username} ({email})")
                return True
            except sqlite3.IntegrityError as e:
                logger.error(f"创建用户失败，用户名或邮箱已存在: {e}")
                self.conn.rollback()
                return False
            except Exception as e:
                logger.error(f"创建用户失败: {e}")
                self.conn.rollback()
                return False
    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """根据用户名获取用户信息"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                # 检查is_admin列是否存在
                cursor.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in cursor.fetchall()]
                has_is_admin = 'is_admin' in columns

                if has_is_admin:
                    cursor.execute('''
                    SELECT id, username, email, password_hash, is_active, created_at, updated_at, is_admin
                    FROM users WHERE username = ?
                    ''', (username,))
                else:
                    cursor.execute('''
                    SELECT id, username, email, password_hash, is_active, created_at, updated_at
                    FROM users WHERE username = ?
                    ''', (username,))

                row = cursor.fetchone()
                if row:
                    user_data = {
                        'id': row[0],
                        'username': row[1],
                        'email': row[2],
                        'password_hash': row[3],
                        'is_active': row[4],
                        'created_at': row[5],
                        'updated_at': row[6],
                    }
                    if has_is_admin:
                        user_data['is_admin'] = bool(row[7]) if row[7] is not None else (row[1] == 'admin')
                    else:
                        user_data['is_admin'] = (row[1] == 'admin')
                    return user_data
                return None
            except Exception as e:
                logger.error(f"获取用户信息失败: {e}")
                return None
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """根据邮箱获取用户信息"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                # 检查is_admin列是否存在
                cursor.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in cursor.fetchall()]
                has_is_admin = 'is_admin' in columns

                if has_is_admin:
                    cursor.execute('''
                    SELECT id, username, email, password_hash, is_active, created_at, updated_at, is_admin
                    FROM users WHERE email = ?
                    ''', (email,))
                else:
                    cursor.execute('''
                    SELECT id, username, email, password_hash, is_active, created_at, updated_at
                    FROM users WHERE email = ?
                    ''', (email,))

                row = cursor.fetchone()
                if row:
                    user_data = {
                        'id': row[0],
                        'username': row[1],
                        'email': row[2],
                        'password_hash': row[3],
                        'is_active': row[4],
                        'created_at': row[5],
                        'updated_at': row[6],
                    }
                    if has_is_admin:
                        user_data['is_admin'] = bool(row[7]) if row[7] is not None else (row[1] == 'admin')
                    else:
                        user_data['is_admin'] = (row[1] == 'admin')
                    return user_data
                return None
            except Exception as e:
                logger.error(f"获取用户信息失败: {e}")
                return None
    def verify_user_password(self, username: str, password: str) -> bool:
        """验证用户密码"""
        user = self.get_user_by_username(username)
        if not user:
            return False

        stored_hash = user.get('password_hash')
        password_valid = verify_password_hash(password, stored_hash)
        if password_valid and is_legacy_sha256_hash(stored_hash):
            self.update_user_password(username, password)
            logger.info(f"用户 {username} 密码哈希已升级为bcrypt")
        return password_valid and user['is_active']
    def update_user_password(self, username: str, new_password: str) -> bool:
        """更新用户密码"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                password_hash = hash_user_password(new_password)

                cursor.execute('''
                UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
                WHERE username = ?
                ''', (password_hash, username))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"用户 {username} 密码更新成功")
                    return True
                else:
                    logger.warning(f"用户 {username} 不存在，密码更新失败")
                    return False

            except Exception as e:
                logger.error(f"更新用户密码失败: {e}")
                self.conn.rollback()
                return False
    def generate_verification_code(self) -> str:
        """生成6位数字验证码"""
        return ''.join(random.choices(string.digits, k=6))
    def generate_captcha(self) -> Tuple[str, str]:
        """生成图形验证码
        返回: (验证码文本, base64编码的图片)
        """
        try:
            # 生成4位随机验证码（数字+字母）
            chars = string.ascii_uppercase + string.digits
            captcha_text = ''.join(random.choices(chars, k=4))

            # 创建图片
            width, height = 120, 40
            image = Image.new('RGB', (width, height), color='white')
            draw = ImageDraw.Draw(image)

            # 尝试使用系统字体，如果失败则使用默认字体
            try:
                # Windows系统字体
                font = ImageFont.truetype("arial.ttf", 20)
            except:
                try:
                    # 备用字体
                    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20)
                except:
                    # 使用默认字体
                    font = ImageFont.load_default()

            # 绘制验证码文本
            for i, char in enumerate(captcha_text):
                # 随机颜色
                color = (
                    random.randint(0, 100),
                    random.randint(0, 100),
                    random.randint(0, 100)
                )

                # 随机位置（稍微偏移）
                x = 20 + i * 20 + random.randint(-3, 3)
                y = 8 + random.randint(-3, 3)

                draw.text((x, y), char, font=font, fill=color)

            # 添加干扰线
            for _ in range(3):
                start = (random.randint(0, width), random.randint(0, height))
                end = (random.randint(0, width), random.randint(0, height))
                draw.line([start, end], fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)), width=1)

            # 添加干扰点
            for _ in range(20):
                x = random.randint(0, width)
                y = random.randint(0, height)
                draw.point((x, y), fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))

            # 转换为base64
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            img_base64 = base64.b64encode(buffer.getvalue()).decode()

            return captcha_text, f"data:image/png;base64,{img_base64}"

        except Exception as e:
            logger.error(f"生成图形验证码失败: {e}")
            # 返回简单的文本验证码作为备用
            simple_code = ''.join(random.choices(string.digits, k=4))
            return simple_code, ""
    def save_captcha(self, session_id: str, captcha_text: str, expires_minutes: int = 5) -> bool:
        """保存图形验证码"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                expires_at = time.time() + (expires_minutes * 60)

                # 删除该session的旧验证码
                cursor.execute('DELETE FROM captcha_codes WHERE session_id = ?', (session_id,))

                cursor.execute('''
                INSERT INTO captcha_codes (session_id, code, expires_at)
                VALUES (?, ?, ?)
                ''', (session_id, captcha_text.upper(), expires_at))

                self.conn.commit()
                logger.debug(f"保存图形验证码成功: {session_id}")
                return True
            except Exception as e:
                logger.error(f"保存图形验证码失败: {e}")
                self.conn.rollback()
                return False
    def verify_captcha(self, session_id: str, user_input: str) -> bool:
        """验证图形验证码"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                current_time = time.time()

                # 查找有效的验证码
                cursor.execute('''
                SELECT id FROM captcha_codes
                WHERE session_id = ? AND code = ? AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
                ''', (session_id, user_input.upper(), current_time))

                row = cursor.fetchone()
                if row:
                    # 删除已使用的验证码
                    cursor.execute('DELETE FROM captcha_codes WHERE id = ?', (row[0],))
                    self.conn.commit()
                    logger.debug(f"图形验证码验证成功: {session_id}")
                    return True
                else:
                    logger.warning(f"图形验证码验证失败: {session_id} - {user_input}")
                    return False
            except Exception as e:
                logger.error(f"验证图形验证码失败: {e}")
                return False
    def save_verification_code(self, email: str, code: str, code_type: str = 'register', expires_minutes: int = 10) -> bool:
        """保存邮箱验证码"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                expires_at = time.time() + (expires_minutes * 60)

                cursor.execute('''
                INSERT INTO email_verifications (email, code, type, expires_at)
                VALUES (?, ?, ?, ?)
                ''', (email, code, code_type, expires_at))

                self.conn.commit()
                logger.info(f"保存验证码成功: {email} ({code_type})")
                return True
            except Exception as e:
                logger.error(f"保存验证码失败: {e}")
                self.conn.rollback()
                return False
    def verify_email_code(self, email: str, code: str, code_type: str = 'register') -> bool:
        """验证邮箱验证码"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                current_time = time.time()

                # 查找有效的验证码
                cursor.execute('''
                SELECT id FROM email_verifications
                WHERE email = ? AND code = ? AND type = ? AND expires_at > ? AND used = FALSE
                ORDER BY created_at DESC LIMIT 1
                ''', (email, code, code_type, current_time))

                row = cursor.fetchone()
                if row:
                    # 标记验证码为已使用
                    cursor.execute('''
                    UPDATE email_verifications SET used = TRUE WHERE id = ?
                    ''', (row[0],))
                    self.conn.commit()
                    logger.info(f"验证码验证成功: {email} ({code_type})")
                    return True
                else:
                    logger.warning(f"验证码验证失败: {email} - {code} ({code_type})")
                    return False
            except Exception as e:
                logger.error(f"验证邮箱验证码失败: {e}")
                return False
    async def send_verification_email(self, email: str, code: str) -> bool:
        """发送验证码邮件（支持SMTP和API两种方式）"""
        try:
            subject = "闲鱼管理系统 - 邮箱验证码"
            # 使用简单的纯文本邮件内容
            text_content = f"""【闲鱼管理系统】邮箱验证码

您好！

感谢您使用闲鱼管理系统。为了确保账户安全，请使用以下验证码完成邮箱验证：

验证码：{code}

重要提醒：
• 验证码有效期为 10 分钟，请及时使用
• 请勿将验证码分享给任何人
• 如非本人操作，请忽略此邮件
• 系统不会主动索要您的验证码

感谢您选择闲鱼管理系统！

---
此邮件由系统自动发送，请勿直接回复
© 2026 闲鱼管理系统"""

            # 从系统设置读取SMTP配置
            try:
                smtp_server = self.get_system_setting('smtp_server') or ''
                smtp_port = int(self.get_system_setting('smtp_port') or 0)
                smtp_user = self.get_system_setting('smtp_user') or ''
                smtp_password = self.get_system_setting('smtp_password') or ''
                smtp_from = (self.get_system_setting('smtp_from') or '').strip() or smtp_user
                smtp_use_tls = (self.get_system_setting('smtp_use_tls') or 'true').lower() == 'true'
                smtp_use_ssl = (self.get_system_setting('smtp_use_ssl') or 'false').lower() == 'true'
            except Exception as e:
                logger.error(f"读取SMTP系统设置失败: {e}")
                # 如果读取配置失败，使用API方式
                return await self._send_email_via_api(email, subject, text_content)

            # 检查SMTP配置是否完整
            if smtp_server and smtp_port and smtp_user and smtp_password:
                # 配置完整，使用SMTP方式发送
                logger.info(f"使用SMTP方式发送验证码邮件: {email}")
                return await self._send_email_via_smtp(email, subject, text_content,
                                                     smtp_server, smtp_port, smtp_user,
                                                     smtp_password, smtp_from, smtp_use_tls, smtp_use_ssl)
            else:
                # 配置不完整，使用API方式发送
                logger.info(f"SMTP配置不完整，使用API方式发送验证码邮件: {email}")
                return await self._send_email_via_api(email, subject, text_content)

        except Exception as e:
            logger.error(f"发送验证码邮件异常: {e}")
            return False
    async def _send_email_via_smtp(self, email: str, subject: str, text_content: str,
                                 smtp_server: str, smtp_port: int, smtp_user: str,
                                 smtp_password: str, smtp_from: str, smtp_use_tls: bool, smtp_use_ssl: bool) -> bool:
        """使用SMTP方式发送邮件"""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg['Subject'] = subject
            msg['From'] = smtp_from
            msg['To'] = email

            msg.attach(MIMEText(text_content, 'plain', 'utf-8'))

            if smtp_use_ssl:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port)

            server.ehlo()
            if smtp_use_tls and not smtp_use_ssl:
                server.starttls()
                server.ehlo()

            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [email], msg.as_string())
            server.quit()

            logger.info(f"验证码邮件发送成功(SMTP): {email}")
            return True
        except Exception as e:
            logger.error(f"SMTP发送验证码邮件失败: {e}")
            # SMTP发送失败，尝试使用API方式
            logger.info(f"SMTP发送失败，尝试使用API方式发送: {email}")
            return await self._send_email_via_api(email, subject, text_content)
    async def _send_email_via_api(self, email: str, subject: str, text_content: str) -> bool:
        """使用API方式发送邮件"""
        try:
            import aiohttp

            # 邮件 API 地址：从系统设置读取，未配置则拒绝调用以避免向未知第三方泄露
            api_url = (self.get_system_setting('verification_email_api_url') or '').strip()
            if not api_url:
                logger.warning(f"未配置 verification_email_api_url，无法通过 API 渠道发送验证码邮件: {email}")
                return False
            params = {
                'subject': subject,
                'receiveUser': email,
                'sendHtml': text_content
            }

            async with aiohttp.ClientSession() as session:
                try:
                    logger.info(f"使用API发送验证码邮件: {email}")
                    async with session.get(api_url, params=params, timeout=15) as response:
                        response_text = await response.text()
                        logger.info(f"邮件API响应: {response.status}")

                        if response.status == 200:
                            logger.info(f"验证码邮件发送成功(API): {email}")
                            return True
                        else:
                            logger.error(f"API发送验证码邮件失败: {email}, 状态码: {response.status}, 响应: {response_text[:200]}")
                            return False
                except Exception as e:
                    logger.error(f"API邮件发送异常: {email}, 错误: {e}")
                    return False
        except Exception as e:
            logger.error(f"API邮件发送方法异常: {e}")
            return False
    def get_user_settings(self, user_id: int):
        """获取用户的所有设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT key, value, description, updated_at
                FROM user_settings
                WHERE user_id = ?
                ORDER BY key
                ''', (user_id,))

                settings = {}
                for row in cursor.fetchall():
                    settings[row[0]] = {
                        'value': row[1],
                        'description': row[2],
                        'updated_at': row[3]
                    }

                return settings
            except Exception as e:
                logger.error(f"获取用户设置失败: {e}")
                return {}
    def get_user_setting(self, user_id: int, key: str):
        """获取用户的特定设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT value, description, updated_at
                FROM user_settings
                WHERE user_id = ? AND key = ?
                ''', (user_id, key))

                row = cursor.fetchone()
                if row:
                    return {
                        'key': key,
                        'value': row[0],
                        'description': row[1],
                        'updated_at': row[2]
                    }
                return None
            except Exception as e:
                logger.error(f"获取用户设置失败: {e}")
                return None
    def set_user_setting(self, user_id: int, key: str, value: str, description: str = None):
        """设置用户配置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO user_settings (user_id, key, value, description, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (user_id, key, value, description))

                self.conn.commit()
                logger.info(f"用户设置更新成功: user_id={user_id}, key={key}")
                return True
            except Exception as e:
                logger.error(f"设置用户配置失败: {e}")
                self.conn.rollback()
                return False
    def get_all_users(self):
        """获取所有用户信息（管理员专用）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                # 检查is_admin列是否存在
                cursor.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in cursor.fetchall()]
                has_is_admin = 'is_admin' in columns

                if has_is_admin:
                    cursor.execute('''
                    SELECT id, username, email, created_at, updated_at, is_admin
                    FROM users
                    ORDER BY created_at DESC
                    ''')
                else:
                    cursor.execute('''
                    SELECT id, username, email, created_at, updated_at
                    FROM users
                    ORDER BY created_at DESC
                    ''')

                users = []
                for row in cursor.fetchall():
                    user_data = {
                        'id': row[0],
                        'username': row[1],
                        'email': row[2],
                        'created_at': row[3],
                        'updated_at': row[4],
                    }
                    # 设置is_admin: 如果有该列则使用，否则admin用户名默认为管理员
                    if has_is_admin:
                        user_data['is_admin'] = bool(row[5]) if row[5] is not None else (row[1] == 'admin')
                    else:
                        user_data['is_admin'] = (row[1] == 'admin')
                    users.append(user_data)

                return users
            except Exception as e:
                logger.error(f"获取所有用户失败: {e}")
                return []
    def get_user_by_id(self, user_id: int):
        """根据ID获取用户信息"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                # 检查is_admin列是否存在
                cursor.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in cursor.fetchall()]
                has_is_admin = 'is_admin' in columns

                if has_is_admin:
                    cursor.execute('''
                    SELECT id, username, email, created_at, updated_at, is_admin
                    FROM users
                    WHERE id = ?
                    ''', (user_id,))
                else:
                    cursor.execute('''
                    SELECT id, username, email, created_at, updated_at
                    FROM users
                    WHERE id = ?
                    ''', (user_id,))

                row = cursor.fetchone()
                if row:
                    user_data = {
                        'id': row[0],
                        'username': row[1],
                        'email': row[2],
                        'created_at': row[3],
                        'updated_at': row[4],
                    }
                    if has_is_admin:
                        user_data['is_admin'] = bool(row[5]) if row[5] is not None else (row[1] == 'admin')
                    else:
                        user_data['is_admin'] = (row[1] == 'admin')
                    return user_data
                return None
            except Exception as e:
                logger.error(f"获取用户信息失败: {e}")
                return None
    def update_user_admin_status(self, user_id: int, is_admin: bool) -> bool:
        """更新用户管理员状态"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                UPDATE users SET is_admin = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''', (1 if is_admin else 0, user_id))

                self.conn.commit()
                logger.info(f"用户管理员状态更新成功: user_id={user_id}, is_admin={is_admin}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"更新用户管理员状态失败: {e}")
                self.conn.rollback()
                return False
    def delete_user_and_data(self, user_id: int):
        """删除用户及其所有相关数据"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 开始事务
                cursor.execute('BEGIN TRANSACTION')

                # 删除用户相关的所有数据
                # 1. 删除用户设置
                cursor.execute('DELETE FROM user_settings WHERE user_id = ?', (user_id,))

                # 2. 删除用户的卡券
                cursor.execute('DELETE FROM cards WHERE user_id = ?', (user_id,))

                # 3. 删除用户的发货规则
                cursor.execute('DELETE FROM delivery_rules WHERE user_id = ?', (user_id,))

                # 4. 删除用户的通知渠道
                cursor.execute('DELETE FROM notification_channels WHERE user_id = ?', (user_id,))

                # 5. 删除用户的Cookie
                cursor.execute('DELETE FROM cookies WHERE user_id = ?', (user_id,))

                # 6. 删除用户的关键字
                cursor.execute('DELETE FROM keywords WHERE cookie_id IN (SELECT id FROM cookies WHERE user_id = ?)', (user_id,))

                # 7. 删除用户的默认回复
                cursor.execute('DELETE FROM default_replies WHERE cookie_id IN (SELECT id FROM cookies WHERE user_id = ?)', (user_id,))

                # 8. 删除用户的AI回复设置
                cursor.execute('DELETE FROM ai_reply_settings WHERE cookie_id IN (SELECT id FROM cookies WHERE user_id = ?)', (user_id,))

                # 9. 删除用户的消息通知
                cursor.execute('DELETE FROM message_notifications WHERE cookie_id IN (SELECT id FROM cookies WHERE user_id = ?)', (user_id,))

                # 10. 最后删除用户本身
                cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))

                # 提交事务
                cursor.execute('COMMIT')

                logger.info(f"用户及相关数据删除成功: user_id={user_id}")
                return True

            except Exception as e:
                # 回滚事务
                cursor.execute('ROLLBACK')
                logger.error(f"删除用户及相关数据失败: {e}")
                return False
