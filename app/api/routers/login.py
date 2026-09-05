"""Login / register / captcha / login-security routes (Strangler Fig P1).

Mechanically extracted from reply_server.py at main@0aa4100; behavior-preserving.
External (reply_server) symbols resolve via ctx at request time - see app/api/state.py.
"""

from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio
import base64
import hashlib
import io
import json
import os
import random
import re
import secrets
import time
import urllib.parse
from urllib.parse import unquote
from urllib import request as urllib_request, error as urllib_error

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, Header,
                     HTTPException, Request, Response, UploadFile, status)
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from pydantic import BaseModel

from app.api.state import ctx  # noqa: F401  (module-level helpers; factory param shadows with same singleton)


# 防暴力破解/验证码状态容器留在 reply_server（tests 直接操作 reply_server.login_ip_tracker 等）
# -> 本模块 helpers 经 ctx.<name> 请求时解析访问

# ========================= 登录安全 helpers =========================
def cleanup_login_trackers():
    """清理过期的登录追踪记录"""
    current_time = time.time()
    
    # 清理IP追踪记录
    expired_ips = []
    for ip, data in ctx.login_ip_tracker.items():
        # 如果封禁已过期且超出窗口时间，则清理
        if data.get('blocked_until', 0) < current_time:
            if current_time - data.get('last_attempt', 0) > ctx.BRUTE_FORCE_CONFIG['ip_window_seconds'] * 2:
                expired_ips.append(ip)
    for ip in expired_ips:
        del ctx.login_ip_tracker[ip]
    
    # 清理用户名追踪记录
    expired_users = []
    for username, data in ctx.login_user_tracker.items():
        if data.get('locked_until', 0) < current_time:
            if current_time - data.get('last_attempt', 0) > ctx.BRUTE_FORCE_CONFIG['user_window_seconds'] * 2:
                expired_users.append(username)
    for username in expired_users:
        del ctx.login_user_tracker[username]


def check_ip_blocked(client_ip: str) -> tuple[bool, str, int]:
    """
    检查IP是否被封禁
    返回: (是否封禁, 原因, 剩余封禁秒数)
    """
    # 检查永久黑名单
    if client_ip in ctx.ip_blacklist:
        return True, "IP已被永久封禁", -1
    
    current_time = time.time()
    
    if client_ip in ctx.login_ip_tracker:
        data = ctx.login_ip_tracker[client_ip]
        
        # 检查是否在封禁期内
        if data.get('blocked_until', 0) > current_time:
            remaining = int(data['blocked_until'] - current_time)
            return True, f"IP登录失败次数过多，请{remaining}秒后再试", remaining
        
        # 检查窗口内的失败次数
        if current_time - data.get('first_attempt', 0) <= ctx.BRUTE_FORCE_CONFIG['ip_window_seconds']:
            if data.get('attempts', 0) >= ctx.BRUTE_FORCE_CONFIG['ip_max_attempts']:
                # 触发封禁
                block_duration = ctx.BRUTE_FORCE_CONFIG['ip_block_seconds']
                data['blocked_until'] = current_time + block_duration
                logger.warning(f"🚫 IP {client_ip} 登录失败{data['attempts']}次，封禁{block_duration}秒")
                return True, f"登录失败次数过多，请{block_duration}秒后再试", block_duration
    
    return False, "", 0


def check_user_locked(username: str) -> tuple[bool, str, int]:
    """
    检查用户名是否被锁定
    返回: (是否锁定, 原因, 剩余锁定秒数)
    """
    current_time = time.time()
    
    if username in ctx.login_user_tracker:
        data = ctx.login_user_tracker[username]
        
        # 检查是否在锁定期内
        if data.get('locked_until', 0) > current_time:
            remaining = int(data['locked_until'] - current_time)
            return True, f"账户已被临时锁定，请{remaining}秒后再试", remaining
        
        # 检查窗口内的失败次数
        if current_time - data.get('first_attempt', 0) <= ctx.BRUTE_FORCE_CONFIG['user_window_seconds']:
            if data.get('attempts', 0) >= ctx.BRUTE_FORCE_CONFIG['user_max_attempts']:
                # 触发锁定
                lock_duration = ctx.BRUTE_FORCE_CONFIG['user_lock_seconds']
                data['locked_until'] = current_time + lock_duration
                logger.warning(f"🔒 用户 {username} 登录失败{data['attempts']}次，锁定{lock_duration}秒")
                return True, f"账户登录失败次数过多，已被临时锁定，请{lock_duration}秒后再试", lock_duration
    
    return False, "", 0


