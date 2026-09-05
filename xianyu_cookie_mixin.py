"""Cookie 运行时状态/合并/刷新/验证（自 XianyuAutoAsync.py 拆出，P2-x 步骤④d）。

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


class CookieMixin:
    """Cookie 运行时状态/合并/刷新/验证方法簇。"""

    @staticmethod
    def _extract_cookie_value(cookie_info: Optional[Dict[str, Any]]) -> str:
        """兼容不同调用方返回字段名，提取cookie字符串"""
        if not cookie_info:
            return ''
        return (
            cookie_info.get('value')
            or cookie_info.get('cookies_str')
            or cookie_info.get('cookie_value')
            or ''
        )
    def _reload_latest_cookies_from_db(self, reason: str = "") -> bool:
        """从数据库重载当前账号最新 Cookie。"""
        try:
            from db_manager import db_manager

            account_info = _db_package().get_cookie_details(self.cookie_id)
            new_cookies_str = self._extract_cookie_value(account_info)
            if new_cookies_str and new_cookies_str != self.cookies_str:
                suffix = f" ({reason})" if reason else ""
                logger.info(f"【{self.cookie_id}】检测到数据库中的cookie已更新，重新加载cookie{suffix}")
                self._set_runtime_cookie_state(cookies_str=new_cookies_str, source=f"db_reload{suffix}")
                logger.warning(f"【{self.cookie_id}】Cookie已从数据库重新加载")
                return True
        except Exception as reload_e:
            logger.warning(f"【{self.cookie_id}】从数据库重新加载cookie失败，继续使用当前cookie: {self._safe_str(reload_e)}")
        return False
    def _serialize_cookies(self, cookies_dict: Optional[Dict[str, Any]] = None) -> str:
        cookies = cookies_dict or self.cookies
        return '; '.join([f"{k}={v}" for k, v in cookies.items() if k])
    def _sync_session_cookie_header(self):
        if self.session and not self.session.closed:
            self.session.headers['cookie'] = self.cookies_str
    def _set_runtime_cookie_state(
        self,
        cookies_str: Optional[str] = None,
        cookies_dict: Optional[Dict[str, Any]] = None,
        source: str = "runtime_update",
    ) -> bool:
        normalized_cookies = dict(cookies_dict or _host.trans_cookies(_host.cookies_str or ""))
        if not normalized_cookies:
            logger.warning(f"【{self.cookie_id}】忽略空Cookie更新: source={source}")
            return False

        previous_cookie_string = self.cookies_str
        previous_unb = self.cookies.get('unb') if isinstance(self.cookies, dict) else None

        self.cookies = normalized_cookies
        self.cookies_str = self._serialize_cookies(normalized_cookies)

        new_unb = self.cookies.get('unb')
        if new_unb and new_unb != previous_unb:
            logger.warning(f"【{self.cookie_id}】Cookie中的unb发生变化: {previous_unb} -> {new_unb} (source={source})")
            self.myid = new_unb
            self.device_id = _host.generate_device_id(self.myid)

        self._sync_session_cookie_header()
        return self.cookies_str != previous_cookie_string
    async def _persist_runtime_cookie_state(
        self,
        cookies_str: Optional[str] = None,
        cookies_dict: Optional[Dict[str, Any]] = None,
        source: str = "runtime_update",
    ) -> bool:
        changed = self._set_runtime_cookie_state(
            cookies_str=_host.cookies_str,
            cookies_dict=cookies_dict,
            source=source,
        )
        if changed:
            await self.update_config_cookies()
        return changed
    def _extract_set_cookie_updates(self, response_headers) -> Dict[str, str]:
        if not response_headers:
            return {}

        set_cookie_values = []
        try:
            if hasattr(response_headers, 'getall') and 'set-cookie' in response_headers:
                set_cookie_values = response_headers.getall('set-cookie', [])
            elif hasattr(response_headers, 'get_all'):
                set_cookie_values = response_headers.get_all('set-cookie', [])
            elif isinstance(response_headers, dict):
                raw_value = response_headers.get('set-cookie') or response_headers.get('Set-Cookie')
                if isinstance(raw_value, list):
                    set_cookie_values = raw_value
                elif raw_value:
                    set_cookie_values = [raw_value]
        except Exception:
            set_cookie_values = []

        updates = {}
        for cookie in set_cookie_values:
            if '=' not in cookie:
                continue
            name, value = cookie.split(';')[0].split('=', 1)
            updates[name.strip()] = value.strip()
        return updates
    async def _apply_response_cookie_updates(self, response_headers, source: str) -> bool:
        updates = self._extract_set_cookie_updates(response_headers)
        if not updates:
            return False

        merged_cookies = dict(self.cookies)
        merged_cookies.update(updates)
        changed = await self._persist_runtime_cookie_state(
            cookies_dict=merged_cookies,
            source=source,
        )
        if changed:
            logger.info(f"【{self.cookie_id}】已应用 {len(updates)} 个响应Cookie更新: source={source}")
        return changed
    def _build_cookie_string_with_updates(self, base_cookie_string: str = None, updated_cookies: Optional[Dict[str, Any]] = None) -> str:
        merged_cookies = _host.trans_cookies(base_cookie_string or self.cookies_str)
        for key, value in (updated_cookies or {}).items():
            if key:
                merged_cookies[str(key).strip()] = str(value)
        return self._serialize_cookies(merged_cookies)
    def _build_x5_cookie_snapshot(self, cookie_string: str = None, cookies_dict: dict = None) -> Dict[str, Dict[str, Any]]:
        source_dict = cookies_dict if cookies_dict is not None else _host.trans_cookies(cookie_string or self.cookies_str)
        snapshot = {}
        for key in ('x5sec', 'x5secdata'):
            value = source_dict.get(key)
            snapshot[key] = {
                'present': bool(value),
                'length': len(str(value)) if value else 0,
                'hash': _host.hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:12] if value else None,
            }
        return snapshot
    def _log_x5_cookie_snapshot(self, label: str, cookie_string: str = None, cookies_dict: dict = None):
        snapshot = self._build_x5_cookie_snapshot(cookie_string=cookie_string, cookies_dict=cookies_dict)
        parts = []
        for key, info in snapshot.items():
            if info.get('present'):
                parts.append(f"{key}=存在(len={info['length']}, sha={info['hash']})")
            else:
                parts.append(f"{key}=缺失")
        logger.info(f"【{self.cookie_id}】{label}: {', '.join(parts)}")
    @classmethod
    def protected_merge_cookie_dicts(cls, existing_cookies_dict, incoming_cookies_dict):
        """保护性合并 Cookie，避免不完整快照覆盖关键会话字段。"""
        existing = dict(existing_cookies_dict or {})
        incoming = dict(incoming_cookies_dict or {})
        existing_count = len(existing)
        incoming_count = len(incoming)
        existing_unb = str(existing.get('unb') or '').strip()
        incoming_unb = str(incoming.get('unb') or '').strip()
        account_switched = bool(existing_unb and incoming_unb and existing_unb != incoming_unb)

        if account_switched:
            merged = incoming.copy()
        else:
            merged = existing.copy()
            for key, value in incoming.items():
                merged[key] = value

        updated_fields = []
        changed_fields = []
        new_fields = []
        for key, value in incoming.items():
            old_value = existing.get(key)
            if old_value is None:
                updated_fields.append(f"{key}(新增)")
                new_fields.append(key)
            elif old_value != value:
                updated_fields.append(key)
                changed_fields.append(key)

        would_remove_fields = [key for key in existing.keys() if key not in incoming]
        if account_switched:
            removed_fields = list(would_remove_fields)
            preserved_fields = []
            preserved_protected_fields = []
        else:
            removed_fields = []
            preserved_fields = list(would_remove_fields)
            preserved_protected_fields = [
                key for key in would_remove_fields
                if key in _host.PROTECTED_SESSION_COOKIE_FIELDS and existing.get(key)
            ]

        missing_protected_fields = [
            key for key in _host.PROTECTED_SESSION_COOKIE_FIELDS
            if not merged.get(key)
        ]
        missing_required_fields = [
            key for key in _host.REQUIRED_SESSION_COOKIE_FIELDS
            if not merged.get(key)
        ]
        incoming_missing_protected_fields = [
            key for key in _host.PROTECTED_SESSION_COOKIE_FIELDS
            if not incoming.get(key)
        ]
        incoming_missing_required_fields = [
            key for key in _host.REQUIRED_SESSION_COOKIE_FIELDS
            if not incoming.get(key)
        ]

        return {
            'existing_cookies_dict': existing,
            'incoming_cookies_dict': incoming,
            'merged_cookies_dict': merged,
            'existing_count': existing_count,
            'incoming_count': incoming_count,
            'merged_count': len(merged),
            'updated_fields': updated_fields,
            'changed_fields': changed_fields,
            'new_fields': new_fields,
            'would_remove_fields': would_remove_fields,
            'removed_fields': removed_fields,
            'preserved_fields': preserved_fields,
            'preserved_protected_fields': preserved_protected_fields,
            'missing_protected_fields': missing_protected_fields,
            'missing_required_fields': missing_required_fields,
            'incoming_missing_protected_fields': incoming_missing_protected_fields,
            'incoming_missing_required_fields': incoming_missing_required_fields,
            'account_switched': account_switched,
        }
    def _merge_cookie_dicts(self, incoming_cookies_dict, existing_cookies_dict=None):
        """兼容旧调用，返回保护性合并结果。"""
        merge_result = self.protected_merge_cookie_dicts(
            existing_cookies_dict if existing_cookies_dict is not None else _host.trans_cookies(self.cookies_str),
            incoming_cookies_dict,
        )
        return (
            merge_result['existing_cookies_dict'],
            merge_result['merged_cookies_dict'],
            merge_result['updated_fields'],
            merge_result['changed_fields'],
            merge_result['new_fields'],
        )
    def _log_cookie_merge_summary(self, merged_cookies_dict, updated_fields, changed_fields, new_fields, context: str,
                                  preserved_fields=None, preserved_protected_fields=None,
                                  would_remove_fields=None, removed_fields=None,
                                  missing_protected_fields=None, missing_required_fields=None,
                                  incoming_missing_protected_fields=None, account_switched: bool = False):
        """打印 Cookie 合并结果，重点关注会话关键字段。"""
        context_prefix = f"{context}：" if context else ""
        logger.info(f"【{self.cookie_id}】{context_prefix}合并后cookies包含 {len(merged_cookies_dict)} 个字段")

        if updated_fields:
            logger.info(f"【{self.cookie_id}】{context_prefix}更新的cookie字段: {', '.join(updated_fields)}")
        else:
            logger.info(f"【{self.cookie_id}】{context_prefix}没有cookie字段需要更新")

        if account_switched:
            logger.warning(f"【{self.cookie_id}】{context_prefix}检测到unb变化，按账号切换处理，不保留旧账号Cookie字段")

        if preserved_protected_fields:
            logger.warning(
                f"【{self.cookie_id}】{context_prefix}保护性保留关键字段 ({len(preserved_protected_fields)}个): {', '.join(preserved_protected_fields)}"
            )
        if preserved_fields:
            logger.info(
                f"【{self.cookie_id}】{context_prefix}保留旧Cookie字段 ({len(preserved_fields)}个): {', '.join(preserved_fields)}"
            )
        if would_remove_fields:
            logger.info(
                f"【{self.cookie_id}】{context_prefix}浏览器快照未返回的旧字段 ({len(would_remove_fields)}个): {', '.join(would_remove_fields)}"
            )
        if removed_fields:
            logger.warning(
                f"【{self.cookie_id}】{context_prefix}实际移除旧字段 ({len(removed_fields)}个): {', '.join(removed_fields)}"
            )
        if incoming_missing_protected_fields:
            logger.warning(
                f"【{self.cookie_id}】{context_prefix}新快照缺失的关键字段 ({len(incoming_missing_protected_fields)}个): {', '.join(incoming_missing_protected_fields)}"
            )
        if missing_protected_fields:
            logger.warning(
                f"【{self.cookie_id}】{context_prefix}合并后仍缺失的受保护字段 ({len(missing_protected_fields)}个): {', '.join(missing_protected_fields)}"
            )
        if missing_required_fields:
            logger.error(
                f"【{self.cookie_id}】{context_prefix}合并后仍缺失的核心字段 ({len(missing_required_fields)}个): {', '.join(missing_required_fields)}"
            )

        important_keys = list(_host.PROTECTED_SESSION_COOKIE_FIELDS) + ['x5sec', 'x5secdata']
        logger.info(f"【{self.cookie_id}】{context_prefix}关键字段检查:")
        for key in important_keys:
            if key in merged_cookies_dict:
                val = merged_cookies_dict[key]
                marker = " [已变化]" if key in changed_fields else " [新增]" if key in new_fields else ""
                logger.info(f"【{self.cookie_id}】  ✅ {key}: {'存在' if val else '为空'} (长度: {len(str(val)) if val else 0}){marker}")
            else:
                logger.info(f"【{self.cookie_id}】  ❌ {key}: 缺失")
    async def _update_cookies_and_restart(self, new_cookies_str: str):
        """更新cookies并重启任务"""
        try:
            logger.info(f"【{self.cookie_id}】开始更新cookies并重启任务...")

            # 验证新cookies的有效性
            if not new_cookies_str or not new_cookies_str.strip():
                logger.error(f"【{self.cookie_id}】新cookies为空，无法更新")
                return False

            # 解析新cookies，确保格式正确
            try:
                new_cookies_dict = _host.trans_cookies(new_cookies_str)
                if not new_cookies_dict:
                    logger.error(f"【{self.cookie_id}】新cookies解析失败，无法更新")
                    return False
                logger.info(f"【{self.cookie_id}】新cookies解析成功，包含 {len(new_cookies_dict)} 个字段")
            except Exception as parse_e:
                logger.error(f"【{self.cookie_id}】新cookies解析异常: {self._safe_str(parse_e)}")
                return False

            # 合并cookies：保留原有cookies，只更新新获取到的字段
            try:
                merge_result = self.protected_merge_cookie_dicts(_host.trans_cookies(self.cookies_str), new_cookies_dict)
                merged_cookies_dict = merge_result['merged_cookies_dict']
                updated_fields = merge_result['updated_fields']
                changed_fields = merge_result['changed_fields']
                new_fields = merge_result['new_fields']
                self._log_protected_merge_event("password_refresh_protected_merge", merge_result)

                self._log_cookie_merge_summary(
                    merged_cookies_dict,
                    updated_fields,
                    changed_fields,
                    new_fields,
                    context="密码登录刷新Cookie",
                    preserved_fields=merge_result['preserved_fields'],
                    preserved_protected_fields=merge_result['preserved_protected_fields'],
                    would_remove_fields=merge_result['would_remove_fields'],
                    removed_fields=merge_result['removed_fields'],
                    missing_protected_fields=merge_result['missing_protected_fields'],
                    missing_required_fields=merge_result['missing_required_fields'],
                    incoming_missing_protected_fields=merge_result['incoming_missing_protected_fields'],
                    account_switched=merge_result['account_switched'],
                )

                if merge_result['missing_required_fields']:
                    logger.error(
                        f"【{self.cookie_id}】密码登录刷新后的Cookie仍缺失核心字段，放弃写回并重启: {', '.join(merge_result['missing_required_fields'])}"
                    )
                    return False

                # 使用合并后的cookies字符串
                new_cookies_str = '; '.join([f"{k}={v}" for k, v in merged_cookies_dict.items()])
                new_cookies_dict = merged_cookies_dict

            except Exception as merge_e:
                logger.error(f"【{self.cookie_id}】cookies合并异常: {self._safe_str(merge_e)}")
                logger.warning(f"【{self.cookie_id}】将使用原始新cookies（不合并）")
                # 如果合并失败，继续使用原始的new_cookies_str

            # 备份原有cookies，以防更新失败需要回滚
            old_cookies_str = self.cookies_str
            old_cookies_dict = self.cookies.copy()

            try:
                # 更新当前实例的cookies
                self._set_runtime_cookie_state(
                    cookies_str=new_cookies_str,
                    cookies_dict=new_cookies_dict,
                    source="password_login_refresh",
                )

                # 更新数据库中的cookies
                await self.update_config_cookies()
                logger.info(f"【{self.cookie_id}】数据库cookies更新成功")

                # ⚠️ 在重启前完成所有需要的操作（如发送通知）
                # 因为重启触发后2秒内任务会被取消，不能再执行任何async操作
                logger.info(f"【{self.cookie_id}】cookies更新成功，准备重启任务...")
                
                # 通过CookieManager重启任务
                logger.info(f"【{self.cookie_id}】通过CookieManager触发重启...")
                await self._restart_instance()
                
                # ⚠️ _restart_instance() 已触发重启，当前任务即将被取消
                # 立即返回，不执行任何后续代码（包括发送通知）
                logger.info(f"【{self.cookie_id}】重启请求已触发，等待任务被取消...")
                return True

            except Exception as update_e:
                logger.error(f"【{self.cookie_id}】更新cookies过程中出错，尝试回滚: {self._safe_str(update_e)}")

                # 回滚cookies
                try:
                    self._set_runtime_cookie_state(
                        cookies_str=old_cookies_str,
                        cookies_dict=old_cookies_dict,
                        source="password_login_refresh_rollback",
                    )
                    await self.update_config_cookies()
                    logger.info(f"【{self.cookie_id}】cookies已回滚到原始状态")
                except Exception as rollback_e:
                    logger.error(f"【{self.cookie_id}】cookies回滚失败: {self._safe_str(rollback_e)}")

                return False

        except Exception as e:
            logger.error(f"【{self.cookie_id}】更新cookies并重启任务时出错: {self._safe_str(e)}")
            return False
    async def update_config_cookies(self):
        """更新数据库中的cookies（不会覆盖账号密码等其他字段）"""
        try:
            from db_manager import db_manager

            # 更新数据库中的Cookie
            if hasattr(self, 'cookie_id') and self.cookie_id:
                try:
                    # 获取当前Cookie的用户ID，避免在刷新时改变所有者
                    current_user_id = None
                    if hasattr(self, 'user_id') and self.user_id:
                        current_user_id = self.user_id

                    # 使用 update_cookie_account_info 避免覆盖其他字段（如 username, password, pause_duration, remark 等）
                    # 这个方法会自动处理新账号和现有账号的情况，不会覆盖账号密码
                    success = _db_package().update_cookie_account_info(
                        self.cookie_id, 
                        cookie_value=self.cookies_str,
                        user_id=current_user_id  # 如果是新账号，需要提供user_id
                    )
                    if not success:
                        # 如果更新失败，记录错误但不使用 save_cookie（避免覆盖账号密码）
                        logger.warning(f"更新Cookie到数据库失败: {self.cookie_id}，但不使用save_cookie避免覆盖账号密码")
                    else:
                        logger.warning(f"已更新Cookie到数据库: {self.cookie_id}")
                except Exception as e:
                    logger.error(f"更新数据库Cookie失败: {self._safe_str(e)}")
                    # 发送数据库更新失败通知
                    await self.send_token_refresh_notification(f"数据库Cookie更新失败: {str(e)}", "db_update_failed")
            else:
                logger.warning("Cookie ID不存在，无法更新数据库")
                # 发送Cookie ID缺失通知
                await self.send_token_refresh_notification("Cookie ID不存在，无法更新数据库", "cookie_id_missing")

        except Exception as e:
            logger.error(f"更新Cookie失败: {self._safe_str(e)}")
            # 发送Cookie更新失败通知
            await self.send_token_refresh_notification(f"Cookie更新失败: {str(e)}", "cookie_update_failed")
    async def _verify_cookie_validity(self) -> dict:
        """验证Cookie的有效性，通过实际调用API测试
        
        Returns:
            dict: {
                'valid': bool,  # 总体是否有效
                'confirm_api': bool,  # 确认发货API是否有效
                'image_api': bool,  # 图片上传API是否有效
                'details': str  # 详细信息
            }
        """
        logger.info(f"【{self.cookie_id}】开始验证Cookie有效性（使用真实API调用）...")
        
        result = {
            'valid': True,
            'confirm_api': None,
            'web_session_api': None,
            'image_api': None,
            'details': [],
            'inconclusive': False,
            'relogin_recommended': True,
        }
        
        # 1. 测试确认发货API - 使用测试订单ID实际调用
        # try:
        #     logger.info(f"【{self.cookie_id}】测试确认发货API（使用测试数据实际调用）...")
            
        #     # 确保session存在
        #     if not self.session:
        #         import aiohttp
        #         connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
        #         timeout = aiohttp.ClientTimeout(total=30)
        #         self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            
        #     # 创建临时的确认发货实例
        #     from secure_confirm_decrypted import SecureConfirm
        #     confirm_tester = SecureConfirm(
        #         session=self.session,
        #         cookies_str=self.cookies_str,
        #         cookie_id=self.cookie_id,
        #         main_instance=self
        #     )
            
        #     # 使用一个测试订单ID（不存在的订单ID）
        #     # 如果Cookie有效，应该返回"订单不存在"类的错误
        #     # 如果Cookie无效，会返回"Session过期"错误
        #     test_order_id = "999999999999999999"  # 不存在的测试订单ID
            
        #     # 实际调用API (retry_count=3阻止重试，快速失败)
        #     response = await confirm_tester.auto_confirm(test_order_id, retry_count=3)
            
        #     # 分析响应
        #     if response and isinstance(response, dict):
        #         error_msg = str(response.get('error', ''))
        #         success = response.get('success', False)
                
        #         # 检查是否是Session过期错误
        #         if 'Session过期' in error_msg or 'SESSION_EXPIRED' in error_msg:
        #             logger.warning(f"【{self.cookie_id}】❌ 确认发货API验证失败: Session过期")
        #             result['confirm_api'] = False
        #             result['valid'] = False
        #             result['details'].append("确认发货API: Session过期")
        #         elif '令牌过期' in error_msg:
        #             logger.warning(f"【{self.cookie_id}】❌ 确认发货API验证失败: 令牌过期")
        #             result['confirm_api'] = False
        #             result['valid'] = False
        #             result['details'].append("确认发货API: 令牌过期")
        #         elif success:
        #             # 竟然成功了（不太可能，因为是测试订单ID）
        #             logger.info(f"【{self.cookie_id}】✅ 确认发货API验证通过: API调用成功")
        #             result['confirm_api'] = True
        #             result['details'].append("确认发货API: 通过验证")
        #         elif error_msg and len(error_msg) > 0:
        #             # 有其他错误信息（如订单不存在、重试次数过多等），说明Cookie是有效的
        #             logger.info(f"【{self.cookie_id}】✅ 确认发货API验证通过: Cookie有效（返回业务错误: {error_msg[:50]}）")
        #             result['confirm_api'] = True
        #             result['details'].append(f"确认发货API: 通过验证")
        #         else:
        #             # 没有明确信息，保守认为可能有问题
        #             logger.warning(f"【{self.cookie_id}】⚠️ 确认发货API验证警告: 响应不明确")
        #             result['confirm_api'] = False
        #             result['valid'] = False
        #             result['details'].append("确认发货API: 响应不明确")
        #     else:
        #         # 没有响应，可能有问题
        #         logger.warning(f"【{self.cookie_id}】⚠️ 确认发货API验证警告: 无响应")
        #         result['confirm_api'] = False
        #         result['valid'] = False
        #         result['details'].append("确认发货API: 无响应")
                    
        # except Exception as e:
        #     error_str = self._safe_str(e)
        #     # 检查异常信息中是否包含Session过期
        #     if 'Session过期' in error_str or 'SESSION_EXPIRED' in error_str:
        #         logger.warning(f"【{self.cookie_id}】❌ 确认发货API验证失败: Session过期")
        #         result['confirm_api'] = False
        #         result['valid'] = False
        #         result['details'].append("确认发货API: Session过期")
        #     else:
        #         logger.error(f"【{self.cookie_id}】确认发货API验证异常: {error_str}")
        #         # 网络异常等问题，不一定是Cookie问题，暂时标记为通过
        #         result['confirm_api'] = True
        #         result['details'].append(f"确认发货API: 调用异常(可能非Cookie问题)")
        
        # 2. 测试网页登录态 - 只读访问 IM 页面，检测是否被重定向到登录/验证页
        try:
            logger.info(f"【{self.cookie_id}】测试网页登录态（访问 IM 页面）...")

            if not self.session:
                connector = _host.aiohttp.TCPConnector(limit=100, limit_per_host=30)
                timeout = _host.aiohttp.ClientTimeout(total=30)
                self.session = _host.aiohttp.ClientSession(connector=connector, timeout=timeout)

            async with self.session.get(
                'https://www.goofish.com/im',
                headers={
                    'cookie': self.cookies_str,
                    'Referer': 'https://www.goofish.com/',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                },
                allow_redirects=True
            ) as response:
                final_url = str(response.url)
                page_text = await response.text()

                redirected_to_login = (
                    'passport.goofish.com' in final_url or
                    'mini_login' in final_url or
                    ('mini_login.htm' in page_text and 'alibaba-login-box' in page_text)
                )

                if redirected_to_login or response.status in (401, 403):
                    logger.warning(f"【{self.cookie_id}】❌ 网页登录态验证失败: 已进入登录/验证页 ({final_url})")
                    result['web_session_api'] = False
                    result['valid'] = False
                    result['details'].append("网页登录态: 已重定向到登录/验证页")
                elif response.status >= 500:
                    logger.warning(f"【{self.cookie_id}】⚠️ 网页登录态验证遇到服务端异常: HTTP {response.status}")
                    result['web_session_api'] = None
                    result['inconclusive'] = True
                    if result['valid']:
                        result['relogin_recommended'] = False
                    result['details'].append(f"网页登录态: 服务端异常，结果不确定 (HTTP {response.status})")
                elif response.status == 200:
                    logger.info(f"【{self.cookie_id}】✅ 网页登录态验证通过: {final_url}")
                    result['web_session_api'] = True
                    result['details'].append("网页登录态: 通过验证")
                else:
                    logger.warning(f"【{self.cookie_id}】⚠️ 网页登录态验证结果不明确: HTTP {response.status}, URL={final_url}")
                    result['web_session_api'] = None
                    result['inconclusive'] = True
                    if result['valid']:
                        result['relogin_recommended'] = False
                    result['details'].append(f"网页登录态: 结果不明确 (HTTP {response.status})")

        except (_host.aiohttp.ClientError, asyncio.TimeoutError) as e:
            error_str = self._safe_str(e)
            logger.warning(f"【{self.cookie_id}】⚠️ 网页登录态验证网络异常: {error_str}")
            result['web_session_api'] = None
            result['inconclusive'] = True
            if result['valid']:
                result['relogin_recommended'] = False
            result['details'].append(f"网页登录态: 网络异常，结果不确定 ({error_str[:50]})")
        except Exception as e:
            error_str = self._safe_str(e)
            logger.error(f"【{self.cookie_id}】网页登录态验证异常: {error_str}")
            result['web_session_api'] = None
            result['inconclusive'] = True
            if result['valid']:
                result['relogin_recommended'] = False
            result['details'].append(f"网页登录态: 验证异常，结果不确定 - {error_str[:50]}")

        # 3. 测试图片上传API - 创建测试图片并实际上传
        try:
            logger.info(f"【{self.cookie_id}】测试图片上传API（使用测试图片实际上传）...")
            
            # 创建一个最小的测试图片（1x1像素的PNG）
            import tempfile
            import os
            from PIL import Image
            
            # 创建临时目录
            temp_dir = tempfile.gettempdir()
            test_image_path = _host.os.path.join(temp_dir, f'cookie_test_{self.cookie_id}.png')
            
            try:
                # 创建1x1像素的白色图片
                img = Image.new('RGB', (1, 1), color='white')
                img.save(test_image_path, 'PNG')
                logger.info(f"【{self.cookie_id}】已创建测试图片: {test_image_path}")
                
                # 创建图片上传实例
                from utils.image_uploader import ImageUploader
                uploader = ImageUploader(cookies_str=self.cookies_str)
                
                # 创建session
                await uploader.create_session()
                
                try:
                    upload_result = None
                    error_type = None
                    error_message = None

                    for attempt in range(2):
                        upload_result = await uploader.upload_image(test_image_path)
                        if upload_result:
                            break

                        error_type = getattr(uploader, 'last_error_type', None)
                        error_message = getattr(uploader, 'last_error_message', None) or "未知原因"
                        is_retryable_auth = error_type == 'auth' and error_message == '返回登录页面' and result['web_session_api'] is not False
                        if attempt == 0 and is_retryable_auth:
                            logger.warning(
                                f"【{self.cookie_id}】图片上传校验首次返回登录页，但网页登录态仍可访问，1.5秒后重试一次"
                            )
                            await asyncio.sleep(1.5)
                            continue
                        break
                finally:
                    # 确保关闭session
                    await uploader.close_session()
                
                # 分析上传结果
                if upload_result:
                    # 上传成功，Cookie有效
                    logger.info(f"【{self.cookie_id}】✅ 图片上传API验证通过: 上传成功 ({upload_result[:50]}...)")
                    result['image_api'] = True
                    result['details'].append("图片上传API: 通过验证")
                else:
                    error_type = getattr(uploader, 'last_error_type', None)
                    error_message = getattr(uploader, 'last_error_message', None) or "未知原因"
                    if error_type == 'network':
                        logger.warning(f"【{self.cookie_id}】⚠️ 图片上传API验证遇到网络异常，不判定为Cookie失效: {error_message}")
                        result['image_api'] = None
                        result['inconclusive'] = True
                        if result['valid']:
                            result['relogin_recommended'] = False
                        result['details'].append(f"图片上传API: 网络异常，结果不确定 ({error_message[:50]})")
                    elif error_type == 'http' and getattr(uploader, 'last_http_status', None) and uploader.last_http_status >= 500:
                        logger.warning(f"【{self.cookie_id}】⚠️ 图片上传API返回服务端异常，不判定为Cookie失效: HTTP {uploader.last_http_status}")
                        result['image_api'] = None
                        result['inconclusive'] = True
                        if result['valid']:
                            result['relogin_recommended'] = False
                        result['details'].append(f"图片上传API: 服务端异常，结果不确定 (HTTP {uploader.last_http_status})")
                    elif error_type == 'auth' and error_message == '返回登录页面':
                        logger.warning(
                            f"【{self.cookie_id}】❌ 图片上传接口返回登录页，按旧版严格策略判定Cookie失效"
                        )
                        result['image_api'] = False
                        result['valid'] = False
                        result['details'].append("图片上传API: 返回登录页面")
                    else:
                        # 明确认证/会话异常才视为Cookie失效
                        logger.warning(f"【{self.cookie_id}】❌ 图片上传API验证失败: {error_message}")
                        result['image_api'] = False
                        result['valid'] = False
                        result['details'].append(f"图片上传API: {error_message[:50]}")
                
            finally:
                # 清理测试图片
                if _host.os.path.exists(test_image_path):
                    try:
                        _host.os.remove(test_image_path)
                        logger.debug(f"【{self.cookie_id}】已删除测试图片")
                    except Exception:
                        pass
                        
        except Exception as e:
            error_str = self._safe_str(e)
            logger.error(f"【{self.cookie_id}】图片上传API验证异常: {error_str}")
            error_lower = error_str.lower()
            auth_keywords = ['返回登录页面', 'session过期', '令牌过期', 'login', 'mini_login', 'passport.goofish.com']
            if any(keyword.lower() in error_lower for keyword in auth_keywords):
                result['image_api'] = False
                result['valid'] = False
                result['details'].append(f"图片上传API: 验证异常({error_str[:50]})")
            else:
                # 上传校验异常可能是网络或环境问题，不直接判定为Cookie失效
                result['image_api'] = None
                result['inconclusive'] = True
                if result['valid']:
                    result['relogin_recommended'] = False
                result['details'].append(f"图片上传API: 验证异常，结果不确定 - {error_str[:50]}")
        
        if result['image_api'] is False:
            result['valid'] = False
        elif result['web_session_api'] is False and result['image_api'] is not True:
            result['valid'] = False
        elif result['web_session_api'] is False and result['image_api'] is True:
            logger.warning(f"【{self.cookie_id}】❌ 网页登录态与图片上传校验结果不一致，按严格策略判定Cookie失效")
            result['valid'] = False
            result['details'].append("校验结果: 网页登录态与图片上传结果不一致")

        # 汇总结果
        if result['valid']:
            if result['inconclusive']:
                logger.warning(f"【{self.cookie_id}】⚠️ Cookie验证结果不确定: 未发现明确失效证据，但部分校验存在波动或结果矛盾")
            else:
                logger.info(f"【{self.cookie_id}】✅ Cookie验证通过: 所有关键API均可用")
        else:
            logger.warning(f"【{self.cookie_id}】❌ Cookie验证失败:")
            for detail in result['details']:
                logger.warning(f"【{self.cookie_id}】  - {detail}")
        
        result['details'] = '; '.join(result['details'])
        return result
    async def cookie_refresh_loop(self):
        """Cookie刷新定时任务 - 每小时执行一次"""
        try:
            while True:
                try:
                    # 检查账号是否启用
                    _mgr = self._cookie_mgr
                    if _mgr and not _mgr.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止Cookie刷新循环")
                        break

                    # 检查Cookie刷新功能是否启用
                    if not self.cookie_refresh_enabled:
                        logger.warning(f"【{self.cookie_id}】Cookie刷新功能已禁用，跳过执行")
                        await self._interruptible_sleep(300)  # 5分钟后再检查
                        continue

                    if self.is_manual_refresh_active(self.cookie_id):
                        logger.warning(f"【{self.cookie_id}】手动刷新进行中，跳过自动Cookie刷新")
                        await self._interruptible_sleep(60)
                        continue

                    current_time = time.time()
                    if self._is_account_pause_status(getattr(self, 'last_token_refresh_status', None)):
                        logger.warning(f"【{self.cookie_id}】账号处于人工验证/风控暂停状态，跳过自动Cookie刷新")
                        await self._interruptible_sleep(300)
                        continue

                    if self._should_defer_auth_recovery_for_qr_grace(current_time):
                        await self._interruptible_sleep(max(60, self._get_qr_login_grace_remaining_seconds(current_time)))
                        continue

                    if self._should_skip_token_refresh_for_login_backoff(current_time):
                        logger.info(f"【{self.cookie_id}】当前处于密码登录退避期，跳过自动Cookie刷新")
                        await self._interruptible_sleep(60)
                        continue

                    effective_cookie_refresh_interval = self._get_effective_cookie_refresh_interval()
                    if current_time - self.last_cookie_refresh_time >= effective_cookie_refresh_interval:
                        # 检查是否在消息接收后的冷却时间内
                        time_since_last_message = current_time - self.last_message_received_time
                        if time_since_last_message < self.message_cookie_refresh_cooldown:
                            remaining_time = self.message_cookie_refresh_cooldown - time_since_last_message
                            remaining_minutes = int(remaining_time // 60)
                            remaining_seconds = int(remaining_time % 60)
                            logger.warning(f"【{self.cookie_id}】收到消息后冷却中，还需等待 {remaining_minutes}分{remaining_seconds}秒 才能执行Cookie刷新")
                        # 检查是否已有Cookie刷新任务在执行
                        elif self.cookie_refresh_lock.locked():
                            logger.warning(f"【{self.cookie_id}】Cookie刷新任务已在执行中，跳过本次触发")
                        else:
                            logger.info(f"【{self.cookie_id}】开始执行Cookie刷新任务...")
                            # 在独立的任务中执行Cookie刷新，避免阻塞主循环
                            asyncio.create_task(self._execute_cookie_refresh(current_time))

                    # 每分钟检查一次是否需要执行
                    await self._interruptible_sleep(60)
                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】Cookie刷新循环收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】Cookie刷新循环失败: {self._safe_str(e)}")
                    # 出错后也等待1分钟再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(60)
                    except asyncio.CancelledError:
                        logger.info(f"【{self.cookie_id}】Cookie刷新循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】Cookie刷新循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】Cookie刷新循环已退出")
    async def _execute_cookie_refresh(self, current_time):
        """独立执行Cookie刷新任务，避免阻塞主循环"""

        # 使用Lock确保原子性，防止重复执行
        async with self.cookie_refresh_lock:
            try:
                clear_message_received_flag = False
                if self.is_manual_refresh_active(self.cookie_id):
                    logger.warning(f"【{self.cookie_id}】手动刷新进行中，取消当前自动Cookie刷新任务")
                    return

                logger.info(f"【{self.cookie_id}】开始Cookie刷新任务，暂时暂停心跳以避免连接冲突...")

                # 暂时暂停心跳任务，避免与浏览器操作冲突
                heartbeat_was_running = False
                if self.heartbeat_task and not self.heartbeat_task.done():
                    heartbeat_was_running = True
                    self.heartbeat_task.cancel()
                    logger.warning(f"【{self.cookie_id}】已暂停心跳任务")

                # 为整个Cookie刷新任务添加超时保护（3分钟，缩短时间减少影响）
                success = await asyncio.wait_for(
                    self._refresh_cookies_via_browser(),
                    timeout=180.0  # 3分钟超时，减少对WebSocket的影响
                )

                # 重新启动心跳任务
                if heartbeat_was_running and self.ws and not self.ws.closed:
                    logger.warning(f"【{self.cookie_id}】重新启动心跳任务")
                    self.heartbeat_task = asyncio.create_task(self.heartbeat_loop(self.ws))

                if success:
                    self.last_cookie_refresh_time = current_time
                    logger.info(f"【{self.cookie_id}】Cookie刷新任务完成，心跳已恢复")
                    
                    # 刷新成功后，验证Cookie有效性
                    logger.info(f"【{self.cookie_id}】开始验证刷新后的Cookie有效性...")
                    try:
                        validation_result = await self._verify_cookie_validity()
                        
                        if not validation_result['valid']:
                            logger.warning(f"【{self.cookie_id}】❌ Cookie验证失败: {validation_result['details']}")
                            if validation_result.get('relogin_recommended', True):
                                logger.warning(f"【{self.cookie_id}】检测到Cookie可能无法用于关键API，尝试通过密码登录重新获取...")
                                
                                # 触发密码登录刷新
                                password_refresh_success = await self._try_password_login_refresh("Cookie验证失败(关键API不可用)")
                                
                                if password_refresh_success:
                                    logger.info(f"【{self.cookie_id}】✅ 密码登录刷新成功，Cookie已更新")
                                    clear_message_received_flag = True
                                else:
                                    logger.warning(f"【{self.cookie_id}】⚠️ 密码登录刷新失败，Cookie可能仍然无效")
                                    # 发送通知
                                    await self.send_token_refresh_notification(
                                        f"Cookie验证失败且密码登录刷新也失败\n验证详情: {validation_result['details']}",
                                        "cookie_validation_failed"
                                    )
                            else:
                                logger.warning(f"【{self.cookie_id}】Cookie验证失败，但当前错误更像网络/环境问题，跳过密码登录刷新")
                        else:
                            if validation_result.get('inconclusive'):
                                logger.warning(f"【{self.cookie_id}】⚠️ Cookie验证结果不确定，保留当前消息冷却标志，等待后续保活再次确认: {validation_result['details']}")
                            else:
                                logger.info(f"【{self.cookie_id}】✅ Cookie验证通过: {validation_result['details']}")
                                clear_message_received_flag = True
                            
                    except Exception as verify_e:
                        logger.error(f"【{self.cookie_id}】Cookie验证过程异常: {self._safe_str(verify_e)}")
                        import traceback
                        logger.error(f"【{self.cookie_id}】详细堆栈:\n{traceback.format_exc()}")
                else:
                    logger.warning(f"【{self.cookie_id}】Cookie刷新任务失败")
                    # 即使失败也要更新时间，避免频繁重试
                    self.last_cookie_refresh_time = current_time

            except asyncio.TimeoutError:
                # 超时也要更新时间，避免频繁重试
                self.last_cookie_refresh_time = current_time
            except Exception as e:
                logger.error(f"【{self.cookie_id}】执行Cookie刷新任务异常: {self._safe_str(e)}")
                # 异常也要更新时间，避免频繁重试
                self.last_cookie_refresh_time = current_time
            finally:
                # 确保心跳任务恢复（如果WebSocket仍然连接）
                if (self.ws and not self.ws.closed and
                    (not self.heartbeat_task or self.heartbeat_task.done())):
                    logger.info(f"【{self.cookie_id}】Cookie刷新完成，心跳任务正常运行")
                    self.heartbeat_task = asyncio.create_task(self.heartbeat_loop(self.ws))

                if clear_message_received_flag:
                    # 仅在刷新链路确认恢复可用后，才清空消息接收标志。
                    self.last_message_received_time = 0
                    logger.warning(f"【{self.cookie_id}】Cookie刷新完成，已清空消息接收标志")
                else:
                    logger.warning(f"【{self.cookie_id}】Cookie刷新未确认恢复可用，保留消息接收标志")
    def enable_cookie_refresh(self, enabled: bool = True):
        """启用或禁用Cookie刷新功能"""
        self.cookie_refresh_enabled = enabled
        status = "启用" if enabled else "禁用"
        logger.info(f"【{self.cookie_id}】Cookie刷新功能已{status}")
    async def refresh_cookies_from_qr_login(self, qr_cookies_str: str, cookie_id: str = None, user_id: int = None):
        """使用扫码登录获取的cookie访问指定界面获取真实cookie并存入数据库

        Args:
            qr_cookies_str: 扫码登录获取的cookie字符串
            cookie_id: 可选的cookie ID，如果不提供则使用当前实例的cookie_id
            user_id: 可选的用户ID，如果不提供则使用当前实例的user_id

        Returns:
            bool: 成功返回True，失败返回False
        """
        playwright = None
        browser = None
        target_cookie_id = cookie_id or self.cookie_id
        target_user_id = user_id or self.user_id

        try:
            import asyncio
            from playwright.async_api import async_playwright
            from utils.xianyu_utils import trans_cookies

            logger.info(f"【{target_cookie_id}】开始使用扫码登录cookie获取真实cookie...")
            logger.info(f"【{target_cookie_id}】扫码cookie长度: {len(qr_cookies_str)}")

            # 解析扫码登录的cookie
            qr_cookies_dict = _host.trans_cookies(qr_cookies_str)
            logger.info(f"【{target_cookie_id}】扫码cookie字段数: {len(qr_cookies_dict)}")

            # 使用统一的Playwright启动方法
            playwright = await _host._start_playwright_safe(target_cookie_id)
            if not playwright:
                return False

            # 启动浏览器（参照商品搜索的配置）
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

            # 使用无头浏览器
            browser = await playwright.chromium.launch(
                headless=True,  # 改回无头模式
                args=browser_args
            )

            # 创建浏览器上下文
            context_options = {
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            }

            # 使用标准窗口大小
            context_options['viewport'] = {'width': 1920, 'height': 1080}

            context = await browser.new_context(**context_options)

            # 设置扫码登录获取的Cookie
            cookies = []
            for cookie_pair in qr_cookies_str.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })

            await context.add_cookies(cookies)
            logger.info(f"【{target_cookie_id}】已设置 {len(cookies)} 个扫码Cookie到浏览器")

            # 打印设置的扫码Cookie摘要（仅键名 + has_unb，禁止值）
            cookie_names = [c.get('name', '') for c in cookies]
            has_unb = any(str(n).lower() == 'unb' for n in cookie_names)
            logger.info(
                f"【{target_cookie_id}】=== 设置到浏览器的扫码Cookie摘要 === "
                f"count={len(cookies)} keys={cookie_names} has_unb={has_unb}"
            )

            # 创建页面
            page = await context.new_page()

            # 等待页面准备
            await asyncio.sleep(0.1)

            # 访问指定页面获取真实cookie
            target_url = "https://www.goofish.com/im"
            logger.info(f"【{target_cookie_id}】访问页面获取真实cookie: {target_url}")

            # 使用更灵活的页面访问策略
            try:
                # 首先尝试较短超时
                await page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
                logger.info(f"【{target_cookie_id}】页面访问成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{target_cookie_id}】页面访问超时，尝试降级策略...")
                    try:
                        # 降级策略：只等待基本加载
                        await page.goto(target_url, wait_until='load', timeout=20000)
                        logger.info(f"【{target_cookie_id}】页面访问成功（降级策略）")
                    except Exception as e2:
                        logger.warning(f"【{target_cookie_id}】降级策略也失败，尝试最基本访问...")
                        # 最后尝试：不等待任何加载完成
                        await page.goto(target_url, timeout=25000)
                        logger.info(f"【{target_cookie_id}】页面访问成功（最基本策略）")
                else:
                    raise e

            # 等待页面完全加载并获取真实cookie
            logger.info(f"【{target_cookie_id}】页面加载完成，等待获取真实cookie...")
            await asyncio.sleep(2)

            # 执行一次刷新以确保获取最新的cookie
            logger.info(f"【{target_cookie_id}】执行页面刷新获取最新cookie...")
            try:
                await page.reload(wait_until='domcontentloaded', timeout=12000)
                logger.info(f"【{target_cookie_id}】页面刷新成功")
            except Exception as e:
                error_text = str(e).lower()
                if 'net::err_aborted' in error_text or 'frame was detached' in error_text:
                    logger.warning(f"【{target_cookie_id}】页面刷新被中断，继续直接读取当前上下文Cookie: {self._safe_str(e)}")
                elif 'timeout' in error_text:
                    logger.warning(f"【{target_cookie_id}】页面刷新超时，使用降级策略...")
                    await page.reload(wait_until='load', timeout=15000)
                    logger.info(f"【{target_cookie_id}】页面刷新成功（降级策略）")
                else:
                    raise e
            await asyncio.sleep(1)

            # 获取更新后的真实Cookie
            logger.info(f"【{target_cookie_id}】获取真实Cookie...")
            updated_cookies = await context.cookies()

            # 构造新的Cookie字典
            real_cookies_dict = {}
            for cookie in updated_cookies:
                real_cookies_dict[cookie['name']] = cookie['value']

            # 现有账号不要直接整包覆盖旧Cookie，保留扫码前已经存在但本次页面未返回的字段
            from db_manager import db_manager
            existing_cookie = _db_package().get_cookie_details(target_cookie_id)
            existing_cookie_value = self._extract_cookie_value(existing_cookie)
            existing_cookies_dict = {}
            if existing_cookie_value:
                try:
                    existing_cookies_dict = _host.trans_cookies(existing_cookie_value) or {}
                except Exception as merge_e:
                    logger.warning(f"【{target_cookie_id}】解析现有账号Cookie失败，按空基线继续: {self._safe_str(merge_e)}")

            # 扫码登录代表一个新的可信登录会话。x5 系票据与具体风控挑战/API 强绑定，
            # 如果扫码后的浏览器快照没有返回新的 x5，继续沿用旧 x5sec/x5secdata 反而容易让
            # 首轮 Token 预检命中 FAIL_SYS_USER_VALIDATE / RGV587_ERROR。
            stale_x5_fields = []
            for x5_key in ('x5sec', 'x5secdata', 'x5sectag'):
                if x5_key in existing_cookies_dict and x5_key not in real_cookies_dict:
                    existing_cookies_dict.pop(x5_key, None)
                    stale_x5_fields.append(x5_key)
            if stale_x5_fields:
                logger.warning(
                    f"【{target_cookie_id}】扫码登录快照未返回新的x5票据，已丢弃旧会话x5字段: "
                    f"{', '.join(stale_x5_fields)}"
                )

            merge_result = self.protected_merge_cookie_dicts(existing_cookies_dict, real_cookies_dict)
            real_cookies_dict = merge_result['merged_cookies_dict']
            if target_cookie_id == self.cookie_id:
                self._log_protected_merge_event("qr_login_protected_merge", merge_result)
            else:
                logger.info(
                    f"【{target_cookie_id}】qr_login_protected_merge "
                    f"incoming_count={merge_result.get('incoming_count', 0)} "
                    f"existing_count={merge_result.get('existing_count', 0)} "
                    f"merged_count={merge_result.get('merged_count', 0)} "
                    f"protected_preserved_fields={merge_result.get('preserved_protected_fields') or []} "
                    f"would_remove_fields={merge_result.get('would_remove_fields') or []} "
                    f"account_switched={merge_result.get('account_switched', False)}"
                )
            if merge_result['updated_fields']:
                logger.info(f"【{target_cookie_id}】扫码登录合并更新Cookie字段: {', '.join(merge_result['updated_fields'])}")
            if merge_result['preserved_fields']:
                logger.info(f"【{target_cookie_id}】扫码登录保留现有Cookie字段 ({len(merge_result['preserved_fields'])}个): {', '.join(merge_result['preserved_fields'])}")
            if merge_result['preserved_protected_fields']:
                logger.warning(f"【{target_cookie_id}】扫码登录保护性保留关键字段: {', '.join(merge_result['preserved_protected_fields'])}")
            if merge_result['account_switched']:
                logger.warning(f"【{target_cookie_id}】扫码登录检测到unb变化，按账号切换处理，不保留旧账号Cookie字段")

            missing_required_fields = merge_result['missing_required_fields']
            if missing_required_fields:
                logger.error(f"【{target_cookie_id}】扫码登录真实Cookie仍缺失核心字段，放弃保存: {', '.join(missing_required_fields)}")
                return False

            # 生成真实cookie字符串
            real_cookies_str = '; '.join([f"{k}={v}" for k, v in real_cookies_dict.items()])

            logger.info(f"【{target_cookie_id}】真实Cookie已获取，包含 {len(real_cookies_dict)} 个字段")
            
            # 打印扫码登录真实Cookie摘要（仅键名/长度/has_unb）
            keys = list(real_cookies_dict.keys())
            has_unb = any(str(k).lower() == 'unb' for k in keys)
            logger.info(f"【{target_cookie_id}】========== 扫码登录真实Cookie摘要 ==========")
            logger.info(
                f"【{target_cookie_id}】count={len(keys)} keys={keys} has_unb={has_unb}"
            )
            for i, key in enumerate(keys, 1):
                logger.info(
                    f"【{target_cookie_id}】  {i:2d}. {key}: len={len(str(real_cookies_dict.get(key) or ''))}"
                )
            
            # 检查关键字段
            important_keys = ['unb', '_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 't', 'sgcookie', 'cna']
            logger.info(f"【{target_cookie_id}】关键字段检查:")
            for key in important_keys:
                if key in real_cookies_dict:
                    val = real_cookies_dict[key]
                    logger.info(f"【{target_cookie_id}】  ✅ {key}: {'存在' if val else '为空'} (长度: {len(str(val)) if val else 0})")
                else:
                    logger.info(f"【{target_cookie_id}】  ❌ {key}: 缺失")
            logger.info(f"【{target_cookie_id}】==========================================")

            logger.info(f"【{target_cookie_id}】=== 真实Cookie摘要 ===")
            logger.info(f"【{target_cookie_id}】Cookie字符串长度: {len(real_cookies_str)}")
            logger.info(f"【{target_cookie_id}】Cookie摘要: {self._summarize_cookie_string(real_cookies_str)}")

            # 打印原始扫码Cookie对比
            logger.info(f"【{target_cookie_id}】=== 扫码Cookie对比 ===")
            logger.info(f"【{target_cookie_id}】扫码Cookie长度: {len(qr_cookies_str)}")
            logger.info(f"【{target_cookie_id}】扫码Cookie字段数: {len(qr_cookies_dict)}")
            logger.info(f"【{target_cookie_id}】真实Cookie长度: {len(real_cookies_str)}")
            logger.info(f"【{target_cookie_id}】真实Cookie字段数: {len(real_cookies_dict)}")
            logger.info(f"【{target_cookie_id}】长度增加: {len(real_cookies_str) - len(qr_cookies_str)} 字符")
            logger.info(f"【{target_cookie_id}】字段增加: {len(real_cookies_dict) - len(qr_cookies_dict)} 个")

            # 检查Cookie变化
            changed_cookies = []
            new_cookies = []
            for name, new_value in real_cookies_dict.items():
                old_value = qr_cookies_dict.get(name)
                if old_value is None:
                    new_cookies.append(name)
                elif old_value != new_value:
                    changed_cookies.append(name)

            # 显示Cookie变化统计
            if changed_cookies:
                logger.info(f"【{target_cookie_id}】发生变化的Cookie字段 ({len(changed_cookies)}个): {', '.join(changed_cookies)}")
            if new_cookies:
                logger.info(f"【{target_cookie_id}】新增的Cookie字段 ({len(new_cookies)}个): {', '.join(new_cookies)}")
            if not changed_cookies and not new_cookies:
                logger.info(f"【{target_cookie_id}】Cookie无变化")

            # 打印重要Cookie字段的完整详情
            important_cookies = ['_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 't', 'sgcookie', 'unb', 'uc1', 'uc3', 'uc4']
            logger.info(f"【{target_cookie_id}】=== 重要Cookie字段完整详情 ===")
            for cookie_name in important_cookies:
                if cookie_name in real_cookies_dict:
                    cookie_value = real_cookies_dict[cookie_name]

                    # 标记是否发生了变化
                    change_mark = " [已变化]" if cookie_name in changed_cookies else " [新增]" if cookie_name in new_cookies else " [无变化]"

                    # 显示完整的cookie值
                    logger.info(f"【{target_cookie_id}】{cookie_name}{change_mark}:")
                    logger.info(f"【{target_cookie_id}】  值: {self._mask_secret_value(cookie_value, head=8, tail=6)}")
                    logger.info(f"【{target_cookie_id}】  长度: {len(cookie_value)}")

                    # 如果有对应的扫码cookie值，显示对比
                    if cookie_name in qr_cookies_dict:
                        old_value = qr_cookies_dict[cookie_name]
                        if old_value != cookie_value:
                            logger.info(f"【{target_cookie_id}】  原值: {self._mask_secret_value(old_value, head=8, tail=6)}")
                            logger.info(f"【{target_cookie_id}】  原长度: {len(old_value)}")
                    logger.info(f"【{target_cookie_id}】  ---")
                else:
                    logger.info(f"【{target_cookie_id}】{cookie_name}: [不存在]")

            # 保存真实Cookie到数据库
            # 检查是否为新账号
            existing_cookie = _db_package().get_cookie_details(target_cookie_id)
            if existing_cookie:
                # 现有账号，使用 update_cookie_account_info 避免覆盖其他字段（如 pause_duration, remark 等）
                success = _db_package().update_cookie_account_info(target_cookie_id, cookie_value=real_cookies_str)
            else:
                # 新账号，使用 save_cookie
                success = _db_package().save_cookie(target_cookie_id, real_cookies_str, target_user_id)

            if success:
                logger.info(f"【{target_cookie_id}】真实Cookie已成功保存到数据库")

                # 如果当前实例的cookie_id匹配，更新实例的cookie信息
                if target_cookie_id == self.cookie_id:
                    self._set_runtime_cookie_state(
                        cookies_str=real_cookies_str,
                        cookies_dict=real_cookies_dict,
                        source="qr_login_refresh",
                    )
                    logger.info(f"【{target_cookie_id}】已更新当前实例的Cookie信息")

                # 更新扫码登录Cookie刷新时间标志
                self.last_qr_cookie_refresh_time = time.time()
                logger.info(f"【{target_cookie_id}】已更新扫码登录Cookie刷新时间标志，_refresh_cookies_via_browser将等待{self.qr_cookie_refresh_cooldown//60}分钟后执行")

                return True
            else:
                logger.error(f"【{target_cookie_id}】保存真实Cookie到数据库失败")
                return False

        except Exception as e:
            logger.error(f"【{target_cookie_id}】使用扫码cookie获取真实cookie失败: {self._safe_str(e)}")
            return False
        finally:
            # 确保资源清理
            try:
                # 先关闭浏览器，再关闭Playwright（顺序很重要）
                if browser:
                    try:
                        await asyncio.wait_for(browser.close(), timeout=5.0)
                        logger.warning(f"【{target_cookie_id}】浏览器关闭完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{target_cookie_id}】浏览器关闭超时（5秒），资源可能未完全释放")
                        # 尝试取消浏览器相关的任务
                        try:
                            if hasattr(browser, '_connection'):
                                browser._connection = None
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"【{target_cookie_id}】关闭浏览器时出错: {self._safe_str(e)}")
                
                # Playwright关闭：使用更短的超时，超时后立即放弃
                if playwright:
                    try:
                        logger.warning(f"【{target_cookie_id}】正在关闭Playwright...")
                        await asyncio.wait_for(playwright.stop(), timeout=2.0)
                        logger.warning(f"【{target_cookie_id}】Playwright关闭完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{target_cookie_id}】Playwright关闭超时（2秒），进程可能仍在运行")
                        logger.warning(f"【{target_cookie_id}】提示：如果后续Playwright启动失败，可能需要手动清理残留进程")
                        # 尝试清理Playwright的内部状态
                        try:
                            # 取消可能正在运行的Playwright任务
                            if hasattr(playwright, '_transport'):
                                playwright._transport = None
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"【{target_cookie_id}】关闭Playwright时出错: {self._safe_str(e)}")
            except Exception as cleanup_e:
                logger.warning(f"【{target_cookie_id}】清理浏览器资源时出错: {self._safe_str(cleanup_e)}")
    async def _refresh_cookies_via_browser_page(self, current_cookies_str: str, restart_on_success: bool = True):
        """使用当前cookie访问指定页面获取真实cookie并更新
        
        这是令牌过期时的备用刷新方案，类似于refresh_cookies_from_qr_login，
        但使用当前的cookie而不是扫码登录的cookie。

        Args:
            current_cookies_str: 当前的cookie字符串
            restart_on_success: 成功后是否立即重启任务。扫码登录后的首轮缓冲只需要稳定 Cookie，不应直接重启。

        Returns:
            bool: 成功返回True，失败返回False
        """
        playwright = None
        browser = None

        try:
            import asyncio
            from playwright.async_api import async_playwright
            from utils.xianyu_utils import trans_cookies

            logger.info(f"【{self.cookie_id}】开始使用当前cookie访问指定页面获取真实cookie...")
            logger.info(f"【{self.cookie_id}】当前cookie长度: {len(current_cookies_str)}")

            # 解析当前的cookie
            current_cookies_dict = _host.trans_cookies(current_cookies_str)
            logger.info(f"【{self.cookie_id}】当前cookie字段数: {len(current_cookies_dict)}")

            # 使用统一的Playwright启动方法
            playwright = await _host._start_playwright_safe(self.cookie_id)
            if not playwright:
                return False

            # 启动浏览器（参照商品搜索的配置）
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

            # 读取账号配置以决定浏览器模式（默认无头）
            account_info = _db_host().get_cookie_details(self.cookie_id) or {}
            show_browser = bool(account_info.get('show_browser', False))
            browser = await playwright.chromium.launch(
                headless=not show_browser,
                args=browser_args
            )

            # 创建浏览器上下文
            context_options = {
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            }

            # 使用标准窗口大小
            context_options['viewport'] = {'width': 1920, 'height': 1080}

            context = await browser.new_context(**context_options)

            # 设置当前的Cookie
            cookies = []
            for cookie_pair in current_cookies_str.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })

            await context.add_cookies(cookies)
            logger.info(f"【{self.cookie_id}】已设置 {len(cookies)} 个当前Cookie到浏览器")

            # 创建页面
            page = await context.new_page()

            # 等待页面准备
            await asyncio.sleep(0.1)

            # 访问指定页面获取真实cookie
            target_url = "https://www.goofish.com/im"
            logger.info(f"【{self.cookie_id}】访问页面获取真实cookie: {target_url}")

            # 使用更灵活的页面访问策略
            try:
                # 首先尝试较短超时
                await page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
                logger.info(f"【{self.cookie_id}】页面访问成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】页面访问超时，尝试降级策略...")
                    try:
                        # 降级策略：只等待基本加载
                        await page.goto(target_url, wait_until='load', timeout=20000)
                        logger.info(f"【{self.cookie_id}】页面访问成功（降级策略）")
                    except Exception as e2:
                        logger.warning(f"【{self.cookie_id}】降级策略也失败，尝试最基本访问...")
                        # 最后尝试：不等待任何加载完成
                        await page.goto(target_url, timeout=25000)
                        logger.info(f"【{self.cookie_id}】页面访问成功（最基本策略）")
                else:
                    raise e

            # 等待页面完全加载并获取真实cookie
            logger.info(f"【{self.cookie_id}】页面加载完成，等待获取真实cookie...")
            await asyncio.sleep(2)

            # 执行一次刷新以确保获取最新的cookie
            logger.info(f"【{self.cookie_id}】执行页面刷新获取最新cookie...")
            try:
                await page.reload(wait_until='domcontentloaded', timeout=12000)
                logger.info(f"【{self.cookie_id}】页面刷新成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】页面刷新超时，使用降级策略...")
                    await page.reload(wait_until='load', timeout=15000)
                    logger.info(f"【{self.cookie_id}】页面刷新成功（降级策略）")
                else:
                    raise e
            await asyncio.sleep(1)

            # 获取更新后的真实Cookie
            logger.info(f"【{self.cookie_id}】获取真实Cookie...")
            updated_cookies = await context.cookies()

            # 构造新的Cookie字典
            real_cookies_dict = {}
            for cookie in updated_cookies:
                real_cookies_dict[cookie['name']] = cookie['value']

            merge_result = self.protected_merge_cookie_dicts(current_cookies_dict, real_cookies_dict)
            real_cookies_dict = merge_result['merged_cookies_dict']
            self._log_protected_merge_event("browser_stabilization_protected_merge", merge_result)

            # 生成真实cookie字符串
            real_cookies_str = '; '.join([f"{k}={v}" for k, v in real_cookies_dict.items()])

            logger.info(f"【{self.cookie_id}】真实Cookie已获取，包含 {len(real_cookies_dict)} 个字段")
            logger.info(f"【{self.cookie_id}】真实Cookie摘要: {self._summarize_cookie_string(real_cookies_str)}")

            self._log_cookie_merge_summary(
                real_cookies_dict,
                merge_result['updated_fields'],
                merge_result['changed_fields'],
                merge_result['new_fields'],
                context="浏览器稳定化Cookie",
                preserved_fields=merge_result['preserved_fields'],
                preserved_protected_fields=merge_result['preserved_protected_fields'],
                would_remove_fields=merge_result['would_remove_fields'],
                removed_fields=merge_result['removed_fields'],
                missing_protected_fields=merge_result['missing_protected_fields'],
                missing_required_fields=merge_result['missing_required_fields'],
                incoming_missing_protected_fields=merge_result['incoming_missing_protected_fields'],
                account_switched=merge_result['account_switched'],
            )

            if merge_result['missing_required_fields']:
                logger.error(f"【{self.cookie_id}】浏览器稳定化后的Cookie仍缺失核心字段，放弃写回数据库: {', '.join(merge_result['missing_required_fields'])}")
                return False

            # 检查Cookie是否有有效更新
            changed_cookies = []
            new_cookies = []
            for name, new_value in real_cookies_dict.items():
                old_value = current_cookies_dict.get(name)
                if old_value is None:
                    new_cookies.append(name)
                elif old_value != new_value:
                    changed_cookies.append(name)

            if not changed_cookies and not new_cookies:
                if restart_on_success:
                    logger.warning(f"【{self.cookie_id}】Cookie无变化，可能当前cookie已失效")
                    return False
                logger.info(f"【{self.cookie_id}】Cookie字段无变化，但浏览器稳定化访问已完成")

            logger.info(f"【{self.cookie_id}】发生变化的Cookie字段 ({len(changed_cookies)}个): {', '.join(changed_cookies[:10])}")
            if new_cookies:
                logger.info(f"【{self.cookie_id}】新增的Cookie字段 ({len(new_cookies)}个): {', '.join(new_cookies[:10])}")

            if restart_on_success:
                # 更新Cookie并重启任务
                logger.info(f"【{self.cookie_id}】开始更新Cookie并重启任务...")
                update_success = await self._update_cookies_and_restart(real_cookies_str)

                if update_success:
                    logger.info(f"【{self.cookie_id}】通过访问指定页面成功更新Cookie并重启任务")
                    return True
                else:
                    logger.error(f"【{self.cookie_id}】更新Cookie或重启任务失败")
                    return False

            old_cookies_str = self.cookies_str
            old_cookies_dict = self.cookies.copy()
            try:
                self._set_runtime_cookie_state(
                    cookies_str=real_cookies_str,
                    cookies_dict=real_cookies_dict,
                    source="stabilize_cookie_snapshot",
                )
                await self.update_config_cookies()
                logger.info(f"【{self.cookie_id}】通过访问指定页面成功稳定当前Cookie（不重启任务）")
                return True
            except Exception as update_e:
                self._set_runtime_cookie_state(
                    cookies_str=old_cookies_str,
                    cookies_dict=old_cookies_dict,
                    source="stabilize_cookie_snapshot_rollback",
                )
                logger.error(f"【{self.cookie_id}】稳定Cookie时更新数据库失败: {self._safe_str(update_e)}")
                return False

        except Exception as e:
            logger.error(f"【{self.cookie_id}】使用当前cookie访问指定页面获取真实cookie失败: {self._safe_str(e)}")
            return False
        finally:
            # 确保资源清理
            try:
                # 先关闭浏览器，再关闭Playwright（顺序很重要）
                if browser:
                    try:
                        await asyncio.wait_for(browser.close(), timeout=5.0)
                        logger.warning(f"【{self.cookie_id}】浏览器关闭完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{self.cookie_id}】浏览器关闭超时（5秒），资源可能未完全释放")
                    except Exception as e:
                        logger.warning(f"【{self.cookie_id}】关闭浏览器时出错: {self._safe_str(e)}")
                
                # Playwright关闭：使用更短的超时，超时后立即放弃
                if playwright:
                    try:
                        logger.warning(f"【{self.cookie_id}】正在关闭Playwright...")
                        await asyncio.wait_for(playwright.stop(), timeout=2.0)
                        logger.warning(f"【{self.cookie_id}】Playwright关闭完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{self.cookie_id}】Playwright关闭超时（2秒），进程可能仍在运行")
                    except Exception as e:
                        logger.warning(f"【{self.cookie_id}】关闭Playwright时出错: {self._safe_str(e)}")
            except Exception as cleanup_e:
                logger.warning(f"【{self.cookie_id}】清理浏览器资源时出错: {self._safe_str(cleanup_e)}")
    def reset_qr_cookie_refresh_flag(self):
        """重置扫码登录Cookie刷新标志，允许立即执行_refresh_cookies_via_browser"""
        self.last_qr_cookie_refresh_time = 0
        logger.info(f"【{self.cookie_id}】已重置扫码登录Cookie刷新标志")
    def get_qr_cookie_refresh_remaining_time(self) -> int:
        """获取扫码登录Cookie刷新剩余冷却时间（秒）"""
        current_time = time.time()
        time_since_qr_refresh = current_time - self.last_qr_cookie_refresh_time
        remaining_time = max(0, self.qr_cookie_refresh_cooldown - time_since_qr_refresh)
        return int(remaining_time)
    async def _refresh_cookies_via_browser(self, triggered_by_refresh_token: bool = False):
        """通过浏览器访问指定页面刷新Cookie

        Args:
            triggered_by_refresh_token: 是否由refresh_token方法触发，如果是True则设置browser_cookie_refreshed标志
        """


        playwright = None
        browser = None
        try:
            import asyncio
            from playwright.async_api import async_playwright

            # 检查是否需要等待扫码登录Cookie刷新的冷却时间
            current_time = time.time()
            time_since_qr_refresh = current_time - self.last_qr_cookie_refresh_time

            if time_since_qr_refresh < self.qr_cookie_refresh_cooldown:
                remaining_time = self.qr_cookie_refresh_cooldown - time_since_qr_refresh
                remaining_minutes = int(remaining_time // 60)
                remaining_seconds = int(remaining_time % 60)

                logger.info(f"【{self.cookie_id}】扫码登录Cookie刷新冷却中，还需等待 {remaining_minutes}分{remaining_seconds}秒")
                logger.info(f"【{self.cookie_id}】跳过本次浏览器Cookie刷新")
                return False

            logger.info(f"【{self.cookie_id}】开始通过浏览器刷新Cookie...")
            logger.info(f"【{self.cookie_id}】刷新前Cookie长度: {len(self.cookies_str)}")
            logger.info(f"【{self.cookie_id}】刷新前Cookie字段数: {len(self.cookies)}")

            # 使用统一的Playwright启动方法
            playwright = await _host._start_playwright_safe(self.cookie_id)
            if not playwright:
                return False

            # 启动浏览器（参照商品搜索的配置）
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

            # Cookie刷新模式：读取账号配置以决定浏览器模式（默认无头）
            account_info = _db_host().get_cookie_details(self.cookie_id) or {}
            show_browser = bool(account_info.get('show_browser', False))
            browser = await playwright.chromium.launch(
                headless=not show_browser,
                args=browser_args
            )

            # 创建浏览器上下文
            context_options = {
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            }

            # 使用标准窗口大小
            context_options['viewport'] = {'width': 1920, 'height': 1080}

            context = await browser.new_context(**context_options)

            # 设置当前Cookie
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
            logger.info(f"【{self.cookie_id}】已设置 {len(cookies)} 个Cookie到浏览器")

            # 创建页面
            page = await context.new_page()

            # 等待页面准备
            await asyncio.sleep(0.1)

            # 访问指定页面
            target_url = "https://www.goofish.com/im"
            logger.info(f"【{self.cookie_id}】访问页面: {target_url}")

            # 使用更灵活的页面访问策略
            try:
                # 首先尝试较短超时
                await page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
                logger.info(f"【{self.cookie_id}】页面访问成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】页面访问超时，尝试降级策略...")
                    try:
                        # 降级策略：只等待基本加载
                        await page.goto(target_url, wait_until='load', timeout=20000)
                        logger.info(f"【{self.cookie_id}】页面访问成功（降级策略）")
                    except Exception as e2:
                        logger.warning(f"【{self.cookie_id}】降级策略也失败，尝试最基本访问...")
                        # 最后尝试：不等待任何加载完成
                        await page.goto(target_url, timeout=25000)
                        logger.info(f"【{self.cookie_id}】页面访问成功（最基本策略）")
                else:
                    raise e

            # Cookie刷新模式：执行两次刷新
            logger.info(f"【{self.cookie_id}】页面加载完成，开始刷新...")
            await asyncio.sleep(1)

            # 第一次刷新 - 带重试机制
            logger.info(f"【{self.cookie_id}】执行第一次刷新...")
            try:
                await page.reload(wait_until='domcontentloaded', timeout=12000)
                logger.info(f"【{self.cookie_id}】第一次刷新成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】第一次刷新超时，使用降级策略...")
                    await page.reload(wait_until='load', timeout=15000)
                    logger.info(f"【{self.cookie_id}】第一次刷新成功（降级策略）")
                else:
                    raise e
            await asyncio.sleep(1)

            # 第二次刷新 - 带重试机制
            logger.info(f"【{self.cookie_id}】执行第二次刷新...")
            try:
                await page.reload(wait_until='domcontentloaded', timeout=12000)
                logger.info(f"【{self.cookie_id}】第二次刷新成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】第二次刷新超时，使用降级策略...")
                    await page.reload(wait_until='load', timeout=15000)
                    logger.info(f"【{self.cookie_id}】第二次刷新成功（降级策略）")
                else:
                    raise e
            await asyncio.sleep(1)

            # Cookie刷新模式：正常更新Cookie
            logger.info(f"【{self.cookie_id}】获取更新后的Cookie...")
            updated_cookies = await context.cookies()
            
            # 获取并打印当前页面标题
            page_title = await page.title()
            logger.info(f"【{self.cookie_id}】当前页面标题: {page_title}")

            # 构造新的Cookie字典
            new_cookies_dict = {}
            for cookie in updated_cookies:
                new_cookies_dict[cookie['name']] = cookie['value']

            # 检查Cookie变化
            changed_cookies = []
            new_cookies = []
            for name, new_value in new_cookies_dict.items():
                old_value = self.cookies.get(name)
                if old_value is None:
                    new_cookies.append(name)
                elif old_value != new_value:
                    changed_cookies.append(name)

            merge_result = self.protected_merge_cookie_dicts(self.cookies, new_cookies_dict)
            merged_cookies_dict = merge_result['merged_cookies_dict']
            self._log_protected_merge_event("browser_refresh_protected_merge", merge_result)

            self._log_cookie_merge_summary(
                merged_cookies_dict,
                merge_result['updated_fields'],
                merge_result['changed_fields'],
                merge_result['new_fields'],
                context="浏览器刷新Cookie",
                preserved_fields=merge_result['preserved_fields'],
                preserved_protected_fields=merge_result['preserved_protected_fields'],
                would_remove_fields=merge_result['would_remove_fields'],
                removed_fields=merge_result['removed_fields'],
                missing_protected_fields=merge_result['missing_protected_fields'],
                missing_required_fields=merge_result['missing_required_fields'],
                incoming_missing_protected_fields=merge_result['incoming_missing_protected_fields'],
                account_switched=merge_result['account_switched'],
            )

            if merge_result['missing_required_fields']:
                logger.error(
                    f"【{self.cookie_id}】浏览器刷新后的Cookie仍缺失核心字段，放弃覆盖当前Cookie: {', '.join(merge_result['missing_required_fields'])}"
                )
                return False

            # 更新self.cookies和cookies_str
            self._set_runtime_cookie_state(
                cookies_dict=merged_cookies_dict,
                source="browser_cookie_refresh",
            )

            logger.info(f"【{self.cookie_id}】Cookie已更新，包含 {len(new_cookies_dict)} 个字段")

            # 显示Cookie变化统计
            if changed_cookies:
                logger.info(f"【{self.cookie_id}】发生变化的Cookie字段 ({len(changed_cookies)}个): {', '.join(changed_cookies)}")
            if new_cookies:
                logger.info(f"【{self.cookie_id}】新增的Cookie字段 ({len(new_cookies)}个): {', '.join(new_cookies)}")
            if not changed_cookies and not new_cookies:
                logger.info(f"【{self.cookie_id}】Cookie无变化")

            # 打印完整的更新后Cookie（可选择性启用）
            logger.info(f"【{self.cookie_id}】更新后的Cookie摘要: {self._summarize_cookie_string(self.cookies_str)}")

            # 打印主要的Cookie字段摘要（键名 + 长度 + 变更标记，禁止值）
            important_cookies = ['_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 't', 'sgcookie', 'unb', 'uc1', 'uc3', 'uc4']
            logger.info(f"【{self.cookie_id}】重要Cookie字段摘要:")
            for cookie_name in important_cookies:
                if cookie_name in new_cookies_dict:
                    cookie_value = new_cookies_dict[cookie_name]
                    change_mark = " [已变化]" if cookie_name in changed_cookies else " [新增]" if cookie_name in new_cookies else ""
                    logger.info(
                        f"【{self.cookie_id}】  {cookie_name}: len={len(str(cookie_value or ''))}{change_mark}"
                    )

            # 更新数据库中的Cookie
            await self.update_config_cookies()

            # 只有当由refresh_token触发时才设置浏览器Cookie刷新成功标志
            if triggered_by_refresh_token:
                self.browser_cookie_refreshed = True
                logger.info(f"【{self.cookie_id}】由refresh_token触发，浏览器Cookie刷新成功标志已设置为True")

                # 兜底：直接在此处触发实例重启，避免外层协程在返回后被取消导致未重启
                try:
                    # 标记"刷新流程内已触发重启"，供外层去重
                    self.restarted_in_browser_refresh = True

                    logger.info(f"【{self.cookie_id}】Cookie刷新成功，准备重启实例...(via _refresh_cookies_via_browser)")
                    await self._restart_instance()
                    
                    # ⚠️ _restart_instance() 已触发重启，当前任务即将被取消
                    # 不要等待或执行耗时操作
                    logger.info(f"【{self.cookie_id}】重启请求已触发(via _refresh_cookies_via_browser)")
                    
                    # 标记重启标志（无需主动关闭WS，重启由管理器处理）
                    self.connection_restart_flag = True
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】兜底重启失败: {self._safe_str(e)}")
            else:
                logger.info(f"【{self.cookie_id}】由定时任务触发，不设置浏览器Cookie刷新成功标志")

            logger.info(f"【{self.cookie_id}】Cookie刷新完成")
            return True

        except Exception as e:
            logger.error(f"【{self.cookie_id}】通过浏览器刷新Cookie失败: {self._safe_str(e)}")
            return False
        finally:
            # 异步关闭浏览器：创建清理任务并等待完成，确保资源正确释放
            close_task = None
            try:
                if browser or playwright:
                    # 创建关闭任务
                    close_task = asyncio.create_task(
                        self._async_close_browser(browser, playwright)
                    )
                    logger.info(f"【{self.cookie_id}】浏览器异步关闭任务已启动")
                    
                    # 等待关闭任务完成，但设置超时避免阻塞太久
                    try:
                        await asyncio.wait_for(close_task, timeout=15.0)
                        logger.info(f"【{self.cookie_id}】浏览器关闭任务已完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{self.cookie_id}】浏览器关闭任务超时（15秒），强制继续")
                        # 取消任务，避免资源泄漏
                        if not close_task.done():
                            close_task.cancel()
                            try:
                                await close_task
                            except (asyncio.CancelledError, Exception):
                                pass
                    except Exception as wait_e:
                        logger.warning(f"【{self.cookie_id}】等待浏览器关闭任务时出错: {self._safe_str(wait_e)}")
                        # 确保任务被取消
                        if close_task and not close_task.done():
                            close_task.cancel()
                            try:
                                await close_task
                            except (asyncio.CancelledError, Exception):
                                pass
            except Exception as cleanup_e:
                logger.warning(f"【{self.cookie_id}】创建浏览器关闭任务时出错: {self._safe_str(cleanup_e)}")
                # 如果创建任务失败，尝试直接关闭
                if browser or playwright:
                    try:
                        await self._force_close_resources(browser, playwright)
                    except Exception:
                        pass
