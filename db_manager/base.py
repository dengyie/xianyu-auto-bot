import sqlite3
import os
import threading
import time
import json
import random
import string
import re
import sys
import aiohttp
import io
import base64
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont
from typing import List, Tuple, Dict, Optional, Any
from urllib.parse import parse_qs, urlparse
from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from .security import generate_initial_admin_password, hash_user_password


class DBBase:
    """base"""

    def __init__(self, db_path: str = None):
        """初始化数据库连接和表结构"""
        # 支持环境变量配置数据库路径
        if db_path is None:
            db_path = os.getenv('DB_PATH', 'data/xianyu_data.db')

        # 确保数据目录存在并有正确权限
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, mode=0o755, exist_ok=True)
                logger.info(f"创建数据目录: {db_dir}")
            except PermissionError as e:
                logger.error(f"创建数据目录失败，权限不足: {e}")
                # 尝试使用当前目录
                db_path = os.path.basename(db_path)
                logger.warning(f"使用当前目录作为数据库路径: {db_path}")
            except Exception as e:
                logger.error(f"创建数据目录失败: {e}")
                raise

        # 检查目录权限
        if db_dir and os.path.exists(db_dir):
            if not os.access(db_dir, os.W_OK):
                logger.error(f"数据目录没有写权限: {db_dir}")
                # 尝试使用当前目录
                db_path = os.path.basename(db_path)
                logger.warning(f"使用当前目录作为数据库路径: {db_path}")

        self.db_path = db_path
        logger.info(f"数据库路径: {self.db_path}")
        self.conn = None
        self.lock = threading.RLock()  # 使用可重入锁保护数据库操作
        self.secret_fernet = None
        self.secret_key_path = None

        # SQL日志配置 - 默认启用
        self.sql_log_enabled = False  # 默认关闭SQL日志，避免生产日志泄露业务数据
        self.sql_log_level = 'INFO'  # 默认使用INFO级别

        # 允许通过环境变量覆盖默认设置
        if os.getenv('SQL_LOG_ENABLED'):
            self.sql_log_enabled = os.getenv('SQL_LOG_ENABLED', 'true').lower() == 'true'
        if os.getenv('SQL_LOG_LEVEL'):
            self.sql_log_level = os.getenv('SQL_LOG_LEVEL', 'INFO').upper()

        logger.info(f"SQL日志{'已启用' if self.sql_log_enabled else '已关闭'}，日志级别: {self.sql_log_level}")

        self._init_secret_cipher()

        self.init_db()
        try:
            self.recover_stale_batch_data_reservations()
        except Exception as e:
            logger.warning(f"恢复过期批量数据预占失败: {e}")
        try:
            self._migrate_plaintext_cookie_secrets()
        except Exception as e:
            logger.warning(f"迁移明文账号敏感信息失败: {e}")
    def _init_secret_cipher(self):
        """初始化敏感字段加密器。"""
        env_key = os.getenv('SECRET_ENCRYPTION_KEY', '').strip()
        if env_key:
            key = env_key.encode('utf-8')
        else:
            db_dir = os.path.dirname(self.db_path) or '.'
            self.secret_key_path = os.path.join(db_dir, '.secret_encryption.key')
            if os.path.exists(self.secret_key_path):
                with open(self.secret_key_path, 'rb') as f:
                    key = f.read().strip()
            else:
                key = Fernet.generate_key()
                with open(self.secret_key_path, 'wb') as f:
                    f.write(key)
                try:
                    os.chmod(self.secret_key_path, 0o600)
                except Exception:
                    pass

        self.secret_fernet = Fernet(key)
    def _is_encrypted_secret(self, value: Any) -> bool:
        return isinstance(value, str) and value.startswith('enc$')
    def _encrypt_secret(self, value: Any) -> Any:
        if value is None:
            return None
        text = str(value)
        if text == '':
            return ''
        if self._is_encrypted_secret(text):
            return text
        token = self.secret_fernet.encrypt(text.encode('utf-8')).decode('utf-8')
        return f'enc${token}'
    def _decrypt_secret(self, value: Any) -> str:
        if value in (None, ''):
            return ''
        text = str(value)
        if not self._is_encrypted_secret(text):
            return text
        try:
            return self.secret_fernet.decrypt(text[4:].encode('utf-8')).decode('utf-8')
        except InvalidToken:
            logger.warning("检测到无法解密的敏感字段，按原值返回")
            return text
    def _migrate_plaintext_cookie_secrets(self):
        """将 cookies 表中的明文敏感字段迁移为密文存储。"""
        with self.lock:
            cursor = self.conn.cursor()
            self._execute_sql(cursor, "SELECT id, value, password, proxy_pass FROM cookies")
            rows = cursor.fetchall()
            updated_count = 0

            for cookie_id, cookie_value, password, proxy_pass in rows:
                update_fields = []
                params = []

                if cookie_value and not self._is_encrypted_secret(cookie_value):
                    update_fields.append("value = ?")
                    params.append(self._encrypt_secret(cookie_value))

                if password and not self._is_encrypted_secret(password):
                    update_fields.append("password = ?")
                    params.append(self._encrypt_secret(password))

                if proxy_pass and not self._is_encrypted_secret(proxy_pass):
                    update_fields.append("proxy_pass = ?")
                    params.append(self._encrypt_secret(proxy_pass))

                if not update_fields:
                    continue

                params.append(cookie_id)
                self._execute_sql(cursor, f"UPDATE cookies SET {', '.join(update_fields)} WHERE id = ?", tuple(params))
                updated_count += 1

            if updated_count:
                self.conn.commit()
                logger.info(f"已迁移 {updated_count} 条 cookies 敏感字段为密文存储")
    def _normalize_order_status(self, status: str) -> str:
        """标准化订单状态，统一为系统内部状态值。"""
        if status is None:
            return None

        normalized = str(status).strip().lower()
        if not normalized:
            return None

        status_map = {
            # 内部标准状态
            'processing': 'processing',
            'pending_payment': 'pending_payment',
            'pending_ship': 'pending_ship',
            'pending_delivery': 'pending_ship',
            'partial_success': 'partial_success',
            'partial_pending_finalize': 'partial_pending_finalize',
            'shipped': 'shipped',
            'completed': 'completed',
            'refunding': 'refunding',
            'refund_cancelled': 'refund_cancelled',
            'cancelled': 'cancelled',
            'unknown': 'unknown',
            # 常见外部/历史状态兼容
            'success': 'completed',
            'refunded': 'cancelled',
            'closed': 'cancelled',
            'canceled': 'cancelled',
            'delivered': 'shipped',
            # 中文状态兼容
            '处理中': 'processing',
            '待发货': 'pending_ship',
            '部分发货': 'partial_success',
            '部分待收尾': 'partial_pending_finalize',
            '已发货': 'shipped',
            '已完成': 'completed',
            '退款中': 'refunding',
            '退款撤销': 'refund_cancelled',
            '已关闭': 'cancelled',
        }

        mapped = status_map.get(normalized, normalized)
        if mapped != normalized:
            logger.info(f"标准化订单状态: {status} -> {mapped}")
        elif normalized not in {
            'processing', 'pending_payment', 'pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped', 'completed',
            'refunding', 'refund_cancelled', 'cancelled', 'unknown'
        }:
            logger.warning(f"检测到未映射订单状态，按原值保存: {status}")
        return mapped
    def _get_order_status_priority(self, status: str) -> int:
        normalized = self._normalize_order_status(status)
        priority_map = {
            'processing': 10,
            'pending_payment': 15,
            'pending_ship': 20,
            'partial_success': 30,
            'partial_pending_finalize': 30,
            'shipped': 40,
            'completed': 50,
            'refunding': 60,
            'refund_cancelled': 65,
            'cancelled': 70,
        }
        return priority_map.get(normalized, 0)
    def resolve_external_order_status(self, current_status: str, incoming_status: str, source: str = "external_sync") -> str:
        """合并外部/旁路状态写入，避免更粗粒度状态覆盖内部进度状态。"""
        normalized_current = self._normalize_order_status(current_status)
        normalized_incoming = self._normalize_order_status(incoming_status)

        if not normalized_incoming or normalized_incoming == 'unknown':
            return None

        if not normalized_current or normalized_current == 'unknown':
            return normalized_incoming

        blocked_incoming_map = {
            'pending_payment': {'processing'},
            'pending_ship': {'processing', 'pending_payment'},
            'partial_success': {'processing', 'pending_payment', 'pending_ship', 'shipped'},
            'partial_pending_finalize': {'processing', 'pending_payment', 'pending_ship', 'shipped'},
            'shipped': {'processing', 'pending_payment', 'pending_ship'},
            'completed': {'processing', 'pending_payment', 'pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped'},
            'refunding': {'processing', 'pending_payment', 'pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped'},
            'cancelled': {'processing', 'pending_payment', 'pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped', 'completed', 'refunding'},
        }

        blocked_incoming = blocked_incoming_map.get(normalized_current, set())
        if normalized_incoming in blocked_incoming:
            logger.warning(
                f"忽略外部订单状态覆盖: source={source}, current={normalized_current}, incoming={normalized_incoming}"
            )
            return normalized_current

        current_priority = self._get_order_status_priority(normalized_current)
        incoming_priority = self._get_order_status_priority(normalized_incoming)
        if (
            current_priority
            and incoming_priority
            and incoming_priority < current_priority
            and normalized_incoming not in {'refunding', 'cancelled', 'refund_cancelled'}
        ):
            logger.warning(
                f"忽略低优先级外部状态覆盖: source={source}, current={normalized_current}, incoming={normalized_incoming}"
            )
            return normalized_current

        return normalized_incoming
    def init_db(self):
        """初始化数据库表结构"""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = self.conn.cursor()
            
            # 创建用户表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建邮箱验证码表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                code TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建图形验证码表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS captcha_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                code TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建cookies表（添加user_id字段和auto_confirm字段）
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS cookies (
                id TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                auto_confirm INTEGER DEFAULT 1,
                remark TEXT DEFAULT '',
                status_note TEXT DEFAULT '',
                qr_login_grace_until INTEGER DEFAULT 0,
                pause_duration INTEGER DEFAULT 10,
                username TEXT DEFAULT '',
                password TEXT DEFAULT '',
                show_browser INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            ''')

            
            # 创建keywords表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS keywords (
                cookie_id TEXT,
                keyword TEXT,
                reply TEXT,
                item_id TEXT,
                type TEXT DEFAULT 'text',
                image_url TEXT,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 创建cookie_status表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS cookie_status (
                cookie_id TEXT PRIMARY KEY,
                enabled BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 创建AI回复配置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_reply_settings (
                cookie_id TEXT PRIMARY KEY,
                ai_enabled BOOLEAN DEFAULT FALSE,
                model_name TEXT DEFAULT 'qwen-plus',
                api_key TEXT,
                base_url TEXT DEFAULT 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                api_type TEXT DEFAULT '',
                max_discount_percent INTEGER DEFAULT 10,
                max_discount_amount INTEGER DEFAULT 100,
                max_bargain_rounds INTEGER DEFAULT 3,
                custom_prompts TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 创建AI配置预设表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_config_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                preset_name TEXT NOT NULL,
                model_name TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '',
                api_type TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, preset_name)
            )
            ''')

            # 创建AI对话历史表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT,
                bargain_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies (id) ON DELETE CASCADE
            )
            ''')

            # 创建AI商品信息缓存表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_item_cache (
                item_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                price REAL,
                description TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建卡券表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('api', 'yifan_api', 'text', 'data', 'image')),
                api_config TEXT,
                text_content TEXT,
                data_content TEXT,
                image_url TEXT,
                description TEXT,
                enabled BOOLEAN DEFAULT TRUE,
                delay_seconds INTEGER DEFAULT 0,
                is_multi_spec BOOLEAN DEFAULT FALSE,
                spec_name TEXT,
                spec_value TEXT,
                spec_name_2 TEXT,
                spec_value_2 TEXT,
                user_id INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            ''')

            # 创建订单表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                item_id TEXT,
                buyer_id TEXT,
                sid TEXT,
                spec_name TEXT,
                spec_value TEXT,
                spec_name_2 TEXT,
                spec_value_2 TEXT,
                quantity TEXT,
                amount TEXT,
                bargain_flow_detected INTEGER DEFAULT 0,
                bargain_success_detected INTEGER DEFAULT 0,
                order_status TEXT DEFAULT 'unknown',
                pre_refund_status TEXT,
                platform_created_at TIMESTAMP,
                platform_paid_at TIMESTAMP,
                platform_completed_at TIMESTAMP,
                cookie_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')
            
            # 检查并添加 sid 列到 orders 表（用于简化消息查找订单）
            try:
                self._execute_sql(cursor, "SELECT sid FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                # sid 列不存在，需要添加
                logger.info("正在为 orders 表添加 sid 列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN sid TEXT")
                self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_orders_sid ON orders(sid)")
                logger.info("orders 表 sid 列添加完成")

            # 检查并添加 buyer_nick 列到 orders 表（用于存储买家昵称）
            try:
                self._execute_sql(cursor, "SELECT buyer_nick FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                # buyer_nick 列不存在，需要添加
                logger.info("正在为 orders 表添加 buyer_nick 列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN buyer_nick TEXT")
                logger.info("orders 表 buyer_nick 列添加完成")

            # 检查并添加 pre_refund_status 列到 orders 表（用于退款撤销跨重启回退）
            try:
                self._execute_sql(cursor, "SELECT pre_refund_status FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("正在为 orders 表添加 pre_refund_status 列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN pre_refund_status TEXT")
                logger.info("orders 表 pre_refund_status 列添加完成")

            # 检查并添加 bargain_flow_detected 列（用于记录小刀/拼团成交价覆盖）
            try:
                self._execute_sql(cursor, "SELECT bargain_flow_detected FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("正在为 orders 表添加 bargain_flow_detected 列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN bargain_flow_detected INTEGER DEFAULT 0")
                logger.info("orders 表 bargain_flow_detected 列添加完成")

            # 检查并添加 bargain_success_detected 列（用于记录小刀已进入第二阶段的成功证据）
            try:
                self._execute_sql(cursor, "SELECT bargain_success_detected FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("正在为 orders 表添加 bargain_success_detected 列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN bargain_success_detected INTEGER DEFAULT 0")
                logger.info("orders 表 bargain_success_detected 列添加完成")

            # 检查并添加 user_id 列（用于数据库迁移）
            try:
                self._execute_sql(cursor, "SELECT user_id FROM cards LIMIT 1")
            except sqlite3.OperationalError:
                # user_id 列不存在，需要添加
                logger.info("正在为 cards 表添加 user_id 列...")
                self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
                self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_cards_user_id ON cards(user_id)")
                logger.info("cards 表 user_id 列添加完成")

            # 检查并添加 delay_seconds 列（用于自动发货延时功能）
            try:
                self._execute_sql(cursor, "SELECT delay_seconds FROM cards LIMIT 1")
            except sqlite3.OperationalError:
                # delay_seconds 列不存在，需要添加
                logger.info("正在为 cards 表添加 delay_seconds 列...")
                self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN delay_seconds INTEGER DEFAULT 0")
                logger.info("cards 表 delay_seconds 列添加完成")

            # 检查并添加 item_id 列（用于自动回复商品ID功能）
            try:
                self._execute_sql(cursor, "SELECT item_id FROM keywords LIMIT 1")
            except sqlite3.OperationalError:
                # item_id 列不存在，需要添加
                logger.info("正在为 keywords 表添加 item_id 列...")
                self._execute_sql(cursor, "ALTER TABLE keywords ADD COLUMN item_id TEXT")
                logger.info("keywords 表 item_id 列添加完成")

            # 创建商品信息表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS item_info (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_title TEXT,
                item_description TEXT,
                item_category TEXT,
                item_price TEXT,
                item_detail TEXT,
                is_multi_spec BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE,
                UNIQUE(cookie_id, item_id)
            )
            ''')

            # 检查并添加 multi_quantity_delivery 列（用于多数量发货功能）
            try:
                self._execute_sql(cursor, "SELECT multi_quantity_delivery FROM item_info LIMIT 1")
            except sqlite3.OperationalError:
                # multi_quantity_delivery 列不存在，需要添加
                logger.info("正在为 item_info 表添加 multi_quantity_delivery 列...")
                self._execute_sql(cursor, "ALTER TABLE item_info ADD COLUMN multi_quantity_delivery BOOLEAN DEFAULT FALSE")
                logger.info("item_info 表 multi_quantity_delivery 列添加完成")

            # 创建自动发货规则表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS delivery_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                card_id INTEGER NOT NULL,
                delivery_count INTEGER DEFAULT 1,
                enabled BOOLEAN DEFAULT TRUE,
                description TEXT,
                delivery_times INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
            )
            ''')

            # 创建发货日志表（记录真实发货尝试结果：成功/失败）
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS delivery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                cookie_id TEXT,
                order_id TEXT,
                item_id TEXT,
                buyer_id TEXT,
                buyer_nick TEXT,
                rule_id INTEGER,
                rule_keyword TEXT,
                card_type TEXT,
                match_mode TEXT,
                channel TEXT NOT NULL DEFAULT 'auto',
                status TEXT NOT NULL DEFAULT 'failed',
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE SET NULL,
                FOREIGN KEY (rule_id) REFERENCES delivery_rules(id) ON DELETE SET NULL
            )
            ''')
            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_delivery_logs_user_time ON delivery_logs(user_id, created_at)")
            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_delivery_logs_order_id ON delivery_logs(order_id)")

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS delivery_finalization_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                unit_index INTEGER NOT NULL DEFAULT 1,
                cookie_id TEXT,
                item_id TEXT,
                buyer_id TEXT,
                channel TEXT NOT NULL DEFAULT 'auto',
                status TEXT NOT NULL DEFAULT 'sent',
                delivery_meta TEXT,
                last_error TEXT,
                sent_at TIMESTAMP,
                finalized_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(order_id, unit_index)
            )
            ''')
            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_delivery_finalization_states_status ON delivery_finalization_states(status, updated_at)")

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS data_card_reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                order_id TEXT NOT NULL,
                cookie_id TEXT,
                buyer_id TEXT,
                unit_index INTEGER NOT NULL DEFAULT 1,
                reserved_content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at TIMESTAMP,
                finalized_at TIMESTAMP,
                released_at TIMESTAMP,
                expires_at TIMESTAMP,
                FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
            )
            ''')
            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_data_card_reservations_card_status ON data_card_reservations(card_id, status)")
            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_data_card_reservations_order_status ON data_card_reservations(order_id, status)")
            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_data_card_reservations_card_order_unit ON data_card_reservations(card_id, order_id, unit_index)")

            # 创建默认回复表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS default_replies (
                cookie_id TEXT PRIMARY KEY,
                enabled BOOLEAN DEFAULT FALSE,
                reply_content TEXT,
                reply_once BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 添加 reply_once 字段（如果不存在）
            try:
                cursor.execute('ALTER TABLE default_replies ADD COLUMN reply_once BOOLEAN DEFAULT FALSE')
                self.conn.commit()
                logger.info("已添加 reply_once 字段到 default_replies 表")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"添加 reply_once 字段失败: {e}")

            # 创建指定商品回复表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS item_replay (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    cookie_id TEXT NOT NULL,
                    reply_content TEXT NOT NULL ,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                sender_id TEXT,
                sender_name TEXT,
                content TEXT,
                content_type INTEGER DEFAULT 1,
                image_url TEXT,
                item_id TEXT,
                direction INTEGER DEFAULT 2,
                reply_source TEXT,
                media_url TEXT,
                link_url TEXT,
                extra_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')
            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_chat_messages_lookup ON chat_messages(cookie_id, chat_id, created_at)")

            # 创建默认回复记录表（记录已回复的chat_id）
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS default_reply_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                replied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cookie_id, chat_id),
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 创建通知渠道表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('qq','ding_talk','dingtalk','feishu','lark','bark','email','webhook','wechat','telegram')),
                config TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建系统设置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建消息通知配置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE,
                FOREIGN KEY (channel_id) REFERENCES notification_channels(id) ON DELETE CASCADE,
                UNIQUE(cookie_id, channel_id)
            )
            ''')

            # 创建用户设置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, key)
            )
            ''')

            # 创建好评模板表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS comment_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                is_active BOOLEAN DEFAULT FALSE,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 创建风控日志表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS risk_control_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'slider_captcha',
                session_id TEXT,
                trigger_scene TEXT,
                result_code TEXT,
                event_description TEXT,
                event_meta TEXT,
                processing_result TEXT,
                processing_status TEXT DEFAULT 'processing',
                error_message TEXT,
                duration_ms INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 创建通知模板表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL UNIQUE CHECK (type IN ('message', 'token_refresh', 'delivery', 'slider_success', 'face_verify', 'password_login_success', 'cookie_refresh_success', 'account_paused')),
                template TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建定时任务表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                task_type TEXT NOT NULL DEFAULT 'item_polish',
                account_id TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                interval_hours INTEGER DEFAULT 24,
                delay_minutes INTEGER DEFAULT 0,
                random_delay_max INTEGER DEFAULT 10,
                next_run_at TEXT,
                last_run_at TEXT,
                last_run_result TEXT,
                user_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 插入默认通知模板
            cursor.execute('''
            INSERT OR IGNORE INTO notification_templates (type, template) VALUES
            ('message', '🚨 接收消息通知

账号: {account_id}
买家: {buyer_name} (ID: {buyer_id})
商品ID: {item_id}
聊天ID: {chat_id}
消息内容: {message}

时间: {time}'),
            ('token_refresh', 'Token刷新异常

账号ID: {account_id}
异常时间: {time}
异常信息: {error_message}

请检查账号Cookie是否过期，如有需要请及时更新Cookie配置。'),
            ('delivery', '🚨 自动发货通知

账号: {account_id}
买家: {buyer_name} (ID: {buyer_id})
商品ID: {item_id}
聊天ID: {chat_id}
结果: {result}
时间: {time}

请及时处理！'),
            ('slider_success', '✅ 滑块验证成功，{status_text}

账号: {account_id}
时间: {time}'),
            ('face_verify', '⚠️ 需要{verification_type} 🚫
在验证期间，发货及自动回复暂时无法使用。

{verification_action}
{verification_url}

账号: {account_id}
时间: {time}'),
            ('password_login_success', '✅ 密码登录成功

账号: {account_id}
时间: {time}
Cookie数量: {cookie_count}

账号Cookie已更新，正在重启服务...'),
            ('cookie_refresh_success', '✅ 刷新Cookie成功

账号: {account_id}
时间: {time}
Cookie数量: {cookie_count}

账号已可正常使用。'),
            ('account_paused', '🚫 账号已暂停

账号: {account_id}
状态: {status_note}
原因: {pause_reason}
时间: {time}

说明: {error_message}
验证入口: {verification_url}

{action_hint}')
            ''')

            # 插入默认系统设置（不包括管理员密码，由reply_server.py初始化）
            cursor.execute('''
            INSERT OR IGNORE INTO system_settings (key, value, description) VALUES
            ('theme_color', 'blue', '主题颜色'),
            ('registration_enabled', 'true', '是否开启用户注册'),
            ('show_default_login_info', 'true', '是否显示默认登录信息'),
            ('login_captcha_enabled', 'true', '是否开启登录验证码'),
            ('risk_control_night_mode_enabled', 'false', '是否启用夜间风控降频'),
            ('risk_control_night_start_hour', '1', '夜间风控降频开始小时'),
            ('risk_control_night_end_hour', '6', '夜间风控降频结束小时'),
            ('smtp_server', '', 'SMTP服务器地址'),
            ('smtp_port', '587', 'SMTP端口'),
            ('smtp_user', '', 'SMTP登录用户名（发件邮箱）'),
            ('smtp_password', '', 'SMTP登录密码/授权码'),
            ('smtp_from', '', '发件人显示名（留空则使用邮箱地址）'),
            ('smtp_use_tls', 'true', '是否启用TLS'),
            ('smtp_use_ssl', 'false', '是否启用SSL'),
            ('verification_email_api_url', '', '验证码邮件 API 地址（留空则仅使用 SMTP，不再向旧硬编码地址外发）'),
            ('qq_notification_api_url', '', 'QQ 私信通知 API 地址（留空则禁用 QQ 私信通知）'),
            ('auto_comment_api_url', '', '自动好评辅助 API 地址（留空则禁用此功能，避免 Cookie 外发）'),
            ('qq_reply_secret_key', 'xianyu_qq_reply_2024', 'QQ回复消息API秘钥')
            ''')

            # 检查并升级数据库
            self.check_and_upgrade_db(cursor)

            # 执行数据库迁移
            self._migrate_database(cursor)

            self.conn.commit()
            logger.info("数据库初始化完成")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            self.conn.rollback()
            raise
    def _migrate_database(self, cursor):
        """执行数据库迁移"""
        try:
            # 检查cards表是否存在image_url列
            cursor.execute("PRAGMA table_info(cards)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'image_url' not in columns:
                logger.info("添加cards表的image_url列...")
                cursor.execute("ALTER TABLE cards ADD COLUMN image_url TEXT")
                logger.info("数据库迁移完成：添加image_url列")

            # 检查并更新CHECK约束（重建表以支持image类型）
            self._update_cards_table_constraints(cursor)

            # 检查cookies表是否存在remark列
            cursor.execute("PRAGMA table_info(cookies)")
            cookie_columns = [column[1] for column in cursor.fetchall()]

            if 'remark' not in cookie_columns:
                logger.info("添加cookies表的remark列...")
                cursor.execute("ALTER TABLE cookies ADD COLUMN remark TEXT DEFAULT ''")
                logger.info("数据库迁移完成：添加remark列")

            if 'status_note' not in cookie_columns:
                logger.info("添加cookies表的status_note列...")
                cursor.execute("ALTER TABLE cookies ADD COLUMN status_note TEXT DEFAULT ''")
                logger.info("数据库迁移完成：添加status_note列")

            if 'qr_login_grace_until' not in cookie_columns:
                logger.info("添加cookies表的qr_login_grace_until列...")
                cursor.execute("ALTER TABLE cookies ADD COLUMN qr_login_grace_until INTEGER DEFAULT 0")
                logger.info("数据库迁移完成：添加qr_login_grace_until列")

            # 检查cookies表是否存在pause_duration列
            if 'pause_duration' not in cookie_columns:
                logger.info("添加cookies表的pause_duration列...")
                cursor.execute("ALTER TABLE cookies ADD COLUMN pause_duration INTEGER DEFAULT 10")
                logger.info("数据库迁移完成：添加pause_duration列")

            # 检查cookies表是否存在auto_comment列
            if 'auto_comment' not in cookie_columns:
                logger.info("添加cookies表的auto_comment列...")
                cursor.execute("ALTER TABLE cookies ADD COLUMN auto_comment INTEGER DEFAULT 0")
                logger.info("数据库迁移完成：添加auto_comment列")

            # 历史版本可能缺少订单平台时间字段，不能再依赖旧版本号分支触发
            self._ensure_orders_platform_time_columns(cursor)

            # 迁移notification_templates表以支持新的模板类型
            self._migrate_notification_templates(cursor)

            # 检查ai_reply_settings表是否存在api_type列
            cursor.execute("PRAGMA table_info(ai_reply_settings)")
            ai_columns = [column[1] for column in cursor.fetchall()]
            if 'api_type' not in ai_columns:
                logger.info("添加ai_reply_settings表的api_type列...")
                cursor.execute("ALTER TABLE ai_reply_settings ADD COLUMN api_type TEXT DEFAULT ''")
                logger.info("数据库迁移完成：添加api_type列")

            # 检查ai_config_presets表是否存在api_type列
            cursor.execute("PRAGMA table_info(ai_config_presets)")
            preset_columns = [column[1] for column in cursor.fetchall()]
            if 'api_type' not in preset_columns:
                logger.info("添加ai_config_presets表的api_type列...")
                cursor.execute("ALTER TABLE ai_config_presets ADD COLUMN api_type TEXT NOT NULL DEFAULT ''")
                logger.info("数据库迁移完成：添加ai_config_presets.api_type列")

            # 检查risk_control_logs表扩展字段
            cursor.execute("PRAGMA table_info(risk_control_logs)")
            risk_log_columns = [column[1] for column in cursor.fetchall()]
            risk_log_column_defs = {
                'session_id': "TEXT",
                'trigger_scene': "TEXT",
                'result_code': "TEXT",
                'event_meta': "TEXT",
                'duration_ms': "INTEGER",
            }
            for column_name, column_type in risk_log_column_defs.items():
                if column_name not in risk_log_columns:
                    logger.info(f"添加risk_control_logs表的{column_name}列...")
                    cursor.execute(f"ALTER TABLE risk_control_logs ADD COLUMN {column_name} {column_type}")
                    logger.info(f"数据库迁移完成：添加risk_control_logs.{column_name}列")

            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_risk_control_logs_cookie_created ON risk_control_logs(cookie_id, created_at DESC)")
            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_risk_control_logs_type_status_created ON risk_control_logs(event_type, processing_status, created_at DESC)")
            self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_risk_control_logs_session_id ON risk_control_logs(session_id)")

            cursor.execute("PRAGMA table_info(chat_messages)")
            chat_message_columns = [column[1] for column in cursor.fetchall()]
            if 'media_url' not in chat_message_columns:
                logger.info("添加chat_messages表的media_url列...")
                cursor.execute("ALTER TABLE chat_messages ADD COLUMN media_url TEXT")
            if 'link_url' not in chat_message_columns:
                logger.info("添加chat_messages表的link_url列...")
                cursor.execute("ALTER TABLE chat_messages ADD COLUMN link_url TEXT")
            if 'extra_json' not in chat_message_columns:
                logger.info("添加chat_messages表的extra_json列...")
                cursor.execute("ALTER TABLE chat_messages ADD COLUMN extra_json TEXT")

        except Exception as e:
            logger.error(f"数据库迁移失败: {e}")
            # 迁移失败不应该阻止程序启动
            pass
    def _ensure_orders_platform_time_columns(self, cursor):
        """确保 orders 表存在平台时间字段。"""
        for order_time_column in ("platform_created_at", "platform_paid_at", "platform_completed_at"):
            try:
                self._execute_sql(cursor, f"SELECT {order_time_column} FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                self._execute_sql(cursor, f"ALTER TABLE orders ADD COLUMN {order_time_column} TIMESTAMP")
                logger.info(f"为orders表添加平台时间字段({order_time_column})")
    def _update_cards_table_constraints(self, cursor):
        """更新cards表的CHECK约束以支持image和yifan_api类型"""
        try:
            # 尝试插入一个测试的yifan_api类型记录来检查约束
            cursor.execute('''
                INSERT INTO cards (name, type, user_id)
                VALUES ('__test_yifan_constraint__', 'yifan_api', 1)
            ''')
            # 如果插入成功，立即删除测试记录
            cursor.execute("DELETE FROM cards WHERE name = '__test_yifan_constraint__'")
            logger.info("cards表约束检查通过，支持yifan_api类型")
        except Exception as e:
            if "CHECK constraint failed" in str(e) or "constraint" in str(e).lower():
                logger.info("检测到旧的CHECK约束，开始更新cards表以支持yifan_api类型...")

                # 重建表以更新约束
                try:
                    # 1. 创建新表
                    cursor.execute('''
                    CREATE TABLE IF NOT EXISTS cards_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        type TEXT NOT NULL CHECK (type IN ('api', 'yifan_api', 'text', 'data', 'image')),
                        api_config TEXT,
                        text_content TEXT,
                        data_content TEXT,
                        image_url TEXT,
                        description TEXT,
                        enabled BOOLEAN DEFAULT TRUE,
                        delay_seconds INTEGER DEFAULT 0,
                        is_multi_spec BOOLEAN DEFAULT FALSE,
                        spec_name TEXT,
                        spec_value TEXT,
                        spec_name_2 TEXT,
                        spec_value_2 TEXT,
                        user_id INTEGER NOT NULL DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )
                    ''')

                    # 2. 复制数据（双规格字段设为NULL，由后续迁移填充）
                    cursor.execute('''
                    INSERT INTO cards_new (id, name, type, api_config, text_content, data_content, image_url,
                                          description, enabled, delay_seconds, is_multi_spec, spec_name, spec_value,
                                          spec_name_2, spec_value_2, user_id, created_at, updated_at)
                    SELECT id, name, type, api_config, text_content, data_content, image_url,
                           description, enabled, delay_seconds, is_multi_spec, spec_name, spec_value,
                           NULL, NULL, user_id, created_at, updated_at
                    FROM cards
                    ''')

                    # 3. 删除旧表
                    cursor.execute("DROP TABLE cards")

                    # 4. 重命名新表
                    cursor.execute("ALTER TABLE cards_new RENAME TO cards")

                    logger.info("cards表约束更新完成，现在支持image类型")

                except Exception as rebuild_error:
                    logger.error(f"重建cards表失败: {rebuild_error}")
                    # 如果重建失败，尝试回滚
                    try:
                        cursor.execute("DROP TABLE IF EXISTS cards_new")
                    except:
                        pass
            else:
                logger.error(f"检查cards表约束时出现未知错误: {e}")
    def _migrate_notification_templates(self, cursor):
        """迁移notification_templates表以支持新的模板类型"""
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM notification_templates WHERE type IN ('cookie_refresh_success', 'account_paused')"
            )
            existing_template_count = cursor.fetchone()[0]
            if existing_template_count < 2:
                logger.info("补充通知模板类型，重建notification_templates约束...")

                # 重建表以更新CHECK约束
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS notification_templates_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL UNIQUE CHECK (type IN ('message', 'token_refresh', 'delivery', 'slider_success', 'face_verify', 'password_login_success', 'cookie_refresh_success', 'account_paused')),
                    template TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                ''')

                # 复制现有数据
                cursor.execute('''
                INSERT OR IGNORE INTO notification_templates_new (id, type, template, created_at, updated_at)
                SELECT id, type, template, created_at, updated_at FROM notification_templates
                ''')

                # 删除旧表
                cursor.execute("DROP TABLE notification_templates")

                # 重命名新表
                cursor.execute("ALTER TABLE notification_templates_new RENAME TO notification_templates")

                # 插入新的默认模板（包括之前可能缺失的）
                cursor.execute('''
                INSERT OR IGNORE INTO notification_templates (type, template) VALUES
                ('slider_success', '✅ 滑块验证成功，{status_text}

账号: {account_id}
时间: {time}'),
                ('face_verify', '⚠️ 需要{verification_type} 🚫
在验证期间，发货及自动回复暂时无法使用。

{verification_action}
{verification_url}

账号: {account_id}
时间: {time}'),
                ('password_login_success', '✅ 密码登录成功

账号: {account_id}
时间: {time}
Cookie数量: {cookie_count}

账号Cookie已更新，正在重启服务...'),
                ('cookie_refresh_success', '✅ 刷新Cookie成功

账号: {account_id}
时间: {time}
Cookie数量: {cookie_count}

账号已可正常使用。'),
                ('account_paused', '🚫 账号已暂停

账号: {account_id}
状态: {status_note}
原因: {pause_reason}
时间: {time}

说明: {error_message}
验证入口: {verification_url}

{action_hint}')
                ''')

            old_slider_success_template = '''✅ 滑块验证成功，cookies已自动更新到数据库

账号: {account_id}
时间: {time}'''
            new_slider_success_template = '''✅ 滑块验证成功，{status_text}

账号: {account_id}
时间: {time}'''
            self._execute_sql(
                cursor,
                '''
                UPDATE notification_templates
                SET template = ?, updated_at = CURRENT_TIMESTAMP
                WHERE type = 'slider_success' AND template = ?
                ''',
                (new_slider_success_template, old_slider_success_template)
            )

            logger.info("通知模板类型迁移完成")
        except Exception as e:
            logger.warning(f"迁移notification_templates表时出错（可能表不存在）: {e}")
            # 如果迁移失败，尝试清理
            try:
                cursor.execute("DROP TABLE IF EXISTS notification_templates_new")
            except:
                pass
    def check_and_upgrade_db(self, cursor):
        """检查数据库版本并执行必要的升级"""
        try:
            # 获取当前数据库版本
            current_version = self.get_system_setting("db_version") or "1.0"
            logger.info(f"当前数据库版本: {current_version}")

            if current_version == "1.0":
                logger.info("开始升级数据库到版本1.0...")
                self.update_admin_user_id(cursor)
                self.set_system_setting("db_version", "1.0", "数据库版本号")
                logger.info("数据库升级到版本1.0完成")
            
            # 如果版本低于需要升级的版本，执行升级
            if current_version < "1.1":
                logger.info("开始升级数据库到版本1.1...")
                self.upgrade_notification_channels_table(cursor)
                self.set_system_setting("db_version", "1.1", "数据库版本号")
                logger.info("数据库升级到版本1.1完成")

            # 升级到版本1.2 - 支持更多通知渠道类型
            if current_version < "1.2":
                logger.info("开始升级数据库到版本1.2...")
                self.upgrade_notification_channels_types(cursor)
                self.set_system_setting("db_version", "1.2", "数据库版本号")
                logger.info("数据库升级到版本1.2完成")

            # 升级到版本1.3 - 添加关键词类型和图片URL字段
            if current_version < "1.3":
                logger.info("开始升级数据库到版本1.3...")
                self.upgrade_keywords_table_for_image_support(cursor)
                self.set_system_setting("db_version", "1.3", "数据库版本号")
                logger.info("数据库升级到版本1.3完成")
            
            
            # 升级到版本1.4 - 添加关键词类型和图片URL字段
            if current_version < "1.4":
                logger.info("开始升级数据库到版本1.4...")
                self.upgrade_notification_channels_types(cursor)
                self.set_system_setting("db_version", "1.4", "数据库版本号")
                logger.info("数据库升级到版本1.4完成")

            # 升级到版本1.5 - 为cookies表添加账号登录字段
            if current_version < "1.5":
                logger.info("开始升级数据库到版本1.5...")
                self.upgrade_cookies_table_for_account_login(cursor)
                self.set_system_setting("db_version", "1.5", "数据库版本号")
                logger.info("数据库升级到版本1.5完成")

            # 升级到版本1.6 - 为cookies表添加代理配置字段
            if current_version < "1.6":
                logger.info("开始升级数据库到版本1.6...")
                self.upgrade_cookies_table_for_proxy(cursor)
                self.set_system_setting("db_version", "1.6", "数据库版本号")
                logger.info("数据库升级到版本1.6完成")

            # 升级到版本1.7 - 为users表添加is_admin字段
            if current_version < "1.7":
                logger.info("开始升级数据库到版本1.7...")
                self.upgrade_users_table_for_admin(cursor)
                self.set_system_setting("db_version", "1.7", "数据库版本号")
                logger.info("数据库升级到版本1.7完成")

            # 迁移遗留数据（在所有版本升级完成后执行）
            self.migrate_legacy_data(cursor)

        except Exception as e:
            logger.error(f"数据库版本检查或升级失败: {e}")
            raise
    def update_admin_user_id(self, cursor):
        """更新admin用户ID"""
        try:
            logger.info("开始更新admin用户ID...")
            # 创建默认admin用户（只在首次初始化时创建）
            cursor.execute('SELECT COUNT(*) FROM users WHERE username = ?', ('admin',))
            admin_exists = cursor.fetchone()[0] > 0

            if not admin_exists:
                # 首次创建admin用户：优先使用显式安全配置，否则生成随机密码。
                initial_admin_password = generate_initial_admin_password()
                default_password_hash = hash_user_password(initial_admin_password)
                # 检查is_admin列是否存在
                try:
                    cursor.execute('SELECT is_admin FROM users LIMIT 1')
                    cursor.execute('''
                    INSERT INTO users (username, email, password_hash, is_admin) VALUES
                    ('admin', 'admin@localhost', ?, 1)
                    ''', (default_password_hash,))
                except sqlite3.OperationalError:
                    # is_admin列不存在，使用旧的INSERT语句
                    cursor.execute('''
                    INSERT INTO users (username, email, password_hash) VALUES
                    ('admin', 'admin@localhost', ?)
                    ''', (default_password_hash,))
                logger.warning(f"创建默认admin用户，初始密码: {initial_admin_password}。请首次登录后立即修改。")

            # 获取admin用户ID，用于历史数据绑定
            self._execute_sql(cursor, "SELECT id FROM users WHERE username = 'admin'")
            admin_user = cursor.fetchone()
            if admin_user:
                admin_user_id = admin_user[0]

                # 将历史cookies数据绑定到admin用户（如果user_id列不存在）
                try:
                    self._execute_sql(cursor, "SELECT user_id FROM cookies LIMIT 1")
                except sqlite3.OperationalError:
                    # user_id列不存在，需要添加并更新历史数据
                    self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN user_id INTEGER")
                    self._execute_sql(cursor, "UPDATE cookies SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))
                else:
                    # user_id列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE cookies SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))

                # 为cookies表添加auto_confirm字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT auto_confirm FROM cookies LIMIT 1")
                except sqlite3.OperationalError:
                    # auto_confirm列不存在，需要添加并设置默认值
                    self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN auto_confirm INTEGER DEFAULT 1")
                    self._execute_sql(cursor, "UPDATE cookies SET auto_confirm = 1 WHERE auto_confirm IS NULL")
                else:
                    # auto_confirm列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE cookies SET auto_confirm = 1 WHERE auto_confirm IS NULL")

                # 为delivery_rules表添加user_id字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT user_id FROM delivery_rules LIMIT 1")
                except sqlite3.OperationalError:
                    # user_id列不存在，需要添加并更新历史数据
                    self._execute_sql(cursor, "ALTER TABLE delivery_rules ADD COLUMN user_id INTEGER")
                    self._execute_sql(cursor, "UPDATE delivery_rules SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))
                else:
                    # user_id列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE delivery_rules SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))

                # 为delivery_rules表添加今日发货统计字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT last_delivery_date FROM delivery_rules LIMIT 1")
                except sqlite3.OperationalError:
                    # 今日发货字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE delivery_rules ADD COLUMN last_delivery_date DATE")
                    self._execute_sql(cursor, "ALTER TABLE delivery_rules ADD COLUMN today_delivery_times INTEGER DEFAULT 0")
                    logger.info("已添加 last_delivery_date 和 today_delivery_times 字段到 delivery_rules 表")

                # 为notification_channels表添加user_id字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT user_id FROM notification_channels LIMIT 1")
                except sqlite3.OperationalError:
                    # user_id列不存在，需要添加并更新历史数据
                    self._execute_sql(cursor, "ALTER TABLE notification_channels ADD COLUMN user_id INTEGER")
                    self._execute_sql(cursor, "UPDATE notification_channels SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))
                else:
                    # user_id列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE notification_channels SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))

                # 为email_verifications表添加type字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT type FROM email_verifications LIMIT 1")
                except sqlite3.OperationalError:
                    # type列不存在，需要添加并更新历史数据
                    self._execute_sql(cursor, "ALTER TABLE email_verifications ADD COLUMN type TEXT DEFAULT 'register'")
                    self._execute_sql(cursor, "UPDATE email_verifications SET type = 'register' WHERE type IS NULL")
                else:
                    # type列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE email_verifications SET type = 'register' WHERE type IS NULL")

                # 为cards表添加多规格字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT is_multi_spec FROM cards LIMIT 1")
                except sqlite3.OperationalError:
                    # 多规格字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN is_multi_spec BOOLEAN DEFAULT FALSE")
                    self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN spec_name TEXT")
                    self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN spec_value TEXT")
                    logger.info("为cards表添加多规格字段")

                # 为cards表添加双规格字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT spec_name_2 FROM cards LIMIT 1")
                except sqlite3.OperationalError:
                    # 双规格字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN spec_name_2 TEXT")
                    self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN spec_value_2 TEXT")
                    logger.info("为cards表添加双规格字段(spec_name_2, spec_value_2)")

                # 为orders表添加双规格字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT spec_name_2 FROM orders LIMIT 1")
                except sqlite3.OperationalError:
                    # 双规格字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN spec_name_2 TEXT")
                    self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN spec_value_2 TEXT")
                    logger.info("为orders表添加双规格字段(spec_name_2, spec_value_2)")

                self._ensure_orders_platform_time_columns(cursor)

                # 为item_info表添加多规格字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT is_multi_spec FROM item_info LIMIT 1")
                except sqlite3.OperationalError:
                    # 多规格字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE item_info ADD COLUMN is_multi_spec BOOLEAN DEFAULT FALSE")
                    logger.info("为item_info表添加多规格字段")

                # 为item_info表添加多数量发货字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT multi_quantity_delivery FROM item_info LIMIT 1")
                except sqlite3.OperationalError:
                    # 多数量发货字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE item_info ADD COLUMN multi_quantity_delivery BOOLEAN DEFAULT FALSE")
                    logger.info("为item_info表添加多数量发货字段")

                # 处理keywords表的唯一约束问题
                # 由于SQLite不支持直接修改约束，我们需要重建表
                self._migrate_keywords_table_constraints(cursor)

            self.conn.commit()
            logger.info(f"admin用户ID更新完成")
        except Exception as e:
            logger.error(f"更新admin用户ID失败: {e}")
            raise
    def upgrade_notification_channels_table(self, cursor):
        """升级notification_channels表的type字段约束"""
        try:
            logger.info("开始升级notification_channels表...")
            
            # 检查表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notification_channels'")
            if not cursor.fetchone():
                logger.info("notification_channels表不存在，无需升级")
                return True
                
            # 检查表中是否有数据
            cursor.execute("SELECT COUNT(*) FROM notification_channels")
            count = cursor.fetchone()[0]

            # 删除可能存在的临时表
            cursor.execute("DROP TABLE IF EXISTS notification_channels_new")

            # 创建临时表
            cursor.execute('''
            CREATE TABLE notification_channels_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('qq','ding_talk')),
                config TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 复制数据，并转换不兼容的类型
            if count > 0:
                logger.info(f"复制 {count} 条通知渠道数据到新表")
                # 先查看现有数据的类型
                cursor.execute("SELECT DISTINCT type FROM notification_channels")
                existing_types = [row[0] for row in cursor.fetchall()]
                logger.info(f"现有通知渠道类型: {existing_types}")

                # 获取所有现有数据进行逐行处理
                cursor.execute("SELECT * FROM notification_channels")
                existing_data = cursor.fetchall()

                # 逐行转移数据，确保类型映射正确
                for row in existing_data:
                    old_type = row[3] if len(row) > 3 else 'qq'  # type字段，默认为qq

                    # 类型映射规则
                    type_mapping = {
                        'dingtalk': 'ding_talk',
                        'ding_talk': 'ding_talk',
                        'qq': 'qq',
                        'email': 'qq',  # 暂时映射为qq，后续版本会支持
                        'webhook': 'qq',  # 暂时映射为qq，后续版本会支持
                        'wechat': 'qq',  # 暂时映射为qq，后续版本会支持
                        'telegram': 'qq'  # 暂时映射为qq，后续版本会支持
                    }

                    new_type = type_mapping.get(old_type, 'qq')  # 默认转换为qq类型

                    if old_type != new_type:
                        logger.info(f"转换通知渠道类型: {old_type} -> {new_type}")

                    # 插入到新表
                    cursor.execute('''
                    INSERT INTO notification_channels_new
                    (id, name, user_id, type, config, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        row[0],  # id
                        row[1],  # name
                        row[2],  # user_id
                        new_type,  # type (转换后的)
                        row[4] if len(row) > 4 else '{}',  # config
                        row[5] if len(row) > 5 else True,  # enabled
                        row[6] if len(row) > 6 else None,  # created_at
                        row[7] if len(row) > 7 else None   # updated_at
                    ))
            
            # 删除旧表
            cursor.execute("DROP TABLE notification_channels")
            
            # 重命名新表
            cursor.execute("ALTER TABLE notification_channels_new RENAME TO notification_channels")
            
            logger.info("notification_channels表升级完成")
            return True
        except Exception as e:
            logger.error(f"升级notification_channels表失败: {e}")
            raise
    def upgrade_notification_channels_types(self, cursor):
        """升级notification_channels表支持更多渠道类型"""
        try:
            logger.info("开始升级notification_channels表支持更多渠道类型...")

            # 检查表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notification_channels'")
            if not cursor.fetchone():
                logger.info("notification_channels表不存在，无需升级")
                return True

            # 检查表中是否有数据
            cursor.execute("SELECT COUNT(*) FROM notification_channels")
            count = cursor.fetchone()[0]

            # 获取现有数据
            existing_data = []
            if count > 0:
                cursor.execute("SELECT * FROM notification_channels")
                existing_data = cursor.fetchall()
                logger.info(f"备份 {count} 条通知渠道数据")

            # 创建新表，支持所有通知渠道类型
            cursor.execute('''
            CREATE TABLE notification_channels_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('qq','ding_talk','dingtalk','feishu','lark','bark','email','webhook','wechat','telegram')),
                config TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 复制数据，同时处理类型映射
            if existing_data:
                logger.info(f"迁移 {len(existing_data)} 条通知渠道数据到新表")
                for row in existing_data:
                    # 处理类型映射，支持更多渠道类型
                    old_type = row[3] if len(row) > 3 else 'qq'  # type字段

                    # 完整的类型映射规则，支持所有通知渠道
                    type_mapping = {
                        'ding_talk': 'dingtalk',  # 统一为dingtalk
                        'dingtalk': 'dingtalk',
                        'qq': 'qq',
                        'feishu': 'feishu',      # 飞书通知
                        'lark': 'lark',          # 飞书通知（英文名）
                        'bark': 'bark',          # Bark通知
                        'email': 'email',        # 邮件通知
                        'webhook': 'webhook',    # Webhook通知
                        'wechat': 'wechat',      # 微信通知
                        'telegram': 'telegram'   # Telegram通知
                    }

                    new_type = type_mapping.get(old_type, 'qq')  # 默认为qq

                    if old_type != new_type:
                        logger.info(f"转换通知渠道类型: {old_type} -> {new_type}")

                    # 插入到新表，确保字段完整性
                    cursor.execute('''
                    INSERT INTO notification_channels_new
                    (id, name, user_id, type, config, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        row[0],  # id
                        row[1],  # name
                        row[2],  # user_id
                        new_type,  # type (转换后的)
                        row[4] if len(row) > 4 else '{}',  # config
                        row[5] if len(row) > 5 else True,  # enabled
                        row[6] if len(row) > 6 else None,  # created_at
                        row[7] if len(row) > 7 else None   # updated_at
                    ))

            # 删除旧表
            cursor.execute("DROP TABLE notification_channels")

            # 重命名新表
            cursor.execute("ALTER TABLE notification_channels_new RENAME TO notification_channels")

            logger.info("notification_channels表类型升级完成")
            logger.info("✅ 现在支持以下所有通知渠道类型:")
            logger.info("   - qq (QQ通知)")
            logger.info("   - ding_talk/dingtalk (钉钉通知)")
            logger.info("   - feishu/lark (飞书通知)")
            logger.info("   - bark (Bark通知)")
            logger.info("   - email (邮件通知)")
            logger.info("   - webhook (Webhook通知)")
            logger.info("   - wechat (微信通知)")
            logger.info("   - telegram (Telegram通知)")
            return True
        except Exception as e:
            logger.error(f"升级notification_channels表类型失败: {e}")
            raise
    def upgrade_cookies_table_for_account_login(self, cursor):
        """升级cookies表支持账号密码登录功能"""
        try:
            logger.info("开始为cookies表添加账号登录相关字段...")

            # 为cookies表添加username字段（如果不存在）
            try:
                self._execute_sql(cursor, "SELECT username FROM cookies LIMIT 1")
                logger.info("cookies表username字段已存在")
            except sqlite3.OperationalError:
                # username字段不存在，需要添加
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN username TEXT DEFAULT ''")
                logger.info("为cookies表添加username字段")

            # 为cookies表添加password字段（如果不存在）
            try:
                self._execute_sql(cursor, "SELECT password FROM cookies LIMIT 1")
                logger.info("cookies表password字段已存在")
            except sqlite3.OperationalError:
                # password字段不存在，需要添加
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN password TEXT DEFAULT ''")
                logger.info("为cookies表添加password字段")

            # 为cookies表添加show_browser字段（如果不存在）
            try:
                self._execute_sql(cursor, "SELECT show_browser FROM cookies LIMIT 1")
                logger.info("cookies表show_browser字段已存在")
            except sqlite3.OperationalError:
                # show_browser字段不存在，需要添加
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN show_browser INTEGER DEFAULT 0")
                logger.info("为cookies表添加show_browser字段")

            logger.info("✅ cookies表账号登录字段升级完成")
            logger.info("   - username: 用于密码登录的用户名")
            logger.info("   - password: 用于密码登录的密码")
            logger.info("   - show_browser: 登录时是否显示浏览器（0=隐藏，1=显示）")
            return True
        except Exception as e:
            logger.error(f"升级cookies表账号登录字段失败: {e}")
            raise
    def upgrade_cookies_table_for_proxy(self, cursor):
        """升级cookies表支持代理配置功能"""
        try:
            logger.info("开始为cookies表添加代理配置相关字段...")

            # 为cookies表添加proxy_type字段（代理类型：none/http/https/socks5）
            try:
                self._execute_sql(cursor, "SELECT proxy_type FROM cookies LIMIT 1")
                logger.info("cookies表proxy_type字段已存在")
            except sqlite3.OperationalError:
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN proxy_type TEXT DEFAULT 'none'")
                logger.info("为cookies表添加proxy_type字段")

            # 为cookies表添加proxy_host字段（代理服务器地址）
            try:
                self._execute_sql(cursor, "SELECT proxy_host FROM cookies LIMIT 1")
                logger.info("cookies表proxy_host字段已存在")
            except sqlite3.OperationalError:
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN proxy_host TEXT DEFAULT ''")
                logger.info("为cookies表添加proxy_host字段")

            # 为cookies表添加proxy_port字段（代理端口）
            try:
                self._execute_sql(cursor, "SELECT proxy_port FROM cookies LIMIT 1")
                logger.info("cookies表proxy_port字段已存在")
            except sqlite3.OperationalError:
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN proxy_port INTEGER DEFAULT 0")
                logger.info("为cookies表添加proxy_port字段")

            # 为cookies表添加proxy_user字段（代理认证用户名）
            try:
                self._execute_sql(cursor, "SELECT proxy_user FROM cookies LIMIT 1")
                logger.info("cookies表proxy_user字段已存在")
            except sqlite3.OperationalError:
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN proxy_user TEXT DEFAULT ''")
                logger.info("为cookies表添加proxy_user字段")

            # 为cookies表添加proxy_pass字段（代理认证密码）
            try:
                self._execute_sql(cursor, "SELECT proxy_pass FROM cookies LIMIT 1")
                logger.info("cookies表proxy_pass字段已存在")
            except sqlite3.OperationalError:
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN proxy_pass TEXT DEFAULT ''")
                logger.info("为cookies表添加proxy_pass字段")

            logger.info("✅ cookies表代理配置字段升级完成")
            logger.info("   - proxy_type: 代理类型 (none/http/https/socks5)")
            logger.info("   - proxy_host: 代理服务器地址")
            logger.info("   - proxy_port: 代理端口")
            logger.info("   - proxy_user: 代理认证用户名（可选）")
            logger.info("   - proxy_pass: 代理认证密码（可选）")
            return True
        except Exception as e:
            logger.error(f"升级cookies表代理配置字段失败: {e}")
            raise
    def upgrade_users_table_for_admin(self, cursor):
        """升级users表支持管理员权限字段"""
        try:
            logger.info("开始为users表添加管理员权限字段...")

            # 为users表添加is_admin字段（如果不存在）
            try:
                self._execute_sql(cursor, "SELECT is_admin FROM users LIMIT 1")
                logger.info("users表is_admin字段已存在")
            except sqlite3.OperationalError:
                # is_admin字段不存在，需要添加
                self._execute_sql(cursor, "ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE")
                logger.info("为users表添加is_admin字段")

            # 将admin用户设置为管理员
            self._execute_sql(cursor, "UPDATE users SET is_admin = 1 WHERE username = 'admin'")
            logger.info("已将admin用户设置为管理员")

            logger.info("✅ users表管理员权限字段升级完成")
            logger.info("   - is_admin: 是否为管理员 (0=普通用户, 1=管理员)")
            return True
        except Exception as e:
            logger.error(f"升级users表管理员权限字段失败: {e}")
            raise
    def migrate_legacy_data(self, cursor):
        """迁移遗留数据到新表结构"""
        try:
            logger.info("开始检查和迁移遗留数据...")

            # 检查是否有需要迁移的老表
            legacy_tables = [
                'old_notification_channels',
                'legacy_delivery_rules',
                'old_keywords',
                'backup_cookies'
            ]

            for table_name in legacy_tables:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                if cursor.fetchone():
                    logger.info(f"发现遗留表: {table_name}，开始迁移数据...")
                    self._migrate_table_data(cursor, table_name)

            logger.info("遗留数据迁移完成")
            return True
        except Exception as e:
            logger.error(f"迁移遗留数据失败: {e}")
            return False
    def _migrate_table_data(self, cursor, table_name: str):
        """迁移指定表的数据"""
        try:
            if table_name == 'old_notification_channels':
                # 迁移通知渠道数据
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]

                if count > 0:
                    cursor.execute(f"SELECT * FROM {table_name}")
                    old_data = cursor.fetchall()

                    for row in old_data:
                        # 处理数据格式转换
                        cursor.execute('''
                        INSERT OR IGNORE INTO notification_channels
                        (name, user_id, type, config, enabled)
                        VALUES (?, ?, ?, ?, ?)
                        ''', (
                            row[1] if len(row) > 1 else f"迁移渠道_{row[0]}",
                            row[2] if len(row) > 2 else 1,  # 默认admin用户
                            self._normalize_channel_type(row[3] if len(row) > 3 else 'qq'),
                            row[4] if len(row) > 4 else '{}',
                            row[5] if len(row) > 5 else True
                        ))

                    logger.info(f"成功迁移 {count} 条通知渠道数据")

                    # 迁移完成后删除老表
                    cursor.execute(f"DROP TABLE {table_name}")
                    logger.info(f"已删除遗留表: {table_name}")

        except Exception as e:
            logger.error(f"迁移表 {table_name} 数据失败: {e}")
    def _normalize_channel_type(self, old_type: str) -> str:
        """标准化通知渠道类型"""
        type_mapping = {
            'ding_talk': 'dingtalk',
            'dingtalk': 'dingtalk',
            'qq': 'qq',
            'email': 'email',
            'webhook': 'webhook',
            'wechat': 'wechat',
            'telegram': 'telegram',
            # 处理一些可能的变体
            'dingding': 'dingtalk',
            'weixin': 'wechat',
            'tg': 'telegram'
        }
        return type_mapping.get(old_type.lower(), 'qq')
    def _migrate_keywords_table_constraints(self, cursor):
        """迁移keywords表的约束，支持基于商品ID的唯一性校验"""
        try:
            # 检查是否已经迁移过（通过检查是否存在新的唯一索引）
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_keywords_unique_with_item'")
            if cursor.fetchone():
                logger.info("keywords表约束已经迁移过，跳过")
                return

            logger.info("开始迁移keywords表约束...")

            # 1. 创建临时表，不设置主键约束
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS keywords_temp (
                cookie_id TEXT,
                keyword TEXT,
                reply TEXT,
                item_id TEXT,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 2. 复制现有数据到临时表
            cursor.execute('''
            INSERT INTO keywords_temp (cookie_id, keyword, reply, item_id)
            SELECT cookie_id, keyword, reply, item_id FROM keywords
            ''')

            # 3. 删除原表
            cursor.execute('DROP TABLE keywords')

            # 4. 重命名临时表
            cursor.execute('ALTER TABLE keywords_temp RENAME TO keywords')

            # 5. 创建复合唯一索引来实现我们需要的约束逻辑
            # 对于item_id为空的情况：(cookie_id, keyword)必须唯一
            cursor.execute('''
            CREATE UNIQUE INDEX idx_keywords_unique_no_item
            ON keywords(cookie_id, keyword)
            WHERE item_id IS NULL OR item_id = ''
            ''')

            # 对于item_id不为空的情况：(cookie_id, keyword, item_id)必须唯一
            cursor.execute('''
            CREATE UNIQUE INDEX idx_keywords_unique_with_item
            ON keywords(cookie_id, keyword, item_id)
            WHERE item_id IS NOT NULL AND item_id != ''
            ''')

            logger.info("keywords表约束迁移完成")

        except Exception as e:
            logger.error(f"迁移keywords表约束失败: {e}")
            # 如果迁移失败，尝试回滚
            try:
                cursor.execute('DROP TABLE IF EXISTS keywords_temp')
            except:
                pass
            raise
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None
    def get_connection(self):
        """获取数据库连接，如果已关闭则重新连接"""
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return self.conn
    def _log_sql(self, sql: str, params: tuple = None, operation: str = "EXECUTE"):
        """记录SQL执行日志"""
        if not self.sql_log_enabled:
            return

        # 格式化SQL（移除多余空白）
        formatted_sql = ' '.join(sql.split())
        sql_lower = formatted_sql.lower()
        sensitive_keywords = ('password', 'proxy_pass', 'smtp_password', 'admin_password_hash')
        contains_sensitive = any(keyword in sql_lower for keyword in sensitive_keywords)

        # 格式化参数
        params_str = ""
        if params:
            # 包含敏感字段的SQL统一脱敏参数，避免日志泄露密码等敏感信息
            if contains_sensitive:
                if isinstance(params, (list, tuple)):
                    params_str = f" | 参数: [***敏感参数已脱敏，共{len(params)}项***]"
                else:
                    params_str = " | 参数: [***敏感参数已脱敏***]"
            elif isinstance(params, (list, tuple)):
                if len(params) > 0:
                    # 限制参数长度，避免日志过长
                    formatted_params = []
                    for param in params:
                        if isinstance(param, str) and len(param) > 100:
                            formatted_params.append(f"{param[:100]}...")
                        else:
                            formatted_params.append(repr(param))
                    params_str = f" | 参数: [{', '.join(formatted_params)}]"
            else:
                params_str = f" | 参数: {repr(params)}"

        encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
        icon = "SQL" if encoding and "utf" not in encoding else "🗄️ SQL"
        log_message = f"{icon} {operation}: {formatted_sql}{params_str}"

        if self.sql_log_level == 'DEBUG':
            logger.debug(log_message)
        elif self.sql_log_level == 'INFO':
            logger.info(log_message)
        elif self.sql_log_level == 'WARNING':
            logger.warning(log_message)
        else:
            logger.debug(log_message)
    def _execute_sql(self, cursor, sql: str, params: tuple = None):
        """执行SQL并记录日志"""
        self._log_sql(sql, params, "EXECUTE")
        if params:
            return cursor.execute(sql, params)
        else:
            return cursor.execute(sql)
    def _executemany_sql(self, cursor, sql: str, params_list):
        """批量执行SQL并记录日志"""
        self._log_sql(sql, f"批量执行 {len(params_list)} 条记录", "EXECUTEMANY")
        return cursor.executemany(sql, params_list)
    def execute_query(self, sql: str, params: tuple = None):
        """执行查询并返回结果"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)
                return cursor.fetchall()
            except Exception as e:
                logger.error(f"执行查询失败: {e}")
                raise
    def get_table_data(self, table_name: str):
        """获取指定表的所有数据"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 获取表结构
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns_info = cursor.fetchall()
                columns = [col[1] for col in columns_info]  # 列名

                # 获取表数据
                cursor.execute(f"SELECT * FROM {table_name}")
                rows = cursor.fetchall()

                # 转换为字典列表
                data = []
                for row in rows:
                    row_dict = {}
                    for i, value in enumerate(row):
                        row_dict[columns[i]] = value
                    data.append(row_dict)

                return data, columns

            except Exception as e:
                logger.error(f"获取表数据失败: {table_name} - {e}")
                return [], []
    def _is_valid_buyer_id(buyer_id) -> bool:
        """检查 buyer_id 是否为有效值（非占位符）"""
        if not buyer_id:
            return False
        normalized_buyer_id = str(buyer_id).strip()
        if normalized_buyer_id.endswith('@goofish'):
            normalized_buyer_id = normalized_buyer_id.split('@')[0].strip()
        if normalized_buyer_id in DBManager._INVALID_BUYER_IDS:
            return False
        if normalized_buyer_id.isdigit() and len(normalized_buyer_id) <= 2:
            return False
        return True
    def _sanitize_order_buyer_nick(self, buyer_nick: str = None) -> str:
        """过滤订单买家昵称中的系统通知标题，避免订单列表展示“工作台通知”等文案。"""
        if buyer_nick is None:
            return None

        text = str(buyer_nick).strip()
        if not text:
            return None

        invalid_exact_titles = {
            "订单",
            "全部",
            "交易消息",
            "等待你发货",
            "买家",
            "工作台通知",
            "我完成了评价",
            "你人真不错，送你闲鱼小红花",
            "卖家人不错？送Ta闲鱼小红花",
            "快给ta一个评价吧～",
            "快给ta一个评价吧~",
        }
        if text in invalid_exact_titles:
            logger.info(f"忽略系统标题型订单买家昵称: {text}")
            return None

        invalid_keywords = (
            "小红花", "待付款", "待发货", "待刀成", "成功小刀", "闲鱼",
            "交易", "收货", "退款", "评价", "发货", "付款", "拍下",
            "确认", "关闭", "鼓励", "真不错", "全部", "订单",
        )
        if any(keyword in text for keyword in invalid_keywords):
            logger.info(f"忽略系统关键词型订单买家昵称: {text}")
            return None

        return text
    def _resolve_order_buyer_nick_for_write(self, order_id: str, buyer_nick: str = None, existing_buyer_nick: str = None) -> str:
        sanitized_incoming = self._sanitize_order_buyer_nick(buyer_nick)
        if sanitized_incoming:
            return sanitized_incoming

        sanitized_existing = self._sanitize_order_buyer_nick(existing_buyer_nick)
        if sanitized_existing:
            return sanitized_existing

        return None
    def delete_table_record(self, table_name: str, record_id: str):
        """删除指定表的指定记录"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 根据表名确定主键字段
                primary_key_map = {
                    'users': 'id',
                    'cookies': 'id',
                    'cookie_status': 'id',
                    'keywords': 'id',
                    'default_replies': 'id',
                    'default_reply_records': 'id',
                    'item_replay': 'item_id',
                    'ai_reply_settings': 'id',
                    'ai_conversations': 'id',
                    'ai_item_cache': 'id',
                    'item_info': 'id',
                    'message_notifications': 'id',
                    'cards': 'id',
                    'delivery_rules': 'id',
                    'notification_channels': 'id',
                    'user_settings': 'id',
                    'system_settings': 'id',
                    'email_verifications': 'id',
                    'captcha_codes': 'id',
                    'orders': 'order_id'
                }

                primary_key = primary_key_map.get(table_name, 'id')

                # 删除记录
                cursor.execute(f"DELETE FROM {table_name} WHERE {primary_key} = ?", (record_id,))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除表记录成功: {table_name}.{record_id}")
                    return True
                else:
                    logger.warning(f"删除表记录失败，记录不存在: {table_name}.{record_id}")
                    return False

            except Exception as e:
                logger.error(f"删除表记录失败: {table_name}.{record_id} - {e}")
                self.conn.rollback()
                return False
    def clear_table_data(self, table_name: str):
        """清空指定表的所有数据"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 清空表数据
                cursor.execute(f"DELETE FROM {table_name}")

                # 重置自增ID（如果有的话）
                cursor.execute(f"DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))

                self.conn.commit()
                logger.info(f"清空表数据成功: {table_name}")
                return True

            except Exception as e:
                logger.error(f"清空表数据失败: {table_name} - {e}")
                self.conn.rollback()
                return False
    def upgrade_keywords_table_for_image_support(self, cursor):
        """升级keywords表以支持图片关键词"""
        try:
            logger.info("开始升级keywords表以支持图片关键词...")

            # 检查是否已经有type字段
            cursor.execute("PRAGMA table_info(keywords)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'type' not in columns:
                logger.info("添加type字段到keywords表...")
                cursor.execute("ALTER TABLE keywords ADD COLUMN type TEXT DEFAULT 'text'")

            if 'image_url' not in columns:
                logger.info("添加image_url字段到keywords表...")
                cursor.execute("ALTER TABLE keywords ADD COLUMN image_url TEXT")

            # 为现有记录设置默认类型
            cursor.execute("UPDATE keywords SET type = 'text' WHERE type IS NULL")

            logger.info("keywords表升级完成")
            return True

        except Exception as e:
            logger.error(f"升级keywords表失败: {e}")
            raise
    def cleanup_old_data(self, days: int = 90) -> dict:
        """清理过期的历史数据，防止数据库无限增长
        
        Args:
            days: 保留最近N天的数据，默认90天
            
        Returns:
            清理统计信息
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                stats = {}
                
                # 清理AI对话历史（保留最近90天）
                try:
                    cursor.execute(
                        "DELETE FROM ai_conversations WHERE created_at < datetime('now', '-' || ? || ' days')",
                        (days,)
                    )
                    stats['ai_conversations'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的AI对话记录（{days}天前）")
                except Exception as e:
                    logger.warning(f"清理AI对话历史失败: {e}")
                    stats['ai_conversations'] = 0
                
                # 清理风控日志（保留最近90天）
                try:
                    cursor.execute(
                        "DELETE FROM risk_control_logs WHERE created_at < datetime('now', '-' || ? || ' days')",
                        (days,)
                    )
                    stats['risk_control_logs'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的风控日志（{days}天前）")
                except Exception as e:
                    logger.warning(f"清理风控日志失败: {e}")
                    stats['risk_control_logs'] = 0
                
                # 清理AI商品缓存（保留最近30天）
                cache_days = min(days, 30)  # AI商品缓存最多保留30天
                try:
                    cursor.execute(
                        "DELETE FROM ai_item_cache WHERE last_updated < datetime('now', '-' || ? || ' days')",
                        (cache_days,)
                    )
                    stats['ai_item_cache'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的AI商品缓存（{cache_days}天前）")
                except Exception as e:
                    logger.warning(f"清理AI商品缓存失败: {e}")
                    stats['ai_item_cache'] = 0
                
                # 清理验证码记录（保留最近1天）
                try:
                    cursor.execute(
                        "DELETE FROM captcha_codes WHERE created_at < datetime('now', '-1 day')"
                    )
                    stats['captcha_codes'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的验证码记录")
                except Exception as e:
                    logger.warning(f"清理验证码记录失败: {e}")
                    stats['captcha_codes'] = 0
                
                # 清理邮箱验证记录（保留最近7天）
                try:
                    cursor.execute(
                        "DELETE FROM email_verifications WHERE created_at < datetime('now', '-7 days')"
                    )
                    stats['email_verifications'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的邮箱验证记录")
                except Exception as e:
                    logger.warning(f"清理邮箱验证记录失败: {e}")
                    stats['email_verifications'] = 0
                
                # 提交更改
                self.conn.commit()
                
                # 执行VACUUM以释放磁盘空间（仅当清理了大量数据时）
                total_cleaned = sum(stats.values())
                if total_cleaned > 100:
                    logger.info(f"共清理了 {total_cleaned} 条记录，执行VACUUM以释放磁盘空间...")
                    cursor.execute("VACUUM")
                    logger.info("VACUUM执行完成")
                    stats['vacuum_executed'] = True
                else:
                    stats['vacuum_executed'] = False
                
                stats['total_cleaned'] = total_cleaned
                return stats
                
        except Exception as e:
            logger.error(f"清理历史数据时出错: {e}")
            return {'error': str(e)}
