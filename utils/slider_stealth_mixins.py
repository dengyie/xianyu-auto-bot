"""XianyuSliderStealth 的密码登录与隐身注入 Mixin（自 xianyu_slider_stealth.py 拆出）。

方法经 self/cls 操作宿主实例；宿主模块的剩余模块级符号（单例、常量、函数）
通过 `_host` 代理在调用时解析 —— 兼容运行期替换，且无导入环。
"""
import asyncio
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

import numpy as np
from loguru import logger


class _HostProxy:
    """属性访问转发到 utils.xianyu_slider_stealth 模块级符号（调用时解析）。"""

    def __getattr__(self, name):
        import utils.xianyu_slider_stealth as _m

        return getattr(_m, name)


_host = _HostProxy()


class PasswordLoginMixin:
    """密码登录（Playwright/Headful）全流程与登录态探测。"""

    def login_with_password_playwright(self, account: str, password: str, show_browser: bool = False,
                                      notification_callback: Optional[Callable] = None,
                                      force_clean_context: bool = False) -> dict:
        """使用Playwright进行密码登录（新方法，替代DrissionPage）
        
        Args:
            account: 登录账号（必填）
            password: 登录密码（必填）
            show_browser: 是否显示浏览器窗口（默认False为无头模式）
            notification_callback: 可选的通知回调函数，用于发送二维码/人脸验证通知（接受错误消息字符串作为参数）
            force_clean_context: 是否强制使用干净的临时浏览器上下文
        
        Returns:
            dict: Cookie字典，失败返回None
        """
        try:
            self.last_login_error = ""
            previous_slider_refresh_mode = getattr(self, '_slider_refresh_mode', False)
            self._slider_refresh_mode = force_clean_context
            previous_risk_trigger_scene = getattr(self, 'risk_trigger_scene', None)
            inferred_risk_trigger_scene = 'manual_password_refresh' if force_clean_context else 'password_login'
            if not previous_risk_trigger_scene:
                self.risk_trigger_scene = inferred_risk_trigger_scene
                logger.info(f"【{self.pure_user_id}】密码登录流程自动补齐 risk_trigger_scene={self.risk_trigger_scene}")
            else:
                logger.info(f"【{self.pure_user_id}】密码登录流程沿用 risk_trigger_scene={previous_risk_trigger_scene}")
            self._password_slider_runtime_hardened = False

            # 检查日期有效性
            if not self._check_date_validity():
                logger.error(f"【{self.pure_user_id}】日期验证失败，无法执行登录")
                return self._fail_login("日期验证失败，无法执行登录")

            if not self.browser_channel and not self.executable_path:
                self._ensure_project_playwright_browser()
            
            # 验证必需参数
            if not account or not password:
                logger.error(f"【{self.pure_user_id}】账号或密码不能为空")
                return self._fail_login("账号或密码不能为空")
            
            browser_mode = "有头" if show_browser else "无头"
            notification_scene = "手动刷新Cookie" if force_clean_context else "账号密码登录"
            logger.info(f"【{self.pure_user_id}】开始{browser_mode}模式密码登录流程（使用Playwright）...")
            logger.info(f"【{self.pure_user_id}】账号: {account}")
            logger.info("=" * 60)
            
            import os
            if force_clean_context:
                logger.warning(f"【{self.pure_user_id}】刷新模式启用干净上下文，不复用历史浏览器会话")
            else:
                user_data_dir = os.path.join(os.getcwd(), 'browser_data', f'user_{self.pure_user_id}')
                os.makedirs(user_data_dir, exist_ok=True)
                logger.info(f"【{self.pure_user_id}】使用用户数据目录: {user_data_dir}")
            
            # 在启动Playwright之前，重新检查和设置浏览器路径
            # 确保使用正确的浏览器版本（避免版本不匹配问题）
            import sys
            from pathlib import Path
            if getattr(_host.sys, 'frozen', False):
                # 如果是打包后的exe，检查exe同目录下的浏览器
                exe_dir = Path(_host.sys.executable).parent
                playwright_dir = exe_dir / 'playwright'

                if playwright_dir.exists():
                    chromium_dirs = list(playwright_dir.glob('chromium-*'))
                    # 找到第一个完整的浏览器目录
                    for chromium_dir in chromium_dirs:
                        chrome_exe = chromium_dir / 'chrome-win' / 'chrome.exe'
                        if chrome_exe.exists() and chrome_exe.stat().st_size > 0:
                            # 清除旧的环境变量，使用实际存在的浏览器
                            if 'PLAYWRIGHT_BROWSERS_PATH' in os.environ:
                                old_path = os.environ['PLAYWRIGHT_BROWSERS_PATH']
                                if old_path != str(playwright_dir):
                                    logger.info(f"【{self.pure_user_id}】清除旧的环境变量: {old_path}")
                                    del os.environ['PLAYWRIGHT_BROWSERS_PATH']
                            # 设置正确的环境变量
                            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(playwright_dir)
                            logger.info(f"【{self.pure_user_id}】已设置PLAYWRIGHT_BROWSERS_PATH: {playwright_dir}")
                            logger.info(f"【{self.pure_user_id}】使用浏览器版本: {chromium_dir.name}")
                            break

            # 🔧 关键修复：复用完整浏览器画像，与 captcha 验证流程保持一致
            browser_features = self._get_random_browser_features()
            self.browser_features = browser_features
            self.profile_id = browser_features.get("profile_id", "unknown")
            logger.info(f"【{self.pure_user_id}】密码登录使用浏览器画像: {self.profile_id}, "
                       f"viewport: {browser_features['viewport_width']}x{browser_features['viewport_height']}, "
                       f"scale: {browser_features['device_scale_factor']}")

            # 设置浏览器启动参数（保持原始参数，之前有头模式正常工作）
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
                '--lang=zh-CN',
                '--disable-infobars',
                '--disable-extensions',
                '--disable-popup-blocking',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
            ]

            # 启动浏览器
            if not self.browser_channel and not self.executable_path:
                self._ensure_project_playwright_browser()

            playwright_factory = self._get_sync_playwright_factory()
            playwright = playwright_factory().start()
            self._playwright_thread_id = _host.threading.get_ident()
            browser = None
            used_profile_lock_fallback = False
            launch_options: Dict[str, Any] = {
                'headless': not show_browser,
                'ignore_default_args': ['--enable-automation'],
                'args': browser_args,
            }
            proxy_settings = self._build_playwright_proxy_settings()
            if proxy_settings:
                launch_options['proxy'] = proxy_settings
                logger.info(f"【{self.pure_user_id}】密码登录浏览器启用代理: {proxy_settings['server']}")
            if self.browser_channel:
                launch_options['channel'] = self.browser_channel
            if self.executable_path:
                launch_options['executable_path'] = self.executable_path
            if force_clean_context:
                browser, context = self._launch_clean_cookie_seeded_context(
                    playwright,
                    launch_options,
                    browser_features,
                )
            else:
                try:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir,
                        **launch_options,
                        viewport={'width': browser_features['viewport_width'], 'height': browser_features['viewport_height']},
                        user_agent=browser_features['user_agent'],
                        locale=browser_features['locale'],
                        accept_downloads=True,
                        ignore_https_errors=True,
                        extra_http_headers={
                            'Accept-Language': browser_features['accept_lang']
                        }
                    )
                except Exception as persistent_launch_error:
                    if not self._is_profile_in_use_launch_error(persistent_launch_error):
                        raise
                    used_profile_lock_fallback = True
                    logger.warning(
                        f"【{self.pure_user_id}】持久化浏览器目录被其他 Chromium 进程占用，"
                        f"自动切换到干净上下文兜底登录: {persistent_launch_error}"
                    )
                    browser, context = self._launch_clean_cookie_seeded_context(
                        playwright,
                        launch_options,
                        browser_features,
                    )
            effective_clean_context = force_clean_context or used_profile_lock_fallback
            logger.info(f"【{self.pure_user_id}】已设置浏览器语言为中文（zh-CN）")

            if not browser:
                browser = context.browser
            self._browser_pid = self._extract_browser_pid(browser or context, playwright)
            page = context.new_page()
            self._apply_headless_network_fingerprint(page, browser_features)
            observed_set_cookie_updates: Dict[str, str] = {}

            def _capture_response_set_cookie(response):
                try:
                    updates = self._extract_set_cookie_updates_from_playwright_response(response)
                    if not updates:
                        return
                    interesting_keys = [
                        key for key in ('havana_lgc2_77', 'x5secdata', 'x5sec', '_m_h5_tk', '_m_h5_tk_enc', 'sgcookie')
                        if key in updates
                    ]
                    for key, value in updates.items():
                        observed_set_cookie_updates[key] = value
                    if interesting_keys:
                        summary = ', '.join(
                            f"{key}(长度:{len(str(observed_set_cookie_updates.get(key) or ''))})"
                            for key in interesting_keys
                        )
                        logger.info(
                            f"【{self.pure_user_id}】登录网络响应捕获到Set-Cookie: {summary} | URL: {getattr(response, 'url', '')}"
                        )
                except Exception as capture_e:
                    logger.debug(f"【{self.pure_user_id}】捕获登录响应Set-Cookie失败: {capture_e}")

            try:
                context.on("response", _capture_response_set_cookie)
            except Exception as listener_e:
                logger.warning(f"【{self.pure_user_id}】注册登录响应监听失败（不影响主流程）: {listener_e}")

            # 有头模式使用轻量反检测脚本（完整脚本会覆盖 document.fonts / EventTarget /
            # Performance.now / Date 等浏览器核心 API，导致页面白屏无法渲染）；
            # 无头模式使用完整脚本以通过自动化检测。
            if show_browser:
                stealth_js = """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
                window.chrome = { runtime: {} };
                """
                page.add_init_script(stealth_js)
            else:
                password_login_stealth_mode = None
                if not show_browser and not self.stealth_mode_override:
                    password_login_stealth_mode = "lite"
                    logger.info(f"【{self.pure_user_id}】密码登录链路默认使用 lite 反检测脚本，避免无头登录页白屏")
                self._install_stealth_init_script(page, browser_features, mode_override=password_login_stealth_mode)

            logger.info(f"【{self.pure_user_id}】浏览器已成功启动（{browser_mode}模式，画像: {self.profile_id}）")

            try:
                # 预访问：先访问闲鱼首页建立正常浏览历史（降低空白浏览器的风控风险）
                try:
                    logger.info(f"【{self.pure_user_id}】预访问闲鱼首页，建立浏览历史...")
                    page.goto("https://www.goofish.com", wait_until='domcontentloaded', timeout=15000)
                    time.sleep(random.uniform(1.0, 2.0))
                    logger.info(f"【{self.pure_user_id}】预访问完成，当前URL: {page.url}")
                except Exception as warmup_e:
                    logger.warning(f"【{self.pure_user_id}】预访问失败（不影响登录）: {warmup_e}")

                # 访问登录页面（带重试逻辑）
                login_url = "https://www.goofish.com/im"
                logger.info(f"【{self.pure_user_id}】访问登录页面: {login_url}")

                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        page.goto(login_url, wait_until='networkidle', timeout=60000)
                        break
                    except Exception as e:
                        error_msg = str(_host.e)
                        if any(err in error_msg for err in ['ERR_CONNECTION_CLOSED', 'ERR_CONNECTION_RESET', 'ERR_CONNECTION_REFUSED']):
                            if attempt < max_retries - 1:
                                wait_time = 2 * (attempt + 1)
                                logger.warning(f"【{self.pure_user_id}】连接被关闭，{wait_time}秒后第{attempt+2}次重试...")
                                time.sleep(wait_time)
                                continue
                        raise
                
                # 等待页面加载
                wait_time = 2 if not show_browser else 2
                logger.info(f"【{self.pure_user_id}】等待页面加载（{wait_time}秒）...")
                time.sleep(wait_time)
                
                # 页面诊断信息
                logger.info(f"【{self.pure_user_id}】========== 页面诊断信息 ==========")
                logger.info(f"【{self.pure_user_id}】当前URL: {page.url}")
                logger.info(f"【{self.pure_user_id}】页面标题: {page.title()}")
                logger.info(f"【{self.pure_user_id}】=====================================")
                
                # 【步骤1】查找登录frame（闲鱼登录通常在iframe中）
                logger.info(f"【{self.pure_user_id}】查找登录frame...")
                login_selectors = self._get_password_login_selectors()
                
                # 等待页面和iframe加载完成
                logger.info(f"【{self.pure_user_id}】等待页面和iframe加载...")
                time.sleep(1)
                login_frame, found_login_form, matched_selector = self._find_login_form_with_retry(
                    page,
                    timeout_seconds=8.0,
                    poll_interval=1.0,
                )
                iframes = page.query_selector_all('iframe')
                logger.info(f"【{self.pure_user_id}】当前检测到 {len(iframes)} 个 iframe")
                
                # 【情况1】找到frame且找到登录表单 → 正常登录流程
                if found_login_form:
                    logger.info(f"【{self.pure_user_id}】找到登录表单（{matched_selector}），开始正常登录流程...")
                
                # 【情况2】找到frame但未找到登录表单 → 可能已登录，直接检测滑块
                elif len(iframes) > 0:
                    logger.warning(f"【{self.pure_user_id}】找到iframe但未找到登录表单，可能已登录，检测滑块...")
                    
                    # 先将page和context保存到实例变量（供solve_slider使用）
                    original_page = self.page
                    original_context = self.context
                    original_browser = self.browser
                    original_playwright = self.playwright
                    
                    self.page = page
                    self.context = context
                    self.browser = browser
                    self.playwright = playwright
                    
                    try:
                        monitor_page = self._select_monitor_page(context, page)

                        has_error, error_message = self._check_login_error(monitor_page)
                        if has_error:
                            logger.error(f"【{self.pure_user_id}】❌ 登录失败：{error_message}")
                            raise Exception(error_message if error_message else "登录失败，请检查账号密码是否正确")

                        clicked_direct_enter, direct_enter_page = self._click_direct_enter_if_present(monitor_page, context)
                        if clicked_direct_enter:
                            login_success, active_page, _ = self._probe_context_login_success(context, direct_enter_page or monitor_page)
                            if login_success:
                                logger.success(f"【{self.pure_user_id}】✅ 普通登录页快速进入后登录成功")
                                return self._finalize_logged_in_cookies(
                                    context,
                                    active_page or direct_enter_page or monitor_page,
                                    scene="普通登录页快速进入后登录成功",
                                    notification_callback=notification_callback,
                                    notification_scene=notification_scene,
                                )
                            monitor_page = self._select_monitor_page(context, direct_enter_page or monitor_page)

                        has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                        if has_qr:
                            logger.warning(f"【{self.pure_user_id}】检测到前置身份验证，直接进入验证等待流程")
                            return self._process_verification_requirement(
                                context,
                                monitor_page,
                                qr_frame,
                                notification_callback,
                                notification_scene,
                            )

                        # 检测滑块元素（在主页面和所有frame中查找）
                        slider_selectors = [
                            '#nc_1_n1z',
                            '.nc-container',
                            '.nc_scale',
                            '.nc-wrapper'
                        ]
                        
                        has_slider = False
                        detected_slider_frame = None
                        
                        # 先在主页面查找
                        for selector in slider_selectors:
                            try:
                                element = page.query_selector(selector)
                                if element and element.is_visible():
                                    logger.info(f"【{self.pure_user_id}】✅ 在主页面检测到滑块验证元素: {selector}")
                                    has_slider = True
                                    detected_slider_frame = None  # None表示主页面
                                    break
                            except:
                                continue
                        
                        # 如果主页面没找到，在所有frame中查找
                        if not has_slider:
                            for idx, iframe in enumerate(iframes):
                                try:
                                    frame = iframe.content_frame()
                                    if frame:
                                        # 等待frame内容加载
                                        try:
                                            frame.wait_for_load_state('domcontentloaded', timeout=2000)
                                        except:
                                            pass
                                        
                                        for selector in slider_selectors:
                                            try:
                                                element = frame.query_selector(selector)
                                                if element and element.is_visible():
                                                    logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到滑块验证元素: {selector}")
                                                    has_slider = True
                                                    detected_slider_frame = frame
                                                    break
                                            except:
                                                continue
                                        
                                        if has_slider:
                                            break
                                except Exception as e:
                                    logger.debug(f"【{self.pure_user_id}】检查Frame {idx}时出错: {_host.e}")
                                    continue
                        
                        if has_slider:
                            # 设置检测到的frame，供solve_slider使用
                            self._detected_slider_frame = detected_slider_frame
                            if effective_clean_context:
                                logger.info(f"【{self.pure_user_id}】干净上下文检测到前置风控滑块，尝试自动处理...")

                            logger.warning(f"【{self.pure_user_id}】检测到滑块验证，开始处理...")
                            slider_risk_log = self._start_password_login_slider_risk_log(
                                verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                detection_phase='pre_login_monitor',
                            )
                            time.sleep(3)
                            slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                            
                            if not slider_success:
                                feedback = self.last_verification_feedback or {}
                                if feedback.get("source") == "slider_missing":
                                    logger.error(f"【{self.pure_user_id}】❌ 滑块流程结束后页面已不再包含滑块，停止额外刷新重试")
                                    self._finish_password_login_slider_risk_log(
                                        slider_risk_log,
                                        success=False,
                                        verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                        error_message=self._get_slider_failure_message("页面状态已变化，未找到滑块容器，请重新尝试刷新Cookie"),
                                        extra_meta={'detection_source': 'login_with_password_playwright_pre_login'},
                                    )
                                    return self._fail_login(self._get_slider_failure_message("页面状态已变化，未找到滑块容器，请重新尝试刷新Cookie"))

                                # 常规重试仍失败后，刷新页面再补一次机会。
                                logger.warning(
                                    f"【{self.pure_user_id}】⚠️ 滑块处理{self.slider_max_retries}次仍失败，刷新页面后重试..."
                                )
                                try:
                                    page.reload(wait_until="domcontentloaded", timeout=30000)
                                    logger.info(f"【{self.pure_user_id}】✅ 页面刷新完成")
                                    time.sleep(2)
                                    slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                                    if not slider_success:
                                        feedback = self.last_verification_feedback or {}
                                        if feedback.get("source") == "slider_missing":
                                            logger.error(f"【{self.pure_user_id}】❌ 刷新后页面未出现滑块，停止重复尝试")
                                        logger.error(f"【{self.pure_user_id}】❌ 刷新后滑块验证仍然失败")
                                        self._finish_password_login_slider_risk_log(
                                            slider_risk_log,
                                            success=False,
                                            verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                            error_message=self._get_slider_failure_message("滑块验证失败，请稍后重试"),
                                            extra_meta={'detection_source': 'login_with_password_playwright_pre_login'},
                                        )
                                        return self._fail_login(self._get_slider_failure_message("滑块验证失败，请稍后重试"))
                                    else:
                                        logger.success(f"【{self.pure_user_id}】✅ 刷新后滑块验证成功！")
                                except Exception as e:
                                    logger.error(f"【{self.pure_user_id}】❌ 页面刷新失败: {_host.e}")
                                    self._finish_password_login_slider_risk_log(
                                        slider_risk_log,
                                        success=False,
                                        verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                        error_message=f"页面会话已失效: {str(_host.e)}",
                                        extra_meta={'detection_source': 'login_with_password_playwright_pre_login'},
                                    )
                                    return self._fail_login("页面会话已失效，请重新尝试刷新Cookie")
                            else:
                                logger.success(f"【{self.pure_user_id}】✅ 滑块验证成功！")
                            self._finish_password_login_slider_risk_log(
                                slider_risk_log,
                                success=True,
                                verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                processing_result='密码登录流程中的滑块验证自动处理成功',
                                extra_meta={'detection_source': 'login_with_password_playwright_pre_login'},
                            )
                            
                            # 等待页面加载和状态更新（第一次等待3秒）
                            logger.info(f"【{self.pure_user_id}】等待3秒，让页面加载完成...")
                            time.sleep(3)
                            
                            # 第一次检查登录状态
                            login_success, active_page, _ = self._probe_context_login_success(context, page)
                            
                            # 如果第一次没检测到，再等待5秒后重试
                            if not login_success:
                                logger.info(f"【{self.pure_user_id}】第一次检测未发现登录状态，等待5秒后重试...")
                                time.sleep(5)
                                login_success, active_page, _ = self._probe_context_login_success(context, active_page or page)
                            
                            if login_success:
                                logger.success(f"【{self.pure_user_id}】✅ 滑块验证后登录成功")
                                return self._finalize_logged_in_cookies(
                                    context,
                                    active_page or page,
                                    scene="滑块验证后登录成功",
                                    notification_callback=notification_callback,
                                    notification_scene=notification_scene,
                                )
                            else:
                                # 滑块验证后登录状态不明确，检测是否需要人脸/短信/二维码验证
                                logger.warning(f"【{self.pure_user_id}】⚠️ 滑块验证后登录状态不明确，检测是否需要身份验证...")
                                time.sleep(1)
                                monitor_page = self._select_monitor_page(context, page)
                                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)

                                if has_qr:
                                    return self._process_verification_requirement(
                                        context,
                                        monitor_page,
                                        qr_frame,
                                        notification_callback,
                                        notification_scene,
                                    )
                                else:
                                    logger.warning(f"【{self.pure_user_id}】⚠️ 未检测到身份验证，登录状态不明确")
                                    return self._fail_login("滑块验证后登录状态未确认，请稍后重试")
                        else:
                            logger.info(f"【{self.pure_user_id}】未检测到滑块验证")

                            # 未检测到滑块时，检查是否已登录
                            login_success, active_page, _ = self._probe_context_login_success(context, page)
                            if login_success:
                                logger.success(f"【{self.pure_user_id}】✅ 检测到已登录状态")
                                return self._finalize_logged_in_cookies(
                                    context,
                                    active_page or page,
                                    scene="无滑块已登录场景",
                                    notification_callback=notification_callback,
                                    notification_scene=notification_scene,
                                )
                            else:
                                monitor_page = self._select_monitor_page(context, active_page or page)
                                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                                if has_qr:
                                    return self._process_verification_requirement(
                                        context,
                                        monitor_page,
                                        qr_frame,
                                        notification_callback,
                                        notification_scene,
                                    )
                                logger.warning(f"【{self.pure_user_id}】⚠️ 未检测到滑块且未登录，不获取Cookie")
                                return self._fail_login("未检测到登录表单或有效登录态")
                    
                    finally:
                        # 恢复原始值
                        self.page = original_page
                        self.context = original_context
                        self.browser = original_browser
                        self.playwright = original_playwright
                
                # 【情况3】未找到frame → 检查是否已登录
                else:
                    logger.warning(f"【{self.pure_user_id}】未找到任何iframe，检查是否已登录...")
                    
                    # 等待一下让页面完全加载
                    time.sleep(2)
                    
                    # 检查是否已登录（只有过了滑块才会有这个元素）
                    login_success, active_page, _ = self._probe_context_login_success(context, page)
                    if login_success:
                        logger.success(f"【{self.pure_user_id}】✅ 检测到已登录状态")

                        # 🔧 刷新模式下验证 session 是否真的有效
                        # 注入旧 Cookie 可能让前端显示"已登录"，但服务端 session 已过期
                        if effective_clean_context:
                            logger.info(f"【{self.pure_user_id}】刷新模式：验证服务端Session是否有效...")
                            try:
                                verify_page = context.new_page()
                                verify_resp = verify_page.goto(
                                    "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/?jsv=2.7.2&appKey=34839810&type=originaljson&dataType=json&v=1.0&api=mtop.taobao.idlemessage.pc.login.token&sessionOption=AutoLoginOnly",
                                    wait_until="domcontentloaded",
                                    timeout=10000
                                )
                                verify_text = verify_page.content()
                                verify_page.close()

                                if "FAIL_SYS_SESSION_EXPIRED" in verify_text or "FAIL_SYS_USER_VALIDATE" in verify_text:
                                    logger.warning(
                                        f"【{self.pure_user_id}】服务端Session已过期，"
                                        f"前端登录状态为假象，需要重新账密登录"
                                    )
                                    page, login_frame, found_login_form, matched_selector, reopened_fresh_page = (
                                        self._prepare_login_page_after_cleanup(
                                            context,
                                            page,
                                            clear_storage=True,
                                            reopen_fresh_page=True,
                                            timeout_seconds=8.0,
                                        )
                                    )
                                    if not found_login_form:
                                        logger.error(f"【{self.pure_user_id}】清理会话状态后仍未找到登录表单")
                                        return self._fail_login("Session过期且清理会话状态后未找到登录表单")
                                    if reopened_fresh_page:
                                        logger.info(f"【{self.pure_user_id}】已切换到新页面继续账密登录")
                                    # 跳出当前分支，继续走下面的账密输入流程
                                else:
                                    logger.info(f"【{self.pure_user_id}】✅ 服务端Session验证通过，Cookie有效")
                                    return self._finalize_logged_in_cookies(
                                        context,
                                        active_page or page,
                                        scene="无 iframe 已登录场景(Session已验证)",
                                        notification_callback=notification_callback,
                                        notification_scene=notification_scene,
                                    )
                            except Exception as verify_e:
                                logger.warning(f"【{self.pure_user_id}】Session验证异常: {verify_e}，按Session过期处理")
                                page, login_frame, found_login_form, matched_selector, reopened_fresh_page = (
                                    self._prepare_login_page_after_cleanup(
                                        context,
                                        page,
                                        clear_storage=True,
                                        reopen_fresh_page=True,
                                        timeout_seconds=8.0,
                                    )
                                )
                                if not found_login_form:
                                    return self._fail_login("Session验证异常且清理会话状态后未找到登录表单")
                                if reopened_fresh_page:
                                    logger.info(f"【{self.pure_user_id}】Session异常后已切换到新页面继续账密登录")
                        else:
                            # 非刷新模式，直接返回Cookie
                            return self._finalize_logged_in_cookies(
                                context,
                                active_page or page,
                                scene="无 iframe 已登录场景",
                                notification_callback=notification_callback,
                                notification_scene=notification_scene,
                            )
                    else:
                        # 持久化上下文可能因浏览器缓存导致页面处于"半登录"状态
                        # 既没有登录 iframe，也没有已登录元素
                        if not effective_clean_context:
                            logger.warning(
                                f"【{self.pure_user_id}】持久化上下文页面状态异常（无iframe、无已登录态），"
                                f"清除Cookie和缓存后重新加载..."
                            )
                            page, login_frame, found_login_form, matched_selector, _ = (
                                self._prepare_login_page_after_cleanup(
                                    context,
                                    page,
                                    clear_storage=True,
                                    reopen_fresh_page=False,
                                    timeout_seconds=8.0,
                                )
                            )

                            if not found_login_form:
                                logger.error(f"【{self.pure_user_id}】❌ 清除缓存后仍未找到登录表单")
                                return self._fail_login("持久化上下文清除缓存后仍未找到登录表单")
                            logger.info(f"【{self.pure_user_id}】✓ 清除缓存后找到登录表单: {matched_selector}")
                            # found_login_form=True → 继续走下面的账密输入流程
                        else:
                            logger.error(f"【{self.pure_user_id}】❌ 未找到登录表单且未检测到已登录")
                            return self._fail_login("未找到登录表单且未检测到已登录状态")
                
                # 点击密码登录标签
                logger.info(f"【{self.pure_user_id}】查找密码登录标签...")
                try:
                    password_tab, password_tab_selector = self._query_first_visible(
                        login_frame,
                        login_selectors['tab'],
                    )
                    if password_tab:
                        logger.info(f"【{self.pure_user_id}】✓ 找到密码登录标签，点击中: {password_tab_selector}")
                        password_tab.click()
                        time.sleep(1.5)
                    else:
                        logger.info(f"【{self.pure_user_id}】未找到密码登录标签，可能默认已处于密码登录模式")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】查找密码登录标签失败: {_host.e}")
                
                # 输入账号
                logger.info(f"【{self.pure_user_id}】输入账号: {account}")
                time.sleep(1)
                
                account_input, account_selector = self._query_first_visible(
                    login_frame,
                    login_selectors['account'],
                )
                if account_input:
                    logger.info(f"【{self.pure_user_id}】✓ 找到账号输入框: {account_selector}")
                    account_input.fill(account)
                    logger.info(f"【{self.pure_user_id}】✓ 账号已输入")
                    time.sleep(random.uniform(0.5, 1.0))
                else:
                    handled, recovery_result = self._recover_from_missing_login_inputs(
                        context,
                        page,
                        missing_field='账号输入框',
                        notification_callback=notification_callback,
                        notification_scene=notification_scene,
                    )
                    if handled:
                        return recovery_result
                    logger.error(f"【{self.pure_user_id}】✗ 未找到账号输入框")
                    return self._fail_login("未找到账号输入框")
                
                # 输入密码
                logger.info(f"【{self.pure_user_id}】输入密码...")
                password_input, password_selector = self._query_first_visible(
                    login_frame,
                    login_selectors['password'],
                )
                if password_input:
                    logger.info(f"【{self.pure_user_id}】✓ 找到密码输入框: {password_selector}")
                    password_input.fill(password)
                    logger.info(f"【{self.pure_user_id}】✓ 密码已输入")
                    time.sleep(random.uniform(0.5, 1.0))
                else:
                    handled, recovery_result = self._recover_from_missing_login_inputs(
                        context,
                        page,
                        missing_field='密码输入框',
                        notification_callback=notification_callback,
                        notification_scene=notification_scene,
                    )
                    if handled:
                        return recovery_result
                    logger.error(f"【{self.pure_user_id}】✗ 未找到密码输入框")
                    return self._fail_login("未找到密码输入框")
                
                # 勾选用户协议
                logger.info(f"【{self.pure_user_id}】查找并勾选用户协议...")
                try:
                    agreement_checkbox, agreement_selector = self._query_first_visible(
                        login_frame,
                        login_selectors['agreement'],
                    )
                    if agreement_checkbox:
                        is_checked = agreement_checkbox.evaluate('el => el.checked')
                        if not is_checked:
                            agreement_checkbox.click()
                            time.sleep(0.3)
                            logger.info(f"【{self.pure_user_id}】✓ 用户协议已勾选: {agreement_selector}")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】勾选用户协议失败: {_host.e}")
                
                # 点击登录按钮
                logger.info(f"【{self.pure_user_id}】点击登录按钮...")
                time.sleep(1)
                
                login_button, login_button_selector = self._query_first_visible(
                    login_frame,
                    login_selectors['submit'],
                )
                if login_button:
                    logger.info(f"【{self.pure_user_id}】✓ 找到登录按钮: {login_button_selector}")
                    login_button.click()
                    logger.info(f"【{self.pure_user_id}】✓ 登录按钮已点击")
                else:
                    logger.warning(f"【{self.pure_user_id}】未找到登录按钮，尝试回车提交")
                    try:
                        password_input.press('Enter')
                        logger.info(f"【{self.pure_user_id}】✓ 已通过回车提交登录")
                    except Exception:
                        logger.error(f"【{self.pure_user_id}】✗ 未找到登录按钮且回车提交失败")
                        return self._fail_login("未找到登录按钮")
                
                # 【关键】点击登录后，等待一下再检测滑块
                logger.info(f"【{self.pure_user_id}】========== 登录后监控 ==========")
                logger.info(f"【{self.pure_user_id}】等待页面响应...")
                time.sleep(3)
                
                # 【核心】检测是否有滑块验证 → 如果有，调用 solve_slider() 处理
                logger.info(f"【{self.pure_user_id}】检测是否有滑块验证...")
                
                # 先将page和context保存到实例变量（供solve_slider使用）
                original_page = self.page
                original_context = self.context
                original_browser = self.browser
                original_playwright = self.playwright
                
                self.page = page
                self.context = context
                self.browser = browser
                self.playwright = playwright
                
                try:
                    # 检查页面内容是否包含滑块相关元素
                    page_content = page.content()
                    has_slider = False

                    # 检测滑块元素
                    slider_selectors = [
                        '#nc_1_n1z',
                        '.nc-container',
                        '.nc_scale',
                        '.nc-wrapper'
                    ]

                    # 在主页面和所有 iframe 中查找滑块（阿里系滑块常嵌在 iframe 中）
                    search_frames = [page]
                    try:
                        for frame in page.frames:
                            if frame != page.main_frame:
                                search_frames.append(frame)
                    except Exception:
                        pass

                    for search_frame in search_frames:
                        if has_slider:
                            break
                        for selector in slider_selectors:
                            try:
                                element = search_frame.query_selector(selector)
                                if element and element.is_visible():
                                    logger.info(f"【{self.pure_user_id}】✅ 检测到滑块验证元素: {selector} (frame: {getattr(search_frame, 'url', 'main')[:80]})")
                                    has_slider = True
                                    break
                            except:
                                continue
                    
                    if has_slider:
                        logger.warning(f"【{self.pure_user_id}】检测到滑块验证，开始处理...")
                        slider_risk_log = self._start_password_login_slider_risk_log(
                            verification_url=(getattr(search_frame, 'url', None) if 'search_frame' in locals() else getattr(page, 'url', None)),
                            detection_phase='post_login_monitor',
                        )

                        # 【复用】直接调用 solve_slider() 方法处理滑块
                        slider_success = self.solve_slider(max_retries=self.slider_max_retries)

                        if slider_success:
                            logger.success(f"【{self.pure_user_id}】✅ 滑块验证成功！")
                            self._finish_password_login_slider_risk_log(
                                slider_risk_log,
                                success=True,
                                verification_url=(getattr(search_frame, 'url', None) if 'search_frame' in locals() else getattr(page, 'url', None)),
                                processing_result='密码登录流程中的滑块验证自动处理成功',
                                extra_meta={'detection_source': 'login_with_password_playwright_post_login'},
                            )
                        else:
                            logger.error(f"【{self.pure_user_id}】❌ 滑块验证{self.slider_max_retries}次均失败")
                            self._finish_password_login_slider_risk_log(
                                slider_risk_log,
                                success=False,
                                verification_url=(getattr(search_frame, 'url', None) if 'search_frame' in locals() else getattr(page, 'url', None)),
                                error_message=self._get_slider_failure_message("滑块验证失败，请稍后重试"),
                                extra_meta={'detection_source': 'login_with_password_playwright_post_login'},
                            )
                            fallback_page = locals().get('active_page') or page
                            monitor_page = self._select_monitor_page(context, fallback_page) or fallback_page
                            qr_handoff_frame = None
                            qr_markers = ('扫码', '扫一扫', '安全登录', '二维码')
                            qr_selectors = (
                                'img[src*=\"qrcode\"]',
                                'canvas[class*=\"qrcode\"]',
                                '.qr-code',
                                '#qr-code',
                                '[class*=\"qr-code\"]',
                                '[id*=\"qr-code\"]',
                            )
                            for qr_candidate in [monitor_page] + list(monitor_page.frames):
                                try:
                                    frame_text = qr_candidate.text_content('body') or ''
                                except Exception:
                                    frame_text = ''
                                marker_hit = any(marker in frame_text for marker in qr_markers)
                                selector_hit = False
                                for qr_selector in qr_selectors:
                                    try:
                                        qr_element = qr_candidate.query_selector(qr_selector)
                                        if qr_element and qr_element.is_visible():
                                            selector_hit = True
                                            break
                                    except Exception:
                                        continue
                                if marker_hit or selector_hit:
                                    qr_handoff_frame = qr_candidate
                                    break
                            if qr_handoff_frame is not None:
                                screenshot_path = self._capture_verification_screenshot(
                                    monitor_page,
                                    frame=(None if qr_handoff_frame == monitor_page else qr_handoff_frame),
                                )
                                qr_frame = _host.VerificationFrameWrapper(
                                    qr_handoff_frame,
                                    verification_type='qr_verify',
                                    verify_url=(
                                        qr_handoff_frame.url
                                        if hasattr(qr_handoff_frame, 'url')
                                        else getattr(monitor_page, 'url', None)
                                    ),
                                    screenshot_path=screenshot_path,
                                )
                                logger.warning(
                                    f"【{self.pure_user_id}】滑块多次失败后检测到可扫码验证，转为二维码验证接管"
                                )
                                self._finish_password_login_slider_risk_log(
                                    slider_risk_log,
                                    success=False,
                                    verification_url=(getattr(search_frame, 'url', None) if 'search_frame' in locals() else getattr(page, 'url', None)),
                                    processing_result='滑块失败后检测到可扫码验证，已转交二维码验证流程',
                                    extra_meta={'detection_source': 'login_with_password_playwright_post_login_qr_handoff'},
                                )
                                return self._process_verification_requirement(
                                    context,
                                    monitor_page,
                                    qr_frame,
                                    notification_callback,
                                    '账号密码登录',
                                )
                            return self._fail_login(self._get_slider_failure_message("滑块验证失败，请稍后重试"))
                    else:
                        logger.info(f"【{self.pure_user_id}】未检测到滑块验证")
                    
                    # 等待登录完成
                    logger.info(f"【{self.pure_user_id}】等待登录完成...")
                    time.sleep(5)
                    
                    # 再次检查是否有滑块验证（可能在等待过程中出现）
                    logger.info(f"【{self.pure_user_id}】等待1秒后检查是否有滑块验证...")
                    time.sleep(1)
                    has_slider_after_wait = False
                    for search_frame in search_frames:
                        if has_slider_after_wait:
                            break
                        for selector in slider_selectors:
                            try:
                                element = search_frame.query_selector(selector)
                                if element and element.is_visible():
                                    logger.info(f"【{self.pure_user_id}】✅ 等待后检测到滑块验证元素: {selector}")
                                    has_slider_after_wait = True
                                    break
                            except:
                                continue

                    active_page = locals().get('active_page') or page
                    if has_slider_after_wait:
                        logger.warning(f"【{self.pure_user_id}】检测到滑块验证，开始处理...")
                        wait_slider_risk_log = self._start_password_login_slider_risk_log(
                            verification_url=getattr(active_page or page, 'url', None),
                            detection_phase='post_wait_monitor',
                        )
                        slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                        if slider_success:
                            logger.success(f"【{self.pure_user_id}】✅ 滑块验证成功！")
                            self._finish_password_login_slider_risk_log(
                                wait_slider_risk_log,
                                success=True,
                                verification_url=getattr(active_page or page, 'url', None),
                                processing_result='密码登录流程中的滑块验证自动处理成功（等待后）',
                                extra_meta={'detection_source': 'login_with_password_playwright_post_wait'},
                            )
                            time.sleep(3)  # 等待滑块验证后的状态更新
                        else:
                            logger.error(f"【{self.pure_user_id}】❌ 滑块验证3次均失败")
                            self._finish_password_login_slider_risk_log(
                                wait_slider_risk_log,
                                success=False,
                                verification_url=getattr(active_page or page, 'url', None),
                                error_message=self._get_slider_failure_message("滑块验证失败，请稍后重试"),
                                extra_meta={'detection_source': 'login_with_password_playwright_post_wait'},
                            )
                            return self._fail_login(self._get_slider_failure_message("滑块验证失败，请稍后重试"))
                    
                    # 检查登录状态
                    logger.info(f"【{self.pure_user_id}】等待1秒后检查登录状态...")
                    time.sleep(1)
                    login_success, active_page, _ = self._probe_context_login_success(context, page)
                    
                    if login_success:
                        monitor_page = self._select_monitor_page(context, active_page or page)
                        has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                        if has_qr:
                            logger.warning(f"【{self.pure_user_id}】虽然页面元素判定已登录，但当前仍存在身份验证页，转入验证等待流程")
                            return self._process_verification_requirement(
                                context,
                                monitor_page,
                                qr_frame,
                                notification_callback,
                                notification_scene,
                            )
                        logger.success(f"【{self.pure_user_id}】✅ 登录验证成功！")
                    else:
                        # 检查是否有账密错误
                        logger.info(f"【{self.pure_user_id}】等待1秒后检查是否有账密错误...")
                        time.sleep(1)
                        monitor_page = self._select_monitor_page(context, active_page or page)
                        has_error, error_message = self._check_login_error(monitor_page)
                        if has_error:
                            logger.error(f"【{self.pure_user_id}】❌ 登录失败：{error_message}")
                            # 抛出异常，包含错误消息，让调用者能够获取
                            raise Exception(error_message if error_message else "登录失败，请检查账号密码是否正确")
                        
                        # 【重要】检测是否需要二维码/人脸验证（排除滑块验证）
                        # 注意：_detect_qr_code_verification 如果检测到滑块，会立即处理滑块
                        logger.info(f"【{self.pure_user_id}】等待1秒后检测是否需要二维码/人脸验证...")
                        time.sleep(1)
                        logger.info(f"【{self.pure_user_id}】检测是否需要二维码/人脸验证...")
                        monitor_page = self._select_monitor_page(context, active_page or page)
                        has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                        
                        # 如果检测到滑块并已处理，再次检查登录状态
                        if not has_qr:
                            # 滑块可能已被处理，再次检查登录状态
                            logger.info(f"【{self.pure_user_id}】等待1秒后再次检查登录状态...")
                            time.sleep(1)
                            login_success_after_slider, active_page, _ = self._probe_context_login_success(context, monitor_page)
                            if login_success_after_slider:
                                logger.success(f"【{self.pure_user_id}】✅ 滑块验证后，登录验证成功！")
                                login_success = True
                            else:
                                # 滑块验证后仍未登录成功，继续检测二维码/人脸验证（此时应该不会再检测到滑块）
                                logger.info(f"【{self.pure_user_id}】等待1秒后继续检测是否需要二维码/人脸验证...")
                                time.sleep(1)
                                logger.info(f"【{self.pure_user_id}】滑块验证后，继续检测是否需要二维码/人脸验证...")
                                monitor_page = self._select_monitor_page(context, active_page or monitor_page)
                                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                        
                        if has_qr:
                            return self._process_verification_requirement(
                                context,
                                monitor_page,
                                qr_frame,
                                notification_callback,
                                notification_scene,
                            )
                        else:
                            logger.info(f"【{self.pure_user_id}】未检测到二维码/人脸验证")
                            # 再次检查登录状态，确保登录成功
                            logger.info(f"【{self.pure_user_id}】等待1秒后再次检查登录状态...")
                            time.sleep(1)
                            login_success, active_page, _ = self._probe_context_login_success(context, active_page or page)
                            if not login_success:
                                logger.error(f"【{self.pure_user_id}】❌ 登录状态未确认，无法获取Cookie")
                                return self._fail_login("登录状态未确认，无法获取Cookie")
                            else:
                                logger.success(f"【{self.pure_user_id}】✅ 登录状态已确认")
                    
                    # 【重要】只有在 login_success = True 的情况下，才获取Cookie
                    if not login_success:
                        logger.error(f"【{self.pure_user_id}】❌ 登录未成功，无法获取Cookie")
                        return self._fail_login("登录未成功，无法获取Cookie")
                    
                    # 获取Cookie
                    logger.info(f"【{self.pure_user_id}】等待1秒后获取Cookie...")
                    time.sleep(1)
                    try:
                        cookies_result = self._finalize_logged_in_cookies(
                            context,
                            active_page or page,
                            scene="密码登录完成后",
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                            extra_cookie_updates=observed_set_cookie_updates or None,
                        )
                        if cookies_result:
                            logger.success("✅ 登录成功！Cookie有效")
                        return cookies_result
                    except Exception as e:
                        logger.error(f"【{self.pure_user_id}】获取Cookie失败: {_host.e}")
                        return self._fail_login("获取Cookie失败")
                
                finally:
                    # 恢复原始值
                    self.page = original_page
                    self.context = original_context
                    self.browser = original_browser
                    self.playwright = original_playwright
            
            finally:
                # 关闭浏览器。这里不能无限阻塞，否则上层会话会一直卡在 processing。
                try:
                    close_errors = []

                    def _close_runtime_resources():
                        try:
                            if context:
                                context.close()
                        except Exception as close_context_err:
                            close_errors.append(f"context.close: {close_context_err}")

                        if effective_clean_context and browser:
                            try:
                                browser.close()
                            except Exception as close_browser_err:
                                close_errors.append(f"browser.close: {close_browser_err}")

                        try:
                            if playwright:
                                playwright.stop()
                        except Exception as stop_playwright_err:
                            close_errors.append(f"playwright.stop: {stop_playwright_err}")

                    close_thread = _host.threading.Thread(
                        target=_close_runtime_resources,
                        name=f"pwd-login-close-{self.pure_user_id}",
                        daemon=True,
                    )
                    close_thread.start()
                    close_thread.join(timeout=8)

                    if close_thread.is_alive():
                        logger.warning(f"【{self.pure_user_id}】关闭浏览器超时，改为后台继续清理，避免阻塞密码登录会话收尾")
                        self._force_kill_browser_process_tree("password_login_close_timeout")
                    elif close_errors:
                        logger.warning(f"【{self.pure_user_id}】关闭浏览器时出现异常: {close_errors}")
                        self._force_kill_browser_process_tree("password_login_close_error")
                    elif effective_clean_context:
                        logger.info(f"【{self.pure_user_id}】浏览器已关闭，干净上下文已销毁")
                    else:
                        logger.info(f"【{self.pure_user_id}】浏览器已关闭，缓存已保存")
                        self._browser_pid = None
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】关闭浏览器时出错: {_host.e}")

                # 释放并发槽位（防止槽位泄漏导致后续任务永远等待）
                try:
                    self._release_concurrency_slot("密码登录结束")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】释放并发槽位时出错: {_host.e}")
        
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】密码登录流程异常: {_host.e}")
            import traceback
            logger.error(traceback.format_exc())
            error_message = str(_host.e)
            if self._is_profile_in_use_launch_error(_host.e):
                return self._fail_login("浏览器用户目录正被其他登录流程占用，请稍后重试")
            if "Target page, context or browser has been closed" in error_message:
                return self._fail_login("页面会话已失效，请重新尝试刷新Cookie")
            return self._fail_login(error_message if error_message else "密码登录流程异常")
        finally:
            self._slider_refresh_mode = previous_slider_refresh_mode
            self._password_slider_runtime_hardened = False
            self.risk_trigger_scene = previous_risk_trigger_scene
            # 最外层 finally：确保任何退出路径都释放并发槽位
            try:
                self._release_concurrency_slot("密码登录finally兜底")
            except Exception:
                pass

    def login_with_password_headful(self, account: str = None, password: str = None, show_browser: bool = False):
        """通过浏览器进行密码登录并获取Cookie (使用DrissionPage)
        
        Args:
            account: 登录账号（必填）
            password: 登录密码（必填）
            show_browser: 是否显示浏览器窗口（默认False为无头模式）
                         True: 有头模式，登录后等待5分钟（可手动处理验证码）
                         False: 无头模式，登录后等待10秒
            
        Returns:
            dict: 获取到的cookie字典，失败返回None
        """
        page = None
        try:
            # 检查日期有效性
            if not self._check_date_validity():
                logger.error(f"【{self.pure_user_id}】日期验证失败，无法执行登录")
                return None
            
            # 验证必需参数
            if not account or not password:
                logger.error(f"【{self.pure_user_id}】账号或密码不能为空")
                return None
            
            browser_mode = "有头" if show_browser else "无头"
            logger.info(f"【{self.pure_user_id}】开始{browser_mode}模式密码登录流程（使用DrissionPage）...")
            
            # 导入 DrissionPage
            try:
                from DrissionPage import ChromiumPage, ChromiumOptions
                logger.info(f"【{self.pure_user_id}】DrissionPage导入成功")
            except ImportError:
                logger.error(f"【{self.pure_user_id}】DrissionPage未安装，请执行: pip install DrissionPage")
                return None
            
            # 配置浏览器选项
            logger.info(f"【{self.pure_user_id}】配置浏览器选项（{browser_mode}模式）...")
            co = ChromiumOptions()
            
            # 根据 show_browser 参数决定是否启用无头模式
            if not show_browser:
                co.headless()
                logger.info(f"【{self.pure_user_id}】已启用无头模式")
            else:
                logger.info(f"【{self.pure_user_id}】已启用有头模式（浏览器可见）")
            
            # 设置浏览器参数（反检测）
            co.set_argument('--no-sandbox')
            co.set_argument('--disable-setuid-sandbox')
            co.set_argument('--disable-dev-shm-usage')
            co.set_argument('--disable-blink-features=AutomationControlled')
            co.set_argument('--disable-infobars')
            co.set_argument('--disable-extensions')
            co.set_argument('--disable-popup-blocking')
            co.set_argument('--disable-notifications')
            
            # 无头模式需要的额外参数
            if not show_browser:
                co.set_argument('--disable-gpu')
                co.set_argument('--disable-software-rasterizer')
            else:
                # 有头模式窗口最大化
                co.set_argument('--start-maximized')
            
            # 设置用户代理
            browser_features = self._get_random_browser_features()
            co.set_user_agent(browser_features['user_agent'])
            
            # 设置中文语言
            co.set_argument('--lang=zh-CN')
            logger.info(f"【{self.pure_user_id}】已设置浏览器语言为中文（zh-CN）")
            
            # 禁用自动化特征检测
            co.set_pref('excludeSwitches', ['enable-automation'])
            co.set_pref('useAutomationExtension', False)
            
            # 创建浏览器页面，添加重试机制
            logger.info(f"【{self.pure_user_id}】启动DrissionPage浏览器（{browser_mode}模式）...")
            max_retries = 3
            retry_count = 0
            page = None
            
            while retry_count < max_retries and page is None:
                try:
                    if retry_count > 0:
                        logger.info(f"【{self.pure_user_id}】第 {retry_count + 1} 次尝试启动浏览器...")
                        time.sleep(2)  # 等待2秒后重试
                    
                    page = ChromiumPage(addr_or_opts=co)
                    logger.info(f"【{self.pure_user_id}】浏览器已成功启动（{browser_mode}模式）")
                    break
                    
                except Exception as browser_error:
                    retry_count += 1
                    logger.warning(f"【{self.pure_user_id}】浏览器启动失败 (尝试 {retry_count}/{max_retries}): {str(browser_error)}")
                    
                    if retry_count >= max_retries:
                        logger.error(f"【{self.pure_user_id}】浏览器启动失败，已达到最大重试次数")
                        logger.error(f"【{self.pure_user_id}】可能的原因：")
                        logger.error(f"【{self.pure_user_id}】1. Chrome/Chromium 浏览器未正确安装或路径不正确")
                        logger.error(f"【{self.pure_user_id}】2. 远程调试端口被占用，请关闭其他Chrome实例")
                        logger.error(f"【{self.pure_user_id}】3. 系统资源不足")
                        logger.error(f"【{self.pure_user_id}】建议：")
                        logger.error(f"【{self.pure_user_id}】- 检查Chrome浏览器是否已安装")
                        logger.error(f"【{self.pure_user_id}】- 关闭所有Chrome浏览器窗口后重试")
                        logger.error(f"【{self.pure_user_id}】- 检查任务管理器中是否有残留的chrome.exe进程")
                        raise
                    
                    # 尝试清理可能残留的Chrome进程
                    try:
                        import subprocess
                        import platform
                        if platform.system() == 'Windows':
                            _host.subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'], 
                                         capture_output=True, timeout=5)
                            logger.info(f"【{self.pure_user_id}】已尝试清理残留Chrome进程")
                    except Exception as cleanup_error:
                        logger.debug(f"【{self.pure_user_id}】清理进程时出错: {cleanup_error}")
            
            if page is None:
                logger.error(f"【{self.pure_user_id}】无法启动浏览器")
                return None
            
            # 访问登录页面
            target_url = "https://www.goofish.com/im"
            logger.info(f"【{self.pure_user_id}】访问登录页面: {target_url}")
            page.get(target_url)
            
            # 等待页面加载
            logger.info(f"【{self.pure_user_id}】等待页面加载...")
            time.sleep(5)
            
            # 检查页面状态
            logger.info(f"【{self.pure_user_id}】========== 页面诊断信息 ==========")
            current_url = page.url
            logger.info(f"【{self.pure_user_id}】当前URL: {current_url}")
            page_title = page.title
            logger.info(f"【{self.pure_user_id}】页面标题: {page_title}")
            
            
            logger.info(f"【{self.pure_user_id}】====================================")
            
            # 查找并点击密码登录标签
            logger.info(f"【{self.pure_user_id}】查找密码登录标签...")
            password_tab_selectors = [
                '.password-login-tab-item',
                'text:密码登录',
                'text:账号密码登录',
            ]
            
            password_tab_found = False
            for selector in password_tab_selectors:
                try:
                    tab = page.ele(selector, timeout=3)
                    if tab:
                        logger.info(f"【{self.pure_user_id}】找到密码登录标签: {selector}")
                        tab.click()
                        logger.info(f"【{self.pure_user_id}】密码登录标签已点击")
                        time.sleep(2)
                        password_tab_found = True
                        break
                except:
                    continue
            
            if not password_tab_found:
                logger.warning(f"【{self.pure_user_id}】未找到密码登录标签，可能页面默认就是密码登录模式")
            
            # 查找登录表单
            logger.info(f"【{self.pure_user_id}】开始检测登录表单...")
            username_selectors = [
                '#fm-login-id',
                'input:name=fm-login-id',
                'input:placeholder^=手机',
                'input:placeholder^=账号',
                'input:type=text',
                '#TPL_username_1',
            ]
            
            login_input = None
            for selector in username_selectors:
                try:
                    login_input = page.ele(selector, timeout=2)
                    if login_input:
                        logger.info(f"【{self.pure_user_id}】找到登录表单: {selector}")
                        break
                except:
                    continue
            
            if not login_input:
                logger.error(f"【{self.pure_user_id}】未找到登录表单")
                return None
            
            # 输入账号
            logger.info(f"【{self.pure_user_id}】输入账号: {account}")
            try:
                login_input.click()
                time.sleep(0.5)
                login_input.input(account)
                logger.info(f"【{self.pure_user_id}】账号已输入")
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】输入账号失败: {str(_host.e)}")
                return None
            
            # 输入密码
            logger.info(f"【{self.pure_user_id}】输入密码...")
            password_selectors = [
                '#fm-login-password',
                'input:name=fm-login-password',
                'input:type=password',
                'input:placeholder^=密码',
                '#TPL_password_1',
            ]
            
            password_input = None
            for selector in password_selectors:
                try:
                    password_input = page.ele(selector, timeout=2)
                    if password_input:
                        logger.info(f"【{self.pure_user_id}】找到密码输入框: {selector}")
                        break
                except:
                    continue
            
            if not password_input:
                logger.error(f"【{self.pure_user_id}】未找到密码输入框")
                return None
            
            try:
                password_input.click()
                time.sleep(0.5)
                password_input.input(password)
                logger.info(f"【{self.pure_user_id}】密码已输入")
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】输入密码失败: {str(_host.e)}")
                return None
            
            # 勾选协议（可选）
            logger.info(f"【{self.pure_user_id}】查找并勾选用户协议...")
            agreement_selectors = [
                '#fm-agreement-checkbox',
                'input:type=checkbox',
            ]
            
            for selector in agreement_selectors:
                try:
                    checkbox = page.ele(selector, timeout=1)
                    if checkbox and not checkbox.states.is_checked:
                        checkbox.click()
                        logger.info(f"【{self.pure_user_id}】用户协议已勾选")
                        time.sleep(0.5)
                        break
                except:
                    continue
            
            # 点击登录按钮
            logger.info(f"【{self.pure_user_id}】点击登录按钮...")
            login_button_selectors = [
                '@class=fm-button fm-submit password-login ',
                '.fm-button.fm-submit.password-login',
                'button.password-login',
                '.password-login',
                'button.fm-submit',
                'text:登录',
            ]
            
            login_button_found = False
            for selector in login_button_selectors:
                try:
                    button = page.ele(selector, timeout=2)
                    if button:
                        logger.info(f"【{self.pure_user_id}】找到登录按钮: {selector}")
                        button.click()
                        logger.info(f"【{self.pure_user_id}】登录按钮已点击")
                        login_button_found = True
                        break
                except:
                    continue
            
            if not login_button_found:
                logger.warning(f"【{self.pure_user_id}】未找到登录按钮，尝试按Enter键...")
                try:
                    password_input.input('\n')  # 模拟按Enter
                    logger.info(f"【{self.pure_user_id}】已按Enter键")
                except Exception as e:
                    logger.error(f"【{self.pure_user_id}】按Enter键失败: {str(_host.e)}")
            
            # 等待登录完成
            logger.info(f"【{self.pure_user_id}】等待登录完成...")
            time.sleep(5)
            
            # 检查当前URL和标题
            current_url = page.url
            logger.info(f"【{self.pure_user_id}】登录后URL: {current_url}")
            page_title = page.title
            logger.info(f"【{self.pure_user_id}】登录后页面标题: {page_title}")
            
            # 根据浏览器模式决定等待时间
            # 有头模式：等待5分钟（用户可能需要手动处理验证码等）
            # 无头模式：等待10秒
            if show_browser:
                wait_seconds = 300  # 5分钟
                logger.info(f"【{self.pure_user_id}】有头模式：等待5分钟让Cookie完全生成（期间可手动处理验证码等）...")
            else:
                wait_seconds = 10
                logger.info(f"【{self.pure_user_id}】无头模式：等待10秒让Cookie完全生成...")
            
            time.sleep(wait_seconds)
            logger.info(f"【{self.pure_user_id}】等待完成，准备获取Cookie")
            
            # 获取Cookie
            logger.info(f"【{self.pure_user_id}】开始获取Cookie...")
            cookies_raw = page.cookies()
            
            # 将cookies转换为字典格式
            _host.cookies = {}
            if isinstance(cookies_raw, list):
                # 如果返回的是列表格式，转换为字典
                for cookie in cookies_raw:
                    if isinstance(cookie, dict) and 'name' in cookie and 'value' in cookie:
                        _host.cookies[cookie['name']] = cookie['value']
                    elif isinstance(cookie, tuple) and len(cookie) >= 2:
                        _host.cookies[cookie[0]] = cookie[1]
            elif isinstance(cookies_raw, dict):
                # 如果已经是字典格式，直接使用
                _host.cookies = cookies_raw
            
            if _host.cookies:
                logger.info(f"【{self.pure_user_id}】成功获取 {len(_host.cookies)} 个Cookie")
                logger.info(f"【{self.pure_user_id}】Cookie名称列表: {list(_host.cookies.keys())}")
                logger.info(
                    f"【{self.pure_user_id}】Cookie摘要: keys={list(_host.cookies.keys())}, "
                    f"has_unb={'unb' in _host.cookies}, count={len(_host.cookies)}"
                )
                logger.info(f"【{self.pure_user_id}】登录成功，准备关闭浏览器")
                return _host.cookies
            else:
                logger.error(f"【{self.pure_user_id}】未获取到任何Cookie")
                return None
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】密码登录流程出错: {str(_host.e)}")
            import traceback
            logger.error(f"【{self.pure_user_id}】详细错误信息: {traceback.format_exc()}")
            return None
        finally:
            # 关闭浏览器
            logger.info(f"【{self.pure_user_id}】关闭浏览器...")
            try:
                if page:
                    page.quit()
                    logger.info(f"【{self.pure_user_id}】DrissionPage浏览器已关闭")
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】关闭浏览器时出错: {_host.e}")

    def _is_password_login_scene(self) -> bool:
        return self.risk_trigger_scene in {'password_login', 'manual_password_refresh'}

    def _has_completed_login_cookies(self, cookie_dict: Dict[str, str]) -> bool:
        if not cookie_dict.get('unb'):
            return False

        companion_keys = (
            'cookie2', 'havana_lgc2_77', '_tb_token_', 'sgcookie',
            '_m_h5_tk', '_m_h5_tk_enc', 't'
        )
        return any(cookie_dict.get(key) for key in companion_keys)

    def _page_has_keep_login_prompt(self, page) -> bool:
        try:
            prompt_selectors = [
                'text=保持登录',
                'text=不保持',
            ]
            for selector in prompt_selectors:
                try:
                    element = page.query_selector(selector)
                    if element and element.is_visible():
                        return True
                except Exception as selector_error:
                    _mark_detached_runtime(selector_error)
                    continue
        except Exception:
            pass
        return False

    def _get_password_login_selectors(self) -> Dict[str, List[str]]:
        return {
            'account': [
                '#fm-login-id',
                'input[name="fm-login-id"]',
                'input[placeholder*="手机号"]',
                'input[placeholder*="手机"]',
                'input[placeholder*="邮箱"]',
                'input[placeholder*="账号"]',
                '.fm-login-id',
                '#J_LoginForm input[type="text"]',
                '#TPL_username_1',
            ],
            'password': [
                '#fm-login-password',
                'input[name="fm-login-password"]',
                'input[type="password"]',
                'input[placeholder*="密码"]',
                '#TPL_password_1',
            ],
            'submit': [
                'button.password-login',
                '.fm-button.fm-submit.password-login',
                '.password-login',
                'button.fm-submit',
                'text=登录',
            ],
            'tab': [
                'a.password-login-tab-item',
                '.password-login-tab-item',
                'text=密码登录',
                'text=账号密码登录',
            ],
            'agreement': [
                '#fm-agreement-checkbox',
                'input[type="checkbox"]',
            ],
        }

    def _probe_login_form_state(self, frame) -> Dict[str, Any]:
        """探测当前 frame 是否具备真正可交互的账密登录表单。"""
        if not frame:
            return {
                'is_login_form': False,
                'probe_type': 'missing',
                'matched_selector': None,
                'matched_text': None,
            }

        selectors = self._get_password_login_selectors()
        account_input, account_selector = self._query_first_visible(frame, selectors['account'])
        if account_input:
            return {
                'is_login_form': True,
                'probe_type': 'account_input',
                'matched_selector': account_selector,
                'matched_text': None,
            }

        password_input, password_selector = self._query_first_visible(frame, selectors['password'])
        if password_input:
            return {
                'is_login_form': True,
                'probe_type': 'password_input',
                'matched_selector': password_selector,
                'matched_text': None,
            }

        password_tab, tab_selector = self._query_first_visible(frame, selectors['tab'])
        submit_button, submit_selector = self._query_first_visible(frame, selectors['submit'])

        submit_text = None
        if submit_button:
            try:
                submit_text = ' '.join((submit_button.inner_text() or '').split())
            except Exception:
                submit_text = None

        if password_tab and submit_button:
            return {
                'is_login_form': True,
                'probe_type': 'password_tab_plus_submit',
                'matched_selector': f"{tab_selector} + {submit_selector}",
                'matched_text': submit_text,
            }

        if submit_button:
            probe_type = 'submit_only'
            submit_text_value = submit_text or ''
            # “text=登录” 在主页面/弹窗遮罩里太宽泛，不能当成快速进入；
            # 只有按钮自身文案明确包含“快速进入/继续/去登录/去看看”等免密直达语义时才自动点击。
            if any(keyword in submit_text_value for keyword in ('快速进入', '进入', '继续', '去登录', '去看看')):
                probe_type = 'direct_enter_like'
            return {
                'is_login_form': False,
                'probe_type': probe_type,
                'matched_selector': submit_selector,
                'matched_text': submit_text,
            }

        if password_tab:
            return {
                'is_login_form': False,
                'probe_type': 'tab_only',
                'matched_selector': tab_selector,
                'matched_text': None,
            }

        return {
            'is_login_form': False,
            'probe_type': 'none',
            'matched_selector': None,
            'matched_text': None,
        }

    def _find_login_form_with_retry(self, page, timeout_seconds: float = 8.0,
                                    poll_interval: float = 1.0):
        if not page:
            return None, False, None

        deadline = time.time() + max(timeout_seconds, 0.0)
        attempt = 0
        last_non_form_probe = None

        while True:
            attempt += 1
            search_frames = [('主页面', page)]
            try:
                for idx, frame in enumerate(page.frames):
                    if frame == page.main_frame:
                        continue
                    search_frames.append((f'Frame {idx}', frame))
            except Exception:
                pass

            for frame_label, frame in search_frames:
                probe_info = self._probe_login_form_state(frame)
                if probe_info.get('is_login_form'):
                    matched_selector = probe_info.get('matched_selector')
                    probe_type = probe_info.get('probe_type')
                    probe_text = probe_info.get('matched_text')
                    probe_note = f" [{probe_text}]" if probe_text else ""
                    logger.info(
                        f"【{self.pure_user_id}】✓ 第{attempt}次探测在{frame_label}找到登录表单({probe_type}): "
                        f"{matched_selector}{probe_note}"
                    )
                    return frame, True, matched_selector

                if probe_info.get('probe_type') not in {'missing', 'none'}:
                    last_non_form_probe = {
                        'frame_label': frame_label,
                        'attempt': attempt,
                        **probe_info,
                    }

            if time.time() >= deadline:
                break

            time.sleep(max(poll_interval, 0.1))

        if last_non_form_probe:
            probe_text = last_non_form_probe.get('matched_text')
            probe_note = f" [{probe_text}]" if probe_text else ""
            logger.warning(
                f"【{self.pure_user_id}】登录表单探测超时，最近一次仅命中非表单态"
                f"({last_non_form_probe.get('probe_type')})，位置={last_non_form_probe.get('frame_label')}，"
                f"选择器={last_non_form_probe.get('matched_selector')}{probe_note}"
            )
        logger.warning(
            f"【{self.pure_user_id}】在 {timeout_seconds:.1f}s 内未探测到登录表单"
        )
        return None, False, None

    def _prepare_login_page_after_cleanup(self, context, page, *, clear_storage: bool = False,
                                          reopen_fresh_page: bool = False,
                                          timeout_seconds: float = 8.0):
        if context:
            context.clear_cookies()

        if clear_storage:
            cleared_pages = self._clear_page_storage_state(context, page)
            logger.info(f"【{self.pure_user_id}】已清理 {cleared_pages} 个页面的本地存储")

        active_page = page
        active_page.goto("https://www.goofish.com/im", wait_until="domcontentloaded", timeout=30000)
        time.sleep(1)
        login_frame, found_login_form, matched_selector = self._find_login_form_with_retry(
            active_page,
            timeout_seconds=timeout_seconds,
            poll_interval=1.0,
        )
        if found_login_form:
            return active_page, login_frame, True, matched_selector, False

        if reopen_fresh_page and context:
            try:
                fresh_page = context.new_page()
                fresh_page.goto("https://www.goofish.com/im", wait_until="domcontentloaded", timeout=30000)
                time.sleep(1)
                login_frame, found_login_form, matched_selector = self._find_login_form_with_retry(
                    fresh_page,
                    timeout_seconds=timeout_seconds,
                    poll_interval=1.0,
                )
                if found_login_form:
                    logger.info(f"【{self.pure_user_id}】✓ 新建页面后找到登录表单")
                    return fresh_page, login_frame, True, matched_selector, True
                try:
                    fresh_page.close()
                except Exception:
                    pass
            except Exception as fresh_page_error:
                logger.warning(f"【{self.pure_user_id}】新建页面重新探测登录表单失败: {fresh_page_error}")

        return active_page, None, False, None, False

    def _page_has_login_form(self, page) -> bool:
        if not page:
            return False

        frames_to_check = [page]
        try:
            frames_to_check.extend(list(page.frames))
        except Exception:
            pass

        for frame in frames_to_check:
            try:
                if self._probe_login_form_state(frame).get('is_login_form'):
                    return True
            except Exception:
                continue

        return False

    def _probe_context_login_success(self, context, fallback_page=None) -> Tuple[bool, Any, Dict[str, str]]:
        monitor_page = self._select_monitor_page(context, fallback_page)
        cookie_dict = self._snapshot_context_cookies(context, page=monitor_page)
        pending_identity_markers = self._detect_pending_identity_verification_cookie_state(cookie_dict)

        if monitor_page:
            try:
                current_url = self._safe_page_url(monitor_page)
                page_has_slider = self._page_has_slider(monitor_page)
                page_looks_verification = self._page_looks_like_verification(monitor_page)
                if (
                    self._check_login_success_by_element(monitor_page) and
                    self._has_completed_login_cookies(cookie_dict) and
                    self._is_logged_in_url(current_url) and
                    not page_has_slider and
                    not page_looks_verification and
                    not pending_identity_markers
                ):
                    logger.success(f"【{self.pure_user_id}】✅ 当前监控页面已确认登录成功")
                    return True, monitor_page, cookie_dict
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】检查监控页面登录状态失败: {_host.e}")

        if not self._has_completed_login_cookies(cookie_dict):
            return False, monitor_page, cookie_dict

        pending_identity_markers = self._detect_pending_identity_verification_cookie_state(cookie_dict)
        if monitor_page:
            current_url = self._safe_page_url(monitor_page)
            page_has_slider = self._page_has_slider(monitor_page)
            page_looks_verification = self._page_looks_like_verification(monitor_page)
            if (
                self._is_logged_in_url(current_url) and
                not page_has_slider and
                not page_looks_verification and
                not pending_identity_markers
            ):
                logger.success(
                    f"【{self.pure_user_id}】✅ 检测到上下文已登录，当前URL: {current_url}"
                )
                return True, monitor_page, cookie_dict

        probe_page = None
        try:
            probe_page = context.new_page()
            probe_page.goto('https://www.goofish.com/im', wait_until='domcontentloaded', timeout=30000)
            time.sleep(1.5)

            probe_cookies = self._snapshot_context_cookies(context, page=probe_page)
            probe_url = self._safe_page_url(probe_page)
            probe_has_slider = self._page_has_slider(probe_page)
            probe_looks_verification = self._page_looks_like_verification(probe_page)
            probe_pending_identity_markers = self._detect_pending_identity_verification_cookie_state(probe_cookies)
            if (
                self._check_login_success_by_element(probe_page) and
                self._has_completed_login_cookies(probe_cookies) and
                self._is_logged_in_url(probe_url) and
                not probe_has_slider and
                not probe_looks_verification and
                not probe_pending_identity_markers
            ):
                logger.success(f"【{self.pure_user_id}】✅ 通过探测页面确认登录成功")
                return True, probe_page, probe_cookies

            probe_has_slider = self._page_has_slider(probe_page)
            probe_looks_verification = self._page_looks_like_verification(probe_page)
            probe_pending_identity_markers = self._detect_pending_identity_verification_cookie_state(probe_cookies)
            if (
                self._has_completed_login_cookies(probe_cookies) and
                self._is_logged_in_url(probe_url) and
                not probe_has_slider and
                not probe_looks_verification and
                not probe_pending_identity_markers
            ):
                logger.success(f"【{self.pure_user_id}】✅ 通过探测页面URL和Cookie确认登录成功")
                return True, probe_page, probe_cookies
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】探测上下文登录状态失败: {_host.e}")
        finally:
            if probe_page:
                try:
                    probe_page.close()
                except Exception:
                    pass

        return False, monitor_page, cookie_dict

    def _recover_from_missing_login_inputs(
        self,
        context,
        page,
        *,
        missing_field: str,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ) -> Tuple[bool, Any]:
        logger.warning(
            f"【{self.pure_user_id}】未找到{missing_field}，复检当前页面是否处于已登录态或验证页..."
        )

        login_success, active_page, _ = self._probe_context_login_success(context, page)
        if login_success:
            cookies_result = self._finalize_logged_in_cookies(
                context,
                active_page or page,
                scene=f"{missing_field}复检已登录",
                notification_callback=notification_callback,
                notification_scene=notification_scene,
            )
            logger.success(f"【{self.pure_user_id}】✅ 页面实际已登录，停止继续账密输入")
            return True, cookies_result

        monitor_page = self._select_monitor_page(context, active_page or page) or active_page or page
        if monitor_page:
            has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
            if has_qr:
                logger.info(f"【{self.pure_user_id}】复检发现当前页面需要人工验证，转入验证流程")
                return True, self._process_verification_requirement(
                    context,
                    monitor_page,
                    qr_frame,
                    notification_callback,
                    notification_scene,
                )

        return False, None

    def _wait_for_context_login(
        self,
        context,
        fallback_page,
        max_wait_time: int = 450,
        check_interval: int = 10,
        verification_type: str = 'unknown',
        verification_url: Optional[str] = None,
        verification_screenshot_path: Optional[str] = None,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ) -> Tuple[bool, Any]:
        waited_time = 0
        monitor_page = fallback_page
        last_verification_type = verification_type or 'unknown'
        last_verification_url = verification_url or None
        last_verification_screenshot_path = verification_screenshot_path or None

        while waited_time < max_wait_time:
            monitor_page = self._select_monitor_page(context, monitor_page)
            self._attempt_solve_slider_on_page(monitor_page)
            has_verification, refreshed_frame = self._detect_qr_code_verification(monitor_page)

            login_success, success_page, success_cookies = self._probe_context_login_success(context, monitor_page)
            if login_success:
                pending_identity_markers = self._detect_pending_identity_verification_cookie_state(success_cookies or {})
                missing_protected_fields = [
                    key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                    if not (success_cookies or {}).get(key)
                ]
                if pending_identity_markers:
                    logger.warning(
                        f"【{self.pure_user_id}】验证等待期间虽然检测到页面已登录，"
                        f"但待确认Cookie标记仍存在，继续等待后续验证完成: {pending_identity_markers}"
                    )
                elif has_verification and missing_protected_fields:
                    logger.warning(
                        f"【{self.pure_user_id}】验证等待期间验证页仍存在，且关键Cookie仍未齐全，"
                        f"继续等待后续验证完成: {missing_protected_fields}"
                    )
                else:
                    return True, success_page or monitor_page

            if has_verification and refreshed_frame:
                refreshed_type = getattr(refreshed_frame, 'verification_type', None) or 'unknown'
                refreshed_url = getattr(refreshed_frame, 'verify_url', None)
                if not refreshed_url and hasattr(refreshed_frame, 'url'):
                    refreshed_url = refreshed_frame.url
                refreshed_screenshot_path = getattr(refreshed_frame, 'screenshot_path', None)

                if self._verification_target_is_timed_out(refreshed_frame, fallback_page=monitor_page):
                    recovered_frame = self._recover_timed_out_verification_page(
                        refreshed_frame,
                        fallback_page=monitor_page,
                    )
                    if recovered_frame:
                        refreshed_frame = recovered_frame
                        refreshed_type = getattr(refreshed_frame, 'verification_type', None) or refreshed_type
                        refreshed_url = getattr(refreshed_frame, 'verify_url', None)
                        if not refreshed_url and hasattr(refreshed_frame, 'url'):
                            refreshed_url = refreshed_frame.url
                        refreshed_screenshot_path = getattr(refreshed_frame, 'screenshot_path', None)
                        recovered_from_timeout = True
                    else:
                        timeout_message = self._build_timed_out_verification_message(refreshed_type)
                        self.last_login_error = timeout_message
                        logger.warning(f"【{self.pure_user_id}】{timeout_message}")
                        return False, monitor_page
                else:
                    recovered_from_timeout = False

                # 只按验证类型和 URL 判断是否变化；截图文件每轮都会生成新路径，
                # 如果把 screenshot_path 纳入变化判断，会导致同一个扫码页反复推送通知。
                verification_changed = (
                    recovered_from_timeout or
                    refreshed_type != last_verification_type or
                    (refreshed_url or None) != last_verification_url
                )
                if verification_changed:
                    logger.info(
                        f"【{self.pure_user_id}】验证等待期间检测到验证页变化: "
                        f"{last_verification_type}->{refreshed_type}, url={refreshed_url or 'N/A'}"
                    )
                    self._notify_verification_required(
                        refreshed_type,
                        refreshed_url,
                        refreshed_screenshot_path,
                        notification_callback,
                        notification_scene,
                    )
                    last_verification_type = refreshed_type
                    last_verification_url = refreshed_url or None
                    last_verification_screenshot_path = refreshed_screenshot_path or None

            time.sleep(check_interval)
            waited_time += check_interval
            logger.info(f"【{self.pure_user_id}】等待验证中... (已等待{waited_time}秒/{max_wait_time}秒)")

        return False, self._select_monitor_page(context, monitor_page)

    def _probe_context_login_during_slider(self, fallback_page=None) -> Tuple[bool, Dict[str, str]]:
        """刷新模式下，允许用 context 级登录态确认滑块已间接通过。"""
        if not getattr(self, '_slider_refresh_mode', False):
            return False, {}

        if not self.context:
            return False, {}

        try:
            login_success, _, _host.cookies = self._probe_context_login_success(self.context, fallback_page or self.page)
            if login_success:
                logger.success(f"【{self.pure_user_id}】✅ 滑块阶段检测到上下文已登录，停止继续重试")
                self.last_verification_feedback = {
                    "status": "success",
                    "source": "context_login_confirmed",
                    "message": "上下文登录状态已确认"
                }
                return True, _host.cookies or {}
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】滑块阶段探测上下文登录状态失败: {_host.e}")

        return False, {}

    def _check_login_success_by_element(self, page) -> bool:
        """通过页面元素检测登录是否成功
        
        Args:
            page: Page对象
        
        Returns:
            bool: 登录成功返回True，否则返回False
        """
        try:
            # 检查目标元素
            selector = '.rc-virtual-list-holder-inner'
            logger.info(f"【{self.pure_user_id}】========== 检查登录状态（通过页面元素） ==========")
            logger.info(f"【{self.pure_user_id}】检查选择器: {selector}")
            
            # 查找元素
            element = page.query_selector(selector)
            
            if element:
                # 获取元素的子元素数量
                child_count = element.evaluate('el => el.children.length')
                inner_html = element.inner_html()
                inner_text = element.inner_text() if element.is_visible() else ""
                
                logger.info(f"【{self.pure_user_id}】找到目标元素:")
                logger.info(f"【{self.pure_user_id}】  - 子元素数量: {child_count}")
                logger.info(f"【{self.pure_user_id}】  - 是否可见: {element.is_visible()}")
                logger.info(f"【{self.pure_user_id}】  - innerText长度: {len(inner_text)}")
                logger.info(f"【{self.pure_user_id}】  - innerHTML长度: {len(inner_html)}")
                
                # 判断是否有数据：子元素数量大于0
                if child_count > 0:
                    logger.success(f"【{self.pure_user_id}】✅ 登录成功！检测到列表有 {child_count} 个子元素")
                    logger.info(f"【{self.pure_user_id}】================================================")
                    return True
                else:
                    logger.debug(f"【{self.pure_user_id}】列表为空，登录未完成")
                    logger.info(f"【{self.pure_user_id}】================================================")
                    return False
            else:
                logger.debug(f"【{self.pure_user_id}】未找到目标元素: {selector}")
                logger.info(f"【{self.pure_user_id}】================================================")
                return False
                
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】检查登录状态时出错: {_host.e}")
            import traceback
            logger.debug(f"【{self.pure_user_id}】错误堆栈: {traceback.format_exc()}")
            return False

    def _check_login_error(self, page) -> tuple:
        """检测登录是否出现错误（如账密错误）
        
        Args:
            page: Page对象
        
        Returns:
            tuple: (has_error, error_message) - 是否有错误，错误消息
        """
        try:
            logger.debug(f"【{self.pure_user_id}】检查登录错误...")
            
            # 检测账密错误
            error_selectors = [
                '.login-error-msg',  # 主要的错误消息类
                '[class*="error-msg"]',  # 包含error-msg的类
                'div:has-text("账密错误")',  # 包含"账密错误"文本的div
                'text=账密错误',  # 直接文本匹配
            ]
            
            # 在主页面和所有frame中查找
            frames_to_check = [page] + page.frames
            
            for frame in frames_to_check:
                try:
                    for selector in error_selectors:
                        try:
                            element = frame.query_selector(selector)
                            if element and element.is_visible():
                                error_text = element.inner_text()
                                logger.error(f"【{self.pure_user_id}】❌ 检测到登录错误: {error_text}")
                                return True, error_text
                        except:
                            continue
                            
                    # 也检查页面HTML中是否包含错误文本
                    try:
                        detection_text = self._read_frame_text_for_detection(frame)
                        if '账密错误' in detection_text or '账号密码错误' in detection_text or '用户名或密码错误' in detection_text:
                            logger.error(f"【{self.pure_user_id}】❌ 页面内容中检测到账密错误")
                            return True, "账密错误"
                    except _host.PasswordLoginVerificationError:
                        raise
                    except Exception:
                        pass
                        
                except:
                    continue
            
            return False, None

        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】检查登录错误时出错: {_host.e}")
            return False, None

    def _start_password_login_slider_risk_log(self, verification_url: str = None,
                                              detection_phase: str = None) -> Optional[Dict[str, Any]]:
        try:
            from db_manager import db_manager

            trigger_scene, flow_label = self._resolve_slider_risk_context()
            event_meta = self._build_risk_event_meta(
                verification_url=verification_url,
                extra={
                    'account_id': self.pure_user_id,
                    'source': 'password_login_flow',
                    'refresh_mode': bool(getattr(self, '_slider_refresh_mode', False)),
                    'detection_phase': detection_phase,
                },
            )
            log_id = db_manager.add_risk_control_log(
                cookie_id=self.pure_user_id,
                event_type='slider_captcha',
                session_id=getattr(self, 'risk_session_id', None),
                trigger_scene=trigger_scene,
                result_code='password_login_slider_detected',
                event_description=f'{flow_label}检测到滑块验证',
                event_meta=event_meta,
                processing_status='processing',
                error_message='检测到滑块验证，正在自动处理',
            )
            if log_id:
                logger.info(f"【{self.pure_user_id}】已记录密码登录滑块风控日志: {log_id}")
                return {
                    'log_id': log_id,
                    'started_at': time.time(),
                    'verification_url': verification_url,
                    'event_meta': event_meta,
                    'trigger_scene': trigger_scene,
                    'flow_label': flow_label,
                }
        except Exception as log_err:
            logger.warning(f"【{self.pure_user_id}】记录密码登录滑块风控日志失败: {log_err}")
        return None

    def _finish_password_login_slider_risk_log(self, slider_risk_log: Optional[Dict[str, Any]], *,
                                               success: bool, verification_url: str = None,
                                               processing_result: str = None, error_message: str = None,
                                               extra_meta: Optional[Dict[str, Any]] = None):
        if not slider_risk_log or not slider_risk_log.get('log_id'):
            return

        try:
            from db_manager import db_manager

            trigger_scene = slider_risk_log.get('trigger_scene') or self._resolve_slider_risk_context()[0]
            flow_label = slider_risk_log.get('flow_label') or self._resolve_slider_risk_context()[1]
            final_verification_url = verification_url or slider_risk_log.get('verification_url')
            merged_event_meta = dict(slider_risk_log.get('event_meta') or {})
            if isinstance(extra_meta, dict):
                merged_event_meta.update({key: value for key, value in extra_meta.items() if value is not None})

            final_event_meta = self._build_risk_event_meta(
                verification_url=final_verification_url,
                extra=merged_event_meta,
            )

            result_code = 'password_login_slider_success' if _host.success else 'password_login_slider_failed'
            if _host.success:
                final_processing_result = processing_result or f'{flow_label}中的滑块验证成功'
                final_error_message = None
                event_description = f'{flow_label}中的滑块验证已自动处理成功'
            else:
                final_processing_result = processing_result or f'{flow_label}中的滑块验证失败'
                final_error_message = error_message or '滑块验证失败，请稍后重试'
                event_description = f'{flow_label}中的滑块验证自动处理失败'

            duration_ms = None
            started_at = slider_risk_log.get('started_at')
            if started_at:
                duration_ms = max(0, int((time.time() - float(started_at)) * 1000))

            db_manager.update_risk_control_log(
                log_id=slider_risk_log['log_id'],
                event_description=event_description,
                processing_result=final_processing_result,
                processing_status='success' if _host.success else 'failed',
                error_message=final_error_message,
                session_id=getattr(self, 'risk_session_id', None),
                trigger_scene=trigger_scene,
                result_code=result_code,
                event_meta=final_event_meta,
                duration_ms=duration_ms,
            )
        except Exception as log_err:
            logger.warning(f"【{self.pure_user_id}】更新密码登录滑块风控日志失败: {log_err}")

    def _get_password_scene_final_retry_template(self, effective_ranges: Dict[str, Tuple[float, float]],
                                                 bounds: Dict[str, Any]) -> Dict[str, Tuple[float, float]]:
        def clamp_range(source_range: Tuple[float, float], hard_range: Tuple[float, float], fallback: Tuple[float, float]):
            lower = max(source_range[0], hard_range[0])
            upper = min(source_range[1], hard_range[1])
            if lower > upper:
                lower, upper = fallback
            return (lower, upper)

        overshoot = clamp_range(
            effective_ranges["overshoot"],
            (1.028, min(bounds.get("max_overshoot_ratio", 1.18), 1.055)),
            (1.032, 1.050),
        )
        delay = clamp_range(effective_ranges["delay"], (0.0108, 0.0128), (0.0110, 0.0124))
        curve = clamp_range(effective_ranges["curve"], (1.76, 1.86), (1.78, 1.84))
        jitter = clamp_range(
            effective_ranges["jitter"],
            (max(bounds.get("min_y_jitter", 0.8), 1.55), min(bounds.get("max_y_jitter", 3.5), 2.35)),
            (1.70, 2.20),
        )

        step_min = max(30, effective_ranges["steps"][0], 32)
        step_max = min(36, max(step_min, effective_ranges["steps"][1]))
        if step_min > step_max:
            step_min, step_max = 32, 35

        return {
            "overshoot": overshoot,
            "delay": delay,
            "curve": curve,
            "jitter": jitter,
            "steps": (step_min, step_max),
        }

    def _get_cookies_after_success(self):
        """滑块验证成功后获取cookie"""
        try:
            logger.info(f"【{self.pure_user_id}】开始获取滑块验证成功后的页面cookie...")

            # 检查当前页面URL
            current_url = self.page.url
            logger.info(f"【{self.pure_user_id}】当前页面URL: {current_url}")

            # 检查页面标题
            page_title = self.page.title()
            logger.info(f"【{self.pure_user_id}】当前页面标题: {page_title}")

            # 滑块拦截页常在通过后把浏览器跳到 www.taobao.com，导致新的 _m_h5_tk 落在
            # .taobao.com 域；后续再去签 h5api.m.goofish.com 的接口就会被网关回 FAIL_SYS_ILLEGAL_ACCESS。
            # 主动回访一次 goofish 主域，让网关在 .goofish.com 域上重发 H5 token，再做快照。
            try:
                current_host = (urlparse(current_url).hostname or '').lower()
            except Exception:
                current_host = ''
            if 'goofish.com' not in current_host:
                try:
                    self.page.goto(
                        'https://www.goofish.com/',
                        wait_until='domcontentloaded',
                        timeout=8000,
                    )
                    time.sleep(1.5)
                    logger.info(
                        f"【{self.pure_user_id}】滑块通过后已回访 goofish 主域，"
                        f"等待 .goofish.com 域重新颁发 _m_h5_tk"
                    )
                except Exception as goto_e:
                    logger.warning(
                        f"【{self.pure_user_id}】回访 goofish 主域失败，仍按当前页 cookie 继续: {goto_e}"
                    )

            # 等待一下确保cookie完全更新
            time.sleep(1)

            new_cookies = self._snapshot_context_cookies(
                self.context,
                page=self.page,
                preferred_domain_suffixes=('goofish.com',),
            )

            if new_cookies:
                logger.info(f"【{self.pure_user_id}】滑块验证成功后已获取cookie，共{len(new_cookies)}个cookie")
                
                # 记录所有cookie的详细信息
                logger.info(f"【{self.pure_user_id}】获取到的所有cookie: {list(new_cookies.keys())}")
                
                # 单独记录x5相关cookie，便于排查风控链路
                x5_cookies = {}

                # 筛选出x5相关的cookies（包括x5sec, x5step等）；日志只记 key/len，不记 value
                for cookie_name, cookie_value in new_cookies.items():
                    cookie_name_lower = cookie_name.lower()
                    if cookie_name_lower.startswith('x5') or 'x5sec' in cookie_name_lower:
                        x5_cookies[cookie_name] = cookie_value
                        logger.info(
                            f"【{self.pure_user_id}】x5相关cookie已获取: {cookie_name} "
                            f"(len={len(str(cookie_value or ''))})"
                        )

                logger.info(f"【{self.pure_user_id}】找到{len(x5_cookies)}个x5相关cookies: {list(x5_cookies.keys())}")

                if x5_cookies:
                    logger.info(f"【{self.pure_user_id}】返回完整cookie集合，x5_keys={list(x5_cookies.keys())}")
                else:
                    logger.warning(f"【{self.pure_user_id}】未找到x5相关cookie")

                return new_cookies
            else:
                logger.warning(f"【{self.pure_user_id}】未获取到任何cookie")
                return None
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】获取滑块验证成功后的cookie失败: {str(_host.e)}")
            return None

    def _fail_login(self, message: str):
        self.last_login_error = message
        return None


