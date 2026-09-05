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


class TokenMixin:
    """mtop token 刷新循环/预检/错误分类。"""

    async def preflight_token_after_manual_refresh(self) -> str:
        """手动刷新成功后的 token 预检，确认新实例可直接完成初始化。

        🔧 增加重试机制：密码登录获取的 Cookie 可能需要短暂时间在服务端生效，
        首次 Token 刷新可能因 session 未就绪而失败，等待后重试可提高成功率。
        """
        logger.info(f"【{self.cookie_id}】开始执行手动刷新后的Token预检...")
        self.last_message_received_time = 0

        max_preflight_retries = 3
        for attempt in range(1, max_preflight_retries + 1):
            token = await self.refresh_token(allow_password_login_recovery=False)
            if token:
                self.cache_auth_prewarmed_token(self.cookie_id, token, source='manual_refresh_handoff')
                logger.info(f"【{self.cookie_id}】手动刷新后的Token预检成功（第{attempt}次），已缓存预热token供新实例复用")
                return token

            if attempt < max_preflight_retries:
                wait_secs = 2.0 * attempt
                logger.warning(
                    f"【{self.cookie_id}】Token预检第{attempt}次失败（状态: {self.last_token_refresh_status}），"
                    f"等待{wait_secs:.0f}秒后重试（Cookie可能尚未在服务端生效）"
                )
                await asyncio.sleep(wait_secs)

        raise _host.InitAuthError(f"手动刷新后的Token预检失败，状态: {self.last_token_refresh_status or 'unknown'}")
    async def refresh_token(self, captcha_retry_count: int = 0, allow_password_login_recovery: bool = True):
        if self.token_refresh_lock.locked():
            logger.info(f"【{self.cookie_id}】Token刷新已有执行中任务，等待当前流程完成后复用结果")

        async with self.token_refresh_lock:
            dedup_window = max(5, int(_host.RISK_CONTROL.get('token_refresh_dedup_window_seconds', 60) or 60))
            if (
                captcha_retry_count == 0 and
                self.current_token and
                self.last_token_refresh_status == "success" and
                (time.time() - self.last_token_refresh_time) < dedup_window
            ):
                logger.info(f"【{self.cookie_id}】最近{dedup_window}秒内已有成功的Token刷新结果，直接复用当前Token")
                return self.current_token
            if captcha_retry_count == 0 and self._should_skip_token_refresh_for_login_backoff():
                return None
            return await self._refresh_token_impl(
                captcha_retry_count,
                allow_password_login_recovery=allow_password_login_recovery,
            )
    async def _refresh_token_impl(self, captcha_retry_count: int = 0, post_slider_session_grace_used: bool = False,
                                  allow_password_login_recovery: bool = True,
                                  manual_refresh_browser_stabilization_used: bool = False,
                                  post_slider_session_retry_count: int = 0):
        """刷新token

        Args:
            captcha_retry_count: 滑块验证重试次数，用于防止无限递归
        """
        # 初始化通知发送标志，避免重复发送通知
        notification_sent = False
        
        try:
            logger.info(f"【{self.cookie_id}】开始刷新token... (滑块验证重试次数: {captcha_retry_count})")
            # 标记本次刷新状态
            self.last_token_refresh_status = "started"
            self.last_token_refresh_error_message = None
            # 重置“刷新流程内已重启”标记，避免多次重启
            self.restarted_in_browser_refresh = False

            # 检查滑块验证重试次数，防止无限递归
            if captcha_retry_count >= self.max_captcha_verification_count:
                logger.error(f"【{self.cookie_id}】滑块验证重试次数已达上限 ({self.max_captcha_verification_count})，停止重试")
                self.last_token_refresh_status = "captcha_max_retries_exceeded"
                self._clear_pending_slider_success_notice("滑块重试次数达到上限")
                await self.send_token_refresh_notification(
                    f"滑块验证重试次数已达上限，请手动处理",
                    "captcha_max_retries_exceeded"
                )
                notification_sent = True
                return None

            # 【消息接收检查】检查是否在消息接收后的冷却时间内，与 cookie_refresh_loop 保持一致
            current_time = time.time()
            time_since_last_message = current_time - self.last_message_received_time
            if self.last_message_received_time > 0 and time_since_last_message < self.message_cookie_refresh_cooldown:
                remaining_time = self.message_cookie_refresh_cooldown - time_since_last_message
                remaining_minutes = int(remaining_time // 60)
                remaining_seconds = int(remaining_time % 60)
                logger.info(f"【{self.cookie_id}】收到消息后冷却中，放弃本次token刷新，还需等待 {remaining_minutes}分{remaining_seconds}秒")
                # 标记为因冷却而跳过（正常情况）
                self.last_token_refresh_status = "skipped_cooldown"
                return None

            if self._should_skip_token_refresh_for_login_backoff(current_time):
                return None

            # 【重要】在刷新token前，先从数据库重新加载最新的cookie
            # 这样即使用户已经手动更新了cookie，代码也会使用最新的cookie
            logger.info(f"【{self.cookie_id}】开始执行Cookie刷新任务...")
            self._reload_latest_cookies_from_db("token刷新前")

            # 生成更精确的时间戳
            timestamp = str(int(time.time() * 1000))

            params = {
                'jsv': '2.7.2',
                'appKey': '34839810',
                't': timestamp,
                'sign': '',
                'v': '1.0',
                'type': 'originaljson',
                'accountSite': 'xianyu',
                'dataType': 'json',
                'timeout': '20000',
                'api': 'mtop.taobao.idlemessage.pc.login.token',
                'sessionOption': 'AutoLoginOnly',
                'dangerouslySetWindvaneParams': '%5Bobject%20Object%5D',
                'smToken': 'token',
                'queryToken': 'sm',
                'sm': 'sm',
                'spm_cnt': 'a21ybx.im.0.0',
                'spm_pre': 'a21ybx.home.sidebar.1.4c053da6vYwnmf',
                'log_id': '4c053da6vYwnmf'
            }
            data_val = '{"appKey":"444e9908a51d1cb236a27862abc769c9","deviceId":"' + self.device_id + '"}'
            data = {
                'data': data_val,
            }

            # 获取token
            token = _host.trans_cookies(self.cookies_str).get('_m_h5_tk', '').split('_')[0] if _host.trans_cookies(self.cookies_str).get('_m_h5_tk') else ''

            sign = _host.generate_sign(params['t'], token, data_val)
            params['sign'] = sign

            # 发送请求 - 使用与浏览器完全一致的请求头
            headers = {
                'accept': 'application/json',
                'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'cache-control': 'no-cache',
                'content-type': 'application/x-www-form-urlencoded',
                'pragma': 'no-cache',
                'priority': 'u=1, i',
                'sec-ch-ua': '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-site',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
                'referer': 'https://www.goofish.com/',
                'origin': 'https://www.goofish.com',
                'cookie': self.cookies_str
            }

            # 发送Token刷新请求
            api_url = _host.API_ENDPOINTS.get('token')
            logger.info(f"【{self.cookie_id}】正在刷新Token... API: {api_url}")
            
            # 详细调试信息（仅debug级别）
            logger.debug(f"【{self.cookie_id}】Token刷新参数: timestamp={params['t']}, sign={sign[:16]}...")

            if not self.session:
                await self.create_session()
            request_kwargs = {}
            if getattr(self, '_http_proxy_url', None):
                request_kwargs['proxy'] = self._http_proxy_url
            async with self.session.post(
                    api_url,
                    params=params,
                    data=data,
                    headers=headers,
                    timeout=_host.aiohttp.ClientTimeout(total=30),
                    **request_kwargs,
                ) as response:
                    res_json = await response.json(content_type=None)
                    # 简化日志输出
                    ret_info = res_json.get('ret', [])
                    logger.debug(f"【{self.cookie_id}】Token刷新响应: status={response.status}, ret={ret_info}")

                    response_set_cookies = self._extract_set_cookie_updates(response.headers)

                    transient_recovery_cookies_str = self.cookies_str
                    if response_set_cookies:
                        transient_recovery_cookies_str = self._build_cookie_string_with_updates(
                            self.cookies_str,
                            response_set_cookies
                        )
                        logger.info(
                            f"【{self.cookie_id}】Token预检响应携带 {len(response_set_cookies)} 个临时Cookie，"
                            f"仅用于本次恢复链路，不提前写入数据库"
                        )

                    if isinstance(res_json, dict):
                        ret_value = res_json.get('ret', [])
                        # 检查ret是否包含成功信息
                        if any('SUCCESS::调用成功' in ret for ret in ret_value):
                            if 'data' in res_json and 'accessToken' in res_json['data']:
                                if response_set_cookies:
                                    await self._apply_response_cookie_updates(response.headers, "token_refresh")
                                    logger.warning(f"【{self.cookie_id}】Token刷新成功后已更新Cookie到数据库")

                                new_token = res_json['data']['accessToken']
                                self.current_token = new_token
                                self.last_token_refresh_time = time.time()

                                # 【消息接收时间重置】Token刷新成功后重置消息接收标志，与 cookie_refresh_loop 保持一致
                                self.last_message_received_time = 0
                                logger.warning(f"【{self.cookie_id}】Token刷新成功，已重置消息接收时间标识")
                                self._clear_qr_login_grace_period()
                                self.clear_init_auth_failure_state(self.cookie_id)
                                self.last_init_failure_reason = None
                                self.last_init_failure_type = None
                                self.init_auth_failures = 0

                                logger.info(f"【{self.cookie_id}】Token刷新成功")
                                # 标记为成功
                                self.last_token_refresh_status = "success"
                                self.last_token_refresh_error_message = None
                                if self._consume_pending_slider_success_notice():
                                    await self.send_token_refresh_notification(
                                        "滑块验证通过，账号会话已恢复",
                                        "slider_recovered_success"
                                    )
                                return new_token

                    # 检查是否需要滑块验证
                    if self._need_captcha_verification(res_json):
                        qr_login_grace = self.get_qr_login_grace(self.cookie_id)
                        if qr_login_grace and not qr_login_grace.get('captcha_buffer_used'):
                            logger.warning(f"【{self.cookie_id}】扫码登录后的首轮Token刷新命中风控，执行一次浏览器侧Cookie稳定化后进入稳定期退避，避免继续挤爆")
                            _host.log_captcha_event(
                                self.cookie_id,
                                "扫码登录首轮Token刷新命中风控，执行浏览器侧稳定化后退避",
                                None,
                                f"触发场景: Token刷新, ret={res_json.get('ret', [])}"
                            )
                            self.update_qr_login_grace(
                                self.cookie_id,
                                captcha_buffer_used=True,
                                captcha_detected_at=time.time()
                            )
                            await asyncio.sleep(2)
                            stabilization_success = await self._refresh_cookies_via_browser_page(
                                transient_recovery_cookies_str,
                                restart_on_success=False
                            )
                            if stabilization_success:
                                self.update_qr_login_grace(
                                    self.cookie_id,
                                    browser_stabilized=True,
                                    browser_stabilized_at=time.time()
                                )
                                logger.info(f"【{self.cookie_id}】浏览器侧Cookie稳定化完成；不立即重试Token，等待扫码登录稳定期结束后再恢复")
                            else:
                                logger.warning(f"【{self.cookie_id}】浏览器侧Cookie稳定化未消除风控；不继续进入滑块验证，等待扫码登录稳定期结束后再恢复")

                            remaining = self._get_qr_login_grace_remaining_seconds()
                            self.last_token_refresh_status = "qr_login_grace_wait"
                            self.last_token_refresh_error_message = f"扫码登录后Token预检命中风控，已进入稳定期退避，剩余{remaining}秒"
                            return None

                        manual_refresh_state = self.get_manual_refresh_state(self.cookie_id)
                        is_manual_refresh_handoff = bool(
                            manual_refresh_state and manual_refresh_state.get('phase') == 'handoff_recovery'
                        )
                        if is_manual_refresh_handoff and not manual_refresh_browser_stabilization_used:
                            logger.warning(f"【{self.cookie_id}】手动刷新交接阶段首轮Token预检命中风控，先执行浏览器侧Cookie稳定化")
                            _host.log_captcha_event(
                                self.cookie_id,
                                "手动刷新交接阶段首轮Token预检命中风控，先执行浏览器侧稳定化",
                                None,
                                f"触发场景: Token刷新, ret={res_json.get('ret', [])}"
                            )
                            before_x5_snapshot = self._build_x5_cookie_snapshot(cookie_string=transient_recovery_cookies_str)
                            self._log_x5_cookie_snapshot("手动刷新交接稳定化前的x5票据", cookie_string=transient_recovery_cookies_str)
                            self.last_token_refresh_status = "manual_refresh_browser_stabilizing"
                            stabilization_success = await self._refresh_cookies_via_browser_page(
                                transient_recovery_cookies_str,
                                restart_on_success=False
                            )
                            if stabilization_success:
                                self._reload_latest_cookies_from_db("手动刷新交接阶段浏览器稳定化")
                                after_x5_snapshot = self._build_x5_cookie_snapshot()
                                self._log_x5_cookie_snapshot("手动刷新交接稳定化后的x5票据")
                                changed_x5_fields = [
                                    key for key in ('x5sec', 'x5secdata')
                                    if before_x5_snapshot.get(key, {}).get('hash') != after_x5_snapshot.get(key, {}).get('hash')
                                ]
                                if changed_x5_fields:
                                    logger.info(
                                        f"【{self.cookie_id}】手动刷新交接阶段浏览器稳定化已更新x5票据: {', '.join(changed_x5_fields)}"
                                    )
                                else:
                                    logger.info(f"【{self.cookie_id}】手动刷新交接阶段浏览器稳定化未观察到x5票据变化，继续重试Token预检")
                                return await self._refresh_token_impl(
                                    captcha_retry_count,
                                    post_slider_session_grace_used=post_slider_session_grace_used,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=True,
                                    post_slider_session_retry_count=post_slider_session_retry_count,
                                )
                            logger.warning(f"【{self.cookie_id}】手动刷新交接阶段浏览器稳定化失败，继续进入滑块验证")

                        if self.is_manual_refresh_active(self.cookie_id, allow_handoff_recovery=True):
                            logger.warning(f"【{self.cookie_id}】检测到手动刷新进行中，跳过自动滑块处理")
                            _host.log_captcha_event(
                                self.cookie_id,
                                "手动刷新进行中，跳过自动滑块处理",
                                None,
                                "触发场景: Token刷新"
                            )
                            self.last_token_refresh_status = "manual_refresh_active"
                            self._clear_pending_slider_success_notice("手动刷新进行中")
                            notification_sent = True
                            return None

                        logger.warning(f"【{self.cookie_id}】检测到需要滑块验证，开始处理...")

                        # 记录滑块验证检测到日志文件
                        verification_url = res_json.get('data', {}).get('url', 'Token刷新时检测')
                        _host.log_captcha_event(self.cookie_id, "检测到滑块验证", None, f"触发场景: Token刷新, URL: {verification_url}")
                        captcha_trigger_scene = 'token_refresh'
                        captcha_session_id = self._new_risk_session_id('slider')
                        captcha_event_meta = self._build_risk_event_meta(
                            trigger_scene=captcha_trigger_scene,
                            verification_url=verification_url,
                            extra={'cookie_id': self.cookie_id}
                        )

                        # 添加风控日志记录
                        log_id = None
                        try:
                            log_id = self._create_risk_log(
                                event_type='slider_captcha',
                                session_id=captcha_session_id,
                                trigger_scene=captcha_trigger_scene,
                                result_code='slider_captcha_detected',
                                event_description='检测到滑块验证（Token刷新）',
                                processing_status='processing',
                                event_meta=captcha_event_meta,
                            )
                            if log_id:
                                logger.info(f"【{self.cookie_id}】风控日志记录成功，ID: {log_id}")
                        except Exception as log_e:
                            logger.error(f"【{self.cookie_id}】记录风控日志失败: {log_e}")

                        try:
                            # 尝试通过滑块验证获取新的cookies
                            captcha_start_time = time.time()
                            new_cookies_str = await self._handle_captcha_verification(res_json)
                            captcha_duration = time.time() - captcha_start_time

                            if new_cookies_str:
                                logger.info(f"【{self.cookie_id}】滑块验证成功，准备重启实例...")

                                # 更新风控日志为成功状态
                                if 'log_id' in locals() and log_id:
                                    self._update_risk_log(
                                        log_id,
                                        session_id=captcha_session_id,
                                        trigger_scene=captcha_trigger_scene,
                                        result_code='slider_captcha_success',
                                        processing_result='滑块验证成功，已获取新Cookie',
                                        processing_status='success',
                                        duration_ms=max(0, int(captcha_duration * 1000)),
                                        event_meta=self._build_risk_event_meta(
                                            trigger_scene=captcha_trigger_scene,
                                            verification_url=verification_url,
                                            extra={
                                                'cookie_id': self.cookie_id,
                                                'cookie_length': len(new_cookies_str),
                                            },
                                        ),
                                    )

                                # 重启实例（cookies已在_handle_captcha_verification中更新到数据库）
                                # await self._restart_instance()

                                # 给浏览器回写票据与数据库落盘留一个稳定窗口，避免刚过块就立即重新命中Session过期
                                settle_delay = _host.random.uniform(*self.post_slider_token_retry_delay)
                                logger.info(
                                    f"【{self.cookie_id}】滑块成功后进入稳定窗口 {settle_delay:.2f}s，再重新尝试Token刷新"
                                )
                                await asyncio.sleep(settle_delay)
                                self._reload_latest_cookies_from_db("滑块成功后的稳定窗口")
                                _host.log_captcha_event(
                                    self.cookie_id,
                                    "滑块成功后重新进入Token刷新",
                                    None,
                                    f"类型: token_reentry_after_slider_success, captcha_retry_count={captcha_retry_count + 1}"
                                )

                                # 重新尝试刷新token（递归调用，但有深度限制）
                                return await self._refresh_token_impl(
                                    captcha_retry_count + 1,
                                    post_slider_session_grace_used=False,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=manual_refresh_browser_stabilization_used,
                                    post_slider_session_retry_count=0,
                                )
                            else:
                                logger.error(f"【{self.cookie_id}】滑块验证失败")
                                self.set_password_login_failure_backoff(self.cookie_id, 'slider_failed', 600)
                                self.last_token_refresh_error_message = "滑块验证失败，未获取到新Cookie"
                                logger.warning(f"【{self.cookie_id}】已进入滑块失败退避期: slider_failed, 600秒")

                                # 更新风控日志为失败状态
                                if 'log_id' in locals() and log_id:
                                    self._update_risk_log(
                                        log_id,
                                        session_id=captcha_session_id,
                                        trigger_scene=captcha_trigger_scene,
                                        result_code='slider_captcha_failed',
                                        processing_result='滑块验证失败，未获取到新Cookie',
                                        processing_status='failed',
                                        error_message='未获取到新Cookie',
                                        duration_ms=max(0, int(captcha_duration * 1000)),
                                        event_meta=self._build_risk_event_meta(
                                            trigger_scene=captcha_trigger_scene,
                                            verification_url=verification_url,
                                            extra={'cookie_id': self.cookie_id},
                                        ),
                                    )
                                
                                # 标记已处理，避免后续再发送通用失败通知
                                notification_sent = True
                        except Exception as captcha_e:
                            logger.error(f"【{self.cookie_id}】滑块验证处理异常: {self._safe_str(captcha_e)}")
                            self._clear_pending_slider_success_notice("滑块验证处理异常")
                            self.set_password_login_failure_backoff(self.cookie_id, 'slider_failed', 600)
                            self.last_token_refresh_error_message = self._safe_str(captcha_e)
                            logger.warning(f"【{self.cookie_id}】滑块验证异常后进入退避期: slider_failed, 600秒")

                            # 更新风控日志为异常状态
                            captcha_duration = time.time() - captcha_start_time if 'captcha_start_time' in locals() else 0
                            if 'log_id' in locals() and log_id:
                                self._update_risk_log(
                                    log_id,
                                    session_id=captcha_session_id,
                                    trigger_scene=captcha_trigger_scene,
                                    result_code='slider_captcha_exception',
                                    processing_result='滑块验证处理异常',
                                    processing_status='failed',
                                    error_message=str(captcha_e)[:200],
                                    duration_ms=max(0, int(captcha_duration * 1000)),
                                    event_meta=self._build_risk_event_meta(
                                        trigger_scene=captcha_trigger_scene,
                                        verification_url=verification_url,
                                        extra={'cookie_id': self.cookie_id},
                                    ),
                                )
                            
                            # 标记已处理，避免后续再发送通用失败通知
                            notification_sent = True

                    # 检查是否包含"令牌过期"或"Session过期"
                    if isinstance(res_json, dict):
                        res_json_str = json.dumps(res_json, ensure_ascii=False, separators=(',', ':'))
                        if '令牌过期' in res_json_str or 'Session过期' in res_json_str:
                            # 记录令牌/Session过期到风控日志
                            token_expired_log_id = None
                            token_expired_session_id = self._new_risk_session_id('token')
                            token_expired_started_at = time.time()
                            token_trigger_scene = 'token_refresh'
                            expire_type = '令牌过期' if '令牌过期' in res_json_str else 'Session过期'
                            try:
                                from db_manager import db_manager
                                stale_count = _db_package().mark_stale_risk_control_logs_failed(timeout_minutes=15, cookie_id=self.cookie_id)
                                if stale_count > 0:
                                    logger.warning(f"【{self.cookie_id}】检测到{stale_count}条超时processing风控日志，已自动标记failed")
                                token_expired_log_id = self._create_risk_log(
                                    event_type='token_expired',
                                    session_id=token_expired_session_id,
                                    trigger_scene=token_trigger_scene,
                                    result_code='token_expired_detected',
                                    event_description=f"检测到{expire_type}",
                                    processing_status='processing',
                                    event_meta=self._build_risk_event_meta(
                                        trigger_scene=token_trigger_scene,
                                        extra={'expire_type': expire_type, 'cookie_id': self.cookie_id},
                                    ),
                                )
                            except Exception as log_e:
                                logger.error(f"【{self.cookie_id}】记录风控日志失败: {log_e}")

                            # 调用统一的密码登录刷新方法
                            if self.is_manual_refresh_active(self.cookie_id, allow_handoff_recovery=True):
                                logger.warning(f"【{self.cookie_id}】检测到手动刷新进行中，跳过自动密码登录刷新")
                                if token_expired_log_id:
                                    self._update_risk_log(
                                        token_expired_log_id,
                                        session_id=token_expired_session_id,
                                        trigger_scene=token_trigger_scene,
                                        result_code='manual_refresh_active',
                                        processing_status='failed',
                                        error_message='检测到手动刷新进行中，自动刷新已跳过',
                                        duration_ms=max(0, int((time.time() - token_expired_started_at) * 1000)),
                                        event_meta=self._build_risk_event_meta(
                                            trigger_scene=token_trigger_scene,
                                            extra={'cookie_id': self.cookie_id, 'expire_type': expire_type},
                                        ),
                                    )
                                self.last_token_refresh_status = "manual_refresh_active"
                                self._clear_pending_slider_success_notice("手动刷新进行中")
                                notification_sent = True
                                return None

                            recent_slider_success = self._has_recent_slider_success()
                            max_post_slider_session_retries = max(
                                0,
                                int(_host.RISK_CONTROL.get('max_post_slider_session_retries', 1) or 1),
                            )

                            if recent_slider_success and not post_slider_session_grace_used:
                                grace_delay = _host.random.uniform(*self.post_slider_token_retry_delay)
                                logger.warning(
                                    f"【{self.cookie_id}】检测到最近 {self.slider_success_reentry_window}s 内刚通过滑块，"
                                    f"先等待 {grace_delay:.2f}s 并重载Cookie后再试一次Token刷新"
                                )
                                _host.log_captcha_event(
                                    self.cookie_id,
                                    "滑块成功后Session过期，优先重试Token刷新",
                                    None,
                                    f"类型: token_retry_after_recent_slider_success, expire_type={expire_type}"
                                )
                                await asyncio.sleep(grace_delay)
                                self._reload_latest_cookies_from_db("滑块成功后的Session过期缓冲")
                                return await self._refresh_token_impl(
                                    captcha_retry_count,
                                    post_slider_session_grace_used=True,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=manual_refresh_browser_stabilization_used,
                                    post_slider_session_retry_count=post_slider_session_retry_count,
                                )

                            if (
                                recent_slider_success and
                                not allow_password_login_recovery and
                                post_slider_session_retry_count < max_post_slider_session_retries
                            ):
                                settle_retry_attempt = post_slider_session_retry_count + 1
                                settle_delay = _host.random.uniform(*self.post_slider_token_retry_delay) + ((settle_retry_attempt - 1) * 1.2)
                                logger.warning(
                                    f"【{self.cookie_id}】预检模式下滑块成功后仍返回{expire_type}，"
                                    f"执行第{settle_retry_attempt}/{max_post_slider_session_retries}次稳定重试，"
                                    f"等待 {settle_delay:.2f}s 后再次尝试Token刷新"
                                )
                                _host.log_captcha_event(
                                    self.cookie_id,
                                    "滑块成功后Session仍未稳定，继续重试Token刷新",
                                    None,
                                    f"类型: token_settle_retry_after_slider, expire_type={expire_type}, "
                                    f"attempt={settle_retry_attempt}/{max_post_slider_session_retries}"
                                )
                                self.last_token_refresh_status = "post_slider_session_settling"
                                await asyncio.sleep(settle_delay)
                                self._reload_latest_cookies_from_db(
                                    f"滑块成功后的第{settle_retry_attempt}次Session稳定重试"
                                )
                                return await self._refresh_token_impl(
                                    captcha_retry_count,
                                    post_slider_session_grace_used=True,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=manual_refresh_browser_stabilization_used,
                                    post_slider_session_retry_count=settle_retry_attempt,
                                )

                            refresh_success = False
                            if allow_password_login_recovery:
                                refresh_success = await self._try_password_login_refresh(
                                    "令牌/Session过期",
                                    risk_session_id=token_expired_session_id,
                                    trigger_scene=token_trigger_scene,
                                    ignore_slider_failed_backoff=recent_slider_success,
                                )
                            else:
                                self.last_token_refresh_status = (
                                    "session_expired_after_slider"
                                    if recent_slider_success else
                                    "session_expired_preflight"
                                )
                                self.last_token_refresh_error_message = f"Token预检返回{expire_type}"
                                logger.warning(f"【{self.cookie_id}】当前为预检模式，跳过密码登录恢复，直接返回Token刷新失败")
                            
                            if token_expired_log_id:
                                self._update_risk_log(
                                    token_expired_log_id,
                                    session_id=token_expired_session_id,
                                    trigger_scene=token_trigger_scene,
                                    result_code='token_refresh_recovered' if refresh_success else 'token_refresh_recovery_failed',
                                    processing_status='success' if refresh_success else 'failed',
                                    processing_result='令牌/Session过期触发自动刷新成功，已进入重试流程' if refresh_success else None,
                                    error_message=None if refresh_success else '令牌/Session过期触发自动刷新失败',
                                    duration_ms=max(0, int((time.time() - token_expired_started_at) * 1000)),
                                    event_meta=self._build_risk_event_meta(
                                        trigger_scene=token_trigger_scene,
                                        extra={'cookie_id': self.cookie_id, 'expire_type': expire_type},
                                    ),
                                )
                            
                            if not refresh_success:
                                if allow_password_login_recovery and not self._is_account_pause_status(self.last_token_refresh_status):
                                    self.last_token_refresh_status = "token_expired_recovery_failed"
                                self._clear_pending_slider_success_notice("恢复流程失败")
                                # 标记已发送通知，避免重复通知
                                notification_sent = True
                                # 返回None，让调用者知道刷新失败
                                return None
                            else:
                                # 刷新成功后，重新尝试获取token
                                return await self._refresh_token_impl(
                                    captcha_retry_count,
                                    post_slider_session_grace_used=False,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=manual_refresh_browser_stabilization_used,
                                    post_slider_session_retry_count=0,
                                )
                                
                                # 刷新失败时继续执行原有的失败处理逻辑

                    if self.last_token_refresh_status in (None, "started"):
                        self.last_token_refresh_status = "token_refresh_failed"
                    self.last_token_refresh_error_message = json.dumps(res_json, ensure_ascii=False, separators=(',', ':'))
                    self._clear_pending_slider_success_notice("Token刷新最终失败")
                    logger.error(f"【{self.cookie_id}】Token刷新失败: {res_json}")

                    # 清空当前token，确保下次重试时重新获取
                    self.current_token = None

                    # 只有在没有发送过通知的情况下才发送Token刷新失败通知
                    # 并且WebSocket未连接时才发送（已连接说明只是暂时失败）
                    if not notification_sent:
                        # 检查WebSocket连接状态
                        is_ws_connected = (
                            self.connection_state == _host.ConnectionState.CONNECTED and 
                            self.ws and 
                            not self.ws.closed
                        )
                        
                        if is_ws_connected:
                            logger.info(f"【{self.cookie_id}】WebSocket连接正常，Token刷新失败可能是暂时的，跳过失败通知")
                        else:
                            logger.warning(f"【{self.cookie_id}】WebSocket未连接，发送Token刷新失败通知")
                            await self.send_token_refresh_notification(f"Token刷新失败: {res_json}", "token_refresh_failed")
                    else:
                        logger.info(f"【{self.cookie_id}】已发送滑块验证相关通知，跳过Token刷新失败通知")
                    return None

        except Exception as e:
            self.last_token_refresh_status = "token_refresh_exception"
            self.last_token_refresh_error_message = self._safe_str(e)
            self._clear_pending_slider_success_notice("Token刷新异常")
            logger.error(f"Token刷新异常: {self._safe_str(e)}")

            # 清空当前token，确保下次重试时重新获取
            self.current_token = None

            # 只有在没有发送过通知的情况下才发送Token刷新异常通知
            # 并且WebSocket未连接时才发送（已连接说明只是暂时失败）
            if not notification_sent:
                # 检查WebSocket连接状态
                is_ws_connected = (
                    self.connection_state == _host.ConnectionState.CONNECTED and 
                    self.ws and 
                    not self.ws.closed
                )
                
                if is_ws_connected:
                    logger.info(f"【{self.cookie_id}】WebSocket连接正常，Token刷新异常可能是暂时的，跳过失败通知")
                else:
                    logger.warning(f"【{self.cookie_id}】WebSocket未连接，发送Token刷新异常通知")
                    await self.send_token_refresh_notification(f"Token刷新异常: {str(e)}", "token_refresh_exception")
            else:
                logger.info(f"【{self.cookie_id}】已发送滑块验证相关通知，跳过Token刷新异常通知")
            return None
    def _is_normal_token_expiry(self, error_message: str) -> bool:
        """检查是否是正常的令牌过期或其他不需要通知的情况"""
        # 不需要发送通知的关键词
        no_notification_keywords = [
            # 正常的令牌过期
            'FAIL_SYS_TOKEN_EXOIRED::令牌过期',
            'FAIL_SYS_TOKEN_EXPIRED::令牌过期',
            'FAIL_SYS_TOKEN_EXOIRED',
            'FAIL_SYS_TOKEN_EXPIRED',
            '令牌过期',
            # Session过期（正常情况）
            'FAIL_SYS_SESSION_EXPIRED::Session过期',
            'FAIL_SYS_SESSION_EXPIRED',
            'Session过期',
            # Token定时刷新失败（会自动重试）
            'Token定时刷新失败，将自动重试',
            'Token定时刷新失败'
        ]

        # 检查错误消息是否包含不需要通知的关键词
        for keyword in no_notification_keywords:
            if keyword in error_message:
                return True

        return False
    def _is_token_related_error(self, error_message: str) -> bool:
        """检查是否是Token相关的错误，需要使用3小时冷却时间"""
        # Token相关错误的关键词
        token_error_keywords = [
            # Token刷新失败相关
            'Token刷新失败',
            'Token刷新异常',
            'token刷新失败',
            'token刷新异常',
            'TOKEN刷新失败',
            'TOKEN刷新异常',
            # 具体的Token错误信息
            'FAIL_SYS_USER_VALIDATE',
            'RGV587_ERROR',
            '哎哟喂,被挤爆啦',
            '请稍后重试',
            'punish?x5secdata',
            'captcha',
            # Token获取失败
            '无法获取有效token',
            '无法获取有效Token',
            'Token获取失败',
            'token获取失败',
            'TOKEN获取失败',
            # Token定时刷新失败
            'Token定时刷新失败',
            'token定时刷新失败',
            'TOKEN定时刷新失败',
            # 初始化Token失败
            '初始化时无法获取有效Token',
            '初始化时无法获取有效token',
            # 其他Token相关错误
            'accessToken',
            'access_token',
            '_m_h5_tk',
            'mtop.taobao.idlemessage.pc.login.token'
        ]

        # 检查错误消息是否包含Token相关的关键词
        error_message_lower = error_message.lower()
        for keyword in token_error_keywords:
            if keyword.lower() in error_message_lower:
                return True

        return False
    async def token_refresh_loop(self):
        """会话保活循环。轻量保活优先，重型恢复兜底。"""
        try:
            while True:
                try:
                    # 检查账号是否启用
                    _mgr = self._cookie_mgr
                    if _mgr and not _mgr.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止Token刷新循环")
                        break

                    current_time = time.time()
                    if self._is_account_pause_status(getattr(self, 'last_token_refresh_status', None)):
                        logger.warning(f"【{self.cookie_id}】账号处于人工验证/风控暂停状态，暂停会话保活循环")
                        await self._interruptible_sleep(300)
                        continue

                    if self._should_defer_auth_recovery_for_qr_grace(current_time):
                        await self._interruptible_sleep(max(60, self._get_qr_login_grace_remaining_seconds(current_time)))
                        continue

                    effective_keepalive_interval = self._get_effective_keepalive_interval()
                    if current_time - self.last_session_keepalive_time >= effective_keepalive_interval:
                        logger.info(f"【{self.cookie_id}】开始执行轻量会话保活...")
                        keepalive_ok = await self.keep_session_alive()
                        if keepalive_ok:
                            await self._interruptible_sleep(60)
                            continue

                        keepalive_status = getattr(self, 'last_session_keepalive_status', None)
                        if keepalive_status == "auth_failed":
                            logger.warning(f"【{self.cookie_id}】轻量保活鉴权失败，尝试执行重型Token恢复流程")
                            new_token = await self.refresh_token()
                            if new_token:
                                self.last_session_keepalive_time = time.time()
                                logger.info(f"【{self.cookie_id}】重型Token恢复成功，主动关闭旧WebSocket以使用新Token重连")
                                await self._force_websocket_reconnect("重型Token恢复成功，准备使用新Token重连")
                                break

                            last_refresh_status = getattr(self, 'last_token_refresh_status', None)
                            benign_refresh_statuses = ("skipped_cooldown", "restarted_after_cookie_refresh")
                            if last_refresh_status not in benign_refresh_statuses:
                                scheduled_error_message = self._build_scheduled_token_refresh_error_message(last_refresh_status)
                                await self.send_token_refresh_notification(
                                    scheduled_error_message,
                                    "token_scheduled_refresh_failed"
                                )
                            logger.warning(
                                f"【{self.cookie_id}】重型Token恢复失败(status={last_refresh_status})，"
                                f"{self._compute_token_retry_wait_seconds(current_time)} 秒后重试"
                            )
                            await self._interruptible_sleep(self._compute_token_retry_wait_seconds(current_time))
                        else:
                            logger.warning(
                                f"【{self.cookie_id}】轻量保活失败(status={keepalive_status})，"
                                f"{self.session_keepalive_retry_interval} 秒后重试"
                            )
                            await self._interruptible_sleep(self.session_keepalive_retry_interval)
                        continue
                    await self._interruptible_sleep(60)
                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】Token刷新循环收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"Token刷新循环出错: {self._safe_str(e)}")
                    # 出错后也等待1分钟再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(60)
                    except asyncio.CancelledError:
                        logger.info(f"【{self.cookie_id}】Token刷新循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】Token刷新循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】Token刷新循环已退出")
    def _get_mtop_token(self) -> str:
        token_value = _host.trans_cookies(self.cookies_str).get('_m_h5_tk', '')
        return token_value.split('_')[0] if token_value else ''