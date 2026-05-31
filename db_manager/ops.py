import json
import re
import hashlib
from urllib.parse import urlparse, parse_qs
from loguru import logger
from typing import Any, Dict, List, Optional, Tuple
from .base import DBBase

class DBOpsMixin:
    """ops"""

    def save_ai_reply_settings(self, cookie_id: str, settings: dict) -> bool:
        """保存AI回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO ai_reply_settings
                (cookie_id, ai_enabled, model_name, api_key, base_url, api_type,
                 max_discount_percent, max_discount_amount, max_bargain_rounds,
                 custom_prompts, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (
                    cookie_id,
                    settings.get('ai_enabled', False),
                    settings.get('model_name', 'qwen-plus'),
                    settings.get('api_key', ''),
                    settings.get('base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1'),
                    settings.get('api_type', ''),
                    settings.get('max_discount_percent', 10),
                    settings.get('max_discount_amount', 100),
                    settings.get('max_bargain_rounds', 3),
                    settings.get('custom_prompts', '')
                ))
                self.conn.commit()
                logger.debug(f"AI回复设置保存成功: {cookie_id}")
                return True
            except Exception as e:
                logger.error(f"保存AI回复设置失败: {e}")
                self.conn.rollback()
                return False
    def get_ai_reply_settings(self, cookie_id: str) -> dict:
        """获取AI回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT ai_enabled, model_name, api_key, base_url, api_type,
                       max_discount_percent, max_discount_amount, max_bargain_rounds,
                       custom_prompts
                FROM ai_reply_settings WHERE cookie_id = ?
                ''', (cookie_id,))

                result = cursor.fetchone()
                if result:
                    return {
                        'ai_enabled': bool(result[0]),
                        'model_name': result[1],
                        'api_key': result[2],
                        'base_url': result[3],
                        'api_type': result[4] or '',
                        'max_discount_percent': result[5],
                        'max_discount_amount': result[6],
                        'max_bargain_rounds': result[7],
                        'custom_prompts': result[8]
                    }
                else:
                    # 返回默认设置
                    return {
                        'ai_enabled': False,
                        'model_name': 'qwen-plus',
                        'api_key': '',
                        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                        'api_type': '',
                        'max_discount_percent': 10,
                        'max_discount_amount': 100,
                        'max_bargain_rounds': 3,
                        'custom_prompts': ''
                    }
            except Exception as e:
                logger.error(f"获取AI回复设置失败: {e}")
                return {
                    'ai_enabled': False,
                    'model_name': 'qwen-plus',
                    'api_key': '',
                    'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                    'api_type': '',
                    'max_discount_percent': 10,
                    'max_discount_amount': 100,
                    'max_bargain_rounds': 3,
                    'custom_prompts': ''
                }
    def get_all_ai_reply_settings(self) -> Dict[str, dict]:
        """获取所有账号的AI回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT cookie_id, ai_enabled, model_name, api_key, base_url, api_type,
                       max_discount_percent, max_discount_amount, max_bargain_rounds,
                       custom_prompts
                FROM ai_reply_settings
                ''')

                result = {}
                for row in cursor.fetchall():
                    cookie_id = row[0]
                    result[cookie_id] = {
                        'ai_enabled': bool(row[1]),
                        'model_name': row[2],
                        'api_key': row[3],
                        'base_url': row[4],
                        'api_type': row[5] or '',
                        'max_discount_percent': row[6],
                        'max_discount_amount': row[7],
                        'max_bargain_rounds': row[8],
                        'custom_prompts': row[9]
                    }

                return result
            except Exception as e:
                logger.error(f"获取所有AI回复设置失败: {e}")
                return {}
    def save_ai_config_preset(self, user_id: int, preset_name: str, model_name: str, api_key: str = '', base_url: str = '', api_type: str = '') -> int:
        """保存AI配置预设（存在则更新）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT INTO ai_config_presets (user_id, preset_name, model_name, api_key, base_url, api_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, preset_name) DO UPDATE SET
                    model_name = excluded.model_name,
                    api_key = excluded.api_key,
                    base_url = excluded.base_url,
                    api_type = excluded.api_type,
                    updated_at = CURRENT_TIMESTAMP
                ''', (user_id, preset_name, model_name, api_key, base_url, api_type))
                self.conn.commit()
                preset_id = cursor.lastrowid
                logger.debug(f"保存AI配置预设: user_id={user_id}, preset_name={preset_name}")
                return preset_id
            except Exception as e:
                logger.error(f"保存AI配置预设失败: {e}")
                raise
    def get_ai_config_presets(self, user_id: int) -> list:
        """获取用户的所有AI配置预设"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT id, preset_name, model_name, api_key, base_url, api_type, created_at, updated_at
                FROM ai_config_presets
                WHERE user_id = ?
                ORDER BY updated_at DESC
                ''', (user_id,))
                presets = []
                for row in cursor.fetchall():
                    presets.append({
                        'id': row[0],
                        'preset_name': row[1],
                        'model_name': row[2],
                        'api_key': row[3],
                        'base_url': row[4],
                        'api_type': row[5] or '',
                        'created_at': row[6],
                        'updated_at': row[7]
                    })
                return presets
            except Exception as e:
                logger.error(f"获取AI配置预设失败: {e}")
                return []
    def delete_ai_config_preset(self, user_id: int, preset_id: int) -> bool:
        """删除AI配置预设（带user_id校验）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                DELETE FROM ai_config_presets WHERE id = ? AND user_id = ?
                ''', (preset_id, user_id))
                self.conn.commit()
                deleted = cursor.rowcount > 0
                if deleted:
                    logger.debug(f"删除AI配置预设: preset_id={preset_id}, user_id={user_id}")
                return deleted
            except Exception as e:
                logger.error(f"删除AI配置预设失败: {e}")
                return False
    def save_default_reply(self, cookie_id: str, enabled: bool, reply_content: str = None, reply_once: bool = False):
        """保存默认回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO default_replies (cookie_id, enabled, reply_content, reply_once, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (cookie_id, enabled, reply_content, reply_once))
                self.conn.commit()
                logger.debug(f"保存默认回复设置: {cookie_id} -> {'启用' if enabled else '禁用'}, 只回复一次: {'是' if reply_once else '否'}")
            except Exception as e:
                logger.error(f"保存默认回复设置失败: {e}")
                raise
    def get_default_reply(self, cookie_id: str) -> Optional[Dict[str, any]]:
        """获取指定账号的默认回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT enabled, reply_content, reply_once FROM default_replies WHERE cookie_id = ?
                ''', (cookie_id,))
                result = cursor.fetchone()
                if result:
                    enabled, reply_content, reply_once = result
                    return {
                        'enabled': bool(enabled),
                        'reply_content': reply_content or '',
                        'reply_once': bool(reply_once) if reply_once is not None else False
                    }
                return None
            except Exception as e:
                logger.error(f"获取默认回复设置失败: {e}")
                return None
    def get_all_default_replies(self) -> Dict[str, Dict[str, any]]:
        """获取所有账号的默认回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('SELECT cookie_id, enabled, reply_content, reply_once FROM default_replies')

                result = {}
                for row in cursor.fetchall():
                    cookie_id, enabled, reply_content, reply_once = row
                    result[cookie_id] = {
                        'enabled': bool(enabled),
                        'reply_content': reply_content or '',
                        'reply_once': bool(reply_once) if reply_once is not None else False
                    }

                return result
            except Exception as e:
                logger.error(f"获取所有默认回复设置失败: {e}")
                return {}
    def add_default_reply_record(self, cookie_id: str, chat_id: str):
        """记录已回复的chat_id"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR IGNORE INTO default_reply_records (cookie_id, chat_id)
                VALUES (?, ?)
                ''', (cookie_id, chat_id))
                self.conn.commit()
                logger.debug(f"记录默认回复: {cookie_id} -> {chat_id}")
            except Exception as e:
                logger.error(f"记录默认回复失败: {e}")
    def has_default_reply_record(self, cookie_id: str, chat_id: str) -> bool:
        """检查是否已经回复过该chat_id"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT 1 FROM default_reply_records WHERE cookie_id = ? AND chat_id = ?
                ''', (cookie_id, chat_id))
                result = cursor.fetchone()
                return result is not None
            except Exception as e:
                logger.error(f"检查默认回复记录失败: {e}")
                return False
    def clear_default_reply_records(self, cookie_id: str):
        """清空指定账号的默认回复记录"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('DELETE FROM default_reply_records WHERE cookie_id = ?', (cookie_id,))
                self.conn.commit()
                logger.debug(f"清空默认回复记录: {cookie_id}")
            except Exception as e:
                logger.error(f"清空默认回复记录失败: {e}")
    def delete_default_reply(self, cookie_id: str) -> bool:
        """删除指定账号的默认回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "DELETE FROM default_replies WHERE cookie_id = ?", (cookie_id,))
                self.conn.commit()
                logger.debug(f"删除默认回复设置: {cookie_id}")
                return True
            except Exception as e:
                logger.error(f"删除默认回复设置失败: {e}")
                self.conn.rollback()
                return False
    def create_notification_channel(self, name: str, channel_type: str, config: str, user_id: int = None) -> int:
        """创建通知渠道"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT INTO notification_channels (name, type, config, user_id)
                VALUES (?, ?, ?, ?)
                ''', (name, channel_type, config, user_id))
                self.conn.commit()
                channel_id = cursor.lastrowid
                logger.debug(f"创建通知渠道: {name} (ID: {channel_id})")
                return channel_id
            except Exception as e:
                logger.error(f"创建通知渠道失败: {e}")
                self.conn.rollback()
                raise
    def get_notification_channels(self, user_id: int = None) -> List[Dict[str, any]]:
        """获取所有通知渠道"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute('''
                    SELECT id, name, type, config, enabled, created_at, updated_at
                    FROM notification_channels
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    ''', (user_id,))
                else:
                    cursor.execute('''
                    SELECT id, name, type, config, enabled, created_at, updated_at
                    FROM notification_channels
                    ORDER BY created_at DESC
                    ''')

                channels = []
                for row in cursor.fetchall():
                    channels.append({
                        'id': row[0],
                        'name': row[1],
                        'type': row[2],
                        'config': row[3],
                        'enabled': bool(row[4]),
                        'created_at': row[5],
                        'updated_at': row[6]
                    })

                return channels
            except Exception as e:
                logger.error(f"获取通知渠道失败: {e}")
                return []
    def get_notification_channel(self, channel_id: int, user_id: int = None) -> Optional[Dict[str, any]]:
        """获取指定通知渠道"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute('''
                    SELECT id, name, type, config, enabled, created_at, updated_at, user_id
                    FROM notification_channels WHERE id = ? AND user_id = ?
                    ''', (channel_id, user_id))
                else:
                    cursor.execute('''
                    SELECT id, name, type, config, enabled, created_at, updated_at, user_id
                    FROM notification_channels WHERE id = ?
                    ''', (channel_id,))

                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'name': row[1],
                        'type': row[2],
                        'config': row[3],
                        'enabled': bool(row[4]),
                        'created_at': row[5],
                        'updated_at': row[6],
                        'user_id': row[7]
                    }
                return None
            except Exception as e:
                logger.error(f"获取通知渠道失败: {e}")
                return None
    def update_notification_channel(self, channel_id: int, name: str, config: str, enabled: bool = True, user_id: int = None) -> bool:
        """更新通知渠道"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute('''
                    UPDATE notification_channels
                    SET name = ?, config = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND user_id = ?
                    ''', (name, config, enabled, channel_id, user_id))
                else:
                    cursor.execute('''
                    UPDATE notification_channels
                    SET name = ?, config = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''', (name, config, enabled, channel_id))
                self.conn.commit()
                logger.debug(f"更新通知渠道: {channel_id}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"更新通知渠道失败: {e}")
                self.conn.rollback()
                return False
    def delete_notification_channel(self, channel_id: int, user_id: int = None) -> bool:
        """删除通知渠道"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, "DELETE FROM notification_channels WHERE id = ? AND user_id = ?", (channel_id, user_id))
                else:
                    self._execute_sql(cursor, "DELETE FROM notification_channels WHERE id = ?", (channel_id,))
                self.conn.commit()
                logger.debug(f"删除通知渠道: {channel_id}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"删除通知渠道失败: {e}")
                self.conn.rollback()
                return False
    def set_message_notification(self, cookie_id: str, channel_id: int, enabled: bool = True) -> bool:
        """设置账号的消息通知"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO message_notifications (cookie_id, channel_id, enabled)
                VALUES (?, ?, ?)
                ''', (cookie_id, channel_id, enabled))
                self.conn.commit()
                logger.debug(f"设置消息通知: {cookie_id} -> {channel_id}")
                return True
            except Exception as e:
                logger.error(f"设置消息通知失败: {e}")
                self.conn.rollback()
                return False
    def get_account_notifications(self, cookie_id: str) -> List[Dict[str, any]]:
        """获取账号的通知配置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT mn.id, mn.channel_id, mn.enabled, nc.name, nc.type, nc.config
                FROM message_notifications mn
                JOIN notification_channels nc ON mn.channel_id = nc.id
                JOIN cookies c ON mn.cookie_id = c.id
                WHERE mn.cookie_id = ? AND nc.enabled = 1 AND nc.user_id = c.user_id
                ORDER BY mn.id
                ''', (cookie_id,))

                notifications = []
                for row in cursor.fetchall():
                    notifications.append({
                        'id': row[0],
                        'channel_id': row[1],
                        'enabled': bool(row[2]),
                        'channel_name': row[3],
                        'channel_type': row[4],
                        'channel_config': row[5]
                    })

                return notifications
            except Exception as e:
                logger.error(f"获取账号通知配置失败: {e}")
                return []
    def get_all_message_notifications(self) -> Dict[str, List[Dict[str, any]]]:
        """获取所有账号的通知配置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT mn.cookie_id, mn.id, mn.channel_id, mn.enabled, nc.name, nc.type, nc.config
                FROM message_notifications mn
                JOIN notification_channels nc ON mn.channel_id = nc.id
                JOIN cookies c ON mn.cookie_id = c.id
                WHERE nc.enabled = 1 AND nc.user_id = c.user_id
                ORDER BY mn.cookie_id, mn.id
                ''')

                result = {}
                for row in cursor.fetchall():
                    cookie_id = row[0]
                    if cookie_id not in result:
                        result[cookie_id] = []

                    result[cookie_id].append({
                        'id': row[1],
                        'channel_id': row[2],
                        'enabled': bool(row[3]),
                        'channel_name': row[4],
                        'channel_type': row[5],
                        'channel_config': row[6]
                    })

                return result
            except Exception as e:
                logger.error(f"获取所有消息通知配置失败: {e}")
                return {}
    def delete_message_notification(self, notification_id: int, user_id: int = None) -> bool:
        """删除消息通知配置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, '''
                    DELETE FROM message_notifications
                    WHERE id = ? AND channel_id IN (
                        SELECT id FROM notification_channels WHERE user_id = ?
                    )
                    ''', (notification_id, user_id))
                else:
                    self._execute_sql(cursor, "DELETE FROM message_notifications WHERE id = ?", (notification_id,))
                self.conn.commit()
                logger.debug(f"删除消息通知配置: {notification_id}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"删除消息通知配置失败: {e}")
                self.conn.rollback()
                return False
    def delete_account_notifications(self, cookie_id: str, user_id: int = None) -> bool:
        """删除账号的所有消息通知配置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, '''
                    DELETE FROM message_notifications
                    WHERE cookie_id = ? AND cookie_id IN (
                        SELECT id FROM cookies WHERE user_id = ?
                    )
                    ''', (cookie_id, user_id))
                else:
                    self._execute_sql(cursor, "DELETE FROM message_notifications WHERE cookie_id = ?", (cookie_id,))
                self.conn.commit()
                logger.debug(f"删除账号通知配置: {cookie_id}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"删除账号通知配置失败: {e}")
                self.conn.rollback()
                return False
    def get_all_notification_templates(self) -> List[Dict[str, any]]:
        """获取所有通知模板"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT id, type, template, created_at, updated_at
                FROM notification_templates
                ORDER BY id
                ''')

                templates = []
                for row in cursor.fetchall():
                    templates.append({
                        'id': row[0],
                        'type': row[1],
                        'template': row[2],
                        'created_at': row[3],
                        'updated_at': row[4]
                    })

                return templates
            except Exception as e:
                logger.error(f"获取通知模板失败: {e}")
                return []
    def get_notification_template(self, template_type: str) -> Optional[Dict[str, any]]:
        """获取指定类型的通知模板"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT id, type, template, created_at, updated_at
                FROM notification_templates
                WHERE type = ?
                ''', (template_type,))

                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'type': row[1],
                        'template': row[2],
                        'created_at': row[3],
                        'updated_at': row[4]
                    }
                return None
            except Exception as e:
                logger.error(f"获取通知模板失败: {e}")
                return None
    def update_notification_template(self, template_type: str, template: str) -> bool:
        """更新通知模板"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, '''
                UPDATE notification_templates
                SET template = ?, updated_at = CURRENT_TIMESTAMP
                WHERE type = ?
                ''', (template, template_type))
                self.conn.commit()
                logger.info(f"更新通知模板: {template_type}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"更新通知模板失败: {e}")
                self.conn.rollback()
                return False
    def reset_notification_template(self, template_type: str) -> bool:
        """重置通知模板为默认值"""
        default_templates = {
            'message': '''🚨 接收消息通知