class StealthScriptMixin:
    """隐身注入脚本与浏览器特征伪装。"""

    def _get_stealth_script(self, browser_features):
        """获取更接近真实桌面 Chrome 的反检测脚本。"""
        client_hints = self._build_client_hint_profile(browser_features)
        brands_json = json.dumps(client_hints["brands"], ensure_ascii=False)
        full_version_list_json = json.dumps(client_hints["fullVersionList"], ensure_ascii=False)

        return f"""
            (() => {{
                const defineGetter = (target, key, getter) => {{
                    try {{
                        Object.defineProperty(target, key, {{
                            get: getter,
                            configurable: true
                        }});
                    }} catch (e) {{}}
                }};

                const locale = {json.dumps(browser_features['locale'], ensure_ascii=False)};
                const languages = [locale, 'zh', 'en'];
                const pluginNames = [
                    'PDF Viewer',
                    'Chrome PDF Viewer',
                    'Chromium PDF Viewer',
                    'WebKit built-in PDF'
                ].slice(0, Math.max(1, {int(browser_features['plugin_count'])}));
                const mimeTypes = [
                    {{
                        type: 'application/pdf',
                        suffixes: 'pdf',
                        description: 'Portable Document Format'
                    }},
                    {{
                        type: 'text/pdf',
                        suffixes: 'pdf',
                        description: 'Portable Document Format'
                    }}
                ];

                const makePluginArray = () => {{
                    const arr = pluginNames.map((name) => ({{
                        name,
                        filename: name.toLowerCase().replace(/\\s+/g, '-') + '.dll',
                        description: name,
                        length: 1,
                        0: mimeTypes[0]
                    }}));
                    arr.item = (i) => arr[i] || null;
                    arr.namedItem = (name) => arr.find(p => p.name === name) || null;
                    return arr;
                }};

                const makeMimeTypeArray = () => {{
                    const arr = mimeTypes.map((item) => Object.assign({{}}, item));
                    arr.item = (i) => arr[i] || null;
                    arr.namedItem = (name) => arr.find(p => p.type === name) || null;
                    return arr;
                }};

                const uaData = {{
                    brands: {brands_json},
                    mobile: {str(bool(browser_features['is_mobile'])).lower()},
                    platform: {json.dumps(client_hints['platform'], ensure_ascii=False)},
                    getHighEntropyValues: async (hints) => {{
                        const payload = {{
                            architecture: {json.dumps(client_hints['architecture'])},
                            bitness: {json.dumps(client_hints['bitness'])},
                            brands: {brands_json},
                            fullVersionList: {full_version_list_json},
                            mobile: {str(bool(client_hints['mobile'])).lower()},
                            model: {json.dumps(client_hints['model'])},
                            platform: {json.dumps(client_hints['platform'], ensure_ascii=False)},
                            platformVersion: {json.dumps(client_hints['platformVersion'])},
                            uaFullVersion: {json.dumps(client_hints['fullVersion'])},
                            wow64: {str(bool(client_hints['wow64'])).lower()}
                        }};
                        if (!Array.isArray(hints) || hints.length === 0) {{
                            return payload;
                        }}
                        const result = {{}};
                        for (const key of hints) {{
                            if (Object.prototype.hasOwnProperty.call(payload, key)) {{
                                result[key] = payload[key];
                            }}
                        }}
                        return result;
                    }},
                    toJSON() {{
                        return {{
                            brands: this.brands,
                            mobile: this.mobile,
                            platform: this.platform
                        }};
                    }}
                }};

                // real Chrome keeps navigator.webdriver as a present boolean-like property,
                // deleting it entirely is itself a detectable anomaly.
                defineGetter(Navigator.prototype, 'webdriver', () => false);
                defineGetter(Navigator.prototype, 'languages', () => languages);
                defineGetter(Navigator.prototype, 'plugins', () => makePluginArray());
                defineGetter(Navigator.prototype, 'mimeTypes', () => makeMimeTypeArray());
                defineGetter(Navigator.prototype, 'platform', () => {json.dumps(browser_features['platform'], ensure_ascii=False)});
                defineGetter(Navigator.prototype, 'vendor', () => {json.dumps(browser_features['vendor'], ensure_ascii=False)});
                defineGetter(Navigator.prototype, 'userAgent', () => {json.dumps(browser_features['user_agent'])});
                defineGetter(Navigator.prototype, 'hardwareConcurrency', () => {int(browser_features['hardware_concurrency'])});
                defineGetter(Navigator.prototype, 'deviceMemory', () => {int(browser_features['device_memory'])});
                defineGetter(Navigator.prototype, 'maxTouchPoints', () => {int(browser_features['max_touch_points'])});
                defineGetter(Navigator.prototype, 'userAgentData', () => uaData);
                defineGetter(Navigator.prototype, 'pdfViewerEnabled', () => true);
                defineGetter(Navigator.prototype, 'doNotTrack', () => {json.dumps(browser_features['do_not_track'])});
                defineGetter(window, 'outerWidth', () => {int(browser_features['viewport_width'])});
                defineGetter(window, 'outerHeight', () => {int(browser_features['viewport_height']) + 88});
                defineGetter(screen, 'width', () => {int(browser_features['viewport_width'])});
                defineGetter(screen, 'height', () => {int(browser_features['viewport_height'])});
                defineGetter(screen, 'availWidth', () => {int(browser_features['viewport_width'])});
                defineGetter(screen, 'availHeight', () => {int(browser_features['viewport_height']) - 40});
                defineGetter(screen, 'colorDepth', () => {int(browser_features['color_depth'])});
                defineGetter(screen, 'pixelDepth', () => {int(browser_features['color_depth'])});

                defineGetter(Navigator.prototype, 'connection', () => ({{
                    effectiveType: {json.dumps(browser_features['connection_type'])},
                    rtt: {int(browser_features['connection_rtt'])},
                    downlink: {float(browser_features['connection_downlink'])},
                    saveData: false
                }}));

                if (!window.chrome) {{
                    window.chrome = {{}};
                }}
                window.chrome.runtime = window.chrome.runtime || {{}};
                window.chrome.app = window.chrome.app || {{
                    InstallState: {{
                        DISABLED: 'disabled',
                        INSTALLED: 'installed',
                        NOT_INSTALLED: 'not_installed'
                    }},
                    RunningState: {{
                        CANNOT_RUN: 'cannot_run',
                        READY_TO_RUN: 'ready_to_run',
                        RUNNING: 'running'
                    }},
                    getDetails: () => null,
                    getIsInstalled: () => false,
                    runningState: () => 'cannot_run'
                }};
                window.chrome.csi = window.chrome.csi || (() => ({{}}));
                window.chrome.loadTimes = window.chrome.loadTimes || (() => ({{}}));

                const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
                if (originalQuery) {{
                    window.navigator.permissions.query = (parameters) => {{
                        const name = parameters && parameters.name;
                        if (name === 'notifications') {{
                            return Promise.resolve({{
                                state: {json.dumps(browser_features['notification_permission'])},
                                onchange: null
                            }});
                        }}
                        return originalQuery(parameters);
                    }};
                }}

                if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
                    navigator.mediaDevices.enumerateDevices = async () => ([
                        {{
                            deviceId: 'default',
                            kind: 'audioinput',
                            label: '',
                            groupId: 'default'
                        }},
                        {{
                            deviceId: 'default',
                            kind: 'audiooutput',
                            label: '',
                            groupId: 'default'
                        }}
                    ]);
                }}

                const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                    if (parameter === 37445) return 'Google Inc. (Intel)';
                    if (parameter === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)';
                    return originalGetParameter.call(this, parameter);
                }};

                const originalToString = Function.prototype.toString;
                Function.prototype.toString = function() {{
                    if (this === window.navigator.permissions.query) {{
                        return 'function query() {{ [native code] }}';
                    }}
                    return originalToString.call(this);
                }};

                delete window.playwright;
                delete window.__playwright;
                delete window.__pw_manual;
                delete window.__pw_original;
                delete window.webdriver;
                delete window.__webdriver_script_fn;
                delete window.__webdriver_evaluate;
                delete window.__webdriver_unwrapped;
                delete window.__fxdriver_evaluate;
                delete window.__driver_evaluate;
                delete window.__webdriver_script_func;
                delete window._selenium;
                delete window._phantom;
                delete window.callPhantom;
                delete window.phantom;
            }})();
        """

    def _get_light_stealth_script(self, browser_features: Dict[str, Any]) -> str:
        locale = json.dumps(browser_features.get("locale") or "zh-CN", ensure_ascii=False)
        platform = json.dumps(browser_features.get("platform") or "Win32", ensure_ascii=False)
        vendor = json.dumps(browser_features.get("vendor") or "Google Inc.", ensure_ascii=False)
        user_agent = json.dumps(browser_features.get("user_agent") or "", ensure_ascii=False)

        return f"""
            (() => {{
                const defineGetter = (target, key, getter) => {{
                    try {{
                        Object.defineProperty(target, key, {{
                            get: getter,
                            configurable: true
                        }});
                    }} catch (e) {{}}
                }};

                const languages = [{locale}, 'zh', 'en'];
                defineGetter(Navigator.prototype, 'languages', () => languages);
                defineGetter(Navigator.prototype, 'platform', () => {platform});
                defineGetter(Navigator.prototype, 'vendor', () => {vendor});
                defineGetter(Navigator.prototype, 'userAgent', () => {user_agent});

                if (!window.chrome) {{
                    window.chrome = {{}};
                }}
                window.chrome.runtime = window.chrome.runtime || {{}};
            }})();
        """

    def _get_random_browser_features(self):
        """获取稳定浏览器特征。

        同一账号长期复用同一套桌面画像，避免后台无头链路在每次重启后漂移成
        不同设备，降低风控对“同账号多台机器来回切换”的判定概率。
        """
        runtime_is_windows = os.name == 'nt'

        browser_profiles = [
            # Windows Chrome 120 - 高配台式机
            {
                'profile_id': 'win_chrome_120_desktop',
                'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                'platform': 'Win32',
                'vendor': 'Google Inc.',
                'window_size': '1920,1080',
                'device_memory': 16,
                'hardware_concurrency': 8,
                'max_touch_points': 0,
                'device_scale_factor': 1.0,
                'color_depth': 24,
            },
            # Windows Chrome 120 - 中配笔记本
            {
                'profile_id': 'win_chrome_120_laptop',
                'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                'platform': 'Win32',
                'vendor': 'Google Inc.',
                'window_size': '1366,768',
                'device_memory': 8,
                'hardware_concurrency': 4,
                'max_touch_points': 0,
                'device_scale_factor': 1.25,
                'color_depth': 24,
            },
            # Windows Chrome 119 - 高配台式机
            {
                'profile_id': 'win_chrome_119_desktop',
                'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                'platform': 'Win32',
                'vendor': 'Google Inc.',
                'window_size': '1920,1200',
                'device_memory': 8,
                'hardware_concurrency': 6,
                'max_touch_points': 0,
                'device_scale_factor': 1.0,
                'color_depth': 24,
            },
            # Windows Chrome 118 - 标准台式机
            {
                'profile_id': 'win_chrome_118_standard',
                'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
                'platform': 'Win32',
                'vendor': 'Google Inc.',
                'window_size': '1600,900',
                'device_memory': 8,
                'hardware_concurrency': 4,
                'max_touch_points': 0,
                'device_scale_factor': 1.0,
                'color_depth': 24,
            },
            # Mac Chrome 120 - MacBook Pro
            {
                'profile_id': 'mac_chrome_120_pro',
                'user_agent': "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                'platform': 'MacIntel',
                'vendor': 'Google Inc.',
                'window_size': '2560,1440',
                'device_memory': 16,
                'hardware_concurrency': 10,
                'max_touch_points': 0,
                'device_scale_factor': 2.0,
                'color_depth': 30,
            },
            # Mac Chrome 119 - MacBook Air
            {
                'profile_id': 'mac_chrome_119_air',
                'user_agent': "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                'platform': 'MacIntel',
                'vendor': 'Google Inc.',
                'window_size': '1920,1080',
                'device_memory': 8,
                'hardware_concurrency': 8,
                'max_touch_points': 0,
                'device_scale_factor': 2.0,
                'color_depth': 30,
            },
        ]

        if runtime_is_windows:
            browser_profiles = [profile for profile in browser_profiles if str(profile.get('platform')) == 'Win32']

        languages = [
            ("zh-CN", "zh-CN,zh;q=0.9,en;q=0.8"),
            ("zh-CN", "zh-CN,zh;q=0.9"),
            ("zh-CN", "zh-CN,zh;q=0.8,en;q=0.6")
        ]

        identity = self._load_or_create_browser_identity(
            len(browser_profiles),
            len(languages),
            profile_version=3,
        )

        profile = browser_profiles[identity["profile_index"]]

        # 实测阿里 nocaptcha 在 Windows 无头下对 1366x768 / 高 DPI 组合更敏感，
        # 1600x900 + scale 1.0 的桌面画像通过率明显更稳，直接固定到这套。
        if runtime_is_windows and self.headless:
            preferred_profile = next(
                (item for item in browser_profiles if item.get('window_size') == '1600,900'),
                None,
            )
            if preferred_profile:
                profile = preferred_profile
        lang, accept_lang = languages[identity["language_index"]]

        # 解析窗口大小
        width, height = map(int, profile['window_size'].split(','))

        # 网络特征（桌面端只用 4g，rtt/downlink 在合理范围内随机）
        connection_rtt = random.randint(20, 80)
        connection_downlink = round(random.uniform(3, 10), 2)

        features = {
            'profile_id': profile['profile_id'],
            'window_size': profile['window_size'],
            'lang': lang,
            'accept_lang': accept_lang,
            'user_agent': profile['user_agent'],
            'locale': lang,
            'viewport_width': width,
            'viewport_height': height,
            'device_scale_factor': profile['device_scale_factor'],
            'is_mobile': False,
            'has_touch': False,
            'timezone_id': 'Asia/Shanghai',
            # 一致性指纹字段（与 UA 对应）
            'platform': profile['platform'],
            'vendor': profile['vendor'],
            'device_memory': profile['device_memory'],
            'hardware_concurrency': profile['hardware_concurrency'],
            'max_touch_points': profile['max_touch_points'],
            'color_depth': profile['color_depth'],
            'connection_type': '4g',
            'connection_rtt': connection_rtt,
            'connection_downlink': connection_downlink,
            'color_scheme': identity.get('color_scheme', 'light'),
            'plugin_count': identity.get('plugin_count', 5),
            'notification_permission': identity.get('notification_permission', 'default'),
            'do_not_track': identity.get('do_not_track', '0'),
            'battery_charging': identity.get('battery_charging', True),
            'battery_level': identity.get('battery_level', 0.76),
        }
        return self._apply_runtime_browser_profile(features)

    def _get_browser_family(self) -> str:
        local_browser_info = getattr(self, "local_browser_info", None) or {}
        if local_browser_info.get("family") in {"edge", "chrome"}:
            return str(local_browser_info.get("family"))
        if self.browser_channel == "msedge":
            return "edge"
        if self.browser_channel == "chrome":
            return "chrome"
        path_text = str(self.executable_path or "").lower()
        if "msedge" in path_text:
            return "edge"
        return "chrome"
