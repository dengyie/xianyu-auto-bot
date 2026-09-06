"""Core cookies CRUD routes (Strangler Fig P1).

Mechanically extracted from reply_server.py at main@0aa4100; behavior-preserving.
Shared models/helpers/state live in app/api/models.py, app/api/common.py and app/api/state.py; reply_server-resident symbols are accessed late-bound (reply_server.X) so runtime rebinds stay visible.
"""

from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from app.api.models import AutoCommentUpdate, AutoConfirmUpdate, CommentTemplateCreate, CommentTemplateUpdate, CookieAccountInfo, CookieIn, CookieStatusIn, PauseDurationUpdate, ProxyConfig, RemarkUpdate
import db_manager
import reply_server
import cookie_manager

def create_cookies_router() -> APIRouter:
    router = APIRouter()
    @router.get("/cookies")
    def list_cookies(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        if cookie_manager.manager is None:
            return []

        # 获取当前用户的cookies
        user_id = current_user['user_id']
        user_cookies = db_manager.db_manager.get_all_cookies(user_id)
        return list(user_cookies.keys())

    @router.get("/cookies/details")
    def get_cookies_details(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取所有Cookie的详细信息（包括值和状态）"""
        if cookie_manager.manager is None:
            return []

        user_cookies = reply_server._get_user_cookies_map(current_user)

        result = []
        for cookie_id, cookie_value in user_cookies.items():
            cookie_enabled = cookie_manager.manager.get_cookie_status(cookie_id)
            auto_confirm = db_manager.db_manager.get_auto_confirm(cookie_id)
            auto_comment = db_manager.db_manager.get_auto_comment(cookie_id)
            # 获取备注信息
            cookie_details = db_manager.db_manager.get_cookie_details(cookie_id)
            remark = cookie_details.get('remark', '') if cookie_details else ''
            status_note = cookie_details.get('status_note', '') if cookie_details else ''
            username = cookie_details.get('username', '') if cookie_details else ''
            has_password = bool(cookie_details.get('password')) if cookie_details else False

            result.append({
                'id': cookie_id,
                'value': reply_server.mask_cookie_value(cookie_value),
                'has_cookie_value': bool(cookie_value),
                'enabled': cookie_enabled,
                'auto_confirm': auto_confirm,
                'auto_comment': auto_comment,
                'remark': remark,
                'status_note': status_note,
                'username': username,
                'has_password': has_password,
                'pause_duration': cookie_details.get('pause_duration', 10) if cookie_details else 10,
                'runtime_status': reply_server._build_live_runtime_status(cookie_id),
            })
        return result

    @router.post("/cookies")
    def add_cookie(item: CookieIn, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 添加cookie时绑定到当前用户
            user_id = current_user['user_id']

            reply_server.log_with_user('info', f"尝试添加Cookie: {item.id}, 当前用户ID: {user_id}, 用户名: {current_user.get('username', 'unknown')}", current_user)

            # 检查cookie是否已存在且属于其他用户
            existing_cookies = db_manager.db_manager.get_all_cookies()
            if item.id in existing_cookies:
                # 检查是否属于当前用户
                user_cookies = db_manager.db_manager.get_all_cookies(user_id)
                if item.id not in user_cookies:
                    reply_server.log_with_user('warning', f"Cookie ID冲突: {item.id} 已被其他用户使用", current_user)
                    raise HTTPException(status_code=400, detail="该Cookie ID已被其他用户使用")

            # 保存到数据库时指定用户ID
            db_manager.db_manager.save_cookie(item.id, item.value, user_id)

            # 添加到CookieManager，同时指定用户ID
            handoff_result = cookie_manager.manager.add_cookie(item.id, item.value, user_id=user_id)
            reply_server._consume_cookie_manager_handoff(handoff_result)
            reply_server.log_with_user('info', f"Cookie添加成功: {item.id}", current_user)
            return {"msg": "success"}
        except HTTPException:
            raise
        except Exception as e:
            reply_server.log_with_user('error', f"添加Cookie失败: {item.id} - {reply_server.mask_sensitive_text(e)}", current_user)
            raise HTTPException(status_code=400, detail=reply_server.safe_client_error("添加Cookie失败，请检查输入后重试"))

    @router.put('/cookies/{cid}')
    def update_cookie(cid: str, item: CookieIn, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail='CookieManager 未就绪')
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取旧的 cookie 值，用于判断是否需要重启任务
            old_cookie_details = db_manager.db_manager.get_cookie_details(cid)
            old_cookie_value = old_cookie_details.get('value') if old_cookie_details else None

            # 使用 update_cookie_account_info 更新（只更新cookie值，不覆盖其他字段）
            success = db_manager.db_manager.update_cookie_account_info(cid, cookie_value=item.value)
        
            if not success:
                raise HTTPException(status_code=400, detail="更新Cookie失败")
        
            # 只有当 cookie 值真的发生变化时才重启任务
            if item.value != old_cookie_value:
                logger.info(f"Cookie值已变化，重启任务: {cid}")
                handoff_result = cookie_manager.manager.update_cookie(cid, item.value, save_to_db=False)
                reply_server._consume_cookie_manager_handoff(handoff_result)
            else:
                logger.info(f"Cookie值未变化，无需重启任务: {cid}")
        
            return {'msg': 'updated'}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新Cookie失败: {cid} - {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=reply_server.safe_client_error("更新Cookie失败，请稍后重试"))

    @router.post("/cookie/{cid}/account-info")
    def update_cookie_account_info(cid: str, info: CookieAccountInfo, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新账号信息（Cookie、用户名、密码、显示浏览器设置）"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail='CookieManager 未就绪')
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取旧的 cookie 值，用于判断是否需要重启任务
            old_cookie_details = db_manager.db_manager.get_cookie_details(cid)
            old_cookie_value = old_cookie_details.get('value') if old_cookie_details else None
        
            # 更新数据库
            success = db_manager.db_manager.update_cookie_account_info(
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
                handoff_result = cookie_manager.manager.update_cookie(cid, info.value, save_to_db=False)
                reply_server._consume_cookie_manager_handoff(handoff_result)
            else:
                logger.info(f"Cookie值未变化，无需重启任务: {cid}")
        
            return {'msg': 'updated', 'success': True}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新账号信息失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=reply_server.safe_client_error("更新账号信息失败，请稍后重试"))

    @router.get("/cookie/{cid}/details")
    def get_cookie_account_details(cid: str, include_secrets: bool = False, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取账号详细信息（包括用户名、密码、显示浏览器设置）"""
        try:
            cid = reply_server._ensure_cookie_access(cid, current_user)

            # 获取详细信息
            details = db_manager.db_manager.get_cookie_details(cid)
        
            if not details:
                raise HTTPException(status_code=404, detail="账号不存在")

            runtime_status = reply_server._build_live_runtime_status(cid)

            if not include_secrets:
                details = {
                    **details,
                    'value': reply_server.mask_cookie_value(details.get('value')),
                    'password': reply_server.mask_secret_value(details.get('password')),
                    'proxy_pass': reply_server.mask_secret_value(details.get('proxy_pass')),
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
            logger.error(f"获取账号详情失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=reply_server.safe_client_error("获取账号详情失败，请稍后重试"))

    @router.get("/cookies/{cid}/runtime-status")
    def get_cookie_runtime_status(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取账号运行态状态，便于排查保活/连接问题。"""
        try:
            cid = reply_server._ensure_cookie_access(cid, current_user)
            return {
                'cookie_id': cid,
                'runtime_status': reply_server._build_live_runtime_status(cid),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取账号运行态失败: {cid} - {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=reply_server.safe_client_error("获取账号运行态失败，请稍后重试"))

    @router.get("/cookies/{cid}/conversations/{conversation_id}/history")
    async def get_conversation_history(
        cid: str,
        conversation_id: str,
        page_size: int = 20,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """获取指定会话的历史消息。"""
        try:
            cid = reply_server._ensure_cookie_access(cid, current_user)
            normalized_conversation_id = str(conversation_id or '').strip().split('@')[0]
            if not normalized_conversation_id:
                raise HTTPException(status_code=400, detail="缺少会话ID")

            normalized_page_size = max(1, min(int(page_size or 20), 100))

            from XianyuAutoAsync import XianyuLive
            live_instance = XianyuLive.get_instance(cid)
            if not live_instance:
                raise HTTPException(status_code=400, detail="账号未启动，暂无法查询历史消息")

            reply_server.log_with_user(
                'info',
                f"开始查询账号 {cid} 会话 {normalized_conversation_id} 的历史消息，page_size={normalized_page_size}",
                current_user
            )
            history_messages = await reply_server._run_live_instance_on_manager_loop(
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
                'runtime_status': reply_server._build_live_runtime_status(cid),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取历史消息失败: {cid}/{conversation_id} - {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=reply_server.safe_client_error("获取历史消息失败，请稍后重试"))

    @router.post("/cookies/{cid}/session-keepalive")
    async def trigger_session_keepalive(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """手动触发一次轻量会话保活。"""
        try:
            cid = reply_server._ensure_cookie_access(cid, current_user)

            from XianyuAutoAsync import XianyuLive
            live_instance = XianyuLive.get_instance(cid)
            if not live_instance:
                try:
                    live_instance = getattr(cookie_manager.manager, 'live_instances', {}).get(cid) if cookie_manager.manager else None
                except Exception:
                    live_instance = None

            reply_server.log_with_user('info', f"手动触发账号 {cid} 的轻量会话保活", current_user)
            used_temporary_instance = False

            if live_instance:
                keepalive_ok = await reply_server._run_live_instance_on_manager_loop(
                    cid,
                    lambda: live_instance.keep_session_alive(),
                    timeout=40,
                )
            else:
                # 账号刚完成扫码/手动刷新、或旧误暂停导致主任务尚未恢复时，仍允许用数据库中的
                # 最新 Cookie 做一次 one-shot 轻保活；普通扫码登录不应因为“实例未注册”而无法验证会话。
                cookie_value = db_manager.db_manager.get_cookie(cid)
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
                            logger.warning(f"临时轻量保活关闭会话失败: {cid} - {reply_server.mask_sensitive_text(close_e)}")

                keepalive_ok = await reply_server._run_live_instance_on_manager_loop(
                    cid,
                    _run_temporary_keepalive,
                    timeout=40,
                )
                used_temporary_instance = True

            runtime_status = reply_server._build_live_runtime_status(cid)
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
            logger.error(f"手动轻量保活失败: {cid} - {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=reply_server.safe_client_error("手动轻量保活失败，请稍后重试"))

    @router.get("/cookie/{cid}/proxy")
    def get_cookie_proxy_config(cid: str, include_secret: bool = False, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取账号的代理配置"""
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取代理配置
            proxy_config = db_manager.db_manager.get_cookie_proxy_config(cid)

            if not include_secret:
                proxy_config = {
                    **proxy_config,
                    'proxy_pass': reply_server.mask_secret_value(proxy_config.get('proxy_pass')),
                    'has_proxy_pass': bool(proxy_config.get('proxy_pass')),
                }
        
            return {
                'success': True,
                'data': proxy_config
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取代理配置失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=reply_server.safe_client_error("获取代理配置失败，请稍后重试"))

    @router.post("/cookie/{cid}/proxy")
    def update_cookie_proxy_config(cid: str, config: ProxyConfig, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新账号的代理配置"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail='CookieManager 未就绪')
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

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
            success = db_manager.db_manager.update_cookie_proxy_config(
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
                handoff_result = cookie_manager.manager.update_cookie(cid, cookie_value, save_to_db=False)
                reply_server._consume_cookie_manager_handoff(handoff_result)
        
            return {
                'success': True,
                'msg': '代理配置已更新，账号任务已重启'
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新代理配置失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=400, detail=reply_server.safe_client_error("更新代理配置失败，请稍后重试"))

    @router.delete("/cookies/{cid}")
    def remove_cookie(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            cookie_manager.manager.remove_cookie(cid)
            return {"msg": "removed"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    @router.put('/cookies/{cid}/status')
    def update_cookie_status(cid: str, status_data: CookieStatusIn, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新账号的启用/禁用状态"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail='CookieManager 未就绪')
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            cookie_manager.manager.update_cookie_status(cid, status_data.enabled)
            status_note = ''
            if status_data.enabled:
                db_manager.db_manager.update_cookie_status_note(cid, '')
            else:
                cookie_details = db_manager.db_manager.get_cookie_details(cid)
                status_note = cookie_details.get('status_note', '') if cookie_details else ''
            return {'msg': 'status updated', 'enabled': status_data.enabled, 'status_note': status_note}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.put("/cookies/{cid}/auto-confirm")
    def update_auto_confirm(cid: str, update_data: AutoConfirmUpdate, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新账号的自动确认发货设置"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 更新数据库中的auto_confirm设置
            success = db_manager.db_manager.update_auto_confirm(cid, update_data.auto_confirm)
            if not success:
                raise HTTPException(status_code=500, detail="更新自动确认发货设置失败")

            # 通知CookieManager更新设置（如果账号正在运行）
            if hasattr(cookie_manager.manager, 'update_auto_confirm_setting'):
                cookie_manager.manager.update_auto_confirm_setting(cid, update_data.auto_confirm)

            return {
                "msg": "success",
                "auto_confirm": update_data.auto_confirm,
                "message": f"自动确认发货已{'开启' if update_data.auto_confirm else '关闭'}"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/cookies/{cid}/auto-confirm")
    def get_auto_confirm(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取账号的自动确认发货设置"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取auto_confirm设置
            auto_confirm = db_manager.db_manager.get_auto_confirm(cid)
            return {
                "auto_confirm": auto_confirm,
                "message": f"自动确认发货当前{'开启' if auto_confirm else '关闭'}"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/cookies/{cid}/auto-comment")
    def update_auto_comment(cid: str, update_data: AutoCommentUpdate, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新账号的自动好评设置"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 更新数据库中的auto_comment设置
            success = db_manager.db_manager.update_auto_comment(cid, update_data.auto_comment)
            if not success:
                raise HTTPException(status_code=500, detail="更新自动好评设置失败")

            return {
                "msg": "success",
                "auto_comment": update_data.auto_comment,
                "message": f"自动好评已{'开启' if update_data.auto_comment else '关闭'}"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/cookies/{cid}/auto-comment")
    def get_auto_comment(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取账号的自动好评设置"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取auto_comment设置
            auto_comment = db_manager.db_manager.get_auto_comment(cid)
            return {
                "auto_comment": auto_comment,
                "message": f"自动好评当前{'开启' if auto_comment else '关闭'}"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/cookies/{cid}/comment-templates")
    def get_comment_templates(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取账号的好评模板列表"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            templates = db_manager.db_manager.get_comment_templates(cid)
            return {
                "templates": templates,
                "message": "获取好评模板列表成功"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/cookies/{cid}/comment-templates")
    def add_comment_template(cid: str, template_data: CommentTemplateCreate, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """添加好评模板"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            template_id = db_manager.db_manager.add_comment_template(
                cid, 
                template_data.name, 
                template_data.content, 
                template_data.is_active
            )
            if template_id is None:
                raise HTTPException(status_code=500, detail="添加好评模板失败")

            return {
                "msg": "success",
                "template_id": template_id,
                "message": "添加好评模板成功"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/cookies/{cid}/comment-templates/{template_id}")
    def update_comment_template(cid: str, template_id: int, template_data: CommentTemplateUpdate, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新好评模板"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            success = db_manager.db_manager.update_comment_template(
                template_id,
                name=template_data.name,
                content=template_data.content,
                is_active=template_data.is_active,
                cookie_id=cid
            )
            if not success:
                raise HTTPException(status_code=404, detail="好评模板不存在")

            return {
                "msg": "success",
                "message": "更新好评模板成功"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/cookies/{cid}/comment-templates/{template_id}")
    def delete_comment_template(cid: str, template_id: int, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """删除好评模板"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            success = db_manager.db_manager.delete_comment_template(template_id, cookie_id=cid)
            if not success:
                raise HTTPException(status_code=404, detail="好评模板不存在")

            return {
                "msg": "success",
                "message": "删除好评模板成功"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/cookies/{cid}/comment-templates/{template_id}/activate")
    def activate_comment_template(cid: str, template_id: int, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """激活指定的好评模板"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            success = db_manager.db_manager.set_active_comment_template(cid, template_id)
            if not success:
                raise HTTPException(status_code=404, detail="好评模板不存在")

            return {
                "msg": "success",
                "message": "激活好评模板成功"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/cookies/{cid}/remark")
    def update_cookie_remark(cid: str, update_data: RemarkUpdate, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新账号备注"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 更新备注
            success = db_manager.db_manager.update_cookie_remark(cid, update_data.remark)
            if success:
                reply_server.log_with_user('info', f"更新账号备注: {cid} -> {update_data.remark}", current_user)
                return {
                    "message": "备注更新成功",
                    "remark": update_data.remark
                }
            else:
                raise HTTPException(status_code=500, detail="备注更新失败")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/cookies/{cid}/remark")
    def get_cookie_remark(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取账号备注"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取Cookie详细信息（包含备注）
            cookie_details = db_manager.db_manager.get_cookie_details(cid)
            if cookie_details:
                return {
                    "remark": cookie_details.get('remark', ''),
                    "message": "获取备注成功"
                }
            else:
                raise HTTPException(status_code=404, detail="账号不存在")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/cookies/{cid}/pause-duration")
    def update_cookie_pause_duration(cid: str, update_data: PauseDurationUpdate, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新账号自动回复暂停时间"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 验证暂停时间范围（0-60分钟，0表示不暂停）
            if not (0 <= update_data.pause_duration <= 60):
                raise HTTPException(status_code=400, detail="暂停时间必须在0-60分钟之间（0表示不暂停）")

            # 更新暂停时间
            success = db_manager.db_manager.update_cookie_pause_duration(cid, update_data.pause_duration)
            if success:
                reply_server.log_with_user('info', f"更新账号自动回复暂停时间: {cid} -> {update_data.pause_duration}分钟", current_user)
                return {
                    "message": "暂停时间更新成功",
                    "pause_duration": update_data.pause_duration
                }
            else:
                raise HTTPException(status_code=500, detail="暂停时间更新失败")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/cookies/{cid}/pause-duration")
    def get_cookie_pause_duration(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取账号自动回复暂停时间"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cid not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            # 获取暂停时间
            pause_duration = db_manager.db_manager.get_cookie_pause_duration(cid)
            return {
                "pause_duration": pause_duration,
                "message": "获取暂停时间成功"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/cookies/check")
    async def check_valid_cookies(
        current_user: Optional[Dict[str, Any]] = Depends(reply_server.get_current_user_optional)
    ):
        """检查是否有有效的cookies账户（必须是启用状态）"""
        try:
            if cookie_manager.manager is None:
                return {
                    "success": True,
                    "hasValidCookies": False,
                    "validCount": 0,
                    "enabledCount": 0,
                    "totalCount": 0
                }


            if not current_user:
                return {
                    "success": True,
                    "hasValidCookies": False,
                    "validCount": 0,
                    "enabledCount": 0,
                    "totalCount": 0
                }

            # 获取当前用户的cookies
            all_cookies = db_manager.db_manager.get_all_cookies(current_user["user_id"])

            # 检查启用状态和有效性
            valid_cookies = []
            enabled_cookies = []

            for cookie_id, cookie_value in all_cookies.items():
                # 检查是否启用
                is_enabled = cookie_manager.manager.get_cookie_status(cookie_id)
                if is_enabled:
                    enabled_cookies.append(cookie_id)
                    # 检查是否有效（长度大于50）
                    if len(cookie_value) > 50:
                        valid_cookies.append(cookie_id)

            return {
                "success": True,
                "hasValidCookies": len(valid_cookies) > 0,
                "validCount": len(valid_cookies),
                "enabledCount": len(enabled_cookies),
                "totalCount": len(all_cookies)
            }

        except Exception as e:
            logger.error(f"检查cookies失败: {str(e)}")
            return {
                "success": False,
                "hasValidCookies": False,
                "error": str(e)
            }

    @router.get("/cookies/check")
    async def check_valid_cookies(
        current_user: Optional[Dict[str, Any]] = Depends(reply_server.get_current_user_optional)
    ):
        """检查是否有有效的cookies账户（必须是启用状态）"""
        try:
            if cookie_manager.manager is None:
                return {
                    "success": True,
                    "hasValidCookies": False,
                    "validCount": 0,
                    "enabledCount": 0,
                    "totalCount": 0
                }


            # 获取所有cookies
            all_cookies = db_manager.db_manager.get_all_cookies()

            # 检查启用状态和有效性
            valid_cookies = []
            enabled_cookies = []

            for cookie_id, cookie_value in all_cookies.items():
                # 检查是否启用
                is_enabled = cookie_manager.manager.get_cookie_status(cookie_id)
                if is_enabled:
                    enabled_cookies.append(cookie_id)
                    # 检查是否有效（长度大于50）
                    if len(cookie_value) > 50:
                        valid_cookies.append(cookie_id)

            return {
                "success": True,
                "hasValidCookies": len(valid_cookies) > 0,
                "validCount": len(valid_cookies),
                "enabledCount": len(enabled_cookies),
                "totalCount": len(all_cookies)
            }

        except Exception as e:
            logger.error(f"检查cookies失败: {str(e)}")
            return {
                "success": False,
                "hasValidCookies": False,
                "error": str(e)
            }
    return router
