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
        self.screenshot_path = None  # 风控验证截图/可扫二维码图
        self.verification_task = None  # 风控验证页面保持任务
        self.verification_entered_at = None  # 进入风控验证流程的时间（用于兜底超时）
        self.probe_fail_count = 0  # 浏览器侧探测连续失败次数（用于退避/中止）
        self.success_source = None  # 登录成功来源: api/browser/user
        # 服务端验证页已变成「流程结束」——通常表示用户在其它浏览器完成了人脸
        self.verification_ended_elsewhere = False
        self.user_hint = None
        # CONFIRMED 响应里可能带的 login_token（风控后用于换 Cookie）
        self.pending_login_token = None
        # 是否已为 verification_url 生成过「不消耗令牌」的二维码图
        self.verification_qr_encoded = False
        # 服务端是否已用该 verification_url 打开过 Playwright 页
        # （一次性 havana_iv_token 已绑定服务端会话，此后禁止再把 URL 交给前端）
        self.verification_page_opened = False
        # 验证页结束后同 context 收割 Cookie 的尝试次数/最近一次时间
        # （同会话扫码成功后 unb 落盘有几秒延迟，单次机会不够）
        self.verification_harvest_attempts = 0
        self.last_harvest_at = 0.0

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

        # 风控验证兜底：二维码 EXPIRED 后验证流程最多再等 5 分钟，防止监控循环无限空转烧 CPU
        self.max_verification_wait = 300
        # 浏览器侧探测连续失败上限（超时/DNS 不通时退避并最终放弃，防止 Chromium 空转）
        self.max_probe_failures = 10
        # 验证结束后同 context 新开页收割 Cookie 的最大次数与最小间隔（unb 落盘有秒级延迟）
        self.max_harvest_attempts = 3
        self.harvest_retry_interval = 6.0

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

        login_token = _pick(
            query,
            'token', 'lgToken', 'login_token', 'loginToken', 'loginTicket', 'ticket',
        ) or _pick(
            fragment_query,
            'token', 'lgToken', 'login_token', 'loginToken', 'loginTicket', 'ticket',
        )
        if login_token:
            tokens['login_token'] = login_token

        havana_iv = _pick(query, 'havana_iv_token', 'havanaIvToken', 'iv_token') or _pick(
            fragment_query, 'havana_iv_token', 'havanaIvToken', 'iv_token'
        )
        if havana_iv:
            tokens['havana_iv_token'] = havana_iv

        stoken = _pick(query, 'stoken', 's_token', 'ssoToken') or _pick(
            fragment_query, 'stoken', 's_token', 'ssoToken'
        )
        if stoken:
            tokens['stoken'] = stoken

        # 部分成功页把 token 塞在 path 末段（极少见，兜底）
        if not tokens.get('login_token') and parsed.path:
            path_parts = [p for p in parsed.path.split('/') if p]
            for part in reversed(path_parts[-2:]):
                if 16 <= len(part) <= 128 and re.fullmatch(r'[A-Za-z0-9_=\-]+', part):
                    # 太像普通路径名则跳过
                    if part.lower() in {
                        'login', 'callback', 'iv', 'verify', 'qrcode', 'mini_login', 'im',
                    }:
                        continue
                    tokens.setdefault('login_token_guess', part)
                    break

        return tokens

    def _cookies_from_httpx_response(self, resp) -> Dict[str, str]:
        """从 httpx 响应提取 cookie（含 set-cookie 多域）。"""
        cookie_dict: Dict[str, str] = {}
        try:
            cookie_dict.update({k: v for k, v in resp.cookies.items()})
        except Exception:
            pass
        # httpx Cookies 有时只暴露部分；再从 jar 扫一遍
        try:
            jar = getattr(resp, 'cookies', None)
            if jar is not None:
                for c in jar.jar:
                    if c.name and c.value is not None:
                        cookie_dict[str(c.name)] = str(c.value)
        except Exception:
            pass
        return cookie_dict

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

        cookie_dict: Dict[str, str] = {}
        # 换 token 用更短超时，避免前端卡在「换取登录态」数分钟
        exchange_timeout = httpx.Timeout(connect=15.0, read=25.0, write=15.0, pool=25.0)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=exchange_timeout,
            proxy=self.proxy,
        ) as client:
            headers = {
                **self.headers,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://passport.goofish.com',
                'Referer': 'https://passport.goofish.com/mini_login.htm',
            }
            resp = await client.post(
                f'{self.host}/login_token/login.do',
                params=params,
                data=data or {'deviceId': device_id or 'unknown'},
                cookies=session.cookies,
                headers=headers,
            )
            cookie_dict.update(self._cookies_from_httpx_response(resp))

            # 登录后刷新用户态 mtop（部分批次 unb 在此之后才落下）
            try:
                nav_resp = await client.post(
                    'https://h5api.m.goofish.com/h5/mtop.idle.web.user.page.nav/1.0/',
                    params={
                        'jsv': '2.7.2',
                        'appKey': '34839810',
                        't': str(int(time.time() * 1000)),
                        'sign': '',
                        'v': '1.0',
                        'type': 'originaljson',
                        'dataType': 'json',
                        'timeout': '20000',
                        'api': 'mtop.idle.web.user.page.nav',
                        'sessionOption': 'AutoLoginOnly',
                    },
                    data='data=%7B%7D',
                    cookies={**session.cookies, **cookie_dict},
                    headers={
                        **self.headers,
                        'Referer': 'https://www.goofish.com/',
                        'Origin': 'https://www.goofish.com',
                    },
                )
                cookie_dict.update(self._cookies_from_httpx_response(nav_resp))
            except Exception as e:
                logger.debug(f"login_token 换取后刷新 nav 失败: {session.session_id}, {e}")

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
                cookie_dict.update(self._cookies_from_httpx_response(im_resp))
            except Exception as e:
                logger.debug(f"login_token 换取后访问 /im 失败: {session.session_id}, {e}")

            logger.info(
                f"login_token 换取完成: {session.session_id}, "
                f"status={resp.status_code}, cookie_keys={list(cookie_dict.keys())}, "
                f"has_unb={bool(cookie_dict.get('unb'))}"
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
        url_lower = url.lower()
        is_expired_or_iv_only = any(
            m in url_lower for m in (
                'mini_expired', 'expired.htm', 'timeout.htm',
                'mini_login_check.htm', 'havana_iv_token=',
            )
        )

        # 1) 若 URL 带 login_token / lgToken，优先 API 换 Cookie（轻量、短超时）
        login_token = (
            tokens.get('login_token')
            or tokens.get('login_token_guess')
            or session.pending_login_token
        )
        tried_tokens = set()
        if login_token:
            tried_tokens.add(login_token)
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

        # 无 login_token 且是过期页/纯风控 IV 页：不要再开 Playwright 空耗 3 分钟
        if not login_token and is_expired_or_iv_only:
            session.user_hint = (
                '该链接无法换取登录 Cookie（过期页或仅含风控令牌，没有 login_token）。'
                '请改贴成功侧完整 Cookie（必须含 unb + cookie2/sgcookie），'
                '或重新发起扫码并用手机闲鱼 APP 扫系统页上的验证二维码。'
            )
            logger.warning(
                f"回调URL无login_token且为过期/IV页，快速失败: {session_id}, "
                f"url_host={urlparse(url).hostname}"
            )
            return {
                'success': False,
                'status': session.status,
                'message': session.user_hint,
                'missing_keys': ['unb', 'login_token'],
                'via': 'fast_fail_no_token',
            }

        # 2) Playwright 打开回调 URL，在同一会话 Cookie 上下文中收口
        #    用更短 goto 超时，避免前端卡在「正在用回调网址换取登录态」数分钟
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
            # 同时挂 passport + goofish 域 cookie，提高 unb 落盘概率
            browser_cookies = self._build_browser_cookies(url, session.cookies)
            browser_cookies += self._build_browser_cookies('https://www.goofish.com/', session.cookies)
            # 去重 (name,url)
            seen_ck = set()
            deduped = []
            for ck in browser_cookies:
                key = (ck.get('name'), ck.get('url'))
                if key in seen_ck:
                    continue
                seen_ck.add(key)
                deduped.append(ck)
            if deduped:
                await context.add_cookies(deduped)

            page = await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=25000)
            await page.wait_for_timeout(1500)

            # 页面若再次给出 token 链接，尝试提取
            try:
                current_url = page.url
                page_tokens = self._extract_login_tokens_from_url(current_url)
                page_login_token = page_tokens.get('login_token') or page_tokens.get('login_token_guess')
                if page_login_token and page_login_token not in tried_tokens:
                    tried_tokens.add(page_login_token)
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
            missing = []
            if not final_cookies.get('unb'):
                missing.append('unb')
            if not any(final_cookies.get(k) for k in ('cookie2', 'havana_lgc2_77', '_tb_token_', 'sgcookie')):
                missing.append('cookie2/sgcookie')
            session.user_hint = (
                '已打开回调URL，但服务端仍未拿到完整登录Cookie'
                + (f'（缺 {", ".join(missing)}）' if missing else '')
                + '。常见原因：1) 粘贴的是验证中/过期页而非成功后跳转链接；'
                '2) 成功 Cookie 只落在你的手机浏览器，服务端打不开同一会话。'
                '请改贴成功侧完整 Cookie（必须含 unb）。'
            )
            logger.warning(
                f"回调URL未能换取完整Cookie: {session_id}, url_host={urlparse(url).hostname}, "
                f"cookie_keys={list(final_cookies.keys())}, missing={missing}"
            )
            return {
                'success': False,
                'status': session.status,
                'message': session.user_hint,
                'cookie_keys': sorted(final_cookies.keys()),
                'missing_keys': missing,
            }
        except Exception as e:
            logger.error(f"回调URL换取Cookie失败: {session_id}, 错误: {e}")
            return {
                'success': False,
                'status': session.status,
                'message': (
                    f'打开回调URL失败: {e}。'
                    '若网络超时，请直接粘贴成功侧完整 Cookie（含 unb）。'
                ),
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
        """检测服务端验证页是否已变成「流程结束」（常见于用户在其它浏览器完成人脸）。

        注意：mini_expired.htm / 二维码过期页 ≠ 用户已完成验证，绝不能误判。
        """
        try:
            current_url = str(page.url or '')
        except Exception:
            current_url = ''

        # 过期/失效页：不是「用户侧完成」，而是需要重新出码
        expired_url_markers = (
            'mini_expired',
            'qrcode_expired',
            'expired.htm',
            'timeout.htm',
        )
        if any(marker in current_url.lower() for marker in expired_url_markers):
            session.user_hint = (
                '服务端验证页已过期/失效，无法再扫。请关闭弹窗后重新发起扫码登录。'
            )
            logger.info(
                f"扫码登录验证页为过期页（非用户完成）: {session.session_id}, URL: {current_url}"
            )
            return False

        try:
            text = await page.evaluate("() => (document.body && document.body.innerText) || ''")
        except Exception:
            text = ""
        text = str(text or "")

        # 纯过期文案也不算「用户完成」
        expired_text_markers = (
            "二维码已失效",
            "二维码已过期",
            "请重新获取",
            "验证超时",
            "页面已过期",
        )
        if any(marker in text for marker in expired_text_markers) and not any(
            m in text for m in ("身份校验流程已经结束", "校验流程已经结束", "验证已完成")
        ):
            session.user_hint = (
                '服务端验证二维码已过期。请关闭弹窗后重新发起扫码登录。'
            )
            return False

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
                    "服务端验证页显示流程已结束。若你扫的是服务端截图码，"
                    "系统正在同会话收割登录 Cookie，请保持弹窗等待自动收口；"
                    "仅当长时间无结果时再粘贴回调网址。"
                )
                logger.warning(
                    f"扫码登录验证页已结束（疑似用户侧完成）: {session.session_id}, URL: {current_url}"
                )
            return True
        return False

    async def _page_has_scannable_qr(self, page) -> bool:
        """判断验证页是否已渲染出可供手机扫的二维码（canvas/img/二维码容器）。"""
        try:
            return bool(await page.evaluate(
                """() => {
                    const isVisible = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return r.width >= 80 && r.height >= 80
                            && style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && style.opacity !== '0';
                    };
                    const canvases = Array.from(document.querySelectorAll('canvas'));
                    if (canvases.some(isVisible)) return true;
                    const imgs = Array.from(document.querySelectorAll('img'));
                    for (const img of imgs) {
                        if (!isVisible(img)) continue;
                        const src = (img.currentSrc || img.src || '').toLowerCase();
                        const alt = (img.alt || '').toLowerCase();
                        if (src.includes('qr') || src.includes('code') || src.startsWith('data:image')
                            || alt.includes('二维码') || alt.includes('qr')) {
                            return true;
                        }
                        // 常见方形验证码图
                        const r = img.getBoundingClientRect();
                        if (Math.abs(r.width - r.height) < 30 && r.width >= 120) return true;
                    }
                    const selectors = [
                        '[class*="qrcode" i]', '[id*="qrcode" i]',
                        '[class*="qr-code" i]', '[id*="qr-code" i]',
                        '[class*="qr_code" i]', '[class*="scan" i]',
                    ];
                    for (const sel of selectors) {
                        try {
                            const nodes = document.querySelectorAll(sel);
                            if (Array.from(nodes).some(isVisible)) return true;
                        } catch (e) {}
                    }
                    return false;
                }"""
            ))
        except Exception as e:
            logger.debug(f"检测验证页二维码失败: {e}")
            return False

    async def _capture_verification_screenshot(
        self,
        session: QRLoginSession,
        page,
        *,
        full_page: bool = False,
        timeout_ms: int = 5000,
    ) -> bool:
        """截取验证页并写入 session.screenshot_path，供前端展示给手机扫。

        默认 viewport 截图 + 短超时：full_page/长超时会卡住 keep-alive 循环，延误 Cookie 探测。
        成功截到真页面后清除 verification_qr_encoded，避免前端仍当「链接码」展示。
        """
        try:
            screenshot_bytes = await page.screenshot(
                full_page=full_page,
                type='png',
                timeout=timeout_ms,
                animations='disabled',
            )
        except TypeError:
            # 旧版 Playwright 无 animations 参数
            try:
                screenshot_bytes = await page.screenshot(
                    full_page=full_page,
                    type='png',
                    timeout=timeout_ms,
                )
            except Exception as e:
                logger.warning(f"截取验证页失败: {session.session_id}, {e}")
                return False
        except Exception as e:
            logger.warning(f"截取验证页失败: {session.session_id}, {e}")
            return False
        if not screenshot_bytes:
            return False
        screenshot_path = image_manager.save_image(screenshot_bytes)
        if not screenshot_path:
            logger.warning(f"扫码登录验证截图保存失败: {session.session_id}")
            return False
        if session.screenshot_path and session.screenshot_path != screenshot_path:
            image_manager.delete_image(session.screenshot_path)
        session.screenshot_path = screenshot_path
        # 真页面截图替换了任何 encode 兜底图
        session.verification_qr_encoded = False
        logger.info(f"扫码登录验证截图已保存: {session.session_id}, 路径: {screenshot_path}")
        return True

    async def _probe_browser_login_success(self, session: QRLoginSession, page, context) -> bool:
        """在浏览器侧判断验证是否已经完成（对齐 GuDong）。

        原则：
        - 以 context 里的完整登录 Cookie 为准；
        - Cookie 不完整时 **绝不** 再开 /im（会 30s 超时拖死 keep-alive，且无 unb 时无意义）；
        - /im 仅在「已有完整 Cookie 但需二次确认」的极端路径使用，且短超时。
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
            session.probe_fail_count = 0
            return self._mark_session_success(session, cookie_dict, 'browser', require_complete_cookies=True)

        # 已有完整 Cookie：以 Cookie 为准收口（不必死等 URL）
        if cookies_ready:
            logger.info(
                f"扫码登录浏览器侧已持有完整Cookie，按Cookie成功收口: {session.session_id}, URL: {current_url}"
            )
            session.probe_fail_count = 0
            return self._mark_session_success(session, cookie_dict, 'browser', require_complete_cookies=True)

        # GuDong: if not cookies_ready: return False —— 禁止无 Cookie 时 goto /im
        return False

    async def _harvest_login_cookies_after_verification(
        self,
        session: QRLoginSession,
        page,
        context,
    ) -> bool:
        """验证页已结束但 Cookie 未齐时，在同一 Playwright context 内**新开页**收割。

        手机扫的是服务端页时，部分批次 unb/companion 要等跳转 goofish 后才落盘。
        铁律：
        - 绝不导航 keep-alive 的验证页本身（ended 若误判，会把用户正在扫的码导航掉）；
        - 用 context.new_page() 开临时页，用完必关，Cookie 仍落同一 context；
        - 短超时 + 有限次重试（unb 落盘有秒级延迟，单次机会不够）。
        """
        if not session:
            return False
        if session.status == 'success' and self._has_completed_login_cookies(session.cookies):
            return True
        if session.verification_harvest_attempts >= self.max_harvest_attempts:
            return False
        now = time.time()
        if now - session.last_harvest_at < self.harvest_retry_interval:
            return False

        session.verification_harvest_attempts += 1
        session.last_harvest_at = now
        attempt = session.verification_harvest_attempts
        logger.info(
            f"验证结束后同会话收割 Cookie（第 {attempt}/{self.max_harvest_attempts} 次）: "
            f"{session.session_id}, keepalive_url={getattr(page, 'url', '')}"
        )

        harvest_page = None
        try:
            # 关键：新开页，不动用户正在扫的验证页
            harvest_page = await context.new_page()
            try:
                await harvest_page.goto(
                    'https://www.goofish.com/im',
                    wait_until='domcontentloaded',
                    timeout=12000,
                )
                await harvest_page.wait_for_timeout(1500)
            except Exception as e:
                logger.info(
                    f"验证后收割页导航未完成（继续读 context Cookie）: "
                    f"{session.session_id}, {e}"
                )

            cookie_dict = await self._context_cookie_dict(context)
            keys = sorted(cookie_dict.keys())
            logger.info(
                f"验证后收割 cookie keys: {session.session_id}, "
                f"n={len(keys)}, has_unb={'unb' in cookie_dict}"
            )
            if self._has_completed_login_cookies(cookie_dict):
                return self._mark_session_success(
                    session, cookie_dict, 'browser', require_complete_cookies=True
                )
        except Exception as e:
            logger.debug(f"验证后收割 Cookie 失败: {session.session_id}, {e}")
        finally:
            if harvest_page is not None:
                try:
                    await harvest_page.close()
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

    def _encode_verification_url_as_qr(self, session: QRLoginSession) -> bool:
        """把 iframeRedirectUrl 编码成二维码图（仅 Playwright 完全打不开时的末路兜底）。

        主路径必须是 Playwright 打开并保持验证页（GuDong 模型），Cookie 才会落在服务端。
        手机扫此独立 URL **不会**写回服务端 Cookie，还可能一次性烧掉 havana_iv_token。
        keep-alive 验证页存活期间 **禁止** 调用本方法覆盖 screenshot_path。
        """
        url = str(session.verification_url or '').strip()
        if not url:
            return False
        try:
            from io import BytesIO

            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color='black', back_color='white')
            buffer = BytesIO()
            qr_img.save(buffer, format='PNG')
            screenshot_path = image_manager.save_image(buffer.getvalue())
            if not screenshot_path:
                logger.warning(f"验证URL二维码保存失败: {session.session_id}")
                return False
            if session.screenshot_path and session.screenshot_path != screenshot_path:
                image_manager.delete_image(session.screenshot_path)
            session.screenshot_path = screenshot_path
            session.verification_qr_encoded = True
            session.user_hint = (
                '服务端无法打开验证页，已生成链接兜底码（非服务端会话）。'
                '扫此码完成的认证不会自动登录本系统；请改用「提交回调网址」，'
                '或关闭后重新扫码登录等待服务端截图。'
            )
            logger.warning(
                f"风控验证URL已编码为兜底二维码（非 keep-alive 页）: {session.session_id}, "
                f"path={screenshot_path}"
            )
            return True
        except Exception as e:
            logger.error(f"编码验证URL二维码失败: {session.session_id}, {e}")
            return False

    async def _poll_verification_login_success(self, session: QRLoginSession) -> bool:
        """风控验证后，用 API 会话 Cookie 探测是否已拿到完整登录态。

        不打开 havana_iv 页面（避免消耗令牌）。优先：pending_login_token 换 Cookie，
        再访问 goofish /im / mtop 看 unb 是否落下。
        """
        if not session:
            return False
        if session.status == 'success' and self._has_completed_login_cookies(session.cookies):
            return True

        # 1) 若 CONFIRMED 时缓存了 login_token，优先换
        if session.pending_login_token and not session.cookies.get('unb'):
            try:
                exchanged = await self._exchange_login_token(session, session.pending_login_token)
                self._merge_session_cookies(session, exchanged)
                if self._has_completed_login_cookies(session.cookies):
                    return self._mark_session_success(
                        session, session.cookies, 'api', require_complete_cookies=True
                    )
            except Exception as e:
                logger.debug(f"验证后 pending_login_token 换取失败: {session.session_id}, {e}")

        # 2) 轻量 HTTP 探测 /im + mtop（不启 Chromium）
        try:
            probe_timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=15.0)
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=probe_timeout,
                proxy=self.proxy,
            ) as client:
                cookie_dict: Dict[str, str] = dict(session.cookies)
                try:
                    nav_resp = await client.post(
                        'https://h5api.m.goofish.com/h5/mtop.idle.web.user.page.nav/1.0/',
                        params={
                            'jsv': '2.7.2',
                            'appKey': '34839810',
                            't': str(int(time.time() * 1000)),
                            'sign': '',
                            'v': '1.0',
                            'type': 'originaljson',
                            'dataType': 'json',
                            'timeout': '20000',
                            'api': 'mtop.idle.web.user.page.nav',
                            'sessionOption': 'AutoLoginOnly',
                        },
                        data='data=%7B%7D',
                        cookies=cookie_dict,
                        headers={
                            **self.headers,
                            'Referer': 'https://www.goofish.com/',
                            'Origin': 'https://www.goofish.com',
                        },
                    )
                    cookie_dict.update(self._cookies_from_httpx_response(nav_resp))
                except Exception as e:
                    logger.debug(f"验证后 nav 探测失败: {session.session_id}, {e}")

                try:
                    im_resp = await client.get(
                        'https://www.goofish.com/im',
                        cookies=cookie_dict,
                        headers={
                            **self.headers,
                            'Referer': 'https://www.goofish.com/',
                            'Origin': 'https://www.goofish.com',
                        },
                    )
                    cookie_dict.update(self._cookies_from_httpx_response(im_resp))
                except Exception as e:
                    logger.debug(f"验证后 /im 探测失败: {session.session_id}, {e}")

                self._merge_session_cookies(session, cookie_dict)
                if self._has_completed_login_cookies(session.cookies):
                    logger.info(
                        f"风控验证后 HTTP 探测拿到完整Cookie: {session.session_id}, "
                        f"UNB={session.unb}"
                    )
                    session.probe_fail_count = 0
                    return self._mark_session_success(
                        session, session.cookies, 'api', require_complete_cookies=True
                    )
                session.probe_fail_count += 1
        except Exception as e:
            session.probe_fail_count += 1
            logger.debug(
                f"验证后登录探测异常（连续失败 {session.probe_fail_count}）: "
                f"{session.session_id}, {e}"
            )
        return False

    async def _launch_verification_page(self, session_id: str):
        """在服务端打开验证页面并截取二维码，保持原始会话存活（对齐 GuDong）。

        产品模型（与 GuDong2003/xianyu-auto-reply 一致）：
        - Playwright **打开并保持** iframeRedirect 验证页；
        - 尽快截图给前端（VPS 无摄像头，用户用手机扫这张截图）；
        - 手机扫的是**服务端这一个浏览器会话**里的码，成功 Cookie 会落到同一 context；
        - 后台读 context Cookie 自动收口。

        铁律（a0b72c6d 实锤）：keep-alive 存活期间 **禁止** 把 verification_url
        encode 成独立二维码。手机扫独立 URL 会烧掉一次性 havana_iv_token，
        服务端永远收不到 unb，用户却以为「认证成功」。
        encode 仅允许在 Playwright 完全无法启动时作为末路兜底。
        """
        session = self.sessions.get(session_id)
        if not session or not session.verification_url:
            return

        playwright = None
        browser = None
        context = None
        page = None

        try:
            from playwright.async_api import async_playwright

            logger.info(f"开始打开扫码登录验证页面（GuDong keep-alive）: {session_id}")
            session.user_hint = '账号被风控：正在打开服务端验证页并截取二维码，请稍候…'
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
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                ),
                ignore_https_errors=True,
                extra_http_headers={
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
                }
            )

            # 同时挂验证域 + goofish 域 Cookie，提高收口概率
            browser_cookies = self._build_browser_cookies(session.verification_url, session.cookies)
            browser_cookies += self._build_browser_cookies('https://www.goofish.com/', session.cookies)
            seen_ck = set()
            deduped = []
            for ck in browser_cookies:
                key = (ck.get('name'), ck.get('url'))
                if key in seen_ck:
                    continue
                seen_ck.add(key)
                deduped.append(ck)
            if deduped:
                await context.add_cookies(deduped)

            page = await context.new_page()
            # 令牌自此绑定服务端会话：即使后续 keep-alive 结束，也不得再把 URL 给前端
            session.verification_page_opened = True
            await page.goto(session.verification_url, wait_until='domcontentloaded', timeout=60000)

            # GuDong 风格：先短等再首屏截图，避免等满 scannable 才出图（用户会空等/误扫 encode）
            await page.wait_for_timeout(2500)

            def _page_expired(url: str) -> bool:
                u = str(url or '').lower()
                return any(m in u for m in ('mini_expired', 'expired.htm', 'timeout.htm'))

            try:
                cur_url = str(page.url or '')
            except Exception:
                cur_url = ''

            if _page_expired(cur_url):
                session.user_hint = (
                    '服务端验证页已过期，无法展示可扫二维码。请关闭后重新扫码登录。'
                    '（若刚才扫过「链接生成的码」，一次性令牌可能已在手机会话消耗。）'
                )
                logger.warning(f"验证页打开即为过期页: {session_id}, URL: {cur_url}")
                await self._capture_verification_screenshot(session, page)
                # keep-alive 已打开：禁止 encode 覆盖；无图就保持提示
                return

            # 首屏：viewport 快截；失败再重试，绝不立刻 encode；截图失败不得拖死 keep-alive
            try:
                captured = await asyncio.wait_for(
                    self._capture_verification_screenshot(session, page, timeout_ms=4000),
                    timeout=6.0,
                )
            except Exception as e:
                logger.warning(f"首屏截图超时/失败，继续 keep-alive: {session_id}, {e}")
                captured = False
            if not captured:
                await page.wait_for_timeout(1000)
                try:
                    captured = await asyncio.wait_for(
                        self._capture_verification_screenshot(session, page, timeout_ms=4000),
                        timeout=6.0,
                    )
                except Exception as e:
                    logger.warning(f"首屏截图重试失败: {session_id}, {e}")
                    captured = False

            # 再等可扫二维码（最多 ~12s），出码后升级截图
            qr_ready = await self._page_has_scannable_qr(page)
            if not qr_ready:
                for _attempt in range(12):
                    current = self.sessions.get(session_id)
                    if not current or current.status not in {
                        'verification_required', 'scanned', 'waiting', 'processing'
                    }:
                        return
                    try:
                        cur_url = str(page.url or '')
                    except Exception:
                        cur_url = ''
                    if _page_expired(cur_url):
                        session.user_hint = (
                            '服务端验证页已过期。请关闭后重新扫码登录。'
                        )
                        logger.warning(f"验证页变为过期页: {session_id}, URL: {cur_url}")
                        await self._capture_verification_screenshot(session, page)
                        return
                    if await self._page_has_scannable_qr(page):
                        qr_ready = True
                        break
                    await page.wait_for_timeout(1000)

            if qr_ready:
                session.user_hint = (
                    '账号被风控：请用手机闲鱼 APP 扫描下方「服务端验证页」二维码。'
                    '扫的是服务端会话，成功后系统会自动收口，无需粘贴 Cookie。'
                )
                try:
                    await asyncio.wait_for(
                        self._capture_verification_screenshot(session, page, timeout_ms=4000),
                        timeout=6.0,
                    )
                except Exception as e:
                    logger.warning(f"可扫态升级截图失败: {session_id}, {e}")
            elif session.screenshot_path:
                session.user_hint = (
                    '服务端验证页已打开，但尚未检测到清晰二维码；'
                    '请查看下方截图。若无法扫码请稍候或重新发起登录。'
                )
            else:
                # 页开着但截图持续失败：继续 keep-alive 等重截，禁止 encode
                session.user_hint = (
                    '服务端验证页已打开，截图尚未就绪，请保持弹窗等待…'
                    '不要扫任何「链接生成」的码。'
                )
                logger.warning(
                    f"验证页截图尚未成功，继续 keep-alive 等待重截: {session_id}, URL: {page.url}"
                )

            last_resnapshot = time.time()
            last_cookie_log = 0.0
            while True:
                current_session = self.sessions.get(session_id)
                if not current_session:
                    break
                if current_session.status == 'success':
                    logger.info(f"扫码登录验证页检测到会话已成功: {session_id}")
                    break
                if current_session.status not in {
                    'verification_required', 'scanned', 'waiting', 'processing'
                }:
                    break

                entered = current_session.verification_entered_at
                if entered and time.time() - entered > self.max_verification_wait:
                    current_session.status = 'expired'
                    current_session.user_hint = (
                        f'验证等待超过 {self.max_verification_wait}s 仍未拿到登录 Cookie。'
                        '请重新扫码登录。'
                    )
                    logger.warning(
                        f"扫码登录验证流程超过{self.max_verification_wait}s未完成，"
                        f"关闭验证页并标记过期: {session_id}"
                    )
                    break

                if current_session.probe_fail_count >= self.max_probe_failures:
                    current_session.status = 'expired'
                    current_session.user_hint = (
                        '服务端验证页探测连续失败，已停止等待。请重新扫码登录。'
                    )
                    logger.warning(
                        f"扫码登录浏览器侧探测连续失败 {current_session.probe_fail_count} 次，"
                        f"放弃验证页并标记过期: {session_id}"
                    )
                    break

                try:
                    cur_url = str(page.url or '')
                except Exception:
                    cur_url = ''
                if _page_expired(cur_url):
                    current_session.user_hint = (
                        '服务端验证页已过期。请关闭后重新扫码登录。'
                        '（若扫过链接兜底码，令牌可能已在手机侧消耗。）'
                    )
                    logger.warning(f"keep-alive 中验证页过期: {session_id}, URL: {cur_url}")
                    current_session.status = 'expired'
                    break

                # 周期性重截：无图时更勤（4s），有图 10s；禁止 encode。
                # 截图用 wait_for 封顶，绝不阻塞 Cookie 探测（a0b72c6d：截图 30s 超时拖死循环）。
                resnap_interval = 4 if not current_session.screenshot_path else 10
                if time.time() - last_resnapshot >= resnap_interval:
                    try:
                        scannable = await self._page_has_scannable_qr(page)
                    except Exception:
                        scannable = False
                    if scannable or not current_session.screenshot_path:
                        try:
                            ok = await asyncio.wait_for(
                                self._capture_verification_screenshot(
                                    current_session, page, timeout_ms=4000
                                ),
                                timeout=6.0,
                            )
                        except Exception as e:
                            logger.debug(f"验证页周期截图跳过: {session_id}, {e}")
                            ok = False
                        if ok and scannable:
                            current_session.user_hint = (
                                '账号被风控：请用手机闲鱼 APP 扫描下方「服务端验证页」二维码。'
                                '扫的是服务端会话，成功后自动收口，无需粘贴 Cookie。'
                            )
                    last_resnapshot = time.time()

                # 轻量：只读 context Cookie（GuDong）；完整 Cookie 即收口
                try:
                    cookie_dict = await self._context_cookie_dict(context)
                    keys = sorted(cookie_dict.keys())
                    now = time.time()
                    if now - last_cookie_log >= 15:
                        logger.info(
                            f"验证页 context cookie keys: {session_id}, "
                            f"n={len(keys)}, has_unb={'unb' in cookie_dict}, "
                            f"url={cur_url[:120]}"
                        )
                        last_cookie_log = now
                    if self._has_completed_login_cookies(cookie_dict):
                        if self._mark_session_success(
                            current_session, cookie_dict, 'browser', require_complete_cookies=True
                        ):
                            break
                    ended = await self._detect_verification_ended_elsewhere(current_session, page)
                    # 收割触发有两条：①页面文案显示「流程已结束」；
                    # ②验证页已自行跳出 passport/iv（同会话扫码成功的典型表现），
                    # 但 context 里 unb 还没落盘 —— 二者都要在同 context 新开页兜一次。
                    need_harvest = (
                        ended or self._is_logged_in_url(cur_url)
                    ) and not self._has_completed_login_cookies(cookie_dict)
                    if need_harvest:
                        try:
                            harvested = await asyncio.wait_for(
                                self._harvest_login_cookies_after_verification(
                                    current_session, page, context
                                ),
                                timeout=20.0,
                            )
                        except Exception as e:
                            logger.debug(f"验证后收割超时/异常: {session_id}, {e}")
                            harvested = False
                        if harvested:
                            break
                except Exception as e:
                    logger.debug(f"验证页轻量探测异常: {session_id}, {e}")

                # probe 与 GuDong 一致：无完整 Cookie 时立即 False，不拖 /im
                if await self._probe_browser_login_success(current_session, page, context):
                    break

                # 若 CONFIRMED 时缓存了 login_token，顺带 API 换一次（不替代 browser 路径）
                if (
                    current_session.pending_login_token
                    and not current_session.cookies.get('unb')
                ):
                    try:
                        exchanged = await self._exchange_login_token(
                            current_session, current_session.pending_login_token
                        )
                        if self._mark_session_success(
                            current_session, exchanged, 'api', require_complete_cookies=True
                        ):
                            break
                    except Exception as e:
                        logger.debug(f"验证页期间 login_token 换取失败: {session_id}, {e}")

                await page.wait_for_timeout(2000)

        except asyncio.CancelledError:
            logger.info(f"扫码登录验证页面任务已取消: {session_id}")
            raise
        except Exception as e:
            logger.error(f"打开扫码登录验证页面失败: {session_id}, 错误: {e}")
            latest = self.sessions.get(session_id)
            if latest and latest.status == 'verification_required':
                # 仅 Playwright 完全挂掉时才允许 encode 末路兜底
                if not latest.screenshot_path:
                    self._encode_verification_url_as_qr(latest)
                latest.user_hint = (
                    f'打开服务端验证页失败: {e}。'
                    '请重新扫码登录；或粘贴成功后的回调网址（不要依赖链接兜底码自动登录）。'
                )
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

                    resp_data = (
                        resp.json()
                        .get("content", {})
                        .get("data", {})
                    ) if resp is not None else {}
                    if not isinstance(resp_data, dict):
                        resp_data = {}
                    qrcode_status = resp_data.get("qrCodeStatus")

                    if qrcode_status == "CONFIRMED":
                        # 登录确认
                        self._merge_session_cookies(session, resp.cookies)
                        # 缓存可能出现的 login_token，供风控后换 Cookie
                        pending_token = (
                            resp_data.get("token")
                            or resp_data.get("lgToken")
                            or resp_data.get("login_token")
                            or resp_data.get("loginToken")
                        )
                        if pending_token:
                            session.pending_login_token = str(pending_token)

                        if resp_data.get("iframeRedirect") is True:
                            # 账号被风控，需要手机验证
                            session.status = 'verification_required'
                            if not session.verification_entered_at:
                                session.verification_entered_at = time.time()
                            iframe_url = resp_data.get("iframeRedirectUrl")
                            session.verification_url = iframe_url
                            session.expire_time = max(session.expire_time, 600)
                            self._ensure_verification_task(session)
                            logger.warning(
                                f"账号被风控，需要手机验证: {session_id}, URL: {iframe_url}, "
                                f"has_login_token={bool(session.pending_login_token)}"
                            )
                            # 主扫码轮询到此结束：继续 query.do 只会 EXPIRED 刷日志，
                            # 且无助于收口；验证收口交给 verification_task。
                            break
                        else:
                            # 登录成功（无风控）
                            if self._mark_session_success(session, resp.cookies, 'api'):
                                break
                            # Cookie 不足时再试 login_token
                            if session.pending_login_token:
                                try:
                                    exchanged = await self._exchange_login_token(
                                        session, session.pending_login_token
                                    )
                                    if self._mark_session_success(
                                        session, exchanged, 'api', require_complete_cookies=True
                                    ):
                                        break
                                except Exception as e:
                                    logger.warning(f"CONFIRMED 后 login_token 换取失败: {session_id}, {e}")
                            logger.warning(f"扫码登录API返回成功状态，但关键Cookie不足: {session_id}")

                    elif qrcode_status == "NEW":
                        # 二维码未被扫描，继续轮询
                        continue

                    elif qrcode_status == "EXPIRED":
                        # 二维码已过期
                        if session.status == 'verification_required':
                            # 已进入验证流程：主轮询应已退出；若仍走到这里直接交给验证任务
                            logger.info(
                                f"二维码已过期，验证流程由 verification_task 接管: {session_id}"
                            )
                            break
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
                            break
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
            # 一次性 havana_iv_token 一旦被服务端 context 打开就永久绑定该会话，
            # 之后把 URL 交给前端只会让用户另开会话烧掉令牌（与 encode 同源毒药）。
            # 因此：服务端页开过 → 永远不再暴露（keep-alive 结束后也不放行）；
            # 仅 encode 兜底（Playwright 整体挂了、服务端从未持有会话）才给 URL。
            task = session.verification_task
            launching = bool(task and not task.done())
            may_expose_url = bool(session.verification_qr_encoded) or (
                not session.verification_page_opened and not launching
            )
            result['verification_url'] = (
                session.verification_url if may_expose_url else None
            )
            result['screenshot_path'] = session.screenshot_path
            result['verification_qr_encoded'] = bool(session.verification_qr_encoded)
            result['verification_ended_elsewhere'] = bool(session.verification_ended_elsewhere)
            result['accept_user_cookies'] = True
            result['accept_user_url'] = True
            if session.user_hint:
                result['message'] = session.user_hint
            elif session.verification_ended_elsewhere:
                result['message'] = (
                    '服务端验证页已结束。若扫的是服务端截图码，请保持弹窗等待自动收口；'
                    '仅当长时间无结果时再粘贴回调网址或完整 Cookie'
                )
            elif session.verification_qr_encoded:
                result['message'] = (
                    '当前图为链接兜底码（非服务端会话）。扫它不会自动登录；'
                    '请提交回调网址，或重新扫码等待服务端截图。'
                )
            else:
                result['message'] = (
                    '账号被风控：请用手机闲鱼 APP 扫描下方「服务端验证页」二维码；'
                    '保持弹窗打开，系统会自动收口，一般无需粘贴 Cookie'
                ) if session.screenshot_path else '账号被风控，正在准备服务端验证页截图…'

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