账号: {account_id}
买家: {buyer_name} (ID: {buyer_id})
商品ID: {item_id}
聊天ID: {chat_id}
消息内容: {message}

时间: {time}''',
            'token_refresh': '''Token刷新异常

账号ID: {account_id}
异常时间: {time}
异常信息: {error_message}

请检查账号Cookie是否过期，如有需要请及时更新Cookie配置。''',
            'delivery': '''🚨 自动发货通知

账号: {account_id}
买家: {buyer_name} (ID: {buyer_id})
商品ID: {item_id}
聊天ID: {chat_id}
结果: {result}
时间: {time}

请及时处理！''',
            'slider_success': '''✅ 滑块验证成功，{status_text}

账号: {account_id}
时间: {time}''',
            'face_verify': '''⚠️ 需要{verification_type} 🚫
在验证期间，发货及自动回复暂时无法使用。

{verification_action}
{verification_url}

账号: {account_id}
时间: {time}''',
            'password_login_success': '''✅ 密码登录成功

账号: {account_id}
时间: {time}
Cookie数量: {cookie_count}

账号Cookie已更新，正在重启服务...''',
            'cookie_refresh_success': '''✅ 刷新Cookie成功

账号: {account_id}
时间: {time}
Cookie数量: {cookie_count}

账号已可正常使用。''',
            'account_paused': '''🚫 账号已暂停

