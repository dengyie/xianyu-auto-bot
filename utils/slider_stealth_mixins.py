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
import subprocess
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
            if getattr(sys, 'frozen', False):
                # 如果是打包后的exe，检查exe同目录下的浏览器
                exe_dir = Path(sys.executable).parent
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
                        error_msg = str(e)
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
                                    logger.debug(f"【{self.pure_user_id}】检查Frame {idx}时出错: {e}")
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
                                    logger.error(f"【{self.pure_user_id}】❌ 页面刷新失败: {e}")
                                    self._finish_password_login_slider_risk_log(
                                        slider_risk_log,
                                        success=False,
                                        verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                        error_message=f"页面会话已失效: {str(e)}",
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
                    logger.warning(f"【{self.pure_user_id}】查找密码登录标签失败: {e}")
                
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
                    logger.warning(f"【{self.pure_user_id}】勾选用户协议失败: {e}")
                
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
                        logger.error(f"【{self.pure_user_id}】获取Cookie失败: {e}")
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
                    logger.warning(f"【{self.pure_user_id}】关闭浏览器时出错: {e}")

                # 释放并发槽位（防止槽位泄漏导致后续任务永远等待）
                try:
                    self._release_concurrency_slot("密码登录结束")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】释放并发槽位时出错: {e}")
        
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】密码登录流程异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            error_message = str(e)
            if self._is_profile_in_use_launch_error(e):
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
                            subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'], 
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
                logger.error(f"【{self.pure_user_id}】输入账号失败: {str(e)}")
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
                logger.error(f"【{self.pure_user_id}】输入密码失败: {str(e)}")
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
                    logger.error(f"【{self.pure_user_id}】按Enter键失败: {str(e)}")
            
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
            cookies = {}
            if isinstance(cookies_raw, list):
                # 如果返回的是列表格式，转换为字典
                for cookie in cookies_raw:
                    if isinstance(cookie, dict) and 'name' in cookie and 'value' in cookie:
                        cookies[cookie['name']] = cookie['value']
                    elif isinstance(cookie, tuple) and len(cookie) >= 2:
                        cookies[cookie[0]] = cookie[1]
            elif isinstance(cookies_raw, dict):
                # 如果已经是字典格式，直接使用
                cookies = cookies_raw
            
            if cookies:
                logger.info(f"【{self.pure_user_id}】成功获取 {len(cookies)} 个Cookie")
                logger.info(f"【{self.pure_user_id}】Cookie名称列表: {list(cookies.keys())}")
                logger.info(
                    f"【{self.pure_user_id}】Cookie摘要: keys={list(cookies.keys())}, "
                    f"has_unb={'unb' in cookies}, count={len(cookies)}"
                )
                logger.info(f"【{self.pure_user_id}】登录成功，准备关闭浏览器")
                return cookies
            else:
                logger.error(f"【{self.pure_user_id}】未获取到任何Cookie")
                return None
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】密码登录流程出错: {str(e)}")
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
                logger.warning(f"【{self.pure_user_id}】关闭浏览器时出错: {e}")

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
                    logger.debug(f"【{self.pure_user_id}】检查保持登录提示选择器失败: {selector_error}")
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
                logger.debug(f"【{self.pure_user_id}】检查监控页面登录状态失败: {e}")

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
            logger.debug(f"【{self.pure_user_id}】探测上下文登录状态失败: {e}")
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
            login_success, _, cookies = self._probe_context_login_success(self.context, fallback_page or self.page)
            if login_success:
                logger.success(f"【{self.pure_user_id}】✅ 滑块阶段检测到上下文已登录，停止继续重试")
                self.last_verification_feedback = {
                    "status": "success",
                    "source": "context_login_confirmed",
                    "message": "上下文登录状态已确认"
                }
                return True, cookies or {}
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】滑块阶段探测上下文登录状态失败: {e}")

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
            logger.debug(f"【{self.pure_user_id}】检查登录状态时出错: {e}")
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
            logger.debug(f"【{self.pure_user_id}】检查登录错误时出错: {e}")
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

            result_code = 'password_login_slider_success' if success else 'password_login_slider_failed'
            if success:
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
                processing_status='success' if success else 'failed',
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
            logger.error(f"【{self.pure_user_id}】获取滑块验证成功后的cookie失败: {str(e)}")
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


class SliderTrajectoryMixin:
    """轨迹生成与优化（贝塞尔/缓动/物理轨迹/参数自适应学习）。"""

    def _bezier_curve(self, p0, p1, p2, p3, t):
        """三次贝塞尔曲线 - 生成更自然的轨迹"""
        return (1-t)**3 * p0 + 3*(1-t)**2*t * p1 + 3*(1-t)*t**2 * p2 + t**3 * p3

    def _easing_function(self, t, mode='easeOutQuad'):
        """缓动函数 - 模拟真实人类滑动的速度变化"""
        if mode == 'easeOutQuad':
            return t * (2 - t)
        elif mode == 'easeInOutCubic':
            return 4*t**3 if t < 0.5 else 1 - pow(-2*t + 2, 3) / 2
        elif mode == 'easeOutBack':
            c1 = 1.70158
            c3 = c1 + 1
            return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)
        else:
            return t

    def _generate_physics_trajectory(self, distance: float):
        """基于物理加速度模型生成轨迹 - 极速模式（增强随机性）
        
        优化策略：
        1. 极少轨迹点（5-8步）：快速完成
        2. 持续加速：一气呵成，不减速
        3. 确保超调50%以上：保证滑动到位
        4. 无回退：单向滑动
        5. 每次都有随机变化：步数、速度、曲线都随机
        
        注意：此方法已被参数化版本取代，保留用于兼容性
        """
        # 生成随机参数
        overshoot_ratio = random.uniform(2.0, 2.2)
        steps = random.randint(5, 8)
        base_delay = random.uniform(0.0002, 0.0006)
        acceleration_curve = random.uniform(1.3, 1.8)
        y_jitter_max = random.uniform(1, 3)
        
        # 调用参数化版本
        return self._generate_physics_trajectory_with_params(
            distance, overshoot_ratio, steps, base_delay,
            acceleration_curve, y_jitter_max
        )

    def generate_human_trajectory(self, distance: float, attempt: int = 1):
        """生成人类化滑动轨迹 - 只使用极速物理模型（带智能学习+失败后增加扰动）
        
        Args:
            distance: 滑动距离
            attempt: 当前尝试次数（从1开始），用于在失败后增加随机扰动
            
        🔧 优化说明（基于成功案例分析 + 机器学习策略）：
        - 成功超调比例: 1.79-2.05 (中位数1.97)
        - 成功步数: 6-8步
        - 成功延迟: 0.0003-0.0006秒
        - 成功加速曲线: 1.35-1.7 (中位数1.52)
        - 成功Y抖动: 1.3-2.55像素
        - 成功总耗时: 0.9-1.55秒
        
        🎰 当前重试策略：
        - 第1次优先利用历史成功参数
        - 第2次继续利用，但主动放慢节奏
        - 第3次切换到更果断的高收益分支，不再使用 slow_fallback
        """
        try:
            # 记录轨迹生成前的随机种子状态（用于分析）
            random_state_snapshot = random.getstate()[1][:5]  # 记录前5个随机状态
            
            # 🧠 尝试从历史成功数据中学习最优参数
            optimized_params = self._optimize_trajectory_params(reference_distance=distance)
            force_explore_threshold = _host.ML_STRATEGY_CONFIG.get("force_explore_after_failures", 2)
            slow_fallback_threshold = max(3, force_explore_threshold + 1)
            has_learning = optimized_params.get("learning_enabled") and optimized_params.get("history_count", 0) >= 3
            effective_ranges = self._get_effective_learning_ranges(optimized_params)
            bounds = effective_ranges["bounds"]

            use_exploration = False
            selected_strategy = None
            profile_name = "primary"

            if attempt >= slow_fallback_threshold:
                # 第 3 次及以后：优先使用 learned 变体（加大抖动），无学习数据时才轮换
                if has_learning:
                    if self._is_password_login_scene() and attempt >= max(4, self.slider_max_retries):
                        template_ranges = self._get_password_scene_final_retry_template(effective_ranges, bounds)
                        selected_strategy = "learned_password_template"
                        profile_name = "password_scene_final_template"
                        overshoot_ratio = random.uniform(template_ranges["overshoot"][0], template_ranges["overshoot"][1])
                        steps = random.randint(template_ranges["steps"][0], template_ranges["steps"][1])
                        base_delay = random.uniform(template_ranges["delay"][0], template_ranges["delay"][1])
                        acceleration_curve = random.uniform(template_ranges["curve"][0], template_ranges["curve"][1])
                        y_jitter_max = random.uniform(template_ranges["jitter"][0], template_ranges["jitter"][1])

                        logger.info(
                            f"【{self.pure_user_id}】🧷 第{attempt}次尝试，账密无头最终模板回放: "
                            f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                            f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                        )
                    else:
                        # 🔧 优化：第3次仍然使用学习参数，但加大抖动幅度以增加多样性
                        selected_strategy = "learned_with_jitter"
                        profile_name = "retry_learned_aggressive_jitter"

                        jitter_config = _host.ML_STRATEGY_CONFIG.get("param_jitter", {})
                        # 第3次使用更大的抖动幅度（原来的2倍）
                        overshoot_jitter = jitter_config.get("overshoot_ratio_jitter", 0.05) * 2.0

                        overshoot_ratio = random.uniform(effective_ranges["overshoot"][0], effective_ranges["overshoot"][1])
                        overshoot_ratio *= random.uniform(1 - overshoot_jitter, 1 + overshoot_jitter)
                        overshoot_ratio = max(1.01, min(bounds.get("max_overshoot_ratio", 1.18), overshoot_ratio))

                        # 步数和延迟也加大变化范围
                        steps_min = max(18, effective_ranges["steps"][0] - 3)
                        steps_max = min(42, effective_ranges["steps"][1] + 5)
                        steps = random.randint(steps_min, steps_max)

                        delay_min = max(0.004, effective_ranges["delay"][0] * 0.85)
                        delay_max = min(0.022, effective_ranges["delay"][1] * 1.5)
                        base_delay = random.uniform(delay_min, delay_max)

                        curve_min = max(1.2, effective_ranges["curve"][0] - 0.2)
                        curve_max = min(2.6, effective_ranges["curve"][1] + 0.2)
                        acceleration_curve = random.uniform(curve_min, curve_max)

                        jitter_min = max(0.8, effective_ranges["jitter"][0] - 0.3)
                        jitter_max = min(3.5, effective_ranges["jitter"][1] + 0.5)
                        y_jitter_max = random.uniform(jitter_min, jitter_max)

                        logger.info(
                            f"【{self.pure_user_id}】🛟 第{attempt}次尝试，使用学习参数(大抖动): "
                            f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                            f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                        )
                else:
                    if self._should_prefer_docker_conservative_profile(has_learning):
                        rotation_strategies = ["conservative", "standard"]
                    else:
                        rotation_strategies = ["aggressive", "standard"]
                    rotation_idx = (attempt - slow_fallback_threshold) % len(rotation_strategies)
                    selected_strategy = rotation_strategies[rotation_idx]
                    profile_name = f"retry_rotation_{selected_strategy}"

                    strategy_config = _host.ML_STRATEGY_CONFIG["strategies"][selected_strategy]
                    overshoot_ratio = random.uniform(*strategy_config["overshoot_ratio"])
                    steps = random.randint(*strategy_config["steps"])
                    base_delay = random.uniform(*strategy_config["base_delay"])
                    acceleration_curve = random.uniform(*strategy_config["acceleration_curve"])
                    y_jitter_max = random.uniform(*strategy_config["y_jitter_max"])

                    logger.info(
                        f"【{self.pure_user_id}】🛟 第{attempt}次尝试，轮换策略[{selected_strategy}]: "
                        f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                        f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                    )
            elif attempt == 2 and self._should_prefer_docker_conservative_profile(has_learning):
                selected_strategy = "conservative"
                profile_name = "docker_retry_conservative"

                overshoot_ratio = random.uniform(1.015, 1.045)
                steps = random.randint(32, 42)
                base_delay = random.uniform(0.011, 0.019)
                acceleration_curve = random.uniform(1.95, 2.30)
                y_jitter_max = random.uniform(0.9, 1.8)

                logger.info(
                    f"【{self.pure_user_id}】🧱 Docker第2次继续保守策略: "
                    f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                    f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                )
            elif attempt == 2 and has_learning:
                selected_strategy = "learned_with_jitter"
                profile_name = "retry_stabilized"

                jitter_config = _host.ML_STRATEGY_CONFIG.get("param_jitter", {})
                overshoot_jitter = jitter_config.get("overshoot_ratio_jitter", 0.05)

                overshoot_ratio = random.uniform(effective_ranges["overshoot"][0], effective_ranges["overshoot"][1])
                overshoot_ratio *= random.uniform(1 - overshoot_jitter, 1 + overshoot_jitter)
                overshoot_ratio = max(1.01, min(bounds.get("max_overshoot_ratio", 1.18), overshoot_ratio))

                steps_min = max(24, effective_ranges["steps"][0])
                steps_max = min(40, max(steps_min + 2, effective_ranges["steps"][1] + 5))
                if steps_max < steps_min:
                    steps_max = min(40, steps_min)
                steps = random.randint(steps_min, steps_max)

                delay_min = max(0.007, effective_ranges["delay"][0] * 1.10)
                delay_max = min(0.020, max(delay_min + 0.002, effective_ranges["delay"][1] * 1.35))
                base_delay = random.uniform(delay_min, delay_max)

                curve_min = max(1.45, effective_ranges["curve"][0] - 0.10)
                curve_max = min(2.40, max(curve_min + 0.15, effective_ranges["curve"][1] + 0.10))
                acceleration_curve = random.uniform(curve_min, curve_max)

                jitter_min = max(1.2, effective_ranges["jitter"][0])
                jitter_max = min(bounds.get("max_y_jitter", 3.5), max(jitter_min + 0.3, effective_ranges["jitter"][1] + 0.3))
                y_jitter_max = random.uniform(jitter_min, jitter_max)

                logger.info(
                    f"【{self.pure_user_id}】🧩 第2次尝试继续利用学习参数并放慢节奏: "
                    f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                    f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                )
            else:
                exploration_rate = _host.ML_STRATEGY_CONFIG.get("exploration_rate", 0.35)
                if self._should_force_docker_cold_start_conservative(attempt, has_learning):
                    conservative = _host.ML_STRATEGY_CONFIG["strategies"]["conservative"]
                    overshoot_ratio = random.uniform(*conservative["overshoot_ratio"])
                    steps = random.randint(*conservative["steps"])
                    base_delay = random.uniform(*conservative["base_delay"])
                    acceleration_curve = random.uniform(*conservative["acceleration_curve"])
                    y_jitter_max = random.uniform(*conservative["y_jitter_max"])
                    selected_strategy = "conservative"
                    profile_name = "docker_cold_start_conservative"
                    logger.info(
                        f"【{self.pure_user_id}】🧱 Docker冷启动优先保守策略: "
                        f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                        f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                    )
                elif not has_learning and random.random() < exploration_rate:
                    use_exploration = True
                    overshoot_ratio, steps, base_delay, acceleration_curve, y_jitter_max, selected_strategy = \
                        self._select_exploration_strategy(attempt)
                    profile_name = "cold_start_exploration"
                    logger.info(
                        f"【{self.pure_user_id}】🎯 冷启动探索策略[{selected_strategy}]: "
                        f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                        f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                    )
                elif has_learning:
                    logger.info(f"【{self.pure_user_id}】📐 利用模式：使用学习参数 "
                               f"(基于{optimized_params['history_count']}条记录)")

                    # 添加参数抖动（防止模式被识别）
                    jitter_config = _host.ML_STRATEGY_CONFIG.get("param_jitter", {})
                    overshoot_jitter = jitter_config.get("overshoot_ratio_jitter", 0.03)
                    
                    overshoot_ratio = random.uniform(effective_ranges["overshoot"][0], effective_ranges["overshoot"][1])
                    overshoot_ratio *= random.uniform(1 - overshoot_jitter, 1 + overshoot_jitter)
                    overshoot_ratio = max(1.01, min(bounds.get("max_overshoot_ratio", 1.18), overshoot_ratio))
                    
                    steps = random.randint(effective_ranges["steps"][0], effective_ranges["steps"][1])
                    base_delay = random.uniform(effective_ranges["delay"][0], effective_ranges["delay"][1])
                    acceleration_curve = random.uniform(effective_ranges["curve"][0], effective_ranges["curve"][1])
                    y_jitter_max = random.uniform(effective_ranges["jitter"][0], effective_ranges["jitter"][1])
                    
                    selected_strategy = "learned_with_jitter"
                    profile_name = "primary"
                    logger.info(f"【{self.pure_user_id}】🎯 应用学习参数(带抖动): 超调{(overshoot_ratio-1)*100:.1f}%, "
                               f"步数{steps}, 延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}")
                elif attempt == 1 and self._use_headless_stable_profile():
                    overshoot_ratio = random.uniform(1.03, 1.08)
                    steps = random.randint(23, 34)
                    base_delay = random.uniform(0.008, 0.0135)
                    acceleration_curve = random.uniform(1.68, 2.00)
                    y_jitter_max = random.uniform(1.35, 2.40)

                    selected_strategy = "headless_stable"
                    profile_name = "cold_start_headless_stable"
                    logger.info(
                        f"【{self.pure_user_id}】🎯 使用无头稳定画像策略: "
                        f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                        f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                    )
                else:
                    # 使用标准策略
                    standard = _host.ML_STRATEGY_CONFIG["strategies"]["standard"]
                    overshoot_ratio = random.uniform(standard["overshoot_ratio"][0], standard["overshoot_ratio"][1])
                    steps = random.randint(standard["steps"][0], standard["steps"][1])
                    base_delay = random.uniform(standard["base_delay"][0], standard["base_delay"][1])
                    acceleration_curve = random.uniform(standard["acceleration_curve"][0], standard["acceleration_curve"][1])
                    y_jitter_max = random.uniform(standard["y_jitter_max"][0], standard["y_jitter_max"][1])
                    selected_strategy = "standard"
                    profile_name = "cold_start_standard"
                    logger.info(f"【{self.pure_user_id}】📐 使用标准策略: 超调{(overshoot_ratio-1)*100:.1f}%, "
                               f"步数{steps}, 延迟{base_delay*1000:.1f}ms")
            
            # 生成轨迹（使用上面预生成的参数）
            trajectory = self._generate_physics_trajectory_with_params(
                distance, overshoot_ratio, steps, base_delay, 
                acceleration_curve, y_jitter_max
            )
            
            logger.debug(f"【{self.pure_user_id}】轨迹模式: 贝塞尔超调后回退，执行配置={selected_strategy}/{profile_name}")
            
            # 保存轨迹数据（包含所有随机参数）
            self.current_trajectory_data = {
                "distance": distance,
                "model": "physics_fast_learned" if optimized_params.get("learning_enabled") else "physics_fast",
                "browser_profile_id": self.profile_id,
                "headless": self.headless,
                "total_steps": len(trajectory),
                "trajectory_points": trajectory.copy(),
                "final_left_px": 0,
                "completion_used": False,
                "completion_steps": 0,
                # 新增：记录所有随机参数
                "random_params": {
                    "overshoot_ratio": overshoot_ratio,
                    "steps": steps,
                    "base_delay": base_delay,
                    "acceleration_curve": acceleration_curve,
                    "y_jitter_max": y_jitter_max,
                    "random_state_snapshot": list(random_state_snapshot),
                    "is_learned": optimized_params.get("learning_enabled", False),
                    # 🎰 新增：记录使用的策略名称
                    "strategy": selected_strategy if selected_strategy else "unknown",
                    "profile": profile_name,
                    "use_exploration": use_exploration,
                }
            }
            
            return trajectory
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】生成轨迹时出错: {str(e)}")
            return []

    def _generate_physics_trajectory_with_params(self, distance: float, 
                                                  overshoot_ratio: float,
                                                  steps: int,
                                                  base_delay: float,
                                                  acceleration_curve: float,
                                                  y_jitter_max: float):
        """使用指定参数生成物理轨迹（用于参数记录和复现）
        
        🔧 2025-12-25 重构：使用贝塞尔曲线+真实超调回退+连续Y轴抖动
        """
        trajectory = []
        
        # 尊重上层策略传入的步数，避免“选中的策略”和“实际执行轨迹”脱节
        # Fitts 定律动态步数：距离越长步数越多，距离越短步数越少
        # 基于策略传入的步数，再根据距离做 ±30% 的缩放
        fitts_factor = math.log2(max(1, distance / 50 + 1)) / math.log2(7)  # 归一化到 ~0.5-1.3
        fitts_steps = int(round(steps * max(0.7, min(1.3, fitts_factor))))
        actual_steps = max(18, min(45, fitts_steps))
        
        # 超调目标位置（先滑过，再回退）
        overshoot_target = distance * overshoot_ratio
        
        # === 阶段1：主滑动阶段（使用贝塞尔曲线） ===
        # 控制点设计：模拟人类手部加速-匀速-减速
        main_steps = int(actual_steps * 0.75)  # 75%用于主滑动
        
        # 贝塞尔控制点（三次贝塞尔）
        p0 = 0  # 起点
        p1 = overshoot_target * random.uniform(0.2, 0.35)  # 控制点1（早期加速）
        p2 = overshoot_target * random.uniform(0.7, 0.85)  # 控制点2（后期减速）
        p3 = overshoot_target  # 终点（超调位置）
        
        # Y轴使用 Perlin 噪声（非周期性连续平滑，比 sin 叠加更难被模式识别）
        y_seed1 = random.uniform(0, 1000)  # 低频噪声种子
        y_seed2 = random.uniform(0, 1000)  # 高频噪声种子
        y_freq1 = random.uniform(2.0, 4.0)  # 低频采样频率（手臂移动）
        y_freq2 = random.uniform(6.0, 10.0)  # 高频采样频率（手指颤抖）
        # 延迟也使用 Perlin 生成连续变化（同一次滑动中各点延迟相关联）
        delay_seed = random.uniform(0, 1000)
        
        prev_x = 0
        prev_y = 0
        
        for i in range(main_steps):
            # 进度 0->1，使用非线性进度模拟加速减速
            t = (i + 1) / main_steps
            
            # 使用ease-out曲线（开始快，结束慢）
            eased_t = 1 - (1 - t) ** acceleration_curve
            
            # 三次贝塞尔曲线计算X位置
            x = (1-eased_t)**3 * p0 + \
                3*(1-eased_t)**2 * eased_t * p1 + \
                3*(1-eased_t) * eased_t**2 * p2 + \
                eased_t**3 * p3
            
            # Perlin 噪声 Y 轴波动（叠加低频+高频，非周期性）
            y_low = _host.perlin_octaves_1d(t * y_freq1, octaves=2, seed_offset=y_seed1) * y_jitter_max * 0.65
            y_high = _host.perlin_noise_1d(t * y_freq2, seed_offset=y_seed2) * y_jitter_max * 0.35
            y = y_low + y_high + random.uniform(-0.2, 0.2)  # 微小随机噪声

            # Perlin 连续延迟：开始和结束慢，中间快，且相邻点延迟相关联
            speed_factor = math.sin(t * 3.14159)  # 基础速度包络仍用 sin（0->1->0）
            if speed_factor < 0.1:
                speed_factor = 0.1
            
            # 基础延迟 + 速度调整 + Perlin 连续抖动（相邻点的延迟有平滑关联）
            delay_jitter = 1.0 + _host.perlin_noise_1d(t * 5.0, seed_offset=delay_seed) * 0.15  # ±15% 连续波动
            delay = base_delay / speed_factor * delay_jitter
            
            # 中间可能有微小停顿（8%概率，模拟人类犹豫/调整）
            if 0.2 < t < 0.8 and random.random() < 0.08:
                delay += random.uniform(0.01, 0.03)
            
            # 添加微小位移抖动（生理性颤抖，±0.5px）
            x += random.uniform(-0.5, 0.5)
            
            trajectory.append((x, y, delay))
            prev_x, prev_y = x, y
        
        # === 阶段2：回退阶段（从超调位置回退到目标） ===
        # 5-10%的回退距离
        retreat_steps = int(actual_steps * 0.25)
        retreat_distance = overshoot_target - distance  # 需要回退的距离
        
        if retreat_steps > 0 and retreat_distance > 0:
            for i in range(retreat_steps):
                t = (i + 1) / retreat_steps
                
                # 回退使用ease-in-out（开始慢，中间快，结束慢）
                eased_t = t * t * (3 - 2 * t)  # smoothstep
                
                # 从超调位置回退到目标
                x = overshoot_target - retreat_distance * eased_t
                
                # Y轴继续波动
                y = prev_y * (1 - t) + random.uniform(-y_jitter_max * 0.3, y_jitter_max * 0.3)
                
                # 回退时速度更慢（人类精确调整时更谨慎）
                delay = base_delay * random.uniform(1.2, 1.8)
                
                # 微小位移抖动
                x += random.uniform(-0.3, 0.3)
                
                trajectory.append((x, y, delay))
                prev_x, prev_y = x, y
        
        # === 阶段3：最终微调（模拟人类精确对齐） ===
        # 随机添加1-3个微调点
        fine_tune_count = random.randint(1, 3)
        for _ in range(fine_tune_count):
            # 在目标位置附近做微小调整
            x = distance + random.uniform(-1.5, 1.5)
            y = random.uniform(-y_jitter_max * 0.2, y_jitter_max * 0.2)
            delay = base_delay * random.uniform(0.8, 1.5)
            trajectory.append((x, y, delay))
        
        # 确保最后一个点非常接近目标
        final_x = distance + random.uniform(-0.5, 0.5)
        final_y = random.uniform(-0.2, 0.2)
        trajectory.append((final_x, final_y, base_delay * random.uniform(0.5, 1.0)))
        
        logger.info(f"【{self.pure_user_id}】🎯 贝塞尔轨迹：{len(trajectory)}步，"
                   f"超调{(overshoot_ratio-1)*100:.0f}%→回退到目标，"
                   f"加速曲线^{acceleration_curve:.2f}")
        return trajectory

    def _optimize_trajectory_params(self, reference_distance: Optional[float] = None) -> Dict[str, Any]:
        """基于历史成功数据优化轨迹参数（增强版 - 智能学习）"""
        try:
            if not self.enable_learning:
                return self.trajectory_params
            
            history = self._get_learning_history_with_fallback(reference_distance=reference_distance)
            required_history_count = 2 if self._allow_small_sample_learning(history, reference_distance) else 3
            if len(history) < required_history_count:
                logger.info(f"【{self.pure_user_id}】历史成功数据不足({len(history)}条)，使用默认参数")
                return self.trajectory_params
            if required_history_count == 2:
                logger.info(f"【{self.pure_user_id}】成功样本虽仅2条，但同画像且同距离区间，直接启用学习参数")
            
            # 🎯 新版参数学习：基于新的随机参数结构
            # 收集新版参数（overshoot_ratio, acceleration_curve等）
            overshoot_ratios = [record.get("overshoot_ratio", 2.0) for record in history if record.get("overshoot_ratio")]
            base_delays = [record.get("base_delay", 0.0004) for record in history if record.get("base_delay")]
            acceleration_curves = [record.get("acceleration_curve", 1.5) for record in history if record.get("acceleration_curve")]
            y_jitter_maxs = [record.get("y_jitter_max", 2.0) for record in history if record.get("y_jitter_max")]
            total_steps_list = [record.get("total_steps", 6) for record in history]
            
            # 计算平均值和标准差
            def safe_avg(values):
                return sum(values) / len(values) if values else 0
            
            def safe_std(values):
                if len(values) < 2:
                    return 0
                avg = safe_avg(values)
                variance = sum((x - avg) ** 2 for x in values) / len(values)
                return variance ** 0.5
            
            def safe_percentile(values, percentile):
                """计算百分位数"""
                if not values:
                    return 0
                sorted_values = sorted(values)
                index = int(len(sorted_values) * percentile)
                return sorted_values[min(index, len(sorted_values) - 1)]
            
            # 🧠 智能学习策略（优化版 - 避免过度收敛）：
            # 1. 使用成功记录的中位数作为中心值（更稳定）
            # 2. 使用标准差的0.5倍作为范围（保持随机性）
            # 3. 🔧 应用边界限制，防止学习到极端值
            # 4. 🔧 强制最小范围宽度，保持探索能力
            
            # 获取边界限制
            bounds = _host.ML_STRATEGY_CONFIG.get("learning_bounds", {})
            min_overshoot = bounds.get("min_overshoot_ratio", 1.75)
            max_overshoot = bounds.get("max_overshoot_ratio", 2.12)
            min_y_jitter = bounds.get("min_y_jitter", 0.8)
            max_y_jitter = bounds.get("max_y_jitter", 3.0)
            
            # 学习超调比例（关键参数）
            # 🔧 2025-12-25：适配新的贝塞尔曲线轨迹，超调比例改为真实百分比（1.01-1.15）
            if overshoot_ratios:
                overshoot_median = safe_percentile(overshoot_ratios, 0.5)
                overshoot_std = safe_std(overshoot_ratios)
                
                # 🔧 关键修复：如果中位数超过上限，强制拉回到合理范围
                if overshoot_median > max_overshoot:
                    logger.warning(f"【{self.pure_user_id}】⚠️ 学习到的超调比例中位数({overshoot_median:.2f})过高，"
                                   f"强制调整到{max_overshoot}")
                    overshoot_median = max_overshoot - 0.02
                elif overshoot_median < min_overshoot:
                    logger.warning(f"【{self.pure_user_id}】⚠️ 学习到的超调比例中位数({overshoot_median:.2f})过低，"
                                   f"强制调整到{min_overshoot}")
                    overshoot_median = min_overshoot + 0.02
                
                # 应用边界限制
                overshoot_min = max(min_overshoot, overshoot_median - max(overshoot_std * 0.3, 0.03))
                overshoot_max = min(max_overshoot, overshoot_median + max(overshoot_std * 0.3, 0.03))
                
                # 🔧 确保最小范围宽度（至少0.04的差距，即4%）
                if overshoot_max - overshoot_min < 0.04:
                    overshoot_min = max(min_overshoot, overshoot_median - 0.02)
                    overshoot_max = min(max_overshoot, overshoot_median + 0.02)
                
                learned_overshoot = (overshoot_min, overshoot_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优超调比例: {overshoot_min:.2f}-{overshoot_max:.2f}x "
                           f"(中位数:{overshoot_median:.2f}, 边界限制:{min_overshoot}-{max_overshoot})")
            else:
                learned_overshoot = (1.03, 1.08)  # 🔧 新默认值：3-8%超调
            
            # 学习基础延迟（影响速度感知）
            # 🔧 2025-12-25：改为毫秒级延迟（0.004-0.015秒）
            if base_delays:
                delay_median = safe_percentile(base_delays, 0.5)
                delay_std = safe_std(base_delays)
                delay_min = max(0.003, delay_median - delay_std * 0.4)
                delay_max = min(0.020, delay_median + delay_std * 0.4)
                
                # 🔧 确保最小范围宽度（至少3ms的差距）
                if delay_max - delay_min < 0.003:
                    delay_min = max(0.003, delay_median - 0.0015)
                    delay_max = min(0.020, delay_median + 0.0015)
                
                learned_delay = (delay_min, delay_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优延迟: {delay_min*1000:.1f}-{delay_max*1000:.1f}ms "
                           f"(中位数:{delay_median*1000:.1f}ms)")
            else:
                learned_delay = (0.006, 0.012)  # 🔧 新默认值：6-12ms
            
            # 学习加速曲线（影响轨迹形状）
            # 🔧 2025-12-25：适配贝塞尔曲线的ease-out指数
            if acceleration_curves:
                curve_median = safe_percentile(acceleration_curves, 0.5)
                curve_std = safe_std(acceleration_curves)
                curve_min = max(1.3, curve_median - curve_std * 0.3)
                curve_max = min(2.5, curve_median + curve_std * 0.3)
                
                # 🔧 确保最小范围宽度（至少0.2的差距）
                if curve_max - curve_min < 0.2:
                    curve_min = max(1.3, curve_median - 0.1)
                    curve_max = min(2.5, curve_median + 0.1)
                
                learned_curve = (curve_min, curve_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优加速曲线: ^{curve_min:.2f}-^{curve_max:.2f} "
                           f"(中位数:^{curve_median:.2f})")
            else:
                learned_curve = (1.6, 2.0)  # 🔧 新默认值
            
            # 学习Y轴抖动（影响真实感）
            if y_jitter_maxs:
                jitter_median = safe_percentile(y_jitter_maxs, 0.5)
                jitter_std = safe_std(y_jitter_maxs)
                
                # 🔧 关键修复：如果中位数超过边界，强制拉回
                if jitter_median > max_y_jitter:
                    logger.warning(f"【{self.pure_user_id}】⚠️ 学习到的Y抖动中位数({jitter_median:.1f})过高，"
                                   f"强制调整到{max_y_jitter}")
                    jitter_median = max_y_jitter - 0.3
                elif jitter_median < min_y_jitter:
                    jitter_median = min_y_jitter + 0.3
                
                # 应用边界限制
                jitter_min = max(min_y_jitter, jitter_median - max(jitter_std * 0.4, 0.4))
                jitter_max = min(max_y_jitter, jitter_median + max(jitter_std * 0.4, 0.4))
                
                # 🔧 确保最小范围宽度（至少0.6的差距）
                if jitter_max - jitter_min < 0.6:
                    jitter_min = max(min_y_jitter, jitter_median - 0.3)
                    jitter_max = min(max_y_jitter, jitter_median + 0.3)
                
                learned_jitter = (jitter_min, jitter_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优Y抖动: {jitter_min:.1f}-{jitter_max:.1f}px "
                           f"(中位数:{jitter_median:.1f}px, 边界限制:{min_y_jitter}-{max_y_jitter})")
            else:
                learned_jitter = (1.5, 2.2)  # 🔧 新默认值
            
            # 学习步数范围
            # 这里的步数会直接传递给新轨迹生成器，避免策略与执行脱节
            if total_steps_list:
                steps_median = int(safe_percentile(total_steps_list, 0.5))
                steps_std = safe_std(total_steps_list)
                steps_min = max(20, int(steps_median - steps_std * 0.5))
                steps_max = min(40, int(steps_median + steps_std * 0.5))
                
                # 🔧 确保最小范围宽度（至少5步的差距）
                if steps_max - steps_min < 5:
                    steps_min = max(20, steps_median - 2)
                    steps_max = min(40, steps_median + 3)

                # 防御性兜底：历史样本中位数可能超过上限，导致区间反转
                if steps_min > steps_max:
                    clamped_median = min(40, max(20, steps_median))
                    steps_min = max(20, clamped_median - 3)
                    steps_max = min(40, max(steps_min + 2, clamped_median))

                learned_steps = (steps_min, steps_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优步数: {steps_min}-{steps_max}步 "
                           f"(中位数:{steps_median}步)")
            else:
                learned_steps = (22, 30)  # 🔧 新默认值
            
            # 🎯 新增：学习滑动行为参数（18种行为参数）
            logger.info(f"【{self.pure_user_id}】📚 开始学习滑动行为参数...")
            
            # 收集所有成功记录的滑动行为数据
            slide_behaviors = [record.get("slide_behavior", {}) for record in history if record.get("slide_behavior")]
            
            learned_behavior = {}
            
            if slide_behaviors:
                # 学习接近偏移
                approach_offset_x_list = [b.get("approach_offset_x", -20) for b in slide_behaviors if b.get("approach_offset_x")]
                if approach_offset_x_list:
                    median = safe_percentile(approach_offset_x_list, 0.5)
                    std = safe_std(approach_offset_x_list)
                    x_min = max(-45, median - std * 0.5)
                    x_max = min(-5, median + std * 0.5)
                    # 🔧 确保最小范围宽度（至少10px）
                    if x_max - x_min < 10:
                        x_min = max(-45, median - 5)
                        x_max = min(-5, median + 5)
                    learned_behavior["approach_offset_x"] = (x_min, x_max)
                
                approach_offset_y_list = [b.get("approach_offset_y", 0) for b in slide_behaviors if b.get("approach_offset_y")]
                if approach_offset_y_list:
                    median = safe_percentile(approach_offset_y_list, 0.5)
                    std = safe_std(approach_offset_y_list)
                    y_min = max(-25, median - std * 0.5)
                    y_max = min(25, median + std * 0.5)
                    # 🔧 确保最小范围宽度（至少10px）
                    if y_max - y_min < 10:
                        y_min = max(-25, median - 5)
                        y_max = min(25, median + 5)
                    learned_behavior["approach_offset_y"] = (y_min, y_max)
                
                # 学习接近步数
                approach_steps_list = [b.get("approach_steps", 7) for b in slide_behaviors if b.get("approach_steps")]
                if approach_steps_list:
                    median = int(safe_percentile(approach_steps_list, 0.5))
                    std = safe_std(approach_steps_list)
                    steps_min = max(3, int(median - std * 0.5))
                    steps_max = min(15, int(median + std * 0.5))
                    # 🔧 确保最小范围宽度（至少3步）
                    if steps_max - steps_min < 3:
                        steps_min = max(3, median - 2)
                        steps_max = min(15, median + 2)
                    learned_behavior["approach_steps"] = (steps_min, steps_max)
                
                # 学习停顿时间
                approach_pause_list = [b.get("approach_pause", 0.2) for b in slide_behaviors if b.get("approach_pause")]
                if approach_pause_list:
                    median = safe_percentile(approach_pause_list, 0.5)
                    std = safe_std(approach_pause_list)
                    pause_min = max(0.05, median - std * 0.4)
                    pause_max = min(0.5, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.1秒）
                    if pause_max - pause_min < 0.1:
                        pause_min = max(0.05, median - 0.05)
                        pause_max = min(0.5, median + 0.05)
                    learned_behavior["approach_pause"] = (pause_min, pause_max)
                
                precision_steps_list = [b.get("precision_steps", 5) for b in slide_behaviors if b.get("precision_steps")]
                if precision_steps_list:
                    median = int(safe_percentile(precision_steps_list, 0.5))
                    std = safe_std(precision_steps_list)
                    steps_min = max(2, int(median - std * 0.5))
                    steps_max = min(10, int(median + std * 0.5))
                    # 🔧 确保最小范围宽度（至少2步）
                    if steps_max - steps_min < 2:
                        steps_min = max(2, median - 1)
                        steps_max = min(10, median + 1)
                    learned_behavior["precision_steps"] = (steps_min, steps_max)
                
                precision_pause_list = [b.get("precision_pause", 0.15) for b in slide_behaviors if b.get("precision_pause")]
                if precision_pause_list:
                    median = safe_percentile(precision_pause_list, 0.5)
                    std = safe_std(precision_pause_list)
                    pause_min = max(0.03, median - std * 0.4)
                    pause_max = min(0.4, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.08秒）
                    if pause_max - pause_min < 0.08:
                        pause_min = max(0.03, median - 0.04)
                        pause_max = min(0.4, median + 0.04)
                    learned_behavior["precision_pause"] = (pause_min, pause_max)
                
                # 学习悬停概率
                skip_hover_list = [b.get("skip_hover", False) for b in slide_behaviors if "skip_hover" in b]
                if skip_hover_list:
                    skip_rate = sum(1 for x in skip_hover_list if x) / len(skip_hover_list)
                    learned_behavior["skip_hover_rate"] = skip_rate
                
                hover_pause_list = [b.get("hover_pause", 0.2) for b in slide_behaviors if b.get("hover_pause")]
                if hover_pause_list:
                    median = safe_percentile(hover_pause_list, 0.5)
                    std = safe_std(hover_pause_list)
                    pause_min = max(0.03, median - std * 0.4)
                    pause_max = min(0.5, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.1秒）
                    if pause_max - pause_min < 0.1:
                        pause_min = max(0.03, median - 0.05)
                        pause_max = min(0.5, median + 0.05)
                    learned_behavior["hover_pause"] = (pause_min, pause_max)
                
                # 学习按下停顿
                pre_down_list = [b.get("pre_down_pause", 0.1) for b in slide_behaviors if b.get("pre_down_pause")]
                if pre_down_list:
                    median = safe_percentile(pre_down_list, 0.5)
                    std = safe_std(pre_down_list)
                    pause_min = max(0.01, median - std * 0.4)
                    pause_max = min(0.25, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.05秒）
                    if pause_max - pause_min < 0.05:
                        pause_min = max(0.01, median - 0.025)
                        pause_max = min(0.25, median + 0.025)
                    learned_behavior["pre_down_pause"] = (pause_min, pause_max)
                
                post_down_list = [b.get("post_down_pause", 0.1) for b in slide_behaviors if b.get("post_down_pause")]
                if post_down_list:
                    median = safe_percentile(post_down_list, 0.5)
                    std = safe_std(post_down_list)
                    pause_min = max(0.01, median - std * 0.4)
                    pause_max = min(0.25, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.05秒）
                    if pause_max - pause_min < 0.05:
                        pause_min = max(0.01, median - 0.025)
                        pause_max = min(0.25, median + 0.025)
                    learned_behavior["post_down_pause"] = (pause_min, pause_max)

                server_wait_list = [b.get("server_judge_wait", 0) for b in slide_behaviors if b.get("server_judge_wait")]
                if server_wait_list:
                    median = safe_percentile(server_wait_list, 0.5)
                    std = safe_std(server_wait_list)
                    wait_min = max(0.8, median - max(std * 0.4, 0.3))
                    wait_max = min(15.0, median + max(std * 0.4, 0.3))
                    if wait_max - wait_min < 0.6:
                        wait_min = max(0.8, median - 0.3)
                        wait_max = min(15.0, median + 0.3)
                    learned_behavior["server_judge_wait"] = (wait_min, wait_max)

                logger.info(f"【{self.pure_user_id}】📚 成功学习{len(learned_behavior)}个滑动行为参数")
            
            # 基于完整轨迹数据的学习
            completion_usage_rate = 0
            avg_completion_steps = 0
            
            if len(history) > 0:
                # 计算补全使用率
                completion_used_count = sum(1 for record in history if record.get("completion_used", False))
                completion_usage_rate = completion_used_count / len(history)
                
                # 计算平均补全步数
                completion_steps_list = [record.get("completion_steps", 0) for record in history if record.get("completion_used", False)]
                if completion_steps_list:
                    avg_completion_steps = sum(completion_steps_list) / len(completion_steps_list)
            
            # 构建优化后的参数（新版结构）
            optimized_params = {
                # 新版参数（基于学习结果）
                "learned_overshoot_range": learned_overshoot,
                "learned_delay_range": learned_delay,
                "learned_curve_range": learned_curve,
                "learned_jitter_range": learned_jitter,
                "learned_steps_range": learned_steps,
                # 🎯 新增：学习到的滑动行为参数
                "learned_behavior": learned_behavior,
                # 旧版参数（保留兼容性）
                "total_steps_range": learned_steps,
                "base_delay_range": learned_delay,
                "jitter_x_range": [0, 1],
                "jitter_y_range": [0, 1],
                "slow_factor_range": [10, 15],
                "acceleration_phase": 1.0,
                "fast_phase": 1.0,
                "slow_start_ratio_base": learned_overshoot[0],
                # 学习统计
                "completion_usage_rate": completion_usage_rate,
                "avg_completion_steps": avg_completion_steps,
                "learning_enabled": True,
                "history_count": len(history),
                "learning_version": "2.0"  # 标记为新版学习算法
            }
            
            logger.info(f"【{self.pure_user_id}】基于{len(history)}条成功记录优化轨迹参数: 步数{optimized_params['total_steps_range']}, 延迟{optimized_params['base_delay_range']}")

            return optimized_params
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】优化轨迹参数失败: {e}")
            return self.trajectory_params

    def _get_effective_learning_ranges(self, optimized_params: Dict[str, Any]) -> Dict[str, Tuple[float, float]]:
        """统一整理学习参数边界，确保不同重试分支使用一致口径"""
        bounds = _host.ML_STRATEGY_CONFIG.get("learning_bounds", {})

        learned_overshoot = optimized_params.get("learned_overshoot_range", (1.03, 1.08))
        learned_overshoot = (
            max(bounds.get("min_overshoot_ratio", 1.01), learned_overshoot[0]),
            min(bounds.get("max_overshoot_ratio", 1.15), learned_overshoot[1])
        )

        learned_delay = optimized_params.get("learned_delay_range", (0.006, 0.012))
        learned_curve = optimized_params.get("learned_curve_range", (1.6, 2.0))

        learned_jitter = optimized_params.get("learned_jitter_range", (1.5, 2.2))
        learned_jitter = (
            max(bounds.get("min_y_jitter", 1.0), learned_jitter[0]),
            min(bounds.get("max_y_jitter", 3.0), learned_jitter[1])
        )

        learned_steps = optimized_params.get("learned_steps_range", (22, 30))
        learned_steps = (
            max(20, min(40, int(learned_steps[0]))),
            max(20, min(40, int(learned_steps[1]))),
        )
        if learned_steps[0] > learned_steps[1]:
            learned_steps = (learned_steps[1], learned_steps[0])
        if learned_steps[1] - learned_steps[0] < 2:
            learned_steps = (learned_steps[0], min(40, learned_steps[0] + 2))

        return {
            "overshoot": learned_overshoot,
            "delay": learned_delay,
            "curve": learned_curve,
            "jitter": learned_jitter,
            "steps": learned_steps,
            "bounds": bounds,
        }

    def _select_exploration_strategy(self, attempt: int):
        """🎰 探索策略选择（机器学习多臂老虎机思想 + 自适应权重）
        
        根据尝试次数和动态权重选择不同的策略
        
        Returns:
            tuple: (overshoot_ratio, steps, base_delay, acceleration_curve, y_jitter_max, strategy_name)
        """
        strategies = _host.ML_STRATEGY_CONFIG.get("strategies", {})
        
        # 🤖 使用自适应策略管理器获取动态权重
        try:
            weights = _host.adaptive_strategy_manager.get_dynamic_weights(attempt)
            logger.debug(f"【{self.pure_user_id}】🤖 使用自适应权重: "
                        f"保守={weights.get('conservative', 0)*100:.1f}%, "
                        f"标准={weights.get('standard', 0)*100:.1f}%, "
                        f"激进={weights.get('aggressive', 0)*100:.1f}%")
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】获取动态权重失败: {e}，使用默认权重")
            # 回退到静态权重
            if attempt <= 2:
                weights = {"conservative": 0.18, "standard": 0.52, "aggressive": 0.30}
            elif attempt == 3:
                weights = {"conservative": 0.12, "standard": 0.38, "aggressive": 0.50}
            else:
                weights = {"conservative": 0.10, "standard": 0.30, "aggressive": 0.60}
        
        # 按权重随机选择策略
        rand_val = random.random()
        cumulative = 0
        selected_name = "standard"
        
        for name, weight in weights.items():
            cumulative += weight
            if rand_val <= cumulative:
                selected_name = name
                break
        
        strategy = strategies.get(selected_name, strategies["standard"])
        
        # 从选中的策略中随机生成参数
        overshoot_ratio = random.uniform(strategy["overshoot_ratio"][0], strategy["overshoot_ratio"][1])
        steps = random.randint(strategy["steps"][0], strategy["steps"][1])
        base_delay = random.uniform(strategy["base_delay"][0], strategy["base_delay"][1])
        acceleration_curve = random.uniform(strategy["acceleration_curve"][0], strategy["acceleration_curve"][1])
        y_jitter_max = random.uniform(strategy["y_jitter_max"][0], strategy["y_jitter_max"][1])
        
        # 添加额外的随机扰动（防止模式识别）
        jitter_config = _host.ML_STRATEGY_CONFIG.get("param_jitter", {})
        
        # 对超调比例添加随机扰动
        overshoot_jitter = jitter_config.get("overshoot_ratio_jitter", 0.08)
        overshoot_ratio *= random.uniform(1 - overshoot_jitter/2, 1 + overshoot_jitter/2)
        
        # 对延迟添加随机扰动
        delay_jitter = jitter_config.get("delay_jitter", 0.12)
        base_delay *= random.uniform(1 - delay_jitter/2, 1 + delay_jitter/2)
        
        # 对加速曲线添加随机扰动
        curve_jitter = jitter_config.get("curve_jitter", 0.08)
        acceleration_curve *= random.uniform(1 - curve_jitter/2, 1 + curve_jitter/2)
        
        # 🔧 2025-12-25：确保参数在新的合理范围内
        bounds = _host.ML_STRATEGY_CONFIG.get("learning_bounds", {})
        overshoot_ratio = max(bounds.get("min_overshoot_ratio", 1.01), 
                              min(bounds.get("max_overshoot_ratio", 1.15), overshoot_ratio))
        y_jitter_max = max(bounds.get("min_y_jitter", 1.0), 
                           min(bounds.get("max_y_jitter", 3.0), y_jitter_max))
        base_delay = max(0.003, min(0.020, base_delay))  # 3-20ms
        acceleration_curve = max(1.3, min(2.5, acceleration_curve))
        
        return overshoot_ratio, steps, base_delay, acceleration_curve, y_jitter_max, selected_name

    def _stable_number(self, namespace: str) -> int:
        digest = hashlib.sha256(f"{self.pure_user_id}:{namespace}".encode("utf-8")).hexdigest()
        return int(digest[:12], 16)

    def _check_date_validity(self) -> bool:
        """保留接口兼容，但不再做日期限制。"""
        logger.info(f"【{self.pure_user_id}】日期校验已禁用，直接放行")
        return True


class SliderHarvestMixin:
    """结果收割：Cookie 快照/稳定化/落盘与 mtop 预热探针。"""

    def _update_current_result_meta(
        self,
        status: str,
        attempt: Optional[int] = None,
        cookie_refresh_confirmed: Optional[bool] = None,
        soft_success: bool = False,
        note: Optional[str] = None,
    ):
        if not hasattr(self, "current_trajectory_data"):
            return

        result = self.current_trajectory_data.setdefault("verification_result", {})
        result.update({
            "status": status,
            "attempt": attempt,
            "soft_success": soft_success,
            "cookie_refresh_confirmed": cookie_refresh_confirmed,
            "feedback": dict(self.last_verification_feedback or {}),
            "profile_id": self.profile_id,
            "headless": self.headless,
            "updated_at": _host.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        if note:
            result["note"] = note

    def _collect_runtime_debug_info(self, search_target=None) -> Dict[str, Any]:
        runtime_targets = []
        if search_target is not None:
            runtime_targets.append(("target", search_target))
        if self.page is not None and self.page is not search_target:
            runtime_targets.append(("page", self.page))

        if not runtime_targets:
            return {}

        script = """
            () => {
                const pickText = (selector) => {
                    const node = document.querySelector(selector);
                    if (!node) {
                        return '';
                    }
                    return (node.innerText || node.textContent || '').trim();
                };

                const brands = navigator.userAgentData && Array.isArray(navigator.userAgentData.brands)
                    ? navigator.userAgentData.brands
                    : [];

                return {
                    href: location.href,
                    title: document.title,
                    readyState: document.readyState,
                    userAgent: navigator.userAgent,
                    webdriver: navigator.webdriver,
                    languages: Array.from(navigator.languages || []),
                    platform: navigator.platform,
                    vendor: navigator.vendor,
                    brands,
                    hasNocaptcha: !!document.querySelector('#nocaptcha'),
                    hasSliderButton: !!document.querySelector('#nc_1_n1z'),
                    hasSliderTrack: !!document.querySelector('#nc_1_n1t'),
                    errorText: pickText('.errloading')
                        || pickText('.sm-btn-fail')
                        || pickText('.captcha-tips')
                        || pickText('#nc_1__scale_text'),
                    ncFailCode: window.ncFailCode || '',
                    ncFailCodeList: Array.isArray(window.ncFailCodeList) ? window.ncFailCodeList.slice(-5) : [],
                    hasAWSC: !!window.AWSC,
                    hasAwscEt: !!window.__awsc_et__,
                    hasNC: !!window.nc,
                };
            }
        """

        debug_info: Dict[str, Any] = {}
        for target_name, runtime_target in runtime_targets:
            try:
                runtime_info = runtime_target.evaluate(script)
                if isinstance(runtime_info, dict):
                    debug_info[target_name] = runtime_info
            except Exception as e:
                debug_info[target_name] = {"error": str(e)}

        return debug_info

    def _collect_process_tree(self, root_pid: int) -> List[int]:
        """收集给定 PID 的全部子孙进程，避免残留 Chromium 进程树。"""
        try:
            output = subprocess.check_output(
                ["ps", "-eo", "pid=,ppid="],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return [root_pid]

        parent_map: Dict[int, List[int]] = {}
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except Exception:
                continue
            parent_map.setdefault(ppid, []).append(pid)

        to_visit = [root_pid]
        seen = set()
        ordered: List[int] = []
        while to_visit:
            pid = to_visit.pop()
            if pid in seen:
                continue
            seen.add(pid)
            ordered.append(pid)
            to_visit.extend(parent_map.get(pid, []))
        return ordered

    def _collect_page_text_for_detection(self, page) -> str:
        """读取页面主体文案，用于识别验证页是否已经超时或失效。"""
        if not page:
            return ''

        try:
            visible_text = page.inner_text('body', timeout=1500)
            if visible_text:
                return str(visible_text)[:20000]
        except Exception:
            pass

        try:
            content_text = page.text_content('body', timeout=1500)
            if content_text:
                return str(content_text)[:20000]
        except Exception:
            pass

        return ''

    def _collect_verification_target_text(self, target, fallback_page=None) -> str:
        if target:
            try:
                target_text = self._read_frame_text_for_detection(target)
                if target_text:
                    return target_text
            except Exception:
                pass

        if fallback_page and fallback_page is not target:
            try:
                page_text = self._collect_page_text_for_detection(fallback_page)
                if page_text:
                    return page_text
            except Exception:
                pass

        return ''

    def _build_browser_mtop_probe_requests(self, cookies_dict: Dict[str, str]) -> List[Dict[str, str]]:
        """构造登录成功后的浏览器侧业务预热探测请求。"""
        token = str((cookies_dict or {}).get('_m_h5_tk') or '').split('_')[0]
        user_id = str((cookies_dict or {}).get('unb') or '').strip()
        if not token or not user_id:
            return []

        common_params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }
        probes: List[Dict[str, str]] = []

        token_ts = str(int(time.time() * 1000))
        token_data = json.dumps(
            {
                "appKey": "444e9908a51d1cb236a27862abc769c9",
                "deviceId": _host.generate_cookie_verification_device_id(user_id),
            },
            separators=(',', ':'),
            ensure_ascii=False,
        )
        token_params = dict(common_params)
        token_params.update({
            't': token_ts,
            'api': 'mtop.taobao.idlemessage.pc.login.token',
            'dangerouslySetWindvaneParams': '%5Bobject%20Object%5D',
            'smToken': 'token',
            'queryToken': 'sm',
            'sm': 'sm',
            'sign': _host.build_cookie_verification_sign(token_ts, token, token_data),
        })
        probes.append({
            'name': 'login_token_fetch',
            'url': (
                "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/?"
                + _host.urlencode(token_params)
            ),
            'body': f"data={_host.quote_plus(token_data)}",
        })

        user_ts = str(int(time.time() * 1000))
        user_data = '{}'
        user_params = dict(common_params)
        user_params.update({
            't': user_ts,
            'api': 'mtop.taobao.idlemessage.pc.loginuser.get',
            'sign': _host.build_cookie_verification_sign(user_ts, token, user_data),
        })
        probes.append({
            'name': 'login_user_fetch',
            'url': (
                "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.loginuser.get/1.0/?"
                + _host.urlencode(user_params)
            ),
            'body': f"data={_host.quote_plus(user_data)}",
        })

        return probes

    def _perform_browser_cookie_warmup_probes(
        self,
        context,
        page,
        scene: str,
        initial_cookies: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """在浏览器上下文中主动探测业务接口，尝试逼出延迟下发的关键 Cookie。"""
        if not context or not page:
            return initial_cookies or {}

        self.last_browser_cookie_warmup_verification_hint = None
        best_cookies = dict(initial_cookies or self._snapshot_context_cookies(context, page=page))
        best_missing = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not best_cookies.get(key)
        ]
        probe_requests = self._build_browser_mtop_probe_requests(best_cookies)
        if not probe_requests:
            logger.info(f"【{self.pure_user_id}】{scene}浏览器业务预热跳过：缺少 _m_h5_tk 或 unb")
            return best_cookies

        logger.info(
            f"【{self.pure_user_id}】{scene}标准稳定化后仍缺少关键Cookie，开始浏览器业务预热: "
            f"missing_protected_fields={best_missing}"
        )

        for probe in probe_requests:
            probe_name = probe.get('name') or 'unknown_probe'
            probe_result = {}
            try:
                logger.info(f"【{self.pure_user_id}】{scene}浏览器业务预热探测: {probe_name}")
                probe_result = self._execute_browser_cookie_warmup_probe(context, page, probe)
                if isinstance(probe_result, dict):
                    summary = str(
                        probe_result.get('error')
                        or probe_result.get('text')
                        or ''
                    ).replace('\n', ' ')[:220]
                    logger.info(
                        f"【{self.pure_user_id}】{scene}浏览器业务预热结果[{probe_name}]: "
                        f"status={probe_result.get('status')} ok={probe_result.get('ok')} summary={summary}"
                    )
                    response_cookie_updates = probe_result.get('set_cookie_updates') or {}
                    if response_cookie_updates:
                        logger.info(
                            f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]响应补充Cookie: "
                            f"{sorted(response_cookie_updates.keys())}"
                        )
                    if probe_result.get('timed_out'):
                        logger.warning(
                            f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]超时中止，"
                            f"timeout_ms={probe_result.get('timeout_ms')}"
                        )
                    if (
                        "FAIL_SYS_SESSION_EXPIRED" in summary or
                        "FAIL_SYS_USER_VALIDATE" in summary
                    ):
                        self.last_browser_cookie_warmup_session_unready = True
                        logger.warning(
                            f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]仍提示服务端Session未就绪"
                        )
                    verification_hint = self._extract_browser_cookie_warmup_verification_hint(
                        probe_name,
                        probe_result,
                    )
                    if verification_hint:
                        self.last_browser_cookie_warmup_verification_hint = verification_hint
                        logger.warning(
                            f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]返回了后续验证提示: "
                            f"{verification_hint.get('verification_url')}"
                        )
            except Exception as probe_e:
                logger.warning(f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]失败: {probe_e}")
                continue

            time.sleep(1.0)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            time.sleep(0.5)

            current_cookies = self._snapshot_context_cookies(context, page=page)
            response_cookie_updates = probe_result.get('set_cookie_updates') or {}
            if response_cookie_updates:
                current_cookies = dict(current_cookies or {})
                for cookie_name, cookie_value in response_cookie_updates.items():
                    if cookie_name and cookie_value and not current_cookies.get(cookie_name):
                        current_cookies[cookie_name] = cookie_value
            current_missing = [
                key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                if not current_cookies.get(key)
            ]
            self._log_cookie_snapshot_integrity(current_cookies, f"{scene}业务预热[{probe_name}]")

            if current_cookies and len(current_missing) < len(best_missing):
                best_cookies = current_cookies
                best_missing = current_missing
                logger.info(
                    f"【{self.pure_user_id}】{scene}浏览器业务预热后关键Cookie缺失减少到 "
                    f"{len(best_missing)} 个: {best_missing}"
                )

            if not best_missing:
                break

        if best_cookies.get('havana_lgc2_77'):
            self.last_browser_cookie_warmup_verification_hint = None

        return best_cookies

    def _extract_browser_cookie_warmup_verification_hint(
        self,
        probe_name: str,
        probe_result: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(probe_result, dict):
            return None

        raw_text = str(probe_result.get('text') or '').strip()
        if not raw_text:
            return None

        try:
            payload = json.loads(raw_text)
        except Exception:
            return None

        ret_items = payload.get('ret')
        if isinstance(ret_items, list):
            ret_values = [str(item) for item in ret_items if item is not None]
        elif ret_items is None:
            ret_values = []
        else:
            ret_values = [str(ret_items)]
        ret_summary = " ".join(ret_values)

        data_payload = payload.get('data')
        if not isinstance(data_payload, dict):
            data_payload = {}

        verification_url = str(data_payload.get('url') or '').strip()
        verification_url_lower = verification_url.lower()
        ret_hit = (
            'FAIL_SYS_USER_VALIDATE' in ret_summary or
            'FAIL_SYS_SESSION_EXPIRED' in ret_summary
        )
        url_hit = any(
            token in verification_url_lower
            for token in (
                'punish',
                'x5step=2',
                'action=captcha',
                'purecaptcha',
                'identity_verify',
                '/iv/',
                'qrcode',
                'scan',
            )
        )
        if not verification_url or not (ret_hit or url_hit):
            return None

        verification_type = 'unknown'
        if 'identity_verify' in verification_url_lower or '/iv/' in verification_url_lower:
            verification_type = 'face_verify'
        elif 'qrcode' in verification_url_lower or 'scan' in verification_url_lower:
            verification_type = 'qr_verify'

        return {
            'source': 'browser_cookie_warmup',
            'probe_name': probe_name or 'unknown_probe',
            'verification_url': verification_url,
            'verification_type': verification_type,
            'ret': ret_values,
            'summary': ret_summary,
        }

    def _infer_browser_cookie_warmup_risk_trigger_scene(
        self,
        verification_hint: Optional[Dict[str, Any]],
        verification_url: str,
    ) -> Optional[str]:
        if not isinstance(verification_hint, dict):
            return None

        source = str(verification_hint.get('source') or '').strip().lower()
        if source != 'browser_cookie_warmup':
            return None

        probe_name = str(verification_hint.get('probe_name') or '').strip().lower()
        verification_url_lower = str(verification_url or '').strip().lower()
        if (
            probe_name == 'login_token_fetch' or
            'mtop.taobao.idlemessage.pc.login.token' in verification_url_lower
        ):
            return 'token_refresh'

        return None

    def _execute_browser_cookie_warmup_probe(
        self,
        context,
        page,
        probe: Dict[str, str],
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not probe:
            return {}

        effective_timeout_ms = timeout_ms
        if effective_timeout_ms is None:
            effective_timeout_ms = getattr(self, 'browser_cookie_warmup_probe_timeout_ms', 5000) or 5000
        try:
            effective_timeout_ms = max(1000, int(effective_timeout_ms))
        except Exception:
            effective_timeout_ms = 5000

        request_headers = {
            'accept': 'application/json, text/plain, */*',
            'content-type': 'application/x-www-form-urlencoded',
        }

        request_context = getattr(context, 'request', None) if context else None
        if request_context and hasattr(request_context, 'post'):
            try:
                response = request_context.post(
                    probe['url'],
                    data=probe.get('body') or '',
                    headers=request_headers,
                    timeout=effective_timeout_ms,
                )
                response_text = ''
                try:
                    response_text = str(response.text() or '')
                except Exception as body_err:
                    response_text = ''
                    logger.debug(
                        f"【{self.pure_user_id}】浏览器业务预热响应读取失败，继续使用已有Cookie快照: {body_err}"
                    )

                result = {
                    'ok': bool(getattr(response, 'ok', False)),
                    'status': int(getattr(response, 'status', 0) or 0),
                    'text': response_text[:600],
                    'timed_out': False,
                    'timeout_ms': effective_timeout_ms,
                }
                response_cookie_updates = self._extract_set_cookie_updates_from_playwright_response(response)
                if response_cookie_updates:
                    result['set_cookie_updates'] = response_cookie_updates
                return result
            except Exception as request_err:
                error_text = str(request_err)
                error_name = type(request_err).__name__
                timed_out = (
                    'Timeout' in error_name or
                    'timed out' in error_text.lower() or
                    'timeout' in error_text.lower()
                )
                if timed_out or not page:
                    return {
                        'ok': False,
                        'status': 0,
                        'error': error_text,
                        'timed_out': timed_out,
                        'timeout_ms': effective_timeout_ms,
                    }

                logger.debug(
                    f"【{self.pure_user_id}】浏览器业务预热 request.post 失败，回退到页面内 fetch: {request_err}"
                )

        if not page:
            return {}

        return page.evaluate(
            """
            async ({ url, body, timeoutMs }) => {
                let didTimeout = false;
                const controller = new AbortController();
                const timer = setTimeout(() => {
                    didTimeout = true;
                    controller.abort();
                }, timeoutMs);
                try {
                    const resp = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        cache: 'no-store',
                        headers: {
                            'accept': 'application/json, text/plain, */*',
                            'content-type': 'application/x-www-form-urlencoded',
                        },
                        body,
                        signal: controller.signal,
                    });
                    const text = await resp.text();
                    return {
                        ok: resp.ok,
                        status: resp.status,
                        text: text.slice(0, 600),
                        timed_out: false,
                        timeout_ms: timeoutMs,
                    };
                } catch (error) {
                    return {
                        ok: false,
                        status: 0,
                        error: String((error && error.message) || error || ''),
                        timed_out: didTimeout || String((error && error.name) || '') === 'AbortError',
                        timeout_ms: timeoutMs,
                    };
                } finally {
                    clearTimeout(timer);
                }
            }
            """,
            {
                "url": probe['url'],
                "body": probe['body'],
                "timeoutMs": effective_timeout_ms,
            },
        )

    def _consume_browser_cookie_warmup_verification_hint(
        self,
        context,
        fallback_page,
        cookies_dict: Dict[str, str],
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ):
        verification_hint = getattr(self, 'last_browser_cookie_warmup_verification_hint', None) or {}
        verification_url = str(verification_hint.get('verification_url') or '').strip()
        if not verification_url or not context:
            return None

        if cookies_dict.get('havana_lgc2_77'):
            return None

        logger.warning(
            f"【{self.pure_user_id}】检测到浏览器业务预热返回后续验证入口，"
            f"当前 havana_lgc2_77 仍缺失，转入验证接管: {verification_url}"
        )

        verify_page = None
        override_risk_trigger_scene = self._infer_browser_cookie_warmup_risk_trigger_scene(
            verification_hint,
            verification_url,
        )
        previous_risk_trigger_scene = getattr(self, 'risk_trigger_scene', None)
        try:
            if override_risk_trigger_scene:
                if override_risk_trigger_scene != previous_risk_trigger_scene:
                    logger.info(
                        f"【{self.pure_user_id}】浏览器业务预热验证页临时切换 risk_trigger_scene="
                        f"{override_risk_trigger_scene}（from {previous_risk_trigger_scene or 'unset'}）"
                    )
                self.risk_trigger_scene = override_risk_trigger_scene

            verify_page = context.new_page()
            verify_page.goto(verification_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            recovered_slider_detected = self._page_has_slider(verify_page)
            recovered_purecaptcha_detected = any(
                token in verification_url.lower()
                for token in ('purecaptcha=', 'action=captcha', 'punish?', 'x5step=2')
            )
            if recovered_slider_detected or recovered_purecaptcha_detected:
                logger.info(
                    f"【{self.pure_user_id}】浏览器业务预热返回的验证页命中"
                    f"{'滑块' if recovered_slider_detected else 'pureCaptcha'}特征，优先尝试自动续解"
                )
                solved = self._attempt_solve_slider_on_page(verify_page)
                if solved:
                    login_success, active_page, _ = self._probe_context_login_success(context, verify_page)
                    if login_success:
                        logger.success(f"【{self.pure_user_id}】✅ 浏览器业务预热验证页自动续解后已确认登录成功")
                        return self._finalize_logged_in_cookies(
                            context,
                            active_page or verify_page,
                            scene="浏览器业务预热验证页自动续解",
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                        )

            verification_type = str(verification_hint.get('verification_type') or '').strip() or self._detect_verification_type(verify_page)
            if verification_type == 'unknown' and 'identity_verify' in verification_url.lower():
                verification_type = 'face_verify'

            verification_screenshot = self._capture_verification_screenshot(verify_page)
            verification_wrapper = _host.VerificationFrameWrapper(
                verify_page,
                verification_type=verification_type,
                verify_url=verification_url,
                screenshot_path=verification_screenshot,
            )
            return self._process_verification_requirement(
                context,
                verify_page,
                verification_wrapper,
                notification_callback,
                notification_scene,
            )
        except Exception as open_verify_err:
            logger.warning(
                f"【{self.pure_user_id}】打开浏览器业务预热返回的验证入口失败: {open_verify_err}"
            )
            try:
                if verify_page:
                    verify_page.close()
            except Exception:
                pass
        finally:
            if override_risk_trigger_scene:
                self.risk_trigger_scene = previous_risk_trigger_scene
        return None

    def _extract_set_cookie_updates_from_playwright_response(self, response) -> Dict[str, str]:
        """从 Playwright Response 中提取 Set-Cookie，避免关键票据已下发但未沉淀到 context.cookies。"""
        if not response:
            return {}

        set_cookie_values = []
        try:
            if hasattr(response, 'header_values'):
                set_cookie_values = response.header_values('set-cookie') or []
        except Exception:
            set_cookie_values = []

        if not set_cookie_values:
            try:
                if hasattr(response, 'header_value'):
                    raw_value = response.header_value('set-cookie')
                    if raw_value:
                        set_cookie_values = [item.strip() for item in str(raw_value).splitlines() if item.strip()]
            except Exception:
                set_cookie_values = []

        if not set_cookie_values:
            try:
                headers = response.headers() if callable(getattr(response, 'headers', None)) else (response.headers or {})
                raw_value = headers.get('set-cookie') or headers.get('Set-Cookie')
                if isinstance(raw_value, list):
                    set_cookie_values = [str(item).strip() for item in raw_value if str(item).strip()]
                elif raw_value:
                    set_cookie_values = [item.strip() for item in str(raw_value).splitlines() if item.strip()]
            except Exception:
                set_cookie_values = []

        updates = {}
        for cookie_line in set_cookie_values:
            first_part = str(cookie_line).split(';', 1)[0].strip()
            if not first_part or '=' not in first_part:
                continue
            name, value = first_part.split('=', 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            updates[name] = value
        return updates

    def _stabilize_logged_in_context_cookies(self, context, page=None, scene: str = "登录完成后") -> Dict[str, str]:
        """登录成功后补做一次轻量页面稳定化，尽量把延迟下发的会话 Cookie 补齐。"""
        best_cookies = self._snapshot_context_cookies(context, page=page)
        best_missing = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not best_cookies.get(key)
        ]
        self._log_cookie_snapshot_integrity(best_cookies, f"{scene}初始快照")
        if not best_missing:
            return best_cookies

        work_page = page
        if not work_page:
            pages = self._get_context_pages(context)
            work_page = pages[0] if pages else None
        if not work_page:
            return best_cookies

        actions = [
            ("reload_current", None),
            ("goto_home", "https://www.goofish.com/"),
            ("goto_im", "https://www.goofish.com/im"),
        ]

        logger.info(
            f"【{self.pure_user_id}】{scene}检测到关键Cookie缺失，开始轻量稳定化: "
            f"missing_protected_fields={best_missing}"
        )

        for action_name, target_url in actions:
            try:
                if target_url:
                    logger.info(f"【{self.pure_user_id}】{scene}稳定化动作: {action_name} -> {target_url}")
                    work_page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                else:
                    logger.info(f"【{self.pure_user_id}】{scene}稳定化动作: {action_name}")
                    work_page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception as nav_e:
                logger.warning(f"【{self.pure_user_id}】{scene}稳定化动作 {action_name} 失败: {nav_e}")
                continue

            time.sleep(1.0)
            try:
                work_page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            time.sleep(0.5)

            current_cookies = self._snapshot_context_cookies(context, page=work_page)
            current_missing = [
                key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                if not current_cookies.get(key)
            ]
            self._log_cookie_snapshot_integrity(current_cookies, f"{scene}稳定化[{action_name}]")

            if current_cookies and len(current_missing) < len(best_missing):
                best_cookies = current_cookies
                best_missing = current_missing
                logger.info(
                    f"【{self.pure_user_id}】{scene}稳定化后关键Cookie缺失减少到 {len(best_missing)} 个: {best_missing}"
                )

            if not best_missing:
                break

        if best_missing:
            warmed_cookies = self._perform_browser_cookie_warmup_probes(
                context,
                work_page,
                scene=scene,
                initial_cookies=best_cookies,
            )
            warmed_missing = [
                key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                if not warmed_cookies.get(key)
            ]
            if warmed_cookies and len(warmed_missing) < len(best_missing):
                best_cookies = warmed_cookies
                best_missing = warmed_missing

        return best_cookies

    def _snapshot_context_cookies_via_cdp(self, context=None, page=None) -> Dict[str, str]:
        """通过 CDP 兜底抓取 Chromium 全量 Cookie，补齐 Playwright context.cookies() 可能遗漏的票据。"""
        current_context = context or self.context
        if not current_context:
            return {}

        probe_page = page
        if not probe_page:
            pages = self._get_context_pages(current_context)
            probe_page = pages[0] if pages else None
        if not probe_page:
            return {}

        session = None
        try:
            session = current_context.new_cdp_session(probe_page)
            try:
                session.send("Network.enable")
            except Exception:
                pass
            response = session.send("Network.getAllCookies") or {}
            raw_cookies = response.get("cookies") if isinstance(response, dict) else []
            merged = {}
            for item in raw_cookies or []:
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                merged[name] = str(item.get("value") or "")
            return merged
        except Exception as cdp_e:
            logger.debug(f"【{self.pure_user_id}】CDP Cookie 快照失败: {cdp_e}")
            return {}
        finally:
            if session:
                try:
                    session.detach()
                except Exception:
                    pass

    def _flatten_cookies_by_domain_preference(
        self,
        raw_cookies,
        preferred_domain_suffixes=None,
    ) -> Dict[str, str]:
        """将 [{name, value, domain, ...}] 压扁为 {name: value}。

        当 preferred_domain_suffixes 非空时，同名 Cookie 在多个域共存时优先取
        domain 命中后缀的版本；其它情况下保持原行为（列表顺序后者覆盖前者）。
        """
        if not raw_cookies:
            return {}
        if not preferred_domain_suffixes:
            return {c['name']: c['value'] for c in raw_cookies if c.get('name')}

        suffixes = tuple(s.lstrip('.').lower() for s in preferred_domain_suffixes if s)
        by_name: Dict[str, dict] = {}
        for c in raw_cookies:
            name = c.get('name')
            if not name:
                continue
            existing = by_name.get(name)
            if existing is None:
                by_name[name] = c
                continue
            existing_domain = (existing.get('domain') or '').lstrip('.').lower()
            new_domain = (c.get('domain') or '').lstrip('.').lower()
            existing_hit = any(existing_domain.endswith(s) for s in suffixes)
            new_hit = any(new_domain.endswith(s) for s in suffixes)
            if new_hit and not existing_hit:
                by_name[name] = c
            elif existing_hit and not new_hit:
                pass  # 保留首选域版本
            else:
                by_name[name] = c  # 都命中或都不命中：沿用原"后者覆盖"行为
        return {c['name']: c['value'] for c in by_name.values()}

    def _snapshot_context_cookies(
        self,
        context=None,
        page=None,
        preferred_domain_suffixes=None,
    ) -> Dict[str, str]:
        """快照浏览器上下文中的所有 Cookie，返回 {name: value} 字典。

        Args:
            preferred_domain_suffixes: 可选；同名 Cookie 跨域共存时优先选 domain
                命中这些后缀的版本（默认 None，行为不变）。仅 _get_cookies_after_success
                之类需要"按目标域取真值"的调用方使用，其它调用方保持默认。
        """
        try:
            current_context = context or self.context
            if not current_context:
                return {}

            playwright_cookies = {}
            try:
                raw = current_context.cookies()
                playwright_cookies = self._flatten_cookies_by_domain_preference(
                    raw, preferred_domain_suffixes
                )
            except Exception as playwright_e:
                logger.debug(f"【{self.pure_user_id}】Playwright Cookie 快照失败: {playwright_e}")

            cdp_cookies = self._snapshot_context_cookies_via_cdp(current_context, page=page)
            if not cdp_cookies:
                return playwright_cookies

            merged_cookies = dict(playwright_cookies)
            merged_cookies.update(cdp_cookies)

            extra_keys = sorted(set(cdp_cookies.keys()) - set(playwright_cookies.keys()))
            if extra_keys:
                protected_from_cdp = [
                    key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                    if key in extra_keys
                ]
                logger.info(
                    f"【{self.pure_user_id}】CDP Cookie 快照补充了 {len(extra_keys)} 个字段: "
                    f"{extra_keys[:12]}{' ...' if len(extra_keys) > 12 else ''}"
                )
                if protected_from_cdp:
                    logger.info(
                        f"【{self.pure_user_id}】CDP Cookie 快照补到了关键字段: {protected_from_cdp}"
                    )

            return merged_cookies
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】快照 Cookie 失败: {e}")
            return {}

    def _log_cookie_snapshot_integrity(self, cookies_dict: Dict[str, str], scene: str):
        """记录登录链路中的 Cookie 快照完整性，避免不完整快照静默通过。"""
        if not cookies_dict:
            logger.warning(f"【{self.pure_user_id}】{scene}Cookie快照为空")
            return

        missing_protected_fields = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        missing_required_fields = [
            key for key in self._REQUIRED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]

        if missing_protected_fields:
            logger.warning(
                f"【{self.pure_user_id}】{scene}Cookie快照完整性告警: "
                f"field_count={len(cookies_dict)}, "
                f"missing_protected_fields={missing_protected_fields}"
            )
        if missing_required_fields:
            logger.warning(
                f"【{self.pure_user_id}】{scene}Cookie快照核心字段不足: "
                f"field_count={len(cookies_dict)}, "
                f"missing_required_fields={missing_required_fields}"
            )

    def _finalize_logged_in_cookies(
        self,
        context,
        page,
        *,
        scene: str,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
        extra_cookie_updates: Optional[Dict[str, str]] = None,
    ):
        """登录态已确认后，尽量获取完整 Cookie，并对半登录态做最后兜底。"""
        target_page = page
        try:
            if target_page and hasattr(target_page, 'is_closed') and target_page.is_closed():
                target_page = None
        except Exception:
            pass

        if not target_page:
            target_page = self._select_monitor_page(context, page)

        self.last_browser_cookie_warmup_session_unready = False

        # 账密登录成功后，浏览器可能停留在 login.taobao.com / www.taobao.com，新的 _m_h5_tk
        # 会落到 .taobao.com 域；后续 mtop.idlemessage.pc.login.token 接口被 h5api.m.goofish.com
        # 网关 H5 token 校验时拿不到对应域的 token，直接回 FAIL_SYS_ILLEGAL_ACCESS::非法请求。
        # 参考 1157ab3 在 _get_cookies_after_success 的做法，先回访 goofish 主域让 H5 token
        # 重发到 .goofish.com，再做 cookie 快照，并显式让同名 Cookie 取 goofish 域版本。
        if target_page:
            try:
                pre_snapshot_url = target_page.url or ''
                pre_snapshot_host = (urlparse(pre_snapshot_url).hostname or '').lower()
            except Exception:
                pre_snapshot_host = ''
            if 'goofish.com' not in pre_snapshot_host:
                try:
                    target_page.goto(
                        'https://www.goofish.com/',
                        wait_until='domcontentloaded',
                        timeout=8000,
                    )
                    time.sleep(1.5)
                    logger.info(
                        f"【{self.pure_user_id}】{scene}前已回访 goofish 主域，"
                        f"等待 .goofish.com 域重新颁发 _m_h5_tk"
                    )
                except Exception as goto_e:
                    logger.warning(
                        f"【{self.pure_user_id}】{scene}前回访 goofish 主域失败，仍按当前页 cookie 继续: {goto_e}"
                    )

        cookies_dict = self._snapshot_context_cookies(
            context,
            page=target_page,
            preferred_domain_suffixes=('goofish.com',),
        )
        if extra_cookie_updates:
            merged_from_network = dict(cookies_dict)
            merged_from_network.update(extra_cookie_updates)
            cookies_dict = merged_from_network
            observed_names = sorted(extra_cookie_updates.keys())
            observed_protected = [
                key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                if key in extra_cookie_updates
            ]
            logger.info(
                f"【{self.pure_user_id}】已合并登录响应中的 {len(extra_cookie_updates)} 个Set-Cookie到{scene}快照: "
                f"{observed_names[:16]}{' ...' if len(observed_names) > 16 else ''}"
            )
            if observed_protected:
                logger.info(
                    f"【{self.pure_user_id}】登录响应中包含关键会话Cookie: {observed_protected}"
                )
        logger.info(f"【{self.pure_user_id}】{scene}后获取到 {len(cookies_dict)} 个Cookie字段")

        if not cookies_dict:
            logger.error(f"【{self.pure_user_id}】❌ {scene}后未获取到Cookie")
            return self._fail_login(f"{scene}后未获取到Cookie")

        missing_protected_fields = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if missing_protected_fields:
            logger.warning(
                f"【{self.pure_user_id}】{scene}后Cookie仍缺少关键字段，先执行标准稳定化: "
                f"{missing_protected_fields}"
            )
            stabilized_cookies = self._stabilize_logged_in_context_cookies(
                context,
                target_page,
                scene=scene,
            )
            if stabilized_cookies:
                cookies_dict = stabilized_cookies

        missing_protected_fields = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if missing_protected_fields and target_page:
            logger.warning(
                f"【{self.pure_user_id}】{scene}标准稳定化后仍缺少关键字段，继续执行浏览器业务预热: "
                f"{missing_protected_fields}"
            )
            warmed_cookies = self._perform_browser_cookie_warmup_probes(
                context,
                target_page,
                scene=scene,
                initial_cookies=cookies_dict,
            )
            if warmed_cookies:
                cookies_dict = warmed_cookies

        warmup_hint_result = self._consume_browser_cookie_warmup_verification_hint(
            context,
            target_page,
            cookies_dict,
            notification_callback=notification_callback,
            notification_scene=notification_scene,
        )
        if warmup_hint_result is not None:
            return warmup_hint_result

        pending_identity_error_before = self.last_login_error
        pending_identity_result = self._handle_pending_identity_verification_state(
            context,
            target_page,
            cookies_dict,
            notification_callback=notification_callback,
            notification_scene=notification_scene,
        )
        if pending_identity_result is not None:
            return pending_identity_result
        if self.last_login_error and self.last_login_error != pending_identity_error_before:
            return None

        missing_protected_fields = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if missing_protected_fields and getattr(self, 'last_browser_cookie_warmup_session_unready', False):
            self._log_cookie_snapshot_integrity(cookies_dict, f"{scene}完成后")
            logger.error(
                f"【{self.pure_user_id}】❌ {scene}后关键Cookie仍未齐全，且浏览器业务预热仍提示服务端Session未就绪: "
                f"{missing_protected_fields}"
            )
            return self._fail_login(
                f"{scene}后关键Cookie仍未齐全，服务端Session仍未就绪: {', '.join(missing_protected_fields)}"
            )

        missing_required_fields = [
            key for key in self._REQUIRED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if missing_required_fields:
            self._log_cookie_snapshot_integrity(cookies_dict, f"{scene}完成后")
            logger.error(
                f"【{self.pure_user_id}】❌ {scene}后Cookie仍缺失核心字段: "
                f"{missing_required_fields}"
            )
            return self._fail_login(
                f"{scene}后Cookie仍缺失核心字段: {', '.join(missing_required_fields)}"
            )

        self._log_cookie_snapshot_integrity(cookies_dict, f"{scene}完成后")
        logger.success(f"【{self.pure_user_id}】✅ {scene}后Cookie获取完成，字段数: {len(cookies_dict)}")

        # 验证成功后回填该账号悬挂在 processing 状态的验证类风控日志，
        # 否则前端"查看验证截图"会一直把历史截图当成待处理验证展示
        try:
            from db_manager import db_manager as _db
            resolved_count = _db.resolve_pending_verification_risk_logs(
                self.pure_user_id,
                processing_result=f'{scene}成功，验证已完成',
            )
            if resolved_count:
                logger.info(f"【{self.pure_user_id}】已回填 {resolved_count} 条待处理验证风控日志为成功")
        except Exception as resolve_err:
            logger.warning(f"【{self.pure_user_id}】回填验证风控日志状态失败: {resolve_err}")

        cleared_pending_markers = []
        sanitized_cookies = dict(cookies_dict)
        for key in self._IDENTITY_VERIFY_PENDING_COOKIE_FIELDS:
            if sanitized_cookies.pop(key, None) is not None:
                cleared_pending_markers.append(key)
        if cleared_pending_markers:
            logger.info(
                f"[{self.pure_user_id}] {scene} cleared pending identity markers: "
                f"{cleared_pending_markers}"
            )
        return sanitized_cookies

    def _save_cookies_to_file(self, cookies):
        """保存cookie到文件"""
        try:
            # 确保目录存在
            cookie_dir = f"slider_cookies/{self.user_id}"
            os.makedirs(cookie_dir, exist_ok=True)

            # 保存cookie到JSON文件
            cookie_file = f"{cookie_dir}/cookies_{int(time.time())}.json"
            with open(cookie_file, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)

            logger.info(f"【{self.pure_user_id}】Cookie已保存到文件: {cookie_file}")

        except Exception as e:
            logger.error(f"【{self.pure_user_id}】保存cookie到文件失败: {str(e)}")

    def _has_meaningful_cookie_refresh(self, baseline: Dict[str, str], current: Dict[str, str]) -> bool:
        """判断关键 Cookie 是否发生了有意义的变化。

        判定逻辑（满足其一即可）：
        1. 任何 x5 系 Cookie 的值发生了变化或新增
        2. 关键会话 Cookie 的值发生了变化或新增
        """
        # 检查 x5 系 Cookie
        for name, value in current.items():
            if name.lower().startswith(self._X5_COOKIE_PREFIX):
                old_value = baseline.get(name)
                if old_value is None or old_value != value:
                    logger.info(f"【{self.pure_user_id}】Cookie 刷新检测: x5 系 Cookie '{name}' 已变化")
                    return True

        # 检查关键会话 Cookie
        for name in self._KEY_COOKIE_NAMES:
            new_val = current.get(name)
            if new_val is not None:
                old_val = baseline.get(name)
                if old_val is None or old_val != new_val:
                    logger.info(f"【{self.pure_user_id}】Cookie 刷新检测: 关键会话 Cookie '{name}' 已变化")
                    return True

        logger.warning(f"【{self.pure_user_id}】Cookie 刷新检测: 无有意义的 Cookie 变化")
        return False

    def _build_initial_cookie_payload(self) -> List[Dict[str, Any]]:
        if not self.initial_cookies:
            return []

        cookies: List[Dict[str, Any]] = []
        for cookie_pair in self.initial_cookies.split(";"):
            cookie_pair = cookie_pair.strip()
            if not cookie_pair or "=" not in cookie_pair:
                continue
            name, value = cookie_pair.split("=", 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            cookies.append({
                "name": name,
                "value": value,
                "domain": ".goofish.com",
                "path": "/",
            })
        return cookies


class SliderVerificationMixin:
    """验证页检测：二维码/人脸/超时恢复/风控封锁识别。"""

    def _capture_verification_screenshot(self, page, frame=None, iframe_selector: Optional[str] = None) -> Optional[str]:
        """截取验证页面截图，多种方式逐级回退"""
        try:
            import glob

            screenshots_dir = "static/uploads/images"
            os.makedirs(screenshots_dir, exist_ok=True)

            existing_screenshots = glob.glob(
                os.path.join(screenshots_dir, f"face_verify_{self.pure_user_id}_*.jpg")
            )
            existing_screenshots += glob.glob(
                os.path.join(screenshots_dir, f"face_verify_{self.pure_user_id}_*.png")
            )

            detection_text = ""
            try:
                if frame is not None:
                    detection_text = self._read_frame_text_for_detection(frame)
                if not detection_text and page is not None:
                    detection_text = self._collect_page_text_for_detection(page)
            except Exception:
                detection_text = ""

            if self._is_timed_out_verification_text(detection_text) and existing_screenshots:
                latest_existing = max(existing_screenshots, key=os.path.getmtime)
                reusable_path = latest_existing.replace("\\", "/")
                logger.warning(
                    f"【{self.pure_user_id}】当前验证页已进入超时/失效态，"
                    f"保留上一张可用验证截图，不覆盖为超时页: {reusable_path}"
                )
                return reusable_path

            # 等待验证页面渲染（无头模式下 iframe 渲染需要时间）
            time.sleep(1.5)

            screenshot_bytes = None

            # 方式1：通过 frame.frame_element() 截取 iframe 元素
            if frame is not None and screenshot_bytes is None:
                try:
                    frame_element = frame.frame_element()
                    if frame_element:
                        screenshot_bytes = frame_element.screenshot(timeout=5000)
                        logger.info(f"【{self.pure_user_id}】方式1: 截取验证iframe元素成功")
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】方式1失败(frame_element): {e}")

            # 方式2：通过 iframe 选择器截取
            if screenshot_bytes is None and iframe_selector:
                try:
                    iframe_element = page.query_selector(iframe_selector)
                    if iframe_element:
                        screenshot_bytes = iframe_element.screenshot(timeout=5000)
                        logger.info(f"【{self.pure_user_id}】方式2: 按选择器截取iframe成功")
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】方式2失败(selector): {e}")

            # 方式3：通过 alibaba-login-box 选择器（常见的人脸验证 iframe）
            if screenshot_bytes is None:
                try:
                    login_box = page.query_selector('iframe#alibaba-login-box')
                    if login_box:
                        screenshot_bytes = login_box.screenshot(timeout=5000)
                        logger.info(f"【{self.pure_user_id}】方式3: 截取alibaba-login-box成功")
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】方式3失败(alibaba-login-box): {e}")

            # 方式4：截取整个页面可见区域
            if screenshot_bytes is None:
                try:
                    screenshot_bytes = page.screenshot(full_page=False, timeout=10000)
                    logger.info(f"【{self.pure_user_id}】方式4: 截取整页面成功")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】方式4失败(full_page): {e}")

            # 方式5：截取整个页面（含滚动区域）
            if screenshot_bytes is None:
                try:
                    screenshot_bytes = page.screenshot(full_page=True, timeout=10000)
                    logger.info(f"【{self.pure_user_id}】方式5: 截取完整页面成功")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】方式5失败(full_page=True): {e}")

            if screenshot_bytes is None:
                logger.error(f"【{self.pure_user_id}】所有截图方式均失败")
                return None

            timestamp = _host.datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"face_verify_{self.pure_user_id}_{timestamp}.jpg"
            file_path = os.path.join(screenshots_dir, filename)

            with open(file_path, 'wb') as f:
                f.write(screenshot_bytes)

            screenshot_path = file_path.replace('\\', '/')
            logger.info(f"【{self.pure_user_id}】✅ 验证截图已保存: {screenshot_path} ({len(screenshot_bytes)} bytes)")
            return screenshot_path
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】截取验证截图时出错: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def _detect_pending_identity_verification_cookie_state(self, cookies_dict: Dict[str, str]) -> List[str]:
        """识别“前端已登录但仍卡在二次身份校验态”的 Cookie 痕迹。"""
        if not cookies_dict:
            return []

        pending_markers = [
            key for key in self._IDENTITY_VERIFY_PENDING_COOKIE_FIELDS
            if cookies_dict.get(key)
        ]
        if not pending_markers:
            return []

        if cookies_dict.get('havana_lgc2_77'):
            return []

        missing_required_fields = [
            key for key in self._REQUIRED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if not missing_required_fields:
            return []

        return pending_markers

    def _resolve_pending_identity_verification_url(self, cookies_dict: Dict[str, str]) -> Optional[str]:
        """基于半登录态 Cookie 反查身份校验页面链接。"""
        if not cookies_dict:
            return None

        cookie_text = '; '.join(
            f"{key}={value}"
            for key, value in cookies_dict.items()
            if key and value is not None
        )
        if not cookie_text:
            return None

        try:
            verification_url = _host.resolve_verification_url_from_cookie(
                cookie_text,
                proxy=self.proxy_config,
            )
            if verification_url:
                logger.info(
                    f"【{self.pure_user_id}】已根据半登录态Cookie反查到身份验证链接: {verification_url}"
                )
                return verification_url
        except Exception as resolve_err:
            logger.warning(
                f"【{self.pure_user_id}】根据半登录态Cookie反查身份验证链接失败: {resolve_err}"
            )

        return None

    def _handle_pending_identity_verification_state(
        self,
        context,
        fallback_page,
        cookies_dict: Dict[str, str],
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ):
        """处理“前端已登录但服务端仍要求二次身份校验”的半登录态。"""
        pending_identity_markers = self._detect_pending_identity_verification_cookie_state(cookies_dict)
        if not pending_identity_markers:
            return None

        logger.error(
            f"【{self.pure_user_id}】检测到前端已登录但仍处于二次身份校验态，"
            f"待确认Cookie标记: {pending_identity_markers}"
        )
        logger.error(
            f"【{self.pure_user_id}】该状态下通常不会下发完整业务会话Cookie，"
            f"例如 havana_lgc2_77 / x5secdata"
        )

        monitor_page = self._select_monitor_page(context, fallback_page) or fallback_page
        if monitor_page:
            try:
                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                if has_qr:
                    logger.warning(f"【{self.pure_user_id}】半登录态下检测到可见身份验证页，转入验证等待流程")
                    return self._process_verification_requirement(
                        context,
                        monitor_page,
                        qr_frame,
                        notification_callback,
                        notification_scene,
                    )
            except _host.PasswordLoginVerificationError:
                raise
            except Exception as verify_probe_err:
                logger.warning(
                    f"【{self.pure_user_id}】半登录态复检身份验证页失败，准备尝试反查验证链接: {verify_probe_err}"
                )

        verification_url = self._resolve_pending_identity_verification_url(cookies_dict)
        if verification_url and context:
            verify_page = None
            try:
                verify_page = context.new_page()
                logger.info(f"【{self.pure_user_id}】打开反查到的身份验证链接...")
                verify_page.goto(verification_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)

                recovered_slider_detected = self._page_has_slider(verify_page)
                recovered_purecaptcha_detected = any(
                    token in verification_url.lower()
                    for token in ('purecaptcha=', 'action=captcha', 'punish?', 'x5step=2')
                )
                if recovered_slider_detected or recovered_purecaptcha_detected:
                    logger.info(
                        f"【{self.pure_user_id}】半登录态恢复页命中"
                        f"{'滑块' if recovered_slider_detected else 'pureCaptcha'}特征，优先尝试自动续解"
                    )
                    solved = self._attempt_solve_slider_on_page(verify_page)
                    if solved:
                        login_success, active_page, _ = self._probe_context_login_success(context, verify_page)
                        if login_success:
                            logger.success(f"【{self.pure_user_id}】✅ 半登录态恢复页滑块续解后已确认登录成功")
                            return self._finalize_logged_in_cookies(
                                context,
                                active_page or verify_page,
                                scene="半登录态恢复页自动续解",
                                notification_callback=notification_callback,
                                notification_scene=notification_scene,
                            )
                        logger.warning(
                            f"【{self.pure_user_id}】半登录态恢复页滑块已处理，但暂未确认登录成功，继续走验证识别流程"
                        )
                    else:
                        logger.warning(
                            f"【{self.pure_user_id}】半登录态恢复页自动续解未成功，继续判断是否需要人工验证"
                        )

                verification_type = self._detect_verification_type(verify_page)
                if verification_type == 'unknown' and 'identity_verify' in verification_url.lower():
                    verification_type = 'face_verify'

                verification_screenshot = self._capture_verification_screenshot(verify_page)
                verification_wrapper = _host.VerificationFrameWrapper(
                    verify_page,
                    verification_type=verification_type,
                    verify_url=verification_url,
                    screenshot_path=verification_screenshot,
                )
                logger.warning(f"【{self.pure_user_id}】已根据半登录态Cookie恢复身份验证页面，转入验证等待流程")
                return self._process_verification_requirement(
                    context,
                    verify_page,
                    verification_wrapper,
                    notification_callback,
                    notification_scene,
                )
            except Exception as open_verify_err:
                logger.warning(
                    f"【{self.pure_user_id}】打开反查到的身份验证链接失败: {open_verify_err}"
                )
                try:
                    if verify_page:
                        verify_page.close()
                except Exception:
                    pass

            self._notify_verification_required(
                'qr_verify',
                verification_url,
                None,
                notification_callback,
                notification_scene,
            )
            return self._fail_login(
                "检测到二次身份校验未完成，请按通知中的验证链接完成验证后重试"
            )

        missing_required_fields = [
            key for key in self._REQUIRED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if not missing_required_fields:
            fallback_cookies = dict(cookies_dict)
            cleared_pending_markers = [
                key for key in self._IDENTITY_VERIFY_PENDING_COOKIE_FIELDS
                if fallback_cookies.pop(key, None) is not None
            ]
            logger.warning(
                f"【{self.pure_user_id}】半登录态未恢复出新的验证页，但当前核心会话字段已齐全；"
                f"回退为受保护Cookie交接，待上层合并补齐缺失字段。"
                f"已清理待确认标记: {cleared_pending_markers}"
            )
            return fallback_cookies

        return self._fail_login(
            "检测到二次身份校验未完成，当前仅形成前端登录态，服务端会话未建立"
        )

    def _safe_page_url(self, page) -> str:
        try:
            return str(page.url or '')
        except Exception:
            return ''

    def _safe_page_title(self, page) -> str:
        try:
            return str(page.title() or '')
        except Exception:
            return ''

    def _get_context_pages(self, context=None, fallback_page=None) -> List[Any]:
        pages = []
        seen = set()
        candidates = []

        current_context = context or self.context
        if current_context:
            try:
                candidates.extend(list(current_context.pages))
            except Exception:
                pass

        if fallback_page:
            candidates.append(fallback_page)

        for candidate in candidates:
            if not candidate:
                continue
            candidate_id = id(candidate)
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            try:
                if candidate.is_closed():
                    continue
            except Exception:
                pass
            pages.append(candidate)

        return pages

    def _is_logged_in_url(self, url: str) -> bool:
        current_url = str(url or '')
        if not current_url:
            return False

        current_url_lower = current_url.lower()

        if self._looks_like_verification_url(current_url_lower):
            return False

        if 'www.goofish.com/im' in current_url_lower:
            return True

        return (
            'goofish.com' in current_url_lower and
            'passport.goofish.com' not in current_url_lower and
            'mini_login' not in current_url_lower and
            '/iv/' not in current_url_lower
        )

    def _looks_like_verification_url(self, url: str) -> bool:
        current_url = str(url or '').lower()
        if not current_url:
            return False

        verification_tokens = (
            'passport.goofish.com',
            'mini_login',
            'identity_verify',
            '/iv/',
            'qrcode',
            'scan',
            'verify',
            'punish',
            'x5step=2',
            'action=captcha',
            'purecaptcha',
        )
        return any(token in current_url for token in verification_tokens)

    def _query_first_visible(self, frame, selectors: List[str]):
        if not frame:
            return None, None

        for selector in selectors:
            try:
                element = frame.query_selector(selector)
                if element and element.is_visible():
                    return element, selector
            except Exception:
                continue

        return None, None

    def _page_looks_like_verification(self, page) -> bool:
        try:
            if self._page_has_login_form(page):
                return False

            page_url = self._safe_page_url(page)
            if self._looks_like_verification_url(page_url):
                return True

            try:
                iframe = page.query_selector('iframe#alibaba-login-box')
                if iframe:
                    return True
            except Exception:
                pass

            try:
                for frame in page.frames:
                    if self._looks_like_verification_url(getattr(frame, 'url', '')):
                        return True
            except Exception:
                pass
        except Exception:
            pass

        return False

    def _looks_like_verification_title(self, title: str) -> bool:
        current_title = str(title or '')
        current_title_lower = current_title.lower()
        title_tokens = (
            'captcha',
            'intercept',
            'punish',
            '验证',
            '拦截',
            '验证码',
        )
        return any(token in current_title_lower or token in current_title for token in title_tokens)

    def _select_monitor_page(self, context=None, fallback_page=None):
        pages = self._get_context_pages(context, fallback_page)
        if not pages:
            return fallback_page

        reversed_pages = list(reversed(pages))

        for candidate in reversed_pages:
            if self._page_looks_like_verification(candidate):
                return candidate

        for candidate in reversed_pages:
            if self._page_has_keep_login_prompt(candidate):
                return candidate

        for candidate in reversed_pages:
            page_url = self._safe_page_url(candidate)
            if page_url and page_url != 'about:blank':
                return candidate

        return reversed_pages[0]

    def _page_has_slider(self, page) -> bool:
        if not page:
            return False

        slider_selectors = [
            '#nc_1_n1z',
            '.nc-container',
            '.nc_scale',
            '.nc-wrapper',
            '#baxia-dialog-content',
            '.nc_wrapper',
            '#nocaptcha',
        ]

        frames_to_check = [page]
        try:
            frames_to_check.extend(list(page.frames))
        except Exception:
            pass

        for frame in frames_to_check:
            for selector in slider_selectors:
                try:
                    element = frame.query_selector(selector)
                    if element and element.is_visible():
                        logger.info(f"【{self.pure_user_id}】检测到滑块元素: {selector}")
                        return True
                except Exception:
                    continue

        return False

    def _is_timed_out_verification_text(self, text: str) -> bool:
        content = str(text or '').strip()
        if not content:
            return False

        timeout_markers = (
            '验证失败',
            '验证超时',
            '请在指定时间内完成验证',
            '请重新扫描二维码完成身份验证',
            '重新扫描二维码',
            '返回二维码',
            '返回扫码',
            '二维码已失效',
            '二维码过期',
        )
        return any(marker in content for marker in timeout_markers)

    def _verification_target_is_timed_out(self, target, fallback_page=None) -> bool:
        detection_text = self._collect_verification_target_text(target, fallback_page=fallback_page)
        return self._is_timed_out_verification_text(detection_text)

    def _recover_timed_out_verification_page(self, qr_frame, fallback_page=None):
        recovery_markers = ['返回二维码', '返回扫码', '重新扫描二维码', '重新扫码']
        base_target = getattr(qr_frame, '_original_frame', qr_frame)
        candidate_targets = []
        for candidate in (base_target, fallback_page):
            if candidate is None or candidate in candidate_targets:
                continue
            candidate_targets.append(candidate)

        clicked_marker = None
        clicked_target = None
        for candidate in candidate_targets:
            if not hasattr(candidate, 'evaluate'):
                continue
            try:
                clicked_marker = candidate.evaluate(
                    """
                    (markers) => {
                        const normalize = (text) => (text || '').replace(/\\s+/g, '');
                        const isVisible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            if (!style || style.display === 'none' || style.visibility === 'hidden') {
                                return false;
                            }
                            const rect = el.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0;
                        };

                        const elements = Array.from(
                            document.querySelectorAll('a,button,[role=\"button\"],span,div')
                        );
                        for (const marker of markers) {
                            const normalizedMarker = normalize(marker);
                            const matched = elements.find((el) => {
                                const text = normalize(el.innerText || el.textContent || '');
                                return text && text.includes(normalizedMarker) && isVisible(el);
                            });
                            if (matched) {
                                matched.click();
                                return marker;
                            }
                        }
                        return null;
                    }
                    """,
                    recovery_markers,
                )
                if clicked_marker:
                    clicked_target = candidate
                    logger.info(
                        f"【{self.pure_user_id}】检测到验证页已超时，已尝试点击恢复入口: {clicked_marker}"
                    )
                    break
            except Exception as click_err:
                logger.debug(f"【{self.pure_user_id}】点击超时验证页恢复入口失败: {click_err}")

        if not clicked_marker:
            logger.warning(f"【{self.pure_user_id}】当前超时验证页未找到可用的二维码恢复入口")
            return None

        time.sleep(1.5)
        monitor_page = fallback_page or clicked_target or base_target
        try:
            has_verification, recovered_frame = self._detect_qr_code_verification(monitor_page)
        except Exception as detect_err:
            logger.warning(f"【{self.pure_user_id}】点击恢复入口后重新检测验证页失败: {detect_err}")
            return None

        if not has_verification or not recovered_frame:
            logger.warning(f"【{self.pure_user_id}】点击恢复入口后未检测到新的验证页")
            return None

        if self._verification_target_is_timed_out(recovered_frame, fallback_page=monitor_page):
            logger.warning(f"【{self.pure_user_id}】点击恢复入口后仍然拿到超时/失效验证页")
            return None

        recovered_screenshot_path = getattr(recovered_frame, 'screenshot_path', None)
        if not recovered_screenshot_path:
            try:
                recovered_screenshot_path = self._capture_verification_screenshot(
                    monitor_page,
                    frame=(None if recovered_frame is monitor_page else recovered_frame),
                )
                if recovered_screenshot_path and hasattr(recovered_frame, 'screenshot_path'):
                    recovered_frame.screenshot_path = recovered_screenshot_path
            except Exception as screenshot_err:
                logger.debug(f"【{self.pure_user_id}】恢复后补抓验证截图失败: {screenshot_err}")

        logger.info(f"【{self.pure_user_id}】已从超时验证页恢复出新的可用验证入口")
        return recovered_frame

    def _build_timed_out_verification_message(self, verification_type: str) -> str:
        verification_type_names = {
            'face_verify': '人脸验证',
            'sms_verify': '短信验证',
            'qr_verify': '二维码验证',
            'unknown': '身份验证',
        }
        type_name = verification_type_names.get(verification_type or 'unknown', '身份验证')
        return f"当前{type_name}页面已超时/失效，请重新发起验证"

    def _attempt_solve_slider_on_page(self, page) -> bool:
        if not page or not self._page_has_slider(page):
            return False

        logger.info(f"【{self.pure_user_id}】在当前活动页面检测到滑块，尝试自动处理...")
        original_page = self.page
        try:
            self.page = page
            solved = self.solve_slider(max_retries=3, fast_mode=True)
            if solved:
                logger.success(f"【{self.pure_user_id}】✅ 当前活动页面滑块处理成功")
                time.sleep(2)
            else:
                logger.warning(f"【{self.pure_user_id}】⚠️ 当前活动页面滑块处理未成功")
            return solved
        finally:
            self.page = original_page

    def _detect_verification_type(self, frame) -> str:
        """检测 iframe 内的具体验证类型

        Args:
            frame: iframe 的 content_frame

        Returns:
            str: 验证类型 - 'password_error' / 'face_verify' / 'sms_verify' / 'qr_verify'
                 / 'login_page' / 'unknown'
                 'login_page' 用于命中阿里普通登录页（如 mini_login.htm 左侧"快速进入"扫码登录），
                 这种情况只是登录态丢失而非身份校验，调用方应直接走登录补救而不要标为风控暂停。
        """
        try:
            # ── 0. 先看 frame URL，把"普通登录页"从 keyword 判定中独立出来 ──
            # 历史上 keyword 'qr_verify'(扫码/二维码/扫一扫/手机淘宝) 会把
            # passport.goofish.com/mini_login.htm 误判为身份验证页（因为该页本身就是
            # "扫码登录 + 账密登录" 的组合，文案天然包含"扫码"等关键词），导致
            # _request_stop_after_account_pause 误暂停账号。
            try:
                frame_url_raw = frame.url if hasattr(frame, 'url') else ""
            except Exception:
                frame_url_raw = ""
            frame_url_lower = (frame_url_raw or "").lower()
            risk_url_markers = (
                '/punish', 'captcha', 'verify_account', 'identity_verify',
                'face_verify', 'faceverify', 'liveness', 'risk_control',
                'sec_verify', 'security_verify', 'risk-control',
            )
            login_page_url_markers = (
                'passport.goofish.com/mini_login',
                'passport.goofish.com/newlogin',
                'passport.taobao.com/mini_login',
                'login.taobao.com/member/login',
            )
            if frame_url_lower and not any(m in frame_url_lower for m in risk_url_markers):
                if any(m in frame_url_lower for m in login_page_url_markers):
                    logger.info(
                        f"【{self.pure_user_id}】frame URL 命中普通登录页({frame_url_raw})，"
                        f"不进入身份验证 keyword 判定"
                    )
                    return 'login_page'

            detection_text = self._read_frame_text_for_detection(frame)
            detection_text_lower = detection_text.lower()

            # 1. 检查是否是账密错误
            # 这里不要用过宽的“登录失败”做账密错误判定，mini_login 风控页也会包含该文案。
            password_error_keywords = ['账密错误', '账号密码错误', '用户名或密码错误', '密码错误', '账号或密码错误']
            for keyword in password_error_keywords:
                if keyword in detection_text:
                    logger.info(f"【{self.pure_user_id}】检测到验证类型: 账密错误 (关键词: {keyword})")
                    return 'password_error'

            # 2. 检查是否是短信验证
            sms_keywords = ['短信验证', '验证码', '手机号', '发送验证码', '获取验证码']
            sms_count = sum(1 for keyword in sms_keywords if keyword in detection_text)
            if sms_count >= 2:  # 至少匹配2个关键词
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 短信验证")
                return 'sms_verify'

            # 3. 已超时/失效的人脸页通常需要回到二维码重新开始，不应继续误标为 face_verify
            if self._is_timed_out_verification_text(detection_text):
                logger.info(f"【{self.pure_user_id}】检测到验证页已超时/失效，按二维码恢复页处理")
                return 'qr_verify'

            # 4. 检查是否是人脸验证
            face_keywords = ['人脸', '刷脸', '面部', '拍摄脸部', '刷脸验证', '人脸验证']
            for keyword in face_keywords:
                if keyword in detection_text_lower:
                    logger.info(f"【{self.pure_user_id}】检测到验证类型: 人脸验证 (关键词: {keyword})")
                    return 'face_verify'

            # 5. 检查是否是二维码验证
            qr_keywords = ['扫码', '二维码', '扫一扫', '手机淘宝', '手机扫码']
            for keyword in qr_keywords:
                if keyword in detection_text:
                    logger.info(f"【{self.pure_user_id}】检测到验证类型: 二维码验证 (关键词: {keyword})")
                    return 'qr_verify'

            # 6. 检查 URL 特征
            frame_url = ""
            try:
                frame_url = frame.url if hasattr(frame, 'url') else ""
            except:
                pass

            if 'sms' in frame_url.lower() or 'phone' in frame_url.lower():
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 短信验证 (URL特征)")
                return 'sms_verify'

            if any(token in frame_url.lower() for token in ('face_verify', 'faceverify', 'liveness')):
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 人脸验证 (URL特征)")
                return 'face_verify'

            if 'identity_verify' in frame_url.lower():
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 人脸验证 (identity_verify URL特征)")
                return 'face_verify'

            if 'qrcode' in frame_url.lower() or 'scan' in frame_url.lower():
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 二维码验证 (URL特征)")
                return 'qr_verify'

            # 顶层业务页经常既不是登录页也不是验证页，这里只是未命中验证特征，降低为 debug 避免误导。
            logger.debug(f"【{self.pure_user_id}】当前页面未命中具体验证类型，暂标记为 unknown")
            return 'unknown'

        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】检测验证类型时出错: {e}")
            return 'unknown'

    def _detect_qr_code_verification(self, page) -> tuple:
        """检测是否存在二维码/人脸验证（排除滑块验证）
        
        Args:
            page: Page对象
        
        Returns:
            tuple: (has_qr, qr_frame) - 是否有二维码/人脸验证，验证frame
                   (False, None) - 如果检测到滑块验证，会先处理滑块，然后返回
        """
        try:
            logger.info(f"【{self.pure_user_id}】检测二维码/人脸验证...")
            
            # 先检查是否是滑块验证，如果是滑块验证，立即处理并返回
            slider_selectors = [
                '#nc_1_n1z',
                '.nc-container',
                '.nc_scale',
                '.nc-wrapper',
                '.nc_iconfont',
                '[class*="nc_"]'
            ]
            
            # 在主页面和所有frame中检查滑块
            frames_to_check = [page] + list(page.frames)
            for frame in frames_to_check:
                try:
                    for selector in slider_selectors:
                        try:
                            element = frame.query_selector(selector)
                            if element and element.is_visible():
                                logger.info(f"【{self.pure_user_id}】检测到滑块验证元素，立即处理滑块: {selector}")
                                # 检测到滑块验证，记录是在哪个frame中找到的
                                frame_info = "主页面" if frame == page else f"Frame: {frame.url if hasattr(frame, 'url') else '未知'}"
                                logger.info(f"【{self.pure_user_id}】滑块元素位置: {frame_info}")
                                
                                # 保存找到滑块的frame，供find_slider_elements使用
                                # 如果是在frame中找到的，保存frame引用；如果在主页面找到，保存None
                                if frame == page:
                                    self._detected_slider_frame = None  # 主页面
                                else:
                                    self._detected_slider_frame = frame  # 保存frame引用
                                
                                # 检测到滑块验证，立即处理
                                logger.warning(f"【{self.pure_user_id}】检测到滑块验证，开始自动处理...")
                                slider_risk_log = self._start_password_login_slider_risk_log(
                                    verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                    detection_phase='verification_probe',
                                )
                                slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                                if slider_success:
                                    logger.success(f"【{self.pure_user_id}】✅ 滑块验证成功！")
                                    self._finish_password_login_slider_risk_log(
                                        slider_risk_log,
                                        success=True,
                                        verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                        processing_result='密码登录流程中的滑块验证自动处理成功',
                                        extra_meta={'detection_source': '_detect_qr_code_verification'},
                                    )
                                    time.sleep(3)  # 等待滑块验证后的状态更新
                                    # 内层自救成功 → 立刻把 cookies 抓出来交给 run() 主流程，
                                    # 否则 run() 主体的 success 仍然是 False，会误存 run_failed 快照并触发退避
                                    try:
                                        recovered = self._get_cookies_after_success()
                                        if recovered:
                                            self._post_recovery_success = True
                                            self._post_recovery_cookies = recovered
                                            logger.info(
                                                f"【{self.pure_user_id}】内层滑块自救成功并已捕获 "
                                                f"cookies(条数 {len(recovered) if hasattr(recovered, '__len__') else 'unknown'})，"
                                                f"将上抛给 run() 主流程"
                                            )
                                    except Exception as recover_e:
                                        logger.warning(
                                            f"【{self.pure_user_id}】内层滑块自救成功但获取 cookie 失败: {recover_e}"
                                        )
                                else:
                                    # 常规重试仍失败后，刷新页面再补一次机会。
                                    logger.warning(
                                        f"【{self.pure_user_id}】⚠️ 滑块处理{self.slider_max_retries}次仍失败，刷新页面后重试..."
                                    )
                                    try:
                                        self.page.reload(wait_until="domcontentloaded", timeout=30000)
                                        logger.info(f"【{self.pure_user_id}】✅ 页面刷新完成")
                                        time.sleep(2)
                                        slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                                        if not slider_success:
                                            logger.error(f"【{self.pure_user_id}】❌ 刷新后滑块验证仍然失败")
                                            self._finish_password_login_slider_risk_log(
                                                slider_risk_log,
                                                success=False,
                                                verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                                error_message=self._get_slider_failure_message('滑块验证失败，请稍后重试'),
                                                extra_meta={'detection_source': '_detect_qr_code_verification'},
                                            )
                                        else:
                                            logger.success(f"【{self.pure_user_id}】✅ 刷新后滑块验证成功！")
                                            self._finish_password_login_slider_risk_log(
                                                slider_risk_log,
                                                success=True,
                                                verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                                processing_result='密码登录流程中的滑块验证自动处理成功（刷新后）',
                                                extra_meta={'detection_source': '_detect_qr_code_verification'},
                                            )
                                            time.sleep(3)
                                            # 同上：内层自救成功 → 抓 cookies 交给 run() 主流程
                                            try:
                                                recovered = self._get_cookies_after_success()
                                                if recovered:
                                                    self._post_recovery_success = True
                                                    self._post_recovery_cookies = recovered
                                                    logger.info(
                                                        f"【{self.pure_user_id}】刷新后内层滑块自救成功并已捕获 "
                                                        f"cookies(条数 {len(recovered) if hasattr(recovered, '__len__') else 'unknown'})，"
                                                        f"将上抛给 run() 主流程"
                                                    )
                                            except Exception as recover_e:
                                                logger.warning(
                                                    f"【{self.pure_user_id}】刷新后内层滑块自救成功但获取 cookie 失败: {recover_e}"
                                                )
                                    except Exception as e:
                                        logger.error(f"【{self.pure_user_id}】❌ 页面刷新失败: {e}")
                                        self._finish_password_login_slider_risk_log(
                                            slider_risk_log,
                                            success=False,
                                            verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                            error_message=f'页面刷新失败: {str(e)}',
                                            extra_meta={'detection_source': '_detect_qr_code_verification'},
                                        )
                                
                                # 清理临时变量
                                if hasattr(self, '_detected_slider_frame'):
                                    delattr(self, '_detected_slider_frame')
                                
                                # 返回 False, None 表示不是二维码/人脸验证（已处理滑块）
                                return False, None
                        except:
                            continue
                except:
                    continue

            # 检测所有frames中的二维码/人脸验证
            page_url = self._safe_page_url(page)
            page_verification_type = self._detect_verification_type(page)
            page_has_login_form = self._page_has_login_form(page)
            if self._looks_like_verification_url(page_url) or (
                page_verification_type in {'face_verify', 'sms_verify', 'qr_verify'} and not page_has_login_form
            ):
                if page_verification_type == 'password_error':
                    logger.error(f"【{self.pure_user_id}】❌ 顶层页面判定为账号密码错误")
                    raise _host.PasswordLoginVerificationError("账号密码错误，请检查账号密码是否正确")

                logger.info(f"【{self.pure_user_id}】✅ 顶层页面命中验证特征，URL: {page_url}")
                verification_screenshot = self._capture_verification_screenshot(page)
                return True, _host.VerificationFrameWrapper(
                    page,
                    verification_type=page_verification_type,
                    verify_url=page_url or None,
                    screenshot_path=verification_screenshot
                )

            # 首先检查是否有 alibaba-login-box iframe（人脸验证或短信验证）
            try:
                iframes = page.query_selector_all('iframe')
                for iframe in iframes:
                    try:
                        iframe_id = iframe.get_attribute('id')
                        if iframe_id == 'alibaba-login-box':
                            logger.info(f"【{self.pure_user_id}】✅ 检测到 alibaba-login-box iframe")
                            frame = iframe.content_frame()
                            if frame:
                                frame_url = frame.url if hasattr(frame, 'url') else '未知'
                                logger.info(f"【{self.pure_user_id}】验证Frame URL: {frame_url}")

                                # 先检测具体的验证类型
                                verification_type = self._detect_verification_type(frame)
                                logger.info(f"【{self.pure_user_id}】检测到验证类型: {verification_type}")

                                # 命中"普通登录页"（mini_login.htm 等）→ 不是风控验证，
                                # 但仍是"账号需要重新登录"——交给 _process_verification_requirement
                                # 走「等待用户操作」路径并通知用户扫码；普通扫码登录不应作为暂停账号的诱因。
                                if verification_type == 'login_page':
                                    logger.info(
                                        f"【{self.pure_user_id}】alibaba-login-box 是普通登录页，"
                                        f"作为「待扫码登录」上抛给 _process_verification_requirement"
                                    )
                                    verification_screenshot = self._capture_verification_screenshot(
                                        page,
                                        frame=frame,
                                        iframe_selector='iframe#alibaba-login-box'
                                    )
                                    return True, _host.VerificationFrameWrapper(
                                        frame,
                                        verification_type='login_page',
                                        verify_url=(frame.url if hasattr(frame, 'url') else None),
                                        screenshot_path=verification_screenshot,
                                    )

                                # 记录风控日志
                                try:
                                    from db_manager import db_manager
                                    event_type_map = {
                                        'password_error': 'password_error',
                                        'sms_verify': 'sms_verify',
                                        'qr_verify': 'qr_verify',
                                        'face_verify': 'face_verify',
                                        'unknown': 'unknown'
                                    }
                                    event_type_names = {
                                        'password_error': '账号密码错误',
                                        'sms_verify': '短信验证',
                                        'qr_verify': '二维码验证',
                                        'face_verify': '人脸验证',
                                        'unknown': '身份验证'
                                    }
                                    db_event_type = event_type_map.get(verification_type, 'unknown')
                                    event_name = event_type_names.get(verification_type, '身份验证')
                                    db_manager.add_risk_control_log(
                                        cookie_id=self.pure_user_id,
                                        event_type=db_event_type,
                                        session_id=getattr(self, 'risk_session_id', None),
                                        trigger_scene=getattr(self, 'risk_trigger_scene', None) or 'password_login',
                                        result_code=f"{verification_type}_detected",
                                        event_description=f"检测到{event_name}",
                                        event_meta=self._build_risk_event_meta(
                                            verification_url=frame_url,
                                            extra={
                                                'verification_type': verification_type,
                                                'account_id': self.pure_user_id,
                                            }
                                        ),
                                        processing_status='processing' if verification_type != 'password_error' else 'failed',
                                        error_message='检测到需要人工完成的身份验证' if verification_type != 'password_error' else '账号密码错误'
                                    )
                                    logger.info(f"【{self.pure_user_id}】已记录风控日志: {db_event_type}")
                                except Exception as log_err:
                                    logger.warning(f"【{self.pure_user_id}】记录风控日志失败: {log_err}")

                                # 如果是账密错误，抛出异常让调用者处理
                                if verification_type == 'password_error':
                                    logger.error(f"【{self.pure_user_id}】❌ 检测到账号密码错误")
                                    raise _host.PasswordLoginVerificationError("账号密码错误，请检查账号密码是否正确")

                                verification_screenshot = self._capture_verification_screenshot(
                                    page,
                                    frame=frame,
                                    iframe_selector='iframe#alibaba-login-box'
                                )

                                # 如果是短信验证
                                if verification_type == 'sms_verify':
                                    logger.warning(f"【{self.pure_user_id}】⚠️ 需要短信验证，暂不支持自动处理")
                                    return True, _host.VerificationFrameWrapper(
                                        frame,
                                        verification_type='sms_verify',
                                        screenshot_path=verification_screenshot
                                    )

                                # 如果是二维码验证
                                if verification_type == 'qr_verify':
                                    logger.warning(f"【{self.pure_user_id}】⚠️ 需要二维码验证")
                                    return True, _host.VerificationFrameWrapper(
                                        frame,
                                        verification_type='qr_verify',
                                        screenshot_path=verification_screenshot
                                    )

                                verify_url = None
                                if verification_type == 'face_verify':
                                    verify_url = self._get_face_verification_url(frame)
                                    if verify_url:
                                        logger.info(f"【{self.pure_user_id}】✅ 获取到人脸验证链接: {verify_url}")
                                elif verification_type == 'unknown':
                                    logger.warning(
                                        f"【{self.pure_user_id}】验证类型仍不明确，保留为unknown，不默认按人脸验证处理"
                                    )

                                return True, _host.VerificationFrameWrapper(
                                    frame,
                                    verification_type=verification_type if verification_type in {'face_verify', 'unknown'} else 'unknown',
                                    verify_url=verify_url,
                                    screenshot_path=verification_screenshot
                                )
                    except _host.PasswordLoginVerificationError:
                        raise
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】检查iframe时出错: {e}")
                        continue
            except _host.PasswordLoginVerificationError:
                raise
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】检查alibaba-login-box iframe时出错: {e}")
            
            for idx, frame in enumerate(page.frames):
                try:
                    frame_url = frame.url
                    logger.debug(f"【{self.pure_user_id}】检查Frame {idx} 是否有二维码: {frame_url}")
                    
                    # 检查frame URL是否包含 mini_login（人脸验证或短信验证页面）
                    if 'mini_login' in frame_url:
                        # 进一步确认不是滑块验证
                        is_slider = False
                        for selector in slider_selectors:
                            try:
                                element = frame.query_selector(selector)
                                if element and element.is_visible():
                                    is_slider = True
                                    break
                            except:
                                continue
                        
                        if not is_slider:
                            verification_type = self._detect_verification_type(frame)
                            if verification_type == 'login_page':
                                logger.info(
                                    f"【{self.pure_user_id}】Frame {idx} mini_login 判定为普通登录页，"
                                    f"作为「待扫码登录」上抛"
                                )
                                verification_screenshot = self._capture_verification_screenshot(page, frame=frame)
                                return True, _host.VerificationFrameWrapper(
                                    frame,
                                    verification_type='login_page',
                                    verify_url=frame_url,
                                    screenshot_path=verification_screenshot,
                                )
                            if verification_type == 'password_error':
                                logger.error(f"【{self.pure_user_id}】❌ mini_login 页面判定为账号密码错误")
                                raise _host.PasswordLoginVerificationError("账号密码错误，请检查账号密码是否正确")

                            verification_screenshot = self._capture_verification_screenshot(page, frame=frame)
                            verify_url = frame_url
                            if verification_type == 'face_verify':
                                verify_url = self._get_face_verification_url(frame) or frame_url

                            logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到 mini_login 页面（人脸验证/短信验证）")
                            logger.info(f"【{self.pure_user_id}】人脸验证/短信验证Frame URL: {frame_url}")
                            return True, _host.VerificationFrameWrapper(
                                frame,
                                verification_type=verification_type,
                                verify_url=verify_url,
                                screenshot_path=verification_screenshot
                            )
                    
                    # 检查frame的父iframe是否是alibaba-login-box
                    try:
                        # 尝试通过frame的父元素查找
                        frame_element = frame.frame_element()
                        if frame_element:
                            parent_iframe_id = frame_element.get_attribute('id')
                            if parent_iframe_id == 'alibaba-login-box':
                                logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到 alibaba-login-box（人脸验证/短信验证）")
                                logger.info(f"【{self.pure_user_id}】人脸验证/短信验证Frame URL: {frame_url}")
                                verification_type = self._detect_verification_type(frame)
                                if verification_type == 'login_page':
                                    logger.info(
                                        f"【{self.pure_user_id}】Frame {idx} alibaba-login-box 是普通登录页，"
                                        f"作为「待扫码登录」上抛"
                                    )
                                    verification_screenshot = self._capture_verification_screenshot(page, frame=frame)
                                    return True, _host.VerificationFrameWrapper(
                                        frame,
                                        verification_type='login_page',
                                        verify_url=frame_url,
                                        screenshot_path=verification_screenshot,
                                    )
                                if verification_type == 'password_error':
                                    logger.error(f"【{self.pure_user_id}】❌ alibaba-login-box 页面判定为账号密码错误")
                                    raise _host.PasswordLoginVerificationError("账号密码错误，请检查账号密码是否正确")

                                verification_screenshot = self._capture_verification_screenshot(page, frame=frame)
                                verify_url = frame_url
                                if verification_type == 'face_verify':
                                    verify_url = self._get_face_verification_url(frame) or frame_url

                                return True, _host.VerificationFrameWrapper(
                                    frame,
                                    verification_type=verification_type,
                                    verify_url=verify_url,
                                    screenshot_path=verification_screenshot
                                )
                    except _host.PasswordLoginVerificationError:
                        raise
                    except Exception:
                        pass
                    
                    # 先检查这个frame是否是滑块验证
                    is_slider_frame = False
                    for selector in slider_selectors:
                        try:
                            element = frame.query_selector(selector)
                            if element and element.is_visible():
                                logger.debug(f"【{self.pure_user_id}】Frame {idx} 包含滑块验证元素，跳过")
                                is_slider_frame = True
                                break
                        except:
                            continue
                    
                    if is_slider_frame:
                        continue  # 跳过滑块验证的frame
                    
                    # 二维码验证的选择器（更精确，避免误判滑块验证）
                    qr_selectors = [
                        'img[alt*="二维码"]',
                        'img[alt*="扫码"]',
                        'img[src*="qrcode"]',
                        'canvas[class*="qrcode"]',
                        '.qr-code',
                        '#qr-code',
                        '[class*="qr-code"]',
                        '[id*="qr-code"]'
                    ]
                    
                    # 检查是否有真正的二维码图片（不是滑块验证中的qrcode类）
                    for selector in qr_selectors:
                        try:
                            element = frame.query_selector(selector)
                            if element and element.is_visible():
                                # 进一步验证：检查是否包含滑块元素，如果包含则跳过
                                has_slider_in_frame = False
                                for slider_sel in slider_selectors:
                                    try:
                                        slider_elem = frame.query_selector(slider_sel)
                                        if slider_elem and slider_elem.is_visible():
                                            has_slider_in_frame = True
                                            break
                                    except:
                                        continue
                                
                                if not has_slider_in_frame:
                                    logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到二维码验证: {selector}")
                                    logger.info(f"【{self.pure_user_id}】二维码Frame URL: {frame_url}")
                                    return True, frame
                        except:
                            continue
                    
                    # 人脸验证的关键词（更精确）
                    face_keywords = ['拍摄脸部', '人脸验证', '人脸识别', '面部验证', '请进行人脸验证', '请完成人脸识别']
                    try:
                        frame_text = self._read_frame_text_for_detection(frame)
                        # 检查是否包含人脸验证关键词，但不包含滑块相关关键词
                        has_face_keyword = False
                        for keyword in face_keywords:
                            if keyword in frame_text:
                                has_face_keyword = True
                                break
                        
                        # 如果包含人脸验证关键词，且不包含滑块关键词，则认为是人脸验证
                        if has_face_keyword:
                            slider_keywords = ['滑块', '拖动', 'nc_', 'nc-container']
                            has_slider_keyword = any(keyword in frame_text for keyword in slider_keywords)
                            
                            if not has_slider_keyword:
                                logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到人脸验证")
                                logger.info(f"【{self.pure_user_id}】人脸验证Frame URL: {frame_url}")
                                return True, frame
                    except:
                        pass
                        
                except _host.PasswordLoginVerificationError:
                    raise
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】检查Frame {idx} 失败: {e}")
                    continue
            
            logger.info(f"【{self.pure_user_id}】未检测到二维码/人脸验证")
            return False, None
            
        except _host.PasswordLoginVerificationError:
            raise
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】检测二维码/人脸验证时出错: {e}")
            return False, None

    def _get_face_verification_url(self, frame) -> str:
        """在alibaba-login-box frame中，点击'其他验证方式'，然后找到'通过拍摄脸部'的验证按钮，获取链接"""
        try:
            logger.info(f"【{self.pure_user_id}】开始查找人脸验证链接...")
            
            # 等待frame加载完成
            time.sleep(2)
            
            # 查找"其他验证方式"链接并点击
            other_verify_clicked = False
            try:
                # 尝试通过文本内容查找所有链接
                all_links = frame.query_selector_all('a')
                for link in all_links:
                    try:
                        text = link.inner_text()
                        if '其他验证方式' in text or ('其他' in text and '验证' in text):
                            logger.info(f"【{self.pure_user_id}】找到'其他验证方式'链接，点击中...")
                            link.click()
                            time.sleep(2)  # 等待页面切换
                            other_verify_clicked = True
                            break
                    except:
                        continue
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】查找'其他验证方式'链接时出错: {e}")
            
            if not other_verify_clicked:
                logger.warning(f"【{self.pure_user_id}】未找到'其他验证方式'链接，可能已经在验证方式选择页面")
            
            # 等待页面加载
            time.sleep(2)
            
            # 查找"通过拍摄脸部"相关的验证按钮，获取href并点击按钮
            face_verify_url = None
            
            # 方法1: 使用JavaScript精确查找，获取href并点击按钮（根据HTML结构：li > div.desc包含"通过 拍摄脸部" + a.ui-button包含"立即验证"）
            try:
                href = frame.evaluate("""
                    () => {
                        // 查找所有li元素
                        const listItems = document.querySelectorAll('li');
                        for (let li of listItems) {
                            // 查找包含"通过 拍摄脸部"或"通过拍摄脸部"的desc div，但不能包含"手机"
                            const descDiv = li.querySelector('div.desc');
                            if (descDiv && !descDiv.innerText.includes('手机') && (descDiv.innerText.includes('通过 拍摄脸部') || descDiv.innerText.includes('通过拍摄脸部') || descDiv.innerText.includes('拍摄脸部'))) {
                                // 在同一li中查找"立即验证"按钮
                                const verifyButton = li.querySelector('a.ui-button, a.ui-button-small, button');
                                if (verifyButton && verifyButton.innerText && verifyButton.innerText.includes('立即验证')) {
                                    // 获取按钮的href属性
                                    const href = verifyButton.href || verifyButton.getAttribute('href') || null;
                                    // 点击按钮
                                    verifyButton.click();
                                    // 返回href
                                    return href;
                                }
                            }
                        }
                        return null;
                    }
                """)
                if href:
                    face_verify_url = href
                    logger.info(f"【{self.pure_user_id}】通过JavaScript找到'通过拍摄脸部'验证按钮的href并已点击: {face_verify_url}")
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】方法1（JavaScript）查找失败: {e}")
            
            # 方法2: 如果方法1失败，使用Playwright API查找并点击
            if not face_verify_url:
                try:
                    # 查找所有li元素
                    list_items = frame.query_selector_all('li')
                    for li in list_items:
                        try:
                            # 查找desc div
                            desc_div = li.query_selector('div.desc')
                            if desc_div:
                                desc_text = desc_div.inner_text()
                                if '手机' not in desc_text and ('通过 拍摄脸部' in desc_text or '通过拍摄脸部' in desc_text or '拍摄脸部' in desc_text):
                                    logger.info(f"【{self.pure_user_id}】找到'通过拍摄脸部'选项（方法2）")
                                    # 在同一li中查找验证按钮
                                    verify_button = li.query_selector('a.ui-button, a.ui-button-small, button')
                                    if verify_button:
                                        button_text = verify_button.inner_text()
                                        if '立即验证' in button_text:
                                            # 获取按钮的href属性
                                            href = verify_button.get_attribute('href')
                                            if href:
                                                face_verify_url = href
                                                logger.info(f"【{self.pure_user_id}】找到'通过拍摄脸部'验证按钮的href: {face_verify_url}")
                                                # 点击按钮
                                                logger.info(f"【{self.pure_user_id}】点击'立即验证'按钮...")
                                                verify_button.click()
                                                logger.info(f"【{self.pure_user_id}】已点击'立即验证'按钮")
                                                break
                        except:
                            continue
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】方法2查找失败: {e}")
            
            if face_verify_url:
                # 如果是相对路径，转换为绝对路径
                if not face_verify_url.startswith('http'):
                    base_url = frame.url.split('/iv/')[0] if '/iv/' in frame.url else 'https://passport.goofish.com'
                    if face_verify_url.startswith('/'):
                        face_verify_url = base_url + face_verify_url
                    else:
                        face_verify_url = base_url + '/' + face_verify_url
                
                return face_verify_url
            else:
                logger.warning(f"【{self.pure_user_id}】未找到人脸验证链接，返回原始frame URL")
                return frame.url if hasattr(frame, 'url') else None
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】获取人脸验证链接时出错: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def check_verification_success_fast(self, slider_button: _host.ElementHandle):
        """检查验证结果 - 极速模式"""
        try:
            logger.info(f"【{self.pure_user_id}】检查验证结果（极速模式）...")
            self.last_verification_feedback = {}
            
            # 确定滑块所在的frame（如果已知）
            target_frame = None
            if hasattr(self, '_detected_slider_frame') and self._detected_slider_frame is not None:
                target_frame = self._detected_slider_frame
                logger.info(f"【{self.pure_user_id}】在已知Frame中检查验证结果")
                # 先检查frame是否还存在（未被分离）
                try:
                    # 尝试访问frame的属性来检查是否被分离
                    _ = target_frame.url if hasattr(target_frame, 'url') else None
                except Exception as frame_check_error:
                    error_msg = str(frame_check_error).lower()
                    # 如果frame被分离（detached），说明验证成功，容器已消失
                    if 'detached' in error_msg or 'disconnected' in error_msg:
                        current_block = self._detect_post_slider_blocking_state(self.page)
                        if current_block:
                            logger.warning(
                                f"【{self.pure_user_id}】Frame已分离，但当前命中[{current_block['kind']}]，按验证失败处理"
                            )
                            return False
                        logger.info(f"【{self.pure_user_id}】✓ Frame已被分离，验证成功")
                        self.last_verification_feedback = {"status": "success", "source": "frame_detached", "message": "Frame已被分离"}
                        return True
            else:
                target_frame = self.page
                logger.info(f"【{self.pure_user_id}】在主页面检查验证结果")
            
            # 等待一小段时间让验证结果出现
            time.sleep(0.3)
            
            # 核心逻辑：首先检查frame容器状态
            # 如果容器消失，直接返回成功；如果容器还在，检查失败提示
            def check_container_status():
                """检查容器状态，返回(存在, 可见)"""
                try:
                    if target_frame == self.page:
                        container = self.page.query_selector(".nc-container")
                    else:
                        # 检查frame是否还存在（未被分离）
                        try:
                            # 再次检查frame是否被分离
                            _ = target_frame.url if hasattr(target_frame, 'url') else None
                            container = target_frame.query_selector(".nc-container")
                        except Exception as frame_error:
                            error_msg = str(frame_error).lower()
                            # 如果frame被分离（detached），说明容器已经不存在
                            if 'detached' in error_msg or 'disconnected' in error_msg:
                                logger.info(f"【{self.pure_user_id}】Frame已被分离，容器不存在")
                                return (False, False)
                            # 其他错误，继续尝试
                            raise frame_error
                    
                    if container is None:
                        return (False, False)  # 容器不存在
                    
                    try:
                        is_visible = container.is_visible()
                        return (True, is_visible)
                    except Exception as vis_error:
                        vis_error_msg = str(vis_error).lower()
                        # 如果元素被分离，说明容器不存在
                        if 'detached' in vis_error_msg or 'disconnected' in vis_error_msg:
                            logger.info(f"【{self.pure_user_id}】容器元素已被分离，容器不存在")
                            return (False, False)
                        # 无法检查可见性，假设存在且可见
                        return (True, True)
                except Exception as e:
                    error_msg = str(e).lower()
                    # 如果frame或元素被分离，说明容器不存在
                    if 'detached' in error_msg or 'disconnected' in error_msg:
                        logger.info(f"【{self.pure_user_id}】Frame或容器已被分离，容器不存在")
                        return (False, False)
                    # 其他错误，保守处理，假设存在
                    logger.warning(f"【{self.pure_user_id}】检查容器状态时出错: {e}")
                    return (True, True)
            
            # 第一次检查容器状态
            container_exists, container_visible = check_container_status()
            
            # 如果容器不存在或不可见，直接返回成功
            if not container_exists or not container_visible:
                current_block = self._detect_post_slider_blocking_state(target_frame)
                if current_block:
                    logger.warning(
                        f"【{self.pure_user_id}】滑块容器已消失，但当前命中[{current_block['kind']}]，按验证失败处理"
                    )
                    return False
                logger.info(f"【{self.pure_user_id}】✓ 滑块容器已消失（不存在或不可见），验证成功")
                self.last_verification_feedback = {"status": "success", "source": "container_missing", "message": "滑块容器已消失"}
                return True
            
            # 容器还在，需要等待更长时间并检查失败提示
            logger.info(f"【{self.pure_user_id}】滑块容器仍存在且可见，等待验证结果...")
            time.sleep(1.2)  # 等待验证结果
            
            # 再次检查容器状态
            container_exists, container_visible = check_container_status()
            
            # 如果容器消失了，返回成功
            if not container_exists or not container_visible:
                current_block = self._detect_post_slider_blocking_state(target_frame)
                if current_block:
                    logger.warning(
                        f"【{self.pure_user_id}】滑块容器二次检查已消失，但当前命中[{current_block['kind']}]，按验证失败处理"
                    )
                    return False
                logger.info(f"【{self.pure_user_id}】✓ 滑块容器已消失，验证成功")
                self.last_verification_feedback = {"status": "success", "source": "container_missing", "message": "滑块容器已消失"}
                return True
            
            # 容器还在，检查是否有验证失败提示
            logger.info(f"【{self.pure_user_id}】滑块容器仍存在，检查验证失败提示...")
            if self.check_verification_failure():
                logger.warning(f"【{self.pure_user_id}】检测到验证失败提示，验证失败")
                return False
            
            # 容器还在，但没有失败提示，可能还在验证中或验证失败
            # 再等待一小段时间后再次检查
            time.sleep(0.5)
            container_exists, container_visible = check_container_status()
            
            if not container_exists or not container_visible:
                current_block = self._detect_post_slider_blocking_state(target_frame)
                if current_block:
                    logger.warning(
                        f"【{self.pure_user_id}】滑块容器末次检查已消失，但当前命中[{current_block['kind']}]，按验证失败处理"
                    )
                    return False
                logger.info(f"【{self.pure_user_id}】✓ 滑块容器已消失，验证成功")
                self.last_verification_feedback = {"status": "success", "source": "container_missing", "message": "滑块容器已消失"}
                return True
            
            if self.check_page_changed():
                logger.info(f"【{self.pure_user_id}】✓ 页面状态已变化，按验证成功处理")
                self.last_verification_feedback = {"status": "success", "source": "page_changed", "message": "页面状态已变化"}
                return True

            if self._check_login_success_by_element(self.page):
                logger.info(f"【{self.pure_user_id}】✓ 已检测到登录成功元素，按验证成功处理")
                self.last_verification_feedback = {"status": "success", "source": "login_element_detected", "message": "已检测到登录成功元素"}
                return True

            context_login_success, _ = self._probe_context_login_during_slider(self.page)
            if context_login_success:
                logger.info(f"【{self.pure_user_id}】✓ 上下文登录状态已确认，按验证成功处理")
                self.last_verification_feedback = {
                    "status": "success",
                    "source": "context_login_confirmed",
                    "message": "上下文登录状态已确认"
                }
                return True

            # 容器仍然存在，且没有失败提示，可能是验证失败但没有显示失败提示
            # 或者验证还在进行中，但为了不无限等待，返回失败
            logger.warning(f"【{self.pure_user_id}】滑块容器仍存在且可见，且未检测到失败提示，但验证可能失败")
            self.last_verification_feedback = {
                "status": "failure",
                "source": "container_still_visible",
                "message": "滑块容器仍存在且可见，未检测到明确失败提示"
            }
            self._merge_runtime_feedback(target_frame)
            return False
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】检查验证结果时出错: {str(e)}")
            self.last_verification_feedback = {"status": "error", "source": "exception", "message": str(e)}
            self._merge_runtime_feedback(target_frame if 'target_frame' in locals() else None)
            return False

    def _detect_post_slider_blocking_state(self, primary_target=None):
        """滑块动作后兜底探测处罚页/硬拒绝，避免把容器切换误判成成功。"""
        targets = []
        for candidate in (
            primary_target,
            getattr(self, '_detected_slider_frame', None),
            self.page,
        ):
            if candidate is None:
                continue
            if any(candidate is existing for existing in targets):
                continue
            targets.append(candidate)

        for target in targets:
            try:
                current_block = self._detect_special_captcha_block(target)
            except Exception:
                current_block = None
            if not current_block:
                continue

            self.last_verification_feedback = {
                "status": "hard_block",
                "source": current_block["kind"],
                "message": current_block["message"],
                "url": current_block.get("url") or "",
                "title": current_block.get("title") or "",
            }
            try:
                self._merge_runtime_feedback(target)
            except Exception:
                pass
            return current_block

        return None

    def check_verification_failure(self):
        """检查验证失败提示"""
        try:
            logger.info(f"【{self.pure_user_id}】检查验证失败提示...")
            
            # 等待一下让失败提示出现（由于调用前已经等待了，这里等待时间缩短）
            time.sleep(1.5)

            failure_keywords = [
                "框体错误",
                "验证失败，点击框体重试",
                "点击框体重试",
                "请重试",
                "验证码错误",
                "滑动验证失败"
            ]

            search_targets = []
            if hasattr(self, '_detected_slider_frame') and self._detected_slider_frame is not None:
                search_targets.append((self._detected_slider_frame, "已知Frame"))
            search_targets.append((self.page, "主页面"))
            
            # 检查各种可能的验证失败提示元素
            failure_selectors = [
                "text=验证失败，点击框体重试",
                "text=框体错误",
                "text=点击框体重试",
                ".errloading",
                ".sm-btn-fail",
                ".wrong-cross",
                "[class*='retry']",
                "[class*='fail']",
                "[class*='error']",
                ".captcha-tips"
            ]
            
            seen_targets = set()
            for search_target, target_name in search_targets:
                if search_target is None:
                    continue

                target_key = id(search_target)
                if target_key in seen_targets:
                    continue
                seen_targets.add(target_key)

                try:
                    target_content = search_target.content()
                except Exception as content_err:
                    logger.debug(f"【{self.pure_user_id}】读取{target_name}内容失败: {content_err}")
                    target_content = ""

                for keyword in failure_keywords:
                    if keyword and keyword in target_content:
                        logger.info(f"【{self.pure_user_id}】{target_name}内容包含失败关键词: {keyword}")
                        self.last_verification_feedback = {
                            "status": "failure",
                            "source": "keyword",
                            "message": keyword,
                            "context": target_name
                        }
                        self._merge_runtime_feedback(search_target)
                        self._save_debug_snapshot(f"failure__{target_name}_keyword", search_target)
                        logger.info(f"【{self.pure_user_id}】检测到验证失败关键词，验证失败")
                        return True

                for selector in failure_selectors:
                    try:
                        element = search_target.query_selector(selector)
                        if element and element.is_visible():
                            element_text = ""
                            try:
                                element_text = element.text_content()
                            except Exception:
                                pass
                            
                            logger.info(f"【{self.pure_user_id}】在{target_name}找到验证失败提示: {selector}, 文本: {element_text}")
                            self.last_verification_feedback = {
                                "status": "failure",
                                "source": "selector",
                                "message": element_text or selector,
                                "selector": selector,
                                "context": target_name
                            }
                            self._merge_runtime_feedback(search_target)
                            self._save_debug_snapshot(f"failure__{target_name}_selector", search_target)
                            logger.info(f"【{self.pure_user_id}】检测到验证失败提示元素，验证失败")
                            return True
                    except Exception:
                        continue

            logger.info(f"【{self.pure_user_id}】未找到验证失败提示，可能验证成功了")
            return False
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】检查验证失败时出错: {e}")
            return False

    def _analyze_failure(self, attempt: int, slide_distance: float, trajectory_data: dict):
        """分析失败原因并记录"""
        try:
            failure_reason = {
                "attempt": attempt,
                "slide_distance": slide_distance,
                "total_steps": trajectory_data.get("total_steps", 0),
                "base_delay": trajectory_data.get("base_delay", 0),
                "final_left_px": trajectory_data.get("final_left_px", 0),
                "completion_used": trajectory_data.get("completion_used", False),
                "verification_feedback": self.last_verification_feedback.copy(),
                "timestamp": _host.datetime.now().isoformat()
            }
            
            # 记录失败信息
            logger.warning(f"【{self.pure_user_id}】第{attempt}次尝试失败 - 距离:{slide_distance}px, "
                         f"步数:{failure_reason['total_steps']}, "
                         f"最终位置:{failure_reason['final_left_px']}px")
            
            return failure_reason
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】分析失败原因时出错: {e}")
            return {}

    def _is_hard_block_page(self, page=None) -> bool:
        target_page = page or self.page
        if not target_page:
            return False

        try:
            special_block = self._detect_special_captcha_block(target_page)
            special_block = self._wait_for_punish_slider_dom_ready_if_needed(
                target_page,
                special_block,
                "初始页面拦截判定",
            )
            special_block = self._recover_punish_slider_shell_if_possible(
                target_page,
                special_block,
                "初始页面拦截判定",
            )
            if special_block:
                return True

            page_text = ""
            try:
                page_text = target_page.inner_text('body', timeout=1500) or ""
            except Exception:
                page_text = target_page.content() or ""

            hard_block_keywords = [
                "抱歉，页面访问出现了问题",
                "页面访问出现了问题",
                "点我反馈",
            ]
            keyword_hit = any(keyword in page_text for keyword in hard_block_keywords)

            has_qrcode = False
            has_feedback_link = False
            for selector in (
                ".bx-pu-qrcode-wrap",
                ".captcha-qrcode",
                "#bx-feedback-btn",
                "a[href*='page/feedback']",
            ):
                try:
                    element = target_page.query_selector(selector)
                    if element:
                        if selector in (".bx-pu-qrcode-wrap", ".captcha-qrcode"):
                            has_qrcode = True
                        else:
                            has_feedback_link = True
                except Exception:
                    continue

            has_slider_button = False
            for selector in ("#nc_1_n1z", ".btn_slide", ".sm-btn", ".sm-btn-wrapper", ".nc_scale"):
                try:
                    element = target_page.query_selector(selector)
                    if element:
                        has_slider_button = True
                        break
                except Exception:
                    continue

            if keyword_hit and (has_qrcode or has_feedback_link) and not has_slider_button:
                return True
        except Exception:
            pass

        return False

    def _detect_special_captcha_block(self, target=None) -> Optional[Dict[str, Any]]:
        """检测验证码处罚页/反馈拦截页，避免把不可解风控页继续当普通滑块拖。"""
        target_page = target or self.page
        if not target_page:
            return None

        try:
            detached_runtime = False


            try:
                current_url = str(getattr(target_page, 'url', '') or '')
            except Exception:
                current_url = ''
            current_url_lower = current_url.lower()

            current_title = ''
            try:
                raw_title = target_page.title() if callable(getattr(target_page, 'title', None)) else getattr(target_page, 'title', '')
                current_title = str(raw_title or '')
            except Exception as title_error:
                detached_runtime = _host._is_runtime_detached_error(title_error) or detached_runtime
                current_title = ''
            current_title_lower = current_title.lower()

            page_text = ''
            try:
                page_text = target_page.inner_text('body', timeout=1500) or ''
            except Exception as text_error:
                detached_runtime = _host._is_runtime_detached_error(text_error) or detached_runtime
                try:
                    page_text = target_page.content() or ''
                except Exception as content_error:
                    detached_runtime = _host._is_runtime_detached_error(content_error) or detached_runtime
                    page_text = ''
            page_text_lower = str(page_text or '').lower()

            has_slider_button = False
            for selector in ("#nc_1_n1z", ".btn_slide", ".sm-btn", ".nc_scale"):
                try:
                    element = target_page.query_selector(selector)
                    if element and element.is_visible():
                        has_slider_button = True
                        break
                except Exception:
                    continue

            # `#nocaptcha` / `.sm-btn-wrapper` 常只是处罚页的外壳容器；
            # 只有真正的轨道/按钮出现时，才算“仍可操作的滑块”。
            has_slider_track = False
            for selector in ("#nc_1_n1t", ".nc_scale"):
                try:
                    element = target_page.query_selector(selector)
                    if element and element.is_visible():
                        has_slider_track = True
                        break
                except Exception:
                    continue

            has_operable_slider = has_slider_button or has_slider_track
            if detached_runtime and not page_text and not has_operable_slider:
                logger.debug(
                    f"【{self.pure_user_id}】检测验证码处罚页时目标已分离，忽略旧 frame 残留状态: "
                    f"{current_url or 'unknown'}"
                )
                return None

            punish_tokens = (
                'punish?x5secdata',
                'action=captcha',
                'purecaptcha=true',
                'x5step=2',
            )
            punish_hit_count = sum(1 for token in punish_tokens if token in current_url_lower)
            punish_title_hit = ('验证码拦截' in current_title) or ('captcha intercept' in current_title_lower)
            punish_text_hit = ('验证码拦截' in page_text) or ('验证失败，点击框体重试' in page_text)
            if punish_hit_count >= 2 or punish_title_hit or punish_text_hit:
                if has_operable_slider:
                    logger.debug(
                        f"【{self.pure_user_id}】当前命中 pureCaptcha/处罚页特征，但页面仍存在可操作滑块，继续按普通滑块处理"
                    )
                else:
                    return {
                        'kind': 'punish_captcha',
                        'url': current_url,
                        'title': current_title,
                        'message': '当前命中阿里验证码拦截处罚页（pureCaptcha），且页面不存在可操作滑块',
                    }

            hard_block_keywords = [
                "抱歉，页面访问出现了问题",
                "页面访问出现了问题",
                "点我反馈",
            ]
            keyword_hit = any(keyword in page_text for keyword in hard_block_keywords)
            has_qrcode = False
            has_feedback_link = False
            for selector in (
                ".bx-pu-qrcode-wrap",
                ".captcha-qrcode",
                "#bx-feedback-btn",
                "a[href*='page/feedback']",
            ):
                try:
                    element = target_page.query_selector(selector)
                    if element:
                        if selector in (".bx-pu-qrcode-wrap", ".captcha-qrcode"):
                            has_qrcode = True
                        else:
                            has_feedback_link = True
                except Exception:
                    continue

            if keyword_hit and (has_qrcode or has_feedback_link) and not has_operable_slider:
                return {
                    'kind': 'feedback_block',
                    'url': current_url,
                    'title': current_title,
                    'message': '当前命中反馈二维码/处罚页，不存在可操作滑块',
                }
        except Exception:
            return None

        return None

    def _has_recoverable_punish_slider_shell(self, target) -> bool:
        """识别 pureCaptcha 壳页里仍可被点活的滑块容器。"""
        if not target:
            return False

        shell_selectors = (
            ".errloading",
            "[data-nc-status='error']",
            "#nocaptcha",
            ".nc-container",
            ".nc_wrapper",
            ".nc_scale",
            ".sm-btn-wrapper",
            "#baxia-dialog-content",
        )
        for selector in shell_selectors:
            try:
                element = target.query_selector(selector)
                if not element:
                    continue
                try:
                    if element.is_visible():
                        return True
                except Exception:
                    return True
            except Exception:
                continue
        return False

    def _has_ready_punish_slider_dom(self, target) -> bool:
        """处罚页经常先出壳子、后出真滑块，这里只看关键DOM是否已出现。"""
        if not target:
            return False

        button_selectors = (
            "#nc_1_n1z",
            ".btn_slide",
            ".sm-btn",
        )
        track_selectors = (
            "#nc_1_n1t",
            ".nc_scale",
        )
        text_selectors = (
            "#nc_1__scale_text",
            ".captcha-tips",
        )

        has_button = False
        has_track = False
        has_text = False

        for selector in button_selectors:
            try:
                if target.query_selector(selector):
                    has_button = True
                    break
            except Exception:
                continue

        for selector in track_selectors:
            try:
                if target.query_selector(selector):
                    has_track = True
                    break
            except Exception:
                continue

        for selector in text_selectors:
            try:
                element = target.query_selector(selector)
                if element and str(element.text_content() or "").strip():
                    has_text = True
                    break
            except Exception:
                continue

        return has_button and (has_track or has_text)

    def _wait_for_punish_slider_dom_ready_if_needed(
        self,
        target,
        current_block: Optional[Dict[str, Any]],
        context_label: str,
        max_wait_seconds: float = 1.2,
        poll_interval: float = 0.25,
    ) -> Optional[Dict[str, Any]]:
        """pureCaptcha 页面真实滑块会晚一点挂出来，先给一小段收敛窗口，别太早判死刑。"""
        if not current_block or current_block.get("kind") != "punish_captcha":
            return current_block

        if self._has_ready_punish_slider_dom(target):
            logger.info(f"【{self.pure_user_id}】{context_label} 检测到处罚页真实滑块DOM已就绪，继续按正常滑块处理")
            return None

        deadline = time.time() + max(0.0, max_wait_seconds)
        refreshed_block = current_block
        while time.time() < deadline:
            time.sleep(max(0.05, poll_interval))
            if self._has_ready_punish_slider_dom(target):
                logger.info(f"【{self.pure_user_id}】{context_label} 处罚页真实滑块DOM已延迟出现，继续按正常滑块处理")
                return None
            refreshed_block = self._detect_special_captcha_block(target)
            if not refreshed_block:
                logger.info(f"【{self.pure_user_id}】{context_label} 处罚页状态已恢复，继续按正常滑块处理")
                return None

        return refreshed_block

    def _click_first_activation_target(self, target, selectors: List[Tuple[str, str]], context_label: str) -> bool:
        """在指定 page/frame 中点击第一个可用激活区域。"""
        if not target:
            return False

        for selector, desc in selectors:
            try:
                element = target.query_selector(selector)
                if not element:
                    continue
                try:
                    if not element.is_visible():
                        continue
                except Exception:
                    pass

                try:
                    box = element.bounding_box()
                except Exception:
                    box = None

                try:
                    if box and getattr(self, "page", None):
                        click_x = box["x"] + box["width"] / 2
                        click_y = box["y"] + box["height"] / 2
                        self.page.mouse.click(click_x, click_y)
                    else:
                        element.click(timeout=1000)
                    logger.info(f"【{self.pure_user_id}】已点击{context_label}激活区域[{desc}]: {selector}")
                    return True
                except Exception as click_err:
                    logger.debug(f"【{self.pure_user_id}】点击{context_label}激活区域[{desc}]失败: {click_err}")
                    continue
            except Exception as find_err:
                logger.debug(f"【{self.pure_user_id}】查找{context_label}激活区域[{desc}]失败: {find_err}")
                continue
        return False

    def _recover_punish_slider_shell_if_possible(
        self,
        target,
        current_block: Optional[Dict[str, Any]],
        context_label: str,
    ) -> Optional[Dict[str, Any]]:
        """对可恢复的 pureCaptcha 壳页先尝试点活，再重新探测。"""
        if not current_block or current_block.get("kind") != "punish_captcha":
            return current_block
        if not self._has_recoverable_punish_slider_shell(target):
            return current_block

        activation_selectors = [
            (".errloading", "错误提示区"),
            ("[data-nc-status='error']", "NC错误状态"),
            (".nc-container", "滑块容器"),
            ("#nocaptcha", "NoCaptcha容器"),
            (".nc_wrapper", "滑块包装器"),
            (".nc_scale", "滑块轨道"),
            (".sm-btn-wrapper", "滑块按钮包装器"),
            ("#baxia-dialog-content", "验证码对话框"),
        ]
        if not self._click_first_activation_target(target, activation_selectors, context_label):
            return current_block

        time.sleep(0.8)
        refreshed_block = self._detect_special_captcha_block(target)
        if refreshed_block:
            logger.info(
                f"【{self.pure_user_id}】{context_label} pureCaptcha 壳页点活后仍是硬拦截[{refreshed_block.get('kind')}]"
            )
        else:
            logger.info(f"【{self.pure_user_id}】{context_label} pureCaptcha 壳页已点活，继续按正常滑块处理")
        return refreshed_block

    def check_page_changed(self):
        """检查页面是否改变"""
        try:
            # 检查页面标题是否改变
            current_title = self.page.title()
            logger.info(f"【{self.pure_user_id}】当前页面标题: {current_title}")

            if self._looks_like_verification_title(current_title):
                logger.info(f"【{self.pure_user_id}】页面标题仍像验证页，暂不判定成功")
                return False

            # 检查URL是否改变
            current_url = self.page.url
            logger.info(f"【{self.pure_user_id}】当前页面URL: {current_url}")

            if self._looks_like_verification_url(current_url):
                logger.info(f"【{self.pure_user_id}】页面URL仍处于验证链路，暂不判定成功")
                return False

            logger.info(f"【{self.pure_user_id}】页面已脱离验证链路，判定验证成功")
            return True
            
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】检查页面改变时出错: {e}")
            return False

    def _is_password_scene_success_sample(self, history_path: str, record: Optional[Dict[str, Any]] = None) -> bool:
        if not self._is_password_login_scene():
            return True

        parts = [os.path.basename(str(history_path or ""))]
        if isinstance(record, dict):
            parts.append(str(record.get("user_id") or ""))

        sample_text = " ".join(parts).lower()
        if not sample_text:
            return False

        if any(token in sample_text for token in ("cookie", "import_user_cookie", "ui_cookie")):
            return False

        return any(token in sample_text for token in ("password", "pwd"))

