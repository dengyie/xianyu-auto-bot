"""XianyuLive 的通知分发 / 消息管线 / 发送与回复内容 Mixin（P2-x 步骤④b）。

方法经 self/cls 操作宿主实例状态；XianyuAutoAsync 模块级剩余符号经 `_host`
代理调用时解析（兼容测试替换）；db_manager 逐方法保留原 seam
（方法体内惰性导入 = 包属性，否则 = 宿主绑定）。
"""
import asyncio
import base64
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


class _HostProxy:
    """属性访问转发到 XianyuAutoAsync 模块级符号（调用时解析）。"""

    def __getattr__(self, name):
        import XianyuAutoAsync

        return getattr(XianyuAutoAsync, name)


_host = _HostProxy()

# 自宿主模块迁入（被搬方法的默认参数在类创建期求值，不能用 _host）
DELIVERY_BATCH_MAX_UNITS = 10
DELIVERY_BATCH_MAX_CHARS = 1200


def _db_package():
    """惰性包属性：等价于原方法体内的 from db_manager import db_manager。"""
    from db_manager import db_manager

    return db_manager


def _db_host():
    """宿主绑定：等价于原模块级 from-import 名字（import 期绑定）。"""
    import XianyuAutoAsync

    return XianyuAutoAsync.db_manager