def record_login_failure(client_ip: str, username: str):
    """记录登录失败"""
    current_time = time.time()
    
    # 更新IP记录
    if client_ip not in ctx.login_ip_tracker:
        ctx.login_ip_tracker[client_ip] = {
            'attempts': 0,
            'first_attempt': current_time,
            'last_attempt': current_time,
            'blocked_until': 0
        }
    
    ip_data = ctx.login_ip_tracker[client_ip]
    
    # 如果超出窗口时间，重置计数
    if current_time - ip_data['first_attempt'] > ctx.BRUTE_FORCE_CONFIG['ip_window_seconds']:
        ip_data['attempts'] = 0
        ip_data['first_attempt'] = current_time
    
    ip_data['attempts'] += 1
    ip_data['last_attempt'] = current_time
    
    # 检查是否需要加入永久黑名单
    if ip_data['attempts'] >= ctx.BRUTE_FORCE_CONFIG['auto_blacklist_threshold']:
        ctx.ip_blacklist.add(client_ip)
        logger.error(f"⛔ IP {client_ip} 登录失败{ip_data['attempts']}次，已加入永久黑名单！")
    
    # 更新用户名记录
    if username:
        if username not in ctx.login_user_tracker:
            ctx.login_user_tracker[username] = {
                'attempts': 0,
                'first_attempt': current_time,
                'last_attempt': current_time,
                'locked_until': 0
            }
        
        user_data = ctx.login_user_tracker[username]
        
        # 如果超出窗口时间，重置计数
        if current_time - user_data['first_attempt'] > ctx.BRUTE_FORCE_CONFIG['user_window_seconds']:
            user_data['attempts'] = 0
            user_data['first_attempt'] = current_time
        
        user_data['attempts'] += 1
        user_data['last_attempt'] = current_time


def record_login_success(client_ip: str, username: str):
    """记录登录成功，重置计数"""
    if client_ip in ctx.login_ip_tracker:
        ctx.login_ip_tracker[client_ip]['attempts'] = 0
    if username and username in ctx.login_user_tracker:
        ctx.login_user_tracker[username]['attempts'] = 0


def check_username_rate_limit(username: str):
    if not username:
        return False, 0
    current_time = time.time()
    window = ctx.BRUTE_FORCE_CONFIG.get('username_rate_window', 60)
    max_attempts = ctx.BRUTE_FORCE_CONFIG.get('username_rate_per_minute', 5)
    if username not in ctx.username_rate_tracker:
        ctx.username_rate_tracker[username] = []
    timestamps = ctx.username_rate_tracker[username]
    timestamps[:] = [t for t in timestamps if current_time - t < window]
    if len(timestamps) >= max_attempts:
        remaining = int(window - (current_time - timestamps[0]))
        return True, remaining
    return False, 0


def record_username_rate(username: str):
    if not username:
        return
    if username not in ctx.username_rate_tracker:
        ctx.username_rate_tracker[username] = []
    ctx.username_rate_tracker[username].append(time.time())


def get_response_delay(client_ip: str) -> float:
    """计算响应延迟时间（失败次数越多，延迟越长）"""
    if client_ip not in ctx.login_ip_tracker:
        return 0
    
    attempts = ctx.login_ip_tracker[client_ip].get('attempts', 0)
    if attempts <= 1:
        return 0
    
    delay = ctx.BRUTE_FORCE_CONFIG['response_delay_base'] + \
            (attempts - 1) * ctx.BRUTE_FORCE_CONFIG['response_delay_multiplier']
    return min(delay, ctx.BRUTE_FORCE_CONFIG['max_response_delay'])


def is_captcha_required(client_ip: str, user_agent: str = "") -> bool:
    """检查是否需要验证码(Codex豁免)"""
    if ctx.is_codex_browser(user_agent):
        return False
    if client_ip not in ctx.login_ip_tracker:
        return False
    attempts = ctx.login_ip_tracker[client_ip].get('attempts', 0)
    return attempts >= ctx.BRUTE_FORCE_CONFIG.get('captcha_require_failures', 2)


