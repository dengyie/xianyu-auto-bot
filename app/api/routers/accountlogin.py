"""QR/password/manual-cookie face-verification login routes (Strangler Fig P2-B3).

Mechanically extracted from reply_server.py; behavior-preserving.
External (reply_server) symbols resolve via ctx at request time - see app/api/state.py.
"""

from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio
import base64
import hashlib
import io
import json
import os
import random
import re
import secrets
import time
import urllib.parse
from urllib.parse import unquote
from urllib import request as urllib_request, error as urllib_error

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, Header,
                     HTTPException, Request, Response, UploadFile, status)
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from pydantic import BaseModel


def create_account_login_router(ctx) -> APIRouter:
    router = APIRouter()
    @router.post("/manual-cookie-import")
    async def manual_cookie_import(
        request: ctx.ManualCookieImportRequest,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user),
    ):
        """手动导入 Cookie，并按单次调试链路执行真实浏览器滑块验证。"""
        try:
            account_id = str(request.account_id or '').strip()
            cookie_value = str(request.cookie or '').replace('\ufeff', '').strip()
            show_browser = bool(request.show_browser)
            user_id = current_user['user_id']

            if not account_id or not cookie_value:
                return {'success': False, 'message': '账号ID和Cookie不能为空'}

            existing_cookies = ctx.db_manager.get_all_cookies()
            if account_id in existing_cookies:
                user_cookies = ctx.db_manager.get_all_cookies(user_id)
                if account_id not in user_cookies:
                    return {'success': False, 'message': '该账号ID已被其他用户使用'}

            session_id = secrets.token_urlsafe(16)
            ctx.manual_cookie_import_sessions[session_id] = {
                'account_id': account_id,
                'show_browser': show_browser,
                'status': 'processing',
                'verification_url': None,
                'screenshot_path': None,
                'verification_type': None,
                'slider_instance': None,
                'task': None,
                'timestamp': time.time(),
                'completed_at': None,
                'user_id': user_id,
            }

            task = asyncio.create_task(ctx._execute_manual_cookie_import(
                session_id,
                account_id,
                cookie_value,
                show_browser,
                user_id,
                current_user,
            ))
            ctx.manual_cookie_import_sessions[session_id]['task'] = task

            return {
                'success': True,
                'session_id': session_id,
                'status': 'processing',
                'message': 'Cookie导入验证任务已启动，请等待...',
            }
        except Exception as exc:
            ctx.log_with_user('error', f"手动导入 Cookie 异常: {str(exc)}", current_user)
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'message': f'手动导入 Cookie 失败: {str(exc)}'}

    @router.get("/manual-cookie-import/check/{session_id}")
    async def check_manual_cookie_import_status(
        session_id: str,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user),
    ):
        """检查手动导入 Cookie 的执行状态。"""
        try:
            current_time = time.time()
            expired_sessions = [
                sid for sid, session in ctx.manual_cookie_import_sessions.items()
                if (
                    session.get('completed_at') and current_time - session['completed_at'] > 300
                ) or current_time - session['timestamp'] > 3600
            ]
            for sid in expired_sessions:
                if sid in ctx.manual_cookie_import_sessions:
                    del ctx.manual_cookie_import_sessions[sid]

            if session_id not in ctx.manual_cookie_import_sessions:
                return {'status': 'not_found', 'message': '会话不存在或已过期'}

            session = ctx.manual_cookie_import_sessions[session_id]
            if session['user_id'] != current_user['user_id']:
                return {'status': 'forbidden', 'message': '无权限访问该会话'}

            status = session['status']
            if status == 'verification_required':
                screenshot_path = session.get('screenshot_path')
                verification_url = session.get('verification_url')
                verification_type = session.get('verification_type') or '身份验证'
                return {
                    'status': 'verification_required',
                    'verification_url': verification_url,
                    'screenshot_path': screenshot_path,
                    'verification_type': verification_type,
                    'message': f'需要{verification_type}，请查看验证截图' if screenshot_path else f'需要{verification_type}，请点击验证链接',
                }
            if status == 'success':
                return {
                    'status': 'success',
                    'message': f'账号 {session["account_id"]} Cookie 导入并验证成功',
                    'account_id': session['account_id'],
                    'is_new_account': session.get('is_new_account', False),
                    'cookie_count': session.get('cookie_count', 0),
                }
            if status == 'failed':
                error_msg = session.get('error', 'Cookie 导入验证失败')
                return {
                    'status': 'failed',
                    'message': error_msg,
                    'error': error_msg,
                }
            return {
                'status': 'processing',
                'message': 'Cookie 导入验证处理中，请稍候...',
            }
        except Exception as exc:
            ctx.log_with_user('error', f"检查手动导入 Cookie 状态异常: {str(exc)}", current_user)
            return {'status': 'error', 'message': str(exc)}

    @router.post("/password-login")
    async def password_login(
        request: Dict[str, Any],
        current_user: Dict[str, Any] = Depends(ctx.get_current_user)
    ):
        """账号密码登录接口（异步，支持人脸认证）"""
        try:
            account_id = request.get('account_id')
            account = request.get('account')
            password = request.get('password')
            # 检查前端是否明确指定了 show_browser 参数
            show_browser_specified = 'show_browser' in request
            show_browser = request.get('show_browser', False)
            refresh_mode = request.get('refresh_mode', False)  # 刷新模式：从数据库读取账密
            risk_log_id = None

            user_id = current_user['user_id']

            # 刷新模式：从数据库读取已保存的账号密码
            if refresh_mode and account_id:
                from XianyuAutoAsync import XianyuLive
                cookie_info = ctx.db_manager.get_cookie_details(account_id)
                if not cookie_info:
                    return {'success': False, 'message': f'未找到账号: {account_id}'}

                # 验证账号归属
                if cookie_info.get('user_id') != user_id:
                    return {'success': False, 'message': '无权操作此账号'}

                account = cookie_info.get('username')
                password = cookie_info.get('password')

                if not account or not password:
                    return {'success': False, 'message': '该账号未配置用户名和密码，无法刷新Cookie'}

                # 获取 show_browser 设置（只有当前端没有明确指定时，才使用数据库配置）
                if not show_browser_specified:
                    show_browser = cookie_info.get('show_browser', False)

                ctx.log_with_user('info', f"刷新Cookie模式: {account_id}, 用户名: {account}, show_browser: {show_browser}", current_user)

                if XianyuLive.is_manual_refresh_active(account_id):
                    return {'success': False, 'message': f'账号 {account_id} 正在执行手动刷新，请稍候再试'}

            if not account_id or not account or not password:
                return {'success': False, 'message': '账号ID、登录账号和密码不能为空'}

            ctx.log_with_user('info', f"开始账号密码登录: {account_id}, 账号: {account}", current_user)
        
            # 生成会话ID
            session_id = secrets.token_urlsafe(16)
            risk_session_id = ctx._new_risk_log_session_id('pwd')

            # 记录手动刷新Cookie到风控日志
            risk_log_id = None
            if refresh_mode:
                try:
                    risk_log_id = ctx.db_manager.add_risk_control_log(
                        cookie_id=account_id,
                        event_type='cookie_refresh',
                        session_id=risk_session_id,
                        trigger_scene='manual_password_refresh',
                        result_code='manual_cookie_refresh_started',
                        event_description='手动触发账密Cookie刷新',
                        processing_status='processing',
                        event_meta=ctx._build_risk_event_meta({
                            'account_id': account_id,
                            'show_browser': bool(show_browser),
                            'refresh_mode': True,
                        })
                    )
                except Exception as log_e:
                    risk_log_id = None
                    logger.error(f"记录风控日志失败: {log_e}")
        
            user_id = current_user['user_id']
        
            # 创建登录会话
            ctx.password_login_sessions[session_id] = {
                'account_id': account_id,
                'account': account,
                'show_browser': show_browser,
                'refresh_mode': refresh_mode,  # 保存刷新模式标志
                'risk_control_log_id': risk_log_id if refresh_mode else None,  # 风控日志ID
                'risk_session_id': risk_session_id,
                'status': 'processing',
                'verification_url': None,
                'screenshot_path': None,
                'qr_code_url': None,
                'verification_type': None,
                'slider_instance': None,
                'task': None,
                'timestamp': time.time(),
                'completed_at': None,
                'user_id': user_id
            }
        
            # 启动后台登录任务
            task = asyncio.create_task(ctx._execute_password_login(
                session_id, account_id, account, password, show_browser, user_id, current_user
            ))
            ctx.password_login_sessions[session_id]['task'] = task
        
            return {
                'success': True,
                'session_id': session_id,
                'status': 'processing',
                'message': '登录任务已启动，请等待...'
            }
        
        except Exception as e:
            ctx.log_with_user('error', f"账号密码登录异常: {str(e)}", current_user)
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'message': f'登录失败: {str(e)}'}

    @router.get("/password-login/check/{session_id}")
    async def check_password_login_status(
        session_id: str,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user)
    ):
        """检查账号密码登录状态"""
        try:
            # 清理过期会话（超过1小时）
            current_time = time.time()
            expired_sessions = [
                sid for sid, session in ctx.password_login_sessions.items()
                if (
                    session.get('completed_at') and current_time - session['completed_at'] > 300
                ) or current_time - session['timestamp'] > 3600
            ]
            for sid in expired_sessions:
                expired_session = ctx.password_login_sessions.get(sid)
                if expired_session:
                    expired_screenshot_path = expired_session.get('screenshot_path')
                    if expired_screenshot_path:
                        try:
                            from utils.image_utils import image_manager
                            if ctx.image_manager.delete_image(expired_screenshot_path):
                                ctx.log_with_user('info', f"密码登录会话过期，已删除验证截图: {expired_screenshot_path}", current_user)
                            else:
                                ctx.log_with_user('warning', f"密码登录会话过期，但删除验证截图失败: {expired_screenshot_path}", current_user)
                        except Exception as cleanup_err:
                            ctx.log_with_user('error', f"清理过期密码登录截图时出错: {str(cleanup_err)}", current_user)
                if sid in ctx.password_login_sessions:
                    del ctx.password_login_sessions[sid]
        
            if session_id not in ctx.password_login_sessions:
                return {'status': 'not_found', 'message': '会话不存在或已过期'}
        
            session = ctx.password_login_sessions[session_id]
        
            # 检查用户权限
            if session['user_id'] != current_user['user_id']:
                return {'status': 'forbidden', 'message': '无权限访问该会话'}
        
            status = session['status']
        
            if status == 'verification_required':
                # 需要身份验证
                screenshot_path = session.get('screenshot_path')
                verification_url = session.get('verification_url')
                verification_type = session.get('verification_type') or '身份验证'
                return {
                    'status': 'verification_required',
                    'verification_url': verification_url,
                    'screenshot_path': screenshot_path,
                    'qr_code_url': session.get('qr_code_url'),  # 保留兼容性
                    'verification_type': verification_type,
                    'message': f'需要{verification_type}，请查看验证截图' if screenshot_path else f'需要{verification_type}，请点击验证链接'
                }
            elif status == 'success':
                return {
                    'status': 'success',
                    'message': f'账号 {session["account_id"]} 登录成功',
                    'account_id': session['account_id'],
                    'is_new_account': session.get('is_new_account', False),
                    'cookie_count': session.get('cookie_count', 0)
                }
            elif status == 'failed':
                error_msg = session.get('error', '登录失败')
                ctx.log_with_user('info', f"返回登录失败状态: {session_id}, 错误消息: {error_msg}", current_user)  # 添加日志
                return {
                    'status': 'failed',
                    'message': error_msg,
                    'error': error_msg  # 也包含error字段，确保前端能获取到
                }
            elif status == 'cancelled':
                return {
                    'status': 'cancelled',
                    'message': session.get('error') or '登录已取消'
                }
            else:
                # 处理中
                return {
                    'status': 'processing',
                    'message': '登录处理中，请稍候...'
                }
        
        except Exception as e:
            ctx.log_with_user('error', f"检查账号密码登录状态异常: {str(e)}", current_user)
            return {'status': 'error', 'message': str(e)}

    @router.post("/password-login/cancel/{session_id}")
    async def cancel_password_login(
        session_id: str,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user)
    ):
        """取消账号密码登录/刷新 Cookie 会话，避免前端反复弹出验证窗口。"""
        try:
            session = ctx.password_login_sessions.get(session_id)
            if not session:
                return {'success': False, 'status': 'not_found', 'message': '会话不存在或已过期'}

            if session['user_id'] != current_user['user_id']:
                return {'success': False, 'status': 'forbidden', 'message': '无权限访问该会话'}

            current_status = str(session.get('status') or '').strip().lower()
            if current_status in ctx.PASSWORD_LOGIN_TERMINAL_STATUSES:
                return {
                    'success': True,
                    'status': current_status,
                    'message': session.get('error') or '会话已结束'
                }

            ctx._set_password_login_session_status(session_id, 'cancelled', error='用户取消登录')
            ctx._update_session_risk_log(session_id, 'failed', error_message='用户取消登录')
            ctx._close_password_login_pending_verification_risk_logs(
                session_id,
                'failed',
                error_message='用户取消登录',
                result_code='password_login_cancelled',
            )

            slider_instance = session.get('slider_instance')
            if slider_instance:
                try:
                    slider_instance.close_browser()
                    ctx.log_with_user('info', f"已关闭密码登录浏览器实例: {session_id}", current_user)
                except Exception as close_err:
                    ctx.log_with_user('warning', f"关闭密码登录浏览器实例失败: {session_id}, 错误: {close_err}", current_user)

            return {
                'success': True,
                'status': 'cancelled',
                'message': '登录已取消'
            }
        except Exception as exc:
            ctx.log_with_user('error', f"取消账号密码登录异常: {str(exc)}", current_user)
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'status': 'error', 'message': str(exc)}

    @router.get("/face-verification/screenshot/{account_id}")
    async def get_account_face_verification_screenshot(
        account_id: str,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user)
    ):
        """获取指定账号的人脸验证截图"""
        try:
            import glob
        
            # 检查账号是否属于当前用户
            user_id = current_user['user_id']
            username = current_user['username']
        
            # 如果是管理员，允许访问所有账号
            is_admin = username == 'admin'
        
            if not is_admin:
                cookie_info = ctx.db_manager.get_cookie_details(account_id)
                if not cookie_info:
                    ctx.log_with_user('warning', f"账号 {account_id} 不存在", current_user)
                    return {
                        'success': False,
                        'message': '账号不存在'
                    }
            
                cookie_user_id = cookie_info.get('user_id')
                if cookie_user_id != user_id:
                    ctx.log_with_user('warning', f"用户 {user_id} 尝试访问账号 {account_id}（归属用户: {cookie_user_id}）", current_user)
                    return {
                        'success': False,
                        'message': '无权访问该账号'
                    }

            session_scope_user_id = None if is_admin else user_id
            latest_password_login_session = ctx._get_latest_password_login_session_for_account(
                account_id,
                user_id=session_scope_user_id,
            )
            if latest_password_login_session:
                session_status = str(latest_password_login_session.get('status') or '').strip().lower()
                session_screenshot_path = latest_password_login_session.get('screenshot_path')

                if session_status == 'verification_required' and session_screenshot_path and os.path.exists(session_screenshot_path):
                    screenshot_info = ctx._build_face_verification_screenshot_info(account_id, session_screenshot_path)
                    ctx.log_with_user('info', f"优先返回账号 {account_id} 当前登录会话的验证截图", current_user)
                    return {
                        'success': True,
                        'screenshot': screenshot_info
                    }

                if session_status == 'failed':
                    session_error_message = str(latest_password_login_session.get('error') or '').strip()
                    if ctx._is_password_login_verification_timeout_message(session_error_message):
                        ctx.log_with_user('info', f"账号 {account_id} 最近一次验证已超时，忽略历史截图", current_user)
                        return {
                            'success': False,
                            'message': session_error_message
                        }

            latest_verification_log = ctx._get_latest_verification_risk_log_for_account(account_id)
            if latest_verification_log:
                log_status = str(latest_verification_log.get('processing_status') or '').strip().lower()
                if log_status == 'failed' and ctx._is_timed_out_verification_risk_log(latest_verification_log):
                    timeout_message = (
                        str(latest_verification_log.get('error_message') or '').strip()
                        or '当前验证页面已超时/失效，请重新发起验证'
                    )
                    ctx.log_with_user('info', f"账号 {account_id} 最新验证风控已超时，忽略历史截图", current_user)
                    return {
                        'success': False,
                        'message': timeout_message
                    }
                if log_status == 'success':
                    # 最近一次验证已完成，历史截图仅作留档，不应再当成待处理验证展示
                    ctx.log_with_user('info', f"账号 {account_id} 最新验证已完成，无待处理验证", current_user)
                    return {
                        'success': False,
                        'message': '最近一次验证已完成，当前没有待处理的验证'
                    }

            # 获取该账号的验证截图
            screenshots_dir = os.path.join(ctx.static_dir, 'uploads', 'images')
            pattern_jpg = os.path.join(screenshots_dir, f'face_verify_{account_id}_*.jpg')
            pattern_png = os.path.join(screenshots_dir, f'face_verify_{account_id}_*.png')
            screenshot_files = glob.glob(pattern_jpg) + glob.glob(pattern_png)
            screenshot_files = [file_path for file_path in screenshot_files if os.path.exists(file_path)]
        
            ctx.log_with_user(
                'debug',
                f"查找截图: {pattern_jpg} / {pattern_png}, 找到 {len(screenshot_files)} 个有效文件",
                current_user,
            )
        
            if not screenshot_files:
                ctx.log_with_user('warning', f"账号 {account_id} 没有找到验证截图", current_user)
                return {
                    'success': False,
                    'message': '未找到验证截图'
                }
        
            # 获取最新的截图
            latest_file = max(screenshot_files, key=os.path.getmtime)

            # 新鲜度门槛：若最近一次风控事件（含 slider_captcha 等不产生截图的类型）
            # 比这张截图还新，说明当前问题不是这张截图对应的验证（如滑块被风控硬拒），
            # 旧截图不应再当成待处理验证展示，避免"当前提醒被历史截图覆盖"的误导
            latest_risk_epoch = ctx._get_latest_risk_log_epoch_for_account(account_id)
            freshness_status, freshness_message = ctx._evaluate_screenshot_freshness(latest_file, latest_risk_epoch)
            if freshness_status == 'unavailable':
                ctx.log_with_user('warning', f"账号 {account_id} 截图不可用: {freshness_message}", current_user)
                return {'success': False, 'message': freshness_message}
            if freshness_status == 'stale':
                ctx.log_with_user('info', f"账号 {account_id} 最新风控事件晚于历史截图，判定截图已过期，不展示", current_user)
                return {'success': False, 'message': freshness_message}

            screenshot_info = ctx._build_face_verification_screenshot_info(account_id, latest_file)

            ctx.log_with_user('info', f"获取账号 {account_id} 的验证截图", current_user)

            return {
                'success': True,
                'screenshot': screenshot_info
            }
        
        except Exception as e:
            ctx.log_with_user('error', f"获取验证截图失败: {str(e)}", current_user)
            return {
                'success': False,
                'message': str(e)
            }

    @router.delete("/face-verification/screenshot/{account_id}")
    async def delete_account_face_verification_screenshot(
        account_id: str,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user)
    ):
        """删除指定账号的人脸验证截图"""
        try:
            import glob
        
            # 检查账号是否属于当前用户
            user_id = current_user['user_id']
            cookie_info = ctx.db_manager.get_cookie_details(account_id)
            if not cookie_info or cookie_info.get('user_id') != user_id:
                return {
                    'success': False,
                    'message': '无权访问该账号'
                }
        
            # 删除该账号的所有验证截图
            screenshots_dir = os.path.join(ctx.static_dir, 'uploads', 'images')
            pattern = os.path.join(screenshots_dir, f'face_verify_{account_id}_*.jpg')
            screenshot_files = glob.glob(pattern)
        
            deleted_count = 0
            for file_path in screenshot_files:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        deleted_count += 1
                        ctx.log_with_user('info', f"删除账号 {account_id} 的验证截图: {os.path.basename(file_path)}", current_user)
                except Exception as e:
                    ctx.log_with_user('error', f"删除截图失败 {file_path}: {str(e)}", current_user)
        
            return {
                'success': True,
                'message': f'已删除 {deleted_count} 个验证截图',
                'deleted_count': deleted_count
            }
        
        except Exception as e:
            ctx.log_with_user('error', f"删除验证截图失败: {str(e)}", current_user)
            return {
                'success': False,
                'message': str(e)
            }

    @router.post("/qr-login/generate")
    async def generate_qr_code(current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """生成扫码登录二维码"""
        try:
            ctx.log_with_user('info', "请求生成扫码登录二维码", current_user)

            result = await ctx.qr_login_manager.generate_qr_code(user_id=current_user['user_id'])

            if result['success']:
                ctx.log_with_user('info', f"扫码登录二维码生成成功: {result['session_id']}", current_user)
            else:
                ctx.log_with_user('warning', f"扫码登录二维码生成失败: {result.get('message', '未知错误')}", current_user)

            return result

        except Exception as e:
            ctx.log_with_user('error', f"生成扫码登录二维码异常: {str(e)}", current_user)
            return {'success': False, 'message': f'生成二维码失败: {str(e)}'}

    @router.get("/qr-login/check/{session_id}")
    async def check_qr_code_status(session_id: str, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """检查扫码登录状态"""
        try:
            # 清理过期记录
            await ctx.cleanup_qr_check_records()

            # 检查是否已经处理过
            if session_id in ctx.qr_check_processed:
                record = ctx.qr_check_processed[session_id]
                if record['processed']:
                    ctx.log_with_user('debug', f"扫码登录session {session_id} 已处理过，直接返回", current_user)
                    if record.get('error'):
                        return {'status': 'error', 'message': record['error']}

                    account_info = record.get('account_info')
                    if account_info:
                        handoff_error = ctx._qr_runtime_handoff_error(account_info)
                        if handoff_error:
                            return {
                                'status': 'error',
                                'message': handoff_error,
                                'account_info': account_info,
                                'already_processed': True,
                            }
                        return {
                            'status': 'success',
                            'message': '扫码登录已完成',
                            'account_info': account_info,
                            'already_processed': True,
                        }

                    return {'status': 'already_processed', 'message': '该会话已处理完成'}

            # 获取该session的锁
            session_lock = ctx.qr_check_locks[session_id]

            # 使用非阻塞方式尝试获取锁
            if session_lock.locked():
                ctx.log_with_user('debug', f"扫码登录session {session_id} 正在被其他请求处理，跳过", current_user)
                return {'status': 'processing', 'message': '正在处理中，请稍候...'}

            async with session_lock:
                # 再次检查是否已处理（双重检查）
                if session_id in ctx.qr_check_processed and ctx.qr_check_processed[session_id]['processed']:
                    ctx.log_with_user('debug', f"扫码登录session {session_id} 在获取锁后发现已处理，直接返回", current_user)
                    record = ctx.qr_check_processed[session_id]
                    if record.get('error'):
                        return {'status': 'error', 'message': record['error']}

                    account_info = record.get('account_info')
                    if account_info:
                        handoff_error = ctx._qr_runtime_handoff_error(account_info)
                        if handoff_error:
                            return {
                                'status': 'error',
                                'message': handoff_error,
                                'account_info': account_info,
                                'already_processed': True,
                            }
                        return {
                            'status': 'success',
                            'message': '扫码登录已完成',
                            'account_info': account_info,
                            'already_processed': True,
                        }

                    return {'status': 'already_processed', 'message': '该会话已处理完成'}

                # 清理过期会话
                ctx.qr_login_manager.cleanup_expired_sessions()

                # 获取会话状态
                session = ctx.qr_login_manager.sessions.get(session_id)
                if session and session.user_id is not None and session.user_id != current_user['user_id']:
                    return {'status': 'forbidden', 'message': '无权限访问该会话'}

                status_info = ctx.qr_login_manager.get_session_status(session_id)
                ctx.log_with_user(
                    'info',
                    f"获取会话状态: session={session_id}, status={status_info.get('status')}",
                    current_user,
                )
                if status_info['status'] == 'success':
                    ctx.log_with_user(
                        'info',
                        f"会话已成功: session={session_id}, unb_present={bool(status_info.get('unb'))}",
                        current_user,
                    )

                    # 检查是否已经在后台处理中
                    if session_id in ctx.qr_check_processed and ctx.qr_check_processed[session_id].get('processing'):
                        return {'status': 'confirmed', 'message': '已确认，正在获取Cookie...'}

                    # 标记为处理中，立即返回"已确认"状态（不阻塞前端）
                    ctx.qr_check_processed[session_id] = {
                        'processed': False,
                        'processing': True,
                        'timestamp': time.time()
                    }

                    # 获取 Cookie 信息
                    cookies_info = ctx.qr_login_manager.get_session_cookies(session_id)
                    # 安全：只记录 Cookie key 名，绝不落 value（含 unb/cookie2/_tb_token_ 等劫持素材）
                    if cookies_info:
                        _cookie_keys = sorted((cookies_info.get('cookies') or {}).keys()) if isinstance(cookies_info.get('cookies'), dict) else 'redacted'
                        ctx.log_with_user(
                            'info',
                            f"获取会话Cookie: session={session_id}, unb_present={bool(cookies_info.get('unb'))}, keys={_cookie_keys}",
                            current_user,
                        )
                    else:
                        ctx.log_with_user('info', f"获取会话Cookie: session={session_id}, empty", current_user)

                    if cookies_info:
                        # 异步处理 Cookie（不阻塞当前请求）
                        async def _process_cookies_background():
                            try:
                                account_info = await ctx.process_qr_login_cookies(
                                    cookies_info['cookies'],
                                    cookies_info['unb'],
                                    current_user
                                )
                                ctx.log_with_user('info', f"扫码登录处理完成: {session_id}, 账号: {account_info.get('account_id', 'unknown')}", current_user)
                                ctx.qr_check_processed[session_id] = {
                                    'processed': True,
                                    'processing': False,
                                    'timestamp': time.time(),
                                    'account_info': account_info
                                }
                            except Exception as bg_e:
                                ctx.log_with_user('error', f"后台处理扫码Cookie失败: {bg_e}", current_user)
                                ctx.qr_check_processed[session_id] = {
                                    'processed': True,
                                    'processing': False,
                                    'timestamp': time.time(),
                                    'error': str(bg_e)
                                }

                        asyncio.create_task(_process_cookies_background())

                    # 立即返回"已确认"状态
                    return {'status': 'confirmed', 'message': '已确认，正在获取Cookie...'}

                # 检查后台处理是否已完成
                if session_id in ctx.qr_check_processed:
                    record = ctx.qr_check_processed[session_id]
                    if record.get('processed') and not record.get('processing'):
                        if record.get('error'):
                            return {'status': 'error', 'message': record['error']}
                        account_info = record.get('account_info', {})
                        handoff_error = ctx._qr_runtime_handoff_error(account_info)
                        if handoff_error:
                            return {
                                'status': 'error',
                                'message': handoff_error,
                                'account_info': account_info,
                            }
                        status_info['status'] = 'success'
                        status_info['account_info'] = account_info
                        return status_info
                    elif record.get('processing'):
                        return {'status': 'confirmed', 'message': '已确认，正在获取Cookie...'}

                return status_info

        except Exception as e:
            ctx.log_with_user('error', f"检查扫码登录状态异常: {str(e)}", current_user)
            return {'status': 'error', 'message': str(e)}

    @router.post("/qr-login/submit-cookies/{session_id}")
    async def submit_qr_login_cookies(
        session_id: str,
        request: ctx.QRLoginSubmitCookiesRequest,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user),
    ):
        """用户侧人脸/验证成功后回传 Cookie，以用户成功为准完成扫码登录。

        闲鱼不会回调我们。用户在手机/本机浏览器完成验证后，成功 Cookie
        落在用户侧浏览器；此接口把该 Cookie 写回当前扫码会话并走原有收口。
        """
        try:
            session = ctx.qr_login_manager.sessions.get(session_id)
            if not session:
                return {'success': False, 'status': 'not_found', 'message': '会话不存在或已过期'}
            if session.user_id is not None and session.user_id != current_user['user_id']:
                return {'success': False, 'status': 'forbidden', 'message': '无权限访问该会话'}

            cookie_text = str(request.cookies or '').replace('﻿', '').strip()
            if not cookie_text:
                return {'success': False, 'status': 'invalid', 'message': 'Cookie不能为空'}
            if len(cookie_text) > 200_000:
                return {'success': False, 'status': 'invalid', 'message': 'Cookie过长，请只粘贴闲鱼相关Cookie'}

            # 与 /qr-login/check 共用锁，避免双开 process_qr_login_cookies
            session_lock = ctx.qr_check_locks[session_id]
            async with session_lock:
                apply_result = ctx.qr_login_manager.apply_external_cookies(
                    session_id,
                    cookie_text,
                    source='user',
                )
                if not apply_result.get('success'):
                    ctx.log_with_user(
                        'warning',
                        f"用户侧Cookie提交失败: {session_id}, {apply_result.get('message')}",
                        current_user,
                    )
                    return apply_result

                ctx.log_with_user(
                    'info',
                    f"用户侧Cookie提交成功: {session_id}, unb={apply_result.get('unb')}",
                    current_user,
                )
                return await ctx._finish_qr_login_after_external_success(
                    session_id, apply_result, current_user, '用户侧Cookie'
                )

        except Exception as e:
            ctx.log_with_user('error', f"提交用户侧Cookie异常: {str(e)}", current_user)
            return {'success': False, 'status': 'error', 'message': f'提交失败: {str(e)}'}

    @router.post("/qr-login/submit-url/{session_id}")
    async def submit_qr_login_url(
        session_id: str,
        request: ctx.QRLoginSubmitUrlRequest,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user),
    ):
        """用户粘贴验证成功后的回调/跳转 URL，由服务端在原会话里换 Cookie。

        产品路径：用户只需贴网址，不必手抠 Cookie。服务端解析 token 或
        Playwright 打开该 URL（带当前扫码会话 Cookie）后收口。
        """
        try:
            session = ctx.qr_login_manager.sessions.get(session_id)
            if not session:
                return {'success': False, 'status': 'not_found', 'message': '会话不存在或已过期'}
            if session.user_id is not None and session.user_id != current_user['user_id']:
                return {'success': False, 'status': 'forbidden', 'message': '无权限访问该会话'}

            url_text = str(request.url or '').replace('﻿', '').strip()
            if not url_text:
                return {'success': False, 'status': 'invalid', 'message': '回调URL不能为空'}
            if len(url_text) > 8000:
                return {'success': False, 'status': 'invalid', 'message': 'URL过长'}

            session_lock = ctx.qr_check_locks[session_id]
            async with session_lock:
                apply_result = await ctx.qr_login_manager.apply_external_callback_url(
                    session_id,
                    url_text,
                    source='user_url',
                )
                if not apply_result.get('success'):
                    ctx.log_with_user(
                        'warning',
                        f"用户回调URL提交失败: {session_id}, {apply_result.get('message')}",
                        current_user,
                    )
                    return apply_result

                ctx.log_with_user(
                    'info',
                    f"用户回调URL提交成功: {session_id}, unb={apply_result.get('unb')}, "
                    f"via={apply_result.get('via')}",
                    current_user,
                )
                return await ctx._finish_qr_login_after_external_success(
                    session_id, apply_result, current_user, '回调URL'
                )

        except Exception as e:
            ctx.log_with_user('error', f"提交回调URL异常: {str(e)}", current_user)
            return {'success': False, 'status': 'error', 'message': f'提交失败: {str(e)}'}

    @router.post("/qr-login-lite/generate")
    async def generate_qr_code_lite(current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """生成轻量扫码登录(纯 HTTP)二维码"""
        try:
            ctx.log_with_user('info', "请求生成轻量扫码登录二维码", current_user)
            ctx._cleanup_qr_lite_sessions()

            session_id = ctx.uuid.uuid4().hex
            ctx.qr_lite_sessions[session_id] = {
                'state': 'pending',
                'qr_data_url': None,
                'error_message': None,
                'account_info': None,
                'started_at': time.time(),
                'finished': False,
                'user_id': current_user.get('user_id'),
            }

            asyncio.create_task(ctx._run_qr_login_lite(session_id, current_user))

            # 等 build_initial_cookies + node tfstk + mini_login + generate.do 出二维码
            deadline = time.time() + 30
            while time.time() < deadline:
                st = ctx.qr_lite_sessions[session_id]
                if st.get('qr_data_url') or st.get('error_message') or st.get('finished'):
                    break
                await asyncio.sleep(0.3)

            st = ctx.qr_lite_sessions[session_id]
            if st.get('error_message'):
                return {'success': False, 'message': st['error_message']}
            if not st.get('qr_data_url'):
                return {'success': False, 'message': '生成二维码超时（>30s），可能 node/网络异常'}

            ctx.log_with_user('info', f"轻量扫码登录二维码生成成功: {session_id}", current_user)
            return {
                'success': True,
                'session_id': session_id,
                'qr_code_url': st['qr_data_url'],
            }
        except Exception as e:
            ctx.log_with_user('error', f"生成轻量扫码登录二维码异常: {str(e)}", current_user)
            return {'success': False, 'message': f'生成二维码失败: {str(e)}'}

    @router.get("/qr-login-lite/check/{session_id}")
    async def check_qr_code_status_lite(session_id: str, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """检查轻量扫码登录状态"""
        try:
            st = ctx.qr_lite_sessions.get(session_id)
            if not st:
                return {'status': 'error', 'message': '会话不存在或已过期'}

            # 归属校验：user_id 可能为 0 等 falsy 值，必须显式判空
            st_user_id = st.get('user_id')
            if st_user_id is not None and st_user_id != current_user.get('user_id'):
                return {'status': 'error', 'message': '无权访问该会话'}

            state = st.get('state', 'pending')
            if state == 'pending':
                return {'status': 'waiting', 'message': '正在生成二维码…'}
            if state == 'waiting':
                return {'status': 'waiting', 'message': '等待扫码…'}
            if state == 'scanned':
                return {'status': 'scanned', 'message': '已扫码，请在手机上确认…'}
            if state == 'confirmed':
                return {'status': 'confirmed', 'message': '已确认，正在获取Cookie…'}
            if state == 'success':
                return {
                    'status': 'success',
                    'message': '扫码登录已完成',
                    'account_info': st.get('account_info') or {},
                }
            if state == 'expired':
                return {'status': 'expired', 'message': st.get('error_message') or '二维码已过期'}
            # error
            return {'status': 'error', 'message': st.get('error_message') or '扫码登录失败'}
        except Exception as e:
            ctx.log_with_user('error', f"检查轻量扫码登录状态异常: {str(e)}", current_user)
            return {'status': 'error', 'message': str(e)}

    @router.post("/qr-login/refresh-cookies")
    async def refresh_cookies_from_qr_login(
        request: Dict[str, Any],
        current_user: Dict[str, Any] = Depends(ctx.get_current_user)
    ):
        """使用扫码登录获取的cookie访问指定界面获取真实cookie并存入数据库"""
        try:
            qr_cookies = request.get('qr_cookies')
            cookie_id = request.get('cookie_id')

            if not qr_cookies:
                return {'success': False, 'message': '缺少扫码登录cookie'}

            if not cookie_id:
                return {'success': False, 'message': '缺少cookie_id'}

            user_cookies = ctx.db_manager.get_all_cookies(current_user['user_id'])
            if cookie_id not in user_cookies:
                return {'success': False, 'message': 'forbidden'}

            ctx.log_with_user('info', f"开始使用扫码cookie刷新真实cookie: {cookie_id}", current_user)

            # 记录扫码刷新Cookie到风控日志
            risk_log_id = None
            risk_session_id = ctx._new_risk_log_session_id('qrrefresh')
            risk_log_started_at = time.time()
            try:
                risk_log_id = ctx.db_manager.add_risk_control_log(
                    cookie_id=cookie_id,
                    event_type='cookie_refresh',
                    session_id=risk_session_id,
                    trigger_scene='manual_qr_refresh',
                    result_code='manual_qr_refresh_started',
                    event_description='手动触发扫码Cookie刷新',
                    processing_status='processing',
                    event_meta=ctx._build_risk_event_meta({'account_id': cookie_id})
                )
            except Exception as log_e:
                logger.error(f"记录风控日志失败: {log_e}")

            # 创建一个临时的XianyuLive实例来执行cookie刷新
            from XianyuAutoAsync import XianyuLive

            # 使用扫码登录的cookie创建临时实例
            temp_instance = XianyuLive(
                cookies_str=qr_cookies,
                cookie_id=cookie_id,
                user_id=current_user['user_id'],
                register_instance=False,
            )

            # 执行cookie刷新
            success = await temp_instance.refresh_cookies_from_qr_login(
                qr_cookies_str=qr_cookies,
                cookie_id=cookie_id,
                user_id=current_user['user_id']
            )

            if success:
                ctx.log_with_user('info', f"扫码cookie刷新成功: {cookie_id}", current_user)

                # 更新风控日志状态
                if risk_log_id:
                    try:
                        ctx.db_manager.update_risk_control_log(
                            log_id=risk_log_id,
                            processing_status='success',
                            processing_result='扫码Cookie刷新成功',
                            session_id=risk_session_id,
                            trigger_scene='manual_qr_refresh',
                            result_code='manual_qr_refresh_success',
                            duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                            event_meta=ctx._build_risk_event_meta({'account_id': cookie_id})
                        )
                    except Exception:
                        pass

                # 如果cookie_manager存在，更新其中的cookie
                if ctx.cookie_manager.manager:
                    # 从数据库获取更新后的cookie
                    updated_cookie_info = ctx.db_manager.get_cookie_by_id(cookie_id)
                    if updated_cookie_info:
                        # refresh_cookies_from_qr_login 已经保存到数据库了，这里不需要再保存
                        handoff_result = ctx.cookie_manager.manager.update_cookie(
                            cookie_id,
                            updated_cookie_info['cookies_str'],
                            save_to_db=False,
                        )
                        ctx._consume_cookie_manager_handoff(handoff_result)
                        ctx.log_with_user('info', f"已更新cookie_manager中的cookie: {cookie_id}", current_user)

                return {
                    'success': True,
                    'message': '真实cookie获取并保存成功',
                    'cookie_id': cookie_id
                }
            else:
                ctx.log_with_user('error', f"扫码cookie刷新失败: {cookie_id}", current_user)
                # 更新风控日志状态
                if risk_log_id:
                    try:
                        ctx.db_manager.update_risk_control_log(
                            log_id=risk_log_id,
                            processing_status='failed',
                            error_message='获取真实cookie失败',
                            session_id=risk_session_id,
                            trigger_scene='manual_qr_refresh',
                            result_code='manual_qr_refresh_failed',
                            duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                            event_meta=ctx._build_risk_event_meta({'account_id': cookie_id})
                        )
                    except Exception:
                        pass
                return {'success': False, 'message': '获取真实cookie失败'}

        except Exception as e:
            ctx.log_with_user('error', f"扫码cookie刷新异常: {str(e)}", current_user)
            # 更新风控日志状态
            if risk_log_id:
                try:
                    ctx.db_manager.update_risk_control_log(
                        log_id=risk_log_id,
                        processing_status='failed',
                        error_message=str(e)[:200],
                        session_id=risk_session_id,
                        trigger_scene='manual_qr_refresh',
                        result_code='manual_qr_refresh_exception',
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=ctx._build_risk_event_meta({'account_id': cookie_id})
                    )
                except Exception:
                    pass
            return {'success': False, 'message': f'刷新cookie失败: {str(e)}'}

    @router.post("/qr-login/reset-cooldown/{cookie_id}")
    async def reset_qr_cookie_refresh_cooldown(
        cookie_id: str,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user)
    ):
        """重置指定账号的扫码登录Cookie刷新冷却时间"""
        try:
            ctx.log_with_user('info', f"重置扫码登录Cookie刷新冷却时间: {cookie_id}", current_user)

            # 检查cookie是否存在
            cookie_info = ctx.db_manager.get_cookie_by_id(cookie_id)
            if not cookie_info:
                return {'success': False, 'message': '账号不存在'}

            user_cookies = ctx.db_manager.get_all_cookies(current_user['user_id'])
            if cookie_id not in user_cookies:
                return {'success': False, 'message': 'forbidden'}

            # 如果cookie_manager中有对应的实例，直接重置
            instance = ctx.cookie_manager.manager.get_xianyu_instance(cookie_id) if ctx.cookie_manager.manager else None
            if instance:
                remaining_time_before = instance.get_qr_cookie_refresh_remaining_time()
                instance.reset_qr_cookie_refresh_flag()

                ctx.log_with_user('info', f"已重置账号 {cookie_id} 的扫码登录冷却时间，原剩余时间: {remaining_time_before}秒", current_user)

                return {
                    'success': True,
                    'message': '扫码登录Cookie刷新冷却时间已重置',
                    'cookie_id': cookie_id,
                    'previous_remaining_time': remaining_time_before
                }
            else:
                # 如果没有活跃实例，返回成功（因为没有冷却时间需要重置）
                ctx.log_with_user('info', f"账号 {cookie_id} 没有活跃实例，无需重置冷却时间", current_user)
                return {
                    'success': True,
                    'message': '账号没有活跃实例，无需重置冷却时间',
                    'cookie_id': cookie_id
                }

        except Exception as e:
            ctx.log_with_user('error', f"重置扫码登录冷却时间异常: {str(e)}", current_user)
            return {'success': False, 'message': f'重置冷却时间失败: {str(e)}'}

    @router.get("/qr-login/cooldown-status/{cookie_id}")
    async def get_qr_cookie_refresh_cooldown_status(
        cookie_id: str,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user)
    ):
        """获取指定账号的扫码登录Cookie刷新冷却状态"""
        try:
            # 检查cookie是否存在
            cookie_info = ctx.db_manager.get_cookie_by_id(cookie_id)
            if not cookie_info:
                return {'success': False, 'message': '账号不存在'}

            user_cookies = ctx.db_manager.get_all_cookies(current_user['user_id'])
            if cookie_id not in user_cookies:
                return {'success': False, 'message': 'forbidden'}

            # 如果cookie_manager中有对应的实例，获取冷却状态
            instance = ctx.cookie_manager.manager.get_xianyu_instance(cookie_id) if ctx.cookie_manager.manager else None
            if instance:
                remaining_time = instance.get_qr_cookie_refresh_remaining_time()
                cooldown_duration = instance.qr_cookie_refresh_cooldown
                last_refresh_time = instance.last_qr_cookie_refresh_time

                return {
                    'success': True,
                    'cookie_id': cookie_id,
                    'remaining_time': remaining_time,
                    'cooldown_duration': cooldown_duration,
                    'last_refresh_time': last_refresh_time,
                    'is_in_cooldown': remaining_time > 0,
                    'remaining_minutes': remaining_time // 60,
                    'remaining_seconds': remaining_time % 60
                }
            else:
                return {
                    'success': True,
                    'cookie_id': cookie_id,
                    'remaining_time': 0,
                    'cooldown_duration': 600,  # 默认10分钟
                    'last_refresh_time': 0,
                    'is_in_cooldown': False,
                    'message': '账号没有活跃实例'
                }

        except Exception as e:
            ctx.log_with_user('error', f"获取扫码登录冷却状态异常: {str(e)}", current_user)
            return {'success': False, 'message': f'获取冷却状态失败: {str(e)}'}

    return router