class NotificationMixin:
    """多渠道通知分发（QQ/钉钉/飞书/Bark/邮件/Webhook/微信/TG）。"""

    async def send_notification(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str = None, chat_id: str = None):
        """发送消息通知"""
        try:
            import hashlib

            # 过滤系统默认消息，不发送通知
            system_messages = [
                '发来一条消息',
                '发来一条新消息'
            ]

            if send_message in system_messages:
                logger.warning(f"📱 系统消息不发送通知: {send_message}")
                return

            # 生成通知的唯一标识（基于消息内容、chat_id、send_user_id）
            # 用于防重复发送
            notification_key = f"{chat_id or 'unknown'}_{send_user_id}_{send_message}"
            notification_hash = _host.hashlib.md5(notification_key.encode('utf-8')).hexdigest()
            reservation_key = f"msg:{notification_hash}"
            
            # 使用异步锁保护防重复检查，确保并发安全
            async with self.notification_lock:
                # 检查是否在冷却时间内已发送过相同的通知
                current_time = time.time()
                if notification_hash in self.last_notification_time:
                    time_since_last = current_time - self.last_notification_time[notification_hash]
                    if time_since_last < self.notification_cooldown:
                        remaining_seconds = int(self.notification_cooldown - time_since_last)
                        logger.warning(f"📱 通知在冷却期内（剩余 {remaining_seconds} 秒），跳过重复发送 - 账号: {self.cookie_id}, 买家: {send_user_name}, 消息: {send_message[:30]}...")
                        return
                if reservation_key in self.pending_notification_keys:
                    logger.warning(f"📱 相同消息通知正在发送中，跳过重复发送 - 账号: {self.cookie_id}, 买家: {send_user_name}")
                    return
                self.pending_notification_keys.add(reservation_key)

            try:
                logger.info(f"📱 开始发送消息通知 - 账号: {self.cookie_id}, 买家: {send_user_name}")

                notification_msg = _host.render_notification_template(
                    'message',
                    account_id=self.cookie_id,
                    buyer_name=send_user_name,
                    buyer_id=send_user_id,
                    item_id=item_id or '未知',
                    chat_id=chat_id or '未知',
                    message=send_message,
                    time=time.strftime('%Y-%m-%d %H:%M:%S')
                )

                notification_sent = await _host.dispatch_account_notifications(
                    self.cookie_id,
                    notification_msg,
                    title='接收消息通知',
                    notification_type='message',
                )

                if not notification_sent:
                    logger.warning(f"📱 消息通知未发送成功，不进入冷却 - 账号: {self.cookie_id}, 买家: {send_user_name}")
                    return

                async with self.notification_lock:
                    sent_time = time.time()
                    self.last_notification_time[notification_hash] = sent_time
                    expired_keys = [
                        key for key, timestamp in self.last_notification_time.items()
                        if sent_time - timestamp > 3600
                    ]
                    for key in expired_keys:
                        del self.last_notification_time[key]
            finally:
                async with self.notification_lock:
                    self.pending_notification_keys.discard(reservation_key)

        except Exception as e:
            logger.error(f"📱 处理消息通知失败: {self._safe_str(e)}")
            import traceback
            logger.error(f"📱 详细错误信息: {traceback.format_exc()}")
    async def _send_qq_notification(self, config_data: dict, message: str):
        """发送QQ通知"""
        try:
            import aiohttp

            logger.info(f"📱 QQ通知 - 开始处理配置数据: {config_data}")

            # 解析配置（QQ号码）
            qq_number = config_data.get('qq_number') or config_data.get('config', '')
            qq_number = qq_number.strip() if qq_number else ''

            logger.info(f"📱 QQ通知 - 解析到QQ号码: {qq_number}")

            if not qq_number:
                logger.warning("📱 QQ通知 - QQ号码配置为空，无法发送通知")
                return False

            # 构建请求URL
            api_url = "http://36.111.68.231:3000/sendPrivateMsg"
            params = {
                'qq': qq_number,
                'msg': message
            }

            logger.info(f"📱 QQ通知 - 请求URL: {api_url}")
            logger.info(f"📱 QQ通知 - 请求参数: qq={qq_number}, msg长度={len(message)}")

            # 发送GET请求
            async with _host.aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, timeout=10) as response:
                    response_text = await response.text()
                    logger.info(f"📱 QQ通知 - 响应状态: {response.status}")

                    # 需求：502 视为成功，且不打印返回内容
                    if response.status == 502:
                        logger.info(f"📱 QQ通知发送成功: {qq_number} (状态码: {response.status})")
                        return True
                    elif response.status == 200:
                        logger.info(f"📱 QQ通知发送成功: {qq_number} (状态码: {response.status})")
                        logger.warning(f"📱 QQ通知 - 响应内容: {response_text}")
                        return True
                    else:
                        logger.warning(f"📱 QQ通知发送失败: HTTP {response.status}")
                        logger.warning(f"📱 QQ通知 - 响应内容: {response_text}")
                        return False

        except Exception as e:
            logger.error(f"📱 发送QQ通知异常: {self._safe_str(e)}")
            import traceback
            logger.error(f"📱 QQ通知异常详情: {traceback.format_exc()}")
            return False
    async def _send_dingtalk_notification(self, config_data: dict, message: str):
        """发送钉钉通知"""
        try:
            import aiohttp
            import json
            import hmac
            import hashlib
            import base64
            import time

            # 解析配置
            webhook_url = config_data.get('webhook_url') or config_data.get('config', '')
            secret = config_data.get('secret', '')

            webhook_url = webhook_url.strip() if webhook_url else ''
            if not webhook_url:
                logger.warning("钉钉通知配置为空")
                return False

            # 如果有加签密钥，生成签名
            if secret:
                timestamp = str(round(time.time() * 1000))
                secret_enc = secret.encode('utf-8')
                string_to_sign = f'{timestamp}\n{secret}'
                string_to_sign_enc = string_to_sign.encode('utf-8')
                hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=_host.hashlib.sha256).digest()
                sign = base64.b64encode(hmac_code).decode('utf-8')
                webhook_url += f'&timestamp={timestamp}&sign={sign}'

            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "闲鱼管理系统通知",
                    "text": message
                }
            }

            async with _host.aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data, timeout=10) as response:
                    if response.status == 200:
                        logger.info(f"钉钉通知发送成功")
                        return True
                    else:
                        logger.warning(f"钉钉通知发送失败: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"发送钉钉通知异常: {self._safe_str(e)}")
            return False
    async def _send_feishu_notification(self, config_data: dict, message: str):
        """发送飞书通知"""
        try:
            import aiohttp
            import json
            import hmac
            import hashlib
            import base64

            logger.info(f"📱 飞书通知 - 开始处理配置数据: {config_data}")

            # 解析配置
            webhook_url = config_data.get('webhook_url', '')
            secret = config_data.get('secret', '')

            logger.info(f"📱 飞书通知 - Webhook URL: {webhook_url[:50]}...")
            logger.info(f"📱 飞书通知 - 是否有签名密钥: {'是' if secret else '否'}")

            if not webhook_url:
                logger.warning("📱 飞书通知 - Webhook URL配置为空，无法发送通知")
                return False

            # 如果有加签密钥，生成签名
            timestamp = str(int(time.time()))
            sign = ""

            if secret:
                string_to_sign = f'{timestamp}\n{secret}'
                hmac_code = hmac.new(
                    string_to_sign.encode('utf-8'),
                    ''.encode('utf-8'),
                    digestmod=_host.hashlib.sha256
                ).digest()
                sign = base64.b64encode(hmac_code).decode('utf-8')
                logger.info(f"📱 飞书通知 - 已生成签名")

            # 构建请求数据
            data = {
                "msg_type": "text",
                "content": {
                    "text": message
                },
                "timestamp": timestamp
            }

            # 如果有签名，添加到请求数据中
            if sign:
                data["sign"] = sign

            logger.info(f"📱 飞书通知 - 请求数据构建完成")

            # 发送POST请求
            async with _host.aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data, timeout=10) as response:
                    response_text = await response.text()
                    logger.info(f"📱 飞书通知 - 响应状态: {response.status}")
                    logger.info(f"📱 飞书通知 - 响应内容: {response_text}")

                    if response.status == 200:
                        try:
                            response_json = json.loads(response_text)
                            if response_json.get('code') == 0:
                                logger.info(f"📱 飞书通知发送成功")
                                return True
                            else:
                                logger.warning(f"📱 飞书通知发送失败: {response_json.get('msg', '未知错误')}")
                                return False
                        except json.JSONDecodeError:
                            logger.info(f"📱 飞书通知发送成功（响应格式异常）")
                            return True
                    else:
                        logger.warning(f"📱 飞书通知发送失败: HTTP {response.status}, 响应: {response_text}")
                        return False

        except Exception as e:
            logger.error(f"📱 发送飞书通知异常: {self._safe_str(e)}")
            import traceback
            logger.error(f"📱 飞书通知异常详情: {traceback.format_exc()}")
            return False
    async def _send_bark_notification(self, config_data: dict, message: str):
        """发送Bark通知"""
        try:
            import aiohttp
            import json
            from urllib.parse import quote

            logger.info(f"📱 Bark通知 - 开始处理配置数据: {config_data}")

            # 解析配置
            server_url = config_data.get('server_url', 'https://api.day.app').rstrip('/')
            device_key = config_data.get('device_key', '')
            title = config_data.get('title', '闲鱼管理系统通知')
            sound = config_data.get('sound', 'default')
            icon = config_data.get('icon', '')
            group = config_data.get('group', 'xianyu')
            url = config_data.get('url', '')

            logger.info(f"📱 Bark通知 - 服务器: {server_url}")
            logger.info(f"📱 Bark通知 - 设备密钥: {device_key[:10]}..." if device_key else "📱 Bark通知 - 设备密钥: 未设置")
            logger.info(f"📱 Bark通知 - 标题: {title}")

            if not device_key:
                logger.warning("📱 Bark通知 - 设备密钥配置为空，无法发送通知")
                return False

            # 构建请求URL和数据
            # Bark支持两种方式：URL路径方式和POST JSON方式
            # 这里使用POST JSON方式，更灵活且支持更多参数

            api_url = f"{server_url}/push"

            # 构建请求数据
            data = {
                "device_key": device_key,
                "title": title,
                "body": message,
                "sound": sound,
                "group": group
            }

            # 可选参数
            if icon:
                data["icon"] = icon
            if url:
                data["url"] = url

            logger.info(f"📱 Bark通知 - API地址: {api_url}")
            logger.info(f"📱 Bark通知 - 请求数据构建完成")

            # 发送POST请求
            async with _host.aiohttp.ClientSession() as session:
                async with session.post(api_url, json=data, timeout=10) as response:
                    response_text = await response.text()
                    logger.info(f"📱 Bark通知 - 响应状态: {response.status}")
                    logger.info(f"📱 Bark通知 - 响应内容: {response_text}")

                    if response.status == 200:
                        try:
                            response_json = json.loads(response_text)
                            if response_json.get('code') == 200:
                                logger.info(f"📱 Bark通知发送成功")
                                return True
                            else:
                                logger.warning(f"📱 Bark通知发送失败: {response_json.get('message', '未知错误')}")
                                return False
                        except json.JSONDecodeError:
                            # 某些Bark服务器可能返回纯文本
                            if 'success' in response_text.lower() or 'ok' in response_text.lower():
                                logger.info(f"📱 Bark通知发送成功")
                                return True
                            else:
                                logger.warning(f"📱 Bark通知响应格式异常: {response_text}")
                                return False
                    else:
                        logger.warning(f"📱 Bark通知发送失败: HTTP {response.status}, 响应: {response_text}")
                        return False

        except Exception as e:
            logger.error(f"📱 发送Bark通知异常: {self._safe_str(e)}")
            import traceback
            logger.error(f"📱 Bark通知异常详情: {traceback.format_exc()}")
            return False
    async def _send_email_notification(self, config_data: dict, message: str, attachment_path: str = None):
        """发送邮件通知（支持附件）
        
        Args:
            config_data: 邮件配置
            message: 邮件正文
            attachment_path: 附件文件路径（可选）
        """
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from email.mime.image import MIMEImage
            import os

            # 解析配置
            smtp_server = config_data.get('smtp_server', '')
            smtp_port = int(config_data.get('smtp_port', 587))
            email_user = config_data.get('email_user', '')
            email_password = config_data.get('email_password', '')
            recipient_email = config_data.get('recipient_email', '')
            smtp_use_tls = config_data.get('smtp_use_tls', smtp_port == 587)  # 修复：添加变量定义

            if not all([smtp_server, email_user, email_password, recipient_email]):
                logger.warning("邮件通知配置不完整")
                return False

            # 创建邮件
            msg = MIMEMultipart()
            msg['From'] = email_user
            msg['To'] = recipient_email
            msg['Subject'] = "闲鱼管理系统通知"

            # 添加邮件正文
            msg.attach(MIMEText(message, 'plain', 'utf-8'))

            # 添加附件（如果有）
            if attachment_path and os.path.exists(attachment_path):
                try:
                    with open(attachment_path, 'rb') as f:
                        img_data = f.read()
                    
                    # 根据文件扩展名判断MIME类型
                    filename = os.path.basename(attachment_path)
                    if attachment_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        img = MIMEImage(img_data)
                        img.add_header('Content-Disposition', 'attachment', filename=filename)
                        msg.attach(img)
                        logger.info(f"已添加图片附件: {filename}")
                    else:
                        from email.mime.application import MIMEApplication
                        attach = MIMEApplication(img_data)
                        attach.add_header('Content-Disposition', 'attachment', filename=filename)
                        msg.attach(attach)
                        logger.info(f"已添加附件: {filename}")
                except Exception as attach_error:
                    logger.error(f"添加邮件附件失败: {self._safe_str(attach_error)}")

            # 发送邮件
            server = None
            try:
                if smtp_port == 465:
                    # 使用SSL连接（端口465）
                    server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
                else:
                    # 使用普通连接，然后升级到TLS（端口587）
                    server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
                    if smtp_use_tls:
                        server.starttls()
                
                # 尝试登录
                try:
                    server.login(email_user, email_password)
                except smtplib.SMTPAuthenticationError as auth_error:
                    error_code = auth_error.smtp_code if hasattr(auth_error, 'smtp_code') else None
                    error_msg = str(auth_error)
                    
                    # 提供详细的错误提示
                    logger.error(f"邮件SMTP认证失败 (错误码: {error_code})")
                    logger.error(f"邮箱地址: {email_user}")
                    logger.error(f"SMTP服务器: {smtp_server}:{smtp_port}")
                    logger.error(f"错误详情: {error_msg}")
                    
                    # 根据常见错误提供解决建议
                    suggestions = []
                    if 'qq.com' in email_user.lower() or 'qq' in smtp_server.lower():
                        suggestions.append("QQ邮箱需要使用授权码而不是登录密码")
                        suggestions.append("请到QQ邮箱设置 -> 账户 -> 开启SMTP服务 -> 生成授权码")
                    elif 'gmail.com' in email_user.lower() or 'gmail' in smtp_server.lower():
                        suggestions.append("Gmail需要使用应用专用密码")
                        suggestions.append("请到Google账户 -> 安全性 -> 两步验证 -> 应用专用密码")
                        suggestions.append("或启用'允许不够安全的应用访问'（不推荐）")
                    elif '163.com' in email_user.lower() or '126.com' in email_user.lower() or 'yeah.net' in email_user.lower():
                        suggestions.append("网易邮箱需要使用授权码")
                        suggestions.append("请到邮箱设置 -> POP3/SMTP/IMAP -> 开启SMTP服务 -> 生成授权码")
                    else:
                        suggestions.append("请检查邮箱密码/授权码是否正确")
                        suggestions.append("某些邮箱服务商需要使用授权码而不是登录密码")
                        suggestions.append("请查看邮箱服务商的SMTP设置说明")
                    
                    if suggestions:
                        logger.error("解决建议:")
                        for i, suggestion in enumerate(suggestions, 1):
                            logger.error(f"  {i}. {suggestion}")
                    
                    raise  # 重新抛出异常
                
                server.send_message(msg)
                logger.info(f"邮件通知发送成功: {recipient_email}")
                return True

            finally:
                # 确保关闭连接
                if server:
                    try:
                        server.quit()
                    except Exception:
                        try:
                            server.close()
                        except Exception:
                            pass

        except smtplib.SMTPAuthenticationError:
            # 认证错误已在上面处理，这里不再重复记录
            return False
        except smtplib.SMTPException as smtp_error:
            logger.error(f"SMTP协议错误: {self._safe_str(smtp_error)}")
            logger.error(f"SMTP服务器: {smtp_server}:{smtp_port}")
            logger.error(f"请检查SMTP服务器地址和端口配置是否正确")
            return False
        except Exception as e:
            logger.error(f"发送邮件通知异常: {self._safe_str(e)}")
            import traceback
            logger.error(f"邮件发送详细错误: {traceback.format_exc()}")
            return False
    async def _send_webhook_notification(self, config_data: dict, message: str):
        """发送Webhook通知"""
        try:
            import aiohttp
            import json

            # 解析配置
            webhook_url = config_data.get('webhook_url', '')
            http_method = config_data.get('http_method', 'POST').upper()
            headers_str = config_data.get('headers', '{}')

            if not webhook_url:
                logger.warning("Webhook通知配置为空")
                return False

            # 解析自定义请求头
            try:
                custom_headers = json.loads(headers_str) if headers_str else {}
            except json.JSONDecodeError:
                custom_headers = {}

            # 设置默认请求头
            headers = {'Content-Type': 'application/json'}
            headers.update(custom_headers)

            # 构建请求数据
            data = {
                'message': message,
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'source': 'xianyu-auto-bot'
            }

            async with _host.aiohttp.ClientSession() as session:
                if http_method == 'POST':
                    async with session.post(webhook_url, json=data, headers=headers, timeout=10) as response:
                        if response.status == 200:
                            logger.info(f"Webhook通知发送成功")
                            return True
                        else:
                            logger.warning(f"Webhook通知发送失败: {response.status}")
                            return False
                elif http_method == 'PUT':
                    async with session.put(webhook_url, json=data, headers=headers, timeout=10) as response:
                        if response.status == 200:
                            logger.info(f"Webhook通知发送成功")
                            return True
                        else:
                            logger.warning(f"Webhook通知发送失败: {response.status}")
                            return False
                else:
                    logger.warning(f"不支持的HTTP方法: {http_method}")
                    return False

        except Exception as e:
            logger.error(f"发送Webhook通知异常: {self._safe_str(e)}")
            return False
    async def _send_wechat_notification(self, config_data: dict, message: str):
        """发送微信通知"""
        try:
            import aiohttp
            import json

            # 解析配置
            webhook_url = config_data.get('webhook_url', '')

            if not webhook_url:
                logger.warning("微信通知配置为空")
                return False

            data = {
                "msgtype": "text",
                "text": {
                    "content": message
                }
            }

            async with _host.aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data, timeout=10) as response:
                    if response.status == 200:
                        logger.info(f"微信通知发送成功")
                        return True
                    else:
                        logger.warning(f"微信通知发送失败: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"发送微信通知异常: {self._safe_str(e)}")
            return False
    async def _send_telegram_notification(self, config_data: dict, message: str):
        """发送Telegram通知"""
        try:
            import aiohttp

            # 解析配置
            bot_token = config_data.get('bot_token', '')
            chat_id = config_data.get('chat_id', '')

            if not all([bot_token, chat_id]):
                logger.warning("Telegram通知配置不完整")
                return False

            # 构建API URL
            api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

            data = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }

            async with _host.aiohttp.ClientSession() as session:
                async with session.post(api_url, json=data, timeout=10) as response:
                    if response.status == 200:
                        logger.info(f"Telegram通知发送成功")
                        return True
                    else:
                        logger.warning(f"Telegram通知发送失败: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"发送Telegram通知异常: {self._safe_str(e)}")
            return False
    async def send_token_refresh_notification(
        self,
        error_message: str,
        notification_type: str = "token_refresh",
        chat_id: str = None,
        attachment_path: str = None,
        verification_url: str = None,
        verification_type: str = None,
    ):
        """发送Token刷新异常通知（带防重复机制，支持附件）
        
        Args:
            error_message: 错误消息
            notification_type: 通知类型
            chat_id: 聊天ID（可选）
            attachment_path: 附件路径（可选，用于发送截图）
            verification_type: 验证类型（可选，优先使用调用方已识别的真实类型）
        """
        try:
            # 检查是否是正常的令牌过期，这种情况不需要发送通知
            if notification_type != "token_scheduled_refresh_failed" and self._is_normal_token_expiry(error_message):
                logger.warning(f"检测到正常的令牌过期，跳过通知: {error_message}")
                return

            notification_key = f"token:{notification_type}"

            # 为Token刷新异常通知使用特殊的3小时冷却时间
            # 基于错误消息内容判断是否为Token相关异常
            if notification_type == "message_stream_stale":
                cooldown_time = self.message_stream_notification_cooldown
                cooldown_desc = f"{max(1, int(cooldown_time // 60))}分钟"
            elif self._is_token_related_error(error_message):
                cooldown_time = self.token_refresh_notification_cooldown
                cooldown_desc = "3小时"
            else:
                cooldown_time = self.notification_cooldown
                cooldown_desc = f"{self.notification_cooldown // 60}分钟"

            async with self.notification_lock:
                current_time = time.time()
                last_time = self.last_notification_time.get(notification_key, 0)
                if notification_key in self.pending_notification_keys:
                    logger.warning(f"Token刷新通知正在发送中，跳过重复发送: {notification_type}")
                    return
                if current_time - last_time < cooldown_time:
                    remaining_time = cooldown_time - (current_time - last_time)
                    remaining_hours = int(remaining_time // 3600)
                    remaining_minutes = int((remaining_time % 3600) // 60)
                    remaining_seconds = int(remaining_time % 60)

                    if remaining_hours > 0:
                        time_desc = f"{remaining_hours}小时{remaining_minutes}分钟"
                    elif remaining_minutes > 0:
                        time_desc = f"{remaining_minutes}分钟{remaining_seconds}秒"
                    else:
                        time_desc = f"{remaining_seconds}秒"

                    logger.warning(f"Token刷新通知在冷却期内，跳过发送: {notification_type} (还需等待 {time_desc})")
                    return
                self.pending_notification_keys.add(notification_key)

            # 构造通知消息（使用模板）
            if notification_type in ("slider_success", "slider_recovered_success"):
                slider_status_text = (
                    "账号会话已恢复"
                    if notification_type == "slider_recovered_success"
                    else "cookies已自动更新到数据库"
                )
                notification_msg = _host.render_notification_template(
                    'slider_success',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    status_text=slider_status_text
                )
            elif "密码登录成功" in error_message or notification_type == "password_login_success":
                notification_msg = _host.render_notification_template(
                    'password_login_success',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    cookie_count='已获取'
                )
            elif "刷新Cookie成功" in error_message or notification_type == "cookie_refresh_success":
                notification_msg = _host.render_notification_template(
                    'cookie_refresh_success',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    cookie_count='已获取'
                )
            elif "人脸验证" in error_message or "短信验证" in error_message or "二维码验证" in error_message or "身份验证" in error_message or (verification_url and "passport" in verification_url):
                notification_msg = _host.build_face_verify_notification(
                    account_id=self.cookie_id,
                    time_text=time.strftime('%Y-%m-%d %H:%M:%S'),
                    verification_type=verification_type or _host.guess_verification_type(error_message, verification_url),
                    verification_url=verification_url or '',
                    error_message=error_message,
                    has_screenshot=bool(attachment_path),
                )
            elif verification_url:
                notification_msg = _host.render_notification_template(
                    'token_refresh',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    error_message=error_message,
                    verification_url=verification_url
                )
            else:
                notification_msg = _host.render_notification_template(
                    'token_refresh',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    error_message=error_message,
                    verification_url='无'
                )

            logger.info(f"准备发送Token刷新异常通知: {self.cookie_id}")

            notification_sent = await _host.dispatch_account_notifications(
                self.cookie_id,
                notification_msg,
                title='闲鱼管理系统通知',
                notification_type=notification_type,
                attachment_path=attachment_path,
            )

            # 如果成功发送了通知，更新最后发送时间
            if notification_sent:
                current_time = time.time()
                async with self.notification_lock:
                    self.last_notification_time[notification_key] = current_time

                # 根据错误消息内容使用不同的冷却时间
                if notification_type == "message_stream_stale":
                    next_send_time = current_time + self.message_stream_notification_cooldown
                    cooldown_desc = f"{max(1, int(self.message_stream_notification_cooldown // 60))}分钟"
                elif self._is_token_related_error(error_message):
                    next_send_time = current_time + self.token_refresh_notification_cooldown
                    cooldown_desc = "3小时"
                else:
                    next_send_time = current_time + self.notification_cooldown
                    cooldown_desc = f"{self.notification_cooldown // 60}分钟"

                next_send_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(next_send_time))
                logger.info(f"Token刷新通知已发送，下次可发送时间: {next_send_time_str} (冷却时间: {cooldown_desc})")
            else:
                logger.warning(f"【{self.cookie_id}】Token刷新通知未发送成功，不进入冷却: {notification_type}")

        except Exception as e:
            logger.error(f"处理Token刷新通知失败: {self._safe_str(e)}")
        finally:
            async with self.notification_lock:
                self.pending_notification_keys.discard(f"token:{notification_type}")
    def _build_scheduled_token_refresh_error_message(self, last_refresh_status: str) -> str:
        """为定时Token刷新失败选择更准确的通知文案。"""
        if last_refresh_status == "account_risk_protected":
            return "检测到账号风控，系统已停止自动登录重试，请前往闲鱼APP处理后再手动启用账号"

        if last_refresh_status == "manual_verification_required":
            return "检测到需要人工验证，系统已自动暂停账号，请完成验证后再手动启用账号"

        if last_refresh_status in {"session_expired_after_slider", "session_expired_preflight"}:
            return "Session已过期，系统自动恢复失败，请重新登录"

        if last_refresh_status == "token_expired_recovery_failed":
            detail = (self.last_token_refresh_error_message or "").lower()
            if "session过期" in detail or "页面会话已失效" in detail:
                return "Session已过期，系统自动恢复失败，请重新登录"

        return "Token定时刷新失败，将自动重试"
    async def send_delivery_failure_notification(
        self,
        send_user_name: str,
        send_user_id: str,
        item_id: str,
        error_message: str,
        chat_id: str = None,
        order_id: str = None,
    ):
        """发送自动发货通知。"""
        try:
            resolved_buyer_name = self._resolve_delivery_notification_buyer_name(
                send_user_name,
                buyer_id=send_user_id,
                chat_id=chat_id,
                order_id=order_id,
                log_prefix=f"【{self.cookie_id}】",
            )
            notification_message = _host.render_notification_template(
                'delivery',
                account_id=self.cookie_id,
                buyer_name=resolved_buyer_name,
                buyer_id=send_user_id,
                item_id=item_id,
                chat_id=chat_id or '未知',
                result=error_message,
                time=time.strftime('%Y-%m-%d %H:%M:%S')
            )

            notification_sent = await _host.dispatch_account_notifications(
                self.cookie_id,
                notification_message,
                title='自动发货通知',
                notification_type='delivery',
            )
            if not notification_sent:
                logger.warning(f"【{self.cookie_id}】自动发货通知未发送成功")

        except Exception as e:
            logger.error(f"发送自动发货通知异常: {self._safe_str(e)}")


class MessagePipelineMixin:
    """消息队列/去重/回复状态机/看门狗/handle_message 热路径。"""

    def _mark_non_heartbeat_message(self, received_at: Optional[float] = None, *, is_sync_package: bool = False):
        """记录最近一次非心跳业务包时间。"""
        now = received_at or time.time()
        self.last_non_heartbeat_message_time = now
        if is_sync_package:
            self.last_sync_package_time = now
        if self.stream_watchdog_trigger_times:
            self.stream_watchdog_trigger_times.clear()
    def _record_message_stream_watchdog_trigger(self, occurred_at: Optional[float] = None) -> int:
        """记录业务流看门狗触发次数，便于识别重复假在线。"""
        now = occurred_at or time.time()
        window_seconds = max(60, int(self.message_stream_notification_window or 0))
        while self.stream_watchdog_trigger_times and now - self.stream_watchdog_trigger_times[0] > window_seconds:
            self.stream_watchdog_trigger_times.popleft()
        self.stream_watchdog_trigger_times.append(now)
        return len(self.stream_watchdog_trigger_times)
    async def _maybe_notify_message_stream_stale(self, occurred_at: float, connected_for: float, business_idle: float):
        """仅在短时间重复触发时发送业务流假在线通知，避免单次波动刷屏。"""
        trigger_count = self._record_message_stream_watchdog_trigger(occurred_at)
        if trigger_count < 2:
            return

        window_minutes = max(1, int(self.message_stream_notification_window // 60))
        sync_desc = (
            f"最近同步包距今{(occurred_at - self.last_sync_package_time):.0f}秒"
            if self.last_sync_package_time else
            "当前连接尚未收到同步包"
        )
        user_chat_desc = (
            f"最近真实买家消息距今{(occurred_at - self.last_user_chat_time):.0f}秒"
            if self.last_user_chat_time else
            "当前连接尚未收到真实买家消息"
        )
        notification_message = (
            f"业务消息流疑似假在线，最近{window_minutes}分钟内已连续触发{trigger_count}次自动重连。"
            f"已连接{connected_for:.0f}秒，最近非心跳业务包距今{business_idle:.0f}秒，"
            f"{sync_desc}，{user_chat_desc}"
        )
        await self.send_token_refresh_notification(notification_message, "message_stream_stale")
    async def message_stream_watchdog_loop(self):
        """检测“只有心跳、没有业务包”的假在线状态。"""
        heartbeat_stale_timeout = max(self.heartbeat_timeout * 2, self.heartbeat_interval * 3)
        try:
            while True:
                try:
                    _mgr = self._cookie_mgr
                    if _mgr and not _mgr.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止业务流看门狗")
                        break

                    await self._interruptible_sleep(self.stream_watchdog_check_interval)

                    ws = self.ws
                    if not ws or getattr(ws, "closed", False):
                        continue

                    if not self.last_successful_connection:
                        continue

                    now = time.time()
                    connected_for = now - self.last_successful_connection
                    if connected_for < self.stream_watchdog_grace_period:
                        continue

                    if not self.last_heartbeat_response:
                        continue

                    heartbeat_age = now - self.last_heartbeat_response
                    if heartbeat_age > heartbeat_stale_timeout:
                        continue

                    last_business_at = self.last_non_heartbeat_message_time or self.last_successful_connection
                    business_idle = now - last_business_at
                    if business_idle < self.message_stream_watchdog_timeout:
                        continue

                    if (
                        self.last_stream_watchdog_reconnect_time
                        and now - self.last_stream_watchdog_reconnect_time < self.message_stream_watchdog_timeout / 2
                    ):
                        continue

                    self.last_stream_watchdog_reconnect_time = now
                    if self.last_sync_package_time:
                        sync_status = f"最近同步包距今{(now - self.last_sync_package_time):.0f}秒"
                    else:
                        sync_status = "当前连接尚未收到同步包"
                    if self.last_user_chat_time:
                        user_chat_status = f"，最近真实买家消息距今{(now - self.last_user_chat_time):.0f}秒"
                    else:
                        user_chat_status = "，当前连接尚未收到真实买家消息"

                    logger.warning(
                        f"【{self.cookie_id}】检测到业务流疑似假在线: "
                        f"已连接{connected_for:.0f}秒，最近非心跳业务包距今{business_idle:.0f}秒，{sync_status}{user_chat_status}"
                    )
                    await self._force_websocket_reconnect("业务消息流长时间只有心跳，疑似假在线")
                    await self._maybe_notify_message_stream_stale(now, connected_for, business_idle)
                except asyncio.CancelledError:
                    logger.info(f"【{self.cookie_id}】业务流看门狗收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】业务流看门狗异常: {self._safe_str(e)}")
                    await self._interruptible_sleep(30)
        except asyncio.CancelledError:
            logger.info(f"【{self.cookie_id}】业务流看门狗已取消，正在退出...")
            raise
        finally:
            logger.info(f"【{self.cookie_id}】业务流看门狗已退出")
    @property
    def message_debounce_delay(self):
        """动态从数据库读取防抖延迟配置，修改后无需重启"""
        try:
            from db_manager import db_manager
            val = _db_package().get_system_setting('message_debounce_delay')
            return int(val) if val else self._message_debounce_delay
        except Exception:
            return self._message_debounce_delay
    def _get_message_priority(self, message_data: dict) -> int:
        """
        根据消息类型确定优先级
        
        优先级定义:
        - 0: 最高优先级（心跳响应、ACK确认）- 立即处理
        - 1: 高优先级（订单相关消息）- 优先处理
        - 2: 中优先级（普通聊天消息）- 正常处理
        - 3: 低优先级（系统通知、其他）- 延后处理
        
        Returns:
            int: 优先级值，越小优先级越高
        """
        try:
            # 检查是否是心跳响应
            if isinstance(message_data, dict):
                # 心跳响应
                if message_data.get("code") == 200 and "body" not in message_data:
                    return 0
                
                # 检查消息体
                body = message_data.get("body", {})
                
                # 同步包消息需要进一步分析
                if "syncPushPackage" in body:
                    try:
                        sync_data = body["syncPushPackage"].get("data", [])
                        if sync_data and isinstance(sync_data, list) and len(sync_data) > 0:
                            first_data = sync_data[0]
                            # 检查是否包含订单相关关键词
                            data_str = str(first_data).lower()
                            if any(kw in data_str for kw in ['orderid', 'order_id', 'bizorderid', 'paysucc', 'paid']):
                                return 1  # 订单消息 - 高优先级
                            if 'message' in data_str or 'chat' in data_str:
                                return 2  # 聊天消息 - 中优先级
                    except Exception:
                        pass
                
                # ACK确认消息
                if message_data.get("code") == 200:
                    return 0
            
            return 3  # 默认低优先级
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】解析消息优先级失败: {e}")
            return 3
    async def _enqueue_message(self, message_data: dict, websocket, msg_id: str = "unknown") -> bool:
        """
        将消息放入优先级队列
        
        Args:
            message_data: 消息数据
            websocket: WebSocket连接
            msg_id: 消息ID
            
        Returns:
            bool: 是否成功入队
        """
        try:
            # 获取消息优先级
            priority = self._get_message_priority(message_data)
            
            # 创建消息包装对象
            async with self.message_queue_lock:
                self.message_queue_counter += 1
                counter = self.message_queue_counter
            
            message_item = {
                'data': message_data,
                'websocket': websocket,
                'msg_id': msg_id,
                'enqueue_time': time.time(),
                'priority': priority,
            }
            
            # 尝试非阻塞入队
            try:
                self.message_queue.put_nowait((priority, counter, message_item))
                self.queue_stats['received'] += 1
                
                # 高优先级消息日志
                if priority <= 1:
                    logger.info(f"【{self.cookie_id}】📥 高优先级消息入队 [P{priority}][ID:{msg_id}] 队列大小: {self.message_queue.qsize()}")
                else:
                    logger.debug(f"【{self.cookie_id}】📥 消息入队 [P{priority}][ID:{msg_id}] 队列大小: {self.message_queue.qsize()}")
                
                return True
            except asyncio.QueueFull:
                # 队列满时，尝试丢弃最低优先级的旧消息
                self.queue_stats['dropped_full'] += 1
                logger.warning(f"【{self.cookie_id}】⚠️ 消息队列已满({self.message_queue_max_size})，消息[ID:{msg_id}]被丢弃")
                return False
                
        except Exception as e:
            logger.error(f"【{self.cookie_id}】消息入队失败: {self._safe_str(e)}")
            return False
    async def _message_worker(self, worker_id: int):
        """
        消息处理工作协程
        
        从队列中取出消息并处理，支持并发处理多个消息
        
        Args:
            worker_id: 工作协程ID
        """
        logger.info(f"【{self.cookie_id}】🔧 消息处理工作协程 #{worker_id} 启动")
        
        while self.message_queue_running:
            try:
                # 设置超时获取，避免无限等待
                try:
                    priority, counter, message_item = await asyncio.wait_for(
                        self.message_queue.get(), 
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    # 超时没有消息，继续循环
                    continue
                
                # 检查消息是否过期
                enqueue_time = message_item['enqueue_time']
                age = time.time() - enqueue_time
                if age > self.message_expire_seconds:
                    self.queue_stats['dropped_expired'] += 1
                    logger.warning(f"【{self.cookie_id}】⏰ 工作协程#{worker_id} 丢弃过期消息 [ID:{message_item['msg_id']}] 已等待{age:.1f}秒")
                    self.message_queue.task_done()
                    continue
                
                # 处理消息
                msg_id = message_item['msg_id']
                try:
                    logger.debug(f"【{self.cookie_id}】🔄 工作协程#{worker_id} 开始处理消息 [P{priority}][ID:{msg_id}] 等待{age:.2f}秒")
                    
                    # 使用信号量控制并发
                    async with self.message_semaphore:
                        self.active_message_tasks += 1
                        try:
                            await self.handle_message(
                                message_item['data'],
                                message_item['websocket'],
                                msg_id
                            )
                            self.queue_stats['processed'] += 1
                        finally:
                            self.active_message_tasks -= 1
                    
                    logger.debug(f"【{self.cookie_id}】✅ 工作协程#{worker_id} 完成消息处理 [ID:{msg_id}]")
                    
                except Exception as e:
                    self.queue_stats['errors'] += 1
                    logger.error(f"【{self.cookie_id}】❌ 工作协程#{worker_id} 处理消息失败 [ID:{msg_id}]: {self._safe_str(e)}")
                finally:
                    self.message_queue.task_done()
                    
            except asyncio.CancelledError:
                logger.info(f"【{self.cookie_id}】🛑 消息处理工作协程 #{worker_id} 被取消")
                break
            except Exception as e:
                logger.error(f"【{self.cookie_id}】工作协程#{worker_id} 异常: {self._safe_str(e)}")
                await asyncio.sleep(1)  # 出错后短暂休息
        
        logger.info(f"【{self.cookie_id}】🔧 消息处理工作协程 #{worker_id} 已停止")
    async def _start_message_queue_workers(self):
        """启动消息队列工作协程"""
        if not self.message_queue_enabled:
            logger.info(f"【{self.cookie_id}】消息队列系统已禁用，使用传统处理模式")
            return
        
        self.message_queue_running = True
        self.message_workers = []
        
        # 创建多个工作协程
        for i in range(self.message_queue_workers):
            worker_task = self._create_tracked_task(self._message_worker(i))
            self.message_workers.append(worker_task)
        
        # 启动队列监控任务
        self._create_tracked_task(self._queue_stats_monitor())
        
        logger.info(f"【{self.cookie_id}】🚀 消息队列系统已启动，{self.message_queue_workers}个工作协程")
    async def _stop_message_queue_workers(self):
        """停止消息队列工作协程"""
        self.message_queue_running = False
        
        # 取消所有工作协程
        for worker_task in self.message_workers:
            if not worker_task.done():
                worker_task.cancel()
        
        # 等待所有工作协程结束
        if self.message_workers:
            await asyncio.gather(*self.message_workers, return_exceptions=True)
        
        self.message_workers = []
        logger.info(f"【{self.cookie_id}】🛑 消息队列系统已停止")
    def _unwrap_message_for_dedupe(self, message_data: dict) -> Optional[dict]:
        """把同步包还原成内部消息结构，让 messageId / createTime 提取走统一路径。

        - 如果 message_data 已是内部结构（包含 key '1'），原样返回
        - 如果是 syncPushPackage 同步包，先 base64 + json 解第一条 data 段返回
        - 其它情况返回 None，让调用方走兜底标识
        """
        if not isinstance(message_data, dict):
            return None
        if "1" in message_data:
            return message_data

        try:
            if not self.is_sync_package(message_data):
                return None
            sync_entries = (
                ((message_data.get("body") or {}).get("syncPushPackage") or {}).get("data") or []
            )
            if not sync_entries:
                return None
            payload = sync_entries[0].get("data")
            if not payload:
                return None
            decoded = base64.b64decode(payload).decode("utf-8")
            inner = json.loads(decoded)
            return inner if isinstance(inner, dict) else None
        except Exception as exc:
            logger.debug(f"【{self.cookie_id}】解析同步包消息用于去重时失败: {self._safe_str(exc)}")
            return None
    def _extract_message_id(self, message_data: dict) -> str:
        """
        从消息数据中提取消息ID，用于去重
        
        Args:
            message_data: 原始消息数据
            
        Returns:
            消息ID字符串，如果无法提取则返回None
        """
        try:
            # 同步包消息要先还原到内部结构，否则下面的 message['1']['10']['bizTag'] 路径取不到
            normalized_message = self._unwrap_message_for_dedupe(message_data)

            # 尝试从 message['1']['10']['bizTag'] 中提取 messageId
            if isinstance(normalized_message, dict) and "1" in normalized_message:
                message_1 = normalized_message.get("1")
                if isinstance(message_1, dict) and "10" in message_1:
                    message_10 = message_1.get("10")
                    if isinstance(message_10, dict) and "bizTag" in message_10:
                        biz_tag = message_10.get("bizTag", "")
                        if isinstance(biz_tag, str):
                            # bizTag 是 JSON 字符串，格式如: '{"sourceId":"S:1","messageId":"984f323c719d4cd0a7b993a0769a33b6"}'
                            try:
                                import json
                                biz_tag_dict = json.loads(biz_tag)
                                if isinstance(biz_tag_dict, dict) and "messageId" in biz_tag_dict:
                                    return biz_tag_dict.get("messageId")
                            except (json.JSONDecodeError, TypeError):
                                pass
                        
                        # 如果 bizTag 解析失败，尝试从 extJson 中提取
                        if "extJson" in message_10:
                            ext_json = message_10.get("extJson", "")
                            if isinstance(ext_json, str):
                                try:
                                    import json
                                    ext_json_dict = json.loads(ext_json)
                                    if isinstance(ext_json_dict, dict) and "messageId" in ext_json_dict:
                                        return ext_json_dict.get("messageId")
                                except (json.JSONDecodeError, TypeError):
                                    pass
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】提取消息ID失败: {self._safe_str(e)}")

        return None
    def _extract_message_id_from_chat_payload(self, message_1: dict, message_10: dict) -> str:
        """从已解出的聊天消息结构里直接提取 messageId，避免重复解同步包。"""
        try:
            if not isinstance(message_1, dict) or not isinstance(message_10, dict):
                return None

            biz_tag = message_10.get("bizTag", "")
            if isinstance(biz_tag, str) and biz_tag:
                try:
                    biz_tag_dict = json.loads(biz_tag)
                    if isinstance(biz_tag_dict, dict) and biz_tag_dict.get("messageId"):
                        return str(biz_tag_dict["messageId"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            ext_json = message_10.get("extJson", "")
            if isinstance(ext_json, str) and ext_json:
                try:
                    ext_json_dict = json.loads(ext_json)
                    if isinstance(ext_json_dict, dict) and ext_json_dict.get("messageId"):
                        return str(ext_json_dict["messageId"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】从聊天消息结构提取messageId失败: {self._safe_str(e)}")

        return None
    def _cleanup_message_reply_state(self, current_time: float):
        """清理过期的已处理/处理中消息状态。"""
        expired_processed_ids = [
            msg_id for msg_id, timestamp in self.processed_message_ids.items()
            if current_time - timestamp > self.message_expire_time
        ]
        for msg_id in expired_processed_ids:
            del self.processed_message_ids[msg_id]

        expired_pending_ids = [
            msg_id for msg_id, timestamp in self.pending_message_ids.items()
            if current_time - timestamp > self.pending_message_expire_time
        ]
        for msg_id in expired_pending_ids:
            del self.pending_message_ids[msg_id]

        if expired_processed_ids:
            logger.info(f"【{self.cookie_id}】已清理 {len(expired_processed_ids)} 个过期消息ID")
        if expired_pending_ids:
            logger.warning(f"【{self.cookie_id}】已清理 {len(expired_pending_ids)} 个超时未完成的消息预占")

        if len(self.processed_message_ids) > self.processed_message_ids_max_size:
            sorted_ids = sorted(self.processed_message_ids.items(), key=lambda x: x[1])
            remove_count = len(sorted_ids) // 2
            for msg_id, _ in sorted_ids[:remove_count]:
                del self.processed_message_ids[msg_id]
            logger.info(f"【{self.cookie_id}】消息ID去重字典过大，已清理 {remove_count} 个最旧记录")
    async def _reserve_message_reply(self, message_id: str) -> bool:
        """为消息创建处理预占，防止并发重复回复。"""
        async with self.processed_message_ids_lock:
            current_time = time.time()
            self._cleanup_message_reply_state(current_time)

            if message_id in self.processed_message_ids:
                last_process_time = self.processed_message_ids[message_id]
                time_elapsed = current_time - last_process_time
                remaining_time = int(max(0, self.message_expire_time - time_elapsed))
                logger.warning(f"【{self.cookie_id}】消息ID {message_id[:50]}... 已处理过，距离可重复回复还需 {remaining_time} 秒")
                return False

            if message_id in self.pending_message_ids:
                time_elapsed = current_time - self.pending_message_ids[message_id]
                remaining_time = int(max(0, self.pending_message_expire_time - time_elapsed))
                logger.warning(f"【{self.cookie_id}】消息ID {message_id[:50]}... 正在处理中，预占剩余约 {remaining_time} 秒")
                return False

            self.pending_message_ids[message_id] = current_time
            return True
    async def _finalize_message_reply(self, message_id: str, reason: str = ""):
        """将消息从处理中转为已完成，后续重复包不再回复。"""
        async with self.processed_message_ids_lock:
            current_time = time.time()
            self.pending_message_ids.pop(message_id, None)
            self.processed_message_ids[message_id] = current_time
            self._cleanup_message_reply_state(current_time)

        if reason:
            logger.info(f"【{self.cookie_id}】消息ID {message_id[:50]}... 已完成处理: {reason}")
    async def _release_message_reply(self, message_id: str, reason: str = ""):
        """释放消息处理预占，允许后续重试。"""
        async with self.processed_message_ids_lock:
            released = self.pending_message_ids.pop(message_id, None)

        if released is not None:
            logger.warning(f"【{self.cookie_id}】消息ID {message_id[:50]}... 已释放预占，允许重试: {reason or 'unknown'}")
    async def _schedule_debounced_reply(self, chat_id: str, message_data: dict, websocket,
                                       send_user_name: str, send_user_id: str, send_message: str,
                                       item_id: str, msg_time: str, dedupe_message_id: str = None,
                                       dedupe_create_time: int = 0):
        """
        调度防抖回复：如果用户连续发送消息，等待用户停止发送后再回复最后一条消息
        
        Args:
            chat_id: 聊天ID
            message_data: 原始消息数据
            websocket: WebSocket连接
            send_user_name: 发送者用户名
            send_user_id: 发送者用户ID
            send_message: 消息内容
            item_id: 商品ID
            msg_time: 消息时间
        """
        # 提取消息ID并检查是否已处理（优先使用调用链已解出的 messageId，避免重复解同步包）
        message_id = str(dedupe_message_id).strip() if dedupe_message_id else self._extract_message_id(message_data)
        # 如果没有 messageId，使用备用标识（chat_id + send_user_id + send_message + 时间戳）
        if not message_id:
            try:
                # 同步包消息要先还原到内部结构再取 createTime
                normalized_message = self._unwrap_message_for_dedupe(message_data) or {}
                # 优先使用调用链里已提取出的 create_time，避免退化成 _0 后缀
                create_time = int(dedupe_create_time or 0)
                if isinstance(normalized_message, dict) and "1" in normalized_message:
                    message_1 = normalized_message.get("1")
                    if isinstance(message_1, dict):
                        create_time = int(message_1.get("5", create_time) or create_time or 0)
                if not create_time:
                    create_time = int(time.time() * 1000)
                # 使用更稳的组合键作为备用标识（带 send_user_id 减少不同人同文本撞车）
                message_id = f"{chat_id}_{send_user_id}_{send_message}_{create_time}"
            except Exception:
                # 如果提取失败，使用当前时间戳
                message_id = f"{chat_id}_{send_user_id}_{send_message}_{int(time.time() * 1000)}"

        # in-flight 锁：原子地检查"已处理 / 正在处理"两个状态，预占后才进入防抖
        # （替代原来的 inline check-and-set，修复同消息并发时被多次回复的问题）
        if not await self._reserve_message_reply(message_id):
            return

        async with self.message_debounce_lock:
            # 如果该chat_id已有防抖任务，取消它
            if chat_id in self.message_debounce_tasks:
                old_task = self.message_debounce_tasks[chat_id].get('task')
                if old_task and not old_task.done():
                    old_task.cancel()
                    logger.warning(f"【{self.cookie_id}】取消chat_id {chat_id} 的旧防抖任务")

            # 更新最后一条消息信息
            current_timer = time.time()
            self.message_debounce_tasks[chat_id] = {
                'last_message': {
                    'message_id': message_id,
                    'message_data': message_data,
                    'websocket': websocket,
                    'send_user_name': send_user_name,
                    'send_user_id': send_user_id,
                    'send_message': send_message,
                    'item_id': item_id,
                    'msg_time': msg_time
                },
                'timer': current_timer
            }
            
            # 创建新的防抖任务
            async def debounce_task():
                saved_timer = current_timer  # 保存创建任务时的时间戳
                try:
                    # 等待防抖延迟时间
                    await asyncio.sleep(self.message_debounce_delay)
                    
                    # 检查是否仍然是最新的消息（防止在等待期间有新消息）
                    async with self.message_debounce_lock:
                        if chat_id not in self.message_debounce_tasks:
                            return
                        
                        debounce_info = self.message_debounce_tasks[chat_id]
                        # 检查时间戳是否匹配（确保这是最新的消息）
                        if saved_timer != debounce_info['timer']:
                            logger.warning(f"【{self.cookie_id}】chat_id {chat_id} 在防抖期间有新消息，跳过旧消息处理")
                            return
                        
                        # 获取最后一条消息
                        last_msg = debounce_info['last_message']
                        
                        # 从防抖任务中移除
                        del self.message_debounce_tasks[chat_id]
                    
                    # 处理最后一条消息
                    logger.info(f"【{self.cookie_id}】防抖延迟结束，开始处理chat_id {chat_id} 的最后一条消息: {last_msg['send_message'][:30]}...")
                    await self._process_chat_message_reply(
                        last_msg['message_data'],
                        last_msg['websocket'],
                        last_msg['send_user_name'],
                        last_msg['send_user_id'],
                        last_msg['send_message'],
                        last_msg['item_id'],
                        chat_id,
                        last_msg['msg_time']
                    )
                    # 无异常即视为已收口，把 in-flight 预占转成已处理（防止短时间重复入队）
                    await self._finalize_message_reply(last_msg['message_id'], reason="回复链处理完成")

                except asyncio.CancelledError:
                    logger.warning(f"【{self.cookie_id}】chat_id {chat_id} 的防抖任务被取消")
                    try:
                        await self._release_message_reply(message_id, reason="防抖任务取消")
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】处理防抖回复时发生错误: {self._safe_str(e)}")
                    try:
                        await self._release_message_reply(message_id, reason=f"防抖任务异常: {self._safe_str(e)}")
                    except Exception:
                        pass
                    # 确保从防抖任务中移除
                    async with self.message_debounce_lock:
                        if chat_id in self.message_debounce_tasks:
                            del self.message_debounce_tasks[chat_id]
            
            task = self._create_tracked_task(debounce_task())
            self.message_debounce_tasks[chat_id]['task'] = task
            logger.warning(f"【{self.cookie_id}】为chat_id {chat_id} 创建防抖任务，延迟 {self.message_debounce_delay} 秒")
    async def _process_chat_message_reply(self, message_data: dict, websocket, send_user_name: str,
                                         send_user_id: str, send_message: str, item_id: str,
                                         chat_id: str, msg_time: str):
        """
        处理聊天消息的回复逻辑（从handle_message中提取出来的核心回复逻辑）
        
        Args:
            message_data: 原始消息数据
            websocket: WebSocket连接
            send_user_name: 发送者用户名
            send_user_id: 发送者用户ID
            send_message: 消息内容
            item_id: 商品ID
            chat_id: 聊天ID
            msg_time: 消息时间
        """
        try:
            # 自动回复消息
            if not _host.AUTO_REPLY.get('enabled', True):
                logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】自动回复已禁用")
                return

            # 检查该chat_id是否处于暂停状态
            if _host.pause_manager.is_chat_paused(chat_id):
                remaining_time = _host.pause_manager.get_remaining_pause_time(chat_id)
                remaining_minutes = remaining_time // 60
                remaining_seconds = remaining_time % 60
                logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】chat_id {chat_id} 自动回复已暂停，剩余时间: {remaining_minutes}分{remaining_seconds}秒")
                return

            blacklist_hit = self._check_buyer_blacklist_for_action(
                buyer_id=send_user_id,
                item_id=item_id,
                buyer_nick=send_user_name,
                action='自动回复',
                log_delivery=False,
            )
            if blacklist_hit:
                return

            reply = None
            reply_source = None

            # 按 README 定义的优先级处理：
            # 指定商品回复 > 商品专用关键词 > 通用关键词 > 默认回复 > AI回复
            reply = await self.get_item_specific_reply(send_user_name, send_user_id, send_message, item_id)
            if reply:
                reply_source = '指定商品'
            else:
                # 1. 尝试关键词匹配（内部已区分商品专用关键词和通用关键词）
                reply = await self.get_keyword_reply(send_user_name, send_user_id, send_message, item_id)
                if reply == "EMPTY_REPLY":
                    # 匹配到关键词但回复内容为空，不进行任何回复
                    logger.info(f"[{msg_time}] 【{self.cookie_id}】匹配到空回复关键词，跳过自动回复")
                    return
                elif reply:
                    reply_source = '关键词'  # 标记为关键词回复
                else:
                    # 2. 关键词匹配失败后，使用默认回复兜底
                    reply = await self.get_default_reply(send_user_name, send_user_id, send_message, chat_id, item_id)
                    if reply == "EMPTY_REPLY":
                        logger.info(f"[{msg_time}] 【{self.cookie_id}】默认回复内容为空，跳过自动回复")
                        return
                    elif reply == "SKIP_REPLY":
                        logger.info(f"[{msg_time}] 【{self.cookie_id}】默认回复已命中过当前会话，跳过自动回复")
                        return
                    elif reply:
                        reply_source = '默认'
                    else:
                        # 3. 最后尝试AI回复
                        reply = await self.get_ai_reply(send_user_name, send_user_id, send_message, item_id, chat_id)
                        if reply:
                            reply_source = 'AI'

            # 注意：这里只有商品ID，没有标题和详情，根据新的规则不保存到数据库
            # 商品信息会在其他有完整信息的地方保存（如发货规则匹配时）
            # 消息通知已在收到消息时立即发送，此处不再重复发送

            # 如果有回复内容，发送消息
            if reply:
                # 检查是否是图片发送标记
                if reply.startswith("__IMAGE_SEND__"):
                    # 提取图片URL（关键词回复不包含卡券ID）
                    image_url = reply.replace("__IMAGE_SEND__", "")
                    # 发送图片消息
                    try:
                        await self.send_image_msg(websocket, chat_id, send_user_id, image_url)
                        # 记录发出的图片消息
                        msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                        logger.info(f"[{msg_time}] 【{reply_source}图片发出】用户: {send_user_name} (ID: {send_user_id}), 商品({item_id}): 图片 {image_url}")
                    except Exception as e:
                        # 图片发送失败，发送错误提示
                        logger.error(f"图片发送失败: {self._safe_str(e)}")
                        await self.send_msg(websocket, chat_id, send_user_id, "抱歉，图片发送失败，请稍后重试。")
                        msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                        logger.error(f"[{msg_time}] 【{reply_source}图片发送失败】用户: {send_user_name} (ID: {send_user_id}), 商品({item_id})")
                else:
                    # 普通文本消息
                    await self.send_msg(websocket, chat_id, send_user_id, reply)
                    # 记录发出的消息
                    msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    logger.info(f"[{msg_time}] 【{reply_source}发出】用户: {send_user_name} (ID: {send_user_id}), 商品({item_id}): {reply}")
                    try:
                        from db_manager import db_manager as _db
                        from chat_event_hub import publish_chat_message
                        image_url = None
                        media_url = None
                        link_url = None
                        extra_json = None
                        _msg_id_db = _db.save_chat_message(
                            cookie_id=self.cookie_id, chat_id=chat_id,
                            sender_id=self.myid, sender_name=self.cookie_id,
                            content=reply, content_type=1,
                            image_url=image_url,
                            item_id=item_id, direction=1, reply_source=reply_source,
                            media_url=media_url, link_url=link_url, extra_json=extra_json,
                        )
                        publish_chat_message(self.cookie_id, {
                            'msg_id': _msg_id_db, 'chat_id': chat_id,
                            'sender_id': self.myid, 'sender_name': self.cookie_id,
                            'content': reply, 'content_type': 1,
                            'image_url': image_url,
                            'item_id': item_id, 'direction': 1, 'reply_source': reply_source,
                            'media_url': media_url, 'link_url': link_url, 'extra_json': extra_json,
                        })
                    except Exception as _e:
                        logger.debug(f"保存/推送发出消息失败: {self._safe_str(_e)}")
            else:
                msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】未找到匹配的回复规则，不回复")
        except Exception as e:
            logger.error(f"处理聊天消息回复时发生错误: {self._safe_str(e)}")
    async def _handle_message_with_semaphore(self, message_data, websocket, msg_id="unknown"):
        """带信号量的消息处理包装器，防止并发任务过多"""
        async with self.message_semaphore:
            self.active_message_tasks += 1
            try:
                await self.handle_message(message_data, websocket, msg_id)
            finally:
                self.active_message_tasks -= 1
                # 定期记录活跃任务数（每100个任务记录一次）
                if self.active_message_tasks % 100 == 0 and self.active_message_tasks > 0:
                    logger.info(f"【{self.cookie_id}】当前活跃消息处理任务数: {self.active_message_tasks}")
    def is_chat_message(self, message):
        """判断是否为用户聊天消息"""
        try:
            return (
                isinstance(message, dict)
                and "1" in message
                and isinstance(message["1"], dict)
                and "10" in message["1"]
                and isinstance(message["1"]["10"], dict)
                and "reminderContent" in message["1"]["10"]
            )
        except Exception:
            return False
    async def handle_message(self, message_data, websocket, msg_id="unknown"):
        """处理所有类型的消息"""
        # 获取消息大小用于追踪
        msg_size = len(json.dumps(message_data)) if message_data else 0
        logger.info(f"【{self.cookie_id}】[{msg_id}] 🚀 开始处理消息 ({msg_size}字节)")
        
        try:
            # 检查账号是否启用
            _mgr = self._cookie_mgr
            if _mgr and not _mgr.get_cookie_status(self.cookie_id):
                logger.warning(f"【{self.cookie_id}】[{msg_id}] ⏹️ 账号已禁用，消息处理结束")
                return

            # 发送确认消息
            try:
                message = message_data
                ack = {
                    "code": 200,
                    "headers": {
                        "mid": message["headers"]["mid"] if "mid" in message["headers"] else _host.generate_mid(),
                        "sid": message["headers"]["sid"] if "sid" in message["headers"] else '',
                    }
                }
                if 'app-key' in message["headers"]:
                    ack["headers"]["app-key"] = message["headers"]["app-key"]
                if 'ua' in message["headers"]:
                    ack["headers"]["ua"] = message["headers"]["ua"]
                if 'dt' in message["headers"]:
                    ack["headers"]["dt"] = message["headers"]["dt"]
                await websocket.send(json.dumps(ack))
            except Exception as e:
                logger.debug(f"【{self.cookie_id}】[{msg_id}] 发送ACK失败: {e}")

            # 如果不是同步包消息，直接返回
            if not self.is_sync_package(message_data):
                logger.debug(f"【{self.cookie_id}】[{msg_id}] ⏹️ 非同步包消息，处理结束")
                return

            # 获取并解密数据
            sync_data = message_data["body"]["syncPushPackage"]["data"][0]

            # 检查是否有必要的字段
            if "data" not in sync_data:
                logger.warning(f"【{self.cookie_id}】[{msg_id}] ⚠️ 同步包中无data字段，消息内容: {sync_data}")
                logger.warning(f"【{self.cookie_id}】[{msg_id}] ⏹️ 消息处理结束（缺少data字段）")
                return

            # 解密数据
            message = None
            try:
                data = sync_data["data"]
                logger.debug(f"【{self.cookie_id}】[{msg_id}] 开始解密同步包数据...")
                try:
                    data = base64.b64decode(data).decode("utf-8")
                    parsed_data = json.loads(data)
                    # 处理未加密的消息（如系统提示等）
                    msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    if isinstance(parsed_data, dict) and 'chatType' in parsed_data:
                        logger.warning(f"【{self.cookie_id}】[{msg_id}] ⚠️ 检测到chatType消息，完整内容: {parsed_data}")
                        if 'operation' in parsed_data and 'content' in parsed_data['operation']:
                            content = parsed_data['operation']['content']
                            if 'sessionArouse' in content:
                                # 处理系统引导消息
                                logger.info(f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】小闲鱼智能提示:")
                                if 'arouseChatScriptInfo' in content['sessionArouse']:
                                    for qa in content['sessionArouse']['arouseChatScriptInfo']:
                                        logger.info(f"  - {qa['chatScrip']}")
                                logger.info(f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⏹️ 系统引导消息处理完成")
                                return
                            elif 'contentType' in content:
                                # 其他类型的未加密消息
                                logger.warning(f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】其他类型消息: {content}")
                        # ⚠️ 修复：不能直接return，应该继续处理这条消息
                        # 因为付款消息可能也包含chatType字段
                        logger.warning(f"【{self.cookie_id}】[{msg_id}] ⚠️ chatType消息但不是引导消息，继续处理...")
                        message = parsed_data
                    else:
                        # 如果不是系统消息，将解析的数据作为message
                        logger.debug(f"【{self.cookie_id}】[{msg_id}] 解密成功，正常消息")
                        message = parsed_data
                except Exception as e:
                    # 如果JSON解析失败，尝试解密
                    logger.debug(f"【{self.cookie_id}】[{msg_id}] JSON解析失败，尝试解密...")
                    decrypted_data = _host.decrypt(data)
                    message = json.loads(decrypted_data)
                    logger.debug(f"【{self.cookie_id}】[{msg_id}] 解密成功")
            except Exception as e:
                # ⚠️ 关键：对于解密失败的大消息，记录完整信息
                logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息解密失败: {self._safe_str(e)}")
                if msg_size > 3000:
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ⚠️⚠️⚠️ 大消息({msg_size}字节)解密失败，完整sync_data: {sync_data}")
                    # 尝试记录base64数据的前后部分
                    try:
                        raw_data = sync_data.get("data", "")
                        logger.error(f"【{self.cookie_id}】[{msg_id}] Base64数据长度: {len(raw_data)}")
                        logger.error(f"【{self.cookie_id}】[{msg_id}] Base64前100字符: {raw_data[:100]}")
                        logger.error(f"【{self.cookie_id}】[{msg_id}] Base64后100字符: {raw_data[-100:]}")
                    except Exception:
                        pass
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 消息处理结束（解密失败）")
                return

            # 确保message不为空
            if message is None:
                logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息解析后为空")
                if msg_size > 3000:
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ⚠️⚠️⚠️ 大消息({msg_size}字节)解析后为空！")
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 消息处理结束（解析后为空）")
                return

            # 确保message是字典类型
            if not isinstance(message, dict):
                logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息格式错误，期望字典但得到: {type(message)}")
                logger.warning(f"【{self.cookie_id}】[{msg_id}] 消息内容: {message}")
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 消息处理结束（格式错误）")
                return

            # 【消息接收标识】记录收到消息的时间，用于控制Cookie刷新
            self.last_message_received_time = time.time()
            logger.warning(f"【{self.cookie_id}】[{msg_id}] ✅ 开始处理消息")

            # 【优先处理】尝试获取订单ID并获取订单详情
            order_id = None
            try:
                logger.info(f"【{self.cookie_id}】[{msg_id}] 🔍 开始提取订单ID，消息类型: {type(message)}")
                order_id = self._extract_order_id(message, message_data)
                if order_id:
                    msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 检测到订单ID: {order_id}，开始获取订单详情')

                    order_context = self._extract_order_message_context(message, msg_id=msg_id)
                    temp_user_id = order_context.get('buyer_id')
                    temp_user_id_source = order_context.get('buyer_id_source')
                    temp_item_id = order_context.get('item_id')
                    temp_sid = order_context.get('sid')
                    temp_buyer_nick = order_context.get('buyer_nick')

                    # 通知订单状态处理器订单ID已提取
                    if self.order_status_handler:
                        logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.on_order_id_extracted: {order_id}")
                        try:
                            self.order_status_handler.on_order_id_extracted(
                                order_id,
                                self.cookie_id,
                                message,
                                match_context={
                                    'sid': temp_sid,
                                    'buyer_id': temp_user_id,
                                    'item_id': temp_item_id,
                                }
                            )
                            logger.info(f"【{self.cookie_id}】订单状态处理器.on_order_id_extracted调用成功: {order_id}")
                        except Exception as e:
                            logger.error(f"【{self.cookie_id}】通知订单状态处理器订单ID提取失败: {self._safe_str(e)}")
                            import traceback
                            logger.error(f"【{self.cookie_id}】详细错误信息: {traceback.format_exc()}")
                    else:
                        logger.warning(f"【{self.cookie_id}】订单状态处理器为None，跳过订单ID提取通知: {order_id}")

                    basic_order_saved = self._preload_basic_order_info(
                        order_id,
                        item_id=temp_item_id,
                        buyer_id=temp_user_id,
                        sid=temp_sid,
                        buyer_nick=temp_buyer_nick,
                        buyer_id_source=temp_user_id_source,
                    )

                    # 立即获取订单详情信息
                    try:
                        # 调用订单详情获取方法（传入sid和buyer_nick用于保存到数据库）
                        order_detail = await self.fetch_order_detail_info(
                            order_id,
                            temp_item_id,
                            temp_user_id,
                            sid=temp_sid,
                            buyer_nick=temp_buyer_nick,
                            buyer_id_source=temp_user_id_source,
                        )
                        if order_detail:
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】✅ 订单详情获取成功: {order_id}')
                        else:
                            logger.warning(f'[{msg_time}] 【{self.cookie_id}】⚠️ 订单详情获取失败: {order_id}')
                            if basic_order_saved:
                                self._schedule_order_detail_retry(
                                    order_id,
                                    item_id=temp_item_id,
                                    buyer_id=temp_user_id,
                                    sid=temp_sid,
                                    buyer_nick=temp_buyer_nick,
                                    delay_seconds=30,
                                    buyer_id_source=temp_user_id_source,
                                )

                    except Exception as detail_e:
                        logger.error(f'[{msg_time}] 【{self.cookie_id}】❌ 获取订单详情异常: {self._safe_str(detail_e)}')
                        if basic_order_saved:
                            self._schedule_order_detail_retry(
                                order_id,
                                item_id=temp_item_id,
                                buyer_id=temp_user_id,
                                sid=temp_sid,
                                buyer_nick=temp_buyer_nick,
                                delay_seconds=30,
                                buyer_id_source=temp_user_id_source,
                            )
                else:
                    logger.warning(f"【{self.cookie_id}】[{msg_id}] 未检测到订单ID")
            except Exception as e:
                logger.error(f"【{self.cookie_id}】[{msg_id}] 提取订单ID失败: {self._safe_str(e)}")

            # 安全地获取用户ID
            user_id = None
            try:
                message_1 = message.get("1")
                if isinstance(message_1, str):
                    # message['1'] 是字符串（sid 或 PNM 等），尝试从 message['4'] 提取 buyer_id
                    message_4 = message.get("4")
                    if isinstance(message_4, dict):
                        user_id = message_4.get("senderUserId") or None
                elif isinstance(message_1, dict):
                    # 如果message['1']是字典，从message["1"]["10"]["senderUserId"]中提取user_id
                    if "10" in message_1 and isinstance(message_1["10"], dict):
                        user_id = message_1["10"].get("senderUserId") or None
                    else:
                        user_id = None
                else:
                    user_id = None
            except Exception as e:
                logger.warning(f"提取用户ID失败: {self._safe_str(e)}")
                user_id = None


            # 安全地提取商品ID
            item_id = None
            try:
                if "1" in message and isinstance(message["1"], dict) and "10" in message["1"] and isinstance(message["1"]["10"], dict):
                    url_info = message["1"]["10"].get("reminderUrl", "")
                    if isinstance(url_info, str) and "itemId=" in url_info:
                        item_id = url_info.split("itemId=")[1].split("&")[0]

                # 如果没有提取到，使用辅助方法
                if not item_id:
                    item_id = self.extract_item_id_from_message(message)

                if not item_id:
                    item_id = f"auto_{user_id}_{int(time.time())}"
                    logger.warning(f"无法提取商品ID，使用默认值: {item_id}")

            except Exception as e:
                logger.error(f"提取商品ID时发生错误: {self._safe_str(e)}")
                item_id = f"auto_{user_id}_{int(time.time())}"
            # 处理订单状态消息
            try:
                logger.info(f"【{self.cookie_id}】[{msg_id}] 消息内容: {message}")
                msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

                # 安全地检查订单状态
                red_reminder = None
                if isinstance(message, dict) and "3" in message and isinstance(message["3"], dict):
                    red_reminder = message["3"].get("redReminder")

                if red_reminder == '等待买家付款':
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】等待买家 {user_url} 付款')
                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（等待买家付款）")
                    return
                elif red_reminder == '交易关闭':
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】买家 {user_url} 交易关闭')

                    # 【修复】更新订单状态到数据库
                    if self.order_status_handler:
                        try:
                            self.order_status_handler.handle_red_reminder_order_status(
                                red_reminder=red_reminder,
                                message=message,
                                user_id=user_id,
                                cookie_id=self.cookie_id,
                                msg_time=msg_time,
                                match_context={
                                    'sid': None,
                                    'buyer_id': user_id,
                                    'item_id': item_id,
                                }
                            )
                        except Exception as e:
                            logger.error(f"【{self.cookie_id}】更新交易关闭订单状态失败: {self._safe_str(e)}")

                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（交易关闭）")
                    return
                elif red_reminder == '等待卖家发货':
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】交易成功 {user_url} 等待卖家发货')
                    
                    # 【关键修复】对于简化结构的消息（message['1']是字符串），根据sid查找订单信息后触发自动发货
                    # 简化消息结构: {'1': '56226853668@goofish', '2': 1, '3': {'redReminder': '等待卖家发货', ...}}
                    # message['1'] 就是 sid（会话ID）
                    # 【优化】只使用简化消息触发自动发货，完整付款消息已注释
                    if isinstance(message.get('1'), str):
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔔 检测到简化结构的发货通知消息，延迟处理')
                        await asyncio.sleep(30)
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔔 延迟30秒后处理简化发货')
                        # 检查是否启用自动确认发货
                        if self.is_auto_confirm_enabled():
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 自动确认发货已启用，开始处理')
                            
                            # 从简化消息中提取sid（会话ID），如 "56226853668@goofish"
                            simple_sid = message.get('1', '')
                            # 提取纯数字部分作为session_id_str
                            session_id_str = simple_sid.split('@')[0] if '@' in str(simple_sid) else simple_sid
                            
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔍 简化消息解析: sid={simple_sid}, session_id={session_id_str}')
                            
                            log_prefix = f'[{msg_time}] 【{self.cookie_id}】[{msg_id}]'
                            sid_lookup_minutes = 5
                            sid_lookup = self._lookup_delivery_order_by_sid(
                                simple_sid,
                                minutes=sid_lookup_minutes,
                                log_prefix=log_prefix
                            )
                            sid_lookup = await self._refresh_sid_lookup_if_needed(
                                simple_sid,
                                sid_lookup,
                                item_id=item_id,
                                buyer_id=user_id,
                                minutes=sid_lookup_minutes,
                                allow_bargain_ready=True,
                                log_prefix=log_prefix
                            )
                            recent_order = sid_lookup.get('order')
                            sid_match_type = sid_lookup.get('match_type', 'missing')
                            
                            if recent_order and sid_match_type in {'pending_ship', 'bargain_ready'}:
                                order_id = recent_order.get('order_id')
                                real_item_id = recent_order.get('item_id')
                                simple_user_id = recent_order.get('buyer_id', user_id)  # 从订单中获取buyer_id
                                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 通过sid从数据库找到订单: order_id={order_id}, item_id={real_item_id}, buyer_id={simple_user_id}')

                                if sid_match_type == 'bargain_ready':
                                    logger.info(
                                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 小刀订单缺少完整待发货卡片，'
                                        f'使用sid+小刀成功证据兜底进入自动发货: order_id={order_id}'
                                    )
                                
                                # 【防重复检查】先检查该订单是否已经在冷却期内（说明完整消息已经处理过）
                                if not self.can_auto_delivery(order_id):
                                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔒 订单 {order_id} 已在冷却期内（可能完整消息已处理），跳过简化消息发货')
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单已处理）")
                                    return
                                
                                # 【防重复检查】检查延迟锁状态
                                if self.is_lock_held(order_id):
                                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔒 订单 {order_id} 延迟锁已被持有（可能完整消息正在处理），跳过简化消息发货')
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单正在处理）")
                                    return
                                
                                # 使用正确的商品ID和订单ID调用自动发货
                                simple_chat_id = session_id_str  # 使用会话ID作为chat_id
                                
                                # 调用自动发货处理（使用简化消息专用方法）
                                await self._handle_simple_message_auto_delivery(
                                    websocket=websocket,
                                    order_id=order_id,
                                    item_id=real_item_id,
                                    user_id=simple_user_id,
                                    chat_id=simple_chat_id,
                                    msg_time=msg_time,
                                    msg_id=msg_id
                                )
                                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（简化消息自动发货）")
                                return
                            elif recent_order:
                                order_id = recent_order.get('order_id')
                                order_status = recent_order.get('order_status') or 'unknown'
                                if sid_match_type == 'already_processed':
                                    logger.info(
                                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ℹ️ sid命中的订单已处理完成，跳过重复发货: '
                                        f'order_id={order_id}, status={order_status}'
                                    )
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单已处理）")
                                elif sid_match_type == 'cancelled':
                                    logger.info(
                                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ℹ️ sid命中的订单已关闭，跳过自动发货: '
                                        f'order_id={order_id}'
                                    )
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单已关闭）")
                                else:
                                    logger.info(
                                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ℹ️ sid命中的订单当前状态不适合简化消息兜底发货，等待后续完整消息: '
                                        f'order_id={order_id}, status={order_status}'
                                    )
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单状态未就绪）")
                                return
                            else:
                                logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ❌ 未找到sid {simple_sid} 的最近订单，跳过自动发货')
                                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（未找到订单）")
                                return
                        else:
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⚠️ 未启用自动确认发货，跳过')
                            logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（未启用自动发货）")
                            return
                    # 如果不是简化结构，继续走正常流程
            except Exception:
                pass

            # 判断是否为聊天消息
            if not self.is_chat_message(message):
                logger.warning(f"【{self.cookie_id}】[{msg_id}] ⏹️ 非聊天消息，处理结束")
                return

            # 处理聊天消息
            try:
                # 安全地提取聊天消息信息
                if not (isinstance(message, dict) and "1" in message and isinstance(message["1"], dict)):
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息格式错误：缺少必要的字段结构")
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（格式错误）")
                    return

                message_1 = message["1"]
                if not isinstance(message_1.get("10"), dict):
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息格式错误：缺少消息详情字段")
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（缺少详情字段）")
                    return

                create_time = int(message_1.get("5", 0))
                message_10 = message_1["10"]
                send_user_id = message_10.get("senderUserId", "unknown")

                chat_id_raw = message_1.get("2", "")
                chat_id = chat_id_raw.split('@')[0] if '@' in str(chat_id_raw) else str(chat_id_raw)

                sender_nick_raw = str(message_10.get("senderNick") or '').strip()
                if sender_nick_raw:
                    send_user_name = sender_nick_raw
                else:
                    # senderNick 缺失时仅使用 reminderTitle 兜底，且必须过滤系统文案
                    # （例如 "买家已拍下，待付款"、"等待你发货"、"工作台通知" 等订单状态/卡片标题），
                    # 否则会被当作买家昵称写入 chat_messages.sender_name 并污染会话列表与通知。
                    reminder_title_raw = str(message_10.get("reminderTitle") or '').strip()
                    sanitized_reminder = self._sanitize_buyer_nick(
                        reminder_title_raw,
                        source="reminderTitle",
                        message_meta=message_10,
                        log_prefix=f"【{self.cookie_id}】[{msg_id}]"
                    ) if reminder_title_raw else None
                    if not sanitized_reminder and send_user_id and send_user_id != "unknown":
                        # 兜底：从本地历史聊天记录里找一个干净的买家昵称
                        try:
                            from db_manager import db_manager as _db_lookup
                            recovered_nick = _db_lookup._lookup_buyer_nick_from_chat_messages(
                                self.cookie_id, chat_id_raw or chat_id, send_user_id
                            )
                            if recovered_nick:
                                sanitized_reminder = recovered_nick
                        except Exception as _lookup_err:
                            logger.debug(
                                f"【{self.cookie_id}】[{msg_id}] 历史买家昵称兜底查询失败: {self._safe_str(_lookup_err)}"
                            )
                    send_user_name = sanitized_reminder or "未知用户"
                send_message = message_10.get("reminderContent", "")
                # 直接从已解出的 chat payload 拿 messageId，传给 dedupe 链路避免重复解同步包
                dedupe_message_id = self._extract_message_id_from_chat_payload(message_1, message_10)

            except Exception as e:
                logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 提取聊天消息信息失败: {self._safe_str(e)}")
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（提取信息失败）")
                return

            # 格式化消息时间
            msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(create_time/1000))


            message_route_info = self._classify_message_route(
                message=message,
                message_1=message_1,
                message_10=message_10,
                send_message=send_message,
            )
            message_route = message_route_info.get('route', 'user_chat')
            order_status_signal = message_route_info.get('order_status_signal')
            should_notify_message = bool(message_route_info.get('should_notify'))
            allow_auto_reply = bool(message_route_info.get('allow_auto_reply'))
            is_system_message = bool(message_route_info.get('is_system_message'))
            is_group_message = bool(message_route_info.get('is_group_message'))
            message_direction = message_route_info.get('message_direction', 0)
            content_type = message_route_info.get('content_type', 0)
            card_title = str(message_route_info.get('card_title') or '').strip()
            special_flow_card_titles = {
                '我已小刀，待刀成',
                '我已小刀,待刀成',
                '我已成功小刀，待发货',
                '我已成功小刀,待发货',
            }

            logger.info(
                f"【{self.cookie_id}】[{msg_id}] 消息分类: route={message_route}, "
                f"status_signal={order_status_signal or 'none'}, notify={should_notify_message}, "
                f"auto_reply={allow_auto_reply}, system={is_system_message}, "
                f"direction={message_direction}, contentType={content_type}"
            )

            if send_user_id == self.myid and not is_system_message:
                logger.info(f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【手动发出】 商品({item_id}): {send_message}")

                # Web /api/chat/send 已经做过落库+publish，如果命中去重标记
                # 说明这是闲鱼对同一条消息的回推，直接跳过避免前端看到两条。
                try:
                    from chat_event_hub import self_send_dedup
                    if self_send_dedup.consume(self.cookie_id, chat_id, str(self.myid), send_message):
                        _host.pause_manager.pause_chat(chat_id, self.cookie_id)
                        logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（Web 自发回推已去重）")
                        return
                except Exception as _e:
                    logger.debug(f"自发消息去重检查失败: {self._safe_str(_e)}")

                try:
                    from db_manager import db_manager as _db
                    from chat_event_hub import publish_chat_message
                    image_url = self._extract_image_url_from_message(message) if content_type == 2 else None
                    media_url = None
                    link_url = None
                    extra_json = None
                    _msg_id_db = _db.save_chat_message(
                        cookie_id=self.cookie_id, chat_id=chat_id,
                        sender_id=self.myid, sender_name=self.cookie_id,
                        content=send_message, content_type=content_type,
                        image_url=image_url,
                        item_id=item_id, direction=1, reply_source='手动',
                        media_url=media_url, link_url=link_url, extra_json=extra_json,
                    )
                    publish_chat_message(self.cookie_id, {
                        'msg_id': _msg_id_db, 'chat_id': chat_id,
                        'sender_id': self.myid, 'sender_name': self.cookie_id,
                        'content': send_message, 'content_type': content_type,
                        'image_url': image_url,
                        'item_id': item_id, 'direction': 1, 'reply_source': '手动',
                        'media_url': media_url, 'link_url': link_url, 'extra_json': extra_json,
                    })
                except Exception as _e:
                    logger.debug(f"保存/推送手动消息失败: {self._safe_str(_e)}")

                # 暂停该chat_id的自动回复10分钟
                _host.pause_manager.pause_chat(chat_id, self.cookie_id)

                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（手动发出消息）")
                return
            elif send_user_id == self.myid and is_system_message:
                logger.info(
                    f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] 检测到系统消息(sender=自己ID)，继续执行状态处理 "
                    f"(direction={message_direction}, contentType={content_type})"
                )
            else:
                logger.info(f"[{msg_time}] 【收到】用户: {send_user_name} (ID: {send_user_id}), 商品({item_id}): {send_message}")
                try:
                    from db_manager import db_manager as _db
                    from chat_event_hub import publish_chat_message
                    image_url = self._extract_image_url_from_message(message) if content_type == 2 else None
                    media_url = None
                    link_url = None
                    extra_json = None
                    _msg_id_db = _db.save_chat_message(
                        cookie_id=self.cookie_id, chat_id=chat_id,
                        sender_id=send_user_id, sender_name=send_user_name,
                        content=send_message, content_type=content_type,
                        image_url=image_url,
                        item_id=item_id, direction=2,
                        media_url=media_url, link_url=link_url, extra_json=extra_json,
                    )
                    publish_chat_message(self.cookie_id, {
                        'msg_id': _msg_id_db, 'chat_id': chat_id,
                        'sender_id': send_user_id, 'sender_name': send_user_name,
                        'content': send_message, 'content_type': content_type,
                        'image_url': image_url,
                        'item_id': item_id, 'direction': 2,
                        'media_url': media_url, 'link_url': link_url, 'extra_json': extra_json,
                    })
                except Exception as _e:
                    logger.debug(f"保存/推送聊天消息失败: {self._safe_str(_e)}")

                if message_route == 'user_chat':
                    self.last_user_chat_time = time.time()

                # 【优先处理】检查是否正在等待亦凡卡劵账号输入
                async with self.yifan_account_lock:
                    if chat_id in self.yifan_account_waiting:
                        waiting_info = self.yifan_account_waiting[chat_id]
                        
                        # 检查超时（30分钟）
                        if time.time() - waiting_info['create_time'] > 1800:
                            logger.warning(f"账号输入等待超时，清除等待状态")
                            del self.yifan_account_waiting[chat_id]
                        elif waiting_info['buyer_id'] == send_user_id:
                            # 检查是否为客户真实消息（过滤系统消息）
                            # 真实客户消息: message['1']['7'] = 2, contentType = 1
                            # 系统消息: message['1']['7'] = 1, contentType = 6 (textCard)
                            message_1 = message.get('1', {})
                            message_direction = message_1.get('7', 0) if isinstance(message_1, dict) else 0
                            
                            # 获取contentType
                            content_type = 0
                            try:
                                message_6 = message_1.get('6', {})
                                if isinstance(message_6, dict):
                                    message_6_3 = message_6.get('3', {})
                                    if isinstance(message_6_3, dict):
                                        content_type = message_6_3.get('4', 0)
                            except Exception:
                                pass
                            
                            # 检查bizTag是否包含系统消息标识
                            is_system_msg = False
                            try:
                                message_10 = message_1.get('10', {})
                                if isinstance(message_10, dict):
                                    biz_tag = message_10.get('bizTag', '')
                                    if biz_tag and ('SECURITY' in biz_tag or 'taskName' in biz_tag or 'taskId' in biz_tag):
                                        is_system_msg = True
                            except Exception:
                                pass
                            
                            # 过滤非真实客户消息：
                            # 1. message['1']['7'] != 2 表示不是接收的消息
                            # 2. contentType = 6 表示系统卡片消息
                            # 3. bizTag包含系统标识
                            if message_direction != 2 or content_type == 6 or is_system_msg:
                                logger.info(f"【{self.cookie_id}】[{msg_id}] 收到系统消息，跳过账号确认处理（direction={message_direction}, contentType={content_type}, isSystem={is_system_msg}）")
                                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（系统消息）")
                                return
                            
                            # 是同一个用户的真实回复
                            if waiting_info['state'] == 'waiting_account':
                                # 等待账号输入阶段
                                account = send_message.strip()
                                if account:
                                    # 保存账号并发送确认消息
                                    waiting_info['account'] = account
                                    waiting_info['state'] = 'waiting_confirm'
                                    
                                    confirm_msg = f"{account}\n这是您要充值的账号，请回答\"是\"，进行确认下单，如果账号不对，请重新输入正确的账号，如果因为您账号输错，导致错误下单，概不退款。"
                                    await self.send_msg(self.ws, chat_id, send_user_id, confirm_msg)
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] 已保存充值账号: {account}，等待用户确认")
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（等待账号确认）")
                                    return  # 处理完毕，不再继续其他流程
                                    
                            elif waiting_info['state'] == 'waiting_confirm':
                                # 等待确认阶段
                                user_reply = send_message.strip()
                                
                                if user_reply == '是':
                                    # 用户确认，继续发货流程
                                    logger.info(f"用户确认账号，继续亦凡API发货流程")
                                    account = waiting_info['account']
                                    rule = waiting_info['rule']
                                    order_id_saved = waiting_info.get('order_id')
                                    item_id_saved = waiting_info.get('item_id')
                                    
                                    # 清除等待状态
                                    del self.yifan_account_waiting[chat_id]
                                    
                                    # 继续执行亦凡API调用（带账号）
                                    try:
                                        if self._check_buyer_blacklist_for_action(
                                            buyer_id=send_user_id,
                                            item_id=item_id_saved,
                                            order_id=order_id_saved,
                                            buyer_nick=send_user_name,
                                            action='亦凡账号确认自动发货',
                                            channel='auto',
                                            log_delivery=True,
                                        ):
                                            logger.info(f"【{self.cookie_id}】[{msg_id}] 亦凡账号确认发货被黑名单拦截")
                                            return

                                        # 直接调用亦凡API下单
                                        delivery_content = await self._call_yifan_api_with_account(
                                            rule, account, order_id_saved, item_id_saved, send_user_id, chat_id
                                        )
                                        
                                        if delivery_content:
                                            delivery_steps = self._build_delivery_steps(
                                                delivery_content,
                                                rule.get('card_description', '')
                                            )
                                            await self._send_delivery_steps(
                                                self.ws,
                                                chat_id,
                                                send_user_id,
                                                delivery_steps,
                                                log_prefix=f"亦凡账号确认发货 order_id={order_id_saved or 'unknown'}"
                                            )

                                            finalize_result = await self._finalize_delivery_after_send(
                                                delivery_meta={
                                                    'success': True,
                                                    'rule_id': rule.get('id'),
                                                    'card_id': rule.get('card_id'),
                                                    'card_type': rule.get('card_type'),
                                                    'order_spec_mode': None,
                                                    'rule_spec_mode': None,
                                                    'item_config_mode': None,
                                                    'data_card_pending_consume': False,
                                                    'data_line': None
                                                },
                                                order_id=order_id_saved,
                                                item_id=item_id_saved
                                            )
                                            if not finalize_result.get('success'):
                                                self._record_delivery_log(
                                                    order_id=order_id_saved,
                                                    item_id=item_id_saved,
                                                    buyer_id=send_user_id,
                                                    status='failed',
                                                    reason=finalize_result.get('error') or '亦凡账号确认发货发送成功但提交副作用失败',
                                                    channel='auto',
                                                    rule_meta={
                                                        'rule_id': rule.get('id'),
                                                        'rule_keyword': rule.get('keyword'),
                                                        'card_type': rule.get('card_type')
                                                    }
                                                )
                                                await self.send_msg(self.ws, chat_id, send_user_id, "发货消息已发送，但确认发货失败，请稍后刷新订单状态。")
                                                logger.error(f"亦凡API自动发货副作用提交失败: {finalize_result.get('error')}")
                                                return

                                            if order_id_saved:
                                                self.mark_delivery_sent(order_id_saved, context="亦凡账号确认发货发送成功")
                                                self._activate_delivery_lock(order_id_saved, delay_minutes=10)

                                            self._record_delivery_log(
                                                order_id=order_id_saved,
                                                item_id=item_id_saved,
                                                buyer_id=send_user_id,
                                                status='success',
                                                reason='亦凡账号确认发货发送成功',
                                                channel='auto',
                                                rule_meta={
                                                    'rule_id': rule.get('id'),
                                                    'rule_keyword': rule.get('keyword'),
                                                    'card_type': rule.get('card_type')
                                                }
                                            )
                                            logger.info(f"亦凡API自动发货成功")
                                        else:
                                            # 发货失败通知
                                            await self.send_msg(self.ws, chat_id, send_user_id, "抱歉，自动发货失败，请联系客服处理。")
                                    except Exception as e:
                                        logger.error(f"亦凡API发货异常: {self._safe_str(e)}")
                                        await self.send_msg(self.ws, chat_id, send_user_id, "系统异常，请联系客服处理。")
                                    
                                    return  # 处理完毕
                                    
                                else:
                                    # 用户输入的不是"是"，认为是重新输入账号
                                    new_account = user_reply
                                    if new_account:
                                        waiting_info['account'] = new_account
                                        waiting_info['retry_count'] += 1
                                        
                                        # 检查重试次数
                                        if waiting_info['retry_count'] >= 5:
                                            logger.warning(f"【{self.cookie_id}】[{msg_id}] 账号确认重试次数过多，取消发货")
                                            del self.yifan_account_waiting[chat_id]
                                            await self.send_msg(self.ws, chat_id, send_user_id, "账号确认失败次数过多，已取消发货，请重新下单。")
                                            logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（重试次数过多）")
                                            return
                                        
                                        confirm_msg = f"{new_account}\n这是您要充值的账号，请回答\"是\"，进行确认下单，如果账号不对，请重新输入正确的账号，如果因为您账号输错，导致错误下单，概不退款。"
                                        await self.send_msg(self.ws, chat_id, send_user_id, confirm_msg)
                                        logger.info(f"【{self.cookie_id}】[{msg_id}] 用户重新输入账号: {new_account}，再次等待确认")
                                        logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（等待账号重新确认）")
                                        return

                try:
                    if is_group_message:
                        logger.info(f"📱 检测到群组消息（sessionType=30），跳过消息通知")
                    elif should_notify_message:
                        await self.send_notification(send_user_name, send_user_id, send_message, item_id, chat_id)
                    else:
                        logger.info(
                            f"📱 当前消息不发送通知: route={message_route}, "
                            f"status_signal={order_status_signal or 'none'}, message={send_message}"
                        )
                except Exception as notify_error:
                    logger.error(f"📱 发送消息通知失败: {self._safe_str(notify_error)}")


            # 【优先处理】使用订单状态处理器处理系统消息
            if self.order_status_handler:
                try:
                    # 处理系统消息的订单状态更新
                    try:
                        handled = self.order_status_handler.handle_system_message(
                            message=message,
                            send_message=send_message,
                            cookie_id=self.cookie_id,
                            msg_time=msg_time,
                            match_context={
                                'sid': message_1.get('2', '') if isinstance(message_1, dict) else None,
                                'buyer_id': send_user_id,
                                'item_id': item_id,
                            }
                        )
                    except Exception as e:
                        logger.error(f"【{self.cookie_id}】处理系统消息失败: {self._safe_str(e)}")
                        handled = False
                    
                    # 处理红色提醒消息
                    if not handled:
                        try:
                            if isinstance(message, dict) and "3" in message and isinstance(message["3"], dict):
                                red_reminder = message["3"].get("redReminder")
                                user_id = message["3"].get("userId", "unknown")
                                
                                if red_reminder:
                                    try:
                                        self.order_status_handler.handle_red_reminder_message(
                                            message=message,
                                            red_reminder=red_reminder,
                                            user_id=user_id,
                                            cookie_id=self.cookie_id,
                                            msg_time=msg_time,
                                            match_context={
                                                'sid': message_1.get('2', '') if isinstance(message_1, dict) else None,
                                                'buyer_id': send_user_id,
                                                'item_id': item_id,
                                            }
                                        )
                                    except Exception as e:
                                        logger.error(f"【{self.cookie_id}】处理红色提醒消息失败: {self._safe_str(e)}")
                        except Exception as red_e:
                            logger.warning(f"处理红色提醒消息失败: {self._safe_str(red_e)}")
                            
                except Exception as e:
                    logger.error(f"订单状态处理失败: {self._safe_str(e)}")

            # 关键状态消息到达时，按需补刷一次订单详情，避免缓存把状态留在旧值
            if order_id and order_status_signal in {'pending_ship', 'shipped', 'completed', 'cancelled', 'refunding'}:
                try:
                    refresh_sid = ''
                    if isinstance(message_1, dict):
                        refresh_sid = message_1.get("2", "")

                    await self._maybe_force_refresh_order_detail_for_signal(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        sid=refresh_sid,
                        buyer_nick=send_user_name,
                        status_signal=order_status_signal,
                        reason=f'message_signal_{order_status_signal}',
                        delay_seconds=1 if order_status_signal == 'pending_ship' else 0,
                        log_prefix=f"【{self.cookie_id}】[{msg_id}]"
                    )
                except Exception as refresh_e:
                    logger.error(
                        f"【{self.cookie_id}】[{msg_id}] 状态消息触发订单详情补刷失败: {self._safe_str(refresh_e)}"
                    )

            # 【优先处理】检查系统消息和自动发货触发消息（不受人工接入暂停影响）
            fallback_ignore_keywords = [
                '不想宝贝被砍价',
                'AI正在帮你回复',
                '发来一条',
                '小心假客服骗钱',
                '蚂蚁森林能量',
                '恭喜你拿到曝光卡',
                '订单即将自动确认收货',
                '温馨提醒：商品信息近期有过变更',
            ]
            if send_message == '[我已拍下，待付款]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 系统消息不处理')
                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（系统消息：待付款）")
                return
            elif send_message == '[你关闭了订单，钱款已原路退返]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 系统消息不处理')
                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（系统消息：订单关闭）")
                return
            elif send_message in [
                '快给ta一个评价吧~',
                '快给ta一个评价吧～',
            ]:
                # 检测到评价提醒消息，尝试自动好评
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🌟 检测到评价提醒消息: {send_message}')
                await self.handle_auto_comment(message, msg_time, msg_id)
                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（评价提醒消息）")
                return
            elif message_route == 'system_notice' or any(keyword in send_message for keyword in fallback_ignore_keywords):
                logger.info(
                    f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⏹️ 系统提示消息不处理: '
                    f'route={message_route}, message={send_message}'
                )
                return
            # 简化消息通过 sid 查找订单，更可靠
            elif message_route == 'order_status' and self._is_auto_delivery_trigger(send_message):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 检测到自动发货触发消息: {send_message}')

                # 只允许系统消息触发自动发货，防止买家手动输入关键字触发
                if not is_system_message:
                    logger.warning(
                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⚠️ 自动发货关键字来自非系统消息，已忽略 '
                        f'(direction={message_direction}, contentType={content_type})'
                    )
                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（非系统触发）")
                    return

                # 检查是否启用自动确认发货
                if not self.is_auto_confirm_enabled():
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 未启用自动确认发货，跳过自动发货')
                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（未启用自动发货）")
                    return
                # 使用统一的自动发货处理方法（传递message_data以便提取订单ID）
                await self._handle_auto_delivery(websocket, message, send_user_name, send_user_id,
                                               item_id, chat_id, msg_time, message_data)
                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（自动发货完成）")
                return
            # 【重要】检查小刀流程卡片消息 - 即使在人工接入暂停期间也要处理
            elif send_message == '[卡片消息]' or card_title in special_flow_card_titles:
                # 检查是否为小刀相关卡片消息
                try:
                    # 从消息中提取卡片内容
                    card_title = card_title or None
                    card_message_1 = message.get("1", {}) if isinstance(message, dict) else {}
                    if not card_title and isinstance(card_message_1, dict):
                        if "6" in card_message_1 and isinstance(card_message_1["6"], dict):
                            message_6 = card_message_1["6"]
                            if "3" in message_6 and isinstance(message_6["3"], dict):
                                message_6_3 = message_6["3"]
                                if "5" in message_6_3:
                                    # 解析JSON内容
                                    try:
                                        card_content = json.loads(message_6_3["5"])
                                        if "dxCard" in card_content and "item" in card_content["dxCard"]:
                                            card_item = card_content["dxCard"]["item"]
                                            if "main" in card_item and "exContent" in card_item["main"]:
                                                ex_content = card_item["main"]["exContent"]
                                                card_title = ex_content.get("title", "")
                                    except (json.JSONDecodeError, KeyError) as e:
                                        logger.warning(f"解析卡片消息失败: {e}")

                    # 卡片流程仅接受系统消息，避免伪造卡片触发
                    card_message_direction = card_message_1.get('7', 0) if isinstance(card_message_1, dict) else 0
                    card_content_type = 0
                    card_is_system_biz = False
                    try:
                        card_message_6 = card_message_1.get('6', {}) if isinstance(card_message_1, dict) else {}
                        if isinstance(card_message_6, dict):
                            card_message_6_3 = card_message_6.get('3', {})
                            if isinstance(card_message_6_3, dict):
                                card_content_type = card_message_6_3.get('4', 0)
                    except Exception:
                        pass

                    try:
                        card_message_10 = card_message_1.get('10', {}) if isinstance(card_message_1, dict) else {}
                        if isinstance(card_message_10, dict):
                            biz_tag = card_message_10.get('bizTag', '')
                            if biz_tag and ('SECURITY' in biz_tag or 'taskName' in biz_tag or 'taskId' in biz_tag):
                                card_is_system_biz = True
                    except Exception:
                        pass

                    is_system_card_message = card_message_direction == 1 or card_content_type == 6 or card_is_system_biz
                    if not is_system_card_message:
                        logger.warning(
                            f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⚠️ 非系统卡片消息，忽略小刀流程 '
                            f'(direction={card_message_direction}, contentType={card_content_type}, isSystemBiz={card_is_system_biz})'
                        )
                        return

                    waiting_bargain_titles = {"我已小刀，待刀成", "我已小刀,待刀成"}
                    ready_to_ship_titles = {"我已成功小刀，待发货", "我已成功小刀,待发货"}

                    # 第一阶段：待刀成，仅执行免拼，不直接发货
                    if card_title in waiting_bargain_titles:
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】【系统】检测到"{card_title}"，执行免拼流程')
                        
                        # 检查是否启用自动确认发货
                        if not self.is_auto_confirm_enabled():
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】未启用自动确认发货，跳过自动小刀和自动发货')
                            return

                        # 检查商品是否属于当前cookies
                        if item_id and item_id != "未知商品":
                            try:
                                if not await self._ensure_item_owned_by_current_account(
                                    item_id,
                                    log_prefix=f'[{msg_time}] 【{self.cookie_id}】'
                                ):
                                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 商品 {item_id} 不属于当前账号，跳过免拼发货')
                                    return
                                logger.warning(f'[{msg_time}] 【{self.cookie_id}】✅ 商品 {item_id} 归属验证通过')
                            except Exception as e:
                                logger.error(f'[{msg_time}] 【{self.cookie_id}】检查商品归属失败: {self._safe_str(e)}，跳过免拼发货')
                                return

                        # 提取订单ID（传递原始消息数据以便在解密消息中找不到时进行备用搜索）
                        order_id = self._extract_order_id(message, message_data)
                        if not order_id:
                            logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 未能提取到订单ID，无法执行免拼发货')
                            return

                        self._mark_order_bargain_flow(
                            order_id,
                            item_id=item_id,
                            buyer_id=send_user_id,
                            context=card_title or 'waiting_bargain',
                        )

                        # 延迟2秒后执行免拼发货
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】延迟2秒后执行免拼发货...')
                        await asyncio.sleep(2)
                        # 调用自动免拼发货方法
                        result = await self.auto_freeshipping(order_id, item_id, send_user_id)
                        if result.get('success'):
                            self._mark_order_bargain_flow(
                                order_id,
                                item_id=item_id,
                                buyer_id=send_user_id,
                                apply_configured_price=True,
                                success_detected=True,
                                context=f'{card_title or "waiting_bargain"}_success',
                            )
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】✅ 自动免拼发货成功')
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】⏳ 已完成免拼，等待"我已成功小刀，待发货"卡片后再自动发货')
                            return
                        else:
                            logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 自动免拼发货失败: {result.get("error", "未知错误")}')
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】⏹️ 免拼失败，不执行自动发货')
                            return

                    # 第二阶段：成功小刀待发货，触发自动发货
                    elif card_title in ready_to_ship_titles:
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】【系统】检测到"{card_title}"，开始自动发货')

                        order_id = self._extract_order_id(message, message_data)
                        if order_id:
                            self._mark_order_bargain_flow(
                                order_id,
                                item_id=item_id,
                                buyer_id=send_user_id,
                                apply_configured_price=True,
                                success_detected=True,
                                context=card_title,
                            )

                        # 检查是否启用自动确认发货
                        if not self.is_auto_confirm_enabled():
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】未启用自动确认发货，跳过自动发货')
                            return

                        await self._handle_auto_delivery(
                            websocket, message, send_user_name, send_user_id,
                            item_id, chat_id, msg_time, message_data
                        )
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】⏹️ 小刀成功待发货卡片处理完成')
                        return
                    else:
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】收到卡片消息，标题: {card_title or "未知"}')
                        # 如果不是目标卡片消息，继续正常处理流程（会受到暂停影响）

                except Exception as e:
                    logger.error(f"处理卡片消息异常: {self._safe_str(e)}")
                    # 如果处理异常，继续正常处理流程（会受到暂停影响）

            # 自动更新买家昵称（补全历史订单的昵称信息）
            # 需要过滤掉系统提示文本，避免将"买家已拍下，待付款"等写入昵称
            if send_user_id and send_user_name:
                valid_buyer_nick = self._sanitize_buyer_nick(
                    send_user_name,
                    source="message_sender",
                    message_meta=message_10 if isinstance(message_10, dict) else None,
                    log_prefix=f"【{self.cookie_id}】[{msg_id}]"
                )
                if valid_buyer_nick:
                    try:
                        from db_manager import db_manager
                        _db_package().update_buyer_nick_by_buyer_id(send_user_id, valid_buyer_nick, self.cookie_id)
                    except Exception as e:
                        logger.debug(f"更新买家昵称失败: {self._safe_str(e)}")

            if not allow_auto_reply:
                logger.info(
                    f"【{self.cookie_id}】[{msg_id}] ⏹️ 当前消息不进入自动回复链: "
                    f"route={message_route}, status_signal={order_status_signal or 'none'}"
                )
                return

            # 身份判断：本账号主动去别人商品下咨询/购买时，自己是买家身份，
            # 对方（卖家）发来的消息不应触发本账号的自动/AI回复
            if await self._is_item_owned_by_self(item_id) is False:
                logger.info(
                    f"【{self.cookie_id}】[{msg_id}] ⏹️ 商品 {item_id} 非本账号所有"
                    f"（本账号为买家身份），跳过自动回复"
                )
                return

            # 使用防抖机制处理聊天消息回复
            # 如果用户连续发送消息，等待用户停止发送后再回复最后一条消息
            await self._schedule_debounced_reply(
                chat_id=chat_id,
                message_data=message_data,
                websocket=websocket,
                send_user_name=send_user_name,
                send_user_id=send_user_id,
                send_message=send_message,
                item_id=item_id,
                msg_time=msg_time,
                dedupe_message_id=dedupe_message_id,
                dedupe_create_time=create_time,
            )

        except Exception as e:
            logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 处理消息时发生异常: {self._safe_str(e)}")
            if msg_size > 3000:
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⚠️⚠️⚠️ 大消息({msg_size}字节)处理异常！")
            logger.warning(f"【{self.cookie_id}】[{msg_id}] 原始消息: {message_data}")
            import traceback
            logger.error(f"【{self.cookie_id}】[{msg_id}] 异常堆栈: {traceback.format_exc()}")
        finally:
            # 确保每条消息都有明确的处理结束标记
            logger.info(f"【{self.cookie_id}】[{msg_id}] 🏁 消息处理完成 ({msg_size}字节)")


class SendMixin:
    """出站消息发送与回复内容生成。"""

    async def create_chat(self, ws, toid, item_id='891198795482'):
        msg = {
            "lwp": "/r/SingleChatConversation/create",
            "headers": {
                "mid": _host.generate_mid()
            },
            "body": [
                {
                    "pairFirst": f"{toid}@goofish",
                    "pairSecond": f"{self.myid}@goofish",
                    "bizType": "1",
                    "extension": {
                        "itemId": item_id
                    },
                    "ctx": {
                        "appVersion": "1.0",
                        "platform": "web"
                    }
                }
            ]
        }
        await ws.send(json.dumps(msg))
    async def send_msg(self, ws, cid, toid, text):
        text = {
            "contentType": 1,
            "text": {
                "text": text
            }
        }
        text_base64 = str(base64.b64encode(json.dumps(text).encode('utf-8')), 'utf-8')
        msg = {
            "lwp": "/r/MessageSend/sendByReceiverScope",
            "headers": {
                "mid": _host.generate_mid()
            },
            "body": [
                {
                    "uuid": _host.generate_uuid(),
                    "cid": f"{cid}@goofish",
                    "conversationType": 1,
                    "content": {
                        "contentType": 101,
                        "custom": {
                            "type": 1,
                            "data": text_base64
                        }
                    },
                    "redPointPolicy": 0,
                    "extension": {
                        "extJson": "{}"
                    },
                    "ctx": {
                        "appVersion": "1.0",
                        "platform": "web"
                    },
                    "mtags": {},
                    "msgReadStatusSetting": 1
                },
                {
                    "actualReceivers": [
                        f"{toid}@goofish",
                        f"{self.myid}@goofish"
                    ]
                }
            ]
        }
        await ws.send(json.dumps(msg))
    async def send_msg_once(self, toid, item_id, text):
        """单次发送消息（创建新的WebSocket连接）"""
        headers = self._build_websocket_headers()

        logger.info(f"【{self.cookie_id}】开始单次发送消息: toid={toid}, item_id={item_id}")

        # 兼容不同版本的websockets库
        try:
            async with _host.websockets.connect(
                self.base_url,
                extra_headers=headers,
                close_timeout=5  # 添加关闭超时
            ) as websocket:
                result = await self._handle_websocket_connection(websocket, toid, item_id, text)
                if result:
                    logger.info(f"【{self.cookie_id}】单次发送消息成功")
                else:
                    raise Exception("消息发送失败")
        except TypeError as e:
            # 安全地检查异常信息
            error_msg = self._safe_str(e)

            if "extra_headers" in error_msg:
                logger.warning("websockets库不支持extra_headers参数，使用兼容模式")
                # 使用兼容模式
                async with _host.websockets.connect(
                    self.base_url,
                    additional_headers=headers,
                    close_timeout=5
                ) as websocket:
                    result = await self._handle_websocket_connection(websocket, toid, item_id, text)
                    if result:
                        logger.info(f"【{self.cookie_id}】单次发送消息成功(兼容模式)")
                    else:
                        raise Exception("消息发送失败")
            else:
                raise
        except _host.websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"【{self.cookie_id}】WebSocket连接关闭: {self._safe_str(e)}")
            # 连接关闭但消息可能已发送，不抛出异常
        except Exception as e:
            logger.error(f"【{self.cookie_id}】单次发送消息异常: {self._safe_str(e)}")
            raise
    async def send_image_msg(self, ws, cid, toid, image_url, width=800, height=600, card_id=None):
        """发送图片消息"""
        try:
            # 检查图片URL是否需要上传到CDN
            original_url = image_url

            if self._is_cdn_url(image_url):
                # 已经是CDN链接，直接使用
                logger.info(f"【{self.cookie_id}】使用已有的CDN图片链接: {image_url}")
            elif image_url.startswith('/static/uploads/') or image_url.startswith('static/uploads/'):
                # 本地图片，需要上传到闲鱼CDN
                local_image_path = image_url.replace('/static/uploads/', 'static/uploads/')
                if os.path.exists(local_image_path):
                    logger.info(f"【{self.cookie_id}】准备上传本地图片到闲鱼CDN: {local_image_path}")

                    # 使用图片上传器上传到闲鱼CDN
                    from utils.image_uploader import ImageUploader
                    uploader = ImageUploader(self.cookies_str)

                    async with uploader:
                        cdn_url = await uploader.upload_image(local_image_path)
                        if cdn_url:
                            logger.info(f"【{self.cookie_id}】图片上传成功，CDN URL: {cdn_url}")
                            image_url = cdn_url

                            # 如果是卡券图片，更新数据库中的图片URL
                            if card_id is not None:
                                await self._update_card_image_url(card_id, cdn_url)

                            # 获取实际图片尺寸
                            from utils.image_utils import image_manager
                            try:
                                actual_width, actual_height = image_manager.get_image_size(local_image_path)
                                if actual_width and actual_height:
                                    width, height = actual_width, actual_height
                                    logger.info(f"【{self.cookie_id}】获取到实际图片尺寸: {width}x{height}")
                            except Exception as e:
                                logger.warning(f"【{self.cookie_id}】获取图片尺寸失败，使用默认尺寸: {e}")
                        else:
                            logger.error(f"【{self.cookie_id}】图片上传失败: {local_image_path}")
                            logger.error(f"【{self.cookie_id}】❌ Cookie可能已失效！请检查配置并更新Cookie")
                            raise Exception(f"图片上传失败（Cookie可能已失效）: {local_image_path}")
                else:
                    logger.error(f"【{self.cookie_id}】本地图片文件不存在: {local_image_path}")
                    raise Exception(f"本地图片文件不存在: {local_image_path}")
            else:
                logger.warning(f"【{self.cookie_id}】未知的图片URL格式: {image_url}")

            # 记录详细的图片信息
            logger.info(f"【{self.cookie_id}】准备发送图片消息:")
            logger.info(f"  - 原始URL: {original_url}")
            logger.info(f"  - CDN URL: {image_url}")
            logger.info(f"  - 图片尺寸: {width}x{height}")
            logger.info(f"  - 聊天ID: {cid}")
            logger.info(f"  - 接收者ID: {toid}")

            # 构造图片消息内容 - 使用正确的闲鱼格式
            image_content = {
                "contentType": 2,  # 图片消息类型
                "image": {
                    "pics": [
                        {
                            "height": int(height),
                            "type": 0,
                            "url": image_url,
                            "width": int(width)
                        }
                    ]
                }
            }

            # Base64编码
            content_json = json.dumps(image_content, ensure_ascii=False)
            content_base64 = str(base64.b64encode(content_json.encode('utf-8')), 'utf-8')

            logger.info(f"【{self.cookie_id}】图片内容JSON: {content_json}")
            logger.info(f"【{self.cookie_id}】Base64编码长度: {len(content_base64)}")

            # 构造WebSocket消息（完全参考send_msg的格式）
            msg = {
                "lwp": "/r/MessageSend/sendByReceiverScope",
                "headers": {
                    "mid": _host.generate_mid()
                },
                "body": [
                    {
                        "uuid": _host.generate_uuid(),
                        "cid": f"{cid}@goofish",
                        "conversationType": 1,
                        "content": {
                            "contentType": 101,
                            "custom": {
                                "type": 1,
                                "data": content_base64
                            }
                        },
                        "redPointPolicy": 0,
                        "extension": {
                            "extJson": "{}"
                        },
                        "ctx": {
                            "appVersion": "1.0",
                            "platform": "web"
                        },
                        "mtags": {},
                        "msgReadStatusSetting": 1
                    },
                    {
                        "actualReceivers": [
                            f"{toid}@goofish",
                            f"{self.myid}@goofish"
                        ]
                    }
                ]
            }

            await ws.send(json.dumps(msg))
            logger.info(f"【{self.cookie_id}】图片消息发送成功: {image_url}")

        except Exception as e:
            logger.error(f"【{self.cookie_id}】发送图片消息失败: {self._safe_str(e)}")
            raise
    async def send_image_from_file(self, ws, cid, toid, image_path):
        """从本地文件发送图片"""
        try:
            # 上传图片到闲鱼CDN
            logger.info(f"【{self.cookie_id}】开始上传图片: {image_path}")

            from utils.image_uploader import ImageUploader
            uploader = ImageUploader(self.cookies_str)

            async with uploader:
                image_url = await uploader.upload_image(image_path)

            if image_url:
                # 获取图片信息
                from utils.image_utils import image_manager
                try:
                    from PIL import Image
                    with Image.open(image_path) as img:
                        width, height = img.size
                except Exception as e:
                    logger.warning(f"无法获取图片尺寸，使用默认值: {e}")
                    width, height = 800, 600

                # 发送图片消息
                await self.send_image_msg(ws, cid, toid, image_url, width, height)
                logger.info(f"【{self.cookie_id}】图片发送完成: {image_path} -> {image_url}")
                return True
            else:
                logger.error(f"【{self.cookie_id}】图片上传失败: {image_path}")
                logger.error(f"【{self.cookie_id}】❌ Cookie可能已失效！请检查配置并更新Cookie")
                return False

        except Exception as e:
            logger.error(f"【{self.cookie_id}】从文件发送图片失败: {self._safe_str(e)}")
            return False
    async def send_heartbeat(self, ws):
        """发送心跳包"""
        # 检查WebSocket连接状态，如果已关闭则不发送
        if ws.closed:
            raise ConnectionError("WebSocket连接已关闭，无法发送心跳")
        
        heartbeat_mid = _host.generate_mid()
        msg = {
            "lwp": "/!",
            "headers": {
                "mid": heartbeat_mid
            }
        }
        # 添加超时保护，避免在WebSocket关闭时阻塞
        try:
            self.last_sent_heartbeat_mid = heartbeat_mid
            self.pending_heartbeat_mids.append(heartbeat_mid)
            await asyncio.wait_for(ws.send(json.dumps(msg)), timeout=2.0)
            self.last_heartbeat_time = time.time()
            logger.warning(f"【{self.cookie_id}】心跳包已发送 [ID:{heartbeat_mid}]")
        except asyncio.TimeoutError:
            raise ConnectionError("心跳发送超时，WebSocket可能已断开")
        except asyncio.CancelledError:
            # 如果被取消，立即重新抛出，不执行后续操作
            raise
    async def send_delivery_steps_once(self, toid, item_id, delivery_steps):
        """单次发送发货步骤（创建新的WebSocket连接）。"""
        headers = self._build_websocket_headers()

        logger.info(f"【{self.cookie_id}】开始单次发送发货步骤: toid={toid}, item_id={item_id}, steps={len(delivery_steps or [])}")

        try:
            async with _host.websockets.connect(
                self.base_url,
                extra_headers=headers,
                close_timeout=5
            ) as websocket:
                result = await self._handle_websocket_connection_steps(websocket, toid, item_id, delivery_steps)
                if result:
                    logger.info(f"【{self.cookie_id}】单次发送发货步骤成功")
                else:
                    raise Exception("发货步骤发送失败")
        except TypeError as e:
            error_msg = self._safe_str(e)

            if "extra_headers" in error_msg:
                logger.warning("websockets库不支持extra_headers参数，使用兼容模式发送发货步骤")
                async with _host.websockets.connect(
                    self.base_url,
                    additional_headers=headers,
                    close_timeout=5
                ) as websocket:
                    result = await self._handle_websocket_connection_steps(websocket, toid, item_id, delivery_steps)
                    if result:
                        logger.info(f"【{self.cookie_id}】单次发送发货步骤成功(兼容模式)")
                    else:
                        raise Exception("发货步骤发送失败")
            else:
                raise
        except _host.websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"【{self.cookie_id}】WebSocket连接关闭: {self._safe_str(e)}")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】单次发送发货步骤异常: {self._safe_str(e)}")
            raise
    async def _send_delivery_steps(self, websocket, chat_id: str, user_id: str, delivery_steps, user_url: str = None,
                                   log_prefix: str = "自动发货", card_id: int = None):
        """按顺序发送发货步骤，支持文本与图片混排。"""
        steps = delivery_steps or []
        if not steps:
            raise ValueError("发货步骤为空")

        total_steps = len(steps)
        user_url = user_url or f'https://www.goofish.com/personal?userId={user_id}'

        for index, step in enumerate(steps, start=1):
            step_type = step.get('type')
            step_content = step.get('content') or ''

            if step_type == 'image':
                image_data = step_content.replace("__IMAGE_SEND__", "", 1)
                image_card_id = card_id
                image_url = image_data
                if "|" in image_data:
                    card_id_str, image_url = image_data.split("|", 1)
                    try:
                        image_card_id = int(card_id_str)
                    except ValueError:
                        logger.error(f"无效的卡券ID: {card_id_str}")
                        image_card_id = card_id

                await self.send_image_msg(websocket, chat_id, user_id, image_url, card_id=image_card_id)
                logger.info(
                    f"【{log_prefix}】步骤 {index}/{total_steps} 已向 {user_url} 发送图片: {image_url}"
                )
            else:
                await self.send_msg(websocket, chat_id, user_id, step_content)
                logger.info(
                    f"【{log_prefix}】步骤 {index}/{total_steps} 已向 {user_url} 发送文本内容"
                )

            if total_steps > 1 and index < total_steps:
                await asyncio.sleep(0.3)
    def _build_delivery_send_groups(self, prepared_units, total_units: int,
                                    max_units_per_message: int = DELIVERY_BATCH_MAX_UNITS,
                                    max_chars_per_message: int = DELIVERY_BATCH_MAX_CHARS):
        """按数量和字符数双阈值生成发货发送批次。"""
        if max(1, int(total_units or 1)) <= 1:
            return [{
                'mode': 'single',
                'units': [prepared_unit],
                'delivery_steps': prepared_unit.get('delivery_steps') or [],
                'unit_count': 1,
                'char_count': 0,
            } for prepared_unit in sorted(prepared_units or [], key=lambda unit: int(unit.get('unit_index') or 0))]

        groups = []
        current_batch_units = []
        current_batch_chars = 0

        def flush_current_batch():
            nonlocal current_batch_units, current_batch_chars
            if not current_batch_units:
                return

            batched_text = '\n\n'.join(unit['batched_text'] for unit in current_batch_units)
            groups.append({
                'mode': 'batched_text',
                'units': list(current_batch_units),
                'delivery_steps': [{'type': 'text', 'content': batched_text}],
                'unit_count': len(current_batch_units),
                'char_count': len(batched_text),
            })
            current_batch_units = []
            current_batch_chars = 0

        for prepared_unit in sorted(prepared_units or [], key=lambda unit: int(unit.get('unit_index') or 0)):
            delivery_steps = prepared_unit.get('delivery_steps') or []
            rule_meta = prepared_unit.get('rule_meta') or {}
            card_type = prepared_unit.get('card_type') or rule_meta.get('card_type')

            if not self._can_batch_text_delivery(delivery_steps, card_type):
                flush_current_batch()
                numbered_steps = self._apply_delivery_unit_numbering(
                    delivery_steps,
                    prepared_unit.get('unit_index') or 1,
                    total_units,
                    card_type,
                )
                groups.append({
                    'mode': 'single',
                    'units': [prepared_unit],
                    'delivery_steps': numbered_steps,
                    'unit_count': 1,
                    'char_count': 0,
                })
                continue

            numbered_text = self._format_delivery_unit_text(
                delivery_steps[0].get('content') or '',
                prepared_unit.get('unit_index') or 1,
                total_units,
            )

            if len(numbered_text) > max_chars_per_message:
                flush_current_batch()
                logger.warning(
                    f"【{self.cookie_id}】发货单元 {prepared_unit.get('unit_index')} 文本长度 {len(numbered_text)} 超过批量阈值 {max_chars_per_message}，回退为单条发送"
                )
                groups.append({
                    'mode': 'single',
                    'units': [prepared_unit],
                    'delivery_steps': [{'type': 'text', 'content': numbered_text}],
                    'unit_count': 1,
                    'char_count': len(numbered_text),
                })
                continue

            separator_chars = 2 if current_batch_units else 0
            exceeds_unit_limit = len(current_batch_units) >= max_units_per_message
            exceeds_char_limit = current_batch_units and (
                current_batch_chars + separator_chars + len(numbered_text) > max_chars_per_message
            )

            if exceeds_unit_limit or exceeds_char_limit:
                flush_current_batch()

            prepared_unit_with_text = dict(prepared_unit)
            prepared_unit_with_text['batched_text'] = numbered_text
            current_batch_units.append(prepared_unit_with_text)
            current_batch_chars += (2 if len(current_batch_units) > 1 else 0) + len(numbered_text)

        flush_current_batch()
        return groups
    async def _finalize_delivery_after_send(self, delivery_meta: dict = None, order_id: str = None,
                                            item_id: str = None, skip_confirm: bool = False,
                                            force_confirm: bool = False):
        """在消息发送成功后提交发货副作用：消费卡密、更新计数、确认发货。"""
        meta = delivery_meta or {}

        if not meta.get('success'):
            return {
                'success': False,
                'error': '发货元数据无效，无法提交副作用'
            }

        from db_manager import db_manager

        consume_required = bool(meta.get('data_card_pending_consume'))
        rule_id = meta.get('rule_id')
        card_id = meta.get('card_id')
        card_type = meta.get('card_type')
        expected_line = meta.get('data_line')
        reservation_id = meta.get('data_reservation_id')
        reservation_already_finalized = False

        if consume_required:
            if reservation_id:
                finalize_state = _db_package().finalize_batch_data_reservation(reservation_id)
                if not finalize_state.get('success'):
                    return {
                        'success': False,
                        'error': '批量数据预占完成失败，已中止后续确认发货'
                    }
                reservation_already_finalized = bool(finalize_state.get('already_finalized'))
            elif not card_id or card_type != 'data':
                return {
                    'success': False,
                    'error': '批量数据卡券元数据不完整，无法提交消费'
                }
            else:
                consumed = _db_package().consume_specific_batch_data(card_id, expected_line)
                if not consumed:
                    return {
                        'success': False,
                        'error': '批量数据消费失败，已中止后续确认发货'
                    }

        if rule_id and not consume_required:
            _db_package().increment_delivery_times(rule_id)

        if order_id and not skip_confirm:
            if not force_confirm and not self.is_auto_confirm_enabled():
                if meta.get('pending_platform_confirm') or meta.get('confirm_retry_required'):
                    return {
                        'success': False,
                        'error': '自动确认发货已关闭，无法补确认平台发货状态',
                        'pending_confirm': True,
                        'platform_confirm_failed': True,
                        'confirm_retry_required': True,
                    }
                logger.info(f"自动确认发货已关闭，跳过订单 {order_id}")
            else:
                current_time = time.time()
                should_confirm = True

                if order_id in self.confirmed_orders:
                    last_confirm_time = self.confirmed_orders[order_id]
                    if current_time - last_confirm_time < self.order_confirm_cooldown:
                        logger.info(f"订单 {order_id} 已在 {self.order_confirm_cooldown} 秒内确认过，跳过重复确认")
                        should_confirm = False

                if should_confirm:
                    logger.info(f"开始自动确认发货: 订单ID={order_id}, 商品ID={item_id}")
                    confirm_result = await self.auto_confirm(order_id, item_id)
                    if confirm_result.get('success'):
                        self.confirmed_orders[order_id] = current_time
                        logger.info(f"🎉 自动确认发货成功！订单ID: {order_id}")
                    else:
                        confirm_error = confirm_result.get('error', '未知错误')
                        stop_confirm_retry = self._is_non_retryable_platform_confirm_error(confirm_error, confirm_result)
                        return {
                            'success': False,
                            'error': f"自动确认发货失败: {confirm_error}",
                            'pending_confirm': not stop_confirm_retry,
                            'platform_confirm_failed': True,
                            'confirm_retry_required': not stop_confirm_retry,
                            'non_retryable_platform_confirm': stop_confirm_retry,
                            'stop_confirm_retry': stop_confirm_retry,
                            'session_expired': bool(confirm_result.get('session_expired')),
                            'need_relogin': bool(confirm_result.get('need_relogin')),
                            'confirm_result': confirm_result,
                        }

        if rule_id and consume_required and not reservation_already_finalized:
            _db_package().increment_delivery_times(rule_id)

        return {
            'success': True
        }
    async def _send_recovered_delivery_without_sid(
        self,
        order: Dict[str, Any],
        *,
        order_id: str,
        item_id: str,
        buyer_id: str,
        source: str,
    ) -> bool:
        from db_manager import db_manager

        lock_key = order_id
        if not self.can_auto_delivery(order_id):
            logger.info(f"【{self.cookie_id}】{source} 订单已处理或处于冷却期，跳过补偿发货: {order_id}")
            return False
        if self.is_lock_held(lock_key):
            logger.info(f"【{self.cookie_id}】{source} 订单延迟锁持有中，跳过补偿发货: {order_id}")
            return False

        order_lock = self._order_locks[lock_key]
        self._lock_usage_times[lock_key] = time.time()

        async with order_lock:
            if self.is_lock_held(lock_key) or not self.can_auto_delivery(order_id):
                logger.info(f"【{self.cookie_id}】{source} 获取锁后发现订单已处理，跳过补偿发货: {order_id}")
                return False

            pending_finalize_meta = self._get_pending_delivery_finalization_meta(order_id, 1)
            if pending_finalize_meta:
                finalize_result = await self._finalize_delivery_after_send(
                    delivery_meta=pending_finalize_meta,
                    order_id=order_id,
                    item_id=item_id,
                )
                if not finalize_result.get('success'):
                    self._persist_delivery_finalization_state(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        delivery_meta=pending_finalize_meta,
                        channel='auto',
                        status='sent',
                        last_error=finalize_result.get('error') or '补偿发货补完成 finalize 失败',
                    )
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        status='failed',
                        reason=finalize_result.get('error') or '补偿发货检测到已发送记录，但补完成发货收尾失败',
                        channel='auto',
                        rule_meta=pending_finalize_meta,
                    )
                    return False

                self._persist_delivery_finalization_state(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    delivery_meta=pending_finalize_meta,
                    channel='auto',
                    status='finalized',
                )
                self._sync_order_delivery_progress(
                    order_id=order_id,
                    cookie_id=self.cookie_id,
                    expected_quantity=1,
                    context=f"{source} 补完成收尾成功",
                )
                self._activate_delivery_lock(lock_key, delay_minutes=10)
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    status='success',
                    reason=f'{source} 检测到发货消息已发送，本次补完成收尾成功',
                    channel='auto',
                    rule_meta=pending_finalize_meta,
                )
                return True

            delivery_result = await self._auto_delivery(
                item_id,
                "待获取商品信息",
                order_id,
                buyer_id,
                '',
                include_meta=True,
            )
            if isinstance(delivery_result, dict):
                delivery_content = delivery_result.get('content')
                delivery_steps = delivery_result.get('delivery_steps') or []
                delivery_error = delivery_result.get('error')
                delivery_meta = delivery_result
            else:
                delivery_content = delivery_result
                delivery_steps = []
                delivery_error = None
                delivery_meta = {}

            if not delivery_content:
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    status='failed',
                    reason=delivery_error or f'{source} 未匹配到发货内容',
                    channel='auto',
                    rule_meta=delivery_meta,
                )
                return False

            if not delivery_steps:
                delivery_steps = self._build_delivery_steps(
                    delivery_content,
                    delivery_meta.get('card_description', '') if isinstance(delivery_meta, dict) else '',
                )

            try:
                await self.send_delivery_steps_once(buyer_id, item_id, delivery_steps)

                if not self._mark_data_reservation_sent_if_needed(delivery_meta):
                    self._release_data_reservation_if_needed(delivery_meta, error='补偿发货发送成功后标记预占已发送失败')
                    raise Exception('批量数据预占标记已发送失败')

                self._persist_delivery_finalization_state(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    delivery_meta=delivery_meta,
                    channel='auto',
                    status='sent',
                )

                finalize_result = await self._finalize_delivery_after_send(
                    delivery_meta=delivery_meta,
                    order_id=order_id,
                    item_id=item_id,
                )
                if not finalize_result.get('success'):
                    self._persist_delivery_finalization_state(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        delivery_meta=delivery_meta,
                        channel='auto',
                        status='sent',
                        last_error=finalize_result.get('error') or '补偿发货发送成功但提交发货副作用失败',
                    )
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        status='failed',
                        reason=finalize_result.get('error') or '补偿发货发送成功但提交发货副作用失败',
                        channel='auto',
                        rule_meta=delivery_meta,
                    )
                    return False

                self._persist_delivery_finalization_state(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    delivery_meta=delivery_meta,
                    channel='auto',
                    status='finalized',
                )
                self._sync_order_delivery_progress(
                    order_id=order_id,
                    cookie_id=self.cookie_id,
                    expected_quantity=int(order.get('quantity') or 1),
                    context=f"{source} 自动发货成功",
                )
                self._activate_delivery_lock(lock_key, delay_minutes=10)
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    status='success',
                    reason=f'{source} 自动发货步骤发送成功',
                    channel='auto',
                    rule_meta=delivery_meta,
                )
                logger.warning(f"【{self.cookie_id}】{source} 已完成补偿自动发货: order_id={order_id}")
                return True
            except Exception as send_error:
                self._release_data_reservation_if_needed(delivery_meta, error=self._safe_str(send_error))
                self._persist_delivery_finalization_state(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    delivery_meta=delivery_meta,
                    channel='auto',
                    status='failed',
                    last_error=self._safe_str(send_error),
                )
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    status='failed',
                    reason=f'{source} 自动发货消息发送失败: {self._safe_str(send_error)}',
                    channel='auto',
                    rule_meta=delivery_meta,
                )
                return False
    async def get_default_reply(self, send_user_name: str, send_user_id: str, send_message: str, chat_id: str, item_id: str = None) -> str:
        """获取默认回复内容，支持变量替换和只回复一次功能"""
        try:
            from db_manager import db_manager

            # 获取当前账号的默认回复设置
            default_reply_settings = _db_package().get_default_reply(self.cookie_id)

            if not default_reply_settings or not default_reply_settings.get('enabled', False):
                logger.warning(f"账号 {self.cookie_id} 未启用默认回复")
                return None

            # 检查"只回复一次"功能
            if default_reply_settings.get('reply_once', False) and chat_id:
                # 检查是否已经回复过这个chat_id
                if _db_package().has_default_reply_record(self.cookie_id, chat_id):
                    logger.info(f"【{self.cookie_id}】chat_id {chat_id} 已使用过默认回复，跳过（只回复一次）")
                    return "SKIP_REPLY"

            reply_content = default_reply_settings.get('reply_content', '')
            if not reply_content or (reply_content and reply_content.strip() == ''):
                logger.info(f"账号 {self.cookie_id} 默认回复内容为空，不进行回复")
                return "EMPTY_REPLY"  # 返回特殊标记表示不回复

            # 进行变量替换
            try:
                formatted_reply = reply_content.format(
                    send_user_name=send_user_name,
                    send_user_id=send_user_id,
                    send_message=send_message
                )

                # 如果开启了"只回复一次"功能，记录这次回复
                if default_reply_settings.get('reply_once', False) and chat_id:
                    _db_package().add_default_reply_record(self.cookie_id, chat_id)
                    logger.info(f"【{self.cookie_id}】记录默认回复: chat_id={chat_id}")

                logger.info(f"【{self.cookie_id}】使用默认回复: {formatted_reply}")
                return formatted_reply
            except Exception as format_error:
                logger.error(f"默认回复变量替换失败: {self._safe_str(format_error)}")
                # 如果变量替换失败，返回原始内容
                return reply_content

        except Exception as e:
            logger.error(f"获取默认回复失败: {self._safe_str(e)}")
            return None
    async def get_keyword_reply(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str = None) -> str:
        """获取关键词匹配回复（支持商品ID优先匹配和图片类型）"""
        try:
            from db_manager import db_manager

            # 获取当前账号的关键词列表（包含类型信息）
            keywords = _db_package().get_keywords_with_type(self.cookie_id)

            if not keywords:
                logger.warning(f"账号 {self.cookie_id} 没有配置关键词")
                return None

            # 1. 如果有商品ID，优先匹配该商品ID对应的关键词
            if item_id:
                for keyword_data in keywords:
                    keyword = keyword_data['keyword']
                    reply = keyword_data['reply']
                    keyword_item_id = keyword_data['item_id']
                    keyword_type = keyword_data.get('type', 'text')
                    image_url = keyword_data.get('image_url')

                    if keyword_item_id == item_id and keyword.lower() in send_message.lower():
                        logger.info(f"商品ID关键词匹配成功: 商品{item_id} '{keyword}' (类型: {keyword_type})")

                        # 根据关键词类型处理
                        if keyword_type == 'image' and image_url:
                            # 图片类型关键词，发送图片
                            return await self._handle_image_keyword(keyword, image_url, send_user_name, send_user_id, send_message)
                        else:
                            # 文本类型关键词，检查回复内容是否为空
                            if not reply or (reply and reply.strip() == ''):
                                logger.info(f"商品ID关键词 '{keyword}' 回复内容为空，不进行回复")
                                return "EMPTY_REPLY"  # 返回特殊标记表示匹配到但不回复

                            # 进行变量替换
                            try:
                                formatted_reply = reply.format(
                                    send_user_name=send_user_name,
                                    send_user_id=send_user_id,
                                    send_message=send_message
                                )
                                logger.info(f"商品ID文本关键词回复: {formatted_reply}")
                                return formatted_reply
                            except Exception as format_error:
                                logger.error(f"关键词回复变量替换失败: {self._safe_str(format_error)}")
                                # 如果变量替换失败，返回原始内容
                                return reply

            # 2. 如果商品ID匹配失败或没有商品ID，匹配没有商品ID的通用关键词
            for keyword_data in keywords:
                keyword = keyword_data['keyword']
                reply = keyword_data['reply']
                keyword_item_id = keyword_data['item_id']
                keyword_type = keyword_data.get('type', 'text')
                image_url = keyword_data.get('image_url')

                if not keyword_item_id and keyword.lower() in send_message.lower():
                    logger.info(f"通用关键词匹配成功: '{keyword}' (类型: {keyword_type})")

                    # 根据关键词类型处理
                    if keyword_type == 'image' and image_url:
                        # 图片类型关键词，发送图片
                        return await self._handle_image_keyword(keyword, image_url, send_user_name, send_user_id, send_message)
                    else:
                        # 文本类型关键词，检查回复内容是否为空
                        if not reply or (reply and reply.strip() == ''):
                            logger.info(f"通用关键词 '{keyword}' 回复内容为空，不进行回复")
                            return "EMPTY_REPLY"  # 返回特殊标记表示匹配到但不回复

                        # 进行变量替换
                        try:
                            formatted_reply = reply.format(
                                send_user_name=send_user_name,
                                send_user_id=send_user_id,
                                send_message=send_message
                            )
                            logger.info(f"通用文本关键词回复: {formatted_reply}")
                            return formatted_reply
                        except Exception as format_error:
                            logger.error(f"关键词回复变量替换失败: {self._safe_str(format_error)}")
                            # 如果变量替换失败，返回原始内容
                            return reply

            logger.warning(f"未找到匹配的关键词: {send_message}")
            return None

        except Exception as e:
            logger.error(f"获取关键词回复失败: {self._safe_str(e)}")
            return None
    async def get_ai_reply(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str, chat_id: str):
        """获取AI回复"""
        try:
            if self._check_buyer_blacklist_for_action(
                buyer_id=send_user_id,
                item_id=item_id,
                buyer_nick=send_user_name,
                action='AI回复',
                log_delivery=False,
            ):
                return None

            from ai_reply_engine import ai_reply_engine

            # 检查是否启用AI回复
            if not ai_reply_engine.is_ai_enabled(self.cookie_id):
                logger.warning(f"账号 {self.cookie_id} 未启用AI回复")
                return None

            # 从数据库获取商品信息
            from db_manager import db_manager
            item_info_raw = _db_package().get_item_info(self.cookie_id, item_id)

            if not item_info_raw:
                logger.warning(f"数据库中无商品信息: {item_id}")
                # 使用默认商品信息
                item_info = {
                    'title': '商品信息获取失败',
                    'price': 0,
                    'desc': '暂无商品描述'
                }
            else:
                # 解析数据库中的商品信息
                item_info = {
                    'title': item_info_raw.get('item_title', '未知商品'),
                    'price': self._parse_price(item_info_raw.get('item_price', '0')),
                    'desc': item_info_raw.get('item_detail', '暂无商品描述')
                }

            # 生成AI回复
            # 由于外部已实现防抖机制，跳过内部等待（skip_wait=True）
            reply = await ai_reply_engine.generate_reply_async(
                message=send_message,
                item_info=item_info,
                chat_id=chat_id,
                cookie_id=self.cookie_id,
                user_id=send_user_id,
                item_id=item_id,
                skip_wait=True  # 跳过内部等待，因为外部已实现防抖
            )

            if reply:
                logger.info(f"【{self.cookie_id}】AI回复生成成功: {reply}")
                return reply
            else:
                logger.warning(f"AI回复生成失败")
                return None

        except Exception as e:
            logger.error(f"获取AI回复失败: {self._safe_str(e)}")
            return None
    async def get_api_reply(self, msg_time, user_url, send_user_id, send_user_name, item_id, send_message, chat_id):
        """调用API获取回复消息"""
        try:
            if not self.session:
                await self.create_session()

            api_config = _host.AUTO_REPLY.get('api', {})
            timeout = _host.aiohttp.ClientTimeout(total=api_config.get('timeout', 10))

            payload = {
                "cookie_id": self.cookie_id,
                "msg_time": msg_time,
                "user_url": user_url,
                "send_user_id": send_user_id,
                "send_user_name": send_user_name,
                "item_id": item_id,
                "send_message": send_message,
                "chat_id": chat_id
            }
            internal_api_key = (os.getenv("XIANYU_REPLY_API_KEY") or "").strip()
            request_headers = {"X-Internal-API-Key": internal_api_key} if internal_api_key else {}

            async with self.session.post(
                api_config.get('url', 'http://localhost:8080/xianyu/reply'),
                json=payload,
                headers=request_headers,
                timeout=timeout
            ) as response:
                result = await response.json()

                # 将code转换为字符串进行比较，或者直接用数字比较
                if str(result.get('code')) == '200' or result.get('code') == 200:
                    send_msg = result.get('data', {}).get('send_msg')
                    if send_msg:
                        # 格式化消息中的占位符
                        return send_msg.format(
                            send_user_id=payload['send_user_id'],
                            send_user_name=payload['send_user_name'],
                            send_message=payload['send_message']
                        )
                    else:
                        logger.warning("API返回成功但无回复消息")
                        return None
                else:
                    logger.warning(f"API返回错误: {result.get('msg', '未知错误')}")
                    return None

        except asyncio.TimeoutError:
            logger.error("API调用超时")
            return None
        except Exception as e:
            logger.error(f"调用API出错: {self._safe_str(e)}")
            return None
    def debug_message_structure(self, message, context=""):
        """调试消息结构的辅助方法"""
        try:
            logger.warning(f"[{context}] 消息结构调试:")
            logger.warning(f"  消息类型: {type(message)}")

            if isinstance(message, dict):
                for key, value in message.items():
                    logger.warning(f"  键 '{key}': {type(value)} - {str(value)[:100]}...")

                    # 特别关注可能包含商品ID的字段
                    if key in ["1", "3"] and isinstance(value, dict):
                        logger.warning(f"    详细结构 '{key}':")
                        for sub_key, sub_value in value.items():
                            logger.warning(f"      '{sub_key}': {type(sub_value)} - {str(sub_value)[:50]}...")
            else:
                logger.warning(f"  消息内容: {str(message)[:200]}...")

        except Exception as e:
            logger.error(f"调试消息结构时发生错误: {self._safe_str(e)}")
    def _extract_image_url_from_message(self, message: dict) -> Optional[str]:
        """从消息结构中提取图片URL"""
        try:
            message_1 = message.get('1', {})
            if not isinstance(message_1, dict):
                return None
            message_6 = message_1.get('6', {})
            if not isinstance(message_6, dict):
                return None
            message_6_3 = message_6.get('3', {})
            if not isinstance(message_6_3, dict):
                return None
            content_json_str = message_6_3.get('5', '')
            if content_json_str:
                import json as _json
                content_obj = _json.loads(content_json_str)
                pics = content_obj.get('image', {}).get('pics', [])
                if pics:
                    return pics[0].get('url', '')
        except Exception:
            pass
        return None
    def _extract_message_card_payload(self, message_1: Any) -> Dict[str, Any]:
        """提取消息卡片 JSON 载荷。"""
        if not isinstance(message_1, dict):
            return {}

        try:
            message_6 = message_1.get('6', {})
            if not isinstance(message_6, dict):
                return {}
            message_6_3 = message_6.get('3', {})
            if not isinstance(message_6_3, dict):
                return {}
            payload = message_6_3.get('5', '')
            return self._load_json_dict(payload)
        except Exception:
            return {}
    def _extract_message_button_text(self, message_1: Any) -> str:
        """提取消息卡片按钮文本。"""
        payload = self._extract_message_card_payload(message_1)
        try:
            return str(
                payload.get('dxCard', {})
                .get('item', {})
                .get('main', {})
                .get('exContent', {})
                .get('button', {})
                .get('text', '')
            ).strip()
        except Exception:
            return ''
    def _extract_message_card_title(self, message_1: Any) -> str:
        """提取消息卡片标题。"""
        payload = self._extract_message_card_payload(message_1)
        try:
            return str(
                payload.get('dxCard', {})
                .get('item', {})
                .get('main', {})
                .get('exContent', {})
                .get('title', '')
            ).strip()
        except Exception:
            return ''
    def _classify_message_route(self, *, message: dict, message_1: dict, message_10: dict,
                                send_message: str) -> Dict[str, Any]:
        """将消息路由到订单状态、系统提示、特殊流程或真人聊天。"""
        message_direction = message_1.get('7', 0) if isinstance(message_1, dict) else 0
        content_type = 0
        try:
            message_6 = message_1.get('6', {}) if isinstance(message_1, dict) else {}
            if isinstance(message_6, dict):
                message_6_3 = message_6.get('3', {})
                if isinstance(message_6_3, dict):
                    content_type = message_6_3.get('4', 0)
        except Exception:
            content_type = 0

        biz_tag_raw = str(message_10.get('bizTag', '') or '').strip()
        biz_tag_dict = self._load_json_dict(biz_tag_raw)
        ext_json_dict = self._load_json_dict(message_10.get('extJson', ''))
        task_name = str(biz_tag_dict.get('taskName') or '').strip()
        update_key = str(ext_json_dict.get('updateKey') or '').strip()
        detail_notice = str(message_10.get('detailNotice', '') or '').strip()
        reminder_content = str(message_10.get('reminderContent', '') or send_message or '').strip()
        reminder_title = str(message_10.get('reminderTitle', '') or '').strip()
        reminder_notice = str(message_10.get('reminderNotice', '') or '').strip()
        red_reminder = ''
        if isinstance(message, dict) and isinstance(message.get('3'), dict):
            red_reminder = str(message.get('3', {}).get('redReminder', '') or '').strip()

        button_text = self._extract_message_button_text(message_1)
        card_title = self._extract_message_card_title(message_1)
        session_type = str(message_10.get('sessionType', '1') or '1').strip()
        is_group_message = session_type == '30'
        is_system_biz = bool(task_name) or 'SECURITY' in biz_tag_raw or 'taskId' in biz_tag_raw
        is_system_message = message_direction == 1 or content_type == 6 or is_system_biz

        texts = []
        for raw_text in (
            send_message,
            reminder_content,
            detail_notice,
            reminder_title,
            reminder_notice,
            red_reminder,
            task_name,
            update_key,
            button_text,
            card_title,
        ):
            normalized_text = str(raw_text or '').strip()
            if normalized_text and normalized_text not in texts:
                texts.append(normalized_text)

        special_flow_messages = {
            '[卡片消息]',
            '快给ta一个评价吧~',
            '快给ta一个评价吧～',
        }
        special_flow_titles = {
            '我已小刀，待刀成',
            '我已小刀,待刀成',
            '我已成功小刀，待发货',
            '我已成功小刀,待发货',
        }

        if send_message in special_flow_messages or card_title in special_flow_titles:
            route = 'special_flow'
            order_status_signal = None
        else:
            order_status_signal = None
            closed_markers = (
                '[你关闭了订单，钱款已原路退返]',
                '交易关闭',
                '订单关闭',
                '钱款已原路退返',
            )
            refund_markers = (
                '退款中',
                '退款成功',
                '退货退款',
                '退款关闭',
            )
            completed_markers = (
                '[买家确认收货，交易成功]',
                '[你已确认收货，交易成功]',
                '买家确认收货',
                '交易成功',
            )
            shipped_markers = (
                '[你已发货]',
                '已发货',
                '等待买家收货',
            )
            pending_ship_markers = (
                '[我已付款，等待你发货]',
                '[已付款，待发货]',
                '我已付款，等待你发货',
                '[记得及时发货]',
                '等待你发货',
                '待发货',
                '去发货',
                '付款完成待发货',
                'TRADE_PAID_DONE_SELLER',
            )
            pending_payment_markers = (
                '[我已拍下，待付款]',
                '买家已拍下，待付款',
                '待付款',
                '等待买家付款',
                '已拍下_未付款',
            )
            system_notice_markers = (
                '闲鱼小红花',
                '温馨提醒',
                '曝光卡',
                '蚂蚁森林',
                '能量可领',
                '创建合约',
                '假客服骗钱',
                '订单即将自动确认收货',
                '宝贝性价比如何，去表个态吧',
                '发来一条消息',
                '发来一条新消息',
                '已送出小红花',
                '已收下',
            )

            def _contains_any(markers) -> bool:
                return any(marker and marker in text for text in texts for marker in markers)

            if _contains_any(closed_markers):
                order_status_signal = 'cancelled'
            elif _contains_any(refund_markers):
                order_status_signal = 'refunding'
            elif _contains_any(completed_markers):
                order_status_signal = 'completed'
            elif _contains_any(shipped_markers):
                order_status_signal = 'shipped'
            elif _contains_any(pending_ship_markers):
                order_status_signal = 'pending_ship'
            elif _contains_any(pending_payment_markers):
                order_status_signal = 'pending_payment'

            if is_system_message and order_status_signal:
                route = 'order_status'
            elif _contains_any(system_notice_markers) and (is_system_message or message_direction != 2):
                route = 'system_notice'
            elif is_system_message:
                route = 'system_notice'
            else:
                route = 'user_chat'

        should_notify = False
        if not is_group_message:
            if route == 'user_chat':
                should_notify = True
            elif route == 'order_status' and order_status_signal in {'pending_ship', 'refunding', 'cancelled'}:
                should_notify = True

        return {
            'route': route,
            'order_status_signal': order_status_signal,
            'should_notify': should_notify,
            'allow_auto_reply': route == 'user_chat',
            'is_system_message': is_system_message,
            'is_group_message': is_group_message,
            'message_direction': message_direction,
            'content_type': content_type,
            'task_name': task_name,
            'button_text': button_text,
            'card_title': card_title,
            'texts': texts,
        }
    async def _handle_simple_message_auto_delivery(self, websocket, order_id: str, item_id: str, 
                                                    user_id: str, chat_id: str, msg_time: str, msg_id: str):
        """处理简化结构消息的自动发货逻辑
        
        专门用于处理简化结构的发货通知消息（message['1']是字符串的情况）
        发货确认统一在 _auto_delivery 内执行，避免重复确认导致漏发
        
        Args:
            websocket: WebSocket连接
            order_id: 订单ID
            item_id: 商品ID
            user_id: 买家用户ID
            chat_id: 聊天ID
            msg_time: 消息时间
            msg_id: 消息ID
        """
        try:
            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🚀 开始处理简化消息自动发货: order_id={order_id}, item_id={item_id}')
            
            # 检查商品是否属于当前账号
            if item_id and item_id != "未知商品":
                try:
                    if not await self._ensure_item_owned_by_current_account(
                        item_id,
                        log_prefix=f'[{msg_time}] 【{self.cookie_id}】[{msg_id}]'
                    ):
                        logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ❌ 商品 {item_id} 不属于当前账号，跳过自动发货')
                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            status='failed',
                            reason='商品不属于当前账号，跳过自动发货',
                            channel='auto'
                        )
                        return
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 商品 {item_id} 归属验证通过')
                except Exception as e:
                    logger.error(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 检查商品归属失败: {self._safe_str(e)}，跳过自动发货')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        status='failed',
                        reason=f'检查商品归属失败: {self._safe_str(e)}',
                        channel='auto'
                    )
                    return

            if self._check_buyer_blacklist_for_action(
                buyer_id=user_id,
                item_id=item_id,
                order_id=order_id,
                action='自动发货',
                channel='auto',
                log_delivery=True,
            ):
                return
            
            # 检查订单是否已发货
            if not self.can_auto_delivery(order_id):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 订单 {order_id} 在冷却期内，跳过发货')
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=user_id,
                    status='skipped',
                    reason='订单在冷却期内，跳过发货',
                    channel='auto'
                )
                return
            
            # 检查延迟锁状态
            lock_key = order_id
            if self.is_lock_held(lock_key):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔒 订单 {lock_key} 延迟锁仍在持有状态，跳过发货')
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=user_id,
                    status='skipped',
                    reason='订单延迟锁持有中，跳过发货',
                    channel='auto'
                )
                return
            
            # 获取订单锁
            order_lock = self._order_locks[lock_key]
            self._lock_usage_times[lock_key] = time.time()
            
            async with order_lock:
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 获取订单锁成功: {lock_key}')
                
                # 再次检查延迟锁和冷却状态
                if self.is_lock_held(lock_key) or not self.can_auto_delivery(order_id):
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 获取锁后检查发现订单已处理，跳过发货')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        status='skipped',
                        reason='获取锁后发现订单已处理，跳过发货',
                        channel='auto'
                    )
                    return

                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 📤 开始执行自动发货内容发送（发送成功后再确认发货）')
                
                # 获取商品标题
                item_title = "待获取商品信息"

                pending_finalize_meta = self._get_pending_delivery_finalization_meta(order_id, 1)
                if pending_finalize_meta:
                    finalize_result = await self._finalize_delivery_after_send(
                        delivery_meta=pending_finalize_meta,
                        order_id=order_id,
                        item_id=item_id
                    )
                    if not finalize_result.get('success'):
                        finalize_error = finalize_result.get('error') or '补完成 finalize 失败'
                        if self._is_platform_confirm_failure_error(finalize_error):
                            self._mark_delivery_pending_platform_confirm(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=user_id,
                                delivery_meta=pending_finalize_meta,
                                confirm_error=finalize_error,
                                expected_quantity=1,
                                context="自动发货补完成收尾时平台确认失败"
                            )
                        else:
                            self._persist_delivery_finalization_state(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=user_id,
                                delivery_meta=pending_finalize_meta,
                                channel='auto',
                                status='sent',
                                last_error=finalize_error
                            )
                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            status='failed',
                            reason=finalize_error if not self._is_platform_confirm_failure_error(finalize_error) else f'卡券已发出，等待补确认: {finalize_error}',
                            channel='auto',
                            rule_meta=pending_finalize_meta
                        )
                        if not self._is_platform_confirm_failure_error(finalize_error):
                            await self.send_delivery_failure_notification(
                                send_user_name="买家",
                                send_user_id=user_id,
                                item_id=item_id,
                                error_message=finalize_error or '检测到已发送记录，但补完成发货收尾失败',
                                chat_id=chat_id,
                                order_id=order_id
                            )
                        return

                    self._persist_delivery_finalization_state(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        delivery_meta=pending_finalize_meta,
                        channel='auto',
                        status='finalized'
                    )
                    self._sync_order_delivery_progress(
                        order_id=order_id,
                        cookie_id=self.cookie_id,
                        expected_quantity=1,
                        context="自动发货补完成收尾成功"
                    )
                    self._activate_delivery_lock(lock_key, delay_minutes=10)
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        status='success',
                        reason='检测到发货消息已发送，本次补完成收尾成功',
                        channel='auto',
                        rule_meta=pending_finalize_meta
                    )
                    await self.send_delivery_failure_notification(
                        send_user_name="买家",
                        send_user_id=user_id,
                        item_id=item_id,
                        error_message="发货成功",
                        chat_id=chat_id,
                        order_id=order_id
                    )
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 简化消息自动发货补完成收尾成功')
                    return
                
                # 调用自动发货方法获取发货内容
                delivery_result = await self._auto_delivery(
                    item_id, item_title, order_id, user_id, chat_id, include_meta=True
                )
                if isinstance(delivery_result, dict):
                    delivery_content = delivery_result.get('content')
                    delivery_error = delivery_result.get('error')
                    delivery_steps = delivery_result.get('delivery_steps') or []
                    delivery_rule_meta = {
                        'rule_id': delivery_result.get('rule_id'),
                        'rule_keyword': delivery_result.get('rule_keyword'),
                        'card_type': delivery_result.get('card_type'),
                        'match_mode': delivery_result.get('match_mode'),
                        'order_spec_mode': delivery_result.get('order_spec_mode'),
                        'rule_spec_mode': delivery_result.get('rule_spec_mode'),
                        'item_config_mode': delivery_result.get('item_config_mode'),
                        'card_id': delivery_result.get('card_id'),
                        'card_description': delivery_result.get('card_description'),
                        'data_card_pending_consume': delivery_result.get('data_card_pending_consume'),
                        'data_line': delivery_result.get('data_line'),
                        'data_reservation_id': delivery_result.get('data_reservation_id'),
                        'data_reservation_status': delivery_result.get('data_reservation_status'),
                        'delivery_unit_index': delivery_result.get('delivery_unit_index')
                    }
                else:
                    delivery_content = delivery_result
                    delivery_error = None
                    delivery_steps = []
                    delivery_rule_meta = {}

                if delivery_content:
                    delivery_rule_meta.setdefault('success', True)
                    if not delivery_steps:
                        delivery_steps = self._build_delivery_steps(
                            delivery_content,
                            delivery_rule_meta.get('card_description', '')
                        )

                    # 发送发货内容
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    
                    try:
                        await self._send_delivery_steps(
                            websocket,
                            chat_id,
                            user_id,
                            delivery_steps,
                            user_url=user_url,
                            log_prefix=f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 自动发货'
                        )

                        if not self._mark_data_reservation_sent_if_needed(delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta):
                            self._release_data_reservation_if_needed(
                                delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                                error='发送成功后标记预占已发送失败'
                            )
                            raise Exception('批量数据预占标记已发送失败')

                        self._persist_delivery_finalization_state(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            delivery_meta=delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                            channel='auto',
                            status='sent'
                        )

                        finalize_result = await self._finalize_delivery_after_send(
                            delivery_meta=delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                            order_id=order_id,
                            item_id=item_id
                        )
                        if not finalize_result.get('success'):
                            finalize_error = finalize_result.get('error') or '发送成功但提交发货副作用失败'
                            delivery_meta_for_state = delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta
                            if self._is_platform_confirm_failure_error(finalize_error):
                                self._mark_delivery_pending_platform_confirm(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=user_id,
                                    delivery_meta=delivery_meta_for_state,
                                    confirm_error=finalize_error,
                                    expected_quantity=1,
                                    context="自动发货发送成功后平台确认失败"
                                )
                            else:
                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=user_id,
                                    delivery_meta=delivery_meta_for_state,
                                    channel='auto',
                                    status='sent',
                                    last_error=finalize_error
                                )
                            self._record_delivery_log(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=user_id,
                                status='failed',
                                reason=finalize_error if not self._is_platform_confirm_failure_error(finalize_error) else f'卡券已发出，等待补确认: {finalize_error}',
                                channel='auto',
                                rule_meta=delivery_rule_meta
                            )
                            if not self._is_platform_confirm_failure_error(finalize_error):
                                await self.send_delivery_failure_notification(
                                    send_user_name="买家",
                                    send_user_id=user_id,
                                    item_id=item_id,
                                    error_message=finalize_error,
                                    chat_id=chat_id,
                                    order_id=order_id
                                )
                            return

                        self._persist_delivery_finalization_state(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            delivery_meta=delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                            channel='auto',
                            status='finalized'
                        )

                        self._sync_order_delivery_progress(
                            order_id=order_id,
                            cookie_id=self.cookie_id,
                            expected_quantity=1,
                            context="自动发货发送成功"
                        )
                        self._activate_delivery_lock(lock_key, delay_minutes=10)

                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            status='success',
                            reason='自动发货步骤发送成功',
                            channel='auto',
                            rule_meta=delivery_rule_meta
                        )
                    except Exception as send_e:
                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            status='failed',
                            reason=f'自动发货消息发送失败: {self._safe_str(send_e)}',
                            channel='auto',
                            rule_meta=delivery_rule_meta
                        )
                        raise
                    
                    # 发送成功通知
                    await self.send_delivery_failure_notification(
                        send_user_name="买家",
                        send_user_id=user_id,
                        item_id=item_id,
                        error_message="发货成功",
                        chat_id=chat_id,
                        order_id=order_id
                    )
                    
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 简化消息自动发货完成')
                else:
                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ❌ 未找到匹配的发货规则或获取发货内容失败')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        status='failed',
                        reason=delivery_error or '未找到匹配的发货规则或获取发货内容失败',
                        channel='auto',
                        rule_meta=delivery_rule_meta
                    )
                    await self.send_delivery_failure_notification(
                        send_user_name="买家",
                        send_user_id=user_id,
                        item_id=item_id,
                        error_message="未找到匹配的发货规则或获取发货内容失败",
                        chat_id=chat_id,
                        order_id=order_id
                    )

        except Exception as e:
            self._release_data_reservation_if_needed(
                delivery_result if 'delivery_result' in locals() and isinstance(delivery_result, dict) else delivery_rule_meta if 'delivery_rule_meta' in locals() else None,
                error=f'自动发货发送失败: {self._safe_str(e)}'
            )
            self._record_delivery_log(
                order_id=order_id,
                item_id=item_id,
                buyer_id=user_id,
                status='failed',
                reason=f'简化消息自动发货异常: {self._safe_str(e)}',
                channel='auto'
            )
            logger.error(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 简化消息自动发货异常: {self._safe_str(e)}')
            import traceback
            logger.error(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 异常堆栈: {traceback.format_exc()}')
    def _extract_buyer_id_from_message_meta(self, message_meta: dict, *, meta_label: str,
                                            log_prefix: str = "") -> Tuple[Optional[str], Optional[str]]:
        if not isinstance(message_meta, dict):
            return None, None

        biz_tag_dict = self._load_json_dict(message_meta.get('bizTag', ''))
        candidates = [
            ('reminderUrl.peerUserId', self._extract_query_value_from_url(message_meta.get('reminderUrl'), 'peerUserId')),
            ('bizTag.senderId', self._normalize_buyer_id_value(biz_tag_dict.get('senderId') or biz_tag_dict.get('sender_id'))),
            (f'{meta_label}.senderUserId', self._normalize_buyer_id_value(message_meta.get('senderUserId'))),
        ]

        low_trust_candidates = []
        for source, candidate in candidates:
            if not candidate:
                continue
            if self._is_trustworthy_buyer_id(candidate):
                return candidate, source
            low_trust_candidates.append(f'{source}={candidate}')

        if low_trust_candidates:
            logger.info(
                f"{log_prefix} 👤 检测到低可信买家ID候选，已忽略: {', '.join(low_trust_candidates[:3])}"
            )
        return None, None