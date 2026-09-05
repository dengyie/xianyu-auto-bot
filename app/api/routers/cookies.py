"""Core cookies CRUD routes (Strangler Fig P1).

Mechanically extracted from reply_server.py at main@0aa4100; behavior-preserving.
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


def create_cookies_router(ctx) -> APIRouter:
    router = APIRouter()
    @router.get("/cookies")
    def list_cookies(current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        if ctx.cookie_manager.manager is None:
            return []

        # 获取当前用户的cookies
        user_id = current_user['user_id']
        from db_manager import db_manager
        user_cookies = ctx.db_manager.get_all_cookies(user_id)
        return list(user_cookies.keys())

    @router.get("/cookies/details")
    def get_cookies_details(current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """获取所有Cookie的详细信息（包括值和状态）"""
        if ctx.cookie_manager.manager is None:
            return []

        user_cookies = ctx._get_user_cookies_map(current_user)

        result = []
        for cookie_id, cookie_value in user_cookies.items():
            cookie_enabled = ctx.cookie_manager.manager.get_cookie_status(cookie_id)
            auto_confirm = ctx.db_manager.get_auto_confirm(cookie_id)
            auto_comment = ctx.db_manager.get_auto_comment(cookie_id)
            # 获取备注信息
            cookie_details = ctx.db_manager.get_cookie_details(cookie_id)
            remark = cookie_details.get('remark', '') if cookie_details else ''
            status_note = cookie_details.get('status_note', '') if cookie_details else ''
            username = cookie_details.get('username', '') if cookie_details else ''
            has_password = bool(cookie_details.get('password')) if cookie_details else False

            result.append({
                'id': cookie_id,
                'value': ctx.mask_cookie_value(cookie_value),
                'has_cookie_value': bool(cookie_value),
                'enabled': cookie_enabled,
                'auto_confirm': auto_confirm,
                'auto_comment': auto_comment,
                'remark': remark,
                'status_note': status_note,
                'username': username,
                'has_password': has_password,
                'pause_duration': cookie_details.get('pause_duration', 10) if cookie_details else 10,
                'runtime_status': ctx._build_live_runtime_status(cookie_id),
            })
        return result

    @router.post("/cookies")
    def add_cookie(item: ctx.CookieIn, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        if ctx.cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 添加cookie时绑定到当前用户
            user_id = current_user['user_id']
            from db_manager import db_manager

            ctx.log_with_user('info', f"尝试添加Cookie: {item.id}, 当前用户ID: {user_id}, 用户名: {current_user.get('username', 'unknown')}", current_user)

            # 检查cookie是否已存在且属于其他用户
            existing_cookies = ctx.db_manager.get_all_cookies()
            if item.id in existing_cookies:
                # 检查是否属于当前用户
                user_cookies = ctx.db_manager.get_all_cookies(user_id)
                if item.id not in user_cookies:
                    ctx.log_with_user('warning', f"Cookie ID冲突: {item.id} 已被其他用户使用", current_user)
                    raise HTTPException(status_code=400, detail="该Cookie ID已被其他用户使用")

            # 保存到数据库时指定用户ID
            ctx.db_manager.save_cookie(item.id, item.value, user_id)

            # 添加到CookieManager，同时指定用户ID
            handoff_result = ctx.cookie_manager.manager.add_cookie(item.id, item.value, user_id=user_id)
            ctx._consume_cookie_manager_handoff(handoff_result)
            ctx.log_with_user('info', f"Cookie添加成功: {item.id}", current_user)
            return {"msg": "success"}
        except HTTPException:
            raise
        except Exception as e:
            ctx.log_with_user('error', f"添加Cookie失败: {item.id} - {ctx.mask_sensitive_text(e)}", current_user)
            raise HTTPException(status_code=400, detail=ctx.safe_client_error("添加Cookie失败，请检查输入后重试"))

    @router.put('/cookies/{cid}')
    def update_cookie(cid: str, item: ctx.CookieIn, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        if ctx.cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail='CookieManager 未就绪')
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            from db_manager import db_manager
            user_cookies = ctx.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取旧的 cookie 值，用于判断是否需要重启任务
            old_cookie_details = ctx.db_manager.get_cookie_details(cid)
            old_cookie_value = old_cookie_details.get('value') if old_cookie_details else None

            # 使用 update_cookie_account_info 更新（只更新cookie值，不覆盖其他字段）
            success = ctx.db_manager.update_cookie_account_info(cid, cookie_value=item.value)
        
            if not success:
                raise HTTPException(status_code=400, detail="更新Cookie失败")
        
            # 只有当 cookie 值真的发生变化时才重启任务
            if item.value != old_cookie_value:
                logger.info(f"Cookie值已变化，重启任务: {cid}")
                handoff_result = ctx.cookie_manager.manager.update_cookie(cid, item.value, save_to_db=False)
                ctx._consume_cookie_manager_handoff(handoff_result)
            else:
                logger.info(f"Cookie值未变化，无需重启任务: {cid}")
        
            return {'msg': 'updated'}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新Cookie失败: {cid} - {ctx.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=ctx.safe_client_error("更新Cookie失败，请稍后重试"))

    @router.post("/cookie/{cid}/account-info")
    def update_cookie_account_info(cid: str, info: ctx.CookieAccountInfo, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """更新账号信息（Cookie、用户名、密码、显示浏览器设置）"""
        if ctx.cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail='CookieManager 未就绪')
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            from db_manager import db_manager
            user_cookies = ctx.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取旧的 cookie 值，用于判断是否需要重启任务
            old_cookie_details = ctx.db_manager.get_cookie_details(cid)
            old_cookie_value = old_cookie_details.get('value') if old_cookie_details else None
        
            # 更新数据库
            success = ctx.db_manager.update_cookie_account_info(
                cid, 
                cookie_value=info.value,
                username=info.username,
                password=info.password,
                show_browser=info.show_browser
            )
        
            if not success:
                raise HTTPException(status_code=400, detail="更新账号信息失败")
        
            # 只有当 cookie 值真的发生变化时才重启任务
            if info.value is not None and info.value != old_cookie_value:
                logger.info(f"Cookie值已变化，重启任务: {cid}")
                handoff_result = ctx.cookie_manager.manager.update_cookie(cid, info.value, save_to_db=False)
                ctx._consume_cookie_manager_handoff(handoff_result)
            else:
                logger.info(f"Cookie值未变化，无需重启任务: {cid}")
        
            return {'msg': 'updated', 'success': True}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新账号信息失败: {ctx.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=ctx.safe_client_error("更新账号信息失败，请稍后重试"))

    @router.get("/cookie/{cid}/details")
    def get_cookie_account_details(cid: str, include_secrets: bool = False, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """获取账号详细信息（包括用户名、密码、显示浏览器设置）"""
        try:
            cid = ctx._ensure_cookie_access(cid, current_user)

            # 获取详细信息
            details = ctx.db_manager.get_cookie_details(cid)
        
            if not details:
                raise HTTPException(status_code=404, detail="账号不存在")

            runtime_status = ctx._build_live_runtime_status(cid)

            if not include_secrets:
                details = {
                    **details,
                    'value': ctx.mask_cookie_value(details.get('value')),
                    'password': ctx.mask_secret_value(details.get('password')),
                    'proxy_pass': ctx.mask_secret_value(details.get('proxy_pass')),
                    'has_cookie_value': bool(details.get('value')),
                    'has_password': bool(details.get('password')),
                    'has_proxy_pass': bool(details.get('proxy_pass')),
                    'runtime_status': runtime_status,
                }
            else:
                details = {
                    **details,
                    'runtime_status': runtime_status,
                }
        
            return details
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取账号详情失败: {ctx.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=ctx.safe_client_error("获取账号详情失败，请稍后重试"))

    @router.get("/cookies/{cid}/runtime-status")
    def get_cookie_runtime_status(cid: str, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """获取账号运行态状态，便于排查保活/连接问题。"""
        try:
            cid = ctx._ensure_cookie_access(cid, current_user)
            return {
                'cookie_id': cid,
                'runtime_status': ctx._build_live_runtime_status(cid),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取账号运行态失败: {cid} - {ctx.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=ctx.safe_client_error("获取账号运行态失败，请稍后重试"))

    @router.get("/cookies/{cid}/conversations/{conversation_id}/history")
    async def get_conversation_history(
        cid: str,
        conversation_id: str,
        page_size: int = 20,
        current_user: Dict[str, Any] = Depends(ctx.get_current_user),
    ):
        """获取指定会话的历史消息。"""
        try:
            cid = ctx._ensure_cookie_access(cid, current_user)
            normalized_conversation_id = str(conversation_id or '').strip().split('@')[0]
            if not normalized_conversation_id:
                raise HTTPException(status_code=400, detail="缺少会话ID")

            normalized_page_size = max(1, min(int(page_size or 20), 100))

            from XianyuAutoAsync import XianyuLive
            live_instance = XianyuLive.get_instance(cid)
            if not live_instance:
                raise HTTPException(status_code=400, detail="账号未启动，暂无法查询历史消息")

            ctx.log_with_user(
                'info',
                f"开始查询账号 {cid} 会话 {normalized_conversation_id} 的历史消息，page_size={normalized_page_size}",
                current_user
            )
            history_messages = await ctx._run_live_instance_on_manager_loop(
                cid,
                lambda: live_instance.list_all_conversations(
                    normalized_conversation_id,
                    page_size=normalized_page_size,
                ),
                timeout=60,
            )
            return {
                'success': True,
                'cookie_id': cid,
                'conversation_id': normalized_conversation_id,
                'page_size': normalized_page_size,
                'count': len(history_messages),
                'messages': history_messages,
                'runtime_status': ctx._build_live_runtime_status(cid),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取历史消息失败: {cid}/{conversation_id} - {ctx.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=ctx.safe_client_error("获取历史消息失败，请稍后重试"))

    @router.post("/cookies/{cid}/session-keepalive")
    async def trigger_session_keepalive(cid: str, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """手动触发一次轻量会话保活。"""
        try:
            cid = ctx._ensure_cookie_access(cid, current_user)

            from XianyuAutoAsync import XianyuLive
            live_instance = XianyuLive.get_instance(cid)
            if not live_instance:
                try:
                    live_instance = getattr(ctx.cookie_manager.manager, 'live_instances', {}).get(cid) if ctx.cookie_manager.manager else None
                except Exception:
                    live_instance = None

            ctx.log_with_user('info', f"手动触发账号 {cid} 的轻量会话保活", current_user)
            used_temporary_instance = False

            if live_instance:
                keepalive_ok = await ctx._run_live_instance_on_manager_loop(
                    cid,
                    lambda: live_instance.keep_session_alive(),
                    timeout=40,
                )
            else:
                # 账号刚完成扫码/手动刷新、或旧误暂停导致主任务尚未恢复时，仍允许用数据库中的
                # 最新 Cookie 做一次 one-shot 轻保活；普通扫码登录不应因为“实例未注册”而无法验证会话。
                cookie_value = ctx.db_manager.get_cookie(cid)
                if not cookie_value:
                    raise HTTPException(status_code=400, detail="账号Cookie不存在，暂无法执行轻量保活")

                async def _run_temporary_keepalive():
                    temp_live = XianyuLive(cookie_value, cookie_id=cid, register_instance=False)
                    try:
                        return await temp_live.keep_session_alive()
                    finally:
                        try:
                            await temp_live.close_session()
                        except Exception as close_e:
                            logger.warning(f"临时轻量保活关闭会话失败: {cid} - {ctx.mask_sensitive_text(close_e)}")

                keepalive_ok = await ctx._run_live_instance_on_manager_loop(
                    cid,
                    _run_temporary_keepalive,
                    timeout=40,
                )
                used_temporary_instance = True

            runtime_status = ctx._build_live_runtime_status(cid)
            return {
                'success': keepalive_ok,
                'cookie_id': cid,
                'message': '轻量会话保活成功' if keepalive_ok else '轻量会话保活失败',
                'runtime_status': runtime_status,
                'temporary_instance': used_temporary_instance,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"手动轻量保活失败: {cid} - {ctx.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=ctx.safe_client_error("手动轻量保活失败，请稍后重试"))

    @router.get("/cookie/{cid}/proxy")
    def get_cookie_proxy_config(cid: str, include_secret: bool = False, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """获取账号的代理配置"""
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            from db_manager import db_manager
            user_cookies = ctx.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取代理配置
            proxy_config = ctx.db_manager.get_cookie_proxy_config(cid)

            if not include_secret:
                proxy_config = {
                    **proxy_config,
                    'proxy_pass': ctx.mask_secret_value(proxy_config.get('proxy_pass')),
                    'has_proxy_pass': bool(proxy_config.get('proxy_pass')),
                }
        
            return {
                'success': True,
                'data': proxy_config
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取代理配置失败: {ctx.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=ctx.safe_client_error("获取代理配置失败，请稍后重试"))

    @router.post("/cookie/{cid}/proxy")
    def update_cookie_proxy_config(cid: str, config: ctx.ProxyConfig, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        """更新账号的代理配置"""
        if ctx.cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail='CookieManager 未就绪')
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            from db_manager import db_manager
            user_cookies = ctx.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 验证代理类型
            valid_proxy_types = ['none', 'http', 'https', 'socks5']
            if config.proxy_type not in valid_proxy_types:
                raise HTTPException(status_code=400, detail=f"无效的代理类型，支持的类型: {', '.join(valid_proxy_types)}")

            # 如果设置了代理类型（非none），验证必要字段
            if config.proxy_type != 'none':
                if not config.proxy_host:
                    raise HTTPException(status_code=400, detail="代理地址不能为空")
                if not config.proxy_port or config.proxy_port <= 0:
                    raise HTTPException(status_code=400, detail="代理端口无效")

            # 更新数据库
            success = ctx.db_manager.update_cookie_proxy_config(
                cid,
                proxy_type=config.proxy_type,
                proxy_host=config.proxy_host,
                proxy_port=config.proxy_port,
                proxy_user=config.proxy_user,
                proxy_pass=config.proxy_pass
            )
        
            if not success:
                raise HTTPException(status_code=400, detail="更新代理配置失败")
        
            # 重启账号任务以应用新的代理配置
            logger.info(f"代理配置已更新，重启账号任务: {cid}")
            cookie_value = user_cookies.get(cid)
            if cookie_value:
                handoff_result = ctx.cookie_manager.manager.update_cookie(cid, cookie_value, save_to_db=False)
                ctx._consume_cookie_manager_handoff(handoff_result)
        
            return {
                'success': True,
                'msg': '代理配置已更新，账号任务已重启'
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新代理配置失败: {ctx.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=ctx.safe_client_error("更新代理配置失败，请稍后重试"))

    @router.delete("/cookies/{cid}")
    def remove_cookie(cid: str, current_user: Dict[str, Any] = Depends(ctx.get_current_user)):
        if ctx.cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            from db_manager import db_manager
            user_cookies = ctx.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            ctx.cookie_manager.manager.remove_cookie(cid)
            return {"msg": "removed"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    return router
