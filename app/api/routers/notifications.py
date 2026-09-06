"""Notification channels / messages / templates routes (Strangler Fig P2-B2b).

Mechanically extracted from reply_server.py; behavior-preserving.
Shared models/helpers/state live in app/api/models.py, app/api/common.py and app/api/state.py; reply_server-resident symbols are accessed late-bound (reply_server.X) so runtime rebinds stay visible.
"""

from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from app.api.models import MessageNotificationIn, NotificationChannelIn, NotificationChannelUpdate, NotificationTemplateIn, TestNotificationIn
import db_manager
import reply_server
from utils.notification_dispatcher import SUPPORTED_NOTIFICATION_TEMPLATE_TYPES

def create_notifications_router() -> APIRouter:
    router = APIRouter()
    @router.get('/notification-channels')
    def get_notification_channels(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取所有通知渠道"""
        try:
            user_id = current_user['user_id']
            return db_manager.db_manager.get_notification_channels(user_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post('/notification-channels')
    def create_notification_channel(channel_data: NotificationChannelIn, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """创建通知渠道"""
        try:
            user_id = current_user['user_id']
            channel_id = db_manager.db_manager.create_notification_channel(
                channel_data.name,
                channel_data.type,
                channel_data.config,
                user_id
            )
            return {'msg': 'notification channel created', 'id': channel_id}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get('/notification-channels/{channel_id}')
    def get_notification_channel(channel_id: int, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取指定通知渠道"""
        try:
            user_id = current_user['user_id']
            channel = db_manager.db_manager.get_notification_channel(channel_id, user_id=user_id)
            if not channel:
                raise HTTPException(status_code=404, detail='通知渠道不存在')
            return channel
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put('/notification-channels/{channel_id}')
    def update_notification_channel(channel_id: int, channel_data: NotificationChannelUpdate, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新通知渠道"""
        try:
            user_id = current_user['user_id']
            success = db_manager.db_manager.update_notification_channel(
                channel_id,
                channel_data.name,
                channel_data.config,
                channel_data.enabled,
                user_id=user_id
            )
            if success:
                return {'msg': 'notification channel updated'}
            else:
                raise HTTPException(status_code=404, detail='通知渠道不存在')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.delete('/notification-channels/{channel_id}')
    def delete_notification_channel(channel_id: int, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """删除通知渠道"""
        try:
            user_id = current_user['user_id']
            success = db_manager.db_manager.delete_notification_channel(channel_id, user_id=user_id)
            if success:
                return {'msg': 'notification channel deleted'}
            else:
                raise HTTPException(status_code=404, detail='通知渠道不存在')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/message-notifications')
    def get_all_message_notifications(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取当前用户所有账号的消息通知配置"""
        try:
            # 只返回当前用户的消息通知配置
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            all_notifications = db_manager.db_manager.get_all_message_notifications()
            # 过滤只属于当前用户的通知配置
            user_notifications = {cid: notifications for cid, notifications in all_notifications.items() if cid in user_cookies}
            return user_notifications
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/message-notifications/{cid}')
    def get_account_notifications(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取指定账号的消息通知配置"""
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限访问该Cookie")

            return db_manager.db_manager.get_account_notifications(cid)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post('/message-notifications/{cid}')
    def set_message_notification(cid: str, notification_data: MessageNotificationIn, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """设置账号的消息通知"""
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 检查通知渠道是否存在
            channel = db_manager.db_manager.get_notification_channel(notification_data.channel_id, user_id=user_id)
            if not channel:
                raise HTTPException(status_code=404, detail='通知渠道不存在')

            success = db_manager.db_manager.set_message_notification(cid, notification_data.channel_id, notification_data.enabled)
            if success:
                return {'msg': 'message notification set'}
            else:
                raise HTTPException(status_code=400, detail='设置失败')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete('/message-notifications/account/{cid}')
    def delete_account_notifications(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """删除账号的所有消息通知配置"""
        try:
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)
            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            success = db_manager.db_manager.delete_account_notifications(cid, user_id=user_id)
            if success:
                return {'msg': 'account notifications deleted'}
            else:
                raise HTTPException(status_code=404, detail='账号通知配置不存在')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete('/message-notifications/{notification_id}')
    def delete_message_notification(notification_id: int, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """删除消息通知配置"""
        try:
            user_id = current_user['user_id']
            success = db_manager.db_manager.delete_message_notification(notification_id, user_id=user_id)
            if success:
                return {'msg': 'message notification deleted'}
            else:
                raise HTTPException(status_code=404, detail='通知配置不存在')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/notification-templates')
    def get_notification_templates(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取所有通知模板"""
        try:
            templates = db_manager.db_manager.get_all_notification_templates()
            return {'templates': templates}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post('/notification-templates/test')
    async def test_notification_template(data: TestNotificationIn, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """发送测试通知"""
        import time as time_module
        import aiohttp

        try:
            if data.template_type not in SUPPORTED_NOTIFICATION_TEMPLATE_TYPES:
                raise HTTPException(status_code=400, detail='无效的模板类型')

            # 获取所有已启用的通知渠道
            channels = db_manager.db_manager.get_notification_channels(current_user['user_id'])
            logger.info(f"获取到的通知渠道: {channels}")
            enabled_channels = [c for c in channels if c.get('enabled', False)]
            logger.info(f"已启用的通知渠道: {enabled_channels}")

            if not enabled_channels:
                raise HTTPException(status_code=400, detail='没有已启用的通知渠道，请先在「通知渠道」页面配置')

            # 准备测试数据
            test_data = {
                'message': {
                    'account_id': '测试账号',
                    'buyer_name': '测试买家',
                    'buyer_id': '123456789',
                    'item_id': '987654321',
                    'chat_id': 'test_chat_001',
                    'message': '这是一条测试消息',
                    'time': time_module.strftime('%Y-%m-%d %H:%M:%S')
                },
                'token_refresh': {
                    'account_id': '测试账号',
                    'time': time_module.strftime('%Y-%m-%d %H:%M:%S'),
                    'error_message': '这是一条测试异常信息',
                    'verification_url': 'https://example.com/verify'
                },
                'delivery': {
                    'account_id': '测试账号',
                    'buyer_name': '测试买家',
                    'buyer_id': '234567890',
                    'item_id': '876543210',
                    'chat_id': 'test_chat_002',
                    'result': '测试发货成功',
                    'time': time_module.strftime('%Y-%m-%d %H:%M:%S')
                },
                'slider_success': {
                    'account_id': '测试账号',
                    'time': time_module.strftime('%Y-%m-%d %H:%M:%S'),
                    'status_text': 'cookies已自动更新到数据库'
                },
                'face_verify': {
                    'account_id': '测试账号',
                    'time': time_module.strftime('%Y-%m-%d %H:%M:%S'),
                    'verification_action': '请点击验证链接完成验证:',
                    'verification_url': 'https://passport.goofish.com/mini_login.htm?example=test',
                    'verification_type': '身份验证'
                },
                'password_login_success': {
                    'account_id': '测试账号',
                    'time': time_module.strftime('%Y-%m-%d %H:%M:%S'),
                    'cookie_count': '30'
                },
                'cookie_refresh_success': {
                    'account_id': '测试账号',
                    'time': time_module.strftime('%Y-%m-%d %H:%M:%S'),
                    'cookie_count': '30'
                },
                'account_paused': {
                    'account_id': '测试账号',
                    'status_note': '待二维码验证',
                    'pause_reason': '二维码验证',
                    'time': time_module.strftime('%Y-%m-%d %H:%M:%S'),
                    'error_message': '检测到需要人工完成的二维码验证',
                    'verification_url': 'https://passport.goofish.com/mini_login.htm?example=test',
                    'action_hint': '请先完成验证，再恢复账号运行。'
                }
            }

            # 格式化模板
            template = data.template
            for key, value in test_data.get(data.template_type, {}).items():
                template = template.replace(f'{{{key}}}', str(value))

            # 发送测试通知到所有已启用的渠道
            success_channels = []
            failed_channels = []

            for channel in enabled_channels:
                channel_type = channel.get('type', '')
                channel_name = channel.get('name', channel_type)
                config_str = channel.get('config', '{}')
                logger.info(f"处理通知渠道: name={channel_name}, type={channel_type}, config={config_str}")

                try:
                    import json
                    config_data = json.loads(config_str) if isinstance(config_str, str) else config_str
                    logger.info(f"解析后的配置: {config_data}")

                    # 根据渠道类型发送通知
                    if channel_type == 'feishu' or channel_type == 'lark':
                        webhook_url = config_data.get('webhook_url', '')
                        secret = config_data.get('secret', '')
                        logger.info(f"飞书渠道配置: webhook_url={webhook_url}, has_secret={bool(secret)}")
                        if webhook_url:
                            import hmac
                            import hashlib
                            import base64

                            # 生成签名（按照实际发送逻辑）
                            timestamp = str(int(time_module.time()))
                            sign = ""

                            if secret:
                                string_to_sign = f'{timestamp}\n{secret}'
                                hmac_code = hmac.new(
                                    string_to_sign.encode('utf-8'),
                                    ''.encode('utf-8'),
                                    digestmod=hashlib.sha256
                                ).digest()
                                sign = base64.b64encode(hmac_code).decode('utf-8')
                                logger.info(f"飞书签名: timestamp={timestamp}")

                            # 构建请求数据
                            payload = {
                                "msg_type": "text",
                                "content": {
                                    "text": f"【测试通知】\n\n{template}"
                                },
                                "timestamp": timestamp
                            }

                            if sign:
                                payload["sign"] = sign

                            logger.info(f"发送飞书通知: {payload}")
                            timeout = aiohttp.ClientTimeout(total=10)
                            async with aiohttp.ClientSession(timeout=timeout) as session:
                                async with session.post(webhook_url, json=payload) as resp:
                                    resp_text = await resp.text()
                                    logger.info(f"飞书响应: status={resp.status}, body={resp_text}")
                                    if resp.status == 200:
                                        try:
                                            resp_json = json.loads(resp_text)
                                            if resp_json.get('code', 0) == 0:
                                                success_channels.append(channel_name)
                                            else:
                                                failed_channels.append(f"{channel_name} ({resp_json.get('msg', resp_text[:50])})")
                                        except:
                                            success_channels.append(channel_name)
                                    else:
                                        failed_channels.append(f"{channel_name} (HTTP {resp.status}: {resp_text[:50]})")
                        else:
                            failed_channels.append(f"{channel_name} (未配置webhook_url)")

                    elif channel_type == 'dingtalk' or channel_type == 'ding_talk':
                        webhook_url = config_data.get('webhook_url', '')
                        if webhook_url:
                            payload = {
                                "msgtype": "text",
                                "text": {
                                    "content": f"【测试通知】\n\n{template}"
                                }
                            }
                            timeout = aiohttp.ClientTimeout(total=10)
                            async with aiohttp.ClientSession(timeout=timeout) as session:
                                async with session.post(webhook_url, json=payload) as resp:
                                    resp_text = await resp.text()
                                    logger.info(f"钉钉响应: status={resp.status}, body={resp_text}")
                                    if resp.status == 200:
                                        success_channels.append(channel_name)
                                    else:
                                        failed_channels.append(f"{channel_name} (HTTP {resp.status})")

                    elif channel_type == 'bark':
                        server_url = config_data.get('server_url', 'https://api.day.app')
                        device_key = config_data.get('device_key', '')
                        if device_key:
                            import urllib.parse
                            encoded_template = urllib.parse.quote(template)
                            url = f"{server_url}/{device_key}/测试通知/{encoded_template}"
                            timeout = aiohttp.ClientTimeout(total=10)
                            async with aiohttp.ClientSession(timeout=timeout) as session:
                                async with session.get(url) as resp:
                                    if resp.status == 200:
                                        success_channels.append(channel_name)
                                    else:
                                        failed_channels.append(f"{channel_name} (HTTP {resp.status})")

                    elif channel_type == 'telegram':
                        bot_token = config_data.get('bot_token', '')
                        chat_id = config_data.get('chat_id', '')
                        if bot_token and chat_id:
                            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                            payload = {
                                "chat_id": chat_id,
                                "text": f"【测试通知】\n\n{template}"
                            }
                            timeout = aiohttp.ClientTimeout(total=10)
                            async with aiohttp.ClientSession(timeout=timeout) as session:
                                async with session.post(url, json=payload) as resp:
                                    if resp.status == 200:
                                        success_channels.append(channel_name)
                                    else:
                                        failed_channels.append(f"{channel_name} (HTTP {resp.status})")

                    elif channel_type == 'webhook':
                        webhook_url = config_data.get('webhook_url', '')
                        if webhook_url:
                            payload = {
                                "title": "测试通知",
                                "content": template,
                                "type": data.template_type
                            }
                            timeout = aiohttp.ClientTimeout(total=10)
                            async with aiohttp.ClientSession(timeout=timeout) as session:
                                async with session.post(webhook_url, json=payload) as resp:
                                    if resp.status == 200:
                                        success_channels.append(channel_name)
                                    else:
                                        failed_channels.append(f"{channel_name} (HTTP {resp.status})")

                    elif channel_type == 'email':
                        failed_channels.append(f"{channel_name} (邮件测试暂不支持)")

                    else:
                        failed_channels.append(f"{channel_name} (不支持的渠道类型)")

                except Exception as e:
                    logger.error(f"渠道 {channel_name} 发送失败: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    failed_channels.append(f"{channel_name} ({str(e)})")

            # 返回结果
            if success_channels:
                return {
                    'success': True,
                    'message': f'测试通知发送成功: {", ".join(success_channels)}',
                    'success_channels': success_channels,
                    'failed_channels': failed_channels
                }
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f'所有渠道发送失败: {", ".join(failed_channels)}'
                )

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/notification-templates/{template_type}')
    def get_notification_template(template_type: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取指定类型的通知模板"""
        try:
            if template_type not in SUPPORTED_NOTIFICATION_TEMPLATE_TYPES:
                raise HTTPException(status_code=400, detail='无效的模板类型')

            template = db_manager.db_manager.get_notification_template(template_type)
            if template:
                return template
            else:
                # 返回默认模板
                default_template = db_manager.db_manager.get_default_notification_template(template_type)
                return {
                    'type': template_type,
                    'template': default_template,
                    'is_default': True
                }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put('/notification-templates/{template_type}')
    def update_notification_template(template_type: str, data: NotificationTemplateIn, current_user: Dict[str, Any] = Depends(reply_server.require_admin)):
        """更新通知模板"""
        try:
            if template_type not in SUPPORTED_NOTIFICATION_TEMPLATE_TYPES:
                raise HTTPException(status_code=400, detail='无效的模板类型')

            # 如果模板不存在，先插入默认值
            existing = db_manager.db_manager.get_notification_template(template_type)
            if not existing:
                cursor = db_manager.db_manager.conn.cursor()
                default_template = db_manager.db_manager.get_default_notification_template(template_type)
                cursor.execute(
                    'INSERT INTO notification_templates (type, template) VALUES (?, ?)',
                    (template_type, default_template)
                )
                db_manager.db_manager.conn.commit()

            success = db_manager.db_manager.update_notification_template(template_type, data.template)
            if success:
                return {'msg': 'notification template updated'}
            else:
                raise HTTPException(status_code=400, detail='更新失败')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post('/notification-templates/{template_type}/reset')
    def reset_notification_template(template_type: str, current_user: Dict[str, Any] = Depends(reply_server.require_admin)):
        """重置通知模板为默认值"""
        try:
            if template_type not in SUPPORTED_NOTIFICATION_TEMPLATE_TYPES:
                raise HTTPException(status_code=400, detail='无效的模板类型')

            success = db_manager.db_manager.reset_notification_template(template_type)
            if success:
                # 返回重置后的模板
                template = db_manager.db_manager.get_notification_template(template_type)
                return {'msg': 'notification template reset', 'template': template}
            else:
                raise HTTPException(status_code=400, detail='重置失败')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/notification-templates/{template_type}/default')
    def get_default_notification_template(template_type: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取默认通知模板"""
        try:
            if template_type not in SUPPORTED_NOTIFICATION_TEMPLATE_TYPES:
                raise HTTPException(status_code=400, detail='无效的模板类型')

            default_template = db_manager.db_manager.get_default_notification_template(template_type)
            if default_template:
                return {'type': template_type, 'template': default_template}
            else:
                raise HTTPException(status_code=404, detail='模板不存在')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return router