账号: {account_id}
状态: {status_note}
原因: {pause_reason}
时间: {time}

说明: {error_message}
验证入口: {verification_url}

{action_hint}'''
        }

        if template_type not in default_templates:
            logger.error(f"未知的模板类型: {template_type}")
            return False

        return self.update_notification_template(template_type, default_templates[template_type])
    def get_default_notification_template(self, template_type: str) -> Optional[str]:
        """获取默认通知模板"""
        default_templates = {
            'message': '''🚨 接收消息通知

账号: {account_id}
买家: {buyer_name} (ID: {buyer_id})
商品ID: {item_id}
聊天ID: {chat_id}
消息内容: {message}

时间: {time}''',
            'token_refresh': '''Token刷新异常

账号ID: {account_id}
异常时间: {time}
异常信息: {error_message}

请检查账号Cookie是否过期，如有需要请及时更新Cookie配置。''',
            'delivery': '''🚨 自动发货通知

账号: {account_id}
买家: {buyer_name} (ID: {buyer_id})
商品ID: {item_id}
聊天ID: {chat_id}
结果: {result}
时间: {time}

请及时处理！''',
            'slider_success': '''✅ 滑块验证成功，{status_text}

账号: {account_id}
时间: {time}''',
            'face_verify': '''⚠️ 需要{verification_type} 🚫
在验证期间，发货及自动回复暂时无法使用。

{verification_action}
{verification_url}

账号: {account_id}
时间: {time}''',
            'password_login_success': '''✅ 密码登录成功

账号: {account_id}
时间: {time}
Cookie数量: {cookie_count}

账号Cookie已更新，正在重启服务...''',
            'cookie_refresh_success': '''✅ 刷新Cookie成功

账号: {account_id}
时间: {time}
Cookie数量: {cookie_count}

账号已可正常使用。''',
            'account_paused': '''🚫 账号已暂停

账号: {account_id}
状态: {status_note}
原因: {pause_reason}
时间: {time}

说明: {error_message}
验证入口: {verification_url}

