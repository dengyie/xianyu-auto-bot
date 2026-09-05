"""自动发货/交付内容/卡密 API（自 XianyuAutoAsync.py 拆出，P2-x 步骤④d）。

方法经 self/cls 操作宿主实例状态；XianyuAutoAsync 模块级剩余符号经 `_host`
代理调用时解析；db_manager 逐方法保留原 seam（惰性导入=包属性，否则=宿主绑定）。
"""
import asyncio
import json
import re
import time
from typing import Any, Dict, Optional, Tuple

from loguru import logger


class _HostProxy:
    """属性访问转发到 XianyuAutoAsync 模块级符号（调用时解析）。"""

    def __getattr__(self, name):
        import XianyuAutoAsync

        return getattr(XianyuAutoAsync, name)


_host = _HostProxy()


def _db_package():
    """惰性包属性：等价于原方法体内的 from db_manager import db_manager。"""
    from db_manager import db_manager

    return db_manager


def _db_host():
    """宿主绑定：等价于原模块级 from-import 名字（import 期绑定）。"""
    import XianyuAutoAsync

    return XianyuAutoAsync.db_manager


class DeliveryMixin:
    """自动发货/交付内容/卡密 API方法簇。"""

    def _resolve_delivery_log_buyer_nick(self, buyer_nick: Any = None, *, order_id: str = None,
                                         buyer_id: str = None, log_prefix: str = "") -> Optional[str]:
        """为发货日志优先选择可信的买家昵称，避免写入系统卡片标题。"""
        from db_manager import db_manager

        normalized_order_id = str(order_id).strip() if order_id else None
        normalized_buyer_id = str(buyer_id).strip() if buyer_id else None

        try:
            if normalized_order_id:
                order_info = _db_package().get_order_by_id(normalized_order_id)
                if order_info:
                    order_cookie_id = str(order_info.get("cookie_id") or "").strip()
                    if not order_cookie_id or order_cookie_id == str(self.cookie_id).strip():
                        order_buyer_nick = self._sanitize_buyer_nick(
                            order_info.get("buyer_nick"),
                            source="delivery_log_order",
                            log_prefix=log_prefix,
                        )
                        if order_buyer_nick:
                            return order_buyer_nick

                    if not normalized_buyer_id:
                        normalized_buyer_id = str(order_info.get("buyer_id") or "").strip() or None

            if normalized_buyer_id:
                recent_order = _db_package().get_recent_order_by_buyer_id(
                    normalized_buyer_id,
                    cookie_id=self.cookie_id,
                    minutes=60,
                )
                if recent_order:
                    recent_buyer_nick = self._sanitize_buyer_nick(
                        recent_order.get("buyer_nick"),
                        source="delivery_log_recent_order",
                        log_prefix=log_prefix,
                    )
                    if recent_buyer_nick:
                        return recent_buyer_nick
        except Exception as resolve_error:
            logger.warning(f"{log_prefix} 发货日志买家昵称解析失败: {self._safe_str(resolve_error)}")

        return self._sanitize_buyer_nick(
            buyer_nick,
            source="delivery_log_raw",
            log_prefix=log_prefix,
        )
    def can_auto_delivery(self, order_id: str) -> bool:
        """检查是否可以进行自动发货（防重复发货）- 基于订单ID"""
        if not order_id:
            # 如果没有订单ID，则不进行冷却检查，允许发货
            return True

        current_time = time.time()
        last_delivery = self.last_delivery_time.get(order_id, 0)

        if current_time - last_delivery < self.delivery_cooldown:
            logger.info(f"【{self.cookie_id}】订单 {order_id} 在冷却期内，跳过自动发货")
            return False

        return True
    def mark_delivery_sent(self, order_id: str, context: str = "自动发货完成"):
        """标记订单已发货"""
        self.delivery_sent_orders.add(order_id)
        self.last_delivery_time[order_id] = time.time()
        logger.info(f"【{self.cookie_id}】订单 {order_id} 已标记为发货")
        
        # 更新订单状态为已发货
        logger.info(f"【{self.cookie_id}】检查自动发货订单状态处理器: handler_exists={self.order_status_handler is not None}")
        if self.order_status_handler:
            logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.handle_auto_delivery_order_status: {order_id}")
            try:
                success = self.order_status_handler.handle_auto_delivery_order_status(
                    order_id=order_id,
                    cookie_id=self.cookie_id,
                    context=context
                )
                logger.info(f"【{self.cookie_id}】订单状态处理器.handle_auto_delivery_order_status返回结果: {success}")
                if success:
                    logger.info(f"【{self.cookie_id}】订单 {order_id} 状态已更新为已发货")
                else:
                    logger.warning(f"【{self.cookie_id}】订单 {order_id} 状态更新为已发货失败")
            except Exception as e:
                logger.error(f"【{self.cookie_id}】订单状态更新失败: {self._safe_str(e)}")
                import traceback
                logger.error(f"【{self.cookie_id}】详细错误信息: {traceback.format_exc()}")
        else:
            logger.warning(f"【{self.cookie_id}】订单状态处理器为None，跳过自动发货状态更新: {order_id}")
    def _activate_delivery_lock(self, lock_key: str, delay_minutes: int = 10):
        """在发货成功后激活订单延迟锁，避免重复发货。"""
        if not lock_key:
            return

        existing_lock = self._lock_hold_info.get(lock_key)
        if existing_lock and existing_lock.get('locked'):
            return

        self._lock_hold_info[lock_key] = {
            'locked': True,
            'lock_time': time.time(),
            'release_time': None,
            'task': None
        }
        delay_task = asyncio.create_task(self._delayed_lock_release(lock_key, delay_minutes=delay_minutes))
        self._lock_hold_info[lock_key]['task'] = delay_task
    def _record_delivery_log(self, order_id: str = None, item_id: str = None, buyer_id: str = None,
                             buyer_nick: str = None, status: str = 'failed', reason: str = None,
                             channel: str = 'auto', rule_meta: dict = None):
        """记录真实发货事件日志（成功/失败）。"""
        try:
            from db_manager import db_manager
            meta = rule_meta or {}
            log_prefix = f"【{self.cookie_id}】"
            resolved_buyer_nick = self._resolve_delivery_log_buyer_nick(
                buyer_nick,
                order_id=order_id,
                buyer_id=buyer_id,
                log_prefix=log_prefix,
            )
            normalized_status = str(status or 'failed').strip().lower()
            if normalized_status not in {'success', 'failed', 'skipped'}:
                normalized_status = 'failed'
            _db_package().create_delivery_log(
                user_id=self.user_id,
                cookie_id=self.cookie_id,
                order_id=order_id,
                item_id=item_id,
                buyer_id=buyer_id,
                buyer_nick=resolved_buyer_nick,
                rule_id=meta.get('rule_id'),
                rule_keyword=meta.get('rule_keyword'),
                card_type=meta.get('card_type'),
                match_mode=meta.get('match_mode'),
                channel=channel or 'auto',
                status=normalized_status,
                reason=self._format_delivery_log_reason(reason, meta)
            )
        except Exception as log_e:
            logger.error(f"【{self.cookie_id}】记录发货日志失败: {self._safe_str(log_e)}")
    def _format_delivery_log_reason(self, reason: str = None, rule_meta: dict = None) -> str:
        """将规格模式上下文拼接到发货日志原因中，便于后续排查。"""
        meta = rule_meta or {}
        context_parts = []

        order_spec_mode = meta.get('order_spec_mode')
        rule_spec_mode = meta.get('rule_spec_mode')
        item_config_mode = meta.get('item_config_mode')

        if order_spec_mode:
            context_parts.append(f"order_spec_mode={order_spec_mode}")
        if rule_spec_mode:
            context_parts.append(f"rule_spec_mode={rule_spec_mode}")
        if item_config_mode:
            context_parts.append(f"item_config_mode={item_config_mode}")

        reason_text = (reason or '').strip()
        if not context_parts:
            return reason_text

        if any(part.split('=')[0] + '=' in reason_text for part in context_parts):
            return reason_text

        if not reason_text:
            reason_text = '未提供发货日志原因'

        return f"{reason_text} [{', '.join(context_parts)}]"
    def _get_pending_delivery_finalization_meta(self, order_id: str, delivery_unit_index: int = 1):
        if not order_id:
            return None

        from db_manager import db_manager
        state = _db_package().get_delivery_finalization_state(order_id, delivery_unit_index)
        if not state or state.get('status') != 'sent':
            return None

        delivery_meta = state.get('delivery_meta') or {}
        delivery_meta.setdefault('success', True)
        delivery_meta.setdefault('delivery_unit_index', delivery_unit_index)
        return delivery_meta
    def _mark_delivery_platform_confirm_no_longer_required(self, order_id: str, item_id: str, buyer_id: str,
                                                           delivery_meta: dict = None, reason: str = '',
                                                           channel: str = 'auto') -> None:
        """平台已处于不可/无需补确认的终态时，清理待补确认，避免无限重试。"""
        if not order_id:
            return
        meta = dict(delivery_meta or {})
        meta.update({
            'pending_confirm': False,
            'pending_platform_confirm': False,
            'confirm_retry_required': False,
            'platform_confirm_status': 'not_required_terminal',
            'confirm_retry_stopped': True,
            'confirm_retry_stopped_reason': str(reason or '订单已处于平台终态，无需继续补确认'),
            'confirm_retry_stopped_at': _host.datetime.now().isoformat(timespec='seconds'),
        })
        self._persist_delivery_finalization_state(
            order_id=order_id,
            item_id=item_id,
            buyer_id=buyer_id,
            delivery_meta=meta,
            channel=channel or 'auto',
            status='finalized',
            last_error=str(reason or '订单已处于平台终态，无需继续补确认'),
        )
        logger.warning(f"【{self.cookie_id}】订单 {order_id} 停止补确认重试: {reason or '订单已处于平台终态'}")
    def _mark_delivery_pending_platform_confirm(self, order_id: str, item_id: str, buyer_id: str,
                                                delivery_meta: dict = None, confirm_error: str = None,
                                                expected_quantity: int = 1,
                                                context: str = "平台确认发货失败，等待补确认",
                                                channel: str = 'auto'):
        """记录“卡券已发出、平台确认失败、等待补确认”的订单状态。"""
        if not order_id:
            return None

        error_text = str(confirm_error or '平台确认发货失败').strip()
        pending_reason = f"卡券已发出，平台确认发货失败，等待补确认: {error_text}"
        meta = dict(delivery_meta or {})
        meta.update({
            'success': True,
            'delivery_message_status': 'sent',
            'platform_confirm_status': 'failed',
            'pending_confirm': True,
            'pending_platform_confirm': True,
            'confirm_retry_required': True,
            'confirm_error': error_text,
            'confirm_failed_at': _host.datetime.now().isoformat(timespec='seconds'),
        })

        self._persist_delivery_finalization_state(
            order_id=order_id,
            item_id=item_id,
            buyer_id=buyer_id,
            delivery_meta=meta,
            channel=channel,
            status='sent',
            last_error=pending_reason
        )
        summary = self._sync_order_delivery_progress(
            order_id=order_id,
            cookie_id=self.cookie_id,
            expected_quantity=expected_quantity,
            context=context
        )
        logger.warning(f"【{self.cookie_id}】订单 {order_id} 已记录为：卡券已发出、平台确认失败、等待补确认。原因: {error_text}")
        self._schedule_auth_recovery_after_platform_confirm_failure(order_id=order_id, error=error_text)
        return summary
    def _persist_delivery_finalization_state(self, order_id: str, item_id: str, buyer_id: str,
                                             delivery_meta: dict = None, channel: str = 'auto',
                                             status: str = 'sent', last_error: str = None) -> bool:
        if not order_id:
            return False

        from db_manager import db_manager
        meta = delivery_meta or {}
        unit_index = int(meta.get('delivery_unit_index') or 1)
        return _db_package().upsert_delivery_finalization_state(
            order_id=order_id,
            unit_index=unit_index,
            cookie_id=self.cookie_id,
            item_id=item_id,
            buyer_id=buyer_id,
            channel=channel,
            status=status,
            delivery_meta=meta,
            last_error=last_error,
        )
    def _summarize_delivery_progress(self, order_id: str, expected_quantity: int = 1):
        if not order_id:
            return {
                'order_id': order_id,
                'expected_quantity': max(1, int(expected_quantity or 1)),
                'aggregate_status': 'pending_ship',
                'finalized_count': 0,
                'pending_finalize_count': 0,
                'remaining_count': max(1, int(expected_quantity or 1)),
                'finalized_unit_indexes': [],
                'pending_finalize_unit_indexes': [],
                'remaining_unit_indexes': list(range(1, max(1, int(expected_quantity or 1)) + 1)),
                'states': [],
            }

        from db_manager import db_manager
        return _db_package().get_delivery_progress_summary(order_id, expected_quantity=expected_quantity)
    def _has_bargain_success_evidence(self, order: dict = None) -> bool:
        order = order or {}
        return bool(order.get('bargain_success_detected'))
    def _apply_bargain_amount_override(self, order_id: str, item_id: str, amount: Any, amount_source: str,
                                       existing_order: dict = None, item_config: dict = None):
        existing_order = existing_order or {}
        if not existing_order.get('bargain_flow_detected'):
            return amount, amount_source

        configured_amount = self._normalize_order_amount_text(item_config.get('item_price') if item_config else None)
        configured_amount_value = self._parse_order_amount_float(configured_amount)
        if configured_amount_value is None:
            return amount, amount_source

        incoming_amount = self._normalize_order_amount_text(amount)
        incoming_amount_value = self._parse_order_amount_float(incoming_amount)

        if incoming_amount_value is None:
            logger.warning(
                f"【{self.cookie_id}】小刀订单缺少可信金额，回退为商品配置价: "
                f"order_id={order_id}, item_id={item_id}, configured_amount={configured_amount}"
            )
            return configured_amount, 'bargain_item_price_locked'

        if incoming_amount_value > configured_amount_value + 0.009:
            logger.warning(
                f"【{self.cookie_id}】检测到小刀订单仍返回原价，使用商品配置价覆盖: "
                f"order_id={order_id}, item_id={item_id}, incoming_amount={incoming_amount}, "
                f"configured_amount={configured_amount}, amount_source={amount_source}"
            )
            return configured_amount, 'bargain_item_price_locked'

        return incoming_amount, amount_source
    def _has_delivery_progress_evidence(self, order_id: str) -> bool:
        normalized_order_id = str(order_id or '').strip()
        if not normalized_order_id:
            return False

        try:
            summary = self._summarize_delivery_progress(normalized_order_id, expected_quantity=1) or {}
        except Exception as summary_error:
            logger.warning(
                f"【{self.cookie_id}】读取订单发货进度失败，按已有发货证据处理: "
                f"order_id={normalized_order_id}, error={self._safe_str(summary_error)}"
            )
            return True

        state_count = int(summary.get('state_count') or 0)
        finalized_count = int(summary.get('finalized_count') or 0)
        pending_finalize_count = int(summary.get('pending_finalize_count') or 0)
        return state_count > 0 or finalized_count > 0 or pending_finalize_count > 0
    def _is_auto_delivery_trigger(self, message: str) -> bool:
        """检查消息是否为自动发货触发关键字"""
        # 定义所有自动发货触发关键字
        auto_delivery_keywords = [
            # 系统消息
            '[我已付款，等待你发货]',
            '[已付款，待发货]',
            '我已付款，等待你发货',
            '[记得及时发货]',
        ]

        # 检查消息是否包含任何触发关键字
        for keyword in auto_delivery_keywords:
            if keyword in message:
                return True

        return False
    async def _handle_auto_delivery(self, websocket, message: dict, send_user_name: str, send_user_id: str,
                                   item_id: str, chat_id: str, msg_time: str, message_data: dict = None):
        """统一处理自动发货逻辑
        
        Args:
            message_data: 原始的WebSocket消息数据，用于提取订单ID时的备用搜索
        """
        try:
            from db_manager import db_manager

            # 检查商品是否属于当前cookies
            if item_id and item_id != "未知商品":
                try:
                    if not await self._ensure_item_owned_by_current_account(
                        item_id,
                        log_prefix=f'[{msg_time}] 【{self.cookie_id}】'
                    ):
                        logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 商品 {item_id} 不属于当前账号，跳过自动发货')
                        self._record_delivery_log(
                            item_id=item_id,
                            buyer_id=send_user_id,
                            buyer_nick=send_user_name,
                            status='failed',
                            reason='商品不属于当前账号，跳过自动发货',
                            channel='auto'
                        )
                        return
                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】✅ 商品 {item_id} 归属验证通过')
                except Exception as e:
                    logger.error(f'[{msg_time}] 【{self.cookie_id}】检查商品归属失败: {self._safe_str(e)}，跳过自动发货')
                    self._record_delivery_log(
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason=f'检查商品归属失败: {self._safe_str(e)}',
                        channel='auto'
                    )
                    return

            if self._check_buyer_blacklist_for_action(
                buyer_id=send_user_id,
                item_id=item_id,
                buyer_nick=send_user_name,
                action='自动发货',
                channel='auto',
                log_delivery=True,
            ):
                return

            # 提取订单ID（传递原始消息数据以便在解密消息中找不到时进行备用搜索）
            order_id = self._extract_order_id(message, message_data)

            # 如果order_id不存在，尝试通过sid进行兜底查单
            if not order_id:
                fallback_sid = None
                try:
                    message_1 = message.get('1', {}) if isinstance(message, dict) else {}
                    if isinstance(message_1, dict):
                        # 优先使用会话字段
                        fallback_sid = message_1.get('2', '')

                        # 备用：从reminderUrl里解析sid
                        if not fallback_sid:
                            message_10 = message_1.get('10', {})
                            if isinstance(message_10, dict):
                                reminder_url = message_10.get('reminderUrl', '') or ''
                                sid_match = re.search(r'[?&]sid=([^&]+)', reminder_url)
                                if sid_match:
                                    fallback_sid = sid_match.group(1)
                except Exception as sid_e:
                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】解析sid失败: {self._safe_str(sid_e)}')

                if fallback_sid:
                    try:
                        log_prefix = f'[{msg_time}] 【{self.cookie_id}】'
                        sid_lookup_minutes = 5
                        sid_lookup = self._lookup_delivery_order_by_sid(
                            fallback_sid,
                            minutes=sid_lookup_minutes,
                            log_prefix=log_prefix
                        )
                        sid_lookup = await self._refresh_sid_lookup_if_needed(
                            fallback_sid,
                            sid_lookup,
                            item_id=item_id,
                            buyer_id=send_user_id,
                            minutes=sid_lookup_minutes,
                            allow_bargain_ready=True,
                            log_prefix=log_prefix
                        )
                    except Exception as sid_query_e:
                        logger.error(f'[{msg_time}] 【{self.cookie_id}】sid兜底查单异常: {self._safe_str(sid_query_e)}')
                        sid_lookup = {'match_type': 'error', 'order': None}

                    recent_order = sid_lookup.get('order')
                    sid_match_type = sid_lookup.get('match_type', 'missing')

                    if recent_order and sid_match_type in {'pending_ship', 'bargain_ready'}:
                        fallback_order_id = recent_order.get('order_id')
                        fallback_item_id = recent_order.get('item_id')
                        fallback_buyer_id = recent_order.get('buyer_id')

                        # 防串单：买家不一致直接拒绝（仅当 DB 中的 buyer_id 可信时才校验）
                        if send_user_id and fallback_buyer_id and self._is_trustworthy_buyer_id(fallback_buyer_id) and str(send_user_id) != str(fallback_buyer_id):
                            logger.warning(
                                f'[{msg_time}] 【{self.cookie_id}】❌ sid兜底命中订单但买家不一致，已拒绝发货: '
                                f'send_user_id={send_user_id}, order_buyer_id={fallback_buyer_id}, sid={fallback_sid}'
                            )
                            return

                        # 防串单：商品不一致直接拒绝
                        if item_id and item_id != "未知商品" and fallback_item_id and str(item_id) != str(fallback_item_id):
                            logger.warning(
                                f'[{msg_time}] 【{self.cookie_id}】❌ sid兜底命中订单但商品不一致，已拒绝发货: '
                                f'message_item_id={item_id}, order_item_id={fallback_item_id}, sid={fallback_sid}'
                            )
                            return

                        order_id = fallback_order_id
                        if (not item_id or item_id == "未知商品") and fallback_item_id:
                            item_id = fallback_item_id

                        if sid_match_type == 'bargain_ready':
                            logger.info(
                                f'[{msg_time}] 【{self.cookie_id}】✅ 订单ID提取失败，但检测到小刀成功证据，'
                                f'使用sid兜底直接进入自动发货: sid={fallback_sid}, order_id={order_id}'
                            )

                        logger.info(
                            f'[{msg_time}] 【{self.cookie_id}】✅ 订单ID提取失败，已通过sid兜底定位订单: '
                            f'sid={fallback_sid}, order_id={order_id}, item_id={item_id}'
                        )
                    elif recent_order:
                        fallback_order_id = recent_order.get('order_id')
                        fallback_status = recent_order.get('order_status') or 'unknown'
                        if sid_match_type == 'already_processed':
                            logger.info(
                                f'[{msg_time}] 【{self.cookie_id}】ℹ️ 订单ID提取失败，但sid命中的订单已处理完成，跳过重复发货: '
                                f'sid={fallback_sid}, order_id={fallback_order_id}, status={fallback_status}'
                            )
                        elif sid_match_type == 'cancelled':
                            logger.info(
                                f'[{msg_time}] 【{self.cookie_id}】ℹ️ 订单ID提取失败，但sid命中的订单已关闭，跳过自动发货: '
                                f'sid={fallback_sid}, order_id={fallback_order_id}'
                            )
                        else:
                            logger.info(
                                f'[{msg_time}] 【{self.cookie_id}】ℹ️ 订单ID提取失败，但sid命中的订单当前状态不适合兜底发货，等待后续完整消息: '
                                f'sid={fallback_sid}, order_id={fallback_order_id}, status={fallback_status}'
                            )
                        return
                    else:
                        logger.warning(
                            f'[{msg_time}] 【{self.cookie_id}】❌ 未能提取到订单ID，sid兜底也未命中待发货订单，跳过自动发货 '
                            f'(sid={fallback_sid})'
                        )
                        self._record_delivery_log(
                            item_id=item_id,
                            buyer_id=send_user_id,
                            buyer_nick=send_user_name,
                            status='failed',
                            reason=f'未能提取订单ID且sid未命中待发货订单: sid={fallback_sid}',
                            channel='auto'
                        )
                        return
                else:
                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 未能提取到订单ID且无可用sid，跳过自动发货')
                    self._record_delivery_log(
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='未能提取到订单ID且无可用sid，跳过自动发货',
                        channel='auto'
                    )
                    return

            # 订单ID已提取，将在自动发货时进行确认发货处理
            # 防串单：对直接提取/兜底后的订单进行一致性校验
            try:
                existing_order = _db_package().get_order_by_id(order_id)
            except Exception as order_check_e:
                logger.error(f'[{msg_time}] 【{self.cookie_id}】查询订单一致性校验失败: {self._safe_str(order_check_e)}')
                existing_order = None

            if existing_order:
                existing_buyer_id = existing_order.get('buyer_id')
                existing_item_id = existing_order.get('item_id')

                if send_user_id and existing_buyer_id and self._is_trustworthy_buyer_id(existing_buyer_id) and str(send_user_id) != str(existing_buyer_id):
                    logger.warning(
                        f'[{msg_time}] 【{self.cookie_id}】❌ 订单与当前会话买家不一致，拒绝自动发货: '
                        f'order_id={order_id}, send_user_id={send_user_id}, order_buyer_id={existing_buyer_id}'
                    )
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='订单与当前会话买家不一致，拒绝自动发货',
                        channel='auto'
                    )
                    return

                if item_id and item_id != "未知商品" and existing_item_id and str(item_id) != str(existing_item_id):
                    logger.warning(
                        f'[{msg_time}] 【{self.cookie_id}】❌ 订单与当前会话商品不一致，拒绝自动发货: '
                        f'order_id={order_id}, message_item_id={item_id}, order_item_id={existing_item_id}'
                    )
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='订单与当前会话商品不一致，拒绝自动发货',
                        channel='auto'
                    )
                    return

                if (not item_id or item_id == "未知商品") and existing_item_id:
                    item_id = existing_item_id
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】订单一致性校验补全商品ID: {item_id}')

            if self._check_buyer_blacklist_for_action(
                buyer_id=send_user_id,
                item_id=item_id,
                order_id=order_id,
                buyer_nick=send_user_name,
                action='自动发货',
                channel='auto',
                log_delivery=True,
            ):
                return

            logger.info(f'[{msg_time}] 【{self.cookie_id}】提取到订单ID: {order_id}，将在自动发货时处理确认发货')

            # 使用订单ID作为锁的键
            lock_key = order_id

            # 第一重检查：延迟锁状态（在获取锁之前检查，避免不必要的等待）
            if self.is_lock_held(lock_key):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】🔒【提前检查】订单 {lock_key} 延迟锁仍在持有状态，跳过发货')
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=send_user_id,
                    buyer_nick=send_user_name,
                    status='failed',
                    reason='订单延迟锁持有中，跳过发货',
                    channel='auto'
                )
                return

            # 第二重检查：基于时间的冷却机制
            if not self.can_auto_delivery(order_id):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】订单 {order_id} 在冷却期内，跳过发货')
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=send_user_id,
                    buyer_nick=send_user_name,
                    status='failed',
                    reason='订单在冷却期内，跳过发货',
                    channel='auto'
                )
                return

            # 获取或创建该订单的锁
            order_lock = self._order_locks[lock_key]

            # 更新锁的使用时间
            self._lock_usage_times[lock_key] = time.time()

            # 使用异步锁防止同一订单的并发处理
            async with order_lock:
                logger.info(f'[{msg_time}] 【{self.cookie_id}】获取订单锁成功: {lock_key}，开始处理自动发货')

                # 第三重检查：获取锁后再次检查延迟锁状态（双重检查，防止在等待锁期间状态发生变化）
                if self.is_lock_held(lock_key):
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】订单 {lock_key} 在获取锁后检查发现延迟锁仍持有，跳过发货')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='获取锁后发现延迟锁仍持有，跳过发货',
                        channel='auto'
                    )
                    return

                # 第四重检查：获取锁后再次检查冷却状态
                if not self.can_auto_delivery(order_id):
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】订单 {order_id} 在获取锁后检查发现仍在冷却期，跳过发货')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='获取锁后发现订单仍在冷却期，跳过发货',
                        channel='auto'
                    )
                    return

                # 构造用户URL
                user_url = f'https://www.goofish.com/personal?userId={send_user_id}'

                # 自动发货逻辑
                try:
                    # 设置默认标题（将通过API获取真实商品信息）
                    item_title = "待获取商品信息"

                    logger.info(f"【{self.cookie_id}】准备自动发货: item_id={item_id}, item_title={item_title}")

                    # 检查是否需要多数量发货
                    from db_manager import db_manager
                    quantity_to_send = 1  # 默认发送1个

                    # 检查商品是否开启了多数量发货
                    multi_quantity_delivery = _db_package().get_item_multi_quantity_delivery_status(self.cookie_id, item_id)

                    if multi_quantity_delivery and order_id:
                        logger.info(f"商品 {item_id} 开启了多数量发货，获取订单详情...")
                        try:
                            # 使用现有方法获取订单详情
                            order_detail = await self.fetch_order_detail_info(order_id, item_id, send_user_id)
                            if order_detail and order_detail.get('quantity'):
                                try:
                                    order_quantity = int(order_detail['quantity'])
                                    if order_quantity > 1:
                                        quantity_to_send = order_quantity
                                        logger.info(f"从订单详情获取数量: {order_quantity}，将发送 {quantity_to_send} 个卡券")
                                    else:
                                        logger.info(f"订单数量为 {order_quantity}，发送单个卡券")
                                except (ValueError, TypeError):
                                    logger.warning(f"订单数量格式无效: {order_detail.get('quantity')}，发送单个卡券")
                            else:
                                logger.info(f"未获取到订单数量信息，发送单个卡券")
                        except Exception as e:
                            logger.error(f"获取订单详情失败: {self._safe_str(e)}，发送单个卡券")
                    elif not multi_quantity_delivery:
                        logger.info(f"商品 {item_id} 未开启多数量发货，发送单个卡券")
                    else:
                        logger.info(f"无订单ID，发送单个卡券")

                    successful_send_count = 0
                    last_delivery_error = None
                    prepared_units = []

                    for i in range(quantity_to_send):
                        unit_index = i + 1
                        rule_meta = {}
                        try:
                            pending_finalize_meta = self._get_pending_delivery_finalization_meta(order_id, unit_index)
                            if pending_finalize_meta:
                                finalize_result = await self._finalize_delivery_after_send(
                                    delivery_meta=pending_finalize_meta,
                                    order_id=order_id,
                                    item_id=item_id
                                )
                                if not finalize_result.get('success'):
                                    last_delivery_error = finalize_result.get('error') or f"第 {unit_index} 个卡券补完成收尾失败"
                                    if self._is_platform_confirm_failure_error(last_delivery_error):
                                        self._mark_delivery_pending_platform_confirm(
                                            order_id=order_id,
                                            item_id=item_id,
                                            buyer_id=send_user_id,
                                            delivery_meta=pending_finalize_meta,
                                            confirm_error=last_delivery_error,
                                            expected_quantity=self._get_order_expected_delivery_quantity(order_id),
                                            context=f"第{unit_index}个卡券补完成收尾平台确认失败"
                                        )
                                    else:
                                        self._persist_delivery_finalization_state(
                                            order_id=order_id,
                                            item_id=item_id,
                                            buyer_id=send_user_id,
                                            delivery_meta=pending_finalize_meta,
                                            channel='auto',
                                            status='sent',
                                            last_error=last_delivery_error
                                        )
                                    self._record_delivery_log(
                                        order_id=order_id,
                                        item_id=item_id,
                                        buyer_id=send_user_id,
                                        buyer_nick=send_user_name,
                                        status='failed',
                                        reason=last_delivery_error if not self._is_platform_confirm_failure_error(last_delivery_error) else f'卡券已发出，等待补确认: {last_delivery_error}',
                                        channel='auto',
                                        rule_meta=pending_finalize_meta
                                    )
                                    logger.error(last_delivery_error)
                                    continue

                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    delivery_meta=pending_finalize_meta,
                                    channel='auto',
                                    status='finalized'
                                )
                                successful_send_count += 1

                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='success',
                                    reason='检测到发货消息已发送，本次补完成收尾成功',
                                    channel='auto',
                                    rule_meta=pending_finalize_meta
                                )
                                continue

                            delivery_result = await self._auto_delivery(
                                item_id,
                                item_title,
                                order_id,
                                send_user_id,
                                chat_id,
                                send_user_name,
                                include_meta=True,
                                delivery_unit_index=unit_index
                            )

                            if isinstance(delivery_result, dict):
                                delivery_content = delivery_result.get('content')
                                delivery_error = delivery_result.get('error')
                                delivery_steps = delivery_result.get('delivery_steps') or []
                                rule_meta = {
                                    'success': True,
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

                            if not delivery_content:
                                failure_reason = delivery_error or f"第 {unit_index}/{quantity_to_send} 个卡券内容获取失败"
                                last_delivery_error = failure_reason
                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='failed',
                                    reason=failure_reason,
                                    channel='auto',
                                    rule_meta=rule_meta
                                )
                                logger.warning(failure_reason)
                                continue

                            if not delivery_steps:
                                delivery_steps = self._build_delivery_steps(delivery_content, rule_meta.get('card_description', ''))
                            if not delivery_steps:
                                failure_reason = f"第 {unit_index}/{quantity_to_send} 个卡券发货步骤构建失败"
                                last_delivery_error = failure_reason
                                self._release_data_reservation_if_needed(rule_meta, error=failure_reason)
                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='failed',
                                    reason=failure_reason,
                                    channel='auto',
                                    rule_meta=rule_meta
                                )
                                logger.error(failure_reason)
                                continue

                            prepared_units.append({
                                'unit_index': unit_index,
                                'delivery_steps': delivery_steps,
                                'rule_meta': rule_meta,
                                'card_type': rule_meta.get('card_type'),
                            })

                        except Exception as e:
                            self._release_data_reservation_if_needed(rule_meta, error=f'准备发货失败: {self._safe_str(e)}')
                            last_delivery_error = f"准备第 {unit_index}/{quantity_to_send} 个卡券失败: {self._safe_str(e)}"
                            self._record_delivery_log(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=send_user_id,
                                buyer_nick=send_user_name,
                                status='failed',
                                reason=last_delivery_error,
                                channel='auto',
                                rule_meta=rule_meta
                            )
                            logger.error(last_delivery_error)

                    send_groups = self._build_delivery_send_groups(prepared_units, quantity_to_send)
                    total_send_groups = len(send_groups)

                    for group_index, send_group in enumerate(send_groups, start=1):
                        group_units = send_group.get('units') or []
                        if not group_units:
                            continue

                        first_unit = group_units[0]
                        single_unit_index = first_unit.get('unit_index') or 1
                        is_batched_text_group = send_group.get('mode') == 'batched_text'

                        if is_batched_text_group:
                            group_log_prefix = (
                                f'[{msg_time}] 多数量自动发货批次 {group_index}/{total_send_groups} '
                                f'({len(group_units)}个单元, {send_group.get("char_count", 0)}字)'
                            )
                        else:
                            group_log_prefix = f'[{msg_time}] 多数量自动发货 {single_unit_index}/{quantity_to_send}'

                        try:
                            await self._send_delivery_steps(
                                websocket,
                                chat_id,
                                send_user_id,
                                send_group.get('delivery_steps') or [],
                                user_url=user_url,
                                log_prefix=group_log_prefix
                            )
                        except Exception as e:
                            group_error = self._safe_str(e)
                            for prepared_unit in group_units:
                                unit_rule_meta = prepared_unit.get('rule_meta') or {}
                                unit_index = prepared_unit.get('unit_index') or 1
                                self._release_data_reservation_if_needed(
                                    unit_rule_meta,
                                    error=f'发送失败(unit={unit_index}): {group_error}'
                                )
                                last_delivery_error = f"发送第 {unit_index}/{quantity_to_send} 个卡券失败: {group_error}"
                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='failed',
                                    reason=last_delivery_error,
                                    channel='auto',
                                    rule_meta=unit_rule_meta
                                )
                                logger.error(last_delivery_error)
                            continue

                        for prepared_unit in group_units:
                            unit_rule_meta = prepared_unit.get('rule_meta') or {}
                            unit_index = prepared_unit.get('unit_index') or 1
                            unit_delivery_steps = prepared_unit.get('delivery_steps') or []

                            try:
                                if not self._mark_data_reservation_sent_if_needed(unit_rule_meta):
                                    self._release_data_reservation_if_needed(
                                        unit_rule_meta,
                                        error=f'发送成功后标记预占已发送失败(unit={unit_index})'
                                    )
                                    last_delivery_error = f'第 {unit_index} 个卡券发送成功后标记预占已发送失败'
                                    self._record_delivery_log(
                                        order_id=order_id,
                                        item_id=item_id,
                                        buyer_id=send_user_id,
                                        buyer_nick=send_user_name,
                                        status='failed',
                                        reason=last_delivery_error,
                                        channel='auto',
                                        rule_meta=unit_rule_meta
                                    )
                                    logger.error(last_delivery_error)
                                    continue

                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    delivery_meta=unit_rule_meta,
                                    channel='auto',
                                    status='sent'
                                )

                                finalize_result = await self._finalize_delivery_after_send(
                                    delivery_meta=unit_rule_meta,
                                    order_id=order_id,
                                    item_id=item_id
                                )
                                if not finalize_result.get('success'):
                                    last_delivery_error = finalize_result.get('error') or f"第 {unit_index} 条消息发送成功但提交发货副作用失败"
                                    if self._is_platform_confirm_failure_error(last_delivery_error):
                                        self._mark_delivery_pending_platform_confirm(
                                            order_id=order_id,
                                            item_id=item_id,
                                            buyer_id=send_user_id,
                                            delivery_meta=unit_rule_meta,
                                            confirm_error=last_delivery_error,
                                            expected_quantity=self._get_order_expected_delivery_quantity(order_id),
                                            context=f"第{unit_index}条消息发送后平台确认失败"
                                        )
                                    else:
                                        self._persist_delivery_finalization_state(
                                            order_id=order_id,
                                            item_id=item_id,
                                            buyer_id=send_user_id,
                                            delivery_meta=unit_rule_meta,
                                            channel='auto',
                                            status='sent',
                                            last_error=last_delivery_error
                                        )
                                    self._record_delivery_log(
                                        order_id=order_id,
                                        item_id=item_id,
                                        buyer_id=send_user_id,
                                        buyer_nick=send_user_name,
                                        status='failed',
                                        reason=last_delivery_error if not self._is_platform_confirm_failure_error(last_delivery_error) else f'卡券已发出，等待补确认: {last_delivery_error}',
                                        channel='auto',
                                        rule_meta=unit_rule_meta
                                    )
                                    logger.error(last_delivery_error)
                                    continue

                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    delivery_meta=unit_rule_meta,
                                    channel='auto',
                                    status='finalized'
                                )

                                successful_send_count += 1

                                has_image_step = any(step.get('type') == 'image' for step in unit_delivery_steps)
                                if has_image_step:
                                    success_reason = '自动发货图片步骤发送成功'
                                elif is_batched_text_group and len(group_units) > 1:
                                    success_reason = '自动发货文本批量合并发送成功'
                                else:
                                    success_reason = '自动发货文本发送成功'

                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='success',
                                    reason=success_reason,
                                    channel='auto',
                                    rule_meta=unit_rule_meta
                                )
                            except Exception as unit_post_error:
                                last_delivery_error = f"第 {unit_index} 个卡券消息已发送，但发送后处理异常: {self._safe_str(unit_post_error)}"
                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    delivery_meta=unit_rule_meta,
                                    channel='auto',
                                    status='sent',
                                    last_error=last_delivery_error
                                )
                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='failed',
                                    reason=last_delivery_error,
                                    channel='auto',
                                    rule_meta=unit_rule_meta
                                )
                                logger.error(last_delivery_error)

                        if total_send_groups > 1 and group_index < total_send_groups:
                            await asyncio.sleep(1)

                    progress_summary = self._sync_order_delivery_progress(
                        order_id=order_id,
                        cookie_id=self.cookie_id,
                        expected_quantity=quantity_to_send,
                        context="自动发货进度同步"
                    ) if order_id else None

                    if progress_summary and progress_summary.get('aggregate_status') in {'partial_success', 'partial_pending_finalize', 'shipped'}:
                        self._activate_delivery_lock(lock_key, delay_minutes=10)

                    if successful_send_count > 0:
                        if progress_summary and quantity_to_send > 1:
                            aggregate_status = progress_summary.get('aggregate_status')
                            finalized_count = progress_summary.get('finalized_count', 0)
                            pending_finalize_count = progress_summary.get('pending_finalize_count', 0)
                            remaining_count = progress_summary.get('remaining_count', 0)

                            if aggregate_status == 'partial_pending_finalize':
                                notify_message = (
                                    f"多数量发货部分完成，已完成 {finalized_count}/{quantity_to_send}，"
                                    f"待收尾 {pending_finalize_count}，待补发 {remaining_count}"
                                )
                            elif aggregate_status == 'partial_success':
                                notify_message = (
                                    f"多数量发货部分成功，已完成 {finalized_count}/{quantity_to_send}，"
                                    f"待补发 {remaining_count}"
                                )
                            else:
                                notify_message = f"多数量发货成功，共完成 {finalized_count}/{quantity_to_send} 个卡券"
                            await self.send_delivery_failure_notification(send_user_name, send_user_id, item_id, notify_message, chat_id, order_id=order_id)
                        else:
                            await self.send_delivery_failure_notification(send_user_name, send_user_id, item_id, "发货成功", chat_id, order_id=order_id)
                    else:
                        logger.warning(f'[{msg_time}] 【自动发货】未找到匹配的发货规则或获取发货内容失败')
                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=send_user_id,
                            buyer_nick=send_user_name,
                            status='failed',
                            reason=last_delivery_error or "未找到匹配的发货规则或获取发货内容失败",
                            channel='auto'
                        )
                        await self.send_delivery_failure_notification(send_user_name, send_user_id, item_id, last_delivery_error or "未找到匹配的发货规则或获取发货内容失败", chat_id, order_id=order_id)

                except Exception as e:
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason=f"自动发货处理异常: {self._safe_str(e)}",
                        channel='auto'
                    )
                    logger.error(f"自动发货处理异常: {self._safe_str(e)}")
                    # 发送自动发货异常通知
                    await self.send_delivery_failure_notification(send_user_name, send_user_id, item_id, f"自动发货处理异常: {str(e)}", chat_id, order_id=order_id)

                logger.info(f'[{msg_time}] 【{self.cookie_id}】订单锁释放: {lock_key}，自动发货处理完成')

        except Exception as e:
            self._record_delivery_log(
                item_id=item_id,
                buyer_id=send_user_id,
                buyer_nick=send_user_name,
                status='failed',
                reason=f"统一自动发货处理异常: {self._safe_str(e)}",
                channel='auto'
            )
            logger.error(f"统一自动发货处理异常: {self._safe_str(e)}")
    async def _update_card_image_url(self, card_id: int, new_image_url: str):
        """更新卡券的图片URL"""
        try:
            from db_manager import db_manager
            success = _db_package().update_card_image_url(card_id, new_image_url)
            if success:
                logger.info(f"卡券图片URL已更新: 卡券ID={card_id} -> {new_image_url}")
            else:
                logger.warning(f"卡券图片URL更新失败: 卡券ID={card_id}")
        except Exception as e:
            logger.error(f"更新卡券图片URL失败: {e}")
    def _resolve_delivery_notification_buyer_name(
        self,
        buyer_name: Any = None,
        *,
        buyer_id: str = None,
        chat_id: str = None,
        order_id: str = None,
        log_prefix: str = "",
    ) -> str:
        """为自动发货通知解析可信买家昵称，避免使用“等待你发货”等系统标题。"""
        normalized_buyer_id = self._normalize_buyer_id_value(buyer_id)
        normalized_chat_id = str(chat_id or '').strip()

        try:
            if order_id:
                order_info = _db_host().get_order_by_id(str(order_id).strip())
                if order_info:
                    order_cookie_id = str(order_info.get('cookie_id') or '').strip()
                    if not order_cookie_id or order_cookie_id == str(self.cookie_id).strip():
                        order_buyer_nick = self._sanitize_buyer_nick(
                            order_info.get('buyer_nick'),
                            source='delivery_notification_order',
                            log_prefix=log_prefix,
                        )
                        if order_buyer_nick:
                            return order_buyer_nick

                        if not normalized_buyer_id:
                            normalized_buyer_id = self._normalize_buyer_id_value(order_info.get('buyer_id'))

                        if not normalized_chat_id:
                            sid = str(order_info.get('sid') or '').strip()
                            normalized_chat_id = sid.split('@')[0].strip() if sid else ''

            if normalized_chat_id:
                chat_messages = _db_host().get_chat_messages(self.cookie_id, normalized_chat_id, limit=80)
                for chat_message in reversed(chat_messages or []):
                    if int(chat_message.get('direction') or 0) != 2:
                        continue

                    sender_id = self._normalize_buyer_id_value(chat_message.get('sender_id'))
                    if sender_id and sender_id == self.myid:
                        continue
                    if normalized_buyer_id and sender_id and sender_id != normalized_buyer_id:
                        continue

                    chat_buyer_nick = self._sanitize_buyer_nick(
                        chat_message.get('sender_name'),
                        source='delivery_notification_chat',
                        log_prefix=log_prefix,
                    )
                    if chat_buyer_nick:
                        return chat_buyer_nick

            if normalized_buyer_id:
                recent_order = _db_host().get_recent_order_by_buyer_id(
                    normalized_buyer_id,
                    cookie_id=self.cookie_id,
                    minutes=24 * 60,
                )
                if recent_order:
                    recent_buyer_nick = self._sanitize_buyer_nick(
                        recent_order.get('buyer_nick'),
                        source='delivery_notification_recent_order',
                        log_prefix=log_prefix,
                    )
                    if recent_buyer_nick:
                        return recent_buyer_nick
        except Exception as resolve_error:
            logger.warning(f"{log_prefix} 自动发货通知买家昵称解析失败: {self._safe_str(resolve_error)}")

        fallback_buyer_name = self._sanitize_buyer_nick(
            buyer_name,
            source='delivery_notification_raw',
            log_prefix=log_prefix,
        )
        return fallback_buyer_name or '买家'
    async def _auto_delivery(self, item_id: str, item_title: str = None, order_id: str = None, send_user_id: str = None,
                             chat_id: str = None, send_user_name: str = None, include_meta: bool = False,
                             data_preview_index: int = 0, delivery_unit_index: int = 1):
        """自动发货功能 - 匹配规则并准备发货内容，不直接提交副作用。"""
        try:
            matched_rule_context = None
            match_mode_context = None

            def build_result(success: bool, content: str = None, error: str = None, matched_rule: dict = None,
                             match_mode_value: str = None, delivery_steps_value: list = None):
                order_spec_mode_value = 'no_spec'
                item_config_mode_value = 'no_spec'
                rule_spec_mode_value = None

                try:
                    order_spec_mode_value = _get_order_spec_mode()
                except Exception:
                    pass

                try:
                    rule_spec_mode_value = _get_rule_spec_mode(matched_rule) if matched_rule else None
                except Exception:
                    pass

                try:
                    item_config_mode_value = 'spec_enabled' if item_config_multi_spec else 'no_spec'
                except Exception:
                    pass

                if include_meta:
                    return {
                        "success": bool(success),
                        "content": content if success else None,
                        "error": error if not success else None,
                        "rule_id": matched_rule.get('id') if matched_rule else None,
                        "rule_keyword": matched_rule.get('keyword') if matched_rule else None,
                        "card_type": matched_rule.get('card_type') if matched_rule else None,
                        "match_mode": match_mode_value,
                        "order_spec_mode": order_spec_mode_value,
                        "rule_spec_mode": rule_spec_mode_value,
                        "item_config_mode": item_config_mode_value,
                        "card_id": matched_rule.get('card_id') if matched_rule else None,
                        "card_description": matched_rule.get('card_description') if matched_rule else None,
                        "delivery_steps": delivery_steps_value or [],
                        "data_card_pending_consume": False,
                        "data_line": None,
                        "data_reservation_id": None,
                        "data_reservation_status": None,
                        "delivery_unit_index": delivery_unit_index
                    }
                return content if success else None

            from db_manager import db_manager

            logger.info(f"开始自动发货检查: 商品ID={item_id}")

            # 获取商品详细信息
            item_info = None
            search_text = item_title  # 默认使用传入的标题

            if item_id and item_id != "未知商品":
                # 直接从数据库获取商品信息（发货时不再调用API）
                try:
                    logger.info(f"从数据库获取商品信息: {item_id}")
                    db_item_info = _db_package().get_item_info(self.cookie_id, item_id)
                    if db_item_info:
                        item_info = db_item_info
                        # 拼接商品标题和详情作为搜索文本
                        item_title_db = db_item_info.get('item_title', '') or ''
                        item_detail_db = db_item_info.get('item_detail', '') or ''

                        # 如果数据库中没有详情，尝试自动获取
                        if not item_detail_db.strip():
                            from config import config
                            auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})

                            if auto_fetch_config.get('enabled', True):
                                logger.info(f"数据库中商品详情为空，尝试自动获取: {item_id}")
                                try:
                                    fetched_detail = await self.fetch_item_detail_from_api(item_id)
                                    if fetched_detail:
                                        # 保存获取到的详情
                                        await self.save_item_detail_only(item_id, fetched_detail)
                                        item_detail_db = fetched_detail
                                        logger.info(f"成功获取并保存商品详情: {item_id}")
                                    else:
                                        logger.warning(f"未能获取到商品详情: {item_id}")
                                except Exception as api_e:
                                    logger.warning(f"获取商品详情失败: {item_id}, 错误: {self._safe_str(api_e)}")
                            else:
                                logger.warning(f"自动获取商品详情功能已禁用，跳过: {item_id}")

                        # 组合搜索文本：商品标题 + 商品详情
                        search_parts = []
                        if item_title_db.strip():
                            search_parts.append(item_title_db.strip())
                        if item_detail_db.strip():
                            search_parts.append(item_detail_db.strip())

                        if search_parts:
                            search_text = ' '.join(search_parts)
                            logger.info(f"使用数据库商品标题+详情作为搜索文本: 标题='{item_title_db}', 详情长度={len(item_detail_db)}")
                            logger.warning(f"完整搜索文本: {search_text[:200]}...")
                        else:
                            logger.warning(f"数据库中商品标题和详情都为空: {item_id}")
                            search_text = item_title or item_id
                    else:
                        logger.warning(f"数据库中未找到商品信息: {item_id}")
                        search_text = item_title or item_id

                except Exception as db_e:
                    logger.warning(f"从数据库获取商品信息失败: {self._safe_str(db_e)}")
                    search_text = item_title or item_id

            if not search_text:
                search_text = item_id or "未知商品"

            logger.info(f"使用搜索文本匹配发货规则: {search_text[:100]}...")

            item_config_multi_spec = _db_package().get_item_multi_spec_status(self.cookie_id, item_id)
            spec_name = ''
            spec_value = ''
            spec_name_2 = ''
            spec_value_2 = ''

            def _apply_spec_from_order_detail(order_detail_data) -> bool:
                nonlocal spec_name, spec_value, spec_name_2, spec_value_2
                if not order_detail_data or not isinstance(order_detail_data, dict):
                    return False
                spec_name = (order_detail_data.get('spec_name') or '').strip()
                spec_value = (order_detail_data.get('spec_value') or '').strip()
                spec_name_2 = (order_detail_data.get('spec_name_2') or '').strip()
                spec_value_2 = (order_detail_data.get('spec_value_2') or '').strip()
                return bool(spec_name and spec_value)

            def _get_order_spec_mode() -> str:
                has_first_spec = bool(spec_name and spec_value)
                has_second_spec = bool(spec_name_2 and spec_value_2)

                if has_first_spec and has_second_spec:
                    return 'two_spec'
                if has_first_spec:
                    return 'one_spec'
                return 'no_spec'

            def _get_rule_spec_mode(rule: dict) -> str:
                if not rule:
                    return 'no_spec'

                rule_spec_name = (rule.get('spec_name') or '').strip()
                rule_spec_value = (rule.get('spec_value') or '').strip()
                rule_spec_name_2 = (rule.get('spec_name_2') or '').strip()
                rule_spec_value_2 = (rule.get('spec_value_2') or '').strip()

                if rule_spec_name and rule_spec_value and rule_spec_name_2 and rule_spec_value_2:
                    return 'two_spec'
                if rule_spec_name and rule_spec_value:
                    return 'one_spec'
                return 'no_spec'

            # 只要有订单ID就尝试拉取订单详情；规格商品缺失规格时自动重试，提升精确命中率
            if order_id:
                logger.info(f"检测到订单ID，获取订单详情用于规则匹配: {order_id}")
                max_detail_attempts = 3 if item_config_multi_spec else 1
                for attempt in range(1, max_detail_attempts + 1):
                    try:
                        force_refresh = attempt > 1
                        if force_refresh:
                            logger.info(f"订单规格信息缺失，开始强刷重试 ({attempt}/{max_detail_attempts}): {order_id}")

                        order_detail = await self.fetch_order_detail_info(
                            order_id,
                            item_id,
                            send_user_id,
                            force_refresh=force_refresh
                        )

                        if _apply_spec_from_order_detail(order_detail):
                            logger.info(f"获取到规格信息: {spec_name} = {spec_value}")
                            if spec_name_2 and spec_value_2:
                                logger.info(f"获取到规格2信息: {spec_name_2} = {spec_value_2}")
                            break

                        if item_config_multi_spec:
                            logger.warning(
                                f"订单详情已获取但未解析到有效规格信息 (尝试 {attempt}/{max_detail_attempts})"
                            )
                        else:
                            logger.info("无规格商品未解析到规格信息，按普通规则继续")
                    except Exception as e:
                        logger.error(
                            f"获取订单详情失败 (尝试 {attempt}/{max_detail_attempts}): {self._safe_str(e)}"
                        )

                    if attempt < max_detail_attempts:
                        await asyncio.sleep(0.6)

                if _get_order_spec_mode() == 'no_spec':
                    try:
                        cached_order = _db_package().get_order_by_id(order_id)
                        if cached_order and _apply_spec_from_order_detail(cached_order):
                            logger.warning(
                                f"订单 {order_id} 从数据库缓存恢复规格成功: "
                                f"{spec_name}:{spec_value}"
                            )
                    except Exception as cache_e:
                        logger.warning(f"订单缓存规格恢复失败: {self._safe_str(cache_e)}")
            else:
                logger.warning("当前无订单ID，跳过订单详情拉取，将仅基于商品文本匹配规则")

            order_spec_mode = _get_order_spec_mode()
            item_config_mode = 'spec_enabled' if item_config_multi_spec else 'no_spec'

            if order_spec_mode != 'no_spec' and item_info is not None and not item_config_multi_spec:
                logger.warning(
                    f"商品已配置为无规格，忽略订单解析到的规格并按普通规则匹配: "
                    f"order_spec_mode={order_spec_mode}, item_id={item_id or 'unknown'}, "
                    f"order_id={order_id or 'unknown'}, spec={spec_name}:{spec_value}"
                )
                spec_name = ''
                spec_value = ''
                spec_name_2 = ''
                spec_value_2 = ''
                order_spec_mode = _get_order_spec_mode()
            elif order_spec_mode == 'no_spec' and item_config_multi_spec:
                block_reason = (
                    f"商品已开启规格匹配，但订单未解析到有效规格信息，已阻断自动发货: "
                    f"order_id={order_id or 'unknown'}, item_id={item_id or 'unknown'}"
                )
                logger.error(block_reason)
                return build_result(False, error=block_reason, match_mode_value='blocked_no_spec_parsed')

            logger.info(
                f"规格模式判定完成: order_spec_mode={order_spec_mode}, "
                f"item_config_mode={item_config_mode}"
            )

            delivery_rules = []
            if order_spec_mode == 'two_spec':
                match_mode = 'two_spec_exact'
                match_mode_context = match_mode
                logger.info(
                    f"尝试精确匹配两组规格发货规则: {search_text[:50]}... "
                    f"[{spec_name}:{spec_value}, {spec_name_2}:{spec_value_2}]"
                )
                delivery_rules = _db_package().get_delivery_rules_by_keyword_and_spec(
                    search_text,
                    spec_name,
                    spec_value,
                    spec_name_2,
                    spec_value_2,
                    user_id=self.user_id,
                    expected_mode='two_spec'
                )
                if not delivery_rules:
                    error_message = "两组规格订单未找到匹配的发货规则"
                    logger.warning(f"{error_message}: {search_text[:50]}...")
                    return build_result(False, error=error_message, match_mode_value='blocked_no_rule')
            elif order_spec_mode == 'one_spec':
                match_mode = 'one_spec_exact'
                match_mode_context = match_mode
                logger.info(
                    f"尝试精确匹配一组规格发货规则: {search_text[:50]}... "
                    f"[{spec_name}:{spec_value}]"
                )
                delivery_rules = _db_package().get_delivery_rules_by_keyword_and_spec(
                    search_text,
                    spec_name,
                    spec_value,
                    spec_name_2,
                    spec_value_2,
                    user_id=self.user_id,
                    expected_mode='one_spec'
                )
                if not delivery_rules:
                    logger.warning(
                        f"一组规格订单未找到精确规格规则，尝试降级匹配普通发货规则: {search_text[:50]}..."
                    )
                    fallback_rules = _db_package().get_delivery_rules_by_keyword(
                        search_text,
                        user_id=self.user_id,
                        only_non_multi_spec=True
                    )
                    if not fallback_rules:
                        error_message = "一组规格订单未找到匹配的发货规则"
                        logger.warning(f"{error_message}: {search_text[:50]}...")
                        return build_result(False, error=error_message, match_mode_value='blocked_no_rule')
                    if len(fallback_rules) != 1:
                        block_reason = (
                            f"一组规格订单精确匹配失败后，普通规则兜底匹配到{len(fallback_rules)}条，"
                            f"已阻断自动发货以避免错发: order_id={order_id or 'unknown'}, "
                            f"item_id={item_id or 'unknown'}"
                        )
                        logger.error(block_reason)
                        return build_result(False, error=block_reason, match_mode_value='blocked_multiple_no_spec_rules')
                    delivery_rules = fallback_rules
                    match_mode = 'one_spec_fallback_no_spec'
                    match_mode_context = match_mode
                    logger.warning(
                        f"一组规格订单已降级命中唯一普通规则: order_id={order_id or 'unknown'}, "
                        f"item_id={item_id or 'unknown'}, rule_id={delivery_rules[0].get('id')}"
                    )
            else:
                match_mode = 'no_spec_match'
                match_mode_context = match_mode
                logger.info(f"无规格订单，尝试匹配普通发货规则: {search_text[:50]}...")
                delivery_rules = _db_package().get_delivery_rules_by_keyword(
                    search_text,
                    user_id=self.user_id,
                    only_non_multi_spec=True
                )
                if not delivery_rules:
                    error_message = "无规格订单未找到匹配的普通发货规则"
                    logger.warning(f"{error_message}: {search_text[:50]}...")
                    return build_result(False, error=error_message, match_mode_value='blocked_no_rule')
                if len(delivery_rules) != 1:
                    block_reason = (
                        f"无规格订单匹配到{len(delivery_rules)}条普通规则，已阻断自动发货以避免错发: "
                        f"order_id={order_id or 'unknown'}, item_id={item_id or 'unknown'}"
                    )
                    logger.error(block_reason)
                    return build_result(False, error=block_reason, match_mode_value='blocked_multiple_no_spec_rules')

            # 使用第一个匹配的规则（按关键字长度降序排列，优先匹配更精确的规则）
            rule = delivery_rules[0]
            matched_rule_context = rule
            rule_spec_mode = _get_rule_spec_mode(rule)

            logger.info(
                f"规则模式判定完成: order_spec_mode={order_spec_mode}, rule_spec_mode={rule_spec_mode}, "
                f"match_mode={match_mode}, rule_id={rule.get('id')}"
            )

            allow_one_spec_fallback = (
                match_mode == 'one_spec_fallback_no_spec'
                and order_spec_mode == 'one_spec'
                and rule_spec_mode == 'no_spec'
            )

            if rule_spec_mode != order_spec_mode and not allow_one_spec_fallback:
                block_reason = (
                    f"订单规格模式与命中规则模式不一致，已阻断自动发货: "
                    f"order_spec_mode={order_spec_mode}, rule_spec_mode={rule_spec_mode}, "
                    f"order_id={order_id or 'unknown'}, item_id={item_id or 'unknown'}, rule_id={rule.get('id')}"
                )
                logger.error(block_reason)
                return build_result(False, error=block_reason, matched_rule=rule, match_mode_value='blocked_rule_mode_mismatch')

            # 注释掉自动发货时的商品信息保存逻辑，避免重复保存导致item_detail字段内容累积
            # 商品信息应该在商品列表获取、订单详情获取等其他环节已经保存过了
            # 保存商品信息到数据库（需要有商品标题才保存）
            # # 尝试获取商品标题
            # item_title_for_save = None
            # try:
            #     from db_manager import db_manager
            #     db_item_info = db_manager.get_item_info(self.cookie_id, item_id)
            #     if db_item_info:
            #         item_title_for_save = db_item_info.get('item_title', '').strip()
            # except:
            #     pass
            # 
            # # 如果有商品标题，则保存商品信息
            # if item_title_for_save:
            #     await self.save_item_info_to_db(item_id, search_text, item_title_for_save)
            # else:
            #     logger.warning(f"跳过保存商品信息：缺少商品标题 - {item_id}")

            # 详细的匹配结果日志
            if order_spec_mode == 'two_spec':
                rule_spec_info = f"{rule['spec_name']}:{rule['spec_value']}, {rule['spec_name_2']}:{rule['spec_value_2']}"
                order_spec_info = f"{spec_name}:{spec_value}, {spec_name_2}:{spec_value_2}"
                logger.info(f"🎯 精确匹配两组规格发货规则: {rule['keyword']} -> {rule['card_name']} [{rule_spec_info}]")
                logger.info(f"📋 订单规格: {order_spec_info} ✅ 匹配卡券规格: {rule_spec_info}")
            elif match_mode == 'one_spec_fallback_no_spec':
                order_spec_info = f"{spec_name}:{spec_value}"
                logger.warning(
                    f"⚠️ 单规格订单降级匹配普通发货规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})"
                )
                logger.warning(f"📋 订单规格: {order_spec_info}，精确规格未命中，已降级到普通规则")
            elif order_spec_mode == 'one_spec':
                rule_spec_info = f"{rule['spec_name']}:{rule['spec_value']}"
                order_spec_info = f"{spec_name}:{spec_value}"
                logger.info(f"🎯 精确匹配一组规格发货规则: {rule['keyword']} -> {rule['card_name']} [{rule_spec_info}]")
                logger.info(f"📋 订单规格: {order_spec_info} ✅ 匹配卡券规格: {rule_spec_info}")
            else:
                logger.info(f"✅ 匹配无规格发货规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})")

            # 获取延时设置
            delay_seconds = rule.get('card_delay_seconds', 0)

            # 执行延时（只准备内容，不执行确认发货）
            if delay_seconds and delay_seconds > 0:
                logger.info(f"检测到发货延时设置: {delay_seconds}秒，开始延时...")
                await asyncio.sleep(delay_seconds)
                logger.info(f"延时完成")

            # 检查是否存在订单ID，只有存在订单ID才处理发货内容
            if order_id:
                # 保存订单基本信息到数据库（如果还没有详细信息）
                try:
                    from db_manager import db_manager

                    # 过滤掉买家订单（如果send_user_id是自己，说明是自己购买的订单）
                    if send_user_id and send_user_id == self.myid:
                        logger.info(f"【{self.cookie_id}】跳过买家订单 {order_id}，buyer_id={send_user_id} 等于自己的ID")
                        # 不保存买家订单，但继续返回发货内容（如果有的话）
                    else:
                        # 检查cookie_id是否在cookies表中存在
                        cookie_info = _db_package().get_cookie_by_id(self.cookie_id)
                        if not cookie_info:
                            logger.warning(f"Cookie ID {self.cookie_id} 不存在于cookies表中，丢弃订单 {order_id}")
                        else:
                            existing_order = _db_package().get_order_by_id(order_id)
                            if not existing_order:
                                # 插入基本订单信息
                                success = _db_package().insert_or_update_order(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    cookie_id=self.cookie_id
                                )

                                # 使用订单状态处理器设置状态
                                if success and self.order_status_handler:
                                    try:
                                        self.order_status_handler.handle_order_basic_info_status(
                                            order_id=order_id,
                                            cookie_id=self.cookie_id,
                                            context="自动发货-基本信息"
                                        )
                                    except Exception as e:
                                        logger.error(f"【{self.cookie_id}】订单状态处理器调用失败: {self._safe_str(e)}")

                                if success:
                                    logger.info(f"保存基本订单信息到数据库: {order_id}")
                except Exception as db_e:
                    logger.error(f"保存基本订单信息失败: {self._safe_str(db_e)}")

                # 开始处理发货内容
                logger.info(f"开始处理发货内容，规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})")

                delivery_content = None
                data_line = None
                data_reservation = None

                # 根据卡券类型处理发货内容
                if rule['card_type'] == 'api':
                    # API类型：调用API获取内容，传入订单和商品信息用于动态参数替换
                    delivery_content = await self._get_api_card_content(rule, order_id, item_id, send_user_id, spec_name, spec_value)

                elif rule['card_type'] == 'yifan_api':
                    # 亦凡卡劵API类型：调用亦凡API获取内容
                    delivery_content = await self._get_yifan_api_card_content(rule, order_id, item_id, send_user_id, chat_id)

                elif rule['card_type'] == 'text':
                    # 固定文字类型：直接使用文字内容
                    delivery_content = rule['text_content']

                elif rule['card_type'] == 'data':
                    # 批量数据类型：先原子预占，再发送，避免并发订单拿到同一条卡密
                    data_reservation = _db_package().reserve_batch_data(
                        card_id=rule['card_id'],
                        order_id=order_id,
                        unit_index=delivery_unit_index,
                        cookie_id=self.cookie_id,
                        buyer_id=send_user_id,
                    )
                    if data_reservation:
                        data_line = data_reservation.get('reserved_content')
                        delivery_content = data_line
                    else:
                        delivery_content = None

                elif rule['card_type'] == 'image':
                    # 图片类型：返回图片发送标记，包含卡券ID
                    image_url = rule.get('image_url')
                    if image_url:
                        delivery_content = f"__IMAGE_SEND__{rule['card_id']}|{image_url}"
                        logger.info(f"准备发送图片: {image_url} (卡券ID: {rule['card_id']})")
                    else:
                        logger.error(f"图片卡券缺少图片URL: 卡券ID={rule['card_id']}")
                        delivery_content = None

                if delivery_content:
                    delivery_steps = self._build_delivery_steps(delivery_content, rule.get('card_description', ''))
                    if not delivery_steps:
                        logger.warning(f"发货步骤构建失败: 规则ID={rule['id']}")
                        return build_result(False, error=f"发货步骤构建失败: 规则ID={rule['id']}", matched_rule=rule, match_mode_value=match_mode)

                    if len(delivery_steps) == 1 and delivery_steps[0].get('type') == 'text':
                        final_content = delivery_steps[0].get('content') or ''
                    else:
                        final_content = delivery_content

                    logger.info(f"自动发货内容准备成功: 规则ID={rule['id']}, 步骤数={len(delivery_steps)}")

                    result = build_result(
                        True,
                        content=final_content,
                        matched_rule=rule,
                        match_mode_value=match_mode,
                        delivery_steps_value=delivery_steps
                    )
                    if include_meta and isinstance(result, dict):
                        result['card_id'] = rule.get('card_id')
                        result['data_card_pending_consume'] = bool(rule['card_type'] == 'data')
                        result['data_line'] = data_line
                        result['data_reservation_id'] = data_reservation.get('id') if data_reservation else None
                        result['data_reservation_status'] = data_reservation.get('status') if data_reservation else None
                        result['delivery_unit_index'] = delivery_unit_index
                    return result
                else:
                    logger.warning(f"获取发货内容失败: 规则ID={rule['id']}")
                    return build_result(False, error=f"获取发货内容失败: 规则ID={rule['id']}", matched_rule=rule, match_mode_value=match_mode)
            else:
                # 没有订单ID，记录日志但不处理发货内容
                logger.info(f"⚠️ 未检测到订单ID，跳过发货内容处理。规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})")
                return build_result(False, error="未检测到订单ID，跳过发货内容处理", matched_rule=rule, match_mode_value=match_mode)

        except Exception as e:
            error_text = self._safe_str(e)
            if matched_rule_context:
                rule_label = matched_rule_context.get('keyword') or f"规则ID={matched_rule_context.get('id')}"
                card_type = matched_rule_context.get('card_type') or 'unknown'
                error_message = f"规则已命中({rule_label})，但{card_type}发货处理异常: {error_text}"
            else:
                error_message = f"自动发货异常: {error_text}"
            logger.error(error_message)
            return build_result(
                False,
                error=error_message,
                matched_rule=matched_rule_context,
                match_mode_value=match_mode_context
            )
    def _process_delivery_content_with_description(self, delivery_content: str, card_description: str) -> str:
        """处理发货内容和备注信息，实现变量替换"""
        try:
            # 如果没有备注信息，直接返回发货内容
            if not card_description or not card_description.strip():
                return delivery_content

            # 替换备注中的变量
            processed_description = card_description.replace('{DELIVERY_CONTENT}', delivery_content)

            # 如果备注中包含变量替换，返回处理后的备注
            if '{DELIVERY_CONTENT}' in card_description:
                return processed_description
            else:
                # 如果备注中没有变量，将备注和发货内容组合
                return f"{processed_description}\n\n{delivery_content}"

        except Exception as e:
            logger.error(f"处理备注信息失败: {e}")
            # 出错时返回原始发货内容
            return delivery_content
    def _build_delivery_steps(self, delivery_content: str, card_description: str):
        """构建发货步骤，确保图片卡券和备注按正确顺序发送。"""
        try:
            raw_content = delivery_content if isinstance(delivery_content, str) else str(delivery_content or '')
            description = (card_description or '').strip()
            steps = []

            if raw_content and not raw_content.startswith("__IMAGE_SEND__"):
                final_text = self._process_delivery_content_with_description(raw_content, description)
                return [{'type': 'text', 'content': final_text}] if final_text else []

            def append_text_step(text: str):
                text = (text or '').strip()
                if text:
                    steps.append({'type': 'text', 'content': text})

            def append_payload_step(payload: str):
                payload = (payload or '').strip()
                if payload:
                    if payload.startswith("__IMAGE_SEND__"):
                        steps.append({'type': 'image', 'content': payload})
                    else:
                        steps.append({'type': 'text', 'content': payload})

            if not description:
                append_payload_step(raw_content)
                return steps

            if '{DELIVERY_CONTENT}' in description:
                placeholder = '{DELIVERY_CONTENT}'
                segments = description.split(placeholder)
                for index, segment in enumerate(segments):
                    append_text_step(segment)
                    if index < len(segments) - 1:
                        append_payload_step(raw_content)
                return steps

            append_text_step(description)
            append_payload_step(raw_content)
            return steps
        except Exception as e:
            logger.error(f"构建发货步骤失败: {e}")
            fallback_content = delivery_content if isinstance(delivery_content, str) else str(delivery_content or '')
            if fallback_content:
                return [{'type': 'image' if fallback_content.startswith("__IMAGE_SEND__") else 'text', 'content': fallback_content}]
            return []
    def _can_batch_text_delivery(self, delivery_steps, card_type: str = None) -> bool:
        """仅将 text/data/api 的单条纯文本步骤纳入批量合并发送。"""
        normalized_card_type = str(card_type or '').strip().lower()
        if normalized_card_type not in {'text', 'data', 'api'}:
            return False

        steps = delivery_steps or []
        if len(steps) != 1:
            return False

        step = steps[0] or {}
        if step.get('type') != 'text':
            return False

        return bool((step.get('content') or '').strip())
    def _format_delivery_unit_text(self, text: str, unit_index: int, total_units: int) -> str:
        """为批量发货文本添加全局连续序号。"""
        safe_total_units = max(1, int(total_units or 1))
        safe_unit_index = max(1, int(unit_index or 1))
        prefix = f"【{safe_unit_index}/{safe_total_units}】"
        content = (text or '').strip()
        return f"{prefix}{content}" if content else prefix
    def _apply_delivery_unit_numbering(self, delivery_steps, unit_index: int, total_units: int, card_type: str = None):
        """为多数量订单中的 text/data/api 步骤补充序号。"""
        if max(1, int(total_units or 1)) <= 1:
            return delivery_steps or []

        normalized_card_type = str(card_type or '').strip().lower()
        if normalized_card_type not in {'text', 'data', 'api'}:
            return delivery_steps or []

        steps = [dict(step or {}) for step in (delivery_steps or [])]
        prefix = f"【{max(1, int(unit_index or 1))}/{max(1, int(total_units or 1))}】"

        for step in steps:
            if step.get('type') == 'text':
                step['content'] = f"{prefix}{(step.get('content') or '').strip()}"
                return steps

        return [{'type': 'text', 'content': prefix}] + steps
    async def _get_api_card_content(self, rule, order_id=None, item_id=None, buyer_id=None, spec_name=None, spec_value=None, retry_count=0):
        """调用API获取卡券内容，支持动态参数替换和重试机制"""
        max_retries = 4

        if retry_count >= max_retries:
            logger.error(f"API调用失败，已达到最大重试次数({max_retries})")
            return None

        try:
            import aiohttp
            import json

            api_config = rule.get('api_config')
            if not api_config:
                logger.error(f"API配置为空，规则ID: {rule.get('id')}, 卡券名称: {rule.get('card_name')}")
                logger.warning(f"规则详情: {rule}")
                return None

            # 解析API配置
            if isinstance(api_config, str):
                api_config = json.loads(api_config)

            url = api_config.get('url')
            method = api_config.get('method', 'GET').upper()
            timeout = api_config.get('timeout', 10)
            headers = api_config.get('headers', '{}')
            params = api_config.get('params', '{}')

            # 解析headers和params
            if isinstance(headers, str):
                headers = json.loads(headers)
            if isinstance(params, str):
                params = json.loads(params)

            # 有动态参数时进行替换（GET 的 query 参数同样需要，
            # 否则 {order_id} 等占位符会原样发给对方接口）
            if params:
                params = await self._replace_api_dynamic_params(params, order_id, item_id, buyer_id, spec_name, spec_value)

            retry_info = f" (重试 {retry_count + 1}/{max_retries})" if retry_count > 0 else ""
            logger.info(f"调用API获取卡券: {method} {url}{retry_info}")
            if method == 'POST' and params:
                logger.warning(f"POST请求参数: {json.dumps(params, ensure_ascii=False)}")

            # 确保session存在
            if not self.session:
                await self.create_session()

            # 发起HTTP请求
            timeout_obj = _host.aiohttp.ClientTimeout(total=timeout)

            if method == 'GET':
                async with self.session.get(url, headers=headers, params=params, timeout=timeout_obj) as response:
                    status_code = response.status
                    response_text = await response.text()
            elif method == 'POST':
                async with self.session.post(url, headers=headers, json=params, timeout=timeout_obj) as response:
                    status_code = response.status
                    response_text = await response.text()
            else:
                logger.error(f"不支持的HTTP方法: {method}")
                return None

            if status_code == 200:
                # 尝试解析JSON响应，如果失败则使用原始文本
                try:
                    result = json.loads(response_text)
                    # 如果返回的是对象，尝试提取常见的内容字段
                    if isinstance(result, dict):
                        content = result.get('data') or result.get('content') or result.get('card') or str(result)
                    else:
                        content = str(result)
                except Exception:
                    content = response_text

                logger.info(f"API调用成功，返回内容长度: {len(content)}")
                return content
            else:
                logger.warning(f"API调用失败: {status_code} - {response_text[:200]}...")

                # 如果是服务器错误(5xx)或请求超时，进行重试
                if status_code >= 500 or status_code == 408:
                    if retry_count < max_retries - 1:
                        wait_time = (retry_count + 1) * 2  # 递增等待时间: 2s, 4s, 6s
                        logger.info(f"等待 {wait_time} 秒后重试...")
                        await asyncio.sleep(wait_time)
                        return await self._get_api_card_content(rule, order_id, item_id, buyer_id, spec_name, spec_value, retry_count + 1)

                return None

        except (asyncio.TimeoutError, _host.aiohttp.ClientError) as e:
            logger.warning(f"API调用网络异常: {self._safe_str(e)}")

            # 网络异常也进行重试
            if retry_count < max_retries - 1:
                wait_time = (retry_count + 1) * 2  # 递增等待时间
                logger.info(f"等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
                return await self._get_api_card_content(rule, order_id, item_id, buyer_id, spec_name, spec_value, retry_count + 1)
            else:
                logger.error(f"API调用网络异常，已达到最大重试次数: {self._safe_str(e)}")
                return None

        except Exception as e:
            logger.error(f"API调用异常: {self._safe_str(e)}")
            return None
    async def _get_yifan_api_card_content(self, rule, order_id=None, item_id=None, buyer_id=None, chat_id=None):
        """调用亦凡卡劵API获取内容"""
        try:
            import hashlib
            import time
            import aiohttp
            import json
            from urllib.parse import urlencode

            # 获取API配置（存储在api_config字段中）
            api_config = rule.get('api_config')
            if not api_config:
                logger.error(f"亦凡API配置为空，规则ID: {rule.get('id')}, 卡券名称: {rule.get('card_name')}")
                return None

            # 解析API配置
            if isinstance(api_config, str):
                api_config = json.loads(api_config)

            # 亦凡API配置直接存储在api_config字段中
            user_id = api_config.get('user_id')
            user_key = api_config.get('user_key')
            goods_id = api_config.get('goods_id')
            # 回调地址：优先使用卡券配置中的，如果没有则从全局配置读取，最后使用默认地址
            callback_url = (api_config.get('callback_url') or '').strip() or (_host.YIFAN_API.get('callback_url') or '').strip() or 'http://116.196.116.76/yifan.php'
            require_account = api_config.get('require_account', False)

            if not user_id or not user_key or not goods_id:
                logger.error(f"亦凡API配置不完整，规则ID: {rule.get('id')}")
                return None

            # 如果需要充值账号，先进行账号询问和确认流程
            recharge_account = None
            if require_account:
                logger.info(f"亦凡API需要充值账号，开始询问流程")
                recharge_account = await self._ask_for_recharge_account(chat_id, buyer_id, rule, order_id, item_id)
                if recharge_account == "__WAITING_ACCOUNT__":
                    # 已设置等待状态，暂时中断发货流程
                    logger.info(f"已设置等待账号输入状态，暂停发货流程")
                    return None
                elif not recharge_account:
                    logger.error(f"获取充值账号失败，取消发货")
                    return None
                logger.info(f"获取到充值账号: {recharge_account}")

            # 构建API请求参数（所有值都转换为字符串，避免空格问题）
            timestamp = str(int(time.time()))
            params = {
                'userid': str(user_id),
                'timestamp': timestamp,
                'goodsid': str(goods_id),
                'buynum': '1',
            }

            # 如果有回调地址，添加到参数中（签名之前添加）
            if callback_url and callback_url.strip():
                params['callbackurl'] = str(callback_url).strip()

            # 如果有充值账号，添加到参数中
            if recharge_account:
                params['attach'] = str(recharge_account).strip()

            # 生成签名（确保参数值没有空格）
            # 1. 按照key的ascii码从小到大排序
            # 2. 空值不参与签名
            # 3. 使用QueryString格式拼接
            # 4. 尾部追加商户KEY
            # 5. MD5后转成32位小写
            sign_params = {k: str(v).strip() for k, v in params.items() if v is not None and str(v).strip() != ''}
            sorted_keys = sorted(sign_params.keys())
            sign_string = '&'.join([f"{key}={sign_params[key]}" for key in sorted_keys])
            sign_string += user_key
            
            logger.info(f"亦凡API签名字符串: {sign_string}")
            
            sign = _host.hashlib.md5(sign_string.encode('utf-8')).hexdigest().lower()
            params['sign'] = sign

            logger.info(f"调用亦凡API: 商户ID={user_id}, 商品ID={goods_id}, 充值账号={recharge_account}, 回调URL={callback_url if callback_url else '无'}")

            # 确保session存在
            if not self.session:
                await self.create_session()

            # 发起API请求（使用data而不是json，发送form格式）
            api_url = "http://price.78shuk.top/dockapiv3/order/create"
            
            timeout_obj = _host.aiohttp.ClientTimeout(total=30)
            async with self.session.post(api_url, data=params, timeout=timeout_obj) as response:
                status_code = response.status
                response_text = await response.text()

                logger.info(f"亦凡API返回状态码: {status_code}, 响应: {response_text}")

                if status_code == 200:
                    try:
                        result = json.loads(response_text)
                        # 根据亦凡API的返回格式处理：code为1表示成功
                        if result.get('code') == 1:
                            # 提取订单信息
                            data = result.get('data', {})
                            order_no = data.get('orderno', '')
                            us_order_no = data.get('usorderno', '')
                            
                            # 构建成功消息
                            success_msg = f"✅ 自动发货订单已提交成功\n\n"
                            success_msg += f"📋 订单信息：\n"
                            success_msg += f"平台订单号: {order_no}\n"
                            if us_order_no:
                                success_msg += f"商家订单号: {us_order_no}\n"
                            
                            # 添加查询地址（从全局配置读取）
                            query_url = _host.YIFAN_API.get('query_url', 'http://116.196.116.76/yifan.php')
                            success_msg += f"\n🔍 查询卡密：\n"
                            success_msg += f"{query_url}\n"
                            success_msg += f"(输入订单号查询)\n"
                            
                            # 添加提示信息
                            success_msg += f"\n⏰ 温馨提示：\n"
                            success_msg += f"订单处理需要一定时间，请耐心等待。\n"
                            success_msg += f"如果1小时后仍未看到卡密信息，\n"
                            success_msg += f"请联系客服处理。"
                            
                            logger.info(f"亦凡API调用成功: order_no={order_no}")
                            
                            # 将亦凡订单号记录到数据库（用于后续回调匹配）
                            if order_id and order_no:
                                try:
                                    from db_manager import db_manager
                                    # 更新订单的亦凡订单号和chat_id
                                    _db_package().update_order_yifan_status(
                                        order_id=order_id,
                                        yifan_orderno=order_no,
                                        delivery_status='processing'
                                    )
                                    if chat_id:
                                        _db_package().update_order_chat_id(order_id, chat_id)
                                    logger.info(f"已记录亦凡订单信息: order_id={order_id}, yifan_orderno={order_no}")
                                except Exception as e:
                                    logger.error(f"记录亦凡订单信息失败: {e}")
                            
                            return success_msg
                        else:
                            # code不为1，下单失败，需要通知用户
                            error_msg = result.get('msg', '未知错误')
                            logger.error(f"亦凡API调用失败: code={result.get('code')}, msg={error_msg}")
                            
                            # 发送通知给用户
                            if chat_id and buyer_id:
                                from db_manager import db_manager
                                notification_msg = f"❌ 自动发货失败\n错误信息: {error_msg}\n请联系客服处理"
                                await self.send_notification("系统", buyer_id, notification_msg, item_id or "unknown", chat_id)
                            
                            return None
                    except Exception as e:
                        logger.error(f"解析亦凡API返回失败: {self._safe_str(e)}")
                        return None
                else:
                    logger.error(f"亦凡API调用失败: HTTP {status_code} - {response_text[:200]}")
                    return None

        except Exception as e:
            logger.error(f"亦凡API调用异常: {self._safe_str(e)}")
            return None
    async def _call_yifan_api_with_account(self, rule, account, order_id=None, item_id=None, buyer_id=None, chat_id=None):
        """使用确认的账号调用亦凡API"""
        try:
            import hashlib
            import time
            import aiohttp
            import json

            # 获取API配置
            api_config = rule.get('api_config')
            if not api_config:
                logger.error(f"亦凡API配置为空")
                return None

            # 解析API配置
            if isinstance(api_config, str):
                api_config = json.loads(api_config)

            # 亦凡API配置直接存储在api_config字段中
            user_id = api_config.get('user_id')
            user_key = api_config.get('user_key')
            goods_id = api_config.get('goods_id')
            callback_url = api_config.get('callback_url', '')

            if not user_id or not user_key or not goods_id:
                logger.error(f"亦凡API配置不完整")
                return None

            # 构建API请求参数（所有值都转换为字符串，避免空格问题）
            timestamp = str(int(time.time()))
            params = {
                'userid': str(user_id),
                'timestamp': timestamp,
                'goodsid': str(goods_id),
                'buynum': '1',
                'attach': str(account).strip()  # 充值账号，去除首尾空格
            }

            # 如果有回调地址，添加到参数中（签名之前添加）
            if callback_url and callback_url.strip():
                params['callbackurl'] = str(callback_url).strip()

            # 生成签名（确保参数值没有空格）
            sign_params = {k: str(v).strip() for k, v in params.items() if v is not None and str(v).strip() != ''}
            sorted_keys = sorted(sign_params.keys())
            sign_string = '&'.join([f"{key}={sign_params[key]}" for key in sorted_keys])
            sign_string += user_key
            
            logger.info(f"亦凡API签名字符串: {sign_string}")
            
            sign = _host.hashlib.md5(sign_string.encode('utf-8')).hexdigest().lower()
            params['sign'] = sign

            logger.info(f"调用亦凡API: 商户ID={user_id}, 商品ID={goods_id}, 充值账号={account}, 回调URL={callback_url if callback_url else '无'}")

            # 确保session存在
            if not self.session:
                await self.create_session()

            # 发起API请求（使用data而不是json，发送form格式）
            api_url = "http://price.78shuk.top/dockapiv3/order/create"
            
            timeout_obj = _host.aiohttp.ClientTimeout(total=30)
            async with self.session.post(api_url, data=params, timeout=timeout_obj) as response:
                status_code = response.status
                response_text = await response.text()

                logger.info(f"亦凡API返回状态码: {status_code}, 响应: {response_text}")

                if status_code == 200:
                    try:
                        result = json.loads(response_text)
                        if result.get('code') == 1:
                            # 下单成功
                            data = result.get('data', {})
                            order_no = data.get('orderno', '')
                            us_order_no = data.get('usorderno', '')
                            
                            success_msg = f"✅ 下单成功\n"
                            success_msg += f"订单号: {order_no}\n"
                            if us_order_no:
                                success_msg += f"用户订单号: {us_order_no}\n"
                            success_msg += f"充值账号: {account}\n"
                            success_msg += f"返回信息: {result.get('msg', '提交成功')}\n"
                            success_msg += f"有任何问题，请及时联系客服处理。"
                            
                            logger.info(f"亦凡API调用成功: {success_msg}")
                            return success_msg
                        else:
                            # 下单失败
                            error_msg = result.get('msg', '未知错误')
                            logger.error(f"亦凡API调用失败: code={result.get('code')}, msg={error_msg}")
                            
                            # 发送通知给用户
                            if chat_id and buyer_id:
                                from db_manager import db_manager
                                notification_msg = f"❌ 自动发货失败\n错误信息: {error_msg}\n请联系客服处理"
                                await self.send_notification("系统", buyer_id, notification_msg, item_id or "unknown", chat_id)
                            
                            return None
                    except Exception as e:
                        logger.error(f"解析亦凡API返回失败: {self._safe_str(e)}")
                        return None
                else:
                    logger.error(f"亦凡API调用失败: HTTP {status_code} - {response_text[:200]}")
                    return None

        except Exception as e:
            logger.error(f"亦凡API调用异常: {self._safe_str(e)}")
            return None
