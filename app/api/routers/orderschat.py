"""Orders / chat / send-message / dashboard page routes (Strangler Fig P2-B6).

Mechanically extracted from reply_server.py; behavior-preserving.
Shared models/helpers/state live in app/api/models.py, app/api/common.py and app/api/state.py; reply_server-resident symbols are accessed late-bound (reply_server.X) so runtime rebinds stay visible.
"""

from typing import Any, Dict
import asyncio
import os
import re
import secrets
import time
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from loguru import logger
from app.api.models import ChatSendRequest, CopyKeywordsRequest, OrderHistorySyncRequest, OrderRecoverRequest, RequestModel, ResponseModel, SaveItemKeywordsRequest, SendMessageRequest, SendMessageResponse
from app.api.common import _normalize_history_optional_text
from app.api import state
import db_manager
import reply_server
from app.application.orders.delivery import ForbiddenOrder, ManualDeliveryContextLoader, MissingOrderAccount, OrderNotFound
from chat_event_hub import chat_event_hub
import order_event_hub
from utils.time_utils import get_local_now
import queue

def create_orders_chat_router() -> APIRouter:
    router = APIRouter()
    @router.post('/send-message', response_model=SendMessageResponse)
    async def send_message_api(request: SendMessageRequest):
        """发送消息API接口（使用秘钥验证）"""
        try:
            # 清理所有参数中的换行符
            def clean_param(param_str):
                """清理参数中的换行符"""
                if isinstance(param_str, str):
                    return param_str.replace('\\n', '').replace('\n', '')
                return param_str

            # 清理所有参数
            cleaned_api_key = clean_param(request.api_key)
            cleaned_cookie_id = clean_param(request.cookie_id)
            cleaned_chat_id = clean_param(request.chat_id)
            cleaned_to_user_id = clean_param(request.to_user_id)
            cleaned_message = clean_param(request.message)

            # 验证API秘钥不能为空
            if not cleaned_api_key:
                logger.warning("API秘钥为空")
                return SendMessageResponse(
                    success=False,
                    message="API秘钥不能为空"
                )

            # 验证API秘钥
            if not reply_server.verify_api_key(cleaned_api_key):
                logger.warning(f"API秘钥验证失败: {reply_server.mask_sensitive_text(cleaned_api_key)}")
                return SendMessageResponse(
                    success=False,
                    message="API秘钥验证失败"
                )

            # 验证必需参数不能为空
            required_params = {
                'cookie_id': cleaned_cookie_id,
                'chat_id': cleaned_chat_id,
                'to_user_id': cleaned_to_user_id,
                'message': cleaned_message
            }

            for param_name, param_value in required_params.items():
                if not param_value:
                    logger.warning(f"必需参数 {param_name} 为空")
                    return SendMessageResponse(
                        success=False,
                        message=f"参数 {param_name} 不能为空"
                    )

            # 直接获取XianyuLive实例，跳过cookie_manager检查
            from XianyuAutoAsync import XianyuLive, ConnectionState
            live_instance = XianyuLive.get_instance(cleaned_cookie_id)

            if not live_instance:
                logger.warning(f"账号实例不存在或未连接: {cleaned_cookie_id}")
                return SendMessageResponse(
                    success=False,
                    message="账号实例不存在或未连接，请检查账号状态"
                )

            # 检查WebSocket连接状态（使用connection_state作为主要判断依据）
            # connection_state 是项目维护的连接状态，比 ws.closed 更可靠
            if live_instance.connection_state != ConnectionState.CONNECTED:
                logger.warning(f"账号WebSocket连接状态异常: {cleaned_cookie_id}, 状态: {live_instance.connection_state}")
                return SendMessageResponse(
                    success=False,
                    message=f"账号WebSocket连接状态异常({live_instance.connection_state.value})，请等待重连"
                )
        
            # 额外检查ws对象是否存在
            if not live_instance.ws:
                logger.warning(f"账号WebSocket对象不存在: {cleaned_cookie_id}")
                return SendMessageResponse(
                    success=False,
                    message="账号WebSocket连接未就绪，请等待重连"
                )

            # 发送消息时需要回到账号实例所属事件循环，避免跨 loop 直接操作 ws
            await reply_server._run_live_instance_on_manager_loop(
                cleaned_cookie_id,
                lambda: live_instance.send_msg(
                    live_instance.ws,
                    cleaned_chat_id,
                    cleaned_to_user_id,
                    cleaned_message
                ),
                timeout=15,
            )

            logger.info(f"API成功发送消息: {cleaned_cookie_id} -> {cleaned_to_user_id}, 内容: {cleaned_message[:50]}{'...' if len(cleaned_message) > 50 else ''}")

            return SendMessageResponse(
                success=True,
                message="消息发送成功"
            )

        except HTTPException as e:
            # 使用清理后的参数记录日志
            cookie_id_for_log = clean_param(request.cookie_id) if 'clean_param' in locals() else request.cookie_id
            to_user_id_for_log = clean_param(request.to_user_id) if 'clean_param' in locals() else request.to_user_id
            logger.warning(f"API发送消息被拒绝: {cookie_id_for_log} -> {to_user_id_for_log}, 原因: {reply_server.mask_sensitive_text(e.detail)}")
            return SendMessageResponse(
                success=False,
                message=str(e.detail or "发送消息失败，请稍后重试")
            )
        except Exception as e:
            # 使用清理后的参数记录日志
            cookie_id_for_log = clean_param(request.cookie_id) if 'clean_param' in locals() else request.cookie_id
            to_user_id_for_log = clean_param(request.to_user_id) if 'clean_param' in locals() else request.to_user_id
            logger.error(f"API发送消息异常: {cookie_id_for_log} -> {to_user_id_for_log}, 错误: {reply_server.mask_sensitive_text(e)}")
            return SendMessageResponse(
                success=False,
                message="发送消息失败，请稍后重试"
            )

    @router.post("/xianyu/reply", response_model=ResponseModel)
    async def xianyu_reply(
        req: RequestModel,
        _: None = Depends(reply_server.require_xianyu_reply_api_key),
    ):
        msg_template = reply_server.match_reply(req.cookie_id, req.send_message)
        is_default_reply = False

        if not msg_template:
            # 从数据库获取默认回复
            default_reply_settings = db_manager.db_manager.get_default_reply(req.cookie_id)

            if default_reply_settings and default_reply_settings.get('enabled', False):
                # 检查是否开启了"只回复一次"功能
                if default_reply_settings.get('reply_once', False):
                    # 检查是否已经回复过这个chat_id
                    if db_manager.db_manager.has_default_reply_record(req.cookie_id, req.chat_id):
                        raise HTTPException(status_code=404, detail="该对话已使用默认回复，不再重复回复")

                msg_template = default_reply_settings.get('reply_content', '')
                is_default_reply = True

            # 如果数据库中没有设置或为空，返回错误
            if not msg_template:
                raise HTTPException(status_code=404, detail="未找到匹配的回复规则且未设置默认回复")

        # 按占位符格式化
        try:
            send_msg = msg_template.format(
                send_user_id=req.send_user_id,
                send_user_name=req.send_user_name,
                send_message=req.send_message,
            )
        except Exception:
            # 如果格式化失败，返回原始内容
            send_msg = msg_template

        # 如果是默认回复且开启了"只回复一次"，记录回复记录
        if is_default_reply:
            default_reply_settings = db_manager.db_manager.get_default_reply(req.cookie_id)
            if default_reply_settings and default_reply_settings.get('reply_once', False):
                db_manager.db_manager.add_default_reply_record(req.cookie_id, req.chat_id)

        return {"code": 200, "data": {"send_msg": send_msg}}

    @router.post('/api/orders/history-sync')
    async def start_order_history_sync(request: OrderHistorySyncRequest, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """按时间范围同步历史订单。"""
        try:
            request_data = request.model_dump()
            start_date = str(request_data.get('start_date') or '').strip()
            end_date = str(request_data.get('end_date') or '').strip()
            if not start_date or not end_date:
                raise HTTPException(status_code=400, detail='开始日期和结束日期不能为空')

            cookie_id = _normalize_history_optional_text(request_data.get('cookie_id'))
            max_orders = min(max(int(request_data.get('max_orders') or 120), 1), 500)
            fetch_details = bool(request_data.get('fetch_details', True))

            reply_server._cleanup_order_history_sync_jobs()

            job_id = f"history_sync_{secrets.token_hex(8)}"
            created_at = get_local_now().strftime('%Y-%m-%d %H:%M:%S')
            job = {
                'job_id': job_id,
                'status': 'pending',
                'message': '历史订单同步任务已创建，等待执行',
                'error': None,
                'created_at': created_at,
                'started_at': None,
                'finished_at': None,
                'finished_ts': None,
                'request': {
                    'cookie_id': cookie_id,
                    'start_date': start_date,
                    'end_date': end_date,
                    'max_orders': max_orders,
                    'fetch_details': fetch_details,
                },
                'user_id': current_user['user_id'],
                'user_info': {
                    'user_id': current_user['user_id'],
                    'username': current_user.get('username'),
                },
                'current_account': None,
                'current_order_id': None,
                'accounts_total': 0,
                'accounts_completed': 0,
                'orders_discovered': 0,
                'orders_processed': 0,
                'orders_saved': 0,
                'orders_skipped': 0,
                'orders_failed': 0,
                'matched_orders': 0,
                'warnings': [],
            }
            state.order_history_sync_jobs[job_id] = job

            task = asyncio.create_task(reply_server._run_order_history_sync_job(job_id))
            state.order_history_sync_tasks[job_id] = task

            def _on_task_done(done_task: asyncio.Task) -> None:
                state.order_history_sync_tasks.pop(job_id, None)
                try:
                    done_task.result()
                except asyncio.CancelledError:
                    pass
                except Exception as task_exc:
                    logger.error(f"历史订单同步后台任务异常: job_id={job_id}, error={task_exc}")

            task.add_done_callback(_on_task_done)

            reply_server.log_with_user(
                'info',
                f"创建历史订单同步任务: job_id={job_id}, cookie_id={cookie_id or 'ALL'}, range={start_date}~{end_date}, max_orders={max_orders}, fetch_details={fetch_details}",
                current_user
            )
            return {"success": True, "data": reply_server._create_order_history_sync_job_snapshot(job)}
        except HTTPException:
            raise
        except Exception as exc:
            reply_server.log_with_user('error', f"创建历史订单同步任务失败: {exc}", current_user)
            raise HTTPException(status_code=500, detail=f"创建历史订单同步任务失败: {exc}")

    @router.get('/api/orders/history-sync/{job_id}')
    def get_order_history_sync_status(job_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """查询历史订单同步任务状态。"""
        reply_server._cleanup_order_history_sync_jobs()

        job = state.order_history_sync_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail='历史订单同步任务不存在或已过期')
        if job.get('user_id') != current_user['user_id']:
            raise HTTPException(status_code=403, detail='无权访问该历史订单同步任务')

        return {"success": True, "data": reply_server._create_order_history_sync_job_snapshot(job)}

    @router.post('/api/orders/history-sync/{job_id}/cancel')
    def cancel_order_history_sync(job_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """取消历史订单同步任务。"""
        job = state.order_history_sync_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail='历史订单同步任务不存在或已过期')
        if job.get('user_id') != current_user['user_id']:
            raise HTTPException(status_code=403, detail='无权取消该历史订单同步任务')

        if str(job.get('status') or '') in {'completed', 'failed', 'cancelled'}:
            return {"success": True, "data": reply_server._create_order_history_sync_job_snapshot(job)}

        job['status'] = 'cancelled'
        job['error'] = None
        job['message'] = '历史订单同步已取消'
        job['finished_at'] = get_local_now().strftime('%Y-%m-%d %H:%M:%S')
        job['finished_ts'] = time.time()

        task = state.order_history_sync_tasks.get(job_id)
        if task and not task.done():
            task.cancel()

        return {"success": True, "data": reply_server._create_order_history_sync_job_snapshot(job)}

    @router.post('/api/orders/recover')
    async def recover_order_by_id(request: OrderRecoverRequest, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """按已知订单号强制补抓订单详情；详情确认待发货时可触发补偿发货。"""
        try:
            import cookie_manager

            cookie_id = reply_server._ensure_cookie_access(request.cookie_id, current_user)
            order_id = _normalize_history_optional_text(request.order_id)
            if not order_id or not re.fullmatch(r'\d{10,}', order_id):
                raise HTTPException(status_code=400, detail='订单ID格式不正确')

            xianyu_instance = cookie_manager.manager.get_xianyu_instance(cookie_id) if cookie_manager.manager else None
            if not xianyu_instance:
                return {"success": False, "recovered": False, "delivered": False, "message": f"账号 {cookie_id} 未运行，请先启动账号"}

            before_order = db_manager.db_manager.get_order_by_id(order_id) or {}
            before_status = reply_server.normalize_order_status_value(before_order.get('order_status')) if before_order else None

            detail_result = await xianyu_instance.fetch_order_detail_info(
                order_id=order_id,
                item_id=_normalize_history_optional_text(request.item_id) or before_order.get('item_id'),
                buyer_id=_normalize_history_optional_text(request.buyer_id) or before_order.get('buyer_id'),
                sid=_normalize_history_optional_text(request.sid) or before_order.get('sid'),
                buyer_nick=_normalize_history_optional_text(request.buyer_nick) or before_order.get('buyer_nick'),
                buyer_id_source='manual_order_recover',
                force_refresh=True,
            )
            if not detail_result:
                return {"success": False, "recovered": False, "delivered": False, "message": "订单详情补抓失败，请确认订单ID和账号是否匹配"}

            latest_order = db_manager.db_manager.get_order_by_id(order_id) or {}
            latest_status = reply_server.normalize_order_status_value(latest_order.get('order_status')) if latest_order else None
            delivered = False
            if request.auto_deliver and latest_status == 'pending_ship':
                delivered = bool(await xianyu_instance._auto_deliver_recovered_pending_order(
                    latest_order,
                    fallback_order={
                        'order_id': order_id,
                        'item_id': _normalize_history_optional_text(request.item_id),
                        'buyer_id': _normalize_history_optional_text(request.buyer_id),
                        'buyer_nick': _normalize_history_optional_text(request.buyer_nick),
                        'sid': _normalize_history_optional_text(request.sid),
                    },
                    source='manual_order_recover',
                ))

            order_event_hub.publish_order_update_event(order_id, source='manual_order_recover')
            reply_server.log_with_user(
                'info',
                f"按订单ID补抓完成: cookie_id={cookie_id}, order_id={order_id}, status={before_status}->{latest_status}, delivered={delivered}",
                current_user,
            )

            if latest_status == 'pending_ship' and request.auto_deliver and not delivered:
                message = '订单已补抓为待发货，但自动发货未完成，请查看发货日志'
            elif delivered:
                message = '订单已补抓并触发自动发货'
            else:
                message = f"订单已补抓，当前状态: {latest_status or '未知'}"

            return {
                "success": True,
                "recovered": True,
                "delivered": delivered,
                "order": latest_order,
                "old_status": before_status,
                "new_status": latest_status,
                "message": message,
            }
        except HTTPException:
            raise
        except Exception as exc:
            import traceback
            reply_server.log_with_user('error', f"按订单ID补抓失败: {exc}", current_user)
            logger.error(f"按订单ID补抓异常堆栈: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"按订单ID补抓失败: {exc}")

    @router.get('/api/orders')
    def get_user_orders(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取当前用户的订单信息"""
        try:

            user_id = current_user['user_id']
            reply_server.log_with_user('info', "查询用户订单信息", current_user)

            # 获取用户的所有Cookie
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            # 获取所有订单数据
            all_orders = []
            for cookie_id in user_cookies.keys():
                orders = db_manager.db_manager.get_orders_by_cookie(cookie_id, limit=1000)  # 增加限制数量
                # 为每个订单添加cookie_id信息
                for order in orders:
                    order['cookie_id'] = cookie_id
                    if reply_server.normalize_order_status_value(order.get('order_status')) == 'partial_pending_finalize':
                        pending_states = db_manager.db_manager.get_pending_platform_confirm_states(
                            cookie_id=cookie_id,
                            order_id=order.get('order_id'),
                            limit=20,
                        )
                        if pending_states:
                            pending_errors = []
                            for state in pending_states:
                                meta = state.get('delivery_meta') or {}
                                error_text = meta.get('confirm_error') or state.get('last_error')
                                if error_text and error_text not in pending_errors:
                                    pending_errors.append(error_text)
                            order['pending_platform_confirm'] = True
                            order['pending_confirm_units'] = len(pending_states)
                            order['pending_confirm_error'] = '；'.join(pending_errors[:3]) if pending_errors else '平台确认发货失败，等待补确认'
                    all_orders.append(order)

            # 历史订单补录后优先按平台下单时间展示，回退到本地入库时间
            all_orders.sort(
                key=lambda x: x.get('platform_created_at') or x.get('created_at') or '',
                reverse=True
            )

            reply_server.log_with_user('info', f"用户订单查询成功，共 {len(all_orders)} 条记录", current_user)
            return {"success": True, "data": all_orders}

        except Exception as e:
            reply_server.log_with_user('error', f"查询用户订单失败: {str(e)}", current_user)
            raise HTTPException(status_code=500, detail=f"查询订单失败: {str(e)}")

    @router.get('/api/orders/stream')
    def stream_user_orders(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """订单实时事件流，仅在订单页激活时使用。"""
        user_id = current_user['user_id']
        subscriber = order_event_hub.subscribe(user_id)

        def event_generator():
            try:
                yield reply_server.format_sse_event('stream.ready', {'type': 'stream.ready', 'timestamp': int(time.time() * 1000)})
                while True:
                    try:
                        event = subscriber.get(timeout=25)
                        yield reply_server.format_sse_event(event.get('type', 'message'), event)
                    except queue.Empty:
                        yield reply_server.format_sse_event('ping', {'type': 'ping', 'timestamp': int(time.time() * 1000)})
            finally:
                order_event_hub.unsubscribe(user_id, subscriber)

        return StreamingResponse(
            event_generator(),
            media_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            }
        )

    @router.delete('/api/orders/{order_id}')
    def delete_user_order(order_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """删除当前用户自己的订单"""
        try:

            user_id = current_user['user_id']
            order = db_manager.db_manager.get_order_by_id(order_id)
            if not order:
                raise HTTPException(status_code=404, detail="订单不存在")

            cookie_id = order.get('cookie_id')
            cookie_info = db_manager.db_manager.get_cookie_details(cookie_id) if cookie_id else None
            if not cookie_info or cookie_info.get('user_id') != user_id:
                raise HTTPException(status_code=403, detail="无权删除此订单")

            success = db_manager.db_manager.delete_order(order_id, cookie_id=cookie_id)
            if not success:
                raise HTTPException(status_code=400, detail="删除订单失败")

            reply_server.log_with_user('info', f"删除订单成功: {order_id}", current_user)
            return {"success": True, "message": "订单删除成功"}
        except HTTPException:
            raise
        except Exception as e:
            reply_server.log_with_user('error', f"删除订单失败: {order_id} - {reply_server.mask_sensitive_text(e)}", current_user)
            raise HTTPException(status_code=500, detail="删除订单失败，请稍后重试")

    @router.post('/api/orders/{order_id}/confirm-retry')
    async def retry_order_platform_confirm(order_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """只重试平台确认发货，不重复发送卡券。"""
        try:
            import cookie_manager

            user_id = current_user['user_id']
            reply_server.log_with_user('info', f"补确认发货请求: 订单 {order_id}", current_user)

            order = db_manager.db_manager.get_order_by_id(order_id)
            if not order:
                return {"success": False, "confirmed": False, "message": "订单不存在"}

            cookie_id = order.get('cookie_id')
            if not cookie_id:
                return {"success": False, "confirmed": False, "message": "订单缺少账号信息"}

            cookie_info = db_manager.db_manager.get_cookie_details(cookie_id)
            if not cookie_info or cookie_info.get('user_id') != user_id:
                return {"success": False, "confirmed": False, "message": "无权操作此订单"}

            pending_states = db_manager.db_manager.get_pending_platform_confirm_states(
                cookie_id=cookie_id,
                order_id=order_id,
                limit=50,
            )
            if not pending_states:
                return {"success": True, "confirmed": False, "message": "该订单没有待补确认记录"}

            xianyu_instance = cookie_manager.manager.get_xianyu_instance(cookie_id) if cookie_manager.manager else None
            if not xianyu_instance:
                return {"success": False, "confirmed": False, "message": f"账号 {cookie_id} 未运行，请先启动账号"}

            result = await xianyu_instance.retry_pending_platform_confirms(
                order_id=order_id,
                source='manual_confirm_retry',
                limit=50,
            )
            try:
                order_event_hub.publish_order_update_event(order_id, source='manual_confirm_retry')
            except Exception:
                pass

            return {
                "success": bool(result.get('success')),
                "confirmed": int(result.get('confirmed') or 0) > 0,
                "message": result.get('message') or '补确认完成',
                "data": result,
            }
        except Exception as e:
            import traceback
            reply_server.log_with_user('error', f"补确认发货异常: 订单 {order_id} - {str(e)}", current_user)
            logger.error(f"补确认发货异常: {traceback.format_exc()}")
            return {"success": False, "confirmed": False, "message": f"补确认异常: {str(e)}"}

    @router.post('/api/orders/{order_id}/deliver')
    async def manual_deliver_order(order_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """手动发货 - 根据订单信息匹配发货规则并发送卡券"""
        try:
            import cookie_manager

            user_id = current_user['user_id']
            reply_server.log_with_user('info', f"手动发货请求: 订单 {order_id}", current_user)

            try:
                delivery_context = ManualDeliveryContextLoader(db_manager.db_manager).load(
                    order_id, user_id
                )
            except OrderNotFound:
                return {"success": False, "delivered": False, "message": "订单不存在"}
            except MissingOrderAccount:
                return {"success": False, "delivered": False, "message": "订单缺少账号信息"}
            except ForbiddenOrder:
                return {"success": False, "delivered": False, "message": "无权操作此订单"}

            order = delivery_context.order
            cookie_id = delivery_context.cookie_id

            # 获取 XianyuLive 实例
            xianyu_instance = cookie_manager.manager.get_xianyu_instance(cookie_id) if cookie_manager.manager else None
            if not xianyu_instance:
                return {"success": False, "delivered": False, "message": f"账号 {cookie_id} 未运行，请先启动账号"}

            # 获取订单详情
            item_id = order.get('item_id')
            buyer_id = order.get('buyer_id')

            if not item_id:
                return {"success": False, "delivered": False, "message": "订单缺少商品信息"}

            if not buyer_id:
                return {"success": False, "delivered": False, "message": "订单缺少买家信息，无法发送消息"}

            # 获取商品标题
            item_info = db_manager.db_manager.get_item_info(cookie_id, item_id)
            item_title = item_info.get('item_title', '') if item_info else ''

            try:
                expected_quantity = max(1, int(order.get('quantity') or 1))
            except (TypeError, ValueError):
                expected_quantity = 1

            progress_summary_before = xianyu_instance._summarize_delivery_progress(order_id, expected_quantity)
            pending_finalize_units = list(progress_summary_before.get('pending_finalize_unit_indexes') or [])
            finalize_completed_units = 0
            for unit_index in pending_finalize_units:
                pending_finalize_meta = xianyu_instance._get_pending_delivery_finalization_meta(order_id, unit_index)
                if not pending_finalize_meta:
                    continue

                finalize_result = await xianyu_instance._finalize_delivery_after_send(
                    delivery_meta=pending_finalize_meta,
                    order_id=order_id,
                    item_id=item_id
                )
                if not finalize_result.get('success'):
                    xianyu_instance._persist_delivery_finalization_state(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        delivery_meta=pending_finalize_meta,
                        channel='manual',
                        status='sent',
                        last_error=finalize_result.get('error') or f'检测到第 {unit_index} 个发货单元已发送记录，但补完成收尾失败'
                    )
                    return {"success": False, "delivered": False, "message": finalize_result.get('error') or f'检测到第 {unit_index} 个发货单元已发送记录，但补完成收尾失败'}

                xianyu_instance._persist_delivery_finalization_state(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    delivery_meta=pending_finalize_meta,
                    channel='manual',
                    status='finalized'
                )
                finalize_completed_units += 1

            if finalize_completed_units > 0:
                progress_after_finalize = xianyu_instance._sync_order_delivery_progress(
                    order_id=order_id,
                    cookie_id=cookie_id,
                    expected_quantity=expected_quantity,
                    context="手动发货补完成收尾成功"
                )
                order_event_hub.publish_order_update_event(order_id, source='manual_delivery_finalize')
                reply_server.log_with_user('info', f"检测到订单 {order_id} 存在待完成收尾记录，已先补完成 {finalize_completed_units} 个单元，继续执行补发", current_user)
            else:
                progress_after_finalize = progress_summary_before

            remaining_unit_indexes = list(progress_after_finalize.get('remaining_unit_indexes') or [])
            if not remaining_unit_indexes:
                aggregate_status = progress_after_finalize.get('aggregate_status')
                if aggregate_status == 'shipped':
                    return {"success": True, "delivered": True, "message": "订单所有发货单元都已完成，本次仅补完成未收尾记录"}
                return {"success": True, "delivered": True, "message": "订单当前没有可补发的未完成单元"}

            unit_results = []
            prepared_units = []

            def format_delivery_reason(reason: str, order_spec_mode: str = None, rule_spec_mode: str = None, item_config_mode: str = None) -> str:
                context_parts = []
                if order_spec_mode:
                    context_parts.append(f"order_spec_mode={order_spec_mode}")
                if rule_spec_mode:
                    context_parts.append(f"rule_spec_mode={rule_spec_mode}")
                if item_config_mode:
                    context_parts.append(f"item_config_mode={item_config_mode}")

                if not context_parts:
                    return reason

                reason_text = (reason or '').strip() or '未提供发货日志原因'
                if any(part.split('=')[0] + '=' in reason_text for part in context_parts):
                    return reason_text
                return f"{reason_text} [{', '.join(context_parts)}]"

            for unit_index in remaining_unit_indexes:
                delivery_result = await xianyu_instance._auto_delivery(
                    item_id=item_id,
                    item_title=item_title,
                    order_id=order_id,
                    send_user_id=buyer_id,
                    include_meta=True,
                    delivery_unit_index=unit_index
                )

                if isinstance(delivery_result, dict):
                    delivery_content = delivery_result.get('content')
                    delivery_steps = delivery_result.get('delivery_steps') or []
                    delivery_success = bool(delivery_result.get('success') and delivery_content)
                    rule_id = delivery_result.get('rule_id')
                    rule_keyword = delivery_result.get('rule_keyword')
                    card_type = delivery_result.get('card_type')
                    card_id = delivery_result.get('card_id')
                    match_mode = delivery_result.get('match_mode')
                    order_spec_mode = delivery_result.get('order_spec_mode')
                    rule_spec_mode = delivery_result.get('rule_spec_mode')
                    item_config_mode = delivery_result.get('item_config_mode')
                    data_card_pending_consume = delivery_result.get('data_card_pending_consume')
                    data_line = delivery_result.get('data_line')
                    data_reservation_id = delivery_result.get('data_reservation_id')
                    data_reservation_status = delivery_result.get('data_reservation_status')
                    failure_reason = delivery_result.get('error')
                else:
                    delivery_content = delivery_result
                    delivery_steps = []
                    delivery_success = bool(delivery_content)
                    rule_id = None
                    rule_keyword = None
                    card_type = None
                    card_id = None
                    match_mode = None
                    order_spec_mode = None
                    rule_spec_mode = None
                    item_config_mode = None
                    data_card_pending_consume = None
                    data_line = None
                    data_reservation_id = None
                    data_reservation_status = None
                    failure_reason = None

                if delivery_success:
                    if not delivery_steps:
                        delivery_steps = xianyu_instance._build_delivery_steps(delivery_content, '')
                    if not delivery_steps:
                        fail_reason = f"第 {unit_index} 个发货单元发货步骤构建失败"
                        xianyu_instance._release_data_reservation_if_needed(
                            {'data_reservation_id': data_reservation_id},
                            error=fail_reason
                        )
                        db_manager.db_manager.create_delivery_log(
                            user_id=user_id,
                            cookie_id=cookie_id,
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=buyer_id,
                            buyer_nick=order.get('buyer_nick'),
                            rule_id=rule_id,
                            rule_keyword=rule_keyword,
                            card_type=card_type,
                            match_mode=match_mode,
                            channel='manual',
                            status='failed',
                            reason=format_delivery_reason(fail_reason, order_spec_mode, rule_spec_mode, item_config_mode)
                        )
                        unit_results.append({'unit_index': unit_index, 'status': 'failed', 'error': fail_reason})
                        continue

                    prepared_units.append({
                        'unit_index': unit_index,
                        'delivery_steps': delivery_steps,
                        'card_type': card_type,
                        'rule_meta': {
                            'success': True,
                            'rule_id': rule_id,
                            'rule_keyword': rule_keyword,
                            'card_id': card_id,
                            'card_type': card_type,
                            'match_mode': match_mode,
                            'order_spec_mode': order_spec_mode,
                            'rule_spec_mode': rule_spec_mode,
                            'item_config_mode': item_config_mode,
                            'data_card_pending_consume': data_card_pending_consume,
                            'data_line': data_line,
                            'data_reservation_id': data_reservation_id,
                            'data_reservation_status': data_reservation_status,
                            'delivery_unit_index': unit_index,
                        }
                    })
                else:
                    fail_reason = failure_reason or f"第 {unit_index} 个发货单元未匹配到发货规则，请检查卡券和发货规则配置"
                    db_manager.db_manager.create_delivery_log(
                        user_id=user_id,
                        cookie_id=cookie_id,
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        buyer_nick=order.get('buyer_nick'),
                        rule_id=rule_id,
                        rule_keyword=rule_keyword,
                        card_type=card_type,
                        match_mode=match_mode,
                        channel='manual',
                        status='failed',
                        reason=format_delivery_reason(fail_reason, order_spec_mode, rule_spec_mode, item_config_mode)
                    )
                    unit_results.append({'unit_index': unit_index, 'status': 'failed', 'error': fail_reason})

            ws = getattr(xianyu_instance, 'ws', None)
            manual_chat_id = buyer_id
            if ws:
                sid = order.get('sid', '')
                if sid:
                    manual_chat_id = sid.replace('@goofish', '')
                    reply_server.log_with_user('info', f"手动发货: 使用现有WebSocket连接发送, cid={manual_chat_id}, buyer_id={buyer_id}", current_user)
                else:
                    reply_server.log_with_user('warning', f"手动发货: 订单无sid，尝试使用buyer_id作为cid, buyer_id={buyer_id}", current_user)
            else:
                reply_server.log_with_user('warning', f"手动发货: 无现有WebSocket连接，使用send_delivery_steps_once, buyer_id={buyer_id}", current_user)

            send_groups = xianyu_instance._build_delivery_send_groups(prepared_units, expected_quantity)
            total_send_groups = len(send_groups)

            for group_index, send_group in enumerate(send_groups, start=1):
                group_units = send_group.get('units') or []
                if not group_units:
                    continue

                first_unit = group_units[0]
                first_unit_index = first_unit.get('unit_index') or 1
                is_batched_text_group = send_group.get('mode') == 'batched_text'

                try:
                    if ws:
                        await xianyu_instance._send_delivery_steps(
                            ws,
                            manual_chat_id,
                            buyer_id,
                            send_group.get('delivery_steps') or [],
                            log_prefix=(
                                f"手动发货 order_id={order_id} batch={group_index}/{total_send_groups}"
                                if is_batched_text_group else
                                f"手动发货 order_id={order_id} unit={first_unit_index}"
                            )
                        )
                    else:
                        await xianyu_instance.send_delivery_steps_once(buyer_id, item_id, send_group.get('delivery_steps') or [])
                except Exception as send_error:
                    send_error_text = str(send_error)
                    for prepared_unit in group_units:
                        unit_index = prepared_unit.get('unit_index') or 1
                        rule_meta = prepared_unit.get('rule_meta') or {}
                        xianyu_instance._release_data_reservation_if_needed(
                            rule_meta,
                            error=f"手动发货发送失败(unit={unit_index}): {send_error_text}"
                        )
                        db_manager.db_manager.create_delivery_log(
                            user_id=user_id,
                            cookie_id=cookie_id,
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=buyer_id,
                            buyer_nick=order.get('buyer_nick'),
                            rule_id=rule_meta.get('rule_id'),
                            rule_keyword=rule_meta.get('rule_keyword'),
                            card_type=rule_meta.get('card_type'),
                            match_mode=rule_meta.get('match_mode'),
                            channel='manual',
                            status='failed',
                            reason=format_delivery_reason(f"第 {unit_index} 个发货单元消息发送失败: {send_error_text}", rule_meta.get('order_spec_mode'), rule_meta.get('rule_spec_mode'), rule_meta.get('item_config_mode'))
                        )
                        unit_results.append({'unit_index': unit_index, 'status': 'failed', 'error': send_error_text})
                    continue

                for prepared_unit in group_units:
                    unit_index = prepared_unit.get('unit_index') or 1
                    rule_meta = prepared_unit.get('rule_meta') or {}

                    try:
                        if not xianyu_instance._mark_data_reservation_sent_if_needed(rule_meta):
                            xianyu_instance._release_data_reservation_if_needed(
                                rule_meta,
                                error=f'手动发货发送成功后标记预占已发送失败(unit={unit_index})'
                            )
                            db_manager.db_manager.create_delivery_log(
                                user_id=user_id,
                                cookie_id=cookie_id,
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=buyer_id,
                                buyer_nick=order.get('buyer_nick'),
                                rule_id=rule_meta.get('rule_id'),
                                rule_keyword=rule_meta.get('rule_keyword'),
                                card_type=rule_meta.get('card_type'),
                                match_mode=rule_meta.get('match_mode'),
                                channel='manual',
                                status='failed',
                                reason=format_delivery_reason('批量数据预占标记已发送失败', rule_meta.get('order_spec_mode'), rule_meta.get('rule_spec_mode'), rule_meta.get('item_config_mode'))
                            )
                            unit_results.append({'unit_index': unit_index, 'status': 'failed', 'error': '批量数据预占标记已发送失败'})
                            continue

                        xianyu_instance._persist_delivery_finalization_state(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=buyer_id,
                            delivery_meta=rule_meta,
                            channel='manual',
                            status='sent'
                        )

                        finalize_result = await xianyu_instance._finalize_delivery_after_send(
                            delivery_meta=rule_meta,
                            order_id=order_id,
                            item_id=item_id
                        )
                        if not finalize_result.get('success'):
                            xianyu_instance._persist_delivery_finalization_state(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=buyer_id,
                                delivery_meta=rule_meta,
                                channel='manual',
                                status='sent',
                                last_error=finalize_result.get('error') or f'第 {unit_index} 个发货单元发送成功但提交发货副作用失败'
                            )
                            db_manager.db_manager.create_delivery_log(
                                user_id=user_id,
                                cookie_id=cookie_id,
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=buyer_id,
                                buyer_nick=order.get('buyer_nick'),
                                rule_id=rule_meta.get('rule_id'),
                                rule_keyword=rule_meta.get('rule_keyword'),
                                card_type=rule_meta.get('card_type'),
                                match_mode=rule_meta.get('match_mode'),
                                channel='manual',
                                status='failed',
                                reason=format_delivery_reason(finalize_result.get('error') or f'第 {unit_index} 个发货单元发送成功但提交发货副作用失败', rule_meta.get('order_spec_mode'), rule_meta.get('rule_spec_mode'), rule_meta.get('item_config_mode'))
                            )
                            unit_results.append({'unit_index': unit_index, 'status': 'pending_finalize', 'error': finalize_result.get('error') or '发送成功但提交发货副作用失败'})
                            continue

                        xianyu_instance._persist_delivery_finalization_state(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=buyer_id,
                            delivery_meta=rule_meta,
                            channel='manual',
                            status='finalized'
                        )
                        success_reason = f'手动发货第 {unit_index} 个单元发送成功'
                        if is_batched_text_group and len(group_units) > 1:
                            success_reason += '（批量合并发送）'
                        db_manager.db_manager.create_delivery_log(
                            user_id=user_id,
                            cookie_id=cookie_id,
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=buyer_id,
                            buyer_nick=order.get('buyer_nick'),
                            rule_id=rule_meta.get('rule_id'),
                            rule_keyword=rule_meta.get('rule_keyword'),
                            card_type=rule_meta.get('card_type'),
                            match_mode=rule_meta.get('match_mode'),
                            channel='manual',
                            status='success',
                            reason=format_delivery_reason(success_reason, rule_meta.get('order_spec_mode'), rule_meta.get('rule_spec_mode'), rule_meta.get('item_config_mode'))
                        )
                        unit_results.append({'unit_index': unit_index, 'status': 'finalized'})

                    except Exception as unit_post_error:
                        unit_error_text = str(unit_post_error)
                        xianyu_instance._persist_delivery_finalization_state(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=buyer_id,
                            delivery_meta=rule_meta,
                            channel='manual',
                            status='sent',
                            last_error=f'第 {unit_index} 个发货单元消息已发送，但发送后处理异常: {unit_error_text}'
                        )
                        db_manager.db_manager.create_delivery_log(
                            user_id=user_id,
                            cookie_id=cookie_id,
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=buyer_id,
                            buyer_nick=order.get('buyer_nick'),
                            rule_id=rule_meta.get('rule_id'),
                            rule_keyword=rule_meta.get('rule_keyword'),
                            card_type=rule_meta.get('card_type'),
                            match_mode=rule_meta.get('match_mode'),
                            channel='manual',
                            status='failed',
                            reason=format_delivery_reason(f"第 {unit_index} 个发货单元消息已发送，但发送后处理异常: {unit_error_text}", rule_meta.get('order_spec_mode'), rule_meta.get('rule_spec_mode'), rule_meta.get('item_config_mode'))
                        )
                        unit_results.append({'unit_index': unit_index, 'status': 'pending_finalize', 'error': unit_error_text})

            progress_summary_after = xianyu_instance._sync_order_delivery_progress(
                order_id=order_id,
                cookie_id=cookie_id,
                expected_quantity=expected_quantity,
                context="手动发货发送成功"
            )
            order_event_hub.publish_order_update_event(order_id, source='manual_delivery')

            finalized_now = [r for r in unit_results if r.get('status') == 'finalized']
            pending_finalize_now = [r for r in unit_results if r.get('status') == 'pending_finalize']
            failed_now = [r for r in unit_results if r.get('status') == 'failed']

            message_parts = []
            if finalize_completed_units > 0:
                message_parts.append(f"已补完成 {finalize_completed_units} 个未收尾单元")
            if finalized_now:
                message_parts.append(f"本次补发成功 {len(finalized_now)} 个单元")
            if pending_finalize_now:
                message_parts.append(f"仍有 {len(pending_finalize_now)} 个单元待收尾")
            if failed_now:
                message_parts.append(f"仍有 {len(failed_now)} 个单元补发失败")

            aggregate_status = progress_summary_after.get('aggregate_status')
            if aggregate_status == 'shipped':
                message_parts.append(f"订单已全部完成（{progress_summary_after.get('finalized_count', 0)}/{expected_quantity}）")
            elif aggregate_status == 'partial_pending_finalize':
                message_parts.append(
                    f"订单当前为部分待收尾（已完成 {progress_summary_after.get('finalized_count', 0)}/{expected_quantity}，待收尾 {progress_summary_after.get('pending_finalize_count', 0)}）"
                )
            elif aggregate_status == 'partial_success':
                message_parts.append(
                    f"订单当前为部分发货（已完成 {progress_summary_after.get('finalized_count', 0)}/{expected_quantity}，待补发 {progress_summary_after.get('remaining_count', 0)}）"
                )

            delivered = bool(finalized_now or finalize_completed_units > 0)
            if not message_parts:
                message_parts.append("订单当前没有可推进的发货单元")

            return {"success": True, "delivered": delivered, "message": '，'.join(message_parts)}

        except Exception as e:
            reply_server.log_with_user('error', f"手动发货异常: 订单 {order_id} - {str(e)}", current_user)
            import traceback
            logger.error(f"手动发货异常堆栈: {traceback.format_exc()}")
            return {"success": False, "delivered": False, "message": f"发货失败: {str(e)}"}

    @router.post('/api/orders/{order_id}/refresh')
    async def refresh_order_status(order_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """刷新订单状态 - 从闲鱼平台获取最新订单状态"""
        try:
            import cookie_manager

            user_id = current_user['user_id']
            reply_server.log_with_user('info', f"刷新订单状态请求: 订单 {order_id}", current_user)

            # 获取订单信息
            order = db_manager.db_manager.get_order_by_id(order_id)
            if not order:
                return {"success": False, "updated": False, "message": "订单不存在"}

            old_status = order.get('order_status', '')

            # 验证订单属于当前用户
            cookie_id = order.get('cookie_id')
            if not cookie_id:
                return {"success": False, "updated": False, "message": "订单缺少账号信息"}

            cookie_info = db_manager.db_manager.get_cookie_details(cookie_id)
            if not cookie_info or cookie_info.get('user_id') != user_id:
                return {"success": False, "updated": False, "message": "无权操作此订单"}

            # 获取 XianyuLive 实例
            xianyu_instance = cookie_manager.manager.get_xianyu_instance(cookie_id) if cookie_manager.manager else None
            if not xianyu_instance:
                return {"success": False, "updated": False, "message": f"账号 {cookie_id} 未运行，请先启动账号"}

            # 获取订单详情（强制从闲鱼平台获取最新信息，跳过缓存）
            item_id = order.get('item_id')
            buyer_id = order.get('buyer_id')
            sid = order.get('sid')

            result = await xianyu_instance.fetch_order_detail_info(
                order_id=order_id,
                item_id=item_id,
                buyer_id=buyer_id,
                sid=sid,
                force_refresh=True  # 强制刷新，跳过缓存
            )

            if result:
                # 获取更新后的订单信息
                updated_order = db_manager.db_manager.get_order_by_id(order_id)
                new_status = updated_order.get('order_status', '') if updated_order else ''
                status_changed = old_status != new_status
                reply_server.log_with_user('info', f"刷新订单状态成功: 订单 {order_id}, 状态: {old_status} -> {new_status}", current_user)
                return {
                    "success": True,
                    "updated": status_changed,
                    "new_status": new_status,
                    "message": f"状态已更新: {new_status}" if status_changed else "订单状态无变化"
                }
            else:
                reply_server.log_with_user('warning', f"刷新订单状态失败: 订单 {order_id}", current_user)
                return {"success": False, "updated": False, "message": "获取订单详情失败，请稍后重试"}

        except Exception as e:
            reply_server.log_with_user('error', f"刷新订单状态异常: 订单 {order_id} - {str(e)}", current_user)
            import traceback
            logger.error(f"刷新订单状态异常堆栈: {traceback.format_exc()}")
            return {"success": False, "updated": False, "message": f"刷新失败: {str(e)}"}

    @router.get('/api/chat/sessions')
    async def get_chat_sessions(
        cookie_id: str = None,
        include_order_fallback: bool = True,
        limit: int = 100,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """获取指定账号的会话列表"""
        try:
            if not cookie_id:
                raise HTTPException(status_code=400, detail="缺少 cookie_id 参数")
            cookie_id = reply_server._ensure_cookie_access(cookie_id, current_user)
            sessions = db_manager.db_manager.get_chat_sessions(cookie_id, limit=min(limit, 200))
            logger.info(
                f"获取聊天会话列表: cookie_id={cookie_id}, local_sessions={len(sessions)}, include_order_fallback={include_order_fallback}, limit={limit}"
            )
            if include_order_fallback:
                fallback_sessions = reply_server._build_chat_sessions_from_recent_orders(cookie_id, limit=min(max(limit, 50), 300))
                logger.info(f"聊天会话列表订单兜底结果: cookie_id={cookie_id}, fallback_sessions={len(fallback_sessions)}")
                sessions = reply_server._merge_chat_sessions_with_order_fallback(sessions, fallback_sessions, limit=min(max(limit, 50), 300))
                logger.info(f"聊天会话列表合并结果: cookie_id={cookie_id}, merged_sessions={len(sessions)}")
            sessions = reply_server._annotate_chat_sessions(cookie_id, sessions)
            sessions = await reply_server._enrich_chat_sessions(cookie_id, sessions, limit=min(max(limit, 20), 30))
            return {'success': True, 'sessions': sessions}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取会话列表失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=500, detail="获取会话列表失败")

    @router.get('/api/chat/messages')
    async def get_chat_messages(
        cookie_id: str = None,
        chat_id: str = None,
        limit: int = 50,
        before_id: int = None,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """获取指定会话的消息列表（仅读本地 DB，新消息走 /api/chat/stream 实时推送）"""
        try:
            if not cookie_id or not chat_id:
                raise HTTPException(status_code=400, detail="缺少 cookie_id 或 chat_id 参数")
            cookie_id = reply_server._ensure_cookie_access(cookie_id, current_user)
            messages = db_manager.db_manager.get_chat_messages(cookie_id, chat_id, limit=min(limit, 100), before_id=before_id)
            return {'success': True, 'messages': messages}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取聊天消息失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=500, detail="获取聊天消息失败")

    @router.post('/api/chat/send')
    async def chat_send_message(
        req: ChatSendRequest,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """在线客服发送消息"""
        try:
            cookie_id = reply_server._ensure_cookie_access(req.cookie_id, current_user)

            from XianyuAutoAsync import XianyuLive, ConnectionState
            live_instance = XianyuLive.get_instance(cookie_id)
            if not live_instance:
                raise HTTPException(status_code=400, detail="账号未启动")
            if live_instance.connection_state != ConnectionState.CONNECTED:
                raise HTTPException(status_code=400, detail="账号WebSocket未连接")
            if not live_instance.ws:
                raise HTTPException(status_code=400, detail="WebSocket连接未就绪")

            await reply_server._run_live_instance_on_manager_loop(
                cookie_id,
                lambda: live_instance.send_msg(
                    live_instance.ws, req.chat_id, req.to_user_id, req.message
                ),
                timeout=15,
            )

            # 闲鱼通过 sendByReceiverScope 发出的消息，WebSocket 不会以"自己发出"形式
            # 稳定回推给同一连接，导致前端在线客服看不到自己刚发的消息。这里仿照
            # XianyuAutoAsync.py 手动发出分支，主动落库 + publish 一次；并 mark
            # 去重标记，避免闲鱼真的回推时再重复一条。
            try:
                from chat_event_hub import publish_chat_message, self_send_dedup
                myid = getattr(live_instance, 'myid', None) or ''
                sender_name = cookie_id
                try:
                    detail = db_manager.db_manager.get_cookie_details(cookie_id) or {}
                    sender_name = detail.get('remark') or detail.get('username') or cookie_id
                except Exception:
                    pass

                _msg_id_db = db_manager.db_manager.save_chat_message(
                    cookie_id=cookie_id, chat_id=req.chat_id,
                    sender_id=str(myid), sender_name=str(sender_name),
                    content=req.message, content_type=1,
                    image_url=None, item_id=None,
                    direction=1, reply_source='手动',
                    media_url=None, link_url=None, extra_json=None,
                )
                publish_chat_message(cookie_id, {
                    'msg_id': _msg_id_db, 'chat_id': req.chat_id,
                    'sender_id': str(myid), 'sender_name': str(sender_name),
                    'content': req.message, 'content_type': 1,
                    'image_url': None,
                    'item_id': None, 'direction': 1, 'reply_source': '手动',
                    'media_url': None, 'link_url': None, 'extra_json': None,
                })
                self_send_dedup.mark(cookie_id, req.chat_id, str(myid), req.message)
            except Exception as e:
                logger.debug(f"客服 Web 发送后回显落库失败: {reply_server.mask_sensitive_text(e)}")

            return {'success': True, 'message': '发送成功'}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"客服发送消息失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=500, detail="发送消息失败")

    @router.get('/api/chat/stream')
    def stream_chat_messages(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """聊天消息实时事件流"""
        user_id = current_user['user_id']
        subscriber = chat_event_hub.subscribe(user_id)

        def event_generator():
            try:
                yield reply_server.format_sse_event('stream.ready', {'type': 'stream.ready', 'timestamp': int(time.time() * 1000)})
                while True:
                    try:
                        event = subscriber.get(timeout=25)
                        yield reply_server.format_sse_event(event.get('type', 'chat.message'), event)
                    except queue.Empty:
                        yield reply_server.format_sse_event('ping', {'type': 'ping', 'timestamp': int(time.time() * 1000)})
            finally:
                chat_event_hub.unsubscribe(user_id, subscriber)

        return StreamingResponse(
            event_generator(),
            media_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            }
        )

    @router.get('/api/chat/accounts')
    def get_chat_accounts(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取当前用户的所有账号列表（在线客服三栏布局用）"""
        try:
            user_cookies = reply_server._get_user_cookies_map(current_user)
            accounts = []
            for cid in user_cookies.keys():
                status = reply_server._build_live_runtime_status(cid)
                detail = db_manager.db_manager.get_cookie_details(cid) or {}
                display_name = detail.get('remark') or detail.get('username') or cid
                accounts.append({
                    'id': cid,
                    'name': display_name,
                    'enabled': db_manager.db_manager.get_cookie_status(cid),
                    'connected': status.get('connection_state') == 'connected' if status else False,
                })
            return {'success': True, 'accounts': accounts}
        except Exception as e:
            logger.error(f"获取聊天账号列表失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=500, detail="获取账号列表失败")

    @router.get('/api/chat/keywords/{cid}/item/{item_id}')
    def get_item_keywords(
        cid: str, item_id: str,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """获取指定商品的关键词列表"""
        try:
            cid = reply_server._ensure_cookie_access(cid, current_user)
            keywords = db_manager.db_manager.get_keywords_by_item_id(cid, item_id)
            item_reply_data = db_manager.db_manager.get_item_reply(cid, item_id)
            item_reply = item_reply_data.get('reply_content') if item_reply_data else None
            return {'success': True, 'keywords': keywords, 'item_reply': item_reply}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取商品关键词失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=500, detail="获取商品关键词失败")

    @router.post('/api/chat/keywords/{cid}/item/{item_id}')
    def save_item_keywords(
        cid: str, item_id: str,
        req: SaveItemKeywordsRequest,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """保存指定商品的关键词和指定商品回复"""
        try:
            cid = reply_server._ensure_cookie_access(cid, current_user)
            success = db_manager.db_manager.save_keywords_for_item(cid, item_id, req.keywords)
            if req.item_reply is not None:
                reply_content = str(req.item_reply or '').strip()
                if reply_content:
                    db_manager.db_manager.update_item_reply(cid, item_id, reply_content)
                else:
                    db_manager.db_manager.delete_item_reply(cid, item_id)
            return {'success': success, 'count': len(req.keywords)}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"保存商品关键词失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=500, detail="保存商品关键词失败")

    @router.post('/api/chat/keywords/{cid}/copy')
    def copy_item_keywords(
        cid: str,
        req: CopyKeywordsRequest,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """复制商品关键词和指定商品回复到其他商品"""
        try:
            cid = reply_server._ensure_cookie_access(cid, current_user)
            results = {}
            source_reply = db_manager.db_manager.get_item_reply(cid, req.source_item_id)
            source_reply_content = source_reply.get('reply_content', '') if source_reply else ''

            for target in req.target_item_ids:
                if target == req.source_item_id:
                    continue
                count = db_manager.db_manager.copy_keywords_to_item(cid, req.source_item_id, target)
                results[target] = count
                if source_reply_content:
                    db_manager.db_manager.update_item_reply(cid, target, source_reply_content)

            return {'success': True, 'results': results, 'total': sum(results.values())}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"复制商品关键词失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=500, detail="复制商品关键词失败")

    @router.get('/api/chat/items/{cid}')
    def get_account_items(
        cid: str,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """获取账号下的商品列表（用于复制回复的目标选择）"""
        try:
            cid = reply_server._ensure_cookie_access(cid, current_user)
            cursor = db_manager.db_manager.conn.cursor()
            db_manager.db_manager._execute_sql(cursor, """
                SELECT item_id, item_title FROM item_info
                WHERE cookie_id = ? ORDER BY item_id
            """, (cid,))
            rows = cursor.fetchall()
            items = [{'item_id': r[0], 'item_title': r[1]} for r in rows]
            return {'success': True, 'items': items}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取商品列表失败: {reply_server.mask_sensitive_text(e)}")
            raise HTTPException(status_code=500, detail="获取商品列表失败")

    @router.get('/', response_class=HTMLResponse)
    async def root():
        login_path = os.path.join(state.STATIC_DIR, 'login.html')
        if os.path.exists(login_path):
            with open(login_path, 'r', encoding='utf-8') as f:
                return HTMLResponse(f.read())
        else:
            return HTMLResponse('<h3>Login page not found</h3>')

    @router.get('/admin', response_class=HTMLResponse)
    async def admin_page():
        index_path = os.path.join(state.STATIC_DIR, 'index.html')
        if not os.path.exists(index_path):
            return HTMLResponse('<h3>No front-end found</h3>')
    
        # 获取静态文件的修改时间作为版本号，解决浏览器缓存问题
        def get_file_version(file_path, default='1.0.0'):
            """获取文件的版本号（基于修改时间）"""
            if os.path.exists(file_path):
                try:
                    mtime = os.path.getmtime(file_path)
                    return str(int(mtime))
                except Exception as e:
                    logger.warning(f"获取文件 {file_path} 修改时间失败: {e}")
            return default
    
        app_js_path = os.path.join(state.STATIC_DIR, 'js', 'app.js')
        app_css_path = os.path.join(state.STATIC_DIR, 'css', 'app.css')
    
        js_version = get_file_version(app_js_path, '2.2.0')
        css_version = get_file_version(app_css_path, '1.0.0')
    
        try:
            with open(index_path, 'r', encoding='utf-8-sig') as f:
                html_content = f.read()
            
                # 替换 app.js 的版本号参数
                js_pattern = r'/static/js/app\.js\?v=[^"\'\s>]+'
                js_new_url = f'/static/js/app.js?v={js_version}'
                if re.search(js_pattern, html_content):
                    html_content = re.sub(js_pattern, js_new_url, html_content)
                    logger.debug(f"已替换 app.js 版本号: {js_version}")
            
                # 为 app.css 添加或更新版本号参数
                css_pattern = r'/static/css/app\.css(\?v=[^"\'\s>]+)?'
                css_new_url = f'/static/css/app.css?v={css_version}'
                html_content = re.sub(css_pattern, css_new_url, html_content)
            
                return HTMLResponse(html_content, headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"})
        except Exception as e:
            logger.error(f"读取或处理 index.html 失败: {e}")
            return HTMLResponse('<h3>Error loading page</h3>')

    @router.get('/download', response_class=HTMLResponse)
    async def download_page():
        download_path = os.path.join(state.STATIC_DIR, 'download.html')
        if os.path.exists(download_path):
            with open(download_path, 'r', encoding='utf-8') as f:
                return HTMLResponse(f.read())
        else:
            return HTMLResponse('<h3>Download page not found</h3>')

    return router