{action_hint}'''
        }

        return default_templates.get(template_type)
    def _serialize_risk_control_event_meta(self, event_meta: Any) -> Optional[str]:
        if event_meta is None:
            return None
        if isinstance(event_meta, str):
            text = event_meta.strip()
            return text or None
        try:
            return json.dumps(event_meta, ensure_ascii=False, sort_keys=True)
        except Exception as e:
            logger.warning(f"序列化风控日志event_meta失败: {e}")
            return None
    def _decode_risk_control_event_meta(self, event_meta: Any) -> Optional[Any]:
        if event_meta is None:
            return None
        if isinstance(event_meta, (dict, list)):
            return event_meta
        if not isinstance(event_meta, str):
            return None
        text = event_meta.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return text
    def _extract_legacy_risk_duration_ms(self, *values: Any) -> Optional[int]:
        duration_pattern = re.compile(r'耗时[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*秒')
        for value in values:
            text = str(value or '').strip()
            if not text:
                continue
            match = duration_pattern.search(text)
            if not match:
                continue
            try:
                return max(0, int(float(match.group(1)) * 1000))
            except Exception:
                continue
        return None
    def _extract_legacy_verification_url(self, *values: Any) -> Optional[str]:
        url_pattern = re.compile(r'https?://\S+')
        for value in values:
            text = str(value or '').strip()
            if not text:
                continue
            match = url_pattern.search(text)
            if match:
                return match.group(0).rstrip('),，。；;')
        return None
    def _build_legacy_verification_meta(self, verification_url: str = None) -> Optional[Dict[str, Any]]:
        text = str(verification_url or '').strip()
        if not text:
            return None

        try:
            parsed = urlparse(text)
            if not parsed.scheme and not parsed.netloc:
                return {'verification_source': text[:120]}

            meta: Dict[str, Any] = {
                'verification_host': parsed.netloc or None,
                'verification_path': parsed.path or None,
            }
            query = parse_qs(parsed.query or '')
            x5secdata = query.get('x5secdata', [None])[0]
            if x5secdata:
                meta['verification_token_hash'] = hashlib.sha256(x5secdata.encode('utf-8')).hexdigest()[:16]
            action = query.get('action', [None])[0]
            if action:
                meta['verification_action'] = action
            step = query.get('x5step', [None])[0]
            if step:
                meta['verification_step'] = step
            return {key: value for key, value in meta.items() if value is not None}
        except Exception:
            return {'verification_source': text[:120]}
    def _infer_legacy_risk_trigger_scene(self, log_info: Dict[str, Any]) -> Optional[str]:
        existing = str(log_info.get('trigger_scene') or '').strip()
        if existing:
            return existing

        event_type = str(log_info.get('event_type') or '').strip()
        description = str(log_info.get('event_description') or '').strip()
        processing_result = str(log_info.get('processing_result') or '').strip()
        error_message = str(log_info.get('error_message') or '').strip()
        combined_text = ' '.join(part for part in (description, processing_result, error_message) if part)
        lower_text = combined_text.lower()

        if '手动触发账密cookie刷新' in description or '账密登录方式' in description:
            return 'manual_password_refresh'
        if '手动触发扫码cookie刷新' in description:
            return 'manual_qr_refresh'
        if '扫码登录获取真实cookie' in description:
            return 'qr_login'

        if event_type in {'face_verify', 'sms_verify', 'qr_verify', 'unknown', 'password_error'}:
            return 'password_login'

        if '连续失败5次' in description or '关键api不可用' in lower_text or 'cookie验证失败' in description:
            return 'auto_cookie_refresh'

        if 'token刷新' in combined_text or '令牌' in combined_text or 'session过期' in lower_text or 'token' in lower_text:
            return 'token_refresh'

        if event_type == 'cookie_refresh':
            return 'auto_cookie_refresh'

        return None
    def _get_risk_trigger_scene_label(self, trigger_scene: Optional[str]) -> Optional[str]:
        scene = str(trigger_scene or '').strip()
        if not scene:
            return None
        scene_labels = {
            'token_refresh': 'Token刷新',
            'auto_cookie_refresh': '自动Cookie刷新',
            'manual_password_refresh': '手动账密刷新',
            'manual_qr_refresh': '手动扫码刷新',
            'password_login': '密码登录',
            'qr_login': '扫码登录',
        }
        return scene_labels.get(scene, scene)
    def _compact_legacy_risk_description(self, log_info: Dict[str, Any]) -> str:
        description = str(log_info.get('event_description') or '').strip()
        if not description:
            return ''

        event_type = str(log_info.get('event_type') or '').strip()
        trigger_scene = self._get_risk_trigger_scene_label(log_info.get('trigger_scene'))
        lower_description = description.lower()

        if event_type == 'slider_captcha' and ('滑块验证' in description or 'url:' in lower_description):
            return f"检测到滑块验证（{trigger_scene}）" if trigger_scene else '检测到滑块验证'

        if event_type == 'token_expired':
            if 'session过期' in lower_description:
                return '检测到Session过期'
            if '令牌过期' in description:
                return '检测到令牌过期'
            return '检测到令牌/Session过期'

        if event_type == 'cookie_refresh':
            replacements = {
                '手动触发Cookie刷新（账密登录方式）': '手动触发账密Cookie刷新',
                '手动触发Cookie刷新（扫码登录方式）': '手动触发扫码Cookie刷新',
                '令牌/Session过期触发Cookie刷新和实例重启': '令牌/Session过期触发Cookie刷新',
                '连续失败5次触发Cookie刷新和实例重启': '连续失败5次触发Cookie刷新',
                'Cookie验证失败(关键API不可用)触发Cookie刷新和实例重启': 'Cookie验证失败（关键API不可用）触发Cookie刷新',
                '滑块成功后Token预热失败触发Cookie刷新和实例重启': '滑块成功后Token预热失败，触发Cookie刷新',
            }
            if description in replacements:
                return replacements[description]

        compacted = re.sub(r'[，,]?\s*URL[:：]\s*https?://\S+', '', description, flags=re.IGNORECASE)
        compacted = re.sub(r'https?://\S+', '', compacted)
        compacted = compacted.replace('准备刷新Cookie并重启实例', '准备刷新Cookie')
        compacted = compacted.replace('触发Cookie刷新和实例重启', '触发Cookie刷新')
        compacted = compacted.replace('  ', ' ')
        compacted = compacted.strip(' ，,;；')
        return compacted or description
    def _compact_legacy_risk_processing_result(self, log_info: Dict[str, Any]) -> str:
        processing_result = str(log_info.get('processing_result') or '').strip()
        if not processing_result:
            return ''

        event_type = str(log_info.get('event_type') or '').strip()
        error_message = str(log_info.get('error_message') or '').strip()
        lower_result = processing_result.lower()

        if event_type == 'slider_captcha':
            if '滑块验证成功' in processing_result:
                return '滑块验证成功，已获取新Cookie'

            reason_match = re.search(r'原因[:：]\s*(.+)$', processing_result)
            if reason_match:
                reason = reason_match.group(1).strip(' ，,;；')
                if '未获取到新cookies' in reason or '未获取到新cookie' in reason.lower():
                    reason = '未获取到新Cookie'
                elif '触发闲鱼风控验证' in reason:
                    reason = '触发闲鱼风控验证'
                return f'滑块验证失败（{reason}）'

            if '触发闲鱼风控验证' in processing_result or '触发闲鱼风控验证' in error_message:
                return '滑块验证失败（触发闲鱼风控验证）'

        if event_type == 'cookie_refresh':
            if '扫码登录真实Cookie获取成功，账号任务已启动' in processing_result:
                if 'Token预热未完成' in processing_result:
                    return '真实Cookie获取成功，Token预热待重试'
                return '真实Cookie获取成功，账号任务已启动'

            cookie_refresh_result_map = {
                'Cookie刷新成功': 'Cookie刷新成功',
                '扫码登录真实Cookie获取成功，但未切换到新任务': '真实Cookie获取成功，但未切换到新任务',
                '密码登录刷新Cookie成功，实例已重启': '密码登录刷新Cookie成功，实例已重启',
            }
            if processing_result in cookie_refresh_result_map:
                return cookie_refresh_result_map[processing_result]

        compacted = re.sub(r'[，,]\s*耗时[:：]\s*[0-9]+(?:\.[0-9]+)?\s*秒', '', processing_result)
        compacted = re.sub(r'[，,]\s*cookies?长度[:：]?\s*\d+', '', compacted, flags=re.IGNORECASE)
        compacted = compacted.replace('未获取到新cookies', '未获取到新Cookie')
        compacted = compacted.replace('未获取到新cookie', '未获取到新Cookie')
        compacted = compacted.replace('  ', ' ')
        compacted = compacted.strip(' ，,;；')
        return compacted or processing_result
    def _compact_legacy_risk_error_message(self, log_info: Dict[str, Any]) -> str:
        error_message = str(log_info.get('error_message') or '').strip()
        if not error_message:
            return ''

        compact_mappings = {
            "cannot access local variable 'is_refresh_mode' where it is not associated with a value": '账密刷新流程变量异常',
            '真实Cookie已获取，但首次Token初始化失败，未切换到新的账号任务': '真实Cookie已获取，但首次Token初始化失败',
            '当前登录页被风控拦截，出现前置滑块，请稍后重试': '当前登录页被风控拦截',
        }
        if error_message in compact_mappings:
            return compact_mappings[error_message]

        if 'No space left on device' in error_message:
            return '磁盘空间不足'

        if '触发闲鱼风控验证' in error_message:
            return '触发闲鱼风控验证'

        if error_message.startswith('触发场景:') and 'URL:' in error_message:
            if '密码登录' in error_message:
                return '密码登录触发验证'
            if '扫码登录' in error_message:
                return '扫码登录触发验证'
            return '触发身份验证'

        if error_message.startswith('滑块验证失败：'):
            reason = error_message.split('：', 1)[1].strip()
            return f'滑块验证失败（{reason}）' if reason else '滑块验证失败'

        compacted = re.sub(r'[，,]?\s*URL[:：]\s*https?://\S+', '', error_message, flags=re.IGNORECASE)
        compacted = re.sub(r'https?://\S+', '', compacted)
        compacted = compacted.replace('  ', ' ')
        compacted = compacted.strip(' ，,;；')
        return compacted or error_message
    def _normalize_legacy_risk_log(self, log_info: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(log_info)
        session_id = str(normalized.get('session_id') or '').strip()
        trigger_scene = str(normalized.get('trigger_scene') or '').strip()
        result_code = str(normalized.get('result_code') or '').strip()
        raw_meta = normalized.get('event_meta')
        duration_ms = normalized.get('duration_ms')

        is_legacy = not any([session_id, trigger_scene, result_code, raw_meta, duration_ms])

        inferred_trigger_scene = self._infer_legacy_risk_trigger_scene(normalized)
        if inferred_trigger_scene and not trigger_scene:
            normalized['trigger_scene'] = inferred_trigger_scene

        if duration_ms in (None, ''):
            inferred_duration_ms = self._extract_legacy_risk_duration_ms(
                normalized.get('processing_result'),
                normalized.get('error_message'),
                normalized.get('event_description'),
            )
            if inferred_duration_ms is not None:
                normalized['duration_ms'] = inferred_duration_ms

        if not raw_meta:
            verification_url = self._extract_legacy_verification_url(
                normalized.get('event_description'),
                normalized.get('error_message'),
            )
            legacy_meta = self._build_legacy_verification_meta(verification_url)
            if legacy_meta:
                legacy_meta['legacy_record'] = True
                if normalized.get('trigger_scene'):
                    legacy_meta['trigger_scene'] = normalized.get('trigger_scene')
                normalized['event_meta'] = legacy_meta
        elif isinstance(raw_meta, dict) and is_legacy:
            legacy_meta = dict(raw_meta)
            legacy_meta.setdefault('legacy_record', True)
            if normalized.get('trigger_scene'):
                legacy_meta.setdefault('trigger_scene', normalized.get('trigger_scene'))
            normalized['event_meta'] = legacy_meta

        normalized['event_description_display'] = self._compact_legacy_risk_description(normalized) or normalized.get('event_description') or '-'
        if is_legacy:
            normalized['processing_result_display'] = self._compact_legacy_risk_processing_result(normalized) or normalized.get('processing_result') or ''
            normalized['error_message_display'] = self._compact_legacy_risk_error_message(normalized) or normalized.get('error_message') or ''
        else:
            normalized['processing_result_display'] = normalized.get('processing_result') or ''
            normalized['error_message_display'] = normalized.get('error_message') or ''
        normalized['is_legacy'] = is_legacy
        normalized['session_display'] = session_id or ('历史记录' if is_legacy else '--')
        return normalized
    def _normalize_risk_log_datetime_param(self, value: Any, end_of_day: bool = False) -> Optional[str]:
        text = str(value or '').strip()
        if not text:
            return None
        if len(text) == 10 and text.count('-') == 2:
            suffix = '23:59:59' if end_of_day else '00:00:00'
            return f"{text} {suffix}"
        return text[:19]
    def _build_risk_control_log_filters(
        self,
        alias: str = '',
        cookie_id: str = None,
        processing_status: str = None,
        event_type: str = None,
        trigger_scene: str = None,
        session_id: str = None,
        result_code: str = None,
        date_from: str = None,
        date_to: str = None,
    ) -> Tuple[List[str], List[Any]]:
        prefix = ''
        if alias:
            prefix = alias if alias.endswith('.') else f"{alias}."

        conditions: List[str] = []
        params: List[Any] = []

        filter_specs = [
            ('cookie_id', cookie_id),
            ('processing_status', processing_status),
            ('event_type', event_type),
            ('trigger_scene', trigger_scene),
            ('session_id', session_id),
            ('result_code', result_code),
        ]
        for column_name, raw_value in filter_specs:
            value = str(raw_value or '').strip()
            if not value:
                continue
            conditions.append(f"{prefix}{column_name} = ?")
            params.append(value)

        normalized_from = self._normalize_risk_log_datetime_param(date_from, end_of_day=False)
        if normalized_from:
            conditions.append(f"datetime({prefix}created_at) >= datetime(?)")
            params.append(normalized_from)

        normalized_to = self._normalize_risk_log_datetime_param(date_to, end_of_day=True)
        if normalized_to:
            conditions.append(f"datetime({prefix}created_at) <= datetime(?)")
            params.append(normalized_to)

        return conditions, params
    def add_risk_control_log(self, cookie_id: str, event_type: str = 'slider_captcha',
                           event_description: str = None, processing_result: str = None,
                           processing_status: str = 'processing', error_message: str = None,
                           session_id: str = None, trigger_scene: str = None,
                           result_code: str = None, event_meta: Any = None,
                           duration_ms: Optional[int] = None):
        """
        添加风控日志记录

        Args:
            cookie_id: Cookie ID
            event_type: 事件类型，默认为'slider_captcha'
            event_description: 事件描述
            processing_result: 处理结果
            processing_status: 处理状态 ('processing', 'success', 'failed')
            error_message: 错误信息
            session_id: 事件链路ID
            trigger_scene: 触发场景
            result_code: 结果代码
            event_meta: 结构化扩展信息
            duration_ms: 处理耗时（毫秒）

        Returns:
            int or None: 添加成功返回日志ID，失败返回None
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                    INSERT INTO risk_control_logs
                    (cookie_id, event_type, session_id, trigger_scene, result_code, event_description,
                     event_meta, processing_result, processing_status, error_message, duration_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    cookie_id,
                    event_type,
                    session_id,
                    trigger_scene,
                    result_code,
                    event_description,
                    self._serialize_risk_control_event_meta(event_meta),
                    processing_result,
                    processing_status,
                    error_message,
                    int(duration_ms) if duration_ms is not None else None,
                ))
                self.conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"添加风控日志失败: {e}")
            return None
    def update_risk_control_log(self, log_id: int, event_description: str = None,
                              processing_result: str = None, processing_status: str = None,
                              error_message: str = None, session_id: str = None,
                              trigger_scene: str = None, result_code: str = None,
                              event_meta: Any = None, duration_ms: Optional[int] = None) -> bool:
        """
        更新风控日志记录

        Args:
            log_id: 日志ID
            event_description: 事件描述
            processing_result: 处理结果
            processing_status: 处理状态
            error_message: 错误信息
            session_id: 事件链路ID
            trigger_scene: 触发场景
            result_code: 结果代码
            event_meta: 结构化扩展信息
            duration_ms: 处理耗时（毫秒）

        Returns:
            bool: 更新成功返回True，失败返回False
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()

                # 构建更新语句
                update_fields = []
                params = []

                if event_description is not None:
                    update_fields.append("event_description = ?")
                    params.append(event_description)

                if processing_result is not None:
                    update_fields.append("processing_result = ?")
                    params.append(processing_result)

                if processing_status is not None:
                    update_fields.append("processing_status = ?")
                    params.append(processing_status)

                if error_message is not None:
                    update_fields.append("error_message = ?")
                    params.append(error_message)

                if session_id is not None:
                    update_fields.append("session_id = ?")
                    params.append(session_id)

                if trigger_scene is not None:
                    update_fields.append("trigger_scene = ?")
                    params.append(trigger_scene)

                if result_code is not None:
                    update_fields.append("result_code = ?")
                    params.append(result_code)

                if event_meta is not None:
                    update_fields.append("event_meta = ?")
                    params.append(self._serialize_risk_control_event_meta(event_meta))

                if duration_ms is not None:
                    update_fields.append("duration_ms = ?")
                    params.append(int(duration_ms))

                if update_fields:
                    update_fields.append("updated_at = CURRENT_TIMESTAMP")
                    params.append(log_id)

                    sql = f"UPDATE risk_control_logs SET {', '.join(update_fields)} WHERE id = ?"
                    cursor.execute(sql, params)
                    self.conn.commit()
                    return cursor.rowcount > 0

                return False
        except Exception as e:
            logger.error(f"更新风控日志失败: {e}")
            return False
    def get_risk_control_logs(self, cookie_id: str = None, processing_status: str = None,
                              event_type: str = None, trigger_scene: str = None,
                              session_id: str = None, result_code: str = None,
                              date_from: str = None, date_to: str = None,
                              limit: int = 100, offset: int = 0) -> List[Dict]:
        """
        获取风控日志列表

        Args:
            cookie_id: Cookie ID，为None时获取所有日志
            processing_status: 处理状态，为None时不过滤状态
            event_type: 事件类型
            trigger_scene: 触发场景
            session_id: 事件链路ID
            result_code: 结果代码
            date_from: 开始时间
            date_to: 结束时间
            limit: 限制返回数量
            offset: 偏移量

        Returns:
            List[Dict]: 风控日志列表
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()

                query = '''
                    SELECT r.*, c.id as cookie_name
                    FROM risk_control_logs r
                    LEFT JOIN cookies c ON r.cookie_id = c.id
                '''
                conditions, params = self._build_risk_control_log_filters(
                    alias='r',
                    cookie_id=cookie_id,
                    processing_status=processing_status,
                    event_type=event_type,
                    trigger_scene=trigger_scene,
                    session_id=session_id,
                    result_code=result_code,
                    date_from=date_from,
                    date_to=date_to,
                )

                if conditions:
                    query += ' WHERE ' + ' AND '.join(conditions)

                query += ' ORDER BY datetime(COALESCE(r.updated_at, r.created_at)) DESC, r.id DESC LIMIT ? OFFSET ?'
                params.extend([limit, offset])
                cursor.execute(query, params)

                columns = [description[0] for description in cursor.description]
                logs = []

                for row in cursor.fetchall():
                    log_info = dict(zip(columns, row))
                    log_info['event_meta'] = self._decode_risk_control_event_meta(log_info.get('event_meta'))
                    logs.append(self._normalize_legacy_risk_log(log_info))

                return logs
        except Exception as e:
            logger.error(f"获取风控日志失败: {e}")
            return []
    def get_risk_control_logs_count(self, cookie_id: str = None, processing_status: str = None,
                                    event_type: str = None, trigger_scene: str = None,
                                    session_id: str = None, result_code: str = None,
                                    date_from: str = None, date_to: str = None) -> int:
        """
        获取风控日志总数

        Args:
            cookie_id: Cookie ID，为None时获取所有日志数量
            processing_status: 处理状态，为None时不过滤状态
            event_type: 事件类型
            trigger_scene: 触发场景
            session_id: 事件链路ID
            result_code: 结果代码
            date_from: 开始时间
            date_to: 结束时间

        Returns:
            int: 日志总数
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()

                query = 'SELECT COUNT(*) FROM risk_control_logs'
                conditions, params = self._build_risk_control_log_filters(
                    cookie_id=cookie_id,
                    processing_status=processing_status,
                    event_type=event_type,
                    trigger_scene=trigger_scene,
                    session_id=session_id,
                    result_code=result_code,
                    date_from=date_from,
                    date_to=date_to,
                )

                if conditions:
                    query += ' WHERE ' + ' AND '.join(conditions)

                cursor.execute(query, params)

                return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"获取风控日志数量失败: {e}")
            return 0
    def get_slider_verification_session_stats(self, cookie_ids: Optional[List[str]] = None, range_key: str = 'all') -> Dict[str, Any]:
        """获取滑块验证会话级统计数据。"""
        empty_stats = {
            'has_data': False,
            'total_sessions': 0,
            'total_attempts': 0,
            'success_count': 0,
            'failure_count': 0,
            'processing_count': 0,
            'completed_sessions': 0,
            'success_rate': 0.0,
            'recent_success': None,
            'recent_failure': None,
            'accounts_with_sessions': 0,
            'accounts_with_failures': 0,
            'stats_mode': 'session',
            'summary_text': '暂无滑块验证记录',
            'selected_range': 'all',
            'range_label': '所有',
        }

        def _normalize_cookie_ids(values: Optional[List[str]]) -> Optional[List[str]]:
            if values is None:
                return None
            normalized = []
            for value in values:
                text = str(value or '').strip()
                if text:
                    normalized.append(text)
            return normalized

        def _format_datetime_text(value: Any) -> Optional[str]:
            if not isinstance(value, str):
                return None
            text = value.strip()
            if not text:
                return None
            return text[:16]

        def _normalize_range(value: Any) -> str:
            text = str(value or '').strip().lower()
            if text in {'today', '7d', 'all'}:
                return text
            return 'all'

        def _build_range_filter(value: str) -> Tuple[List[str], List[Any], str]:
            normalized = _normalize_range(value)
            label_map = {
                'today': '当日',
                '7d': '近 7 天',
                'all': '所有',
            }
            if normalized == 'all':
                return [], [], label_map[normalized]

            beijing_tz = timezone(timedelta(hours=8))
            now_local = datetime.now(beijing_tz)
            days_back = 0 if normalized == 'today' else 6
            start_local = (now_local - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
            start_utc = start_local.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            return ["datetime(created_at) >= datetime(?)"], [start_utc], label_map[normalized]

        try:
            normalized_cookie_ids = _normalize_cookie_ids(cookie_ids)
            normalized_range = _normalize_range(range_key)
            if cookie_ids is not None and not normalized_cookie_ids:
                empty_result = dict(empty_stats)
                empty_result.update({
                    'selected_range': normalized_range,
                    'range_label': _build_range_filter(normalized_range)[2],
                })
                return empty_result

            with self.lock:
                cursor = self.conn.cursor()

                scope_conditions: List[str] = []
                scope_params: List[Any] = []

                if normalized_cookie_ids is not None:
                    placeholders = ', '.join(['?'] * len(normalized_cookie_ids))
                    scope_conditions.append(f"cookie_id IN ({placeholders})")
                    scope_params.extend(normalized_cookie_ids)

                range_conditions, range_params, range_label = _build_range_filter(normalized_range)
                scope_conditions.extend(range_conditions)
                scope_params.extend(range_params)

                where_clause = ''
                if scope_conditions:
                    where_clause = ' WHERE ' + ' AND '.join(scope_conditions)

                cursor.execute(
                    f'''
                    SELECT
                        COALESCE(SUM(CASE WHEN event_type = 'slider_captcha' AND processing_status = 'success' THEN 1 ELSE 0 END), 0) AS success_count,
                        COALESCE(SUM(CASE WHEN ((event_type = 'slider_captcha' AND processing_status = 'failed') OR result_code = 'password_login_slider_failed') THEN 1 ELSE 0 END), 0) AS failure_count,
                        COALESCE(SUM(CASE WHEN event_type = 'slider_captcha' AND processing_status = 'processing' THEN 1 ELSE 0 END), 0) AS processing_count,
                        COUNT(DISTINCT CASE WHEN (event_type = 'slider_captcha' OR result_code = 'password_login_slider_failed') THEN cookie_id END) AS accounts_with_sessions
                    FROM risk_control_logs
                    {where_clause}
                    ''',
                    scope_params,
                )
                row = cursor.fetchone() or (0, 0, 0, 0)

                success_count = int(row[0] or 0)
                failure_count = int(row[1] or 0)
                processing_count = int(row[2] or 0)
                accounts_with_sessions = int(row[3] or 0)
                completed_sessions = success_count + failure_count
                total_sessions = completed_sessions + processing_count
                success_rate = round((success_count / completed_sessions) * 100, 1) if completed_sessions > 0 else 0.0

                def _fetch_recent_datetime(extra_condition: str, extra_params: List[Any]) -> Optional[str]:
                    conditions = list(scope_conditions)
                    params = list(scope_params)
                    conditions.append(extra_condition)
                    params.extend(extra_params)
                    recent_where = ' WHERE ' + ' AND '.join(conditions)

                    cursor.execute(
                        f'''
                        SELECT COALESCE(updated_at, created_at)
                        FROM risk_control_logs
                        {recent_where}
                        ORDER BY datetime(COALESCE(updated_at, created_at)) DESC, id DESC
                        LIMIT 1
                        ''',
                        params,
                    )
                    row = cursor.fetchone()
                    return _format_datetime_text(row[0] if row else None)

                if total_sessions > 0:
                    if normalized_range == 'all':
                        summary_text = '已包含全部时间的滑块成功/失败，并将账密刷新中的滑块失败计入失败次数'
                    else:
                        summary_text = f'已按{range_label}范围统计滑块成功/失败，并将账密刷新中的滑块失败计入失败次数'
                else:
                    summary_text = '暂无滑块验证记录' if normalized_range == 'all' else f'{range_label}暂无滑块验证记录'

                return {
                    'has_data': total_sessions > 0,
                    'total_sessions': total_sessions,
                    'total_attempts': total_sessions,
                    'success_count': success_count,
                    'failure_count': failure_count,
                    'processing_count': processing_count,
                    'completed_sessions': completed_sessions,
                    'success_rate': success_rate,
                    'recent_success': _fetch_recent_datetime("event_type = ? AND processing_status = ?", ['slider_captcha', 'success']),
                    'recent_failure': _fetch_recent_datetime("((event_type = ? AND processing_status = ?) OR result_code = ?)", ['slider_captcha', 'failed', 'password_login_slider_failed']),
                    'accounts_with_sessions': accounts_with_sessions,
                    'accounts_with_failures': accounts_with_sessions,
                    'stats_mode': 'session',
                    'summary_text': summary_text,
                    'selected_range': normalized_range,
                    'range_label': range_label,
                }
        except Exception as e:
            logger.error(f"获取滑块验证统计失败: {e}")
            empty_result = dict(empty_stats)
            normalized_range = str(range_key or '').strip().lower()
            if normalized_range in {'today', '7d'}:
                empty_result.update({
                    'selected_range': normalized_range,
                    'range_label': '当日' if normalized_range == 'today' else '近 7 天',
                    'summary_text': '当日暂无滑块验证记录' if normalized_range == 'today' else '近 7 天暂无滑块验证记录',
                })
            return empty_result
    def delete_risk_control_log(self, log_id: int) -> bool:
        """
        删除风控日志记录

        Args:
            log_id: 日志ID

        Returns:
            bool: 删除成功返回True，失败返回False
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('DELETE FROM risk_control_logs WHERE id = ?', (log_id,))
                self.conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除风控日志失败: {e}")
            return False
    def mark_stale_risk_control_logs_failed(self, timeout_minutes: int = 15, cookie_id: str = None) -> int:
        """将超时仍为processing的风控日志标记为failed

        Args:
            timeout_minutes: 超时分钟数
            cookie_id: 可选，指定cookie_id范围

        Returns:
            int: 更新的记录数
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()

                if cookie_id:
                    cursor.execute(
                        '''
                        UPDATE risk_control_logs
                        SET
                            processing_status = 'failed',
                            error_message = COALESCE(error_message, ?),
                            processing_result = COALESCE(processing_result, ?),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE processing_status = 'processing'
                          AND cookie_id = ?
                          AND datetime(created_at) <= datetime('now', '-' || ? || ' minutes')
                        ''',
                        (
                            f'处理超时（>{timeout_minutes}分钟），系统自动关闭',
                            '处理超时，自动标记失败',
                            cookie_id,
                            timeout_minutes
                        )
                    )
                else:
                    cursor.execute(
                        '''
                        UPDATE risk_control_logs
                        SET
                            processing_status = 'failed',
                            error_message = COALESCE(error_message, ?),
                            processing_result = COALESCE(processing_result, ?),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE processing_status = 'processing'
                          AND datetime(created_at) <= datetime('now', '-' || ? || ' minutes')
                        ''',
                        (
                            f'处理超时（>{timeout_minutes}分钟），系统自动关闭',
                            '处理超时，自动标记失败',
                            timeout_minutes
                        )
                    )

                self.conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.error(f"标记超时风控日志失败: {e}")
            return 0
    def calculate_next_daily_run(self, run_hour, random_delay_max=10, include_today=True):
        """计算每日定时任务的下次运行时间"""
        from datetime import datetime, timedelta
        import random

        now = datetime.now()
        safe_hour = max(0, min(23, int(run_hour)))
        safe_random_max = max(0, int(random_delay_max or 0))
        random_min = random.randint(0, safe_random_max) if safe_random_max > 0 else 0

        next_run = now.replace(hour=safe_hour, minute=random_min, second=0, microsecond=0)
        if not include_today or next_run <= now:
            next_run += timedelta(days=1)

        return next_run.strftime('%Y-%m-%d %H:%M:%S')
    def create_scheduled_task(self, name, task_type, account_id, user_id=None,
                              interval_hours=24, delay_minutes=0, random_delay_max=10,
                              next_run_at=None, enabled=1):
        """创建定时任务

        Args:
            delay_minutes: 用作每日运行的目标小时 (0-23)
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                next_run_value = next_run_at or self.calculate_next_daily_run(
                    delay_minutes,
                    random_delay_max,
                    include_today=True
                )

                self._execute_sql(cursor, """
                    INSERT INTO scheduled_tasks (name, task_type, account_id, user_id,
                        enabled, interval_hours, delay_minutes, random_delay_max, next_run_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, task_type, account_id, user_id,
                      1 if enabled else 0, interval_hours, delay_minutes, random_delay_max,
                      next_run_value))
                self.conn.commit()
                task_id = cursor.lastrowid
                logger.info(f"创建定时任务成功: {name} (ID: {task_id})")
                return task_id
            except Exception as e:
                logger.error(f"创建定时任务失败: {e}")
                self.conn.rollback()
                return None
    def get_scheduled_tasks(self, user_id=None):
        """获取定时任务列表"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, """
                        SELECT id, name, task_type, account_id, enabled, interval_hours,
                               delay_minutes, random_delay_max, next_run_at, last_run_at,
                               last_run_result, user_id, created_at, updated_at
                        FROM scheduled_tasks WHERE user_id = ?
                        ORDER BY id DESC
                    """, (user_id,))
                else:
                    self._execute_sql(cursor, """
                        SELECT id, name, task_type, account_id, enabled, interval_hours,
                               delay_minutes, random_delay_max, next_run_at, last_run_at,
                               last_run_result, user_id, created_at, updated_at
                        FROM scheduled_tasks ORDER BY id DESC
                    """)
                rows = cursor.fetchall()
                tasks = []
                for row in rows:
                    tasks.append({
                        'id': row[0], 'name': row[1], 'task_type': row[2],
                        'account_id': row[3], 'enabled': bool(row[4]),
                        'interval_hours': row[5], 'delay_minutes': row[6],
                        'random_delay_max': row[7], 'next_run_at': row[8],
                        'last_run_at': row[9], 'last_run_result': row[10],
                        'user_id': row[11], 'created_at': row[12], 'updated_at': row[13]
                    })
                return tasks
            except Exception as e:
                logger.error(f"获取定时任务列表失败: {e}")
                return []
    def get_scheduled_task(self, task_id):
        """获取单个定时任务"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, """
                    SELECT id, name, task_type, account_id, enabled, interval_hours,
                           delay_minutes, random_delay_max, next_run_at, last_run_at,
                           last_run_result, user_id, created_at, updated_at
                    FROM scheduled_tasks WHERE id = ?
                """, (task_id,))
                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0], 'name': row[1], 'task_type': row[2],
                        'account_id': row[3], 'enabled': bool(row[4]),
                        'interval_hours': row[5], 'delay_minutes': row[6],
                        'random_delay_max': row[7], 'next_run_at': row[8],
                        'last_run_at': row[9], 'last_run_result': row[10],
                        'user_id': row[11], 'created_at': row[12], 'updated_at': row[13]
                    }
                return None
            except Exception as e:
                logger.error(f"获取定时任务失败: {e}")
                return None
    def get_scheduled_task_by_account(self, account_id, user_id=None, task_type=None):
        """按账号获取最新的定时任务"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                params = [account_id]
                sql = """
                    SELECT id, name, task_type, account_id, enabled, interval_hours,
                           delay_minutes, random_delay_max, next_run_at, last_run_at,
                           last_run_result, user_id, created_at, updated_at
                    FROM scheduled_tasks
                    WHERE account_id = ?
                """

                if user_id is not None:
                    sql += " AND user_id = ?"
                    params.append(user_id)

                if task_type is not None:
                    sql += " AND task_type = ?"
                    params.append(task_type)

                sql += " ORDER BY enabled DESC, id DESC LIMIT 1"
                self._execute_sql(cursor, sql, tuple(params))
                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0], 'name': row[1], 'task_type': row[2],
                        'account_id': row[3], 'enabled': bool(row[4]),
                        'interval_hours': row[5], 'delay_minutes': row[6],
                        'random_delay_max': row[7], 'next_run_at': row[8],
                        'last_run_at': row[9], 'last_run_result': row[10],
                        'user_id': row[11], 'created_at': row[12], 'updated_at': row[13]
                    }
                return None
            except Exception as e:
                logger.error(f"按账号获取定时任务失败: {e}")
                return None
    def update_scheduled_task(self, task_id, **kwargs):
        """更新定时任务"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                allowed_fields = {'name', 'task_type', 'account_id', 'enabled',
                                  'interval_hours', 'delay_minutes', 'random_delay_max',
                                  'next_run_at', 'user_id'}
                update_fields = []
                params = []
                for key, value in kwargs.items():
                    if key in allowed_fields:
                        update_fields.append(f"{key} = ?")
                        params.append(value)

                if not update_fields:
                    return False

                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                params.append(task_id)
                sql = f"UPDATE scheduled_tasks SET {', '.join(update_fields)} WHERE id = ?"
                self._execute_sql(cursor, sql, tuple(params))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"更新定时任务失败: {e}")
                self.conn.rollback()
                return False
    def delete_scheduled_task(self, task_id):
        """删除定时任务"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"删除定时任务失败: {e}")
                self.conn.rollback()
                return False
    def get_due_tasks(self):
        """获取到期需要执行的任务"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                from datetime import datetime
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self._execute_sql(cursor, """
                    SELECT id, name, task_type, account_id, enabled, interval_hours,
                           delay_minutes, random_delay_max, next_run_at, last_run_at,
                           last_run_result, user_id, created_at, updated_at
                    FROM scheduled_tasks
                    WHERE enabled = 1 AND next_run_at <= ?
                    ORDER BY next_run_at ASC
                """, (now,))
                rows = cursor.fetchall()
                tasks = []
                for row in rows:
                    tasks.append({
                        'id': row[0], 'name': row[1], 'task_type': row[2],
                        'account_id': row[3], 'enabled': bool(row[4]),
                        'interval_hours': row[5], 'delay_minutes': row[6],
                        'random_delay_max': row[7], 'next_run_at': row[8],
                        'last_run_at': row[9], 'last_run_result': row[10],
                        'user_id': row[11], 'created_at': row[12], 'updated_at': row[13]
                    })
                return tasks
            except Exception as e:
                logger.error(f"获取到期任务失败: {e}")
                return []
    def update_task_run_result(self, task_id, result, next_run_at):
        """更新任务执行结果和下次运行时间"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                from datetime import datetime
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                result_str = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                self._execute_sql(cursor, """
                    UPDATE scheduled_tasks
                    SET last_run_at = ?, last_run_result = ?, next_run_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (now, result_str, next_run_at, task_id))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"更新任务执行结果失败: {e}")
                self.conn.rollback()
                return False
    def save_chat_message(self, cookie_id: str, chat_id: str, sender_id: str,
                          sender_name: str, content: str, content_type: int = 1,
                          image_url: str = None, item_id: str = None,
                          direction: int = 2, reply_source: str = None,
                          media_url: str = None, link_url: str = None,
                          extra_json: str = None,
                          created_at: str = None) -> Optional[int]:
        """保存聊天消息"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if created_at:
                    self._execute_sql(cursor, """
                        INSERT INTO chat_messages (cookie_id, chat_id, sender_id, sender_name,
                            content, content_type, image_url, item_id, direction, reply_source,
                            media_url, link_url, extra_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (cookie_id, chat_id, sender_id, sender_name, content,
                          content_type, image_url, item_id, direction, reply_source,
                          media_url, link_url, extra_json, created_at))
                else:
                    self._execute_sql(cursor, """
                        INSERT INTO chat_messages (cookie_id, chat_id, sender_id, sender_name,
                            content, content_type, image_url, item_id, direction, reply_source,
                            media_url, link_url, extra_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (cookie_id, chat_id, sender_id, sender_name, content,
                          content_type, image_url, item_id, direction, reply_source,
                          media_url, link_url, extra_json))
                self.conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"保存聊天消息失败: {e}")
                self.conn.rollback()
                return None
    def get_chat_sessions(self, cookie_id: str, limit: int = 50) -> list:
        """获取指定账号的会话列表（按最新消息排序），包含买家名称"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                # 过滤 sender_name 中混入的系统文案/订单状态文本，避免污染 buyer_name
                # （例如 "买家已拍下，待付款"、"工作台通知" 等会被当成买家昵称显示）
                # SQLite 的 CURRENT_TIMESTAMP 落库为 UTC，对外统一转换为北京时间（UTC+8）给前端展示
                self._execute_sql(cursor, """
                    SELECT m.chat_id, m.sender_name, m.content, m.content_type,
                           m.item_id, datetime(m.created_at, '+8 hours') AS created_at,
                           m.direction, m.sender_id,
                           buyer.buyer_name, buyer.buyer_id
                    FROM chat_messages m
                    INNER JOIN (
                        SELECT chat_id, MAX(id) AS max_id
                        FROM chat_messages
                        WHERE cookie_id = ?
                        GROUP BY chat_id
                    ) latest ON m.chat_id = latest.chat_id AND m.id = latest.max_id
                    LEFT JOIN (
                        SELECT chat_id, sender_name AS buyer_name, sender_id AS buyer_id
                        FROM chat_messages
                        WHERE cookie_id = ? AND direction = 2
                          AND sender_name IS NOT NULL AND sender_name != ''
                          AND sender_name NOT IN ('未知用户', '工作台通知', '订单', '交易消息', '买家', '全部')
                          AND sender_name NOT LIKE '%待付款%'
                          AND sender_name NOT LIKE '%待发货%'
                          AND sender_name NOT LIKE '%已发货%'
                          AND sender_name NOT LIKE '%拍下%'
                          AND sender_name NOT LIKE '%付款%'
                          AND sender_name NOT LIKE '%发货%'
                          AND sender_name NOT LIKE '%收货%'
                          AND sender_name NOT LIKE '%退款%'
                          AND sender_name NOT LIKE '%评价%'
                          AND sender_name NOT LIKE '%交易%'
                          AND sender_name NOT LIKE '%关闭%'
                          AND sender_name NOT LIKE '%确认%'
                          AND sender_name NOT LIKE '%小红花%'
                          AND sender_name NOT LIKE '%等待%'
                        GROUP BY chat_id
                    ) buyer ON m.chat_id = buyer.chat_id
                    WHERE m.cookie_id = ?
                    ORDER BY m.created_at DESC
                    LIMIT ?
                """, (cookie_id, cookie_id, cookie_id, limit))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
            except Exception as e:
                logger.error(f"获取会话列表失败: {e}")
                return []
    def get_chat_messages(self, cookie_id: str, chat_id: str, limit: int = 50, before_id: int = None) -> list:
        """获取指定会话的消息列表"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if before_id:
                    self._execute_sql(cursor, """
                        SELECT id, cookie_id, chat_id, sender_id, sender_name, content,
                               content_type, image_url, item_id, direction, reply_source,
                               media_url, link_url, extra_json,
                               datetime(created_at, '+8 hours') AS created_at
                        FROM chat_messages
                        WHERE cookie_id = ? AND chat_id = ? AND id < ?
                        ORDER BY id DESC
                        LIMIT ?
                    """, (cookie_id, chat_id, before_id, limit))
                else:
                    self._execute_sql(cursor, """
                        SELECT id, cookie_id, chat_id, sender_id, sender_name, content,
                               content_type, image_url, item_id, direction, reply_source,
                               media_url, link_url, extra_json,
                               datetime(created_at, '+8 hours') AS created_at
                        FROM chat_messages
                        WHERE cookie_id = ? AND chat_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    """, (cookie_id, chat_id, limit))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                result = [dict(zip(columns, row)) for row in rows]
                result.reverse()
                return result
            except Exception as e:
                logger.error(f"获取聊天消息失败: {e}")
                return []
    def cleanup_old_chat_messages(self, days: int = 30) -> int:
        """清理指定天数前的聊天消息"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, """
                    DELETE FROM chat_messages
                    WHERE created_at < datetime('now', ?)
                """, (f'-{days} days',))
                deleted = cursor.rowcount
                self.conn.commit()
                if deleted > 0:
                    logger.info(f"清理了 {deleted} 条过期聊天消息（{days}天前）")
                return deleted
            except Exception as e:
                logger.error(f"清理聊天消息失败: {e}")
                self.conn.rollback()
                return 0
    def delete_chat_messages_by_session(self, cookie_id: str, chat_id: str) -> int:
        """删除指定会话的聊天消息，用于历史补拉重建。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, """
                    DELETE FROM chat_messages
                    WHERE cookie_id = ? AND chat_id = ?
                """, (cookie_id, chat_id))
                deleted = cursor.rowcount
                self.conn.commit()
                logger.info(f"删除会话聊天消息成功: cookie_id={cookie_id}, chat_id={chat_id}, deleted={deleted}")
                return deleted
            except Exception as e:
                logger.error(f"删除会话聊天消息失败: cookie_id={cookie_id}, chat_id={chat_id}, error={e}")
                self.conn.rollback()
                return 0
    def get_all_chat_sessions(self, user_id: int, limit: int = 200) -> list:
        """获取用户所有账号的会话列表（三栏布局用）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, """
                    SELECT m.cookie_id, m.chat_id, m.sender_name, m.content,
                           m.content_type, m.item_id, m.created_at, m.direction, m.sender_id
                    FROM chat_messages m
                    INNER JOIN (
                        SELECT cookie_id, chat_id, MAX(id) AS max_id
                        FROM chat_messages
                        WHERE cookie_id IN (SELECT id FROM cookies WHERE user_id = ?)
                        GROUP BY cookie_id, chat_id
                    ) latest ON m.cookie_id = latest.cookie_id
                               AND m.chat_id = latest.chat_id
                               AND m.id = latest.max_id
                    ORDER BY m.created_at DESC
                    LIMIT ?
                """, (user_id, limit))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
            except Exception as e:
                logger.error(f"获取全量会话列表失败: {e}")
                return []