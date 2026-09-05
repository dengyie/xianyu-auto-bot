"""XianyuLive 的订单与商品方法簇（自 XianyuAutoAsync.py 拆出，P2-x 步骤④）。

方法经 self/cls 操作宿主实例状态；XianyuAutoAsync 模块级剩余符号
（db_manager、order_status_handler、各种模块函数/常量）通过 `_host` 代理在
调用时解析 —— 兼容测试对宿主模块属性的替换，且无导入环。
"""
import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

def _db_package():
    """惰性包属性：等价于原方法体内的 from db_manager import db_manager（调用时取包现值）。"""
    from db_manager import db_manager

    return db_manager


def _db_host():
    """宿主绑定：等价于原模块级 from-import 名字（import 期绑定）。"""
    import XianyuAutoAsync

    return XianyuAutoAsync.db_manager



class _HostProxy:
    """属性访问转发到 XianyuAutoAsync 模块级符号（调用时解析）。"""

    def __getattr__(self, name):
        import XianyuAutoAsync

        return getattr(XianyuAutoAsync, name)


_host = _HostProxy()


class OrderMixin:
    """订单详情/状态同步/交付恢复方法簇（状态在宿主 XianyuLive 上）。"""

    def _init_order_status_handler(self):
        """初始化订单状态处理器"""
        try:
            # 直接导入订单状态处理器
            from order_status_handler import order_status_handler
            self.order_status_handler = order_status_handler
            logger.info(f"【{self.cookie_id}】订单状态处理器已启用")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】初始化订单状态处理器失败: {self._safe_str(e)}")
            self.order_status_handler = None
    def _lookup_delivery_order_by_sid(self, sid: str, *, minutes: int = 10,
                                      log_prefix: str = "") -> Dict[str, Any]:
        """根据 sid 查找简化发货对应订单，并区分是否已处理/已关闭。"""
        normalized_sid = str(sid or "").strip()
        if not normalized_sid:
            return {"match_type": "missing", "order": None}

        try:
            pending_orders = _db_host().find_recent_orders_by_match_context(
                sid=normalized_sid,
                cookie_id=self.cookie_id,
                statuses=[
                    "pending_ship",
                    "pending_delivery",
                    "partial_success",
                    "partial_pending_finalize",
                ],
                minutes=minutes,
                limit=5,
            )
        except Exception as lookup_error:
            logger.error(f"{log_prefix} sid兜底查单异常: {self._safe_str(lookup_error)}")
            return {"match_type": "error", "order": None}

        if pending_orders:
            order = pending_orders[0]
            logger.info(
                f"{log_prefix} sid兜底命中待发货订单: sid={normalized_sid}, "
                f"order_id={order.get('order_id')}, status={order.get('order_status') or 'unknown'}"
            )
            return {"match_type": "pending_ship", "order": order}

        try:
            recent_orders = _db_host().find_recent_orders_by_match_context(
                sid=normalized_sid,
                cookie_id=self.cookie_id,
                statuses=[
                    "processing",
                    "pending_payment",
                    "shipped",
                    "completed",
                    "cancelled",
                ],
                minutes=minutes,
                limit=5,
            )
        except Exception as lookup_error:
            logger.error(f"{log_prefix} sid兜底查单异常: {self._safe_str(lookup_error)}")
            return {"match_type": "error", "order": None}

        if not recent_orders:
            return {"match_type": "missing", "order": None}

        order = recent_orders[0]
        order_id = str(order.get("order_id") or "").strip()
        order_status = str(order.get("order_status") or "").strip()
        if order_status == "shipped":
            if self._has_delivery_progress_evidence(order_id):
                match_type = "already_processed"
            else:
                match_type = "suspicious_shipped"
                logger.warning(
                    f"{log_prefix} sid兜底命中可疑已发货订单，检测到无真实发货进度，继续允许纠偏: "
                    f"sid={normalized_sid}, order_id={order_id}, status={order_status}"
                )
        elif order_status == "completed":
            match_type = "already_processed"
        elif order_status == "cancelled":
            match_type = "cancelled"
        elif order_status in {"processing", "pending_payment"}:
            match_type = "not_ready"
        else:
            match_type = "other_status"

        logger.info(
            f"{log_prefix} sid兜底命中订单: sid={normalized_sid}, "
            f"order_id={order.get('order_id')}, status={order_status or 'unknown'}, match_type={match_type}"
        )
        return {"match_type": match_type, "order": order}
    def _select_buyer_identity_for_order_write(self, order_id: str, *, incoming_buyer_id: Any = None,
                                               incoming_buyer_nick: Any = None, existing_order: Dict[str, Any] = None,
                                               buyer_id_source: str = None, buyer_nick_source: str = 'unknown',
                                               log_prefix: str = '') -> Tuple[Optional[str], Optional[str], bool]:
        incoming_buyer_id = self._normalize_buyer_id_value(incoming_buyer_id)
        incoming_buyer_nick = self._sanitize_buyer_nick(
            incoming_buyer_nick,
            source=buyer_nick_source,
            log_prefix=log_prefix,
        )

        existing_buyer_id = self._normalize_buyer_id_value((existing_order or {}).get('buyer_id'))
        existing_buyer_nick = (existing_order or {}).get('buyer_nick')
        existing_buyer_is_trustworthy = self._is_trustworthy_buyer_id(existing_buyer_id)
        incoming_buyer_is_trustworthy = self._is_trustworthy_buyer_id(incoming_buyer_id)
        source_label = buyer_id_source or 'unknown'

        if incoming_buyer_id and incoming_buyer_id == self.myid:
            if existing_order:
                preserved_buyer_id = existing_buyer_id if existing_buyer_id and existing_buyer_id != self.myid else None
                if existing_buyer_nick:
                    incoming_buyer_nick = existing_buyer_nick
                logger.info(
                    f"{log_prefix} 订单 {order_id} 命中自己买家ID保护，继续刷新并保留已有买家信息: "
                    f"incoming_buyer_id={incoming_buyer_id}, preserved_buyer_id={preserved_buyer_id}"
                )
                return preserved_buyer_id, incoming_buyer_nick, False

            logger.info(
                f"{log_prefix} 跳过疑似买家订单 {order_id} 的首次写入，buyer_id={incoming_buyer_id} 等于自己的ID"
            )
            return None, incoming_buyer_nick, True

        if existing_buyer_is_trustworthy:
            if not incoming_buyer_id:
                return existing_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

            if not incoming_buyer_is_trustworthy:
                logger.info(
                    f"{log_prefix} 忽略低可信buyer_id覆盖，保留已有买家信息: "
                    f"order_id={order_id}, incoming_buyer_id={incoming_buyer_id}, "
                    f"incoming_source={source_label}, preserved_buyer_id={existing_buyer_id}"
                )
                return existing_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

            if incoming_buyer_id != existing_buyer_id:
                logger.warning(
                    f"{log_prefix} 检测到买家ID冲突，保留已有可信买家信息: "
                    f"order_id={order_id}, incoming_buyer_id={incoming_buyer_id}, "
                    f"incoming_source={source_label}, preserved_buyer_id={existing_buyer_id}"
                )
                return existing_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

            return existing_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

        if incoming_buyer_is_trustworthy:
            return incoming_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

        if incoming_buyer_id:
            logger.info(
                f"{log_prefix} 检测到低可信buyer_id，暂不写入订单: "
                f"order_id={order_id}, incoming_buyer_id={incoming_buyer_id}, incoming_source={source_label}"
            )

        fallback_buyer_id = existing_buyer_id if existing_buyer_id and existing_buyer_id != self.myid else None
        return fallback_buyer_id, incoming_buyer_nick or existing_buyer_nick, False
    def _extract_order_message_context(self, message: dict, msg_id: str = None) -> Dict[str, Any]:
        """从订单相关消息中提取买家、会话和商品信息。"""
        buyer_id = None
        buyer_id_source = None
        buyer_nick = None
        sid = ""
        item_id = None
        log_prefix = f"【{self.cookie_id}】[{msg_id}]" if msg_id else f"【{self.cookie_id}】"

        try:
            message_1 = message.get("1")
            if isinstance(message_1, str):
                # message['1'] 是字符串，可能是 sid（如 "56226853668@goofish"）或消息ID（如 "4003914207496.PNM"）
                if '@' in message_1:
                    sid = message_1
                else:
                    # PNM 等非 sid 格式，真正的 sid 在 message['2']
                    sid = message.get("2", "") or ""
                buyer_id = None
                # 尝试从 message['4'] 提取 buyer_id（PNM 等格式的 senderUserId 在这里）
                message_4 = message.get("4")
                if isinstance(message_4, dict):
                    buyer_id, buyer_id_source = self._extract_buyer_id_from_message_meta(
                        message_4,
                        meta_label='message[4]',
                        log_prefix=log_prefix,
                    )
                    buyer_nick = self._sanitize_buyer_nick(
                        message_4.get("senderNick"),
                        source="senderNick(msg4)",
                        message_meta=message_4,
                        log_prefix=log_prefix
                    )
                    if not buyer_nick:
                        reminder_title = message_4.get("reminderTitle", "")
                        buyer_nick = self._sanitize_buyer_nick(
                            reminder_title,
                            source="reminderTitle(msg4)",
                            message_meta=message_4,
                            log_prefix=log_prefix
                        )
                        if buyer_nick:
                            logger.info(f"{log_prefix} 👤 从message[4].reminderTitle提取到买家昵称: {buyer_nick}")
                    if buyer_nick:
                        logger.info(f"{log_prefix} 👤 从message[4]提取到买家昵称: {buyer_nick}")
                logger.info(
                    f"{log_prefix} 📌 简化消息，sid: {sid}，buyer_id: {buyer_id}，"
                    f"buyer_id_source: {buyer_id_source or '-'}"
                )
            elif isinstance(message_1, dict):
                if "10" in message_1 and isinstance(message_1["10"], dict):
                    message_10 = message_1["10"]
                    buyer_id, buyer_id_source = self._extract_buyer_id_from_message_meta(
                        message_10,
                        meta_label='message[1][10]',
                        log_prefix=log_prefix,
                    )
                    buyer_nick = self._sanitize_buyer_nick(
                        message_10.get("senderNick"),
                        source="senderNick",
                        message_meta=message_10,
                        log_prefix=log_prefix
                    )
                    if not buyer_nick:
                        reminder_title = message_10.get("reminderTitle", "")
                        buyer_nick = self._sanitize_buyer_nick(
                            reminder_title,
                            source="reminderTitle",
                            message_meta=message_10,
                            log_prefix=log_prefix
                        )
                        if buyer_nick:
                            logger.info(f"{log_prefix} 👤 从reminderTitle提取到买家昵称: {buyer_nick}")
                    if buyer_nick:
                        logger.info(f"{log_prefix} 👤 提取到买家昵称: {buyer_nick}")
                sid = message_1.get("2", "")
                if sid:
                    logger.info(f"{log_prefix} 📌 提取到sid: {sid}")
        except Exception as context_e:
            logger.warning(f"{log_prefix} 提取订单上下文失败: {self._safe_str(context_e)}")

        try:
            if "1" in message and isinstance(message["1"], dict) and "10" in message["1"] and isinstance(message["1"]["10"], dict):
                url_info = message["1"]["10"].get("reminderUrl", "")
                if isinstance(url_info, str) and "itemId=" in url_info:
                    item_id = url_info.split("itemId=")[1].split("&")[0]

            # message['4'] 中也可能包含 reminderUrl（PNM 等格式）
            if not item_id and "4" in message and isinstance(message["4"], dict):
                url_info = message["4"].get("reminderUrl", "")
                if isinstance(url_info, str) and "itemId=" in url_info:
                    item_id = url_info.split("itemId=")[1].split("&")[0]

            if not item_id:
                item_id = self.extract_item_id_from_message(message)
        except Exception as item_e:
            logger.warning(f"{log_prefix} 提取商品ID失败: {self._safe_str(item_e)}")

        return {
            'buyer_id': buyer_id,
            'buyer_id_source': buyer_id_source,
            'buyer_nick': buyer_nick,
            'sid': sid,
            'item_id': item_id,
        }
    def _preload_basic_order_info(self, order_id: str, item_id: str = None, buyer_id: str = None,
                                  sid: str = None, buyer_nick: str = None,
                                  buyer_id_source: str = None) -> bool:
        """在详情抓取前先落基础订单，避免详情超时导致整单丢失。"""
        try:
            existing_order = _db_host().get_order_by_id(order_id)
            buyer_id_to_save, buyer_nick_to_save, should_skip_write = self._select_buyer_identity_for_order_write(
                order_id,
                incoming_buyer_id=buyer_id,
                incoming_buyer_nick=buyer_nick,
                existing_order=existing_order,
                buyer_id_source=buyer_id_source,
                buyer_nick_source="preload",
                log_prefix=f"【{self.cookie_id}】",
            )
            if should_skip_write:
                return False

            success = _db_host().insert_or_update_order(
                order_id=order_id,
                item_id=item_id,
                buyer_id=buyer_id_to_save,
                buyer_nick=buyer_nick_to_save,
                sid=sid,
                cookie_id=self.cookie_id,
                order_status='processing' if not existing_order else None
            )
            if success:
                action = "更新基础订单信息" if existing_order else "基础订单已预入库"
                logger.info(
                    f"【{self.cookie_id}】{action}: order_id={order_id}, item_id={item_id}, "
                    f"buyer_id={buyer_id_to_save}, sid={sid or '-'}"
                )
            else:
                logger.warning(f"【{self.cookie_id}】基础订单预入库失败: {order_id}")
            return success
        except Exception as e:
            logger.error(f"【{self.cookie_id}】基础订单预入库异常: {self._safe_str(e)}")
            return False
    async def _retry_order_detail_after_delay(self, order_id: str, item_id: str = None, buyer_id: str = None,
                                              sid: str = None, buyer_nick: str = None, delay_seconds: int = 30,
                                              buyer_id_source: str = None):
        """订单详情首次抓取失败后，后台延迟补抓一次。"""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(delay_seconds)
            logger.info(f"【{self.cookie_id}】开始延迟补抓订单详情: order_id={order_id}, delay={delay_seconds}s")
            result = await self.fetch_order_detail_info(
                order_id,
                item_id,
                buyer_id,
                sid=sid,
                buyer_nick=buyer_nick,
                buyer_id_source=buyer_id_source,
                force_refresh=True
            )
            if result:
                logger.info(f"【{self.cookie_id}】订单详情延迟补抓成功: {order_id}")
            else:
                logger.warning(f"【{self.cookie_id}】订单详情延迟补抓仍失败，保留基础订单: {order_id}")
        except asyncio.CancelledError:
            logger.info(f"【{self.cookie_id}】订单详情延迟补抓任务已取消: {order_id}")
            raise
        except Exception as e:
            logger.error(f"【{self.cookie_id}】订单详情延迟补抓异常: {order_id} - {self._safe_str(e)}")
        finally:
            existing_task = self.order_detail_retry_tasks.get(order_id)
            if existing_task is current_task:
                self.order_detail_retry_tasks.pop(order_id, None)
    def _schedule_order_detail_retry(self, order_id: str, item_id: str = None, buyer_id: str = None,
                                     sid: str = None, buyer_nick: str = None, delay_seconds: int = 30,
                                     buyer_id_source: str = None):
        """调度订单详情补抓任务，避免同一订单重复创建补抓。"""
        existing_task = self.order_detail_retry_tasks.get(order_id)
        if existing_task and not existing_task.done():
            logger.info(f"【{self.cookie_id}】订单详情补抓任务已存在，跳过重复调度: {order_id}")
            return

        task = self._create_tracked_task(
            self._retry_order_detail_after_delay(
                order_id,
                item_id=item_id,
                buyer_id=buyer_id,
                sid=sid,
                buyer_nick=buyer_nick,
                delay_seconds=delay_seconds,
                buyer_id_source=buyer_id_source,
            )
        )
        self.order_detail_retry_tasks[order_id] = task
        logger.info(f"【{self.cookie_id}】已调度订单详情补抓任务: order_id={order_id}, delay={delay_seconds}s")
    def _extract_order_id_for_comment(self, message: dict) -> str:
        """从评价提醒消息中提取订单ID"""
        try:
            order_id = self._extract_order_id(message)
            if order_id:
                logger.info(f'【{self.cookie_id}】评价提醒消息提取到订单ID: {order_id}')
            return order_id
            
        except Exception as e:
            logger.error(f"【{self.cookie_id}】提取评价订单ID失败: {self._safe_str(e)}")
            return None
    def _get_normalized_local_order_status(self, order_id: str) -> str:
        if not order_id:
            return ''
        try:
            from db_manager import db_manager
            order = _db_package().get_order_by_id(order_id)
            if not order:
                return ''
            return _db_package()._normalize_order_status(order.get('order_status'))
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】读取本地订单状态失败: order_id={order_id}, error={self._safe_str(e)}")
            return ''
    def _get_order_expected_delivery_quantity(self, order_id: str) -> int:
        """获取订单应发货单元数，回退为已记录单元最大序号。"""
        expected = 1
        try:
            from db_manager import db_manager
            order = _db_package().get_order_by_id(order_id) if order_id else None
            if order and order.get('quantity'):
                expected = max(1, int(order.get('quantity') or 1))
            states = _db_package().get_delivery_finalization_states(order_id) if order_id else []
            for state in states:
                try:
                    expected = max(expected, int(state.get('unit_index') or 1))
                except (TypeError, ValueError):
                    continue
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】获取订单应发数量失败: order_id={order_id}, error={self._safe_str(e)}")
        return expected
    def _resolve_external_order_status(self, current_status: str, incoming_status: str, source: str):
        from db_manager import db_manager

        merged_status = _db_package().resolve_external_order_status(current_status, incoming_status, source=source)
        normalized_current = _db_package()._normalize_order_status(current_status)

        if merged_status and merged_status != normalized_current:
            return merged_status
        return None
    def _normalize_order_amount_text(self, value: Any):
        text = str(value or '').strip()
        if not text:
            return None
        text = text.replace('¥', '').replace('￥', '').replace(',', '')
        match = re.search(r'\d+(?:\.\d{1,2})?', text)
        if not match:
            return None
        try:
            return f"{float(match.group(0)):.2f}"
        except (TypeError, ValueError):
            return None
    def _parse_order_amount_float(self, value: Any):
        normalized = self._normalize_order_amount_text(value)
        if normalized is None:
            return None
        try:
            return float(normalized)
        except (TypeError, ValueError):
            return None
    def _mark_order_bargain_flow(self, order_id: str, item_id: str = None, buyer_id: str = None,
                                 sid: str = None, *, apply_configured_price: bool = False,
                                 success_detected=..., context: str = '') -> bool:
        if not order_id:
            return False

        from db_manager import db_manager

        existing_order = _db_package().get_order_by_id(order_id) or {}
        effective_item_id = item_id or existing_order.get('item_id')
        effective_buyer_id = buyer_id or existing_order.get('buyer_id')
        effective_sid = sid or existing_order.get('sid')
        amount_to_save = None

        if apply_configured_price and effective_item_id:
            item_config = _db_package().get_item_info(self.cookie_id, effective_item_id)
            configured_amount = self._normalize_order_amount_text(item_config.get('item_price') if item_config else None)
            configured_amount_value = self._parse_order_amount_float(configured_amount)
            existing_amount_value = self._parse_order_amount_float(existing_order.get('amount'))
            if configured_amount_value is not None and (
                existing_amount_value is None or configured_amount_value < existing_amount_value - 0.009
            ):
                amount_to_save = configured_amount

        success = _db_package().insert_or_update_order(
            order_id=order_id,
            item_id=effective_item_id,
            buyer_id=effective_buyer_id,
            sid=effective_sid,
            amount=amount_to_save,
            cookie_id=self.cookie_id,
            bargain_flow_detected=True,
            bargain_success_detected=success_detected,
        )

        if success:
            logger.info(
                f"【{self.cookie_id}】标记订单为小刀流程: order_id={order_id}, context={context or 'unknown'}, "
                f"apply_configured_price={apply_configured_price}, amount_override={amount_to_save or ''}, "
                f"success_detected={success_detected if success_detected is not ... else 'unchanged'}"
            )
        else:
            logger.warning(
                f"【{self.cookie_id}】标记订单小刀流程失败: order_id={order_id}, context={context or 'unknown'}"
            )
        return success
    def _resolve_delivery_progress_order_status(self, current_status: str, aggregate_status: str):
        from db_manager import db_manager

        normalized_current = _db_package()._normalize_order_status(current_status)
        normalized_aggregate = _db_package()._normalize_order_status(aggregate_status)

        if not normalized_aggregate or normalized_aggregate == 'unknown':
            return None

        if not normalized_current or normalized_current == 'unknown':
            return normalized_aggregate

        if normalized_current in {'completed', 'refunding', 'cancelled'} and normalized_aggregate in {
            'pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped'
        }:
            logger.warning(
                f"【{self.cookie_id}】保留订单终态，忽略发货进度覆盖: current={normalized_current}, incoming={normalized_aggregate}"
            )
            return normalized_current

        if normalized_current == 'shipped' and normalized_aggregate in {'pending_ship', 'partial_success', 'partial_pending_finalize'}:
            logger.warning(
                f"【{self.cookie_id}】保留已发货状态，忽略较低发货进度覆盖: current={normalized_current}, incoming={normalized_aggregate}"
            )
            return normalized_current

        if normalized_current in {'partial_success', 'partial_pending_finalize'} and normalized_aggregate == 'pending_ship':
            logger.warning(
                f"【{self.cookie_id}】保留部分发货状态，忽略待发货覆盖: current={normalized_current}, incoming={normalized_aggregate}"
            )
            return normalized_current

        return normalized_aggregate
    def _sync_order_delivery_progress(self, order_id: str, cookie_id: str, expected_quantity: int = 1,
                                      context: str = "自动发货进度同步"):
        summary = self._summarize_delivery_progress(order_id, expected_quantity=expected_quantity)
        aggregate_status = summary.get('aggregate_status') or 'pending_ship'
        previous_status = None

        try:
            from db_manager import db_manager
            current_order = _db_package().get_order_by_id(order_id) if order_id else None
            previous_status = _db_package()._normalize_order_status(current_order.get('order_status')) if current_order else None
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】读取订单旧状态失败: {self._safe_str(e)}")

        logger.info(
            f"【{self.cookie_id}】同步订单发货进度: order_id={order_id}, status={aggregate_status}, "
            f"finalized={summary.get('finalized_count')}/{summary.get('expected_quantity')}, "
            f"pending_finalize={summary.get('pending_finalize_count')}, remaining={summary.get('remaining_count')}"
        )

        status_to_write = self._resolve_delivery_progress_order_status(previous_status, aggregate_status)

        if aggregate_status in {'shipped', 'partial_success', 'partial_pending_finalize'}:
            self.delivery_sent_orders.add(order_id)
            self.last_delivery_time[order_id] = time.time()

        if self.order_status_handler and status_to_write == 'shipped' and previous_status != 'shipped':
            try:
                self.order_status_handler.handle_auto_delivery_order_status(
                    order_id=order_id,
                    cookie_id=cookie_id,
                    context=context
                )
            except Exception as e:
                logger.warning(f"【{self.cookie_id}】通过状态处理器同步已发货状态失败: {self._safe_str(e)}")

        try:
            from db_manager import db_manager
            success = True
            if status_to_write and status_to_write != previous_status:
                success = _db_package().insert_or_update_order(order_id=order_id, order_status=status_to_write, cookie_id=cookie_id)

            if success and status_to_write in {'partial_success', 'partial_pending_finalize'} and previous_status != status_to_write:
                try:
                    from order_event_hub import publish_order_update_event
                    publish_order_update_event(order_id, source='delivery_progress_sync')
                except Exception as publish_e:
                    logger.warning(
                        f"【{self.cookie_id}】发布部分发货实时事件失败: order_id={order_id}, error={self._safe_str(publish_e)}"
                    )
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】写入订单聚合发货状态失败: {self._safe_str(e)}")

        return summary
    def _get_order_status_priority(self, status: str) -> int:
        normalized_status = _db_host()._normalize_order_status(status)
        priority_map = {
            'unknown': 0,
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
        return priority_map.get(normalized_status or 'unknown', 0)
    def _reserve_order_detail_force_refresh(self, order_id: str, *, reason: str,
                                            log_prefix: str = "", cooldown_seconds: float = None) -> bool:
        normalized_order_id = str(order_id or '').strip()
        if not normalized_order_id:
            return False

        cooldown = float(cooldown_seconds or self.order_detail_force_refresh_cooldown or 0)
        now = time.time()
        existing = self.order_detail_force_refresh_marks.get(normalized_order_id) or {}
        last_timestamp = existing.get('timestamp', 0)
        elapsed = now - last_timestamp

        if last_timestamp and cooldown > 0 and elapsed < cooldown:
            logger.info(
                f"{log_prefix} 订单详情强刷命中冷却，跳过重复刷新: "
                f"order_id={normalized_order_id}, reason={reason}, "
                f"last_reason={existing.get('reason', 'unknown')}, remaining={round(cooldown - elapsed, 2)}s"
            )
            return False

        self.order_detail_force_refresh_marks[normalized_order_id] = {
            'timestamp': now,
            'reason': reason,
        }
        return True
    def _should_accept_order_detail_status_correction(self, current_status: str, incoming_status: str,
                                                      incoming_source: str, *, force_refresh: bool,
                                                      order_id: str = None) -> bool:
        normalized_current = _db_host()._normalize_order_status(current_status)
        normalized_incoming = _db_host()._normalize_order_status(incoming_status)
        normalized_source = str(incoming_source or 'unknown').strip().lower()

        if not force_refresh:
            return False
        if normalized_current != 'shipped' or normalized_incoming != 'pending_ship':
            return False
        if normalized_source not in {'selector', 'button'}:
            return False
        if self._has_delivery_progress_evidence(order_id):
            return False
        return True
    def _should_reject_order_detail_status_update(self, current_status: str, incoming_status: str,
                                                  incoming_source: str, *, force_refresh: bool) -> bool:
        normalized_current = _db_host()._normalize_order_status(current_status)
        normalized_incoming = _db_host()._normalize_order_status(incoming_status)
        normalized_source = str(incoming_source or 'unknown').strip().lower()

        if normalized_incoming != 'completed' or normalized_source != 'body':
            return False

        if force_refresh and normalized_current in {'shipped', 'pending_ship', 'partial_success', 'partial_pending_finalize'}:
            return True

        return False
    async def _maybe_force_refresh_order_detail_for_signal(self, order_id: str, *, item_id: str = None,
                                                           buyer_id: str = None, sid: str = None,
                                                           buyer_nick: str = None, status_signal: str = None,
                                                           reason: str = "status_signal",
                                                           delay_seconds: float = 0,
                                                           log_prefix: str = "") -> bool:
        normalized_order_id = str(order_id or '').strip()
        if not normalized_order_id:
            return False

        current_order = _db_host().get_order_by_id(normalized_order_id) or {}
        current_status = current_order.get('order_status')
        if not self._should_force_refresh_after_status_signal(status_signal, current_status, normalized_order_id):
            logger.info(
                f"{log_prefix} 当前订单状态无需为该信号强刷详情: order_id={normalized_order_id}, "
                f"signal={status_signal or 'unknown'}, current_status={current_status or 'unknown'}"
            )
            return False

        if not self._reserve_order_detail_force_refresh(
            normalized_order_id,
            reason=reason,
            log_prefix=log_prefix,
        ):
            return False

        if delay_seconds and delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        latest_order = _db_host().get_order_by_id(normalized_order_id) or {}
        latest_status = latest_order.get('order_status')
        if not self._should_force_refresh_after_status_signal(status_signal, latest_status, normalized_order_id):
            logger.info(
                f"{log_prefix} 延迟后订单状态已更新，无需再强刷详情: order_id={normalized_order_id}, "
                f"signal={status_signal or 'unknown'}, current_status={latest_status or 'unknown'}"
            )
            return False

        refresh_item_id = item_id or latest_order.get('item_id')
        refresh_buyer_id = buyer_id or latest_order.get('buyer_id')
        logger.info(
            f"{log_prefix} 状态信号触发订单详情强刷: order_id={normalized_order_id}, "
            f"signal={status_signal or 'unknown'}, current_status={latest_status or 'unknown'}, reason={reason}"
        )

        try:
            await self.fetch_order_detail_info(
                order_id=normalized_order_id,
                item_id=refresh_item_id,
                buyer_id=refresh_buyer_id,
                sid=sid,
                buyer_nick=buyer_nick,
                force_refresh=True
            )
            return True
        except Exception as refresh_error:
            logger.error(
                f"{log_prefix} 状态信号触发订单详情强刷失败: order_id={normalized_order_id}, "
                f"reason={reason}, error={self._safe_str(refresh_error)}"
            )
            return False
    def _extract_order_id_from_update_key(self, raw_text: Any) -> Optional[str]:
        normalized_text = str(raw_text or '').strip()
        if not normalized_text:
            return None

        direct_match_found = False
        direct_match = re.search(r'updateKey["\']?\s*[:=]\s*["\']([^"\']+)', normalized_text)
        if direct_match:
            direct_match_found = True
            normalized_text = direct_match.group(1)

        colon_parts = [part.strip().strip('"\'') for part in normalized_text.split(':')]
        long_numeric_parts = [part for part in colon_parts if part.isdigit() and len(part) >= 16]
        if long_numeric_parts:
            return long_numeric_parts[0]

        if direct_match_found:
            generic_matches = re.findall(r'\d{16,}', normalized_text)
            if generic_matches:
                return generic_matches[0]
        return None
    def _extract_order_id_from_candidate_text(self, raw_text: Any, source: str = '') -> Optional[str]:
        normalized_text = str(raw_text or '').strip()
        if not normalized_text:
            return None

        patterns = [
            r'orderId(?:=|:|%3[Dd]|\\u003[dD])\s*"?(\d{10,})',
            r'bizOrderId["\']?\s*[:=]\s*"?(\d{10,})',
            r'order[_-]?id["\']?\s*[:=]\s*"?(\d{10,})',
            r'order[_-]?detail\?(?:[^\s#]*?&)?id=(\d{10,})',
            r'order-detail\?(?:[^\s#]*?&)?orderId=(\d{10,})',
        ]

        for pattern in patterns:
            match = re.search(pattern, normalized_text)
            if match:
                return match.group(1)

        source_lower = source.lower()
        text_lower = normalized_text.lower()
        if (
            'updatekey' in source_lower
            or 'updatekey' in text_lower
            or ('trade_' in text_lower and ':' in normalized_text)
            or ('buyer_confirm' in text_lower and ':' in normalized_text)
        ):
            return self._extract_order_id_from_update_key(normalized_text)

        return None
    def _collect_order_id_candidate_texts(self, data: Any, root: str = 'message'):
        candidates = []
        seen = set()

        def add_candidate(source: str, value: Any):
            if value is None:
                return
            normalized_text = str(value).strip()
            if not normalized_text:
                return
            dedupe_key = (source, normalized_text)
            if dedupe_key in seen:
                return
            seen.add(dedupe_key)
            candidates.append((source, normalized_text))

            if normalized_text[:1] in {'{', '['}:
                try:
                    parsed_value = json.loads(normalized_text)
                except Exception:
                    return
                walk_value(parsed_value, f'{source}.json')

        def walk_value(value: Any, source: str):
            if isinstance(value, dict):
                for key, nested_value in value.items():
                    nested_source = f'{source}.{key}'
                    if isinstance(nested_value, (dict, list)):
                        walk_value(nested_value, nested_source)
                    else:
                        add_candidate(nested_source, nested_value)
            elif isinstance(value, list):
                for index, nested_value in enumerate(value[:20]):
                    walk_value(nested_value, f'{source}[{index}]')
            else:
                add_candidate(source, value)

        walk_value(data, root)
        return candidates
    def _extract_order_id(self, message: dict, raw_message_data: dict = None) -> str:
        """从消息中提取订单ID
        
        Args:
            message: 解密后的消息内容
            raw_message_data: 原始的WebSocket消息数据（用于在解密消息中找不到订单ID时进行搜索）
        """
        try:
            # 先查看消息的完整结构
            logger.warning(f"【{self.cookie_id}】🔍 完整消息结构: {message}")

            for source, candidate_text in self._collect_order_id_candidate_texts(message, root='message'):
                order_id = self._extract_order_id_from_candidate_text(candidate_text, source=source)
                if order_id:
                    logger.info(f'【{self.cookie_id}】🎯 最终提取到订单ID: {order_id} (source={source})')
                    return order_id

            if raw_message_data:
                logger.info(f'【{self.cookie_id}】🔍 尝试从原始消息数据中搜索订单ID')
                for source, candidate_text in self._collect_order_id_candidate_texts(raw_message_data, root='raw_message'):
                    order_id = self._extract_order_id_from_candidate_text(candidate_text, source=source)
                    if order_id:
                        logger.info(f'【{self.cookie_id}】🎯 从原始消息提取到订单ID: {order_id} (source={source})')
                        return order_id

                try:
                    sync_data_list = raw_message_data.get("body", {}).get("syncPushPackage", {}).get("data", [])
                    for idx, sync_data_item in enumerate(sync_data_list[:20]):
                        if not isinstance(sync_data_item, dict) or "data" not in sync_data_item:
                            continue

                        item_data = sync_data_item.get("data")
                        if item_data is None:
                            continue

                        try:
                            decoded_data = _host.base64.b64decode(item_data).decode("utf-8")
                        except Exception:
                            decoded_data = item_data

                        for source, candidate_text in self._collect_order_id_candidate_texts(decoded_data, root=f'raw_sync[{idx}]'):
                            order_id = self._extract_order_id_from_candidate_text(candidate_text, source=source)
                            if order_id:
                                logger.info(f'【{self.cookie_id}】🎯 从syncPushPackage.data提取到订单ID: {order_id} (source={source})')
                                return order_id
                except Exception as multi_data_e:
                    logger.warning(f"遍历syncPushPackage.data时出错: {multi_data_e}")

            logger.warning(f'【{self.cookie_id}】❌ 未能从消息中提取到订单ID')
            return None

        except Exception as e:
            logger.error(f"【{self.cookie_id}】提取订单ID失败: {self._safe_str(e)}")
            return None
    async def fetch_order_detail_info(self, order_id: str, item_id: str = None, buyer_id: str = None, debug_headless: bool = None, sid: str = None, force_refresh: bool = False, buyer_nick: str = None, buyer_id_source: str = None):
        """获取订单详情信息（使用独立的锁机制，不受延迟锁影响）

        Args:
            order_id: 订单ID
            item_id: 商品ID
            buyer_id: 买家ID
            debug_headless: 是否使用有头模式调试
            sid: 会话ID（如 56226853668@goofish），用于简化消息匹配订单
            force_refresh: 是否强制刷新（跳过缓存直接从闲鱼获取）
            buyer_nick: 买家昵称（从下单消息中提取）
        """
        # 使用独立的订单详情锁，不与自动发货锁冲突
        order_detail_lock = self._order_detail_locks[order_id]

        # 如果锁绑定了不同的事件循环（如从 Web API 调用），创建新锁
        try:
            current_loop = asyncio.get_running_loop()
            lock_loop = getattr(order_detail_lock, '_loop', None)
            if lock_loop is not None and lock_loop is not current_loop:
                order_detail_lock = asyncio.Lock()
                self._order_detail_locks[order_id] = order_detail_lock
                logger.info(f"【{self.cookie_id}】订单详情锁 {order_id} 事件循环不匹配，已重建")
        except RuntimeError:
            pass

        # 记录订单详情锁的使用时间
        self._order_detail_lock_times[order_id] = time.time()

        async with order_detail_lock:
            logger.info(f"🔍 【{self.cookie_id}】获取订单详情锁 {order_id}，开始处理...")
            
            try:
                logger.info(f"【{self.cookie_id}】开始获取订单详情: {order_id}, sid={sid}")

                # 导入订单详情获取器
                from utils.order_detail_fetcher import fetch_order_detail_simple
                from db_manager import db_manager

                # 获取当前账号的cookie字符串
                cookie_string = self.cookies_str
                logger.warning(f"【{self.cookie_id}】使用Cookie长度: {len(cookie_string) if cookie_string else 0}")

                # 确定是否使用有头模式（调试用）
                headless_mode = True if debug_headless is None else debug_headless
                if not headless_mode:
                    logger.info(f"【{self.cookie_id}】🖥️ 启用有头模式进行调试")

                # 异步获取订单详情（使用当前账号的cookie）
                result = await fetch_order_detail_simple(
                    order_id,
                    cookie_string,
                    headless=headless_mode,
                    force_refresh=force_refresh,
                    cookie_id_for_log=self.cookie_id
                )

                if result:
                    retry_task = self.order_detail_retry_tasks.get(order_id)
                    current_task = asyncio.current_task()
                    if retry_task and retry_task is not current_task and not retry_task.done():
                        retry_task.cancel()
                        self.order_detail_retry_tasks.pop(order_id, None)
                        logger.info(f"【{self.cookie_id}】订单详情已成功获取，取消待执行的补抓任务: {order_id}")

                    logger.info(f"【{self.cookie_id}】订单详情获取成功: {order_id}")
                    logger.info(f"【{self.cookie_id}】页面标题: {result.get('title', '未知')}")

                    def _normalize_optional_text(value):
                        if value is None:
                            return None
                        text = str(value).strip()
                        return text if text else None

                    def _normalize_amount_text(value):
                        text = _normalize_optional_text(value)
                        if not text:
                            return None
                        # 避免将无数字的异常文本写入金额字段
                        if not re.search(r'\d', text):
                            return None
                        return text

                    def _parse_amount_float(value):
                        text = _normalize_amount_text(value)
                        if not text:
                            return None
                        try:
                            return float(text)
                        except (TypeError, ValueError):
                            return None

                    # 获取解析后的规格信息
                    spec_parse_mode = str(result.get('spec_parse_mode') or '').strip() or 'no_spec'
                    spec_name = _normalize_optional_text(result.get('spec_name'))
                    spec_value = _normalize_optional_text(result.get('spec_value'))
                    spec_name_2 = _normalize_optional_text(result.get('spec_name_2'))
                    spec_value_2 = _normalize_optional_text(result.get('spec_value_2'))
                    quantity = _normalize_optional_text(result.get('quantity'))
                    amount = _normalize_amount_text(result.get('amount'))
                    amount_source = _normalize_optional_text(result.get('amount_source')) or 'unknown'
                    platform_created_at = _normalize_optional_text(result.get('platform_created_at'))
                    platform_paid_at = _normalize_optional_text(result.get('platform_paid_at'))
                    platform_completed_at = _normalize_optional_text(result.get('platform_completed_at'))
                    item_config = _db_package().get_item_info(self.cookie_id, item_id) if item_id else None
                    item_config_multi_spec = bool(item_config and item_config.get('is_multi_spec'))
                    item_config_detail = _normalize_optional_text(item_config.get('item_detail')) if item_config else None
                    is_coin_deduction_item = bool(item_config_detail and '闲鱼币抵扣' in item_config_detail)
                    configured_item_amount = _normalize_amount_text(item_config.get('item_price')) if item_config else None
                    configured_item_amount_value = _parse_amount_float(configured_item_amount)

                    if item_config is not None and not item_config_multi_spec and any(
                        [spec_name, spec_value, spec_name_2, spec_value_2]
                    ):
                        logger.warning(
                            f"【{self.cookie_id}】商品配置为无规格，刷新订单详情时忽略解析到的规格信息: "
                            f"order_id={order_id}, item_id={item_id}, "
                            f"spec={spec_name or ''}:{spec_value or ''}, spec2={spec_name_2 or ''}:{spec_value_2 or ''}"
                        )
                        spec_name = None
                        spec_value = None
                        spec_name_2 = None
                        spec_value_2 = None

                    if spec_parse_mode == 'one_spec' and spec_name and spec_value and not (spec_name_2 or spec_value_2):
                        spec_name_2 = ''
                        spec_value_2 = ''
                        logger.info(
                            f"【{self.cookie_id}】订单详情明确解析为单规格，允许清空历史残留的第二规格字段: "
                            f"order_id={order_id}, item_id={item_id}, spec={spec_name}:{spec_value}"
                        )

                    # 获取订单状态（从闲鱼页面解析）
                    raw_order_status = _normalize_optional_text(result.get('order_status'))
                    order_status_source = _normalize_optional_text(result.get('order_status_source')) or 'unknown'
                    # unknown 视为解析失败，不覆盖已有状态
                    order_status = raw_order_status if raw_order_status and raw_order_status.lower() != 'unknown' else None
                    if order_status:
                        logger.info(f"【{self.cookie_id}】📊 订单状态: {order_status} (source={order_status_source})")
                    elif raw_order_status and raw_order_status.lower() == 'unknown':
                        logger.warning(f"【{self.cookie_id}】订单状态解析为unknown，跳过状态字段写库")

                    if spec_name and spec_value:
                        logger.info(f"【{self.cookie_id}】📋 规格名称: {spec_name}")
                        logger.info(f"【{self.cookie_id}】📝 规格值: {spec_value}")
                        if spec_name_2 and spec_value_2:
                            logger.info(f"【{self.cookie_id}】📋 规格2名称: {spec_name_2}")
                            logger.info(f"【{self.cookie_id}】📝 规格2值: {spec_value_2}")
                            print(f"🛍️ 【{self.cookie_id}】订单 {order_id} 规格信息: {spec_name} -> {spec_value}, {spec_name_2} -> {spec_value_2}")
                        else:
                            print(f"🛍️ 【{self.cookie_id}】订单 {order_id} 规格信息: {spec_name} -> {spec_value}")
                    else:
                        logger.warning(f"【{self.cookie_id}】未获取到有效的规格信息")
                        print(f"⚠️ 【{self.cookie_id}】订单 {order_id} 规格信息获取失败")

                    if amount:
                        logger.info(f"【{self.cookie_id}】💰 订单金额: {amount} (source={amount_source})")

                    # 插入或更新订单信息到数据库
                    try:
                        # 对于系统消息误识别出的“自己是买家”场景，保留已有买家信息并继续刷新订单字段
                        existing_order = _db_package().get_order_by_id(order_id)
                        current_order_status = existing_order.get('order_status') if existing_order else None
                        existing_amount = existing_order.get('amount') if existing_order else None
                        existing_amount_value = _parse_amount_float(existing_amount)
                        amount, amount_source = self._apply_bargain_amount_override(
                            order_id,
                            item_id,
                            amount,
                            amount_source,
                            existing_order=existing_order,
                            item_config=item_config,
                        )
                        incoming_amount_value = _parse_amount_float(amount)
                        has_valid_spec = bool(spec_name and spec_value)
                        low_confidence_amount_sources = {
                            'selector_direct',
                            'selector_currency',
                            'text_currency',
                            'unknown',
                        }

                        if (
                            is_coin_deduction_item and existing_amount_value is not None and incoming_amount_value is not None and
                            configured_item_amount_value is not None and existing_amount_value + 0.009 < configured_item_amount_value and
                            abs(incoming_amount_value - configured_item_amount_value) <= 0.009
                        ):
                            logger.warning(
                                f"【{self.cookie_id}】闲鱼币抵扣订单返回原价，保留已有实付金额: "
                                f"order_id={order_id}, existing_amount={existing_amount}, incoming_amount={amount}, "
                                f"configured_amount={configured_item_amount}, amount_source={amount_source}"
                            )
                            amount = _normalize_amount_text(existing_amount)
                            amount_source = 'coin_deduction_preserved_existing'
                            incoming_amount_value = _parse_amount_float(amount)

                        if amount and amount_source in low_confidence_amount_sources and not has_valid_spec and not order_status:
                            if existing_amount_value is not None:
                                logger.warning(
                                    f"【{self.cookie_id}】订单详情返回低置信度金额，保留已有金额: "
                                    f"order_id={order_id}, existing_amount={existing_amount}, incoming_amount={amount}, "
                                    f"amount_source={amount_source}"
                                )
                                amount = _normalize_amount_text(existing_amount)
                                amount_source = 'preserved_existing'
                            else:
                                logger.warning(
                                    f"【{self.cookie_id}】订单详情返回低置信度金额，且缺少规格/状态佐证，跳过写库: "
                                    f"order_id={order_id}, incoming_amount={amount}, amount_source={amount_source}"
                                )
                                amount = None

                        elif (
                            amount and existing_amount_value is not None and incoming_amount_value is not None and
                            abs(existing_amount_value - incoming_amount_value) > 0.009 and
                            not has_valid_spec and not order_status and
                            amount_source not in {'selector_keyword_high', 'selector_keyword_low', 'text_keyword_high', 'text_keyword_low', 'cache'}
                        ):
                            logger.warning(
                                f"【{self.cookie_id}】订单详情金额跳变且缺少规格/状态佐证，保留已有金额: "
                                f"order_id={order_id}, existing_amount={existing_amount}, incoming_amount={amount}, "
                                f"amount_source={amount_source}"
                            )
                            amount = _normalize_amount_text(existing_amount)
                            amount_source = 'preserved_existing'

                        if self._should_reject_order_detail_status_update(
                            current_status=current_order_status,
                            incoming_status=order_status,
                            incoming_source=order_status_source,
                            force_refresh=force_refresh,
                        ):
                            logger.warning(
                                f"【{self.cookie_id}】强制刷新结果仅来自正文，拒绝将订单状态更新为completed: "
                                f"order_id={order_id}, current={current_order_status}, incoming={order_status}, "
                                f"source={order_status_source}"
                            )
                            order_status = None

                        normalized_current_order_status = _db_package()._normalize_order_status(current_order_status)
                        normalized_incoming_order_status = _db_package()._normalize_order_status(order_status)
                        if self._should_accept_order_detail_status_correction(
                            current_order_status,
                            order_status,
                            order_status_source,
                            force_refresh=force_refresh,
                            order_id=order_id,
                        ):
                            order_status_to_save = normalized_incoming_order_status
                            logger.warning(
                                f"【{self.cookie_id}】检测到可疑已发货状态，允许强刷后的结构化待发货结果纠偏: "
                                f"order_id={order_id}, current={current_order_status}, incoming={order_status}, "
                                f"source={order_status_source}"
                            )
                        else:
                            order_status_to_save = self._resolve_external_order_status(
                                current_order_status,
                                order_status,
                                source='order_detail_refresh'
                            )

                        if (
                            order_status and existing_order and order_status_to_save is None and
                            normalized_current_order_status != normalized_incoming_order_status
                        ):
                            logger.info(
                                f"【{self.cookie_id}】保留订单现有状态，跳过详情页覆盖: "
                                f"order_id={order_id}, current={current_order_status}, incoming={order_status}"
                            )

                        buyer_id_to_save, buyer_nick_to_save, should_skip_write = self._select_buyer_identity_for_order_write(
                            order_id,
                            incoming_buyer_id=buyer_id,
                            incoming_buyer_nick=buyer_nick,
                            existing_order=existing_order,
                            buyer_id_source=buyer_id_source,
                            buyer_nick_source="order_detail",
                            log_prefix=f"【{self.cookie_id}】",
                        )
                        if should_skip_write:
                            return result

                        # 检查cookie_id是否在cookies表中存在
                        cookie_info = _db_package().get_cookie_by_id(self.cookie_id)
                        if not cookie_info:
                            logger.warning(f"Cookie ID {self.cookie_id} 不存在于cookies表中，丢弃订单 {order_id}")
                        else:
                            # 先保存订单基本信息（包含sid和buyer_nick用于简化消息匹配）
                            success = _db_package().insert_or_update_order(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=buyer_id_to_save,
                                buyer_nick=buyer_nick_to_save,  # 传递买家昵称
                                sid=sid,
                                spec_name=spec_name,
                                spec_value=spec_value,
                                spec_name_2=spec_name_2,
                                spec_value_2=spec_value_2,
                                quantity=quantity,
                                amount=amount,
                                cookie_id=self.cookie_id,
                                order_status=order_status_to_save,  # 外部详情状态仅在不会回退内部状态时写库
                                platform_created_at=platform_created_at,
                                platform_paid_at=platform_paid_at,
                                platform_completed_at=platform_completed_at
                            )
                            
                            # 使用订单状态处理器设置状态
                            logger.info(f"【{self.cookie_id}】检查订单状态处理器调用条件: success={success}, handler_exists={self.order_status_handler is not None}")
                            if success and self.order_status_handler:
                                logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.handle_order_detail_fetched_status: {order_id}")
                                try:
                                    handler_result = self.order_status_handler.handle_order_detail_fetched_status(
                                        order_id=order_id,
                                        cookie_id=self.cookie_id,
                                        context="订单详情已拉取"
                                    )
                                    logger.info(f"【{self.cookie_id}】订单状态处理器.handle_order_detail_fetched_status返回结果: {handler_result}")
                                    
                                    # 处理待处理队列
                                    logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.on_order_details_fetched: {order_id}")
                                    self.order_status_handler.on_order_details_fetched(order_id)
                                    logger.info(f"【{self.cookie_id}】订单状态处理器.on_order_details_fetched调用成功: {order_id}")
                                except Exception as e:
                                    logger.error(f"【{self.cookie_id}】订单状态处理器调用失败: {self._safe_str(e)}")
                                    import traceback
                                    logger.error(f"【{self.cookie_id}】详细错误信息: {traceback.format_exc()}")
                            else:
                                logger.warning(f"【{self.cookie_id}】订单状态处理器调用条件不满足: success={success}, handler_exists={self.order_status_handler is not None}")

                            if success:
                                logger.info(f"【{self.cookie_id}】订单信息已保存到数据库: {order_id}")
                                print(f"💾 【{self.cookie_id}】订单 {order_id} 信息已保存到数据库")
                            else:
                                logger.warning(f"【{self.cookie_id}】订单信息保存失败: {order_id}")

                    except Exception as db_e:
                        logger.error(f"【{self.cookie_id}】保存订单信息到数据库失败: {self._safe_str(db_e)}")

                    return result
                else:
                    logger.warning(f"【{self.cookie_id}】订单详情获取失败: {order_id}")
                    return None

            except Exception as e:
                logger.error(f"【{self.cookie_id}】获取订单详情异常: {self._safe_str(e)}")
                return None
    async def _auto_deliver_recovered_pending_order(
        self,
        order: Dict[str, Any],
        *,
        fallback_order: Optional[Dict[str, Any]] = None,
        source: str = 'order_recovery',
    ) -> bool:
        from db_manager import db_manager

        fallback_order = fallback_order or {}
        order_id = str(order.get('order_id') or fallback_order.get('order_id') or '').strip()
        item_id = str(order.get('item_id') or fallback_order.get('item_id') or '').strip()
        buyer_id = str(order.get('buyer_id') or fallback_order.get('buyer_id') or '').strip()
        sid = str(order.get('sid') or fallback_order.get('sid') or '').strip()

        if not order_id:
            return False
        if _db_package()._normalize_order_status(order.get('order_status')) != 'pending_ship':
            logger.info(f"【{self.cookie_id}】{source} 订单不是待发货，跳过自动发货: order_id={order_id}, status={order.get('order_status')}")
            return False
        if not self.is_auto_confirm_enabled():
            logger.info(f"【{self.cookie_id}】{source} 发现订单待发货，但自动发货未启用: {order_id}")
            return False
        if not item_id or not buyer_id:
            logger.warning(
                f"【{self.cookie_id}】{source} 发现订单待发货，但缺少商品或买家信息，无法补偿发货: "
                f"order_id={order_id}, item_id={item_id or '-'}, buyer_id={buyer_id or '-'}"
            )
            return False

        websocket = getattr(self, 'ws', None)
        chat_id = sid.replace('@goofish', '')
        if websocket and chat_id:
            await self._handle_simple_message_auto_delivery(
                websocket,
                order_id,
                item_id,
                buyer_id,
                chat_id,
                time.strftime('%Y-%m-%d %H:%M:%S'),
                source,
            )
            return True

        logger.warning(
            f"【{self.cookie_id}】{source} 待发货订单缺少可用会话ID，尝试通过买家和商品建立会话补偿发货: "
            f"order_id={order_id}, buyer_id={buyer_id}, item_id={item_id}"
        )
        return await self._send_recovered_delivery_without_sid(
            order,
            order_id=order_id,
            item_id=item_id,
            buyer_id=buyer_id,
            source=source,
        )


class ItemMixin:
    """商品详情缓存/搜索/擦亮方法簇。"""

    async def _ensure_item_owned_by_current_account(self, item_id: str, *,
                                                    log_prefix: str = "",
                                                    page_size: int = 50,
                                                    max_pages: int = 3) -> bool:
        """优先查本地缓存，未命中时刷新在售商品列表进行归属校验。"""
        if not item_id or item_id == "未知商品":
            return False

        existing_item = _db_host().get_item_info(self.cookie_id, item_id)
        if existing_item:
            return True

        logger.info(f"{log_prefix} 商品 {item_id} 未命中本地缓存，刷新在售商品列表后重试归属校验")
        try:
            for page_number in range(1, max_pages + 1):
                result = await self.get_item_list_info(page_number=page_number, page_size=page_size)
                if not result.get("success"):
                    logger.warning(f"{log_prefix} 刷新在售商品列表失败，停止归属校验回退: page={page_number}, result={result}")
                    break

                current_items = result.get("items", [])
                if any(str(item.get("id", "")).strip() == str(item_id).strip() for item in current_items):
                    logger.info(f"{log_prefix} 商品 {item_id} 在第 {page_number} 页在售商品列表中命中，归属校验通过")
                    return True

                if len(current_items) < page_size:
                    break
        except Exception as e:
            logger.error(f"{log_prefix} 刷新在售商品列表进行归属校验失败: {self._safe_str(e)}")

        return bool(_db_host().get_item_info(self.cookie_id, item_id))
    async def save_item_info_to_db(self, item_id: str, item_detail: str = None, item_title: str = None):
        """保存商品信息到数据库

        Args:
            item_id: 商品ID
            item_detail: 商品详情内容（可以是任意格式的文本）
            item_title: 商品标题
        """
        try:
            # 跳过以 auto_ 开头的商品ID
            if item_id and item_id.startswith('auto_'):
                logger.warning(f"跳过保存自动生成的商品ID: {item_id}")
                return

            # 验证：如果只有商品ID，没有商品标题和商品详情，则不插入数据库
            if not item_title and not item_detail:
                logger.warning(f"跳过保存商品信息：缺少商品标题和详情 - {item_id}")
                return

            # 如果有商品标题但没有详情，也跳过（根据需求，需要同时有标题和详情）
            if not item_title or not item_detail:
                logger.warning(f"跳过保存商品信息：商品标题或详情不完整 - {item_id}")
                return

            from db_manager import db_manager

            # 直接使用传入的详情内容
            item_data = item_detail

            # 保存到数据库
            success = _db_package().save_item_info(self.cookie_id, item_id, item_data)
            if success:
                logger.info(f"商品信息已保存到数据库: {item_id}")
            else:
                logger.warning(f"保存商品信息到数据库失败: {item_id}")

        except Exception as e:
            logger.error(f"保存商品信息到数据库异常: {self._safe_str(e)}")
    async def save_item_detail_only(self, item_id, item_detail):
        """仅保存商品详情（不影响标题等基本信息）"""
        try:
            from db_manager import db_manager

            # 使用专门的详情更新方法
            success = _db_package().update_item_detail(self.cookie_id, item_id, item_detail)

            if success:
                logger.info(f"商品详情已更新: {item_id}")
            else:
                logger.warning(f"更新商品详情失败: {item_id}")

            return success

        except Exception as e:
            logger.error(f"更新商品详情异常: {self._safe_str(e)}")
            return False
    async def fetch_item_detail_from_api(self, item_id: str, force_refresh: bool = False) -> str:
        """获取商品详情（使用浏览器获取，支持24小时缓存）

        Args:
            item_id: 商品ID
            force_refresh: 是否绕过缓存强制拉取最新详情

        Returns:
            str: 商品详情文本，获取失败返回空字符串
        """
        try:
            # 检查是否启用自动获取功能
            from config import config
            auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})

            if not auto_fetch_config.get('enabled', True):
                logger.warning(f"自动获取商品详情功能已禁用: {item_id}")
                return ""

            # 1. 首先检查缓存（24小时有效）
            if not force_refresh:
                async with self._item_detail_cache_lock:
                    if item_id in self._item_detail_cache:
                        cache_data = self._item_detail_cache[item_id]
                        cache_time = cache_data['timestamp']
                        current_time = time.time()

                        # 检查缓存是否在24小时内
                        if current_time - cache_time < self._item_detail_cache_ttl:
                            # 更新访问时间（用于LRU）
                            cache_data['access_time'] = current_time
                            logger.info(f"从缓存获取商品详情: {item_id}")
                            return cache_data['detail']
                        else:
                            # 缓存过期，删除
                            del self._item_detail_cache[item_id]
                            logger.warning(f"缓存已过期，删除: {item_id}")
            else:
                logger.info(f"强制刷新商品详情，跳过缓存: {item_id}")

            # 2. 尝试使用浏览器获取商品详情
            detail_from_browser = await self._fetch_item_detail_from_browser(item_id)
            if detail_from_browser:
                # 保存到缓存（带大小限制）
                await self._add_to_item_cache(item_id, detail_from_browser)
                logger.info(f"成功通过浏览器获取商品详情: {item_id}, 长度: {len(detail_from_browser)}")
                return detail_from_browser

            # 浏览器获取失败
            logger.warning(f"浏览器获取商品详情失败: {item_id}")
            return ""

        except Exception as e:
            logger.error(f"获取商品详情异常: {item_id}, 错误: {self._safe_str(e)}")
            return ""
    async def _add_to_item_cache(self, item_id: str, detail: str):
        """添加商品详情到缓存，实现LRU策略和大小限制
        
        Args:
            item_id: 商品ID
            detail: 商品详情
        """
        async with self._item_detail_cache_lock:
            current_time = time.time()
            
            # 检查缓存大小，如果超过限制则清理
            if len(self._item_detail_cache) >= self._item_detail_cache_max_size:
                # 使用LRU策略删除最久未访问的项
                if self._item_detail_cache:
                    # 找到最久未访问的项
                    oldest_item = min(
                        self._item_detail_cache.items(),
                        key=lambda x: x[1].get('access_time', x[1]['timestamp'])
                    )
                    oldest_item_id = oldest_item[0]
                    del self._item_detail_cache[oldest_item_id]
                    logger.warning(f"缓存已满，删除最旧项: {oldest_item_id}")
            
            # 添加新项到缓存
            self._item_detail_cache[item_id] = {
                'detail': detail,
                'timestamp': current_time,
                'access_time': current_time
            }
            logger.warning(f"添加商品详情到缓存: {item_id}, 当前缓存大小: {len(self._item_detail_cache)}")
    @classmethod
    async def _cleanup_item_cache(cls):
        """清理过期的商品详情缓存"""
        try:
            async with cls._item_detail_cache_lock:
                # 在持有锁时也要能响应取消信号
                await asyncio.sleep(0)
                
                current_time = time.time()
                expired_items = []
                
                # 找出所有过期的项
                for item_id, cache_data in cls._item_detail_cache.items():
                    # 在循环中也要能响应取消信号
                    await asyncio.sleep(0)
                    if current_time - cache_data['timestamp'] >= cls._item_detail_cache_ttl:
                        expired_items.append(item_id)
                
                # 删除过期项
                for item_id in expired_items:
                    await asyncio.sleep(0)  # 让出控制权
                    del cls._item_detail_cache[item_id]
                
                if expired_items:
                    logger.info(f"清理了 {len(expired_items)} 个过期的商品详情缓存")
                
                return len(expired_items)
        except asyncio.CancelledError:
            # 如果被取消，确保锁能正确释放
            raise
    async def _fetch_item_detail_from_browser(self, item_id: str) -> str:
        """使用浏览器获取商品详情"""
        playwright = None
        browser = None
        try:
            from playwright.async_api import async_playwright

            logger.info(f"开始使用浏览器获取商品详情: {item_id}")

            playwright = await async_playwright().start()

            # 启动浏览器（参照order_detail_fetcher的配置）
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-features=TranslateUI',
                '--disable-ipc-flooding-protection',
                '--disable-extensions',
                '--disable-default-apps',
                '--disable-sync',
                '--disable-translate',
                '--hide-scrollbars',
                '--mute-audio',
                '--no-default-browser-check',
                '--no-pings'
            ]

            # 在Docker环境中添加额外参数
            if _host.os.getenv('DOCKER_ENV'):
                browser_args.extend([
                    # '--single-process',  # 注释掉，避免多用户并发时的进程冲突和资源泄漏
                    '--disable-background-networking',
                    '--disable-client-side-phishing-detection',
                    '--disable-hang-monitor',
                    '--disable-popup-blocking',
                    '--disable-prompt-on-repost',
                    '--disable-web-resources',
                    '--metrics-recording-only',
                    '--safebrowsing-disable-auto-update',
                    '--enable-automation',
                    '--password-store=basic',
                    '--use-mock-keychain'
                ])

            browser = await playwright.chromium.launch(
                headless=True,  # 移动模式使用无头模式
                args=browser_args
            )

            # 创建移动设备浏览器上下文（模拟iPhone）
            context = await browser.new_context(
                viewport={'width': 375, 'height': 812},  # iPhone X/11/12 尺寸
                user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 AliApp(TB/11.15.0)',
                device_scale_factor=3,  # iPhone 的屏幕缩放比例
                is_mobile=True,
                has_touch=True
            )

            # 设置Cookie
            cookies = []
            for cookie_pair in self.cookies_str.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })

            await context.add_cookies(cookies)
            logger.info(f"已设置 {len(cookies)} 个Cookie（移动模式）")

            # 创建页面
            page = await context.new_page()

            # 构造移动版商品详情页面URL
            item_url = f"https://h5.m.goofish.com/item?id={item_id}"
            logger.info(f"访问移动版商品页面: {item_url}")

            # 访问页面
            await page.goto(item_url, wait_until='networkidle', timeout=30000)

            # 等待页面完全加载
            await asyncio.sleep(2)

            # 获取商品详情内容
            detail_text = ""
            try:
                # 移动版页面选择器列表（按优先级排序）
                selectors = [
                    '.detailDesc--descText--1FMDTCm',  # 移动版商品详情主选择器
                    'span.rax-text-v2.detailDesc--descText--1FMDTCm',  # 完整选择器
                    '[class*="detailDesc--descText"]',  # 匹配包含detailDesc--descText的类名
                    '[class*="descText"]',  # 匹配包含descText的类名
                    '.desc--GaIUKUQY',  # PC版选择器（备用）
                    '.detail-desc',     # 常见的详情选择器
                    '.item-desc',       # 商品描述
                    '[class*="desc"]',  # 包含desc的类名
                ]
                
                for selector in selectors:
                    try:
                        # 尝试等待元素出现（短超时）
                        await page.wait_for_selector(selector, timeout=3000)
                        detail_element = await page.query_selector(selector)
                        if detail_element:
                            detail_text = await detail_element.inner_text()
                            if detail_text and len(detail_text.strip()) > 0:
                                logger.info(f"成功获取商品详情（选择器: {selector}）: {item_id}, 长度: {len(detail_text)}")
                                return detail_text.strip()
                    except Exception as e:
                        logger.debug(f"选择器 {selector} 未找到: {self._safe_str(e)}")
                        continue
                
                # 如果所有选择器都失败，尝试获取整个页面的文本内容
                logger.warning(f"未找到特定详情元素，尝试获取整个页面内容: {item_id}")
                body_text = await page.inner_text('body')
                if body_text:
                    logger.info(f"获取到页面整体内容: {item_id}, 长度: {len(body_text)}")
                    return body_text.strip()
                else:
                    logger.warning(f"未找到商品详情元素: {item_id}")

            except Exception as e:
                logger.warning(f"获取商品详情元素失败: {item_id}, 错误: {self._safe_str(e)}")

            return ""

        except Exception as e:
            logger.error(f"浏览器获取商品详情异常: {item_id}, 错误: {self._safe_str(e)}")
            return ""
        finally:
            # 确保资源被正确清理
            try:
                if browser:
                    await browser.close()
                    logger.warning(f"Browser已关闭: {item_id}")
            except Exception as e:
                logger.warning(f"关闭browser时出错: {self._safe_str(e)}")
            
            try:
                if playwright:
                    await playwright.stop()
                    logger.warning(f"Playwright已停止: {item_id}")
            except Exception as e:
                logger.warning(f"停止playwright时出错: {self._safe_str(e)}")
    async def save_items_list_to_db(self, items_list, sync_item_details=False):
        """批量保存商品列表信息到数据库（并发安全）

        Args:
            items_list: 从get_item_list_info获取的商品列表
            sync_item_details: 是否同步已存在商品的最新详情
        """
        try:
            from db_manager import db_manager

            # 准备批量数据，区分新商品和需要更新的商品
            batch_new_data = []  # 新商品，保存所有信息
            batch_update_data = []  # 已有商品，只更新标题和价格
            items_need_detail = []  # 需要获取或同步详情的商品列表

            for item in items_list:
                item_id = item.get('id')
                if not item_id or item_id.startswith('auto_'):
                    continue

                # 构造商品详情数据
                item_detail = {
                    'title': item.get('title', ''),
                    'price': item.get('price', ''),
                    'price_text': item.get('price_text', ''),
                    'category_id': item.get('category_id', ''),
                    'auction_type': item.get('auction_type', ''),
                    'item_status': item.get('item_status', 0),
                    'detail_url': item.get('detail_url', ''),
                    'pic_info': item.get('pic_info', {}),
                    'detail_params': item.get('detail_params', {}),
                    'track_params': item.get('track_params', {}),
                    'item_label_data': item.get('item_label_data', {}),
                    'card_type': item.get('card_type', 0)
                }

                # 检查数据库中是否已有该商品
                existing_item = _db_package().get_item_info(self.cookie_id, item_id)
                
                if existing_item:
                    # 商品已存在，先更新标题和价格；商品详情按同步模式单独处理
                    batch_update_data.append({
                        'cookie_id': self.cookie_id,
                        'item_id': item_id,
                        'item_title': item.get('title', ''),
                        'item_price': item.get('price_text', ''),
                        'item_category': str(item.get('category_id', ''))
                    })
                    if sync_item_details:
                        items_need_detail.append({
                            'item_id': item_id,
                            'item_title': item.get('title', '')
                        })
                    logger.debug(f"商品 {item_id} 已存在，将更新标题和价格")
                else:
                    # 新商品，保存所有信息
                    batch_new_data.append({
                        'cookie_id': self.cookie_id,
                        'item_id': item_id,
                        'item_title': item.get('title', ''),
                        'item_description': '',  # 暂时为空
                        'item_category': str(item.get('category_id', '')),
                        'item_price': item.get('price_text', ''),
                        'item_detail': json.dumps(item_detail, ensure_ascii=False)
                    })
                    
                    # 新商品需要获取详情
                    items_need_detail.append({
                        'item_id': item_id,
                        'item_title': item.get('title', '')
                    })
                    logger.debug(f"商品 {item_id} 是新商品，将保存完整信息")

            saved_count = 0
            
            # 保存新商品
            if batch_new_data:
                new_count = _db_package().batch_save_item_basic_info(batch_new_data)
                logger.info(f"新增商品信息: {new_count}/{len(batch_new_data)} 个")
                saved_count += new_count
            
            # 更新已有商品的标题和价格
            if batch_update_data:
                update_count = _db_package().batch_update_item_title_price(batch_update_data)
                logger.info(f"更新商品标题和价格: {update_count}/{len(batch_update_data)} 个")
                saved_count += update_count

            # 异步获取商品详情
            if items_need_detail:
                from config import config
                auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})

                if auto_fetch_config.get('enabled', True):
                    action_text = '同步最新详情' if sync_item_details else '获取缺失详情'
                    logger.info(f"准备为 {len(items_need_detail)} 个商品{action_text}...")
                    detail_success_count = await self._fetch_item_details(
                        items_need_detail,
                        force_refresh=sync_item_details,
                    )
                    logger.info(f"成功为 {detail_success_count}/{len(items_need_detail)} 个商品{action_text}")
                else:
                    logger.info(f"有 {len(items_need_detail)} 个商品需要获取详情，但自动获取功能已禁用")

            return saved_count

        except Exception as e:
            logger.error(f"批量保存商品信息异常: {self._safe_str(e)}")
            return 0
    async def _fetch_item_details(self, items_need_detail, force_refresh=False):
        """批量获取或同步商品详情

        Args:
            items_need_detail: 需要获取详情的商品列表
            force_refresh: 是否绕过缓存强制拉取最新详情

        Returns:
            int: 成功获取详情的商品数量
        """
        success_count = 0

        try:
            from db_manager import db_manager
            from config import config

            # 从配置获取并发数量和延迟时间
            auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})
            max_concurrent = auto_fetch_config.get('max_concurrent', 3)
            retry_delay = auto_fetch_config.get('retry_delay', 0.5)

            # 限制并发数量，避免对API服务器造成压力
            semaphore = asyncio.Semaphore(max_concurrent)

            async def fetch_single_item_detail(item_info):
                async with semaphore:
                    try:
                        item_id = item_info['item_id']
                        item_title = item_info['item_title']

                        # 获取商品详情
                        item_detail_text = await self.fetch_item_detail_from_api(
                            item_id,
                            force_refresh=force_refresh,
                        )

                        if item_detail_text:
                            # 保存详情到数据库
                            success = await self.save_item_detail_only(item_id, item_detail_text)
                            if success:
                                logger.info(f"✅ 成功获取并保存商品详情: {item_id} - {item_title}")
                                return 1
                            else:
                                logger.warning(f"❌ 获取详情成功但保存失败: {item_id}")
                        else:
                            logger.warning(f"❌ 未能获取商品详情: {item_id} - {item_title}")

                        # 添加延迟，避免请求过于频繁
                        await asyncio.sleep(retry_delay)
                        return 0

                    except Exception as e:
                        logger.error(f"获取单个商品详情异常: {item_info.get('item_id', 'unknown')}, 错误: {self._safe_str(e)}")
                        return 0

            # 并发获取所有商品详情
            tasks = [fetch_single_item_detail(item_info) for item_info in items_need_detail]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 统计成功数量
            for result in results:
                if isinstance(result, int):
                    success_count += result
                elif isinstance(result, Exception):
                    logger.error(f"获取商品详情任务异常: {result}")

            return success_count

        except Exception as e:
            logger.error(f"批量获取商品详情异常: {self._safe_str(e)}")
            return success_count
    async def get_item_info(self, item_id, retry_count=0):
        """获取商品信息，自动处理token失效的情况"""
        if retry_count >= 4:  # 最多重试3次
            logger.error("获取商品信息失败，重试次数过多")
            return {"error": "获取商品信息失败，重试次数过多"}

        # 确保session已创建
        if not self.session:
            await self.create_session()

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.taobao.idle.pc.detail',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }

        data_val = '{"itemId":"' + item_id + '"}'
        data = {
            'data': data_val,
        }

        # 始终从最新的cookies中获取_m_h5_tk token（刷新后cookies会被更新）
        token = _host.trans_cookies(self.cookies_str).get('_m_h5_tk', '').split('_')[0] if _host.trans_cookies(self.cookies_str).get('_m_h5_tk') else ''

        if token:
            logger.warning(f"使用cookies中的_m_h5_tk token: {self._mask_secret_value(token, head=6, tail=4)}")
        else:
            logger.warning("cookies中没有找到_m_h5_tk token")

        from utils.xianyu_utils import generate_sign
        sign = _host.generate_sign(params['t'], token, data_val)
        params['sign'] = sign

        try:
            async with self.session.post(
                'https://h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail/1.0/',
                params=params,
                data=data
            ) as response:
                res_json = await response.json()

                if await self._apply_response_cookie_updates(response.headers, "item_detail"):
                    logger.warning("已更新Cookie到数据库")

                logger.warning(f"商品信息获取成功: {res_json}")
                # 检查返回状态
                if isinstance(res_json, dict):
                    ret_value = res_json.get('ret', [])
                    # 检查ret是否包含成功信息
                    if not any('SUCCESS::调用成功' in ret for ret in ret_value):
                        logger.warning(f"商品信息API调用失败，错误信息: {ret_value}")

                        await asyncio.sleep(0.5)
                        return await self.get_item_info(item_id, retry_count + 1)
                    else:
                        logger.warning(f"商品信息获取成功: {item_id}")
                        return res_json
                else:
                    logger.error(f"商品信息API返回格式异常: {res_json}")
                    return await self.get_item_info(item_id, retry_count + 1)

        except Exception as e:
            logger.error(f"商品信息API请求异常: {self._safe_str(e)}")
            await asyncio.sleep(0.5)
            return await self.get_item_info(item_id, retry_count + 1)
    async def _is_item_owned_by_self(self, item_id: str):
        """判断商品是否属于本账号，用于区分本账号在会话中的买家/卖家身份。

        Returns:
            True: 本账号的商品（自己是卖家）
            False: 别人的商品（本账号主动咨询/购买，自己是买家）
            None: 无法判断（缺商品ID或接口失败）
        判断顺序：内存缓存 -> 本地 item_info 表（只存本账号商品）-> 商品详情API比对卖家ID。
        """
        if not item_id:
            return None

        cache = getattr(self, '_item_ownership_cache', None)
        if cache is None:
            cache = self._item_ownership_cache = {}
        if item_id in cache:
            return cache[item_id]

        # 本地 item_info 表只存本账号的商品，命中即认定为自己的
        try:
            from db_manager import db_manager
            if _db_package().get_item_info(self.cookie_id, item_id):
                cache[item_id] = True
                return True
        except Exception as db_e:
            logger.debug(f"【{self.cookie_id}】查询本地商品归属失败: {self._safe_str(db_e)}")

        # 本地没有：调商品详情接口比对卖家ID（结果缓存，避免重复请求）
        try:
            detail = await self.get_item_info(item_id)
            data = (detail or {}).get('data') or {}
            seller = data.get('sellerDO') or {}
            seller_id = str(
                seller.get('sellerId')
                or seller.get('userId')
                or (data.get('itemDO') or {}).get('sellerId')
                or ''
            ).strip()
            if seller_id:
                owned = seller_id == str(self.myid)
                cache[item_id] = owned
                if not owned:
                    logger.info(
                        f"【{self.cookie_id}】商品 {item_id} 属于卖家 {seller_id}，"
                        f"本账号({self.myid})在该会话中是买家身份"
                    )
                return owned
        except Exception as api_e:
            logger.warning(f"【{self.cookie_id}】判断商品归属失败（默认放行）: {self._safe_str(api_e)}")

        return None
    def extract_item_id_from_message(self, message):
        """从消息中提取商品ID的辅助方法"""
        try:
            # 注意: message["1"] 是会话ID(chat_id/cid)，格式如 "56226853668@goofish"
            # 不能从中提取商品ID，否则会把chat_id误当作item_id

            # 方法1: 从message["3"]中提取
            message_3 = message.get('3', {})
            if isinstance(message_3, dict):

                # 从extension中提取
                if 'extension' in message_3:
                    extension = message_3['extension']
                    if isinstance(extension, dict):
                        item_id = extension.get('itemId') or extension.get('item_id')
                        if item_id:
                            logger.info(f"从extension中提取商品ID: {item_id}")
                            return item_id

                # 从bizData中提取
                if 'bizData' in message_3:
                    biz_data = message_3['bizData']
                    if isinstance(biz_data, dict):
                        item_id = biz_data.get('itemId') or biz_data.get('item_id')
                        if item_id:
                            logger.info(f"从bizData中提取商品ID: {item_id}")
                            return item_id

                # 从其他可能的字段中提取
                for key, value in message_3.items():
                    if isinstance(value, dict):
                        item_id = value.get('itemId') or value.get('item_id')
                        if item_id:
                            logger.info(f"从{key}字段中提取商品ID: {item_id}")
                            return item_id

                # 从消息内容中提取数字ID
                content = message_3.get('content', '')
                if isinstance(content, str) and content:
                    id_match = re.search(r'(\d{10,})', content)
                    if id_match:
                        logger.info(f"【{self.cookie_id}】从消息内容中提取商品ID: {id_match.group(1)}")
                        return id_match.group(1)

            # 方法2: 遍历整个消息结构查找可能的商品ID
            # 跳过的字段: "1" 是会话ID(chat_id/cid)，不包含商品ID
            # 跳过可能包含非商品ID的字段
            skip_keys = {'1', 'tradeId', 'trade_id', 'bizId', 'biz_id', 'orderId', 'order_id',
                        'userId', 'user_id', 'senderId', 'sender_id', 'receiverId', 'receiver_id',
                        'chatId', 'chat_id', 'conversationId', 'conversation_id', 'msgId', 'msg_id'}

            def find_item_id_recursive(obj, path=""):
                if isinstance(obj, dict):
                    # 只查找明确命名为 itemId 的字段（不查找通用的 'id' 字段，避免误提取 tradeId 等）
                    for key in ['itemId', 'item_id']:
                        if key in obj and isinstance(obj[key], (str, int)):
                            value = str(obj[key])
                            if len(value) >= 10 and value.isdigit():
                                logger.info(f"从{path}.{key}中提取商品ID: {value}")
                                return value

                    # 递归查找（跳过chat_id和其他非商品ID字段）
                    for key, value in obj.items():
                        if key in skip_keys:
                            continue
                        result = find_item_id_recursive(value, f"{path}.{key}" if path else key)
                        if result:
                            return result

                elif isinstance(obj, str):
                    # 跳过chat_id格式的字符串（如 "56226853668@goofish"）
                    if '@goofish' in obj or '@xianyu' in obj:
                        return None
                    # 只从URL中提取itemId参数，不从普通字符串中提取数字（避免误提取）
                    if 'itemId=' in obj:
                        id_match = re.search(r'itemId=(\d{10,})', obj)
                        if id_match:
                            logger.info(f"从{path}的URL参数中提取商品ID: {id_match.group(1)}")
                            return id_match.group(1)

                return None

            result = find_item_id_recursive(message)
            if result:
                return result

            logger.warning("所有方法都未能提取到商品ID")
            return None

        except Exception as e:
            logger.error(f"提取商品ID失败: {self._safe_str(e)}")
            return None
    async def get_item_specific_reply(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str = None) -> str:
        """获取指定商品回复内容"""
        if not item_id:
            return None

        try:
            from db_manager import db_manager

            item_reply = _db_package().get_item_reply(self.cookie_id, item_id)
            if not item_reply or not item_reply.get('reply_content'):
                return None

            reply_content = item_reply['reply_content']
            logger.info(f"【{self.cookie_id}】使用指定商品回复: 商品ID={item_id}")

            try:
                formatted_reply = reply_content.format(
                    send_user_name=send_user_name,
                    send_user_id=send_user_id,
                    send_message=send_message,
                    item_id=item_id
                )
                logger.info(f"【{self.cookie_id}】指定商品回复内容: {formatted_reply}")
                return formatted_reply
            except Exception as format_error:
                logger.error(f"指定商品回复变量替换失败: {self._safe_str(format_error)}")
                return reply_content

        except Exception as e:
            logger.error(f"获取指定商品回复失败: {self._safe_str(e)}")
            return None
    async def get_item_list_info(self, page_number=1, page_size=20, retry_count=0, sync_item_details=False):
        """获取商品信息，自动处理token失效的情况

        Args:
            page_number (int): 页码，从1开始
            page_size (int): 每页数量，默认20
            retry_count (int): 重试次数，内部使用
            sync_item_details (bool): 是否同步已存在商品的最新详情
        """
        if retry_count >= 4:  # 最多重试3次
            logger.error("获取商品信息失败，重试次数过多")
            return {"error": "获取商品信息失败，重试次数过多"}

        # 确保session已创建
        if not self.session:
            await self.create_session()

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.idle.web.xyh.item.list',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
            'spm_pre': 'a21ybx.collection.menu.1.272b5141NafCNK'
        }

        data = {
            'needGroupInfo': False,
            'pageNumber': page_number,
            'pageSize': page_size,
            'groupName': '在售',
            'groupId': '58877261',
            'defaultGroup': True,
            "userId": self.myid
        }

        # 始终从最新的cookies中获取_m_h5_tk token（刷新后cookies会被更新）
        token = _host.trans_cookies(self.cookies_str).get('_m_h5_tk', '').split('_')[0] if _host.trans_cookies(self.cookies_str).get('_m_h5_tk') else ''

        logger.warning(f"准备获取商品列表，token: {token}")
        if token:
            logger.warning(f"使用cookies中的_m_h5_tk token: {self._mask_secret_value(token, head=6, tail=4)}")
        else:
            logger.warning("cookies中没有找到_m_h5_tk token")

        # 生成签名
        data_val = json.dumps(data, separators=(',', ':'))
        sign = _host.generate_sign(params['t'], token, data_val)
        params['sign'] = sign

        try:
            async with self.session.post(
                'https://h5api.m.goofish.com/h5/mtop.idle.web.xyh.item.list/1.0/',
                params=params,
                data={'data': data_val}
            ) as response:
                res_json = await response.json()

                if await self._apply_response_cookie_updates(response.headers, "item_list"):
                    logger.warning("已更新Cookie到数据库")

                logger.info(f"商品信息获取响应: {res_json}")

                # 检查响应是否成功
                if res_json.get('ret') and res_json['ret'][0] == 'SUCCESS::调用成功':
                    items_data = res_json.get('data', {})
                    # 从cardList中提取商品信息
                    card_list = items_data.get('cardList', [])

                    # 解析cardList中的商品信息
                    items_list = []
                    for card in card_list:
                        card_data = card.get('cardData', {})
                        if card_data:
                            # 提取商品基本信息
                            item_info = {
                                'id': card_data.get('id', ''),
                                'title': card_data.get('title', ''),
                                'price': card_data.get('priceInfo', {}).get('price', ''),
                                'price_text': card_data.get('priceInfo', {}).get('preText', '') + card_data.get('priceInfo', {}).get('price', ''),
                                'category_id': card_data.get('categoryId', ''),
                                'auction_type': card_data.get('auctionType', ''),
                                'item_status': card_data.get('itemStatus', 0),
                                'detail_url': card_data.get('detailUrl', ''),
                                'pic_info': card_data.get('picInfo', {}),
                                'detail_params': card_data.get('detailParams', {}),
                                'track_params': card_data.get('trackParams', {}),
                                'item_label_data': card_data.get('itemLabelDataVO', {}),
                                'card_type': card.get('cardType', 0)
                            }
                            items_list.append(item_info)

                    logger.info(f"成功获取到 {len(items_list)} 个商品")

                    # 打印商品详细信息到控制台
                    print("\n" + "="*80)
                    print(f"📦 账号 {self.myid} 的商品列表 (第{page_number}页，{len(items_list)} 个商品)")
                    print("="*80)

                    for i, item in enumerate(items_list, 1):
                        print(f"\n🔸 商品 {i}:")
                        print(f"   商品ID: {item.get('id', 'N/A')}")
                        print(f"   商品标题: {item.get('title', 'N/A')}")
                        print(f"   价格: {item.get('price_text', 'N/A')}")
                        print(f"   分类ID: {item.get('category_id', 'N/A')}")
                        print(f"   商品状态: {item.get('item_status', 'N/A')}")
                        print(f"   拍卖类型: {item.get('auction_type', 'N/A')}")
                        print(f"   详情链接: {item.get('detail_url', 'N/A')}")
                        if item.get('pic_info'):
                            pic_info = item['pic_info']
                            print(f"   图片信息: {pic_info.get('width', 'N/A')}x{pic_info.get('height', 'N/A')}")
                            print(f"   图片链接: {pic_info.get('picUrl', 'N/A')}")
                        print(f"   完整信息: {json.dumps(item, ensure_ascii=False, indent=2)}")

                    print("\n" + "="*80)
                    print("✅ 商品列表获取完成")
                    print("="*80)

                    # 自动保存商品信息到数据库
                    if items_list:
                        saved_count = await self.save_items_list_to_db(
                            items_list,
                            sync_item_details=sync_item_details,
                        )
                        logger.info(f"已将 {saved_count} 个商品信息保存到数据库")

                    return {
                        "success": True,
                        "page_number": page_number,
                        "page_size": page_size,
                        "current_count": len(items_list),
                        "items": items_list,
                        "saved_count": saved_count if items_list else 0,
                        "raw_data": items_data  # 保留原始数据以备调试
                    }
                else:
                    # 检查是否是token失效
                    error_msg = res_json.get('ret', [''])[0] if res_json.get('ret') else ''
                    if 'FAIL_SYS_TOKEN_EXOIRED' in error_msg or 'token' in error_msg.lower():
                        logger.warning(f"Token失效，准备重试: {error_msg}")
                        await asyncio.sleep(0.5)
                        return await self.get_item_list_info(
                            page_number,
                            page_size,
                            retry_count + 1,
                            sync_item_details=sync_item_details,
                        )
                    else:
                        logger.error(f"获取商品信息失败: {res_json}")
                        return {"error": f"获取商品信息失败: {error_msg}"}

        except Exception as e:
            logger.error(f"商品信息API请求异常: {self._safe_str(e)}")
            await asyncio.sleep(0.5)
            return await self.get_item_list_info(
                page_number,
                page_size,
                retry_count + 1,
                sync_item_details=sync_item_details,
            )
    async def get_all_items(self, page_size=20, max_pages=None, sync_item_details=False):
        """获取所有商品信息（自动分页）

        Args:
            page_size (int): 每页数量，默认20
            max_pages (int): 最大页数限制，None表示无限制
            sync_item_details (bool): 是否同步已存在商品的最新详情

        Returns:
            dict: 包含所有商品信息的字典
        """
        all_items = []
        page_number = 1
        total_saved = 0

        logger.info(f"开始获取所有商品信息，每页{page_size}条")

        while True:
            if max_pages and page_number > max_pages:
                logger.info(f"达到最大页数限制 {max_pages}，停止获取")
                break

            logger.info(f"正在获取第 {page_number} 页...")
            result = await self.get_item_list_info(
                page_number,
                page_size,
                sync_item_details=sync_item_details,
            )

            if not result.get("success"):
                logger.error(f"获取第 {page_number} 页失败: {result}")
                break

            current_items = result.get("items", [])
            if not current_items:
                logger.info(f"第 {page_number} 页没有数据，获取完成")
                break

            all_items.extend(current_items)
            total_saved += result.get("saved_count", 0)

            logger.info(f"第 {page_number} 页获取到 {len(current_items)} 个商品")

            # 如果当前页商品数量少于页面大小，说明已经是最后一页
            if len(current_items) < page_size:
                logger.info(f"第 {page_number} 页商品数量({len(current_items)})少于页面大小({page_size})，获取完成")
                break

            page_number += 1

            # 添加延迟避免请求过快
            await asyncio.sleep(1)

        logger.info(f"所有商品获取完成，共 {len(all_items)} 个商品，保存了 {total_saved} 个")

        return {
            "success": True,
            "total_pages": page_number,
            "total_count": len(all_items),
            "total_saved": total_saved,
            "items": all_items
        }
    def _get_item_polish_module(self):
        from item_polish_module import ItemPolishModule

        return ItemPolishModule(self)
    async def polish_item(self, item_id, retry_count=0):
        """擦亮单个商品。"""
        return await self._get_item_polish_module().polish_item(item_id, retry_count)
    async def _polish_item_backup(self, item_id):
        """使用备用API擦亮商品。"""
        return await self._get_item_polish_module()._polish_item_backup(item_id)
    async def polish_all_items(self):
        """擦亮所有在售商品。"""
        return await self._get_item_polish_module().polish_all_items()