def generate_captcha_image(code: str) -> bytes:
    """生成验证码图片"""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import random
    
    # 图片尺寸
    width, height = 150, 50
    
    # 创建图片
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    # 添加干扰线
    for _ in range(5):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)), width=1)
    
    # 添加干扰点
    for _ in range(50):
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.point((x, y), fill=(random.randint(0, 150), random.randint(0, 150), random.randint(0, 150)))
    
    # 尝试加载字体，如果失败则使用默认字体
    font = None
    font_paths = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/ARIALBD.TTF", 
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    
    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, 32)
            break
        except:
            continue
    
    if font is None:
        # 使用默认字体
        font = ImageFont.load_default()
    
    # 绘制验证码字符
    colors = [
        (0, 0, 139),      # 深蓝
        (139, 0, 0),      # 深红
        (0, 100, 0),      # 深绿
        (139, 69, 19),    # 棕色
        (75, 0, 130),     # 靛蓝
    ]
    
    x_offset = 15
    for i, char in enumerate(code):
        # 随机颜色
        color = random.choice(colors)
        # 随机角度（-15到15度）
        angle = random.randint(-15, 15)
        
        # 创建单个字符的图片用于旋转
        char_image = Image.new('RGBA', (35, 45), (255, 255, 255, 0))
        char_draw = ImageDraw.Draw(char_image)
        char_draw.text((5, 5), char, font=font, fill=color)
        
        # 旋转
        char_image = char_image.rotate(angle, expand=False, fillcolor=(255, 255, 255, 0))
        
        # 粘贴到主图
        y_offset = random.randint(2, 10)
        image.paste(char_image, (x_offset, y_offset), char_image)
        x_offset += 28
    
    # 添加轻微模糊
    image = image.filter(ImageFilter.SMOOTH)
    
    # 转换为bytes
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer.getvalue()


def generate_captcha_code(length: int = 4) -> str:
    """生成验证码字符串（排除容易混淆的字符）"""
    # 排除 0, O, 1, I, l 等容易混淆的字符
    chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(chars) for _ in range(length))


def cleanup_expired_captchas():
    """清理过期的验证码"""
    current_time = time.time()
    expired = [cid for cid, data in ctx.captcha_storage.items() 
               if current_time - data['created_at'] > ctx.CAPTCHA_EXPIRE_SECONDS]
    for cid in expired:
        del ctx.captcha_storage[cid]


def verify_login_captcha(captcha_id: str, captcha_code: str, client_ip: str) -> tuple[bool, str]:
    """
    验证登录验证码
    返回: (是否验证成功, 错误消息)
    """
    if not captcha_id or not captcha_code:
        return False, "请输入验证码"
    
    if captcha_id not in ctx.captcha_storage:
        return False, "验证码已过期，请刷新"
    
    captcha_data = ctx.captcha_storage[captcha_id]
    
    # 检查是否过期
    if time.time() - captcha_data['created_at'] > ctx.CAPTCHA_EXPIRE_SECONDS:
        del ctx.captcha_storage[captcha_id]
        return False, "验证码已过期，请刷新"
    
    # 检查IP是否匹配（防止验证码被其他IP使用）
    if captcha_data.get('ip') and captcha_data['ip'] != client_ip:
        return False, "验证码无效，请刷新"
    
    # 验证码比较（忽略大小写）
    if captcha_code.upper() != captcha_data['code'].upper():
        return False, "验证码错误"
    
    # 验证成功后删除验证码（一次性使用）
    del ctx.captcha_storage[captcha_id]
    return True, ""


def get_ip_failure_count(client_ip: str) -> int:
    """获取IP的登录失败次数"""
    if client_ip not in ctx.login_ip_tracker:
        return 0
    return ctx.login_ip_tracker[client_ip].get('attempts', 0)


# ========================= request/response models =========================
class LoginRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None
    verification_code: Optional[str] = None
    captcha_id: Optional[str] = None      # 验证码ID
    captcha_code: Optional[str] = None    # 用户输入的验证码


class LoginResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    message: str
    user_id: Optional[int] = None
    username: Optional[str] = None
    is_admin: Optional[bool] = None
    captcha_required: Optional[bool] = None  # 是否需要验证码


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    verification_code: str


class RegisterResponse(BaseModel):
    success: bool
    message: str


class SendCodeRequest(BaseModel):
    email: str
    session_id: Optional[str] = None
    type: Optional[str] = 'register'  # 'register' 或 'login'


class SendCodeResponse(BaseModel):
    success: bool
    message: str


class CaptchaRequest(BaseModel):
    session_id: str


class CaptchaResponse(BaseModel):
    success: bool
    captcha_image: str
    session_id: str
    message: str


class VerifyCaptchaRequest(BaseModel):
    session_id: str
    captcha_code: str


class VerifyCaptchaResponse(BaseModel):
    success: bool
    message: str



