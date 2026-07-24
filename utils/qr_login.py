#!/usr/bin/env python3
"""
闲鱼扫码登录工具
基于API接口实现二维码生成和Cookie获取（参照myfish-main项目）
"""

import asyncio
import time
import uuid
import json
import re
from random import random
from typing import Optional, Dict, Any
import httpx
import qrcode
import qrcode.constants
from loguru import logger
import hashlib
from urllib.parse import parse_qs, unquote, urlparse

from utils.image_utils import image_manager


def generate_headers():
    """生成请求头"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'Referer': 'https://passport.goofish.com/',
        'Origin': 'https://passport.goofish.com',
    }


class GetLoginParamsError(Exception):
    """获取登录参数错误"""


class GetLoginQRCodeError(Exception):
    """获取登录二维码失败"""


class NotLoginError(Exception):
    """未登录错误"""


class QRLoginSession:
    """二维码登录会话"""

    def __init__(self, session_id: str, user_id: Optional[int] = None):
        self.session_id = session_id
        self.user_id = user_id
        self.status = 'waiting'  # waiting, scanned, success, expired, cancelled, verification_required
        self.qr_code_url = None
        self.qr_content = None
        self.cookies = {}
        self.unb = None
        self.created_time = time.time()
        self.expire_time = 300  # 5分钟过期
        self.params = {}  # 存储登录参数
        self.verification_url = None  # 风控验证URL
        self.screenshot_path = None  # 风控验证截图
        self.verification_task = None  # 风控验证页面保持任务
        self.success_source = None  # 登录成功来源: api/browser/user
        # 服务端验证页已变成「流程结束」——通常表示用户在其它浏览器完成了人脸
        self.verification_ended_elsewhere = False
        self.user_hint = None

    def is_expired(self) -> bool:
        """检查是否过期"""
        return time.time() - self.created_time > self.expire_time

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'session_id': self.session_id,
            'status': self.status,
            'qr_code_url': self.qr_code_url,
            'created_time': self.created_time,
            'is_expired': self.is_expired()
        }


class QRLoginManager:
    """二维码登录管理器"""

    def __init__(self):
        self.sessions: Dict[str, QRLoginSession] = {}
        self.headers = generate_headers()
        self.host = "https://passport.goofish.com"
        self.api_mini_login = f"{self.host}/mini_login.htm"
        self.api_generate_qr = f"{self.host}/newlogin/qrcode/generate.do"
        self.api_scan_status = f"{self.host}/newlogin/qrcode/query.do"
        self.api_h5_tk = "https://h5api.m.goofish.com/h5/mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get/1.0/"
        
        # 配置代理（如果需要的话，取消注释并修改代理地址）
        # self.proxy = "http://127.0.0.1:7890"
        self.proxy = None
        
        # 配置超时时间
        self.timeout = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)

    def _cookie_marshal(self, cookies: dict) -> str:
        """将Cookie字典转换为字符串"""
        return "; ".join([f"{k}={v}" for k, v in cookies.items()])

    def _build_browser_cookies(self, target_url: str, cookies: Dict[str, str]) -> list[Dict[str, Any]]:
        """将API会话中的Cookie转换为Playwright可用格式。

        Playwright 要求 cookie 使用 url，或 domain+path 二选一。
        同时传 url 与 path 会直接报错：
        "Cookie should have either url or path"，导致风控验证页无法打开，
        人脸/扫码验证完成后也无法回写登录态。
        """
        browser_cookies = []
        parsed = urlparse(target_url or self.host)
        target_origin = f"{parsed.scheme or 'https'}://{parsed.netloc or 'passport.goofish.com'}"

        for name, value in (cookies or {}).items():
            if not name or value is None:
                continue
            browser_cookies.append({
                'name': str(name),
                'value': str(value),
                # 只用 url；path 由 Playwright 从 url 推导
                'url': target_origin,
            })

        return browser_cookies

    def _normalize_cookie_dict(self, cookies: Any) -> Dict[str, str]:
        """将不同形式的Cookie数据统一转换为字典"""
        if cookies is None:
            return {}

        if isinstance(cookies, str):
            text = cookies.replace("﻿", "").strip()
            if not text:
                return {}
            # 兼容 JSON 对象字符串：{"unb":"...","cookie2":"..."}
            if text[0] in "{[":
                try:
                    parsed = json.loads(text)
                    return self._normalize_cookie_dict(parsed)
                except Exception:
                    pass
            normalized = {}
            for item in text.split(";"):
                item = item.strip()
                if not item or "=" not in item:
                    continue
                name, value = item.split("=", 1)
                name = name.strip()
                value = value.strip()
                if name and value:
                    normalized[str(name)] = str(value)
            return normalized

        if isinstance(cookies, dict) or hasattr(cookies, 'items'):
            return {
                str(name): str(value)
                for name, value in cookies.items()
                if name and value is not None and str(value) != ""
            }

        normalized = {}
        for cookie in cookies or []:
            if not isinstance(cookie, dict):
                continue
            name = cookie.get('name')
            value = cookie.get('value')
            if name and value is not None and str(value) != "":
                normalized[str(name)] = str(value)
        return normalized

    def _merge_session_cookies(self, session: QRLoginSession, cookies: Any):
        """合并Cookie到会话中"""
        cookie_dict = self._normalize_cookie_dict(cookies)
        if not cookie_dict:
            return

        session.cookies.update(cookie_dict)
        if cookie_dict.get('unb'):
            session.unb = cookie_dict['unb']

    def _has_completed_login_cookies(self, cookie_dict: Dict[str, str]) -> bool:
        """基于关键Cookie判断是否已经完成登录"""
        if not cookie_dict.get('unb'):
            return False

        companion_keys = ('cookie2', 'havana_lgc2_77', '_tb_token_', 'sgcookie')
        return any(cookie_dict.get(key) for key in companion_keys)

    def _is_logged_in_url(self, url: str) -> bool:
        """判断URL是否已经跳转到登录后的页面"""
        current_url = str(url or '')
        if not current_url:
            return False

        if 'www.goofish.com/im' in current_url:
            return True

        return (
            'goofish.com' in current_url and
            'passport.goofish.com' not in current_url and
            'mini_login' not in current_url and
            '/iv/' not in current_url
        )

    def _extract_first_url(self, text: str) -> Optional[str]:
        """从用户粘贴内容中提取第一个 http(s) URL。"""
        raw = str(text or '').replace('﻿', '').strip()
        if not raw:
            return None
        # 整段就是 URL
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw.split()[0].strip('\'"<>')
        match = re.search(r'https?://[^\s\'"<>]+', raw)
        if not match:
            return None
        return match.group(0).rstrip('.,;)]}')

    def _is_allowed_callback_url(self, url: str) -> bool:
        """只允许闲鱼/淘宝登录相关域名，避免开放代理。"""
        try:
            parsed = urlparse(str(url or '').strip())
        except Exception:
            return False
        if parsed.scheme not in ('http', 'https'):
            return False
        host = (parsed.hostname or '').lower()
        if not host:
            return False
        allowed_suffixes = (
            'goofish.com',
            'taobao.com',
            'tmall.com',
            'alipay.com',
            'alibaba.com',
            'alicdn.com',
            'mmstat.com',
        )
        return any(host == suffix or host.endswith('.' + suffix) for suffix in allowed_suffixes)

    def _extract_login_tokens_from_url(self, url: str) -> Dict[str, str]:
        """从回调/跳转 URL 中提取可用于换登录态的 token 参数。"""
        tokens: Dict[str, str] = {}
        raw = str(url or '').strip()
        if not raw:
            return tokens
        try:
            parsed = urlparse(raw)
        except Exception:
            return tokens

        query = parse_qs(parsed.query, keep_blank_values=False)
        # 部分回调把参数塞在 fragment
        fragment_query = parse_qs(parsed.fragment, keep_blank_values=False) if parsed.fragment else {}

        def _pick(mapping: Dict[str, list], *names: str) -> Optional[str]:
            for name in names:
                values = mapping.get(name) or mapping.get(name.lower()) or mapping.get(name.upper())
                if values and str(values[0]).strip():
                    return unquote(str(values[0]).strip())
            return None

        login_token = _pick(query, 'token', 'lgToken', 'login_token', 'loginToken') or _pick(
            fragment_query, 'token', 'lgToken', 'login_token', 'loginToken'
        )
        if login_token:
            tokens['login_token'] = login_token

        havana_iv = _pick(query, 'havana_iv_token', 'havanaIvToken') or _pick(
            fragment_query, 'havana_iv_token', 'havanaIvToken'
        )
        if havana_iv:
            tokens['havana_iv_token'] = havana_iv

        stoken = _pick(query, 'stoken', 's_token', 'ssoToken') or _pick(
            fragment_query, 'stoken', 's_token', 'ssoToken'
        )
        if stoken:
            tokens['stoken'] = stoken

        return tokens

    async def _exchange_login_token(
        self,
        session: QRLoginSession,
        login_token: str,
    ) -> Dict[str, str]:
        """用 login_token 换取登录 Cookie（与 qr_login_lite 同路径）。"""
        if not login_token:
            return {}
        params = {
            'token': login_token,
            'subFlow': 'DIALOG_CHECK_LOGIN_RPC',
            'nextCode': '0018',
            'bizScene': 'qrcode',
            'confirm': 'true',
        }
        data = {}
        device_id = session.cookies.get('cna') or session.params.get('deviceId') or ''
        if device_id:
            data['deviceId'] = device_id

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.timeout,
            proxy=self.proxy,
        ) as client:
            resp = await client.post(
                f'{self.host}/login_token/login.do',
                params=params,
                data=data or None,
                cookies=session.cookies,
                headers=self.headers,
            )
            cookie_dict = {k: v for k, v in resp.cookies.items()}
            # 再访问主站一次，尽量把 goofish 域 Cookie 拉全
            try:
                im_resp = await client.get(
                    'https://www.goofish.com/im',
                    cookies={**session.cookies, **cookie_dict},
                    headers={
                        **self.headers,
                        'Referer': 'https://www.goofish.com/',
                        'Origin': 'https://www.goofish.com',
                    },
                )
                cookie_dict.update({k: v for k, v in im_resp.cookies.items()})
            except Exception as e:
                logger.debug(f"login_token 换取后访问 /im 失败: {session.session_id}, {e}")
            logger.info(
                f"login_token 换取完成: {session.session_id}, "
                f"status={resp.status_code}, cookie_keys={list(cookie_dict.keys())}"
            )
            return cookie_dict

    async def apply_external_callback_url(
        self,
        session_id: str,
        callback_url: str,
        source: str = 'user_url',
    ) -> Dict[str, Any]:
        """用户侧验证完成后，用回调/跳转 URL 在服务端会话里换 Cookie。

        产品目标：用户只需粘贴成功后的网址，不必再手抠 Cookie。
        实现：允许域名校验 → 解析 token → login_token 换 Cookie →
        Playwright 打开 URL（带当前会话 Cookie）→ 探测完整登录态。
        """
        session = self.sessions.get(session_id)
        if not session:
            return {'success': False, 'status': 'not_found', 'message': '会话不存在或已过期'}

        if session.is_expired() and session.status not in {'success'}:
            session.status = 'expired'
            return {
                'success': False,
                'status': 'expired',
                'message': '会话已过期，请重新发起扫码登录后再提交回调URL',
            }

        if session.status == 'success' and session.unb and self._has_completed_login_cookies(session.cookies):
            return {
                'success': True,
                'status': 'success',
                'message': '会话已是登录成功状态',
                'already_success': True,
                'unb': session.unb,
            }

        if session.status not in {
            'verification_required', 'scanned', 'waiting', 'processing', 'success'
        }:
            return {
                'success': False,
                'status': session.status,
                'message': f'当前会话状态不允许提交回调URL: {session.status}',
            }

        url = self._extract_first_url(callback_url) or str(callback_url or '').strip()
        if not url:
            return {'success': False, 'status': session.status, 'message': '回调URL为空'}
        if not self._is_allowed_callback_url(url):
            return {
                'success': False,
                'status': session.status,
                'message': 'URL域名不允许。请粘贴 goofish/淘宝登录相关跳转链接',
            }
        if len(url) > 8000:
            return {'success': False, 'status': session.status, 'message': 'URL过长'}

        session.user_hint = '正在用你提供的回调URL换取登录态...'
        tokens = self._extract_login_tokens_from_url(url)
        merged: Dict[str, str] = {}

        # 1) 若 URL 带 login_token / lgToken，优先 API 换 Cookie（轻量）
        login_token = tokens.get('login_token')
        if login_token:
            try:
                exchanged = await self._exchange_login_token(session, login_token)
                merged.update(exchanged)
                self._merge_session_cookies(session, exchanged)
            except Exception as e:
                logger.warning(f"login_token 换取失败: {session_id}, {e}")

        if self._has_completed_login_cookies({**session.cookies, **merged}):
            if self._mark_session_success(
                session, {**session.cookies, **merged}, source, require_complete_cookies=True
            ):
                session.user_hint = None
                session.verification_ended_elsewhere = True
                logger.info(
                    f"扫码登录已按回调URL(token换取)成功收口: {session_id}, "
                    f"source={source}, UNB={session.unb}"
                )
                return {
                    'success': True,
                    'status': 'success',
                    'message': '已使用回调URL中的token完成登录',
                    'unb': session.unb,
                    'via': 'login_token',
                }

        # 2) Playwright 打开回调 URL，在同一会话 Cookie 上下文中收口
        playwright = None
        browser = None
        context = None
        page = None
        try:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--lang=zh-CN',
                ],
            )
            context = await browser.new_context(
                viewport={'width': 540, 'height': 960},
                locale='zh-CN',
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                ),
                ignore_https_errors=True,
                extra_http_headers={
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
                },
            )
            browser_cookies = self._build_browser_cookies(url, session.cookies)
            if browser_cookies:
                await context.add_cookies(browser_cookies)

            page = await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(2000)

            # 页面若再次给出 token 链接，尝试提取
            try:
                current_url = page.url
                page_tokens = self._extract_login_tokens_from_url(current_url)
                page_login_token = page_tokens.get('login_token')
                if page_login_token and page_login_token != login_token:
                    exchanged = await self._exchange_login_token(session, page_login_token)
                    merged.update(exchanged)
                    self._merge_session_cookies(session, exchanged)
            except Exception as e:
                logger.debug(f"从当前页URL提取token失败: {session_id}, {e}")

            cookie_dict = await self._context_cookie_dict(context)
            merged.update(cookie_dict)
            self._merge_session_cookies(session, cookie_dict)

            # 完整 Cookie 或登录后 URL → 成功；否则再探 /im
            if await self._probe_browser_login_success(session, page, context):
                session.user_hint = None
                session.verification_ended_elsewhere = True
                return {
                    'success': True,
                    'status': 'success',
                    'message': '已使用回调URL在服务端会话中完成登录',
                    'unb': session.unb,
                    'via': 'browser_url',
                }

            # 再显式判断一次合并后的 Cookie
            final_cookies = {**session.cookies, **merged}
            if self._mark_session_success(
                session, final_cookies, source, require_complete_cookies=True
            ):
                session.user_hint = None
                session.verification_ended_elsewhere = True
                return {
                    'success': True,
                    'status': 'success',
                    'message': '已使用回调URL完成登录',
                    'unb': session.unb,
                    'via': 'browser_cookies',
                }

            await self._detect_verification_ended_elsewhere(session, page)
            session.user_hint = (
                '已打开回调URL，但服务端仍未拿到完整登录Cookie。'
                '请确认该链接来自「已验证成功」的页面；'
                '若仍失败，可改贴成功侧完整Cookie（含 unb）。'
            )
            logger.warning(
                f"回调URL未能换取完整Cookie: {session_id}, url_host={urlparse(url).hostname}, "
                f"cookie_keys={list(final_cookies.keys())}"
            )
            return {
                'success': False,
                'status': session.status,
                'message': session.user_hint,
                'cookie_keys': sorted(final_cookies.keys()),
            }
        except Exception as e:
            logger.error(f"回调URL换取Cookie失败: {session_id}, 错误: {e}")
            return {
                'success': False,
                'status': session.status,
                'message': f'打开回调URL失败: {e}',
            }
        finally:
            for closer in (
                (page, 'close'),
                (context, 'close'),
                (browser, 'close'),
                (playwright, 'stop'),
            ):
                obj, method = closer
                if not obj:
                    continue
                try:
                    await getattr(obj, method)()
                except Exception:
                    pass

    def _mark_session_success(
        self,
        session: QRLoginSession,
        cookies: Any,
        source: str,
        require_complete_cookies: bool = False
    ) -> bool:
        """统一的会话成功收口，避免多条链路重复覆盖状态"""
        if not session:
            return False

        self._merge_session_cookies(session, cookies)

        has_success_cookie = bool(session.cookies.get('unb'))
        has_complete_cookies = self._has_completed_login_cookies(session.cookies)
        if not has_success_cookie:
            return False
        if require_complete_cookies and not has_complete_cookies:
            return False

        was_success = session.status == 'success'
        session.status = 'success'
        session.success_source = session.success_source or source

        if not was_success:
            logger.info(
                f"扫码登录成功（来源: {source}）: {session.session_id}, "
                f"UNB: {session.unb}"
            )

        return True

    async def _context_cookie_dict(self, context) -> Dict[str, str]:
        """提取浏览器上下文中的Cookie字典"""
        cookies = await context.cookies()
        return self._normalize_cookie_dict(cookies)

    async def _detect_verification_ended_elsewhere(self, session: QRLoginSession, page) -> bool:
        """检测服务端验证页是否已变成「流程结束」（常见于用户在其它浏览器完成人脸）。"""
        try:
            text = await page.evaluate("() => (document.body && document.body.innerText) || ''")
        except Exception:
            text = ""
        text = str(text or "")
        # 避免单独匹配「请关闭页面」等泛化文案，降低误报
        ended_markers = (
            "身份校验流程已经结束",
            "校验流程已经结束",
            "验证已完成，请关闭",
            "验证完成，请关闭",
        )
        if any(marker in text for marker in ended_markers) or (
            "身份校验" in text and "已经结束" in text
        ):
            if not session.verification_ended_elsewhere:
                session.verification_ended_elsewhere = True
                session.user_hint = (
                    "服务端验证页显示流程已结束：说明人脸多半已在你的手机/浏览器完成。"
                    "请把验证成功后的回调/跳转网址粘贴回来（推荐），"
                    "或粘贴成功侧完整 Cookie，以你的成功为准完成登录。"
                )
                logger.warning(
                    f"扫码登录验证页已结束（疑似用户侧完成）: {session.session_id}, URL: {page.url}"
                )
            return True
        return False

    async def _probe_browser_login_success(self, session: QRLoginSession, page, context) -> bool:
        """在浏览器侧兜底判断验证是否已经完成。

        原则：哪边先拿到完整登录 Cookie，就以那边为准。
        URL 跳转只是辅助信号，不能否定已到手的 Cookie。
        """
        current_url = page.url
        cookie_dict = await self._context_cookie_dict(context)
        cookies_ready = self._has_completed_login_cookies(cookie_dict)
        url_ready = self._is_logged_in_url(current_url)
        await self._detect_verification_ended_elsewhere(session, page)

        if cookies_ready and url_ready:
            logger.info(
                f"扫码登录浏览器侧检测成功（当前页）: {session.session_id}, URL: {current_url}"
            )
            return self._mark_session_success(session, cookie_dict, 'browser', require_complete_cookies=True)

        # 服务端上下文里已经有完整登录 Cookie：以 Cookie 为准，不必死等 URL
        if cookies_ready:
            logger.info(
                f"扫码登录浏览器侧已持有完整Cookie，按Cookie成功收口: {session.session_id}, URL: {current_url}"
            )
            return self._mark_session_success(session, cookie_dict, 'browser', require_complete_cookies=True)

        # Cookie 尚不完整时，再探测 /im —— 部分风控页要跳转后才落全量登录 Cookie
        probe_page = None
        try:
            probe_page = await context.new_page()
            await probe_page.goto('https://www.goofish.com/im', wait_until='domcontentloaded', timeout=30000)
            await probe_page.wait_for_timeout(1500)

            probe_url = probe_page.url
            probe_cookie_dict = await self._context_cookie_dict(context)
            im_root = await probe_page.query_selector('.rc-virtual-list-holder-inner')
            has_im_root = im_root is not None

            if self._has_completed_login_cookies(probe_cookie_dict):
                logger.info(
                    f"扫码登录浏览器侧探测成功: {session.session_id}, "
                    f"probe_url: {probe_url}, has_im_root: {has_im_root}"
                )
                return self._mark_session_success(
                    session, probe_cookie_dict, 'browser', require_complete_cookies=True
                )
        except Exception as e:
            logger.debug(f"扫码登录浏览器侧探测未确认成功: {session.session_id}, 错误: {e}")
        finally:
            if probe_page:
                try:
                    await probe_page.close()
                except Exception:
                    pass

        return False

    def apply_external_cookies(self, session_id: str, cookies: Any, source: str = 'user') -> Dict[str, Any]:
        """用「用户侧成功」拿到的 Cookie 收口会话。

        用户在手机/本机浏览器完成人脸后，成功 Cookie 落在用户浏览器。
        闲鱼不会回调我们，因此允许把用户侧 Cookie 提交回来，以用户成功为准。
        """
        session = self.sessions.get(session_id)
        if not session:
            return {'success': False, 'status': 'not_found', 'message': '会话不存在或已过期'}

        if session.is_expired() and session.status not in {'success'}:
            session.status = 'expired'
            return {
                'success': False,
                'status': 'expired',
                'message': '会话已过期，请重新发起扫码登录后再提交Cookie',
            }

        if session.status == 'success' and session.unb and self._has_completed_login_cookies(session.cookies):
            return {
                'success': True,
                'status': 'success',
                'message': '会话已是登录成功状态',
                'already_success': True,
                'unb': session.unb,
            }

        if session.status not in {
            'verification_required', 'scanned', 'waiting', 'processing', 'success'
        }:
            return {
                'success': False,
                'status': session.status,
                'message': f'当前会话状态不允许提交Cookie: {session.status}',
            }

        cookie_dict = self._normalize_cookie_dict(cookies)
        if not cookie_dict:
            return {'success': False, 'status': session.status, 'message': 'Cookie为空或格式无法识别'}

        if not self._has_completed_login_cookies(cookie_dict):
            missing = []
            if not cookie_dict.get('unb'):
                missing.append('unb')
            if not any(cookie_dict.get(k) for k in ('cookie2', 'havana_lgc2_77', '_tb_token_', 'sgcookie')):
                missing.append('cookie2/havana_lgc2_77/_tb_token_/sgcookie 之一')
            return {
                'success': False,
                'status': session.status,
                'message': f'Cookie不完整，缺少: {", ".join(missing)}。请从已登录成功的 goofish/闲鱼 浏览器导出完整Cookie。',
            }

        if self._mark_session_success(session, cookie_dict, source, require_complete_cookies=True):
            session.user_hint = None
            logger.info(
                f"扫码登录已按用户侧Cookie成功收口: {session_id}, source={source}, UNB={session.unb}"
            )
            return {
                'success': True,
                'status': 'success',
                'message': '已使用用户侧成功Cookie完成登录',
                'unb': session.unb,
            }

        return {
            'success': False,
            'status': session.status,
            'message': 'Cookie已解析，但未能标记会话成功',
        }

    async def _launch_verification_page(self, session_id: str):
        """在服务端打开验证页面并截取二维码，保持原始会话存活"""
        session = self.sessions.get(session_id)
        if not session or not session.verification_url:
            return

        playwright = None
        browser = None
        context = None
        page = None

        try:
            from playwright.async_api import async_playwright

            logger.info(f"开始打开扫码登录验证页面: {session_id}")
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--lang=zh-CN',
                ]
            )
            context = await browser.new_context(
                viewport={'width': 540, 'height': 960},
                locale='zh-CN',
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ignore_https_errors=True,
                extra_http_headers={
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
                }
            )

            browser_cookies = self._build_browser_cookies(session.verification_url, session.cookies)
            if browser_cookies:
                await context.add_cookies(browser_cookies)

            page = await context.new_page()
            await page.goto(session.verification_url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(2500)

            screenshot_bytes = await page.screenshot(full_page=True)
            if screenshot_bytes:
                screenshot_path = image_manager.save_image(screenshot_bytes)
                if screenshot_path:
                    if session.screenshot_path and session.screenshot_path != screenshot_path:
                        image_manager.delete_image(session.screenshot_path)
                    session.screenshot_path = screenshot_path
                    logger.info(f"扫码登录验证截图已保存: {session_id}, 路径: {screenshot_path}")
                else:
                    logger.warning(f"扫码登录验证截图保存失败: {session_id}")
            else:
                logger.warning(f"扫码登录验证截图为空: {session_id}")

            while True:
                current_session = self.sessions.get(session_id)
                if not current_session:
                    break
                if current_session.status == 'success':
                    logger.info(f"扫码登录验证页检测到会话已成功: {session_id}")
                    break
                if current_session.status not in {'verification_required', 'scanned', 'waiting', 'processing'}:
                    break

                if await self._probe_browser_login_success(current_session, page, context):
                    break

                await page.wait_for_timeout(3000)

        except asyncio.CancelledError:
            logger.info(f"扫码登录验证页面任务已取消: {session_id}")
            raise
        except Exception as e:
            logger.error(f"打开扫码登录验证页面失败: {session_id}, 错误: {e}")
        finally:
            try:
                if page:
                    await page.close()
            except Exception:
                pass
            try:
                if context:
                    await context.close()
            except Exception:
                pass
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass
            try:
                if playwright:
                    await playwright.stop()
            except Exception:
                pass

            latest_session = self.sessions.get(session_id)
            if latest_session:
                latest_session.verification_task = None

            logger.info(f"扫码登录验证页面已关闭: {session_id}")

    def _ensure_verification_task(self, session: QRLoginSession):
        """确保风控验证页面任务只启动一次"""
        task = session.verification_task
        if task and not task.done():
            return
        session.verification_task = asyncio.create_task(self._launch_verification_page(session.session_id))

    def _cleanup_session_assets(self, session: QRLoginSession):
        """清理会话关联的截图和后台任务"""
        task = session.verification_task
        if task and not task.done():
            task.cancel()
        session.verification_task = None

        if session.screenshot_path:
            image_manager.delete_image(session.screenshot_path)
            session.screenshot_path = None

    async def _get_mh5tk(self, session: QRLoginSession) -> dict:
        """获取m_h5_tk和m_h5_tk_enc"""
        data = {"bizScene": "home"}
        data_str = json.dumps(data, separators=(',', ':'))
        t = str(int(time.time() * 1000))
        app_key = "34839810"

        # 先发一次 GET 请求，获取 cookie 中的 m_h5_tk
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=self.proxy) as client:
            try:
                resp = await client.get(self.api_h5_tk, headers=self.headers)
                cookies = {k: v for k, v in resp.cookies.items()}
                session.cookies.update(cookies)

                m_h5_tk = cookies.get("m_h5_tk", "")
                token = m_h5_tk.split("_")[0] if "_" in m_h5_tk else ""

                # 生成签名
                sign_input = f"{token}&{t}&{app_key}&{data_str}"
                sign = hashlib.md5(sign_input.encode()).hexdigest()

                # 构造最终请求参数
                params = {
                    "jsv": "2.7.2",
                    "appKey": app_key,
                    "t": t,
                    "sign": sign,
                    "v": "1.0",
                    "type": "originaljson",
                    "dataType": "json",
                    "timeout": 20000,
                    "api": "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get",
                    "data": data_str,
                }

                # 发请求正式获取数据，确保 token 有效
                await client.post(self.api_h5_tk, params=params, headers=self.headers, cookies=session.cookies)

                return cookies
            except httpx.ConnectTimeout:
                logger.error("获取m_h5_tk时连接超时")
                raise
            except httpx.ReadTimeout:
                logger.error("获取m_h5_tk时读取超时")
                raise
            except httpx.ConnectError:
                logger.error("获取m_h5_tk时连接错误")
                raise

    async def _get_login_params(self, session: QRLoginSession) -> dict:
        """获取二维码登录时需要的表单参数"""
        params = {
            "lang": "zh_cn",
            "appName": "xianyu",
            "appEntrance": "web",
            "styleType": "vertical",
            "bizParams": "",
            "notLoadSsoView": False,
            "notKeepLogin": False,
            "isMobile": False,
            "qrCodeFirst": False,
            "stie": 77,
            "rnd": random(),
        }

        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
            try:
                resp = await client.get(
                    self.api_mini_login,
                    params=params,
                    cookies=session.cookies,
                    headers=self.headers,
                )

                # 正则匹配需要的json数据
                pattern = r"window\.viewData\s*=\s*(\{.*?\});"
                match = re.search(pattern, resp.text)
                if match:
                    json_string = match.group(1)
                    view_data = json.loads(json_string)
                    data = view_data.get("loginFormData")
                    if data:
                        data["umidTag"] = "SERVER"
                        session.params.update(data)
                        return data
                    else:
                        raise GetLoginParamsError("未找到loginFormData")
                else:
                    raise GetLoginParamsError("获取登录参数失败")
            except httpx.ConnectTimeout:
                logger.error("获取登录参数时连接超时")
                raise
            except httpx.ReadTimeout:
                logger.error("获取登录参数时读取超时")
                raise
            except httpx.ConnectError:
                logger.error("获取登录参数时连接错误")
                raise
    
    async def generate_qr_code(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """生成二维码"""
        try:
            # 创建新的会话
            session_id = str(uuid.uuid4())
            session = QRLoginSession(session_id, user_id=user_id)

            # 1. 获取m_h5_tk
            await self._get_mh5tk(session)
            logger.info(f"获取m_h5_tk成功: {session_id}")

            # 2. 获取登录参数
            login_params = await self._get_login_params(session)
            logger.info(f"获取登录参数成功: {session_id}")

            # 3. 生成二维码
            async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
                resp = await client.get(
                    self.api_generate_qr,
                    params=login_params,
                    headers=self.headers
                )
                logger.debug(f"[调试] 获取二维码接口原始响应: {resp.text}")

                try:
                    results = resp.json()
                    logger.debug(f"[调试] 获取二维码接口解析后: {json.dumps(results, ensure_ascii=False)}")
                except Exception as e:
                    logger.exception("二维码接口返回不是JSON")
                    raise GetLoginQRCodeError(f"二维码接口返回异常: {resp.text}")

                if results.get("content", {}).get("success") == True:
                    # 更新会话参数
                    session.params.update({
                        "t": results["content"]["data"]["t"],
                        "ck": results["content"]["data"]["ck"],
                    })

                    # 获取二维码内容
                    qr_content = results["content"]["data"]["codeContent"]
                    session.qr_content = qr_content

                    # 生成二维码图片（base64格式）
                    qr = qrcode.QRCode(
                        version=5,
                        error_correction=qrcode.constants.ERROR_CORRECT_L,
                        box_size=10,
                        border=2,
                    )
                    qr.add_data(qr_content)
                    qr.make()

                    # 将二维码转换为base64
                    from io import BytesIO
                    import base64

                    qr_img = qr.make_image()
                    buffer = BytesIO()
                    qr_img.save(buffer, format='PNG')
                    qr_base64 = base64.b64encode(buffer.getvalue()).decode()
                    qr_data_url = f"data:image/png;base64,{qr_base64}"

                    session.qr_code_url = qr_data_url
                    session.status = 'waiting'

                    # 保存会话
                    self.sessions[session_id] = session

                    # 启动状态检查任务
                    asyncio.create_task(self._monitor_qr_status(session_id))

                    logger.info(f"二维码生成成功: {session_id}")
                    return {
                        'success': True,
                        'session_id': session_id,
                        'qr_code_url': qr_data_url
                    }
                else:
                    raise GetLoginQRCodeError("获取登录二维码失败")

        except httpx.ConnectTimeout as e:
            logger.error(f"连接超时: {e}")
            return {'success': False, 'message': f'连接超时，请检查网络或尝试使用代理'}
        except httpx.ReadTimeout as e:
            logger.error(f"读取超时: {e}")
            return {'success': False, 'message': f'读取超时，服务器响应过慢'}
        except httpx.ConnectError as e:
            logger.error(f"连接错误: {e}")
            return {'success': False, 'message': f'连接错误，请检查网络或代理设置'}
        except Exception as e:
            logger.exception("二维码生成过程中发生异常")
            return {'success': False, 'message': f'生成二维码失败: {str(e)}'}
    
    async def _poll_qrcode_status(self, session: QRLoginSession) -> httpx.Response:
        """获取二维码扫描状态"""
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
            resp = await client.post(
                self.api_scan_status,
                data=session.params,
                cookies=session.cookies,
                headers=self.headers,
            )
            return resp

    async def _monitor_qr_status(self, session_id: str):
        """监控二维码状态"""
        try:
            session = self.sessions.get(session_id)
            if not session:
                return

            logger.info(f"开始监控二维码状态: {session_id}")

            # 监控登录状态
            max_wait_time = 300  # 5分钟
            start_time = time.time()

            while time.time() - start_time < max_wait_time:
                try:
                    # 检查会话是否还存在
                    if session_id not in self.sessions:
                        break
                    if session.status == 'success':
                        logger.info(f"扫码登录API轮询检测到会话已成功: {session_id}")
                        break

                    # 轮询二维码状态
                    resp = await self._poll_qrcode_status(session)
                    if session.status == 'success':
                        logger.info(f"扫码登录API轮询响应返回前，会话已由其他链路成功: {session_id}")
                        break
                    qrcode_status = (
                        resp.json()
                        .get("content", {})
                        .get("data", {})
                        .get("qrCodeStatus")
                    )

                    if qrcode_status == "CONFIRMED":
                        # 登录确认
                        if (
                            resp.json()
                            .get("content", {})
                            .get("data", {})
                            .get("iframeRedirect")
                            is True
                        ):
                            # 账号被风控，需要手机验证
                            session.status = 'verification_required'
                            iframe_url = (
                                resp.json()
                                .get("content", {})
                                .get("data", {})
                                .get("iframeRedirectUrl")
                            )
                            session.verification_url = iframe_url
                            session.expire_time = max(session.expire_time, 600)
                            self._merge_session_cookies(session, resp.cookies)
                            self._ensure_verification_task(session)
                            logger.warning(f"账号被风控，需要手机验证: {session_id}, URL: {iframe_url}")
                            await asyncio.sleep(0.8)
                            continue
                        else:
                            # 登录成功
                            if self._mark_session_success(session, resp.cookies, 'api'):
                                break
                            logger.warning(f"扫码登录API返回成功状态，但关键Cookie不足: {session_id}")

                    elif qrcode_status == "NEW":
                        # 二维码未被扫描，继续轮询
                        continue

                    elif qrcode_status == "EXPIRED":
                        # 二维码已过期
                        if session.status == 'verification_required':
                            logger.info(f"二维码已过期，但会话已进入验证流程，继续等待: {session_id}")
                        else:
                            session.status = 'expired'
                            logger.info(f"二维码已过期: {session_id}")
                            break

                    elif qrcode_status == "SCANED":
                        # 二维码已被扫描，等待确认
                        if session.status == 'waiting':
                            session.status = 'scanned'
                            logger.info(f"二维码已扫描，等待确认: {session_id}")
                    elif qrcode_status in ("CANCELED", "CANCELLED", "CANCEL"):
                        # 只在显式取消状态下终止会话
                        if session.status == 'verification_required':
                            logger.info(f"扫码状态 {qrcode_status}，但验证流程仍在进行，继续等待: {session_id}")
                        else:
                            session.status = 'cancelled'
                            logger.info(f"用户取消登录: {session_id}")
                            break
                    else:
                        # 未知/空状态（网络抖动、服务端新增状态码），继续轮询而不是当作取消
                        logger.debug(f"未知扫码状态 {qrcode_status!r}，继续轮询: {session_id}")

                    await asyncio.sleep(0.8)  # 每0.8秒检查一次

                except Exception as e:
                    logger.error(f"监控二维码状态异常: {e}")
                    await asyncio.sleep(2)

            # 超时处理
            if session.status not in ['success', 'expired', 'cancelled', 'verification_required']:
                session.status = 'expired'
                logger.info(f"二维码监控超时，标记为过期: {session_id}")

        except Exception as e:
            logger.error(f"监控二维码状态失败: {e}")
            if session_id in self.sessions:
                self.sessions[session_id].status = 'expired'
    
    def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """获取会话状态"""
        session = self.sessions.get(session_id)
        if not session:
            return {'status': 'not_found'}

        if session.is_expired() and session.status != 'success':
            session.status = 'expired'

        result = {
            'status': session.status,
            'session_id': session_id
        }
        logger.info(f"获取会话状态: {result}")
        # 如果需要验证，返回验证URL
        if session.status == 'verification_required':
            result['verification_url'] = session.verification_url
            result['screenshot_path'] = session.screenshot_path
            result['verification_ended_elsewhere'] = bool(session.verification_ended_elsewhere)
            result['accept_user_cookies'] = True
            result['accept_user_url'] = True
            if session.user_hint:
                result['message'] = session.user_hint
            elif session.verification_ended_elsewhere:
                result['message'] = (
                    '服务端验证页已结束。若你已在手机/浏览器完成验证，'
                    '请粘贴成功后的回调/跳转网址（推荐），或粘贴完整 Cookie'
                )
            else:
                result['message'] = (
                    '账号被风控：优先扫服务端截图二维码；'
                    '若你已在其它浏览器完成验证，可粘贴成功后的回调网址或完整 Cookie'
                ) if session.screenshot_path else '账号被风控，正在准备验证二维码'

        # 如果登录成功，返回Cookie信息
        if session.status == 'success' and session.cookies and session.unb:
            result['cookies'] = self._cookie_marshal(session.cookies)
            result['unb'] = session.unb
            result['success_source'] = session.success_source

        return result

    def cleanup_expired_sessions(self):
        """清理过期会话"""
        expired_sessions = []
        for session_id, session in self.sessions.items():
            if session.is_expired():
                expired_sessions.append(session_id)

        for session_id in expired_sessions:
            self._cleanup_session_assets(self.sessions[session_id])
            del self.sessions[session_id]
            logger.info(f"清理过期会话: {session_id}")

    def get_session_cookies(self, session_id: str) -> Optional[Dict[str, str]]:
        """获取会话Cookie"""
        session = self.sessions.get(session_id)
        if session and session.status == 'success':
            return {
                'cookies': self._cookie_marshal(session.cookies),
                'unb': session.unb
            }
        return None

# 全局二维码登录管理器实例
qr_login_manager = QRLoginManager()
