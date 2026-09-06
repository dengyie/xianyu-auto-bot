"""Settings / registration / user-settings routes (Strangler Fig P2-B2).

Mechanically extracted from reply_server.py; behavior-preserving.
Shared models/helpers/state live in app/api/models.py, app/api/common.py and app/api/state.py; reply_server-resident symbols are accessed late-bound (reply_server.X) so runtime rebinds stay visible.
"""

from typing import Any, Dict, Optional
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from app.api.models import LoginInfoSettingUpdate, RegistrationSettingUpdate, SystemSettingIn
from app.api.common import NIGHT_MODE_SYSTEM_SETTING_KEYS, ORDER_SALES_TIME_SQL, _validate_system_setting_value
import db_manager
import reply_server
from utils.time_utils import get_local_now, local_date_to_utc_end_exclusive, local_date_to_utc_start

def create_settings_router() -> APIRouter:
    router = APIRouter()
    @router.get('/system-settings')
    def get_system_settings(current_user: Dict[str, Any] = Depends(reply_server.require_admin)):
        """获取系统设置（排除敏感信息）"""
        try:
            settings = db_manager.db_manager.get_all_system_settings()
            # 移除敏感信息
            if 'admin_password_hash' in settings:
                del settings['admin_password_hash']
            return settings
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put('/system-settings/{key}')
    def update_system_setting(key: str, setting_data: SystemSettingIn, current_user: Dict[str, Any] = Depends(reply_server.require_admin)):
        """更新系统设置"""
        try:
            # 禁止直接修改密码哈希
            if key == 'admin_password_hash':
                raise HTTPException(status_code=400, detail='请使用密码修改接口')

            value = _validate_system_setting_value(key, setting_data.value)

            if key in NIGHT_MODE_SYSTEM_SETTING_KEYS and not current_user.get('is_admin'):
                raise HTTPException(status_code=403, detail='仅管理员可修改夜间风控降频设置')

            success = db_manager.db_manager.set_system_setting(key, value, setting_data.description)
            if success:
                return {'msg': 'system setting updated'}
            else:
                raise HTTPException(status_code=400, detail='更新失败')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/registration-status')
    def get_registration_status():
        """获取注册开关状态（公开接口，无需认证）"""
        try:
            enabled_str = db_manager.db_manager.get_system_setting('registration_enabled')
            logger.info(f"从数据库获取的注册设置值: '{enabled_str}'")  # 调试信息

            # 如果设置不存在，默认为开启
            if enabled_str is None:
                enabled_bool = True
                message = '注册功能已开启'
            else:
                enabled_bool = enabled_str == 'true'
                message = '注册功能已开启' if enabled_bool else '注册功能已关闭'

            logger.info(f"解析后的注册状态: enabled={enabled_bool}, message='{message}'")  # 调试信息

            return {
                'enabled': enabled_bool,
                'message': message
            }
        except Exception as e:
            logger.error(f"获取注册状态失败: {e}")
            return {'enabled': True, 'message': '注册功能已开启'}  # 出错时默认开启

    @router.get('/login-info-status')
    def get_login_info_status():
        """获取默认登录信息显示状态（公开接口，无需认证）"""
        try:
            enabled_str = db_manager.db_manager.get_system_setting('show_default_login_info')
            logger.debug(f"从数据库获取的登录信息显示设置值: '{enabled_str}'")

            # 如果设置不存在，默认为开启
            if enabled_str is None:
                enabled_bool = True
            else:
                enabled_bool = enabled_str == 'true'

            return {"enabled": enabled_bool}
        except Exception as e:
            logger.error(f"获取登录信息显示状态失败: {e}")
            # 出错时默认为开启
            return {"enabled": True}

    @router.put('/registration-settings')
    def update_registration_settings(setting_data: RegistrationSettingUpdate, admin_user: Dict[str, Any] = Depends(reply_server.require_admin)):
        """更新注册开关设置（仅管理员）"""
        try:
            enabled = setting_data.enabled
            success = db_manager.db_manager.set_system_setting(
                'registration_enabled',
                'true' if enabled else 'false',
                '是否开启用户注册'
            )
            if success:
                reply_server.log_with_user('info', f"更新注册设置: {'开启' if enabled else '关闭'}", admin_user)
                return {
                    'success': True,
                    'enabled': enabled,
                    'message': f"注册功能已{'开启' if enabled else '关闭'}"
                }
            else:
                raise HTTPException(status_code=500, detail='更新注册设置失败')
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新注册设置失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put('/login-info-settings')
    def update_login_info_settings(setting_data: LoginInfoSettingUpdate, admin_user: Dict[str, Any] = Depends(reply_server.require_admin)):
        """更新默认登录信息显示设置（仅管理员）"""
        try:
            enabled = setting_data.enabled
            success = db_manager.db_manager.set_system_setting(
                'show_default_login_info',
                'true' if enabled else 'false',
                '是否显示默认登录信息'
            )
            if success:
                reply_server.log_with_user('info', f"更新登录信息显示设置: {'开启' if enabled else '关闭'}", admin_user)
                return {
                    'success': True,
                    'enabled': enabled,
                    'message': f"默认登录信息显示已{'开启' if enabled else '关闭'}"
                }
            else:
                raise HTTPException(status_code=500, detail='更新登录信息显示设置失败')
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新登录信息显示设置失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/login-captcha-settings')
    def get_login_captcha_settings(admin_user: Dict[str, Any] = Depends(reply_server.require_admin)):
        """获取登录验证码设置（仅管理员）"""
        try:
            enabled_str = db_manager.db_manager.get_system_setting('login_captcha_enabled')
            logger.debug(f"从数据库获取的登录验证码设置值: '{enabled_str}'")

            # 如果设置不存在，默认为开启
            if enabled_str is None:
                enabled_bool = True
            else:
                enabled_bool = enabled_str == 'true'

            return {"enabled": enabled_bool}
        except Exception as e:
            logger.error(f"获取登录验证码设置失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put('/login-captcha-settings')
    def update_login_captcha_settings(setting_data: LoginInfoSettingUpdate, admin_user: Dict[str, Any] = Depends(reply_server.require_admin)):
        """更新登录验证码设置（仅管理员）"""
        try:
            enabled = setting_data.enabled
            success = db_manager.db_manager.set_system_setting(
                'login_captcha_enabled',
                'true' if enabled else 'false',
                '是否开启登录验证码'
            )
            if success:
                reply_server.log_with_user('info', f"更新登录验证码设置: {'开启' if enabled else '关闭'}", admin_user)
                return {
                    'success': True,
                    'enabled': enabled,
                    'message': f"登录验证码已{'开启' if enabled else '关闭'}"
                }
            else:
                raise HTTPException(status_code=500, detail='更新登录验证码设置失败')
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新登录验证码设置失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/api/login-captcha-enabled')
    def get_login_captcha_enabled():
        """获取登录验证码是否启用（公开接口，供登录页面判断）"""
        try:
            enabled_str = db_manager.db_manager.get_system_setting('login_captcha_enabled')
            enabled_bool = enabled_str == 'true' if enabled_str is not None else True
            return {"enabled": enabled_bool}
        except Exception as e:
            logger.error(f"获取登录验证码设置失败: {e}")
            return {"enabled": True}  # 出错时默认开启验证码

    @router.get('/user-settings')
    def get_user_settings(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取当前用户的设置"""
        try:
            user_id = current_user['user_id']
            settings = db_manager.db_manager.get_user_settings(user_id)
            return settings
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put('/user-settings/{key}')
    def update_user_setting(key: str, setting_data: dict, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新用户设置"""
        try:
            user_id = current_user['user_id']
            value = setting_data.get('value')
            description = setting_data.get('description', '')

            reply_server.log_with_user('info', f"更新用户设置: {key} = {value}", current_user)

            success = db_manager.db_manager.set_user_setting(user_id, key, value, description)
            if success:
                reply_server.log_with_user('info', f"用户设置更新成功: {key}", current_user)
                return {'msg': 'setting updated', 'key': key, 'value': value}
            else:
                reply_server.log_with_user('error', f"用户设置更新失败: {key}", current_user)
                raise HTTPException(status_code=400, detail='更新失败')
        except HTTPException:
            raise
        except Exception as e:
            reply_server.log_with_user('error', f"更新用户设置异常: {key} - {str(e)}", current_user)
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/user-settings/{key}')
    def get_user_setting(key: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取用户特定设置"""
        try:
            user_id = current_user['user_id']
            setting = db_manager.db_manager.get_user_setting(user_id, key)
            if setting:
                return setting
            else:
                raise HTTPException(status_code=404, detail='设置不存在')
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get('/api/sales')
    async def get_sales_data(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_info: Optional[Dict[str, Any]] = Depends(reply_server.verify_token)
    ):
        """
        获取销售额数据
        - start_date: 开始日期 (格式: YYYY-MM-DD)
        - end_date: 结束日期 (格式: YYYY-MM-DD)
        """
        try:

            current_user_id = (user_info or {}).get('user_id')
            if current_user_id is None:
                raise HTTPException(status_code=401, detail='未登录或登录已过期')

            user_cookies = db_manager.db_manager.get_all_cookies(current_user_id)
            cookie_ids = list(user_cookies.keys())
            if not cookie_ids:
                return {
                    'success': True,
                    'data': {
                        'sales': [],
                        'total': 0.0,
                        'count': 0
                    },
                    'message': '获取销售额数据成功'
                }
        
            # 构建查询
            placeholders = ','.join(['?'] * len(cookie_ids))
            query = (
                f"SELECT amount, {ORDER_SALES_TIME_SQL} AS effective_sales_at, order_status "
                f"FROM orders WHERE cookie_id IN ({placeholders})"
            )
            params = list(cookie_ids)
        
            if start_date:
                utc_start = local_date_to_utc_start(start_date)
                if not utc_start:
                    raise HTTPException(status_code=400, detail='开始日期格式错误，应为 YYYY-MM-DD')
                query += f" AND {ORDER_SALES_TIME_SQL} >= ?"
                params.append(utc_start)
            if end_date:
                utc_end_exclusive = local_date_to_utc_end_exclusive(end_date)
                if not utc_end_exclusive:
                    raise HTTPException(status_code=400, detail='结束日期格式错误，应为 YYYY-MM-DD')
                query += f" AND {ORDER_SALES_TIME_SQL} < ?"
                params.append(utc_end_exclusive)
        
            # 执行查询
            orders = db_manager.db_manager.execute_query(query, params)
        
            # 处理数据
            sales_by_date = {}
            total_sales = 0.0
            valid_count = 0
            skipped_invalid_amount = 0
            skipped_ineligible_status = 0

            for order in orders:
                amount_str = order[0]
                effective_sales_at = order[1]
                order_status = order[2]

                if not reply_server.is_sales_eligible_order_status(order_status):
                    skipped_ineligible_status += 1
                    continue

                amount = reply_server.parse_order_amount_value(amount_str)
                if amount is None:
                    skipped_invalid_amount += 1
                    continue

                local_date = reply_server.utc_timestamp_to_local_date_string(effective_sales_at)
                if not local_date:
                    continue

                total_sales += amount
                valid_count += 1

                if local_date not in sales_by_date:
                    sales_by_date[local_date] = 0
                sales_by_date[local_date] += amount

            logger.info(
                f"销售额数据统计完成: valid_count={valid_count}, skipped_invalid_amount={skipped_invalid_amount}, "
                f"skipped_ineligible_status={skipped_ineligible_status}"
            )
        
            # 转换为列表格式
            formatted_data = [
                {
                    'date': date,
                    'amount': round(amount, 2)
                }
                for date, amount in sorted(sales_by_date.items())
            ]
        
            return {
                'success': True,
                'data': {
                    'sales': formatted_data,
                    'total': round(total_sales, 2),
                    'count': valid_count
                },
                'message': '获取销售额数据成功'
            }
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取销售额数据失败: {e}")
            return {
                'success': False,
                'data': None,
                'message': f'获取销售额数据失败: {str(e)}'
            }

    @router.get('/api/sales/summary')
    async def get_sales_summary(
        user_info: Optional[Dict[str, Any]] = Depends(reply_server.verify_token)
    ):
        """
        获取当日、本周和本月销售额摘要
        """
        try:

            current_user_id = (user_info or {}).get('user_id')
            if current_user_id is None:
                raise HTTPException(status_code=401, detail='未登录或登录已过期')

            user_cookies = db_manager.db_manager.get_all_cookies(current_user_id)
            cookie_ids = list(user_cookies.keys())
            if not cookie_ids:
                now = get_local_now()
                return {
                    'success': True,
                    'data': {
                        'today_sales': 0.0,
                        'week_sales': 0.0,
                        'month_sales': 0.0,
                        'update_time': now.strftime('%Y-%m-%d %H:%M:%S')
                    },
                    'message': '获取销售额摘要成功'
                }
        
            # 计算时间范围
            now = get_local_now()
        
            # 当日开始
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_str = today_start.strftime('%Y-%m-%d')
        
            # 本周开始（周一）
            week_start = today_start - timedelta(days=today_start.weekday())
            week_start_str = week_start.strftime('%Y-%m-%d')
        
            # 本月开始
            month_start = today_start.replace(day=1)
            month_start_str = month_start.strftime('%Y-%m-%d')
        
            # 单次查询获取所有数据，减少数据库访问
            placeholders = ','.join(['?'] * len(cookie_ids))
            month_start_utc = local_date_to_utc_start(month_start_str)
            query = (
                f"SELECT amount, {ORDER_SALES_TIME_SQL} AS effective_sales_at, order_status "
                f"FROM orders WHERE {ORDER_SALES_TIME_SQL} >= ? AND cookie_id IN ({placeholders})"
            )
            all_orders = db_manager.db_manager.execute_query(query, [month_start_utc] + cookie_ids)

            # 计算销售额
            today_sales = 0.0
            week_sales = 0.0
            month_sales = 0.0
            skipped_invalid_amount = 0
            skipped_ineligible_status = 0

            for order in all_orders:
                amount_str = order[0]
                effective_sales_at = order[1]
                order_status = order[2]

                if not reply_server.is_sales_eligible_order_status(order_status):
                    skipped_ineligible_status += 1
                    continue

                amount = reply_server.parse_order_amount_value(amount_str)
                if amount is None:
                    skipped_invalid_amount += 1
                    continue

                local_effective_sales_at = reply_server.utc_timestamp_to_local_datetime(effective_sales_at)
                if not local_effective_sales_at:
                    continue

                if local_effective_sales_at >= month_start:
                    month_sales += amount

                if local_effective_sales_at >= week_start:
                    week_sales += amount

                if local_effective_sales_at >= today_start:
                    today_sales += amount

            logger.info(
                f"销售额摘要统计完成: skipped_invalid_amount={skipped_invalid_amount}, "
                f"skipped_ineligible_status={skipped_ineligible_status}"
            )
        
            today_sales = round(today_sales, 2)
            week_sales = round(week_sales, 2)
            month_sales = round(month_sales, 2)
        
            return {
                'success': True,
                'data': {
                    'today_sales': today_sales,
                    'week_sales': week_sales,
                    'month_sales': month_sales,
                    'update_time': now.strftime('%Y-%m-%d %H:%M:%S')
                },
                'message': '获取销售额摘要成功'
            }
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取销售额摘要失败: {e}")
            return {
                'success': False,
                'data': None,
                'message': f'获取销售额摘要失败: {str(e)}'
            }

    return router