def create_login_router(ctx, session_service, security, verify_dependency, admin_username) -> APIRouter:
    """Factory keeps the established create_auth_router dependency style."""
    router = APIRouter()
    @router.get('/captcha/generate')
    async def generate_captcha(request: Request):
        """生成验证码图片"""
        # 获取客户端IP
        client_ip = ctx.get_client_ip(request)
    
        # 清理过期验证码
        cleanup_expired_captchas()
    
        # 生成验证码
        code = generate_captcha_code(4)
        captcha_id = secrets.token_urlsafe(16)
    
        # 存储验证码
        ctx.captcha_storage[captcha_id] = {
            'code': code,
            'created_at': time.time(),
            'ip': client_ip
        }
    
        # 生成图片
        image_bytes = generate_captcha_image(code)
    
        logger.debug(f"🔢 生成验证码: {captcha_id[:8]}... (IP: {client_ip})")
    
        # 返回图片和ID
        return StreamingResponse(
            io.BytesIO(image_bytes),
            media_type="image/png",
            headers={
                "X-Captcha-Id": captcha_id,
                "Access-Control-Expose-Headers": "X-Captcha-Id",
                "Cache-Control": "no-cache, no-store, must-revalidate"
            }
        )


        required = is_captcha_required(client_ip)
        failure_count = get_ip_failure_count(client_ip)
    
        return {
            'required': required,
            'failure_count': failure_count,
            'threshold': ctx.BRUTE_FORCE_CONFIG.get('captcha_require_failures', 2)
        }

    @router.get("/captcha/check-required")
    async def check_captcha_required(request: Request):
        client_ip = ctx.get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "")
        required = is_captcha_required(client_ip, user_agent)
        failure_count = get_ip_failure_count(client_ip)
        return {
            "required": required,
            "codex_browser": ctx.is_codex_browser(user_agent),
            "failure_count": failure_count,
            "threshold": ctx.BRUTE_FORCE_CONFIG.get("captcha_require_failures", 2)
        }

    @router.get('/login.html', response_class=HTMLResponse)
    async def login_page():
        login_path = os.path.join(ctx.static_dir, 'login.html')
        if os.path.exists(login_path):
            with open(login_path, 'r', encoding='utf-8') as f:
                return HTMLResponse(f.read())
        else:
            return HTMLResponse('<h3>Login page not found</h3>')

    @router.get('/register.html', response_class=HTMLResponse)
    async def register_page():
        # 检查注册是否开启
        from db_manager import db_manager
        registration_enabled = ctx.db_manager.get_system_setting('registration_enabled')
        if registration_enabled != 'true':
            return HTMLResponse('''
            <!DOCTYPE html>
            <html>
            <head>
                <title>注册已关闭</title>
                <meta charset="utf-8">
                <style>
                    body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                    .message { color: #666; font-size: 18px; }
                    .back-link { margin-top: 20px; }
                    .back-link a { color: #007bff; text-decoration: none; }
                </style>
            </head>
            <body>
                <h2>🚫 注册功能已关闭</h2>
                <p class="message">系统管理员已关闭用户注册功能</p>
                <div class="back-link">
                    <a href="/">← 返回首页</a>
                </div>
            </body>
            </html>
            ''', status_code=403)

        register_path = os.path.join(ctx.static_dir, 'register.html')
        if os.path.exists(register_path):
            with open(register_path, 'r', encoding='utf-8') as f:
                return HTMLResponse(f.read())
        else:
            return HTMLResponse('<h3>Register page not found</h3>')

    @router.post('/login')
    async def login(login_request: LoginRequest, request: Request):
        from db_manager import db_manager
    
        # 获取客户端IP（考虑代理）
        client_ip = ctx.get_client_ip(request)
    
        # 定期清理过期记录
        cleanup_login_trackers()
    
        # 检查IP是否被封禁
        ip_blocked, ip_block_reason, ip_remaining = check_ip_blocked(client_ip)
        if ip_blocked:
            logger.warning(f"🚫 IP {client_ip} 尝试登录但已被封禁: {ip_block_reason}")
            return LoginResponse(
                success=False,
                message=ip_block_reason
            )
    
        # 获取登录标识（用户名或邮箱）
        login_identifier = login_request.username or login_request.email or ''
    
        # 检查用户名是否被锁定
        if login_identifier:
            user_locked, user_lock_reason, user_remaining = check_user_locked(login_identifier)
            if user_locked:
                logger.warning(f"🔒 用户 {login_identifier} 尝试登录但账户已锁定 (IP: {client_ip})")
                # 即使锁定也要记录IP的尝试
                record_login_failure(client_ip, login_identifier)
                return LoginResponse(
                    success=False,
                    message=user_lock_reason,
                    captcha_required=True
                )
    
        # 用户名速率限制
        if login_identifier:
            rate_limited, rate_remaining = check_username_rate_limit(login_identifier)
            if rate_limited:
                logger.warning("[RATE] user=" + login_identifier + " too many attempts")
                return LoginResponse(
                    success=False,
                    message="请求过于频繁，请稍后重试",
                    captcha_required=True
                )
            record_username_rate(login_identifier)
    
        # 检查是否需要验证码
        captcha_enabled_str = ctx.db_manager.get_system_setting('login_captcha_enabled')
        captcha_enabled = captcha_enabled_str == 'true' if captcha_enabled_str is not None else True

        if captcha_enabled:
            user_agent = request.headers.get("User-Agent", "")
            if ctx.is_codex_browser(user_agent):
                logger.info("Codex browser, skip captcha")
            else:
                captcha_valid, captcha_error = verify_login_captcha(
                    login_request.captcha_id,
                    login_request.captcha_code,
                    client_ip
                )
                if not captcha_valid:
                    ctx.audit_event(
                        category="auth",
                        action="login",
                        status="failed",
                        request=request,
                        message="Login captcha failed",
                        details={
                            "login_type": "captcha",
                            "identifier": login_identifier,
                            "captcha_id": login_request.captcha_id,
                            "captcha_code": login_request.captcha_code,
                            "error": captcha_error,
                        },
                    )
                    logger.warning(f"🔢 IP {client_ip} 验证码验证失败: {captcha_error}")
                    return LoginResponse(
                    success=False,
                    message=captcha_error,
                    captcha_required=True
                    )
                logger.info(f"🔢 IP {client_ip} 验证码验证成功")
        else:
            logger.info(f"🔢 IP {client_ip} 登录验证码已关闭，跳过验证")

        # 判断登录方式
        if login_request.username and login_request.password:
            # 用户名/密码登录
            logger.info(f"【{login_request.username}】尝试用户名登录 (IP: {client_ip})")

            # 统一使用用户表验证（包括admin用户）
            if ctx.db_manager.verify_user_password(login_request.username, login_request.password):
                user = ctx.db_manager.get_user_by_username(login_request.username)
                if user:
                    # 登录成功，重置计数
                    record_login_success(client_ip, login_request.username)

                    # 获取is_admin状态
                    user_is_admin = user.get('is_admin', False)

                    token = ctx.session_service.issue(user)
                    ctx.audit_event(
                        category="auth",
                        action="login",
                        status="success",
                        actor={"user_id": user["id"], "username": user["username"], "is_admin": user_is_admin},
                        request=request,
                        resource_type="user",
                        resource_id=user["id"],
                        message="Login succeeded",
                        details={"login_type": "username", "identifier": login_request.username},
                    )

                    # 区分管理员和普通用户的日志
                    if user_is_admin:
                        logger.info(f"【{user['username']}#{user['id']}】登录成功（管理员）(IP: {client_ip})")
                    else:
                        logger.info(f"【{user['username']}#{user['id']}】登录成功 (IP: {client_ip})")

                    return LoginResponse(
                        success=True,
                        token=token,
                        message="登录成功",
                        user_id=user['id'],
                        username=user['username'],
                        is_admin=user_is_admin
                    )

            # 登录失败，记录失败次数
            record_login_failure(client_ip, login_request.username)
        
            # 计算响应延迟（防止快速暴力破解）
            delay = get_response_delay(client_ip)
            if delay > 0:
                logger.info(f"🐢 IP {client_ip} 登录失败，延迟响应 {delay:.1f} 秒")
                await asyncio.sleep(delay)
        
            logger.warning(f"【{login_request.username}】登录失败：用户名或密码错误 (IP: {client_ip})")
            # 检查下次是否需要验证码
            next_captcha_required = is_captcha_required(client_ip)
            ctx.audit_event(
                category="auth",
                action="login",
                status="failed",
                request=request,
                message="Login failed",
                details={
                    "login_type": "username",
                    "username": login_request.username,
                    "password": login_request.password,
                    "captcha_required": next_captcha_required,
                },
            )
            return LoginResponse(
                success=False,
                message="用户名或密码错误",
                captcha_required=next_captcha_required
            )

        elif login_request.email and login_request.password:
            # 邮箱/密码登录
            logger.info(f"【{login_request.email}】尝试邮箱密码登录 (IP: {client_ip})")

            user = ctx.db_manager.get_user_by_email(login_request.email)
            if user and ctx.db_manager.verify_user_password(user['username'], login_request.password):
                # 登录成功，重置计数
                record_login_success(client_ip, login_request.email)

                # 获取is_admin状态
                user_is_admin = user.get('is_admin', False)

                token = ctx.session_service.issue(user)

                if user_is_admin:
                    logger.info(f"【{user['username']}#{user['id']}】邮箱登录成功（管理员）(IP: {client_ip})")
                else:
                    logger.info(f"【{user['username']}#{user['id']}】邮箱登录成功 (IP: {client_ip})")

                return LoginResponse(
                    success=True,
                    token=token,
                    message="登录成功",
                    user_id=user['id'],
                    username=user['username'],
                    is_admin=user_is_admin
                )

            # 登录失败，记录失败次数
            record_login_failure(client_ip, login_request.email)
        
            # 计算响应延迟
            delay = get_response_delay(client_ip)
            if delay > 0:
                await asyncio.sleep(delay)
        
            logger.warning(f"【{login_request.email}】邮箱登录失败：邮箱或密码错误 (IP: {client_ip})")
            next_captcha_required = is_captcha_required(client_ip)
            return LoginResponse(
                success=False,
                message="邮箱或密码错误",
                captcha_required=next_captcha_required
            )

        elif login_request.email and login_request.verification_code:
            # 邮箱/验证码登录
            logger.info(f"【{login_request.email}】尝试邮箱验证码登录 (IP: {client_ip})")

            # 验证邮箱验证码
            if not ctx.db_manager.verify_email_code(login_request.email, login_request.verification_code, 'login'):
                # 验证码错误也记录失败
                record_login_failure(client_ip, login_request.email)
                delay = get_response_delay(client_ip)
                if delay > 0:
                    await asyncio.sleep(delay)

                logger.warning(f"【{login_request.email}】验证码登录失败：验证码错误或已过期 (IP: {client_ip})")
                next_captcha_required = is_captcha_required(client_ip)
                return LoginResponse(
                    success=False,
                    message="验证码错误或已过期",
                    captcha_required=next_captcha_required
                )

            # 获取用户信息
            user = ctx.db_manager.get_user_by_email(login_request.email)
            if not user:
                logger.warning(f"【{login_request.email}】验证码登录失败：用户不存在 (IP: {client_ip})")
                return LoginResponse(
                    success=False,
                    message="用户不存在"
                )

            # 登录成功，重置计数
            record_login_success(client_ip, login_request.email)

            # 获取is_admin状态
            user_is_admin = user.get('is_admin', False)

            token = ctx.session_service.issue(user)

            if user_is_admin:
                logger.info(f"【{user['username']}#{user['id']}】验证码登录成功（管理员）(IP: {client_ip})")
            else:
                logger.info(f"【{user['username']}#{user['id']}】验证码登录成功 (IP: {client_ip})")

            return LoginResponse(
                success=True,
                token=token,
                message="登录成功",
                user_id=user['id'],
                username=user['username'],
                is_admin=user_is_admin
            )

        else:
            return LoginResponse(
                success=False,
                message="请提供有效的登录信息"
            )

    @router.get('/admin/security/login-stats')
    async def get_login_security_stats(admin_user: Dict[str, Any] = Depends(ctx.verify_admin_token)):
        """获取登录安全统计信息（仅管理员）"""
        current_time = time.time()
    
        # 统计IP封禁信息
        blocked_ips = []
        for ip, data in ctx.login_ip_tracker.items():
            if data.get('blocked_until', 0) > current_time:
                blocked_ips.append({
                    'ip': ip,
                    'attempts': data.get('attempts', 0),
                    'blocked_until': data.get('blocked_until', 0),
                    'remaining_seconds': int(data['blocked_until'] - current_time)
                })
    
        # 统计用户锁定信息
        locked_users = []
        for username, data in ctx.login_user_tracker.items():
            if data.get('locked_until', 0) > current_time:
                locked_users.append({
                    'username': username,
                    'attempts': data.get('attempts', 0),
                    'locked_until': data.get('locked_until', 0),
                    'remaining_seconds': int(data['locked_until'] - current_time)
                })
    
        # 最近失败的IP
        recent_failed_ips = []
        for ip, data in ctx.login_ip_tracker.items():
            if data.get('attempts', 0) > 0:
                recent_failed_ips.append({
                    'ip': ip,
                    'attempts': data.get('attempts', 0),
                    'last_attempt': data.get('last_attempt', 0)
                })
        recent_failed_ips.sort(key=lambda x: x['last_attempt'], reverse=True)
    
        return {
            'success': True,
            'data': {
                'blocked_ips': blocked_ips,
                'blocked_ip_count': len(blocked_ips),
                'locked_users': locked_users,
                'locked_user_count': len(locked_users),
                'blacklisted_ips': list(ctx.ip_blacklist),
                'blacklist_count': len(ctx.ip_blacklist),
                'recent_failed_ips': recent_failed_ips[:20],  # 最近20个
                'config': ctx.BRUTE_FORCE_CONFIG
            }
        }

    @router.post('/admin/security/unblock-ip/{ip}')
    async def unblock_ip(ip: str, admin_user: Dict[str, Any] = Depends(ctx.verify_admin_token)):
        """解除IP封禁（仅管理员）"""
        unblocked = False
    
        # 从临时封禁中移除
        if ip in ctx.login_ip_tracker:
            ctx.login_ip_tracker[ip]['blocked_until'] = 0
            ctx.login_ip_tracker[ip]['attempts'] = 0
            unblocked = True
            logger.info(f"🔓 管理员 {admin_user['username']} 解除了IP {ip} 的临时封禁")
    
        # 从永久黑名单中移除
        if ip in ctx.ip_blacklist:
            ctx.ip_blacklist.discard(ip)
            unblocked = True
            logger.info(f"🔓 管理员 {admin_user['username']} 将IP {ip} 从永久黑名单中移除")
    
        if unblocked:
            return {'success': True, 'message': f'IP {ip} 已解除封禁'}
        else:
            return {'success': False, 'message': f'IP {ip} 未在封禁列表中'}

    @router.post('/admin/security/unlock-user/{username}')
    async def unlock_user(username: str, admin_user: Dict[str, Any] = Depends(ctx.verify_admin_token)):
        """解除用户锁定（仅管理员）"""
        if username in ctx.login_user_tracker:
            ctx.login_user_tracker[username]['locked_until'] = 0
            ctx.login_user_tracker[username]['attempts'] = 0
            logger.info(f"🔓 管理员 {admin_user['username']} 解除了用户 {username} 的锁定")
            return {'success': True, 'message': f'用户 {username} 已解除锁定'}
        else:
            return {'success': False, 'message': f'用户 {username} 未在锁定列表中'}

    @router.post('/admin/security/blacklist-ip/{ip}')
    async def add_ip_to_blacklist(ip: str, admin_user: Dict[str, Any] = Depends(ctx.verify_admin_token)):
        """将IP加入永久黑名单（仅管理员）"""
        ctx.ip_blacklist.add(ip)
        logger.warning(f"⛔ 管理员 {admin_user['username']} 将IP {ip} 加入永久黑名单")
        return {'success': True, 'message': f'IP {ip} 已加入永久黑名单'}

    @router.post('/admin/security/update-config')
    async def update_brute_force_config(
        config: Dict[str, Any],
        admin_user: Dict[str, Any] = Depends(ctx.verify_admin_token)
    ):
        """更新防暴力破解配置（仅管理员）"""
        valid_keys = set(ctx.BRUTE_FORCE_CONFIG.keys())
        updated = []
    
        for key, value in config.items():
            if key in valid_keys and isinstance(value, (int, float)):
                ctx.BRUTE_FORCE_CONFIG[key] = value
                updated.append(key)
    
        if updated:
            logger.info(f"⚙️ 管理员 {admin_user['username']} 更新了防暴力破解配置: {updated}")
            return {'success': True, 'message': f'已更新配置: {updated}', 'config': ctx.BRUTE_FORCE_CONFIG}
        else:
            return {'success': False, 'message': '没有有效的配置项被更新'}

    @router.post('/change-admin-password')
    async def change_admin_password(request: ChangePasswordRequest, admin_user: Dict[str, Any] = Depends(ctx.verify_admin_token)):
        from db_manager import db_manager

        try:
            # 验证当前密码（使用用户表验证）
            if not ctx.db_manager.verify_user_password('admin', request.current_password):
                return {"success": False, "message": "当前密码错误"}

            # 更新密码（使用用户表更新）
            success = ctx.db_manager.update_user_password('admin', request.new_password)

            if success:
                logger.info(f"【admin#{admin_user['user_id']}】管理员密码修改成功")
                return {"success": True, "message": "密码修改成功"}
            else:
                return {"success": False, "message": "密码修改失败"}

        except Exception as e:
            logger.error(f"修改管理员密码异常: {e}")
            return {"success": False, "message": "系统错误"}

    @router.post('/generate-captcha')
    async def generate_captcha(request: CaptchaRequest):
        from db_manager import db_manager

        try:
            # 生成图形验证码
            captcha_text, captcha_image = ctx.db_manager.generate_captcha()

            if not captcha_image:
                return CaptchaResponse(
                    success=False,
                    captcha_image="",
                    session_id=request.session_id,
                    message="图形验证码生成失败"
                )

            # 保存验证码到数据库
            if ctx.db_manager.save_captcha(request.session_id, captcha_text):
                return CaptchaResponse(
                    success=True,
                    captcha_image=captcha_image,
                    session_id=request.session_id,
                    message="图形验证码生成成功"
                )
            else:
                return CaptchaResponse(
                    success=False,
                    captcha_image="",
                    session_id=request.session_id,
                    message="图形验证码保存失败"
                )

        except Exception as e:
            logger.error(f"生成图形验证码失败: {e}")
            return CaptchaResponse(
                success=False,
                captcha_image="",
                session_id=request.session_id,
                message="图形验证码生成失败"
            )

    @router.post('/verify-captcha')
    async def verify_captcha(request: VerifyCaptchaRequest):
        from db_manager import db_manager

        try:
            if ctx.db_manager.verify_captcha(request.session_id, request.captcha_code):
                return VerifyCaptchaResponse(
                    success=True,
                    message="图形验证码验证成功"
                )
            else:
                return VerifyCaptchaResponse(
                    success=False,
                    message="图形验证码错误或已过期"
                )

        except Exception as e:
            logger.error(f"验证图形验证码失败: {e}")
            return VerifyCaptchaResponse(
                success=False,
                message="图形验证码验证失败"
            )

    @router.post('/send-verification-code')
    async def send_verification_code(request: SendCodeRequest):
        from db_manager import db_manager

        try:
            # 检查是否已验证图形验证码
            # 通过检查数据库中是否存在已验证的图形验证码记录
            with ctx.db_manager.lock:
                cursor = ctx.db_manager.conn.cursor()
                current_time = time.time()

                # 查找最近5分钟内该session_id的验证记录
                # 由于验证成功后验证码会被删除，我们需要另一种方式来跟踪验证状态
                # 这里我们检查该session_id是否在最近验证过（通过检查是否有已删除的记录）

                # 为了简化，我们要求前端在验证图形验证码成功后立即发送邮件验证码
                # 或者我们可以在验证成功后设置一个临时标记
                pass

            # 根据验证码类型进行不同的检查
            if request.type == 'register':
                # 注册验证码：检查邮箱是否已注册
                existing_user = ctx.db_manager.get_user_by_email(request.email)
                if existing_user:
                    return SendCodeResponse(
                        success=False,
                        message="该邮箱已被注册"
                    )
            elif request.type == 'login':
                # 登录验证码：检查邮箱是否存在
                existing_user = ctx.db_manager.get_user_by_email(request.email)
                if not existing_user:
                    return SendCodeResponse(
                        success=False,
                        message="该邮箱未注册"
                    )

            # 生成验证码
            code = ctx.db_manager.generate_verification_code()

            # 保存验证码到数据库
            if not ctx.db_manager.save_verification_code(request.email, code, request.type):
                return SendCodeResponse(
                    success=False,
                    message="验证码保存失败，请稍后重试"
                )

            # 发送验证码邮件
            if await ctx.db_manager.send_verification_email(request.email, code):
                return SendCodeResponse(
                    success=True,
                    message="验证码已发送到您的邮箱，请查收"
                )
            else:
                return SendCodeResponse(
                    success=False,
                    message="验证码发送失败，请检查邮箱地址或稍后重试"
                )

        except Exception as e:
            logger.error(f"发送验证码失败: {e}")
            return SendCodeResponse(
                success=False,
                message="发送验证码失败，请稍后重试"
            )

    @router.post('/register')
    async def register(request: RegisterRequest):
        from db_manager import db_manager

        # 检查注册是否开启
        registration_enabled = ctx.db_manager.get_system_setting('registration_enabled')
        if registration_enabled != 'true':
            logger.warning(f"【{request.username}】注册失败: 注册功能已关闭")
            return RegisterResponse(
                success=False,
                message="注册功能已关闭，请联系管理员"
            )

        try:
            logger.info(f"【{request.username}】尝试注册，邮箱: {request.email}")

            # 验证邮箱验证码
            if not ctx.db_manager.verify_email_code(request.email, request.verification_code):
                logger.warning(f"【{request.username}】注册失败: 验证码错误或已过期")
                return RegisterResponse(
                    success=False,
                    message="验证码错误或已过期"
                )

            # 检查用户名是否已存在
            existing_user = ctx.db_manager.get_user_by_username(request.username)
            if existing_user:
                logger.warning(f"【{request.username}】注册失败: 用户名已存在")
                return RegisterResponse(
                    success=False,
                    message="用户名已存在"
                )

            # 检查邮箱是否已注册
            existing_email = ctx.db_manager.get_user_by_email(request.email)
            if existing_email:
                logger.warning(f"【{request.username}】注册失败: 邮箱已被注册")
                return RegisterResponse(
                    success=False,
                    message="该邮箱已被注册"
                )

            # 创建用户
            if ctx.db_manager.create_user(request.username, request.email, request.password):
                logger.info(f"【{request.username}】注册成功")
                return RegisterResponse(
                    success=True,
                    message="注册成功，请登录"
                )
            else:
                logger.error(f"【{request.username}】注册失败: 数据库操作失败")
                return RegisterResponse(
                    success=False,
                    message="注册失败，请稍后重试"
                )

        except Exception as e:
            logger.error(f"【{request.username}】注册异常: {e}")
            return RegisterResponse(
                success=False,
                message="注册失败，请稍后重试"
            )

    return router
