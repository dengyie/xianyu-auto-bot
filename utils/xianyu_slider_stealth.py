#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
闲鱼滑块验证 - 增强反检测版本
基于最新的反检测技术，专门针对闲鱼、淘宝、阿里平台的滑块验证
"""

import time
import random
import json
import glob
import hashlib
import os
import math
import threading
import tempfile
import shutil
import subprocess
import re
import sys
import socket
import signal
from datetime import datetime
from urllib.parse import parse_qs, urlparse, urlencode, quote_plus
from playwright.sync_api import sync_playwright as playwright_sync_playwright, ElementHandle
try:
    from patchright.sync_api import sync_playwright as patchright_sync_playwright
except ImportError:
    patchright_sync_playwright = None
from playwright.async_api import async_playwright
import asyncio
from typing import Optional, Tuple, List, Dict, Any, Callable
from loguru import logger
from utils.slider_stealth_mixins import (
    PasswordLoginMixin, SliderHarvestMixin, SliderTrajectoryMixin,
    SliderVerificationMixin, StealthScriptMixin,
)
from collections import defaultdict

_PLAYWRIGHT_BROWSER_INSTALL_LOCK = threading.Lock()


# ============================================================================
# 1D Perlin 噪声实现（纯 Python，无外部依赖）
# 用于生成连续平滑的非周期性随机序列，替代 sin 叠加
# ============================================================================
def _is_runtime_detached_error(error: Exception) -> bool:
    """判断浏览器异常是否为页面运行时 detached/disconnected（纯函数）。"""
    error_text = str(error).lower()
    return 'detached' in error_text or 'disconnected' in error_text

def _perlin_fade(t):
    """Perlin 缓动函数: 6t^5 - 15t^4 + 10t^3"""
    return t * t * t * (t * (t * 6 - 15) + 10)


def _perlin_lerp(a, b, t):
    """线性插值"""
    return a + t * (b - a)


def _perlin_grad_1d(hash_val, x):
    """1D 梯度：根据 hash 值决定方向"""
    return x if (hash_val & 1) == 0 else -x


# 使用固定排列表（经典 Perlin 实现）
_PERLIN_PERM = list(range(256))
random.shuffle(_PERLIN_PERM)
_PERLIN_PERM = _PERLIN_PERM + _PERLIN_PERM  # 扩展到 512


def perlin_noise_1d(x, seed_offset=0):
    """1D Perlin 噪声，返回 [-1, 1] 范围的值

    Args:
        x: 采样坐标（连续浮点数）
        seed_offset: 种子偏移量，用于生成不同的噪声序列
    """
    xi = int(math.floor(x)) & 255
    xf = x - math.floor(x)
    u = _perlin_fade(xf)

    idx = (xi + int(seed_offset)) & 255
    a = _PERLIN_PERM[idx]
    b = _PERLIN_PERM[idx + 1]

    return _perlin_lerp(
        _perlin_grad_1d(a, xf),
        _perlin_grad_1d(b, xf - 1),
        u
    )


def perlin_octaves_1d(x, octaves=2, persistence=0.5, seed_offset=0):
    """多八度叠加的 1D Perlin 噪声（更丰富的细节）

    Args:
        x: 采样坐标
        octaves: 八度数（叠加层数）
        persistence: 每层振幅衰减比
        seed_offset: 种子偏移
    Returns:
        [-1, 1] 范围的噪声值
    """
    total = 0.0
    amplitude = 1.0
    frequency = 1.0
    max_amplitude = 0.0

    for _ in range(octaves):
        total += perlin_noise_1d(x * frequency, seed_offset) * amplitude
        max_amplitude += amplitude
        amplitude *= persistence
        frequency *= 2.0

    return total / max_amplitude if max_amplitude > 0 else 0.0


def parse_cookie_string(cookie_text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for part in str(cookie_text or "").replace("\ufeff", "").split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def generate_cookie_verification_device_id(user_id: str) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    buffer: List[str] = []
    for idx in range(36):
        if idx in (8, 13, 18, 23):
            buffer.append("-")
        elif idx == 14:
            buffer.append("4")
        else:
            rand_val = int(16 * random.random())
            if idx == 19:
                buffer.append(chars[(rand_val & 0x3) | 0x8])
            else:
                buffer.append(chars[rand_val])
    return "".join(buffer) + f"-{user_id}"


def build_cookie_verification_sign(ts: str, token: str, data: str) -> str:
    return hashlib.md5(f"{token}&{ts}&34839810&{data}".encode("utf-8")).hexdigest()


def probe_cookie_verification_from_cookie(
    cookie_text: str,
    proxy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    import requests

    cookies = parse_cookie_string(cookie_text)
    user_id = cookies.get("unb", "")
    token = cookies.get("_m_h5_tk", "").split("_")[0]
    if not user_id or not token:
        raise ValueError("Cookie 缺少 unb 或 _m_h5_tk，无法获取最新 verification_url")

    session = requests.Session()
    session.headers.update({
        "accept": "application/json",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "origin": "https://www.goofish.com",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://www.goofish.com/",
        "sec-ch-ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        ),
    })
    session.cookies.update(cookies)

    proxies = None
    proxy_config = dict(proxy or {})
    proxy_type = str(proxy_config.get("proxy_type") or "").strip().lower()
    proxy_host = str(proxy_config.get("proxy_host") or "").strip()
    proxy_port = proxy_config.get("proxy_port")
    if proxy_type not in {"", "none"} and proxy_host and proxy_port:
        auth = ""
        if proxy_config.get("proxy_user"):
            auth = str(proxy_config["proxy_user"])
            if proxy_config.get("proxy_pass"):
                auth += f":{proxy_config['proxy_pass']}"
            auth += "@"
        proxy_url = f"{proxy_type}://{auth}{proxy_host}:{proxy_port}"
        proxies = {"http": proxy_url, "https": proxy_url}

    device_id = generate_cookie_verification_device_id(user_id)
    ts = str(int(time.time()) * 1000)
    data_val = (
        '{"appKey":"444e9908a51d1cb236a27862abc769c9",'
        f'"deviceId":"{device_id}"'
        "}"
    )
    params = {
        "jsv": "2.7.2",
        "appKey": "34839810",
        "t": ts,
        "sign": build_cookie_verification_sign(ts, token, data_val),
        "v": "1.0",
        "type": "originaljson",
        "accountSite": "xianyu",
        "dataType": "json",
        "timeout": "20000",
        "api": "mtop.taobao.idlemessage.pc.login.token",
        "sessionOption": "AutoLoginOnly",
        "spm_cnt": "a21ybx.im.0.0",
    }
    response = session.post(
        "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/",
        params=params,
        data={"data": data_val},
        proxies=proxies,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    data_payload = payload.get("data") or {}
    verification_url = str(data_payload.get("url") or "").strip() or None
    ret_value = payload.get("ret") or []
    success_ret = any("SUCCESS::调用成功" in str(ret) for ret in ret_value)
    has_token_payload = any(
        str(data_payload.get(field) or "").strip()
        for field in ("accessToken", "refreshToken")
    )

    status = "unknown"
    if verification_url:
        status = "verification_required"
    elif success_ret and has_token_payload:
        status = "cookie_valid"

    session_cookies = {}
    try:
        session_cookies = dict(session.cookies.get_dict())
    except Exception:
        session_cookies = dict(cookies)

    return {
        "status": status,
        "verification_url": verification_url,
        "payload": payload,
        "session_cookies": session_cookies,
        "success_ret": success_ret,
        "has_token_payload": has_token_payload,
    }


def resolve_verification_url_from_cookie(cookie_text: str, proxy: Optional[Dict[str, Any]] = None) -> str:
    probe_result = probe_cookie_verification_from_cookie(cookie_text, proxy=proxy)
    verification_url = probe_result.get("verification_url")
    if verification_url:
        return verification_url
    if probe_result.get("status") == "cookie_valid":
        raise RuntimeError(f"Cookie 已直接有效，无需 verification_url: {probe_result.get('payload')}")
    raise RuntimeError(f"未拿到最新 verification_url: {probe_result.get('payload')}")


class PasswordLoginVerificationError(Exception):
    """账号密码登录流程中的可识别验证错误。"""


class VerificationFrameWrapper:
    def __init__(self, original_frame, verification_type='unknown', verify_url=None, screenshot_path=None):
        self._original_frame = original_frame
        self.verification_type = verification_type
        self.verify_url = verify_url
        self.screenshot_path = screenshot_path

    def __getattr__(self, name):
        return getattr(self._original_frame, name)

# 导入配置
try:
    from config import SLIDER_VERIFICATION
    SLIDER_MAX_CONCURRENT = SLIDER_VERIFICATION.get('max_concurrent', 3)
    SLIDER_WAIT_TIMEOUT = SLIDER_VERIFICATION.get('wait_timeout', 60)
except ImportError:
    # 如果无法导入配置，使用默认值
    SLIDER_MAX_CONCURRENT = 3
    SLIDER_WAIT_TIMEOUT = 60

# ============================================================================
# 🏆 黄金参数配置（基于成功案例分析）
# 分析来源：trajectory_history/*.json 成功记录
# 分析时间：2026-01-28 优化版本
# ============================================================================
GOLDEN_PARAMS = {
    # 轨迹生成参数 - 🔧 2026-01-28 扩大随机范围，降低被检测概率
    "trajectory": {
        "overshoot_ratio": (1.02, 1.15),      # 🔧 改为真实超调比例2-15%（原1.93-2.05太极端）
        "steps": (18, 35),                     # 🔧 增加步数范围（原6-8太少）
        "base_delay": (0.004, 0.015),         # 🔧 增加延迟范围（原0.0003-0.0006太快）
        "acceleration_curve": (1.3, 2.2),     # 🔧 扩大曲线范围（原1.4-1.65）
        "y_jitter_max": (1.0, 3.5),           # 🔧 扩大Y轴抖动范围（原1.5-2.5）
    },
    # 滑动行为参数（🔧 2026-01-28 增加随机性）
    "slide_behavior": {
        "approach_offset_x": (-30, -15),       # 🔧 扩大范围（原-25到-20）
        "approach_offset_y": (8, 22),          # 🔧 扩大范围（原12到18）
        "approach_steps": (6, 12),             # 🔧 扩大范围（原8-10）
        "approach_pause": (0.03, 0.18),        # 🔧 扩大范围
        "precision_steps": (6, 12),            # 🔧 扩大范围（原8-10）
        "precision_pause": (0.05, 0.15),       # 🔧 扩大范围
        "skip_hover_rate": 0.25,               # 🔧 增加跳过率，增加随机性
        "pre_down_pause": (0.08, 0.20),        # 🔧 扩大范围
        "post_down_pause": (0.08, 0.20),       # 🔧 扩大范围
        "pre_up_pause": (0.02, 0.08),          # 🔧 扩大范围
        "post_up_pause": (0.01, 0.06),         # 🔧 扩大范围
    },
    # 时间控制
    "timing": {
        "total_elapsed_time": (0.8, 2.0),      # 🔧 扩大耗时范围（原0.9-1.55）
        "page_wait": (0.05, 0.30),             # 🔧 扩大等待范围
    },
    # 重试策略 - 🔧 2026-01-28 增加冷却时间
    "retry": {
        "perturbation_factor_increment": 0.12, # 🔧 增大扰动递增（原0.08）
        "base_retry_delay": 1.5,               # 🔧 增加基础延迟（原0.4）- 给服务器冷却时间
        "retry_delay_increment": 1.0,          # 🔧 增加延迟递增（原0.2）
    }
}

# ============================================================================
# 🎰 机器学习策略配置（探索-利用平衡）
# 🔧 2026-01-28 更新：扩大参数范围，增加随机性，降低被检测概率
# ============================================================================
ML_STRATEGY_CONFIG = {
    # 🔧 2026-01-28：降低探索率，更多使用已验证有效的参数
    "exploration_rate": 0.06,  # 进一步降低探索率，优先复用已验证有效的参数

    # 连续失败后切换慢速兜底的阈值基线
    "force_explore_after_failures": 2,  # 第3次尝试会进入慢速兜底

    # 多策略模式配置 - 🔧 2026-01-28 扩大所有参数范围
    "strategies": {
        # 保守策略：较小超调，模拟谨慎用户
        "conservative": {
            "overshoot_ratio": (1.01, 1.06),   # 1-6%超调
            "steps": (28, 40),                  # 🔧 增加步数，更自然
            "base_delay": (0.010, 0.020),      # 🔧 增加延迟（10-20ms）
            "acceleration_curve": (1.8, 2.4),  # 更平滑的ease-out
            "y_jitter_max": (0.8, 2.0),        # 较小Y抖动
            "weight": 0.08,                    # 🔧 从0.18降到0.08，历史成功率仅12%
        },
        # 标准策略：中等超调，模拟普通用户
        "standard": {
            "overshoot_ratio": (1.03, 1.10),   # 3-10%超调
            "steps": (22, 35),                  # 🔧 增加步数范围
            "base_delay": (0.006, 0.015),      # 6-15ms延迟
            "acceleration_curve": (1.5, 2.1),
            "y_jitter_max": (1.2, 2.8),
            "weight": 0.57,                    # 🔧 从0.47提高到0.57，吸收conservative释放的权重
        },
        # 激进策略：较大超调，模拟快速用户
        "aggressive": {
            "overshoot_ratio": (1.06, 1.15),   # 6-15%超调
            "steps": (18, 30),
            "base_delay": (0.004, 0.012),      # 4-12ms延迟
            "acceleration_curve": (1.3, 1.9),  # 更陡的加速曲线
            "y_jitter_max": (1.5, 3.2),
            "weight": 0.35,
        },
    },

    # 参数抖动范围 - 🔧 增加抖动幅度
    "param_jitter": {
        "overshoot_ratio_jitter": 0.05,  # 🔧 从±3%增加到±5%
        "delay_jitter": 0.20,             # 🔧 从±12%增加到±20%
        "curve_jitter": 0.12,             # 🔧 从±8%增加到±12%
    },

    # 学习参数边界 - 🔧 扩大边界
    "learning_bounds": {
        "max_overshoot_ratio": 1.18,      # 🔧 从1.15增加到1.18
        "min_overshoot_ratio": 1.01,
        "max_y_jitter": 3.5,              # 🔧 从3.0增加到3.5
        "min_y_jitter": 0.8,              # 🔧 从1.0降到0.8
        "max_acceleration_curve": 2.6,    # 🔧 从2.5增加到2.6
        "min_acceleration_curve": 1.2,    # 🔧 从1.3降到1.2
    },

    # 🔄 自动权重调整配置
    "auto_weight_adjustment": {
        "enabled": True,
        "min_samples": 3,                  # 🔧 从5降到3，更快开始调整
        "smoothing_factor": 0.4,           # 🔧 从0.3增加到0.4，更快响应
        "min_weight": 0.05,                # 🔧 从0.15降到0.05，允许低效策略被进一步压低
        "max_weight": 0.55,                # 🔧 从0.60降到0.55
    },

    # 🧹 自动数据清理配置
    "auto_data_cleanup": {
        "enabled": True,
        "min_success_rate": 0.20,          # 🔧 从0.15增加到0.20
        "check_window": 15,                # 🔧 从20降到15，更快响应
        "cleanup_threshold": 0.12,         # 🔧 从0.10增加到0.12
        "max_history_age_days": 5,         # 🔧 从7天降到5天，更新更快
    }
}


# ============================================================================
# 🤖 自适应策略管理器（自动调整权重+自动清理数据）
# ============================================================================
class AdaptiveStrategyManager:
    """自适应策略管理器 - 基于多臂老虎机算法动态调整策略权重"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.stats_lock = threading.Lock()
            # 策略统计：{strategy_name: {"success": count, "fail": count, "total": count}}
            self.strategy_stats = {
                "conservative": {"success": 0, "fail": 0, "total": 0},
                "standard": {"success": 0, "fail": 0, "total": 0},
                "aggressive": {"success": 0, "fail": 0, "total": 0},
                "learned_with_jitter": {"success": 0, "fail": 0, "total": 0},
            }
            # 动态权重（与 ML_STRATEGY_CONFIG 初始权重一致）
            self.dynamic_weights = {
                "conservative": 0.08,
                "standard": 0.57,
                "aggressive": 0.35,
            }
            # 统计文件路径
            self.stats_file = "trajectory_history/adaptive_strategy_stats.json"
            # 加载历史统计
            self._load_stats()
            self._initialized = True
            logger.info("🤖 自适应策略管理器初始化完成")
    
    # 已废弃的策略名称，加载时自动清理
    _DEPRECATED_STRATEGIES = {"slow_fallback"}

    def _load_stats(self):
        """加载历史统计数据"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.strategy_stats.update(data.get("strategy_stats", {}))
                    self.dynamic_weights.update(data.get("dynamic_weights", {}))
                # 清理已废弃策略的残留数据
                cleaned = False
                for dep in self._DEPRECATED_STRATEGIES:
                    if dep in self.strategy_stats:
                        del self.strategy_stats[dep]
                        cleaned = True
                    if dep in self.dynamic_weights:
                        del self.dynamic_weights[dep]
                        cleaned = True
                if cleaned:
                    logger.info(f"🤖 已清理废弃策略统计: {self._DEPRECATED_STRATEGIES}")
                    self._save_stats()
                logger.info(f"🤖 加载历史策略统计: {self.stats_file}")
        except Exception as e:
            logger.warning(f"🤖 加载策略统计失败: {e}")
    
    def _save_stats(self):
        """保存统计数据"""
        try:
            os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "strategy_stats": self.strategy_stats,
                    "dynamic_weights": self.dynamic_weights,
                    "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"🤖 保存策略统计失败: {e}")
    
    def record_result(self, strategy_name: str, success: bool):
        """记录策略使用结果
        
        Args:
            strategy_name: 策略名称 (conservative/standard/aggressive/learned_with_jitter)
            success: 是否成功
        """
        with self.stats_lock:
            if strategy_name not in self.strategy_stats:
                self.strategy_stats[strategy_name] = {"success": 0, "fail": 0, "total": 0}
            
            stats = self.strategy_stats[strategy_name]
            stats["total"] += 1
            if success:
                stats["success"] += 1
            else:
                stats["fail"] += 1
            
            # 计算成功率
            success_rate = stats["success"] / stats["total"] if stats["total"] > 0 else 0
            
            logger.info(f"🤖 策略[{strategy_name}]记录: {'✅成功' if success else '❌失败'} "
                       f"(成功率: {success_rate*100:.1f}%, 总计: {stats['total']}次)")
            
            # 自动调整权重
            self._auto_adjust_weights()
            
            # 保存统计
            self._save_stats()
    
    def _auto_adjust_weights(self):
        """自动调整策略权重（基于成功率）"""
        config = ML_STRATEGY_CONFIG.get("auto_weight_adjustment", {})
        if not config.get("enabled", True):
            return
        
        min_samples = config.get("min_samples", 5)
        smoothing = config.get("smoothing_factor", 0.3)
        min_weight = config.get("min_weight", 0.10)
        max_weight = config.get("max_weight", 0.60)
        
        # 只调整三个主要策略的权重
        main_strategies = ["conservative", "standard", "aggressive"]
        
        # 检查是否有足够的样本
        total_samples = sum(
            self.strategy_stats.get(s, {}).get("total", 0) 
            for s in main_strategies
        )
        
        if total_samples < min_samples * len(main_strategies):
            return  # 样本不足，不调整
        
        # 计算每个策略的成功率
        success_rates = {}
        for strategy in main_strategies:
            stats = self.strategy_stats.get(strategy, {})
            total = stats.get("total", 0)
            success = stats.get("success", 0)
            if total >= min_samples:
                success_rates[strategy] = success / total
            else:
                success_rates[strategy] = 0.33  # 默认成功率
        
        # 计算新权重（基于成功率的softmax）
        total_rate = sum(success_rates.values())
        if total_rate > 0:
            new_weights = {}
            for strategy in main_strategies:
                # 使用指数加权，成功率高的策略权重更高
                raw_weight = success_rates[strategy] / total_rate
                # 应用边界限制
                new_weights[strategy] = max(min_weight, min(max_weight, raw_weight))
            
            # 归一化确保权重和为1
            weight_sum = sum(new_weights.values())
            for strategy in main_strategies:
                new_weights[strategy] /= weight_sum
            
            # 平滑更新（避免剧烈变化）
            for strategy in main_strategies:
                old_weight = self.dynamic_weights.get(strategy, 0.33)
                self.dynamic_weights[strategy] = (
                    old_weight * (1 - smoothing) + new_weights[strategy] * smoothing
                )
            
            logger.info(f"🤖 自动调整权重: "
                       f"保守={self.dynamic_weights['conservative']*100:.1f}%, "
                       f"标准={self.dynamic_weights['standard']*100:.1f}%, "
                       f"激进={self.dynamic_weights['aggressive']*100:.1f}%")
    
    def get_dynamic_weights(self, attempt: int = 1) -> dict:
        """获取动态权重（结合尝试次数调整）
        
        Args:
            attempt: 当前尝试次数
            
        Returns:
            dict: {strategy_name: weight}
        """
        with self.stats_lock:
            # 基础权重
            weights = self.dynamic_weights.copy()

            # 固定给低成功率策略一个更低上限，避免无头链路过度分配到保守分支
            weights["conservative"] = min(0.22, max(0.12, weights.get("conservative", 0.18)))
            weights["standard"] = max(0.40, weights.get("standard", 0.47))
            weights["aggressive"] = max(0.28, weights.get("aggressive", 0.35))

            total = sum(weights.values())
            if total > 0:
                for strategy in list(weights.keys()):
                    weights[strategy] = weights[strategy] / total
            
            # 根据尝试次数微调
            if attempt >= 3:
                # 第3次尝试优先走更果断的轨迹，不再依赖低收益慢速分支
                weights["aggressive"] = min(0.55, weights.get("aggressive", 0.35) + 0.12)
                # 相应减少其他策略
                total_other = weights.get("conservative", 0.18) + weights.get("standard", 0.47)
                if total_other > 0:
                    factor = (1 - weights["aggressive"]) / total_other
                    weights["conservative"] = weights.get("conservative", 0.18) * factor
                    weights["standard"] = weights.get("standard", 0.47) * factor
            
            return weights
    
    def check_and_cleanup_history(self, user_id: str, history_file: str) -> bool:
        """检查并自动清理历史数据
        
        Args:
            user_id: 用户ID
            history_file: 历史文件路径
            
        Returns:
            bool: 是否执行了清理
        """
        config = ML_STRATEGY_CONFIG.get("auto_data_cleanup", {})
        if not config.get("enabled", True):
            return False
        
        min_success_rate = config.get("min_success_rate", 0.15)
        check_window = config.get("check_window", 20)
        cleanup_threshold = config.get("cleanup_threshold", 0.10)
        max_age_days = config.get("max_history_age_days", 7)
        
        try:
            if not os.path.exists(history_file):
                return False
            
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
            
            if len(history) < check_window:
                return False  # 数据不足，不检查
            
            # 检查1：最近N条记录的成功率
            recent_records = history[-check_window:]
            # 注意：历史记录都是成功的，所以这里检查的是整体趋势
            # 我们通过检查记录的时间分布来判断
            
            # 检查2：清理过期数据
            current_time = time.time()
            max_age_seconds = max_age_days * 24 * 3600
            
            # 过滤掉过期的记录
            valid_records = [
                r for r in history 
                if current_time - r.get("timestamp", 0) < max_age_seconds
            ]
            
            if len(valid_records) < len(history):
                # 有过期记录，执行清理
                removed_count = len(history) - len(valid_records)
                logger.warning(f"🧹 【{user_id}】自动清理{removed_count}条过期历史记录"
                              f"（超过{max_age_days}天）")
                
                with open(history_file, 'w', encoding='utf-8') as f:
                    json.dump(valid_records, f, indent=2, ensure_ascii=False)
                
                return True
            
            # 检查3：如果历史记录中的参数明显偏离最优范围，清理部分记录
            bounds = ML_STRATEGY_CONFIG.get("learning_bounds", {})
            max_overshoot = bounds.get("max_overshoot_ratio", 2.12)
            
            # 检查最近记录的超调比例
            recent_overshoots = [
                r.get("overshoot_ratio", 0) 
                for r in recent_records 
                if r.get("overshoot_ratio", 0) > 0
            ]
            
            if recent_overshoots:
                avg_overshoot = sum(recent_overshoots) / len(recent_overshoots)
                if avg_overshoot > max_overshoot:
                    # 超调比例偏高，清理一半的历史记录
                    logger.warning(f"🧹 【{user_id}】检测到历史数据超调比例偏高"
                                  f"（平均{avg_overshoot:.2f}），清理一半历史记录")
                    
                    # 保留较新的一半记录
                    half_count = len(history) // 2
                    new_history = history[half_count:]
                    
                    with open(history_file, 'w', encoding='utf-8') as f:
                        json.dump(new_history, f, indent=2, ensure_ascii=False)
                    
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"🧹 检查历史数据时出错: {e}")
            return False
    
    def get_stats_summary(self) -> str:
        """获取统计摘要"""
        with self.stats_lock:
            lines = ["=" * 60]
            lines.append("🤖 自适应策略统计")
            lines.append("=" * 60)
            
            for strategy, stats in self.strategy_stats.items():
                total = stats.get("total", 0)
                success = stats.get("success", 0)
                rate = success / total * 100 if total > 0 else 0
                weight = self.dynamic_weights.get(strategy, 0) * 100
                lines.append(f"{strategy:25} | 成功率: {rate:5.1f}% | "
                           f"样本: {total:4} | 权重: {weight:5.1f}%")
            
            lines.append("=" * 60)
            return "\n".join(lines)


# 全局自适应策略管理器实例
adaptive_strategy_manager = AdaptiveStrategyManager()

# 使用loguru日志库，与主程序保持一致

# 全局并发控制
class SliderConcurrencyManager:
    """滑块验证并发管理器"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.max_concurrent = SLIDER_MAX_CONCURRENT  # 从配置文件读取最大并发数
            self.wait_timeout = SLIDER_WAIT_TIMEOUT  # 从配置文件读取等待超时时间
            self.active_instances = {}  # 活跃实例
            self.waiting_queue = []  # 等待队列
            self.instance_lock = threading.Lock()
            self._initialized = True
            logger.info(f"滑块验证并发管理器初始化: 最大并发数={self.max_concurrent}, 等待超时={self.wait_timeout}秒")
    
    def can_start_instance(self, user_id: str) -> bool:
        """检查是否可以启动新实例"""
        with self.instance_lock:
            return self._can_start_locked(user_id)

    def _find_same_account_active_locked(self, user_id: str):
        """查找同账号的活跃实例，避免同账号并发滑块互相踩踏"""
        pure_user_id = self._extract_pure_user_id(user_id)
        for active_user_id in self.active_instances:
            if self._extract_pure_user_id(active_user_id) == pure_user_id:
                return active_user_id
        return None

    def _can_start_locked(self, user_id: str) -> bool:
        """在持锁状态下检查是否允许启动实例"""
        same_account_active = self._find_same_account_active_locked(user_id)
        return len(self.active_instances) < self.max_concurrent and same_account_active is None
    
    def wait_for_slot(self, user_id: str, timeout: int = None) -> bool:
        """等待可用槽位"""
        if timeout is None:
            timeout = self.wait_timeout
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            with self.instance_lock:
                same_account_active = self._find_same_account_active_locked(user_id)
                if len(self.active_instances) < self.max_concurrent and same_account_active is None:
                    return True
            
            # 检查是否在等待队列中
            with self.instance_lock:
                if user_id not in self.waiting_queue:
                    self.waiting_queue.append(user_id)
                    # 提取纯用户ID用于日志显示
                    pure_user_id = self._extract_pure_user_id(user_id)
                    same_account_active = self._find_same_account_active_locked(user_id)
                    if same_account_active:
                        logger.warning(
                            f"【{pure_user_id}】同账号滑块任务正在执行({same_account_active})，进入等待队列，当前队列长度: {len(self.waiting_queue)}"
                        )
                    else:
                        logger.info(f"【{pure_user_id}】进入等待队列，当前队列长度: {len(self.waiting_queue)}")
            
            # 等待1秒后重试
            time.sleep(1)
        
        # 超时后从队列中移除
        with self.instance_lock:
            if user_id in self.waiting_queue:
                self.waiting_queue.remove(user_id)
                # 提取纯用户ID用于日志显示
                pure_user_id = self._extract_pure_user_id(user_id)
                logger.warning(f"【{pure_user_id}】等待超时，从队列中移除")
        
        return False
    
    def register_instance(self, user_id: str, instance):
        """注册实例"""
        with self.instance_lock:
            if not self._can_start_locked(user_id):
                return False
            self.active_instances[user_id] = {
                'instance': instance,
                'start_time': time.time()
            }
            # 从等待队列中移除
            if user_id in self.waiting_queue:
                self.waiting_queue.remove(user_id)
            return True
    
    def unregister_instance(self, user_id: str, instance=None):
        """注销实例；如果提供 instance，则仅在实例归属匹配时释放。"""
        with self.instance_lock:
            active_entry = self.active_instances.get(user_id)
            if not active_entry:
                return False

            if instance is not None and active_entry.get('instance') is not instance:
                pure_user_id = self._extract_pure_user_id(user_id)
                logger.debug(f"【{pure_user_id}】跳过注销实例：当前活跃实例已切换，避免误释放新槽位")
                return False

            del self.active_instances[user_id]
            # 提取纯用户ID用于日志显示
            pure_user_id = self._extract_pure_user_id(user_id)
            logger.info(f"【{pure_user_id}】实例已注销，当前活跃: {len(self.active_instances)}")
            return True
    
    def _extract_pure_user_id(self, user_id: str) -> str:
        """提取纯用户ID（移除时间戳部分）"""
        if '_' in user_id:
            # 检查最后一部分是否为数字（时间戳）
            parts = user_id.split('_')
            if len(parts) >= 2 and parts[-1].isdigit() and len(parts[-1]) >= 10:
                # 最后一部分是时间戳，移除它
                return '_'.join(parts[:-1])
            else:
                # 不是时间戳格式，使用原始ID
                return user_id
        else:
            # 没有下划线，直接使用
            return user_id
    
    def get_stats(self):
        """获取统计信息"""
        with self.instance_lock:
            return {
                'active_count': len(self.active_instances),
                'max_concurrent': self.max_concurrent,
                'available_slots': self.max_concurrent - len(self.active_instances),
                'queue_length': len(self.waiting_queue),
                'waiting_users': self.waiting_queue.copy()
            }

# 全局并发管理器实例
concurrency_manager = SliderConcurrencyManager()

# 策略统计管理器
class RetryStrategyStats:
    """重试策略成功率统计管理器"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.stats_lock = threading.Lock()
            self.strategy_stats = {
                'attempt_1_default': {'total': 0, 'success': 0, 'fail': 0},
                'attempt_2_cautious': {'total': 0, 'success': 0, 'fail': 0},
                'attempt_3_fast': {'total': 0, 'success': 0, 'fail': 0},
                'attempt_3_slow': {'total': 0, 'success': 0, 'fail': 0},
            }
            self.stats_file = 'trajectory_history/strategy_stats.json'
            self._load_stats()
            self._initialized = True
            logger.info("策略统计管理器初始化完成")
    
    def _load_stats(self):
        """从文件加载统计数据"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    loaded_stats = json.load(f)
                    self.strategy_stats.update(loaded_stats)
                logger.info(f"已加载历史策略统计数据: {self.stats_file}")
        except Exception as e:
            logger.warning(f"加载策略统计数据失败: {e}")
    
    def _save_stats(self):
        """保存统计数据到文件"""
        try:
            os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(self.strategy_stats, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存策略统计数据失败: {e}")
    
    def record_attempt(self, attempt: int, strategy_type: str, success: bool):
        """记录一次尝试结果
        
        Args:
            attempt: 尝试次数 (1, 2, 3)
            strategy_type: 策略类型 ('default', 'cautious', 'fast', 'slow')
            success: 是否成功
        """
        with self.stats_lock:
            key = f'attempt_{attempt}_{strategy_type}'
            if key not in self.strategy_stats:
                self.strategy_stats[key] = {'total': 0, 'success': 0, 'fail': 0}
            
            self.strategy_stats[key]['total'] += 1
            if success:
                self.strategy_stats[key]['success'] += 1
            else:
                self.strategy_stats[key]['fail'] += 1
            
            # 每次记录后保存
            self._save_stats()
    
    def get_stats_summary(self):
        """获取统计摘要"""
        with self.stats_lock:
            summary = {}
            for key, stats in self.strategy_stats.items():
                if stats['total'] > 0:
                    success_rate = (stats['success'] / stats['total']) * 100
                    summary[key] = {
                        'total': stats['total'],
                        'success': stats['success'],
                        'fail': stats['fail'],
                        'success_rate': f"{success_rate:.2f}%"
                    }
            return summary
    
    def log_summary(self):
        """输出统计摘要到日志"""
        summary = self.get_stats_summary()
        if summary:
            logger.info("=" * 60)
            logger.info("📊 重试策略成功率统计")
            logger.info("=" * 60)
            for key, stats in summary.items():
                logger.info(f"{key:25s} | 总计:{stats['total']:4d} | 成功:{stats['success']:4d} | 失败:{stats['fail']:4d} | 成功率:{stats['success_rate']}")
            logger.info("=" * 60)

# 全局策略统计实例
strategy_stats = RetryStrategyStats()

class XianyuSliderStealth(SliderVerificationMixin, SliderHarvestMixin, SliderTrajectoryMixin, StealthScriptMixin, PasswordLoginMixin):
    _verification_notification_lock = threading.Lock()
    _verification_notification_cache: Dict[Tuple[str, str, str], float] = {}
    _verification_notification_dedup_seconds = 180
    
    def __init__(self, user_id: str = "default", enable_learning: bool = True, headless: bool = True,
                 initial_cookies: Optional[str] = None, proxy: Optional[Dict[str, Any]] = None,
                 browser_channel: Optional[str] = None, executable_path: Optional[str] = None,
                 slider_max_retries: int = 3, use_account_persistent_profile: bool = False,
                 account_persistent_profile_dir: Optional[str] = None):
        self.user_id = user_id
        self.enable_learning = enable_learning
        self.headless = headless  # 是否使用无头模式
        self.initial_cookies = str(initial_cookies or "").replace("\ufeff", "").strip()
        self.proxy_config = dict(proxy or {})
        self.browser_channel = browser_channel or os.environ.get("XY_SLIDER_BROWSER_CHANNEL", "").strip() or None
        self.executable_path = executable_path or os.environ.get("XY_SLIDER_BROWSER_PATH", "").strip() or None
        self.slider_max_retries = max(1, min(int(slider_max_retries or 3), 4))
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.playwright_browser_name = os.environ.get("XY_SLIDER_PLAYWRIGHT_BROWSER", "chromium").strip() or "chromium"
        existing_playwright_cache_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
        is_docker_env = os.environ.get("DOCKER_ENV", "").strip().lower() in {"1", "true", "yes", "on"}
        self.is_docker_env = is_docker_env
        if existing_playwright_cache_dir and existing_playwright_cache_dir != "0":
            self.playwright_browser_cache_dir = existing_playwright_cache_dir
        elif is_docker_env and os.path.isdir("/ms-playwright"):
            self.playwright_browser_cache_dir = "/ms-playwright"
        else:
            self.playwright_browser_cache_dir = os.path.join(self.project_root, ".playwright-browsers")

        default_download_proxy = "" if is_docker_env else "http://127.0.0.1:1081"
        self.playwright_download_proxy = (
            os.environ.get("XY_SLIDER_DOWNLOAD_PROXY", "").strip() or
            os.environ.get("XY_DOWNLOAD_PROXY", "").strip() or
            default_download_proxy
        )
        verification_wait_timeout_text = os.environ.get("XY_VERIFICATION_WAIT_TIMEOUT", "").strip()
        try:
            self.verification_wait_timeout = max(5, int(verification_wait_timeout_text)) if verification_wait_timeout_text else 450
        except ValueError:
            self.verification_wait_timeout = 450
        self.keep_verification_screenshots = (
            os.environ.get("XY_KEEP_VERIFICATION_SCREENSHOT", "").strip().lower() in {"1", "true", "yes", "on"}
        )
        self.disable_headless_warmup = (
            os.environ.get("XY_SLIDER_HEADLESS_WARMUP", "").strip().lower() not in {"1", "true", "yes", "on"}
        )
        backend_env = os.environ.get("XY_SLIDER_AUTOMATION_BACKEND", "").strip().lower()
        if backend_env in {"patchright", "playwright"}:
            self.automation_backend = backend_env
        else:
            self.automation_backend = "playwright"
        self.stealth_mode_override = os.environ.get("XY_SLIDER_STEALTH_MODE", "").strip().lower()
        self.active_stealth_mode = "auto"
        self.browser = None
        self.page = None
        self.context = None
        self._browser_pid = None
        self.local_browser_info = {}
        try:
            self.browser_cookie_warmup_probe_timeout_ms = max(
                1000,
                int(os.environ.get("XY_BROWSER_COOKIE_WARMUP_TIMEOUT_MS", "5000") or 5000),
            )
        except Exception:
            self.browser_cookie_warmup_probe_timeout_ms = 5000
        if not self.browser_channel and not self.executable_path:
            detected_browser = self._detect_local_browser_info()
            if detected_browser:
                self.local_browser_info = dict(detected_browser)
                detected_path = str(detected_browser.get("path") or "").strip()
                detected_channel = str(detected_browser.get("channel") or "").strip()
                if os.name == 'nt' and detected_channel:
                    self.browser_channel = detected_channel
                elif detected_path:
                    self.executable_path = detected_path
                elif detected_channel:
                    self.browser_channel = detected_channel
        self.playwright = None
        self._playwright_thread_id: Optional[int] = None
        # 内层 _detect_qr_code_verification 滑块自救成功后的兜底回流标记，由 run() 入口重置
        self._post_recovery_success: bool = False
        self._post_recovery_cookies = None
        self._concurrency_slot_registered = False
        
        # 提取纯用户ID（移除时间戳部分）
        self.pure_user_id = concurrency_manager._extract_pure_user_id(user_id)
        
        # 检查日期限制
        if not self._check_date_validity():
            raise Exception(f"【{self.pure_user_id}】日期验证失败，功能已过期")
        
        # 为每个实例创建独立的临时目录
        self.temp_dir = tempfile.mkdtemp(prefix=f"slider_{user_id}_")
        logger.debug(f"【{self.pure_user_id}】创建临时目录: {self.temp_dir}")
        
        # 等待可用槽位（排队机制）
        logger.info(f"【{self.pure_user_id}】检查并发限制...")
        if not concurrency_manager.wait_for_slot(self.user_id):
            stats = concurrency_manager.get_stats()
            logger.error(f"【{self.pure_user_id}】等待槽位超时，当前活跃: {stats['active_count']}/{stats['max_concurrent']}")
            raise Exception(f"滑块验证等待槽位超时，请稍后重试")
        
        # 注册实例
        if not concurrency_manager.register_instance(self.user_id, self):
            raise Exception(f"【{self.pure_user_id}】同账号已有滑块任务正在执行，请稍后重试")
        self._concurrency_slot_registered = True
        stats = concurrency_manager.get_stats()
        logger.info(f"【{self.pure_user_id}】实例已注册，当前并发: {stats['active_count']}/{stats['max_concurrent']}")
        
        # 轨迹学习相关属性
        
        self.success_history_file = f"trajectory_history/{self.pure_user_id}_success.json"
        self.failure_history_file = f"trajectory_history/{self.pure_user_id}_failure.json"
        self.browser_profile_file = f"trajectory_history/{self.pure_user_id}_browser_profile.json"
        self.last_verification_feedback = {}
        self.last_login_error = ""
        self.last_browser_cookie_warmup_verification_hint = None
        self.last_browser_cookie_warmup_session_unready = False
        self._slider_refresh_mode = False
        self.risk_session_id = None
        self.risk_trigger_scene = None
        self._password_slider_runtime_hardened = False
        self.browser_features = {}
        self.browser_identity = {}
        self.profile_id = "unassigned"
        self.use_account_persistent_profile = bool(use_account_persistent_profile)
        self.account_persistent_profile_dir = str(account_persistent_profile_dir or "").strip() or None
        self.trajectory_params = {
            "total_steps_range": [5, 8],  # 极速：5-8步（超快滑动）
            "base_delay_range": [0.0002, 0.0005],  # 极速：0.2-0.5ms延迟
            "jitter_x_range": [0, 1],  # 极小抖动
            "jitter_y_range": [0, 1],  # 极小抖动
            "slow_factor_range": [10, 15],  # 极快加速因子
            "acceleration_phase": 1.0,  # 全程加速
            "fast_phase": 1.0,  # 无慢速
            "slow_start_ratio_base": 2.0,  # 确保超调100%
            "completion_usage_rate": 0.05,  # 极少补全使用率
            "avg_completion_steps": 1.0,  # 极少补全步数
            "trajectory_length_stats": [],
            "learning_enabled": False
        }
        
        # 保存最后一次使用的轨迹参数（用于分析优化）
        self.last_trajectory_params = {}

        self.local_browser_info = {}
        if self.executable_path:
            version_text = self._read_local_browser_version(self.executable_path)
            self.local_browser_info = {
                "path": self.executable_path,
                "version": version_text,
                "major_version": (version_text.split(".", 1)[0] if version_text else ""),
                "family": self._get_browser_family(),
            }


    def _build_risk_event_meta(self, verification_url: str = None, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        trigger_scene = getattr(self, 'risk_trigger_scene', None)
        if trigger_scene:
            payload['trigger_scene'] = trigger_scene

        text = str(verification_url or '').strip()
        if text:
            try:
                parsed = urlparse(text)
                if parsed.scheme or parsed.netloc:
                    if parsed.netloc:
                        payload['verification_host'] = parsed.netloc
                    if parsed.path:
                        payload['verification_path'] = parsed.path
                    query = parse_qs(parsed.query or '')
                    x5secdata = query.get('x5secdata', [None])[0]
                    if x5secdata:
                        payload['verification_token_hash'] = hashlib.sha256(x5secdata.encode('utf-8')).hexdigest()[:16]
                    action = query.get('action', [None])[0]
                    if action:
                        payload['verification_action'] = action
                else:
                    payload['verification_source'] = text[:120]
            except Exception:
                payload['verification_source'] = text[:120]

        if isinstance(extra, dict):
            payload.update({key: value for key, value in extra.items() if value is not None})
        return payload or None

    def _resolve_slider_risk_context(self) -> Tuple[str, str]:
        trigger_scene = getattr(self, 'risk_trigger_scene', None)
        if not trigger_scene:
            trigger_scene = 'manual_password_refresh' if getattr(self, '_slider_refresh_mode', False) else 'password_login'

        if trigger_scene == 'manual_password_refresh':
            flow_label = '手动刷新Cookie'
        elif trigger_scene == 'password_login':
            flow_label = '账号密码登录'
        elif trigger_scene == 'auto_cookie_refresh':
            flow_label = '自动Cookie刷新'
        else:
            flow_label = '密码登录流程'

        return trigger_scene, flow_label


    def _get_slider_failure_message(self, default_message: str) -> str:
        feedback = self.last_verification_feedback or {}
        feedback_message = str(feedback.get("message") or "").strip()
        feedback_source = str(feedback.get("source") or "").strip()
        if feedback_source in {"punish_captcha", "feedback_block"} and feedback_message:
            return feedback_message
        if feedback_message:
            return f"滑块验证失败：{feedback_message}"
        return default_message

    def _should_abort_token_refresh_slider_flow_after_failure(self) -> Tuple[bool, str]:
        """识别 token_refresh 场景下的已知硬拒绝，尽快交给外层走账密恢复。"""
        if getattr(self, "risk_trigger_scene", None) != "token_refresh":
            return False, ""

        feedback = self.last_verification_feedback or {}
        fail_code = str(feedback.get("fail_code") or "").strip().lower()
        message_parts = [
            str(feedback.get("message") or "").strip(),
            str(feedback.get("dom_error_text") or "").strip(),
        ]
        message_text = " ".join(part for part in message_parts if part)

        has_retry_failure_message = "验证失败，点击框体重试" in message_text
        has_error_code = bool(fail_code) or ("error:" in message_text.lower())
        if has_retry_failure_message and has_error_code:
            fail_code_note = fail_code or "unknown"
            return True, f"token_refresh 场景命中已知 hard reject({fail_code_note})，提前结束当前滑块流程"

        return False, ""

    def _should_abort_slider_retry_after_failure(self) -> Tuple[bool, str]:
        return self._should_abort_token_refresh_slider_flow_after_failure()

    
    def _load_or_create_browser_identity(self, profile_count: int, language_count: int,
                                         profile_version: int = 2) -> Dict[str, Any]:
        if self.browser_identity:
            return self.browser_identity

        identity = None
        try:
            if os.path.exists(self.browser_profile_file):
                with open(self.browser_profile_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    if int(loaded.get("profile_version", 0)) != int(profile_version):
                        loaded = None
                    if not loaded:
                        raise ValueError("browser profile version changed")
                    profile_index = int(loaded.get("profile_index", -1))
                    language_index = int(loaded.get("language_index", -1))
                    if 0 <= profile_index < profile_count and 0 <= language_index < language_count:
                        identity = loaded
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】加载浏览器画像失败，重新生成: {e}")

        if identity is None:
            identity = {
                "profile_version": int(profile_version),
                "profile_index": self._stable_number("browser_profile") % max(1, profile_count),
                "language_index": self._stable_number("browser_language") % max(1, language_count),
                "color_scheme": ["light", "no-preference"][self._stable_number("color_scheme") % 2],
                "plugin_count": 4 + (self._stable_number("plugin_count") % 3),
                "notification_permission": ["default", "denied"][self._stable_number("notification_permission") % 2],
                "do_not_track": ["0", "1", "unspecified"][self._stable_number("do_not_track") % 3],
                "battery_charging": bool(self._stable_number("battery_charging") % 2),
                "battery_level": round(0.45 + (self._stable_number("battery_level") % 45) / 100, 2),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            try:
                os.makedirs(os.path.dirname(self.browser_profile_file), exist_ok=True)
                with open(self.browser_profile_file, "w", encoding="utf-8") as f:
                    json.dump(identity, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】保存浏览器画像失败: {e}")

        self.browser_identity = identity
        return identity

    def _extract_profile_window_size(self, profile_id: Optional[str]) -> Optional[str]:
        match = re.search(r'_(\d+)x(\d+)$', str(profile_id or '').strip())
        if not match:
            return None
        return f"{match.group(1)},{match.group(2)}"

    def _extract_relaxed_learning_profile_group(self, profile_id: Optional[str]) -> Optional[str]:
        normalized = str(profile_id or "").strip().lower()
        match = re.match(r'^(win_chrome)_(\d+)_(\d+)x(\d+)$', normalized)
        if not match:
            return None
        if match.group(3) != "1600" or match.group(4) != "900":
            return None
        return f"{match.group(1)}_{match.group(3)}x{match.group(4)}"

    def _canonical_learning_profile_id(self, profile_id: Optional[str]) -> str:
        normalized = str(profile_id or "").strip()
        if not normalized:
            return ""
        if self.headless and self._is_password_login_scene() and self._use_headless_stable_profile():
            relaxed_group = self._extract_relaxed_learning_profile_group(normalized)
            if relaxed_group:
                return relaxed_group
        return normalized

    def _is_learning_profile_compatible(self, record_profile_id: Optional[str]) -> bool:
        if not self.profile_id:
            return True
        current_profile = self._canonical_learning_profile_id(self.profile_id)
        target_profile = self._canonical_learning_profile_id(record_profile_id)
        if not target_profile:
            return True
        return current_profile == target_profile

    def _allow_small_sample_learning(self, history: List[Dict[str, Any]],
                                     reference_distance: Optional[float] = None) -> bool:
        if len(history) < 2:
            return False

        profile_ids = set()
        canonical_profile_ids = set()
        distances = []
        for record in history:
            if not isinstance(record, dict) or not record.get("success"):
                return False

            verification_result = record.get("verification_result", {}) or {}
            record_profile_id = str(
                record.get("profile_id")
                or verification_result.get("profile_id")
                or ""
            ).strip()
            if record_profile_id:
                profile_ids.add(record_profile_id)
                canonical_profile_ids.add(self._canonical_learning_profile_id(record_profile_id))

            distance_value = record.get("distance")
            if isinstance(distance_value, (int, float)):
                distances.append(float(distance_value))

        if self.profile_id and profile_ids and any(
            not self._is_learning_profile_compatible(profile_id) for profile_id in profile_ids
        ):
            return False
        if len({profile_id for profile_id in canonical_profile_ids if profile_id}) > 1:
            return False
        if reference_distance is None:
            return False
        if not distances or any(abs(distance - float(reference_distance)) > 12.0 for distance in distances):
            return False

        return True

    def _select_preferred_browser_profile(self, browser_profiles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        stable_profile = next(
            (item for item in browser_profiles if item.get('window_size') == '1600,900'),
            None,
        )

        # 无头滑块已经验证过 1600x900 更稳，别再让画像乱飘。
        if self.headless and stable_profile:
            return stable_profile

        if self.risk_trigger_scene not in {'password_login', 'manual_password_refresh'}:
            return None

        history = self._load_success_history()
        for record in reversed(history):
            if not isinstance(record, dict) or not record.get("success"):
                continue

            verification_result = record.get("verification_result", {}) or {}
            record_profile_id = str(
                record.get("profile_id")
                or verification_result.get("profile_id")
                or ""
            ).strip()
            preferred_window_size = self._extract_profile_window_size(record_profile_id)
            if not preferred_window_size:
                continue

            matched_profile = next(
                (item for item in browser_profiles if item.get('window_size') == preferred_window_size),
                None,
            )
            if matched_profile:
                logger.info(
                    f"【{self.pure_user_id}】密码登录优先复用成功画像: {record_profile_id}"
                )
                return matched_profile

        if stable_profile:
            logger.info(f"【{self.pure_user_id}】密码登录未命中成功画像，回退到 1600x900 稳定画像")
            return stable_profile
        return None


    def _should_accept_soft_success_without_cookie_refresh(
        self,
        current_cookies: Dict[str, str],
        fallback_page=None,
    ) -> Tuple[bool, str]:
        feedback = self.last_verification_feedback or {}
        feedback_source = str(feedback.get("source") or "")
        accepted_sources = {
            "frame_detached",
            "container_missing",
            "page_changed",
            "login_element_detected",
            "context_login_confirmed",
        }

        monitor_page = fallback_page or self.page
        if self.context:
            monitor_page = self._select_monitor_page(self.context, monitor_page)

        if not monitor_page:
            return False, ""

        try:
            if self._check_login_success_by_element(monitor_page):
                return True, "登录成功元素已出现，接受无 Cookie 变更的软成功"
        except Exception:
            pass

        monitor_url = self._safe_page_url(monitor_page)
        page_has_slider = self._page_has_slider(monitor_page)
        page_looks_verify = self._page_looks_like_verification(monitor_page)

        if feedback_source in accepted_sources and not page_has_slider and not page_looks_verify:
            return True, f"页面已脱离验证态({feedback_source})，接受软成功"

        if self._has_completed_login_cookies(current_cookies) and not page_has_slider:
            if not page_looks_verify or self._is_logged_in_url(monitor_url):
                return True, "关键登录 Cookie 已完整，且页面已脱离滑块态"

        return False, ""

    def _detect_local_browser_info(self) -> Dict[str, Any]:
        if os.name != 'nt':
            return {}

        browser_candidates = [
            {
                "family": "edge",
                "channel": "msedge",
                "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            },
            {
                "family": "edge",
                "channel": "msedge",
                "path": r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            },
            {
                "family": "chrome",
                "channel": "chrome",
                "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            },
            {
                "family": "chrome",
                "channel": "chrome",
                "path": r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            },
        ]

        for candidate in browser_candidates:
            browser_path = candidate["path"]
            if not os.path.exists(browser_path):
                continue

            info = dict(candidate)
            version_text = self._read_local_browser_version(browser_path)
            if version_text:
                info["version"] = version_text
                version_match = re.search(r"(\d+)(?:\.\d+){0,3}", version_text)
                if version_match:
                    info["major_version"] = version_match.group(1)
            return info

        return {}

    def _configure_playwright_browser_env(self, env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        target_env = env if env is not None else os.environ
        target_env["PLAYWRIGHT_BROWSERS_PATH"] = self.playwright_browser_cache_dir
        return target_env

    def _find_project_browser_executable(self, browser_name: Optional[str] = None) -> Optional[str]:
        browser_name = str(browser_name or self.playwright_browser_name or "chromium").strip().lower()
        browser_root = self.playwright_browser_cache_dir
        if not os.path.isdir(browser_root):
            return None

        if sys.platform.startswith("win"):
            search_rules = {
                "chromium": [
                    ("chromium-*", os.path.join("chrome-win64", "chrome.exe")),
                    ("chromium-*", os.path.join("chrome-win", "chrome.exe")),
                ],
                "chrome": [
                    ("chrome-*", os.path.join("chrome-win64", "chrome.exe")),
                    ("chrome-*", os.path.join("chrome-win", "chrome.exe")),
                ],
                "msedge": [("msedge-*", os.path.join("msedge-win", "msedge.exe"))],
                "firefox": [("firefox-*", os.path.join("firefox", "firefox.exe"))],
                "webkit": [("webkit-*", os.path.join("Playwright.exe"))],
            }
        elif sys.platform.startswith("linux"):
            search_rules = {
                "chromium": [
                    ("chromium-*", os.path.join("chrome-linux", "chrome")),
                    ("chromium-*", os.path.join("chrome-linux", "headless_shell")),
                ],
                "chrome": [
                    ("chrome-*", os.path.join("chrome-linux", "chrome")),
                ],
                "msedge": [("msedge-*", os.path.join("msedge-linux", "msedge"))],
                "firefox": [("firefox-*", os.path.join("firefox", "firefox"))],
                "webkit": [("webkit-*", os.path.join("pw_run.sh"))],
            }
        else:
            search_rules = {
                "chromium": [("chromium-*", os.path.join("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"))],
                "chrome": [("chrome-*", os.path.join("chrome-mac", "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing"))],
                "msedge": [("msedge-*", os.path.join("msedge-mac", "Microsoft Edge.app", "Contents", "MacOS", "Microsoft Edge"))],
                "firefox": [("firefox-*", os.path.join("firefox", "Nightly.app", "Contents", "MacOS", "firefox"))],
                "webkit": [("webkit-*", os.path.join("pw_run.sh"))],
            }

        for folder_pattern, relative_binary in search_rules.get(browser_name, []):
            for folder_name in sorted(os.listdir(browser_root), reverse=True):
                if not re.fullmatch(folder_pattern.replace("*", ".*"), folder_name):
                    continue
                candidate = os.path.join(browser_root, folder_name, relative_binary)
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                    return candidate
        return None

    def _apply_project_browser_runtime_info(self, executable_path: str, browser_name: Optional[str] = None) -> Optional[str]:
        browser_name = str(browser_name or self.playwright_browser_name or "chromium").strip().lower()
        version_text = self._read_local_browser_version(executable_path)
        browser_family = "edge" if browser_name == "msedge" else "chrome"
        self.executable_path = executable_path
        self.browser_channel = None
        self.local_browser_info = {
            "path": executable_path,
            "version": version_text,
            "major_version": (version_text.split(".", 1)[0] if version_text else ""),
            "family": browser_family,
            "source": "project_playwright_cache",
        }
        return version_text

    def _summarize_subprocess_output(self, text: str, limit: int = 600) -> str:
        cleaned = (text or "").strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[-limit:]

    def _ensure_project_playwright_browser(self) -> Optional[str]:
        browser_name = str(self.playwright_browser_name or "chromium").strip().lower()
        self._configure_playwright_browser_env()
        os.makedirs(self.playwright_browser_cache_dir, exist_ok=True)

        existing_executable = self._find_project_browser_executable(browser_name)
        if existing_executable:
            self._apply_project_browser_runtime_info(existing_executable, browser_name)
            logger.info(f"【{self.pure_user_id}】复用项目内 Playwright 浏览器: {existing_executable}")
            return existing_executable

        with _PLAYWRIGHT_BROWSER_INSTALL_LOCK:
            existing_executable = self._find_project_browser_executable(browser_name)
            if existing_executable:
                self._apply_project_browser_runtime_info(existing_executable, browser_name)
                logger.info(f"【{self.pure_user_id}】复用已下载的 Playwright 浏览器: {existing_executable}")
                return existing_executable

            try:
                has_cached_entries = any(os.scandir(self.playwright_browser_cache_dir))
            except Exception:
                has_cached_entries = False
            if has_cached_entries:
                logger.warning(
                    f"【{self.pure_user_id}】Playwright 浏览器目录已存在但未直接解析到可执行文件，"
                    f"保留 Playwright 默认查找逻辑: {self.playwright_browser_cache_dir}"
                )
                return None

            install_env = self._configure_playwright_browser_env(os.environ.copy())
            proxy_url = str(self.playwright_download_proxy or "").strip()
            if proxy_url:
                install_env.setdefault("HTTP_PROXY", proxy_url)
                install_env.setdefault("HTTPS_PROXY", proxy_url)
                install_env.setdefault("ALL_PROXY", proxy_url)

            install_cmd = [sys.executable, "-m", "playwright", "install", browser_name]
            logger.info(
                f"【{self.pure_user_id}】项目内未发现 Playwright 浏览器，开始自动下载: "
                f"{browser_name}, cache={self.playwright_browser_cache_dir}, proxy={proxy_url or 'none'}"
            )
            install_result = subprocess.run(
                install_cmd,
                env=install_env,
                timeout=900,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if install_result.returncode != 0:
                stdout_text = self._summarize_subprocess_output(install_result.stdout)
                stderr_text = self._summarize_subprocess_output(install_result.stderr)
                logger.error(f"【{self.pure_user_id}】Playwright 浏览器自动下载失败，stdout: {stdout_text}")
                logger.error(f"【{self.pure_user_id}】Playwright 浏览器自动下载失败，stderr: {stderr_text}")
                raise RuntimeError(f"Playwright 浏览器自动下载失败: {browser_name}")

            existing_executable = self._find_project_browser_executable(browser_name)
            if not existing_executable:
                raise RuntimeError(f"Playwright 浏览器下载完成但未找到可执行文件: {browser_name}")

            self._apply_project_browser_runtime_info(existing_executable, browser_name)
            logger.info(f"【{self.pure_user_id}】Playwright 浏览器下载完成: {existing_executable}")
            return existing_executable

    def _read_local_browser_version(self, browser_path: str) -> Optional[str]:
        if not browser_path or not os.path.exists(browser_path):
            return None

        if os.name == 'nt':
            try:
                output = subprocess.check_output(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"(Get-Item -LiteralPath '{browser_path}').VersionInfo.ProductVersion",
                    ],
                    timeout=3,
                    encoding="utf-8",
                    errors="ignore",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                ).strip()
                version_match = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
                version_text = version_match.group(1) if version_match else ""
                if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version_text):
                    return version_text
            except Exception:
                pass

            try:
                escaped_browser_path = browser_path.replace("\\", "\\\\")
                output = subprocess.check_output(
                    [
                        "cmd",
                        "/c",
                        "wmic",
                        "datafile",
                        "where",
                        f"name='{escaped_browser_path}'",
                        "get",
                        "Version",
                        "/value",
                    ],
                    timeout=3,
                    encoding="utf-8",
                    errors="ignore",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                ).strip()
                version_match = re.search(r"Version=(\d+\.\d+\.\d+\.\d+)", output)
                if version_match:
                    return version_match.group(1)
            except Exception:
                pass

        try:
            output = subprocess.check_output(
                [browser_path, "--version"],
                timeout=3,
                encoding="utf-8",
                errors="ignore",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).strip()
            match = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
            return match.group(1) if match else None
        except Exception:
            return None


    def _get_sync_playwright_factory(self):
        if self.automation_backend == "patchright" and patchright_sync_playwright is not None:
            return patchright_sync_playwright
        return playwright_sync_playwright

    def _resolve_stealth_mode(self) -> str:
        override = str(self.stealth_mode_override or "").strip().lower()
        if override in {"off", "lite", "full"}:
            return override

        # Patchright 的 init_script 是通过路由注入的，额外脚本越多越容易把自己暴露出去。
        if self.headless and self.automation_backend == "patchright":
            return "off"

        return "full"

    def _use_headless_stable_profile(self) -> bool:
        return bool(
            self.headless
            and str(self.profile_id or "").startswith("win_chrome_147_1600x900")
        )

    def _should_prefer_docker_conservative_profile(self, has_learning: bool) -> bool:
        if has_learning:
            return False
        if not (self.headless and self.is_docker_env and self.automation_backend == "playwright"):
            return False
        if not self._use_headless_stable_profile():
            return False
        local_browser_info = getattr(self, "local_browser_info", None) or {}
        return bool(
            str(local_browser_info.get("source") or "") == "project_playwright_cache"
            or bool(local_browser_info.get("version"))
            or bool(self.executable_path)
        )

    def _should_force_docker_cold_start_conservative(self, attempt: int, has_learning: bool) -> bool:
        return attempt == 1 and self._should_prefer_docker_conservative_profile(has_learning)


    def _install_stealth_init_script(self, page, browser_features: Dict[str, Any], mode_override: Optional[str] = None):
        mode = str(mode_override or "").strip().lower() or self._resolve_stealth_mode()
        self.active_stealth_mode = mode

        if mode == "off":
            logger.info(
                f"【{self.pure_user_id}】跳过自定义 init_script：backend={self.automation_backend}, "
                f"headless={self.headless}, mode={mode}"
            )
            return

        script = self._get_stealth_script(browser_features)
        if mode == "lite":
            script = self._get_light_stealth_script(browser_features)

        page.add_init_script(script)
        logger.info(
            f"【{self.pure_user_id}】已注入 {mode} 级别反检测脚本："
            f"backend={self.automation_backend}, headless={self.headless}"
        )


    def _merge_runtime_feedback(self, search_target=None):
        feedback = dict(self.last_verification_feedback or {})
        runtime_debug = self._collect_runtime_debug_info(search_target)
        target_debug = runtime_debug.get("target") or runtime_debug.get("page") or {}

        fail_code = str(target_debug.get("ncFailCode") or "").strip()
        error_text = str(target_debug.get("errorText") or "").strip()
        if fail_code:
            feedback["fail_code"] = fail_code
        if error_text:
            feedback["dom_error_text"] = error_text

        self.last_verification_feedback = feedback

    def _harden_password_slider_runtime(self, search_target=None) -> None:
        if getattr(self, "_password_slider_runtime_hardened", False):
            return

        targets = []
        if search_target is not None:
            targets.append(("slider", search_target))
        if self.page is not None and self.page is not search_target:
            targets.append(("page", self.page))

        if not targets:
            self._password_slider_runtime_hardened = True
            return

        harden_script = """
            () => {
                const defineGetter = (target, prop, getter) => {
                    try {
                        Object.defineProperty(target, prop, {
                            get: getter,
                            configurable: true
                        });
                    } catch (e) {}
                };

                try {
                    defineGetter(Navigator.prototype, 'webdriver', () => undefined);
                } catch (e) {}
                try {
                    defineGetter(Navigator.prototype, 'languages', () => ['zh-CN', 'zh', 'en']);
                } catch (e) {}
                try {
                    defineGetter(Navigator.prototype, 'plugins', () => [1, 2, 3, 4, 5]);
                } catch (e) {}
                try {
                    window.chrome = window.chrome || {};
                    window.chrome.runtime = window.chrome.runtime || {};
                } catch (e) {}
                return true;
            }
        """

        applied = False
        for target_name, target in targets:
            try:
                target.evaluate(harden_script)
                applied = True
                logger.info(f"【{self.pure_user_id}】已加固密码登录滑块运行时: {target_name}")
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】加固密码登录滑块运行时失败({target_name}): {e}")

        self._password_slider_runtime_hardened = True
        if not applied:
            logger.debug(f"【{self.pure_user_id}】密码登录滑块运行时加固未命中可执行目标")

    def _apply_runtime_browser_profile(self, browser_features: Dict[str, Any]) -> Dict[str, Any]:
        features = dict(browser_features)

        if os.name == 'nt':
            features['platform'] = 'Win32'
            features['timezone_id'] = 'Asia/Shanghai'

        local_browser_info = getattr(self, "local_browser_info", None) or {}
        full_version = str(local_browser_info.get("version") or "").strip()
        major_version = str(local_browser_info.get("major_version") or "").strip()
        if not full_version:
            ua_match = re.search(r"Chrome/(\d+\.\d+\.\d+\.\d+)", str(features.get("user_agent") or ""))
            if ua_match:
                full_version = ua_match.group(1)
                major_version = full_version.split(".", 1)[0]

        if not full_version:
            return features

        browser_family = self._get_browser_family()
        if browser_family == "edge":
            features['user_agent'] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{full_version} Safari/537.36 Edg/{full_version}"
            )
            features['profile_id'] = (
                f"win_edge_{major_version}_{features.get('viewport_width', 1600)}x"
                f"{features.get('viewport_height', 900)}"
            )
        else:
            features['user_agent'] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{full_version} Safari/537.36"
            )
            features['profile_id'] = (
                f"win_chrome_{major_version}_{features.get('viewport_width', 1600)}x"
                f"{features.get('viewport_height', 900)}"
            )

        features['browser_version'] = full_version
        features['browser_major_version'] = major_version
        return features

    def _warmup_slider_context(self, target_url: Optional[str] = None):
        if not self.page:
            return

        warmup_urls = [
            "https://www.goofish.com",
            "https://www.goofish.com/im",
        ]

        for warmup_url in warmup_urls:
            if target_url and warmup_url == target_url:
                continue
            try:
                logger.info(f"【{self.pure_user_id}】预热访问: {warmup_url}")
                self.page.goto(warmup_url, wait_until="domcontentloaded", timeout=15000)
                time.sleep(random.uniform(0.8, 1.6))
                self.page.mouse.move(random.randint(260, 980), random.randint(180, 620))
                time.sleep(random.uniform(0.05, 0.12))
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】预热访问失败({warmup_url}): {e}")

    def _build_playwright_proxy_settings(self) -> Optional[Dict[str, str]]:
        proxy_type = str(self.proxy_config.get("proxy_type") or "").strip().lower()
        proxy_host = str(self.proxy_config.get("proxy_host") or "").strip()
        proxy_port = self.proxy_config.get("proxy_port")
        if proxy_type in {"", "none"} or not proxy_host or not proxy_port:
            return None

        proxy_settings: Dict[str, str] = {
            "server": f"{proxy_type}://{proxy_host}:{proxy_port}"
        }
        proxy_user = str(self.proxy_config.get("proxy_user") or "").strip()
        proxy_pass = str(self.proxy_config.get("proxy_pass") or "").strip()
        if proxy_user:
            proxy_settings["username"] = proxy_user
        if proxy_pass:
            proxy_settings["password"] = proxy_pass
        return proxy_settings

    def _should_use_account_persistent_profile(self) -> bool:
        return bool(getattr(self, "use_account_persistent_profile", False))

    def _resolve_account_persistent_profile_dir(self) -> str:
        profile_dir = str(getattr(self, "account_persistent_profile_dir", None) or "").strip()
        if not profile_dir:
            profile_dir = os.path.join(os.getcwd(), 'browser_data', f'user_{self.pure_user_id}')
        os.makedirs(profile_dir, exist_ok=True)
        return profile_dir

    def _build_playwright_context_options(self, browser_features: Dict[str, Any]) -> Dict[str, Any]:
        context_options: Dict[str, Any] = {
            'user_agent': browser_features['user_agent'],
            'locale': browser_features['locale'],
            'timezone_id': browser_features['timezone_id'],
            'color_scheme': browser_features['color_scheme'],
            'extra_http_headers': {
                'Accept-Language': browser_features['accept_lang']
            },
        }
        if not self.headless:
            context_options['no_viewport'] = True
        else:
            context_options.update({
                'viewport': {'width': browser_features['viewport_width'], 'height': browser_features['viewport_height']},
                'screen': {'width': browser_features['viewport_width'], 'height': browser_features['viewport_height']},
                'device_scale_factor': browser_features['device_scale_factor'],
                'is_mobile': browser_features['is_mobile'],
                'has_touch': browser_features['has_touch'],
            })
        return context_options


    def _try_reset_slider_error_state(self, search_root, slider_container=None) -> bool:
        """阿里系 nocaptcha 常先落在“验证失败，点击框体重试”态，先点一下把真滑块唤出来。"""
        try:
            candidate_selectors = [
                "#nocaptcha .errloading",
                ".nc_wrapper .errloading",
                "[id*='refresh']",
                ".errloading",
            ]

            clicked = False
            for selector in candidate_selectors:
                try:
                    element = search_root.query_selector(selector)
                    if not element:
                        continue
                    try:
                        text = (element.inner_text() or "").strip()
                    except Exception:
                        text = ""
                    if text and ("点击框体重试" not in text and "验证失败" not in text):
                        continue
                    element.click(timeout=1500)
                    logger.info(f"【{self.pure_user_id}】检测到滑块错误态，已点击重试元素: {selector}")
                    clicked = True
                    break
                except Exception as selector_error:
                    continue

            if not clicked and slider_container:
                try:
                    slider_container.click(timeout=1500)
                    logger.info(f"【{self.pure_user_id}】未命中重试元素，已点击滑块容器尝试唤起真实滑块")
                    clicked = True
                except Exception:
                    pass

            if clicked:
                time.sleep(1.2)
                return True
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】重置滑块错误态失败: {e}")
        return False

    def init_browser(self):
        """初始化浏览器 - 增强反检测版本"""
        try:
            if not self.browser_channel and not self.executable_path:
                self._ensure_project_playwright_browser()

            # 启动 Playwright
            playwright_factory = self._get_sync_playwright_factory()
            logger.info(f"【{self.pure_user_id}】启动浏览器自动化后端: {self.automation_backend}")
            self.playwright = playwright_factory().start()
            self._playwright_thread_id = threading.get_ident()
            logger.info(f"【{self.pure_user_id}】{self.automation_backend} 启动成功")
            
            # 为账号加载稳定浏览器画像
            browser_features = self._get_random_browser_features()
            self.browser_features = browser_features
            self.profile_id = browser_features.get("profile_id", "unknown")
            
            # 启动浏览器，使用稳定特征
            logger.info(
                f"【{self.pure_user_id}】启动浏览器，headless模式: {self.headless}, "
                f"画像: {self.profile_id}, UA: {browser_features['user_agent']}"
            )
            launch_options: Dict[str, Any] = {
                "headless": self.headless,
                "ignore_default_args": ["--enable-automation"],
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    f"--window-size={browser_features['window_size']}",
                    f"--lang={browser_features['lang']}",
                    f"--accept-lang={browser_features['accept_lang']}",
                    "--disable-blink-features=AutomationControlled",
                    "--mute-audio",
                    "--no-default-browser-check",
                    "--force-color-profile=srgb",
                    "--password-store=basic",
                    "--use-mock-keychain",
                ],
            }
            proxy_settings = self._build_playwright_proxy_settings()
            if proxy_settings:
                launch_options["proxy"] = proxy_settings
                logger.info(f"【{self.pure_user_id}】滑块浏览器启用代理: {proxy_settings['server']}")
            if self.browser_channel:
                launch_options["channel"] = self.browser_channel
            if self.executable_path:
                launch_options["executable_path"] = self.executable_path
                logger.info(f"【{self.pure_user_id}】滑块浏览器使用本机可执行文件: {self.executable_path}")
            context_options = self._build_playwright_context_options(browser_features)
            launched_with_persistent_profile = False

            if self._should_use_account_persistent_profile():
                user_data_dir = self._resolve_account_persistent_profile_dir()
                persistent_launch_options = dict(launch_options)
                persistent_launch_options.update(context_options)
                persistent_launch_options.update({
                    'accept_downloads': True,
                    'ignore_https_errors': True,
                })
                logger.info(f"【{self.pure_user_id}】token_refresh滑块优先复用账号级浏览器目录: {user_data_dir}")
                try:
                    self.context = self.playwright.chromium.launch_persistent_context(
                        user_data_dir,
                        **persistent_launch_options,
                    )
                    launched_with_persistent_profile = True
                    self.browser = None
                    self._browser_pid = self._extract_browser_pid(self.context)
                except Exception as persistent_launch_error:
                    if not self._is_profile_in_use_launch_error(persistent_launch_error):
                        raise
                    cleaned_stale_lock = self._try_cleanup_stale_chromium_singleton_lock(user_data_dir)
                    if cleaned_stale_lock:
                        logger.warning(
                            f"【{self.pure_user_id}】检测到账号级 profile 疑似残留 stale Chromium 锁，"
                            f"已清理并重试 persistent context: {user_data_dir}"
                        )
                        try:
                            self.context = self.playwright.chromium.launch_persistent_context(
                                user_data_dir,
                                **persistent_launch_options,
                            )
                            launched_with_persistent_profile = True
                            self.browser = None
                            self._browser_pid = self._extract_browser_pid(self.context)
                        except Exception as retry_launch_error:
                            if not self._is_profile_in_use_launch_error(retry_launch_error):
                                raise
                            logger.warning(
                                f"【{self.pure_user_id}】清理 stale Chromium 锁后仍提示 profile 被占用，"
                                f"回退临时上下文链路: {retry_launch_error}"
                            )
                    else:
                        logger.warning(
                            f"【{self.pure_user_id}】账号级浏览器目录被占用，且无法证明是 stale Chromium 锁，"
                            f"回退临时上下文链路: {persistent_launch_error}"
                        )

            if not launched_with_persistent_profile:
                try:
                    self.browser = self.playwright.chromium.launch(**launch_options)
                    self._browser_pid = self._extract_browser_pid(self.browser)
                except Exception as launch_error:
                    if self.headless and (launch_options.get("executable_path") or launch_options.get("channel")):
                        fallback_options = dict(launch_options)
                        fallback_options.pop("executable_path", None)
                        fallback_options.pop("channel", None)
                        logger.warning(
                            f"【{self.pure_user_id}】指定浏览器无头启动失败，回退到 Playwright Chromium: {launch_error}"
                        )
                        self.browser = self.playwright.chromium.launch(**fallback_options)
                        self._browser_pid = self._extract_browser_pid(self.browser)
                    else:
                        raise
            
            if launched_with_persistent_profile:
                logger.info(f"【{self.pure_user_id}】账号级 persistent browser context 启动成功")
            else:
                # 验证浏览器已启动
                if not self.browser or not self.browser.is_connected():
                    raise Exception("浏览器启动失败或连接已断开")
                logger.info(f"【{self.pure_user_id}】浏览器启动成功，已连接: {self.browser.is_connected()}")
                
                # 创建上下文，使用随机特征
                logger.info(f"【{self.pure_user_id}】创建浏览器上下文...")
                self.context = self.browser.new_context(**context_options)
            
            # 验证上下文已创建
            if not self.context:
                raise Exception("浏览器上下文创建失败")
            logger.info(f"【{self.pure_user_id}】浏览器上下文创建成功")

            initial_cookie_payload = self._build_initial_cookie_payload()
            if initial_cookie_payload:
                self.context.add_cookies(initial_cookie_payload)
                logger.info(f"【{self.pure_user_id}】已向滑块上下文注入 {len(initial_cookie_payload)} 个初始Cookie")
            
            # 创建新页面
            logger.info(f"【{self.pure_user_id}】创建新页面...")
            self.page = self.context.new_page()
            
            # 验证页面已创建
            if not self.page:
                raise Exception("页面创建失败")
            logger.info(f"【{self.pure_user_id}】页面创建成功（{'最大化窗口模式' if not self.headless else '无头模式'}）")
            
            # 添加增强反检测脚本
            logger.info(f"【{self.pure_user_id}】添加反检测脚本...")
            self._install_stealth_init_script(self.page, browser_features)
            logger.info(f"【{self.pure_user_id}】浏览器初始化完成")
            
            return self.page
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】初始化浏览器失败: {e}")
            import traceback
            logger.error(f"【{self.pure_user_id}】详细错误堆栈: {traceback.format_exc()}")
            # 确保在异常时也清理已创建的资源
            self._cleanup_on_init_failure()
            raise
    
    def _cleanup_on_init_failure(self):
        """初始化失败时的清理"""
        try:
            if hasattr(self, 'page') and self.page:
                self.page.close()
                self.page = None
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】清理页面时出错: {e}")
        
        try:
            if hasattr(self, 'context') and self.context:
                self.context.close()
                self.context = None
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】清理上下文时出错: {e}")
        
        try:
            if hasattr(self, 'browser') and self.browser:
                self.browser.close()
                self.browser = None
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】清理浏览器时出错: {e}")

        self._force_kill_browser_process_tree("init_failure_cleanup")
        
        try:
            if hasattr(self, 'playwright') and self.playwright:
                self.playwright.stop()
                self.playwright = None
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】清理Playwright时出错: {e}")
    
    def _load_success_history(self) -> List[Dict[str, Any]]:
        """加载历史成功数据（带自动清理）"""
        try:
            if not os.path.exists(self.success_history_file):
                return []
            
            # 🧹 自动检查并清理历史数据
            try:
                cleaned = adaptive_strategy_manager.check_and_cleanup_history(
                    self.pure_user_id, 
                    self.success_history_file
                )
                if cleaned:
                    logger.info(f"【{self.pure_user_id}】🧹 历史数据已自动清理")
            except Exception as cleanup_e:
                logger.debug(f"【{self.pure_user_id}】清理检查跳过: {cleanup_e}")
            
            with open(self.success_history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
                logger.info(f"【{self.pure_user_id}】加载历史成功数据: {len(history)}条记录")
                return history
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】加载历史数据失败: {e}")
            return []
    
    def _get_learning_history_with_fallback(self, reference_distance: Optional[float] = None,
                                            limit: int = 24) -> List[Dict[str, Any]]:
        """Cold-start fallback: reuse recent success samples from the same headless profile."""
        history = self._load_success_history()
        if len(history) >= 3 or not self._use_headless_stable_profile():
            return history

        try:
            history_dir = os.path.dirname(self.success_history_file) or "trajectory_history"
            current_file = os.path.abspath(self.success_history_file)
            matched_records = []
            relaxed_records = []
            distance_tolerance = 3.0 if self._is_password_login_scene() else 12.0

            for history_path in glob.glob(os.path.join(history_dir, "*_success.json")):
                try:
                    if os.path.abspath(history_path) == current_file:
                        continue

                    with open(history_path, 'r', encoding='utf-8') as f:
                        raw_records = json.load(f)

                    if isinstance(raw_records, dict):
                        raw_records = [raw_records]
                    if not isinstance(raw_records, list):
                        continue

                    for record in raw_records:
                        if not isinstance(record, dict) or not record.get("success"):
                            continue
                        if not self._is_learning_sample_scene_compatible(history_path, record):
                            continue

                        verification_result = record.get("verification_result", {}) or {}
                        record_headless = bool(record.get("headless", verification_result.get("headless", self.headless)))
                        if record_headless != bool(self.headless):
                            continue

                        record_profile_id = str(
                            record.get("profile_id")
                            or verification_result.get("profile_id")
                            or ""
                        ).strip()
                        if self.profile_id and record_profile_id and not self._is_learning_profile_compatible(record_profile_id):
                            continue

                        distance_value = record.get("distance")
                        if reference_distance is not None and isinstance(distance_value, (int, float)):
                            if abs(float(distance_value) - float(reference_distance)) <= distance_tolerance:
                                matched_records.append(record)
                            else:
                                relaxed_records.append(record)
                        else:
                            matched_records.append(record)
                except Exception as history_err:
                    logger.debug(f"【{self.pure_user_id}】读取全局成功样本失败 {history_path}: {history_err}")

            matched_records.sort(key=lambda item: item.get("timestamp", 0), reverse=True)
            relaxed_records.sort(key=lambda item: item.get("timestamp", 0), reverse=True)

            if reference_distance is not None and len(matched_records) < 3 and not self._is_password_login_scene():
                matched_records.extend(relaxed_records)

            if limit > 0:
                matched_records = matched_records[:limit]

            if matched_records:
                needed = max(0, limit - len(history))
                injected_records = matched_records[:needed]
                history = list(history) + injected_records
                logger.info(
                    f"【{self.pure_user_id}】本地成功记录不足，补充加载 {len(injected_records)} 条同画像全局成功样本"
                )
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】加载全局成功样本失败: {e}")

        return history

    def _normalize_learning_scene(self, trigger_scene: Optional[str] = None) -> str:
        scene = str(trigger_scene or getattr(self, "risk_trigger_scene", None) or "").strip().lower()
        if scene in {"password_login", "manual_password_refresh"}:
            return "password"
        if scene == "token_refresh":
            return "token_refresh"
        if scene == "auto_cookie_refresh":
            return "cookie"
        return scene or "generic"

    def _infer_success_sample_scene(self, history_path: str, record: Optional[Dict[str, Any]] = None) -> str:
        explicit_scene = ""
        if isinstance(record, dict):
            explicit_scene = str(
                record.get("trigger_scene")
                or record.get("risk_trigger_scene")
                or ""
            ).strip().lower()
        normalized_explicit_scene = self._normalize_learning_scene(explicit_scene) if explicit_scene else ""
        if normalized_explicit_scene and normalized_explicit_scene != "generic":
            return normalized_explicit_scene

        parts = [os.path.basename(str(history_path or ""))]
        if isinstance(record, dict):
            parts.extend(
                [
                    str(record.get("user_id") or ""),
                    str(record.get("page_url") or ""),
                    str(record.get("page_title") or ""),
                ]
            )

        sample_text = " ".join(parts).lower()
        if not sample_text:
            return "generic"

        token_refresh_tokens = (
            "token_refresh",
            "keepalive",
            "session_keepalive",
            "captcha_verification_failed",
        )
        if any(token in sample_text for token in token_refresh_tokens):
            return "token_refresh"

        if any(token in sample_text for token in ("password", "pwd")):
            return "password"

        cookie_tokens = (
            "ui_cookie",
            "import_user_cookie",
            "manual_cookie",
            "cookie_import",
            "manual_import",
            "cookie_flow",
            "cookie_run",
            "cookie_headless",
        )
        if any(token in sample_text for token in cookie_tokens):
            return "cookie"

        if "refresh" in sample_text and "password" not in sample_text and "pwd" not in sample_text:
            return "token_refresh"

        return "generic"

    def _is_learning_sample_scene_compatible(self, history_path: str, record: Optional[Dict[str, Any]] = None) -> bool:
        current_scene = self._normalize_learning_scene()
        if current_scene == "generic":
            return self._is_password_scene_success_sample(history_path, record)

        sample_scene = self._infer_success_sample_scene(history_path, record)
        if sample_scene == "generic":
            return False
        return sample_scene == current_scene


    def _save_success_record(self, trajectory_data: Dict[str, Any]):
        """保存成功记录（增强版 - 记录所有随机参数用于学习优化）"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.success_history_file), exist_ok=True)
            
            # 加载现有历史
            history = self._load_success_history()
            
            # 获取随机参数
            random_params = trajectory_data.get("random_params", {})
            slide_behavior = trajectory_data.get("slide_behavior", {})
            verification_result = trajectory_data.get("verification_result", {})
            
            # 添加新记录 - 保存完整的随机参数用于学习
            record = {
                "timestamp": time.time(),
                "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user_id": self.pure_user_id,
                "trigger_scene": getattr(self, "risk_trigger_scene", None),
                "distance": trajectory_data.get("distance", 0),
                "total_steps": trajectory_data.get("total_steps", 0),
                "model": trajectory_data.get("model", "unknown"),
                # 新增：保存所有轨迹生成的随机参数
                "overshoot_ratio": random_params.get("overshoot_ratio", 0),
                "base_delay": random_params.get("base_delay", 0),
                "acceleration_curve": random_params.get("acceleration_curve", 0),
                "y_jitter_max": random_params.get("y_jitter_max", 0),
                "random_state_snapshot": random_params.get("random_state_snapshot", []),
                # 新增：保存所有滑动行为的随机参数（18个随机因素）
                "slide_behavior": {
                    "approach_offset_x": slide_behavior.get("approach_offset_x", 0),
                    "approach_offset_y": slide_behavior.get("approach_offset_y", 0),
                    "approach_steps": slide_behavior.get("approach_steps", 0),
                    "approach_pause": slide_behavior.get("approach_pause", 0),
                    "precision_steps": slide_behavior.get("precision_steps", 0),
                    "precision_pause": slide_behavior.get("precision_pause", 0),
                    "skip_hover": slide_behavior.get("skip_hover", False),
                    "hover_pause": slide_behavior.get("hover_pause", 0),
                    "pre_down_pause": slide_behavior.get("pre_down_pause", 0),
                    "post_down_pause": slide_behavior.get("post_down_pause", 0),
                    "move_steps_range": slide_behavior.get("move_steps_range", (1, 3)),
                    "delay_variation": slide_behavior.get("delay_variation", (0.9, 1.1)),
                    "pre_up_pause": slide_behavior.get("pre_up_pause", 0),
                    "post_up_pause": slide_behavior.get("post_up_pause", 0),
                    "server_judge_wait": slide_behavior.get("server_judge_wait", 0),
                    "total_elapsed_time": slide_behavior.get("total_elapsed_time", 0),
                },
                # 保留旧字段以兼容旧版本
                "base_delay_old": trajectory_data.get("base_delay", 0),
                "jitter_x_range": trajectory_data.get("jitter_x_range", [0, 0]),
                "jitter_y_range": trajectory_data.get("jitter_y_range", [0, 0]),
                "slow_factor": trajectory_data.get("slow_factor", 0),
                "acceleration_phase": trajectory_data.get("acceleration_phase", 0),
                "fast_phase": trajectory_data.get("fast_phase", 0),
                "slow_start_ratio": trajectory_data.get("slow_start_ratio", 0),
                # 【优化】不再保存完整轨迹点，节省 90% 存储空间
                # "trajectory_points": trajectory_data.get("trajectory_points", []),
                "trajectory_point_count": len(trajectory_data.get("trajectory_points", [])),  # 只记录数量
                "final_left_px": trajectory_data.get("final_left_px", 0),
                "completion_used": trajectory_data.get("completion_used", False),
                "completion_steps": trajectory_data.get("completion_steps", 0),
                "profile_id": verification_result.get("profile_id", self.profile_id),
                "headless": verification_result.get("headless", self.headless),
                "verification_result": verification_result,
                "success": True
            }
            
            history.append(record)
            
            # 只保留最近100条成功记录
            if len(history) > 100:
                history = history[-100:]
            
            # 保存到文件
            with open(self.success_history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            
            # 统计滑动行为参数数量
            behavior_params_count = len([k for k in slide_behavior.keys() if not k.startswith('hesitation_at_')])
            
            logger.info(f"【{self.pure_user_id}】✅ 保存成功记录: "
                       f"距离{record['distance']:.1f}px, 步数{record['total_steps']}, "
                       f"超调{record['overshoot_ratio']:.2f}x, 加速^{record['acceleration_curve']:.2f}, "
                       f"行为参数{behavior_params_count}个")
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】保存成功记录失败: {e}")

    def _save_failure_record(self, trajectory_data: Dict[str, Any], failure_info: Dict[str, Any]):
        """保存失败记录，便于分析最近失败样本"""
        try:
            os.makedirs(os.path.dirname(self.failure_history_file), exist_ok=True)

            history = []
            if os.path.exists(self.failure_history_file):
                with open(self.failure_history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)

            random_params = trajectory_data.get("random_params", {})
            slide_behavior = trajectory_data.get("slide_behavior", {})
            verification_feedback = failure_info.get("verification_feedback", {})
            verification_result = trajectory_data.get("verification_result", {})

            try:
                page_url = self.page.url if self.page else ""
            except Exception:
                page_url = ""

            try:
                page_title = self.page.title() if self.page else ""
            except Exception:
                page_title = ""

            record = {
                "timestamp": time.time(),
                "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user_id": self.pure_user_id,
                "trigger_scene": getattr(self, "risk_trigger_scene", None),
                "attempt": failure_info.get("attempt", 0),
                "distance": trajectory_data.get("distance", 0),
                "slide_distance": failure_info.get("slide_distance", 0),
                "total_steps": trajectory_data.get("total_steps", 0),
                "model": trajectory_data.get("model", "unknown"),
                "overshoot_ratio": random_params.get("overshoot_ratio", 0),
                "requested_steps": random_params.get("steps", 0),
                "base_delay": random_params.get("base_delay", 0),
                "acceleration_curve": random_params.get("acceleration_curve", 0),
                "y_jitter_max": random_params.get("y_jitter_max", 0),
                "strategy": random_params.get("strategy", "unknown"),
                "profile": random_params.get("profile", "unknown"),
                "use_exploration": random_params.get("use_exploration", False),
                "final_left_px": trajectory_data.get("final_left_px", 0),
                "trajectory_point_count": len(trajectory_data.get("trajectory_points", [])),
                "slide_behavior": {
                    "approach_offset_x": slide_behavior.get("approach_offset_x", 0),
                    "approach_offset_y": slide_behavior.get("approach_offset_y", 0),
                    "approach_steps": slide_behavior.get("approach_steps", 0),
                    "approach_pause": slide_behavior.get("approach_pause", 0),
                    "precision_steps": slide_behavior.get("precision_steps", 0),
                    "precision_pause": slide_behavior.get("precision_pause", 0),
                    "skip_hover": slide_behavior.get("skip_hover", False),
                    "hover_pause": slide_behavior.get("hover_pause", 0),
                    "pre_down_pause": slide_behavior.get("pre_down_pause", 0),
                    "post_down_pause": slide_behavior.get("post_down_pause", 0),
                    "pre_up_pause": slide_behavior.get("pre_up_pause", 0),
                    "post_up_pause": slide_behavior.get("post_up_pause", 0),
                    "delay_variation": slide_behavior.get("delay_variation", (0.9, 1.1)),
                    "server_judge_wait": slide_behavior.get("server_judge_wait", 0),
                    "total_elapsed_time": slide_behavior.get("total_elapsed_time", 0),
                },
                "verification_feedback": verification_feedback,
                "verification_result": verification_result,
                "profile_id": verification_result.get("profile_id", self.profile_id),
                "headless": verification_result.get("headless", self.headless),
                "page_url": page_url,
                "page_title": page_title,
                "success": False
            }

            history.append(record)
            if len(history) > 200:
                history = history[-200:]

            with open(self.failure_history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)

            logger.info(
                f"【{self.pure_user_id}】📝 保存失败记录: 第{record['attempt']}次, "
                f"策略={record['strategy']}/{record['profile']}, "
                f"距离{record['slide_distance']:.1f}px, 步数{record['total_steps']}"
            )

        except Exception as e:
            logger.error(f"【{self.pure_user_id}】保存失败记录失败: {e}")

    def _save_debug_snapshot(self, reason: str, search_target=None):
        """保存失败现场，方便比对页面状态和风控返回。"""
        try:
            debug_dir = os.path.join("logs", "slider_debug")
            os.makedirs(debug_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_reason = "".join(
                ch if ch.isalnum() or ch in "._-" else "_"
                for ch in str(reason or "snapshot")
            ).strip("._") or "snapshot"
            base_name = f"{self.pure_user_id}_{safe_reason}_{timestamp}"

            page_url = ""
            page_title = ""
            try:
                if self.page:
                    page_url = self.page.url or ""
                    page_title = self.page.title() or ""
            except Exception:
                pass

            frame_url = ""
            try:
                if search_target is not None and hasattr(search_target, "url"):
                    frame_url = getattr(search_target, "url", "") or ""
            except Exception:
                pass

            if self.page:
                screenshot_path = os.path.join(debug_dir, f"{base_name}.png")
                try:
                    self.page.screenshot(path=screenshot_path, full_page=True, timeout=10000)
                except Exception:
                    self.page.screenshot(path=screenshot_path, full_page=False, timeout=10000)

                page_html_path = os.path.join(debug_dir, f"{base_name}.html")
                with open(page_html_path, "w", encoding="utf-8") as f:
                    f.write(self.page.content())

            if search_target is not None and search_target is not self.page:
                try:
                    frame_html = search_target.content()
                    frame_html_path = os.path.join(debug_dir, f"{base_name}__frame.html")
                    with open(frame_html_path, "w", encoding="utf-8") as f:
                        f.write(frame_html)
                except Exception as frame_err:
                    logger.debug(f"【{self.pure_user_id}】保存Frame HTML失败: {frame_err}")

            runtime_debug = self._collect_runtime_debug_info(search_target)
            meta = {
                "user_id": self.pure_user_id,
                "reason": reason,
                "page_url": page_url,
                "page_title": page_title,
                "frame_url": frame_url,
                "feedback": dict(self.last_verification_feedback or {}),
                "runtime_debug": runtime_debug,
                "profile_id": self.profile_id,
                "headless": self.headless,
                "automation_backend": self.automation_backend,
                "stealth_mode": self.active_stealth_mode,
                "local_browser_info": dict(self.local_browser_info or {}),
                "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            meta_path = os.path.join(debug_dir, f"{base_name}.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            logger.info(f"【{self.pure_user_id}】已保存调试快照: {os.path.join(debug_dir, base_name)}")
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】保存调试快照失败: {e}")
    
    
    # 关键 Cookie 名称列表（用于判定"有意义的刷新"）
    _KEY_COOKIE_NAMES = {
        '_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 'unb', 'sgcookie',
        'uc1', 'uc3', 'uc4', 'csg', 'sn',
    }
    _PROTECTED_SESSION_COOKIE_FIELDS = (
        'unb',
        'sgcookie',
        'cookie2',
        '_m_h5_tk',
        '_m_h5_tk_enc',
        't',
        'cna',
        'havana_lgc2_77',
        '_tb_token_',
    )
    _REQUIRED_SESSION_COOKIE_FIELDS = (
        'unb',
        'sgcookie',
        'cookie2',
        '_m_h5_tk',
        '_m_h5_tk_enc',
        't',
    )
    _OBSERVED_SESSION_COOKIE_FIELDS = (
        'cna',
    )
    _IDENTITY_VERIFY_PENDING_COOKIE_FIELDS = (
        'ivActionType',
        'tmp0',
        'siv20',
        'last_u_xianyu_web',
    )
    _X5_COOKIE_PREFIX = 'x5'


    def _find_direct_enter_candidate(self, page):
        """查找普通扫码/免密页上的“快速进入/继续/去登录”等直接进入按钮。"""
        if not page:
            return None, None, None

        # 优先检查 iframe。普通登录页的“快速进入”通常在 alibaba-login-box/mini_login iframe 内；
        # 主页面上的 text=登录 很容易被外层弹窗遮罩拦截，不能优先点击。
        search_frames = []
        try:
            for idx, frame in enumerate(page.frames):
                if frame == page.main_frame:
                    continue
                search_frames.append((f'Frame {idx}', frame))
        except Exception:
            pass
        search_frames.append(('主页面', page))

        candidates = []
        for frame_label, frame in search_frames:
            probe_info = self._probe_login_form_state(frame)
            if probe_info.get('probe_type') != 'direct_enter_like':
                continue
            selector = probe_info.get('matched_selector')
            if not selector:
                continue
            element, matched_selector = self._query_first_visible(frame, [selector])
            if not element:
                continue
            matched_text = probe_info.get('matched_text') or ''
            score = 0
            if frame_label != '主页面':
                score += 10
            if '快速进入' in matched_text:
                score += 20
            elif any(keyword in matched_text for keyword in ('进入', '继续', '去登录', '去看看')):
                score += 8
            candidates.append((score, frame, element, {
                'frame_label': frame_label,
                'matched_selector': matched_selector,
                'matched_text': matched_text or None,
            }))

        if not candidates:
            return None, None, None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, frame, element, probe_info = candidates[0]
        return frame, element, probe_info

    def _click_direct_enter_if_present(self, page, context=None) -> Tuple[bool, Any]:
        """普通登录页命中“快速进入”时先自动点击，再探测是否已登录。"""
        frame, element, probe_info = self._find_direct_enter_candidate(page)
        if not element:
            return False, None

        probe_text = probe_info.get('matched_text') if probe_info else None
        probe_note = f" [{probe_text}]" if probe_text else ""
        logger.info(
            f"【{self.pure_user_id}】检测到普通登录页直接进入按钮，自动点击: "
            f"{probe_info.get('frame_label') if probe_info else '未知位置'} "
            f"{probe_info.get('matched_selector') if probe_info else ''}{probe_note}"
        )
        try:
            try:
                element.click(timeout=5000)
            except TypeError:
                element.click()
        except Exception as click_e:
            logger.warning(f"【{self.pure_user_id}】点击普通登录页直接进入按钮失败，尝试JS点击兜底: {click_e}")
            try:
                element.evaluate("el => el.click()")
            except Exception as js_click_e:
                logger.warning(f"【{self.pure_user_id}】JS点击普通登录页直接进入按钮也失败: {js_click_e}")
                return False, None
        time.sleep(3)

        login_success = False
        active_page = page
        try:
            if context:
                login_success, active_page, _ = self._probe_context_login_success(context, page)
            else:
                login_success = self._check_login_success_by_element(page)
        except Exception as probe_e:
            logger.debug(f"【{self.pure_user_id}】点击快速进入后探测登录态失败: {probe_e}")

        if login_success:
            logger.success(f"【{self.pure_user_id}】✅ 点击快速进入后登录态已确认")
        else:
            logger.warning(f"【{self.pure_user_id}】点击快速进入后仍未确认登录态，继续后续验证/扫码流程")
        return True, active_page or page

    def _clear_page_storage_state(self, context=None, fallback_page=None) -> int:
        cleared_pages = 0
        for candidate in self._get_context_pages(context, fallback_page):
            try:
                candidate.evaluate(
                    "() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }"
                )
                cleared_pages += 1
            except Exception:
                continue
        return cleared_pages


    def _read_frame_text_for_detection(self, frame) -> str:
        """优先读取可见文本，避免把 HTML/CSS/JS 误判成验证文案。"""
        if not frame:
            return ''

        try:
            visible_text = frame.inner_text('body', timeout=1500)
            if visible_text:
                return str(visible_text)[:20000]
        except Exception:
            pass

        try:
            content_text = frame.text_content('body', timeout=1500)
            if content_text:
                return str(content_text)[:20000]
        except Exception:
            pass

        return ''


    def _cleanup_verification_screenshots(self):
        try:
            import glob

            screenshots_dir = 'static/uploads/images'
            all_screenshots = glob.glob(os.path.join(screenshots_dir, f'face_verify_{self.pure_user_id}_*.jpg'))
            all_screenshots += glob.glob(os.path.join(screenshots_dir, f'face_verify_{self.pure_user_id}_*.png'))
            for screenshot_file in all_screenshots:
                try:
                    if os.path.exists(screenshot_file):
                        os.remove(screenshot_file)
                        logger.info(f"【{self.pure_user_id}】✅ 已删除验证截图: {screenshot_file}")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】⚠️ 删除截图失败: {e}")
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】删除截图时出错: {e}")


    def _notify_verification_required(
        self,
        verification_type: str,
        frame_url: Optional[str],
        screenshot_path: Optional[str],
        notification_callback: Optional[Callable],
        notification_scene: str,
    ):
        if not notification_callback or not (screenshot_path or frame_url):
            if not notification_callback:
                logger.warning(f"【{self.pure_user_id}】⚠️ notification_callback 未提供，无法发送通知")
            else:
                logger.warning(f"【{self.pure_user_id}】无法获取验证信息，跳过通知发送")
            return

        dedup_key = (
            str(getattr(self, 'pure_user_id', self.user_id) or ''),
            str(verification_type or 'unknown'),
            str(frame_url or ''),
        )
        dedup_seconds = max(
            30,
            int(os.environ.get('XY_VERIFICATION_NOTIFY_DEDUP_SECONDS', self._verification_notification_dedup_seconds) or self._verification_notification_dedup_seconds),
        )
        now = time.time()
        with self._verification_notification_lock:
            # 顺手清理过期项，避免长期运行缓存增长。
            expired_keys = [
                key for key, sent_at in self._verification_notification_cache.items()
                if now - sent_at > dedup_seconds * 3
            ]
            for key in expired_keys:
                self._verification_notification_cache.pop(key, None)

            last_sent_at = self._verification_notification_cache.get(dedup_key)
            if last_sent_at and now - last_sent_at < dedup_seconds:
                logger.info(
                    f"【{self.pure_user_id}】同一验证入口通知在去重窗口内已发送，跳过重复通知: "
                    f"type={verification_type}, url={frame_url or 'N/A'}, remaining={dedup_seconds - int(now - last_sent_at)}s"
                )
                return
            self._verification_notification_cache[dedup_key] = now

        verification_type_titles = {
            'face_verify': f'⚠️ {notification_scene}需要人脸验证',
            'sms_verify': f'⚠️ {notification_scene}需要短信验证',
            'qr_verify': f'⚠️ {notification_scene}需要二维码验证',
            'login_page': f'⚠️ {notification_scene}需要扫码登录',
            'unknown': f'⚠️ {notification_scene}需要身份验证',
        }
        title = verification_type_titles.get(verification_type, f'⚠️ {notification_scene}需要身份验证')

        if screenshot_path:
            notification_msg = (
                f"{title}\n\n"
                f"账号: {self.pure_user_id}\n"
                f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"请登录自动化网站，访问账号管理模块，进行对应账号的验证。"
                f"在验证期间，自动回复功能暂时无法使用。"
            )
        else:
            notification_msg = (
                f"{title}\n\n"
                f"账号: {self.pure_user_id}\n"
                f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"请点击验证链接完成验证:\n{frame_url}\n\n"
                f"在验证期间，自动回复功能暂时无法使用。"
            )

        try:
            logger.info(f"【{self.pure_user_id}】准备发送验证通知，截图路径: {screenshot_path}, URL: {frame_url}")
            import inspect

            if inspect.iscoroutinefunction(notification_callback):
                def run_async_callback():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        try:
                            loop.run_until_complete(
                                notification_callback(
                                    notification_msg,
                                    screenshot_path,
                                    frame_url,
                                    verification_type=verification_type,
                                )
                            )
                        except TypeError:
                            loop.run_until_complete(notification_callback(notification_msg, screenshot_path, frame_url))
                        logger.info(f"【{self.pure_user_id}】✅ 异步通知回调已执行")
                    except Exception as async_err:
                        logger.error(f"【{self.pure_user_id}】异步通知回调执行失败: {async_err}")
                        import traceback
                        logger.error(traceback.format_exc())
                    finally:
                        loop.close()

                thread = threading.Thread(target=run_async_callback, daemon=True)
                thread.start()
                logger.info(f"【{self.pure_user_id}】异步通知线程已启动")
            else:
                try:
                    notification_callback(
                        notification_msg,
                        None,
                        frame_url,
                        screenshot_path,
                        verification_type=verification_type,
                    )
                except TypeError:
                    notification_callback(notification_msg, None, frame_url, screenshot_path)
                logger.info(f"【{self.pure_user_id}】✅ 同步通知回调已执行")
        except Exception as notify_err:
            logger.error(f"【{self.pure_user_id}】发送验证通知失败: {notify_err}")
            import traceback
            logger.error(traceback.format_exc())

    def _process_verification_requirement(
        self,
        context,
        fallback_page,
        qr_frame,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ):
        verification_type = 'unknown'
        if qr_frame and hasattr(qr_frame, 'verification_type'):
            verification_type = qr_frame.verification_type

        verification_type_names = {
            'face_verify': '人脸验证',
            'sms_verify': '短信验证',
            'qr_verify': '二维码验证',
            'login_page': '扫码登录',
            'unknown': '身份验证',
        }
        type_name = verification_type_names.get(verification_type, '身份验证')

        frame_url = None
        screenshot_path = None
        if qr_frame:
            try:
                if hasattr(qr_frame, 'verify_url') and qr_frame.verify_url:
                    frame_url = qr_frame.verify_url
                else:
                    frame_url = qr_frame.url if hasattr(qr_frame, 'url') else None

                if hasattr(qr_frame, 'screenshot_path') and qr_frame.screenshot_path:
                    screenshot_path = qr_frame.screenshot_path
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】获取验证信息失败: {e}")

        if self._verification_target_is_timed_out(qr_frame, fallback_page=fallback_page):
            recovered_frame = self._recover_timed_out_verification_page(
                qr_frame,
                fallback_page=fallback_page,
            )
            if recovered_frame:
                qr_frame = recovered_frame
                verification_type = getattr(qr_frame, 'verification_type', None) or verification_type
                type_name = verification_type_names.get(verification_type, '身份验证')
                frame_url = getattr(qr_frame, 'verify_url', None)
                if not frame_url and hasattr(qr_frame, 'url'):
                    frame_url = qr_frame.url
                screenshot_path = getattr(qr_frame, 'screenshot_path', None)
                logger.info(f"【{self.pure_user_id}】已将超时验证页恢复为新的{type_name}入口")
            else:
                timeout_message = self._build_timed_out_verification_message(verification_type)
                logger.warning(f"【{self.pure_user_id}】{timeout_message}")
                return self._fail_login(timeout_message)

        logger.warning(f"【{self.pure_user_id}】⚠️ 检测到{type_name}")
        logger.info(f"【{self.pure_user_id}】请在浏览器中完成{type_name}")

        if screenshot_path:
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
            logger.warning(f"【{self.pure_user_id}】二维码/人脸验证截图:")
            logger.warning(f"【{self.pure_user_id}】{screenshot_path}")
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
        elif frame_url:
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
            logger.warning(f"【{self.pure_user_id}】二维码/人脸验证链接:")
            logger.warning(f"【{self.pure_user_id}】{frame_url}")
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
        else:
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
            logger.warning(f"【{self.pure_user_id}】二维码/人脸验证已检测到，但无法获取验证信息")
            logger.warning(f"【{self.pure_user_id}】请在浏览器中查看验证页面")
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")

        self._notify_verification_required(
            verification_type,
            frame_url,
            screenshot_path,
            notification_callback,
            notification_scene,
        )

        wait_timeout = max(5, int(getattr(self, 'verification_wait_timeout', 450) or 450))
        logger.info(f"【{self.pure_user_id}】等待二维码/人脸验证完成... (timeout={wait_timeout}s)")
        login_success = False
        success_page = fallback_page
        try:
            login_success, success_page = self._wait_for_context_login(
                context,
                fallback_page,
                max_wait_time=wait_timeout,
                check_interval=10,
                verification_type=verification_type,
                verification_url=frame_url,
                verification_screenshot_path=screenshot_path,
                notification_callback=notification_callback,
                notification_scene=notification_scene,
            )
        finally:
            if screenshot_path:
                logger.info(
                    f"【{self.pure_user_id}】验证流程结束后暂不自动删除验证截图，"
                    f"改由会话过期或手动清理: {screenshot_path}"
                )
            elif self.keep_verification_screenshots:
                logger.info(f"【{self.pure_user_id}】保留验证截图供后续调试")

        if not login_success:
            if self.last_login_error and '已超时/失效，请重新发起验证' in self.last_login_error:
                logger.error(f"【{self.pure_user_id}】❌ {self.last_login_error}")
                return None
            logger.error(f"【{self.pure_user_id}】❌ 等待验证超时（{wait_timeout}秒）")
            return self._fail_login(f"等待{type_name}超时（{wait_timeout}秒）")

        logger.success(f"【{self.pure_user_id}】✅ 验证成功，登录状态已确认！")
        return self._finalize_logged_in_cookies(
            context,
            success_page or fallback_page,
            scene=f"{type_name}验证完成",
            notification_callback=notification_callback,
            notification_scene=notification_scene,
        )


    def _build_client_hint_profile(self, browser_features: Dict[str, Any]) -> Dict[str, Any]:
        user_agent = str(browser_features.get("user_agent") or "")
        version_match = re.search(r"Chrome/(\d+(?:\.\d+){0,3})", user_agent)
        full_version = version_match.group(1) if version_match else "118.0.0.0"
        major_version = full_version.split(".", 1)[0]

        browser_family = self._get_browser_family()
        brands = [{"brand": "Not.A/Brand", "version": "8"}]
        full_version_list = [{"brand": "Not.A/Brand", "version": "8.0.0.0"}]
        sec_ch_ua_parts = ['"Not.A/Brand";v="8"']

        if browser_family == "edge":
            brands.extend([
                {"brand": "Chromium", "version": major_version},
                {"brand": "Microsoft Edge", "version": major_version},
            ])
            full_version_list.extend([
                {"brand": "Chromium", "version": full_version},
                {"brand": "Microsoft Edge", "version": full_version},
            ])
            sec_ch_ua_parts.extend([
                f'"Chromium";v="{major_version}"',
                f'"Microsoft Edge";v="{major_version}"',
            ])
        else:
            brands.extend([
                {"brand": "Chromium", "version": major_version},
                {"brand": "Google Chrome", "version": major_version},
            ])
            full_version_list.extend([
                {"brand": "Chromium", "version": full_version},
                {"brand": "Google Chrome", "version": full_version},
            ])
            sec_ch_ua_parts.extend([
                f'"Chromium";v="{major_version}"',
                f'"Google Chrome";v="{major_version}"',
            ])

        sec_ch_ua = ", ".join(sec_ch_ua_parts)

        return {
            "userAgent": user_agent,
            "fullVersion": full_version,
            "majorVersion": major_version,
            "brands": brands,
            "fullVersionList": full_version_list,
            "secChUa": sec_ch_ua,
            "secChUaMobile": "?1" if browser_features.get("is_mobile") else "?0",
            "secChUaPlatform": f'"{browser_features.get("platform") or "Windows"}"',
            "platform": browser_features.get("platform") or "Windows",
            "platformVersion": "10.0.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": bool(browser_features.get("is_mobile")),
            "model": "",
            "wow64": False,
        }

    def _build_headless_extra_headers(self, browser_features: Dict[str, Any]) -> Dict[str, str]:
        hints = self._build_client_hint_profile(browser_features)
        return {
            "sec-ch-ua": hints["secChUa"],
            "sec-ch-ua-mobile": hints["secChUaMobile"],
            "sec-ch-ua-platform": hints["secChUaPlatform"],
        }

    def _apply_headless_network_fingerprint(self, page, browser_features: Dict[str, Any]):
        if not self.headless or not self.context or not page:
            return

        try:
            hints = self._build_client_hint_profile(browser_features)
            session = self.context.new_cdp_session(page)
            session.send("Network.enable")
            session.send(
                "Network.setUserAgentOverride",
                {
                    "userAgent": hints["userAgent"],
                    "acceptLanguage": browser_features.get("accept_lang") or "zh-CN,zh;q=0.9",
                    "platform": hints["platform"],
                    "userAgentMetadata": {
                        "brands": hints["brands"],
                        "fullVersionList": hints["fullVersionList"],
                        "fullVersion": hints["fullVersion"],
                        "platform": hints["platform"],
                        "platformVersion": hints["platformVersion"],
                        "architecture": hints["architecture"],
                        "bitness": hints["bitness"],
                        "model": hints["model"],
                        "mobile": hints["mobile"],
                        "wow64": hints["wow64"],
                    },
                },
            )
            logger.info(f"【{self.pure_user_id}】已应用无头浏览器 UA/Client-Hints 网络层伪装")
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】应用无头网络层指纹伪装失败: {e}")


    def simulate_slide(self, slider_button: ElementHandle, trajectory):
        """模拟滑动 - 优化版本（增强随机性+智能学习）"""
        try:
            # 🧠 获取学习到的行为参数
            reference_distance = ((getattr(self, 'current_trajectory_data', {}) or {}).get("distance"))
            optimized_params = self._optimize_trajectory_params(reference_distance=reference_distance)
            learned_behavior = optimized_params.get("learned_behavior", {})
            is_learned = optimized_params.get("learning_enabled", False) and len(learned_behavior) > 0

            if is_learned:
                logger.info(f"【{self.pure_user_id}】🧠 应用学习到的滑动行为参数（{len(learned_behavior)}个）")
            else:
                logger.info(f"【{self.pure_user_id}】开始优化滑动模拟...")

            current_profile = str(
                ((getattr(self, "current_trajectory_data", {}) or {}).get("random_params", {}) or {}).get("profile", "")
            )
            stable_headless_profile = current_profile == "cold_start_headless_stable"

            # 🎭 用户速度人格因子：模拟同一个人各阶段行为的一致性
            # 快用户 (0.75~0.95) 各阶段等待都偏短，慢用户 (1.05~1.25) 各阶段等待都偏长
            # 使用 Perlin 噪声使各阶段因子有连续相关性，而非完全相同
            _tempo_seed = random.uniform(0, 1000)
            _tempo_base = random.uniform(0.92, 1.10) if stable_headless_profile else random.uniform(0.80, 1.20)
            def _tempo(phase_idx):
                """为第 phase_idx 个阶段生成连续相关的速度因子"""
                noise_val = perlin_noise_1d(phase_idx * 0.8, seed_offset=_tempo_seed)
                return max(0.65, min(1.40, _tempo_base + noise_val * 0.15))
            logger.debug(f"【{self.pure_user_id}】用户速度人格: base={_tempo_base:.2f}")

            # 🎲 随机1：页面稳定等待时间随机化
            # 🔧 优化：根据成功案例，总耗时约0.9-1.55秒，页面等待不宜过长
            page_wait_range = (0.12, 0.24) if stable_headless_profile else (0.08, 0.25)
            page_wait = random.uniform(*page_wait_range) * _tempo(0)
            time.sleep(page_wait)
            
            # 获取滑块按钮中心位置
            button_box = slider_button.bounding_box()
            if not button_box:
                logger.error(f"【{self.pure_user_id}】无法获取滑块按钮位置")
                return False
            
            start_x = button_box["x"] + button_box["width"] / 2
            start_y = button_box["y"] + button_box["height"] / 2
            logger.debug(f"【{self.pure_user_id}】滑块位置: ({start_x}, {start_y})")
            
            # 记录滑动行为参数（用于学习）
            slide_behavior = {}
            
            # 第一阶段：移动到滑块附近（模拟人类寻找滑块）
            # 🔧 优化说明：根据成功案例，接近偏移集中在 X:-9到-22, Y:-2到-18
            try:
                # 🎲 随机2：偏移量随机化（应用学习结果）
                if "approach_offset_x" in learned_behavior:
                    x_range = learned_behavior["approach_offset_x"]
                    offset_x = random.uniform(x_range[0], x_range[1])
                    logger.debug(f"【{self.pure_user_id}】🧠 使用学习的X偏移: {x_range[0]:.1f}~{x_range[1]:.1f}")
                else:
                    # 🔧 修复：成功记录显示X偏移约-23到-24
                    offset_x = random.uniform(-25, -20)
                
                if "approach_offset_y" in learned_behavior:
                    y_range = learned_behavior["approach_offset_y"]
                    offset_y = random.uniform(y_range[0], y_range[1])
                else:
                    # 🔧 修复：成功记录显示Y偏移应为正值（+12到+18）
                    offset_y = random.uniform(12, 18)
                
                slide_behavior['approach_offset_x'] = offset_x
                slide_behavior['approach_offset_y'] = offset_y
                
                # 🎲 随机3：接近步数随机化（应用学习结果）
                # 🔧 优化：成功案例的接近步数集中在 3-12步，但以3-6步居多
                if "approach_steps" in learned_behavior:
                    steps_range = learned_behavior["approach_steps"]
                    approach_steps = random.randint(steps_range[0], steps_range[1])
                    logger.debug(f"【{self.pure_user_id}】🧠 使用学习的接近步数: {steps_range[0]}~{steps_range[1]}")
                else:
                    # 🔧 修复：成功记录显示接近步数约8-9步
                    approach_steps = random.randint(8, 10)
                
                slide_behavior['approach_steps'] = approach_steps
                
                self.page.mouse.move(
                    start_x + offset_x,
                    start_y + offset_y,
                    steps=approach_steps
                )
                
                # 🎲 随机4：接近后停顿随机化（应用学习结果）
                # 🔧 优化：成功案例的接近停顿集中在 0.17-0.36秒
                if "approach_pause" in learned_behavior:
                    pause_range = learned_behavior["approach_pause"]
                    approach_pause = random.uniform(pause_range[0], pause_range[1])
                else:
                    # 🔧 修复：成功记录显示接近停顿约0.05-0.12秒（更短）
                    approach_pause = random.uniform(0.05, 0.15)
                
                slide_behavior['approach_pause'] = approach_pause
                time.sleep(approach_pause * _tempo(1))
                
                # 🎲 随机5：精确定位步数随机化（应用学习结果）
                # 🔧 优化：成功案例的精确定位步数集中在 3-8步
                if "precision_steps" in learned_behavior:
                    steps_range = learned_behavior["precision_steps"]
                    precision_steps = random.randint(steps_range[0], steps_range[1])
                else:
                    # 🔧 修复：成功记录显示精确定位步数约9-10步
                    precision_steps = random.randint(8, 10)
                
                slide_behavior['precision_steps'] = precision_steps
                
                self.page.mouse.move(
                    start_x,
                    start_y,
                    steps=precision_steps
                )
                
                # 🎲 随机6：定位后停顿随机化（应用学习结果）
                # 🔧 优化：成功案例的定位停顿集中在 0.19-0.28秒
                if "precision_pause" in learned_behavior:
                    pause_range = learned_behavior["precision_pause"]
                    precision_pause = random.uniform(pause_range[0], pause_range[1])
                else:
                    # 🔧 修复：成功记录显示精确定位停顿约0.07-0.09秒（更短）
                    precision_pause = random.uniform(0.07, 0.12)
                
                slide_behavior['precision_pause'] = precision_pause
                time.sleep(precision_pause * _tempo(2))
                
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】移动到滑块失败: {e}，继续尝试")
            
            # 第二阶段：悬停在滑块上
            # 🎲 随机7：跳过悬停概率（应用学习结果）
            # 🔧 优化：成功案例中大多数跳过了悬停（skip_hover=true居多）
            if "skip_hover_rate" in learned_behavior:
                skip_hover = random.random() < learned_behavior["skip_hover_rate"]
                logger.debug(f"【{self.pure_user_id}】🧠 使用学习的跳过悬停概率: {learned_behavior['skip_hover_rate']*100:.1f}%")
            else:
                # 🔧 修复：成功记录显示skip_hover=false，降低跳过率到15%
                skip_hover = False if stable_headless_profile else (random.random() < 0.15)
            
            slide_behavior['skip_hover'] = skip_hover
            
            if not skip_hover:
                try:
                    slider_button.hover(timeout=2000)
                    # 🎲 随机8：悬停时间随机化（应用学习结果）
                    if "hover_pause" in learned_behavior:
                        pause_range = learned_behavior["hover_pause"]
                        hover_pause = random.uniform(pause_range[0], pause_range[1])
                    else:
                        hover_pause = random.uniform(0.08, 0.33) if stable_headless_profile else random.uniform(0.05, 0.4)
                    
                    slide_behavior['hover_pause'] = hover_pause
                    time.sleep(hover_pause * _tempo(3))
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】悬停滑块失败: {e}")
            else:
                logger.debug(f"【{self.pure_user_id}】跳过悬停（随机行为）")
            
            # 第三阶段：按下鼠标
            try:
                self.page.mouse.move(start_x, start_y)
                
                # 🎲 随机9：按下前停顿随机化（应用学习结果）
                # 🔧 优化：成功案例的按下前停顿集中在 0.08-0.17秒
                if "pre_down_pause" in learned_behavior:
                    pause_range = learned_behavior["pre_down_pause"]
                    pre_down_pause = random.uniform(pause_range[0], pause_range[1])
                else:
                    # 🔧 修复：成功记录显示按下前停顿约0.12-0.14秒
                    pre_down_pause = random.uniform(0.10, 0.15)
                
                slide_behavior['pre_down_pause'] = pre_down_pause
                time.sleep(pre_down_pause * _tempo(4))
                
                self.page.mouse.down()
                
                # 🎲 随机10：按下后停顿随机化（应用学习结果）
                # 🔧 优化：成功案例的按下后停顿集中在 0.04-0.09秒
                if "post_down_pause" in learned_behavior:
                    pause_range = learned_behavior["post_down_pause"]
                    post_down_pause = random.uniform(pause_range[0], pause_range[1])
                else:
                    # 🔧 修复：成功记录显示按下后停顿约0.12-0.14秒
                    post_down_pause = random.uniform(0.10, 0.15)
                
                slide_behavior['post_down_pause'] = post_down_pause
                time.sleep(post_down_pause * _tempo(5))
                
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】按下鼠标失败: {e}")
                return False
            
            # 第四阶段：执行滑动轨迹
            try:
                start_time = time.time()
                current_x = start_x
                current_y = start_y
                
                # 🔧 2025-12-25 重构：不使用 Playwright 的 steps 参数
                # steps 会生成均匀插值点，这不是人类行为
                # 直接移动到每个轨迹点，轨迹本身已经包含足够的采样点
                
                # 🎲 延迟波动范围随机化
                delay_variation_min = random.uniform(0.85, 0.95)
                delay_variation_max = random.uniform(1.05, 1.15)
                slide_behavior['delay_variation'] = (delay_variation_min, delay_variation_max)
                
                # 记录上一个位置，用于检测大跳跃
                last_x, last_y = 0, 0
                
                # 执行拖动轨迹 - 直接移动到每个点
                for i, (x, y, delay) in enumerate(trajectory):
                    # 更新当前位置
                    current_x = start_x + x
                    current_y = start_y + y
                    
                    # 🔧 关键改进：直接移动到目标点，不使用 steps 插值
                    # 如果位移过大（>30px），分多次小步移动以更自然
                    dx = x - last_x
                    dy = y - last_y
                    move_distance = math.sqrt(dx*dx + dy*dy)
                    
                    if move_distance > 30:
                        # 大位移时，分成多个小步
                        sub_steps = max(2, int(move_distance / 15))
                        for j in range(sub_steps):
                            progress = (j + 1) / sub_steps
                            sub_x = start_x + last_x + dx * progress
                            sub_y = start_y + last_y + dy * progress
                            self.page.mouse.move(sub_x, sub_y)
                            # 小步之间只有极短延迟
                            time.sleep(random.uniform(0.001, 0.003))
                    else:
                        # 小位移直接移动
                        self.page.mouse.move(current_x, current_y)
                    
                    last_x, last_y = x, y
                    
                    # 🎲 延迟使用自定义波动范围
                    actual_delay = delay * random.uniform(delay_variation_min, delay_variation_max)
                    
                    # 🎲 随机：8%概率在非首尾点增加额外停顿（模拟人类调整）
                    if 0.15 < (i / len(trajectory)) < 0.85 and random.random() < 0.08:
                        hesitation = random.uniform(0.01, 0.04)
                        actual_delay += hesitation
                        slide_behavior[f'hesitation_at_{i}'] = hesitation
                    
                    time.sleep(actual_delay)
                    
                    # 记录最终位置
                    if i == len(trajectory) - 1:
                        try:
                            current_style = slider_button.get_attribute("style")
                            if current_style and "left:" in current_style:
                                import re
                                left_match = re.search(r'left:\s*([^;]+)', current_style)
                                if left_match:
                                    left_value = left_match.group(1).strip()
                                    left_px = float(left_value.replace('px', ''))
                                    if hasattr(self, 'current_trajectory_data'):
                                        self.current_trajectory_data["final_left_px"] = left_px
                                    logger.info(f"【{self.pure_user_id}】滑动完成: {len(trajectory)}步 - 最终位置: {left_value}")
                        except:
                            pass
                
                # 🎨 刮刮乐特殊处理：在目标位置停顿观察
                is_scratch = self.is_scratch_captcha()
                if is_scratch:
                    # 🎲 随机16：刮刮乐停顿时间随机化（0.2-0.6秒）
                    pause_duration = random.uniform(0.2, 0.6)
                    slide_behavior['scratch_pause'] = pause_duration
                    logger.warning(f"【{self.pure_user_id}】🎨 刮刮乐模式：在目标位置停顿{pause_duration:.2f}秒观察...")
                    time.sleep(pause_duration)
                
                # 🎲 随机17：释放前停顿随机化
                # 🔧 优化：成功案例的释放前停顿集中在 0.01-0.07秒
                pre_up_pause = random.uniform(0.01, 0.07)  # 优化：原0.01-0.08
                slide_behavior['pre_up_pause'] = pre_up_pause
                time.sleep(pre_up_pause * _tempo(6))
                
                # 释放鼠标
                self.page.mouse.up()

                # 释放后短暂停顿（模拟手指离开）
                post_up_pause = random.uniform(0.02, 0.06)
                slide_behavior['post_up_pause'] = post_up_pause
                time.sleep(post_up_pause * _tempo(7))

                # 等待服务端验证判定（关键：阿里滑块验证是异步的，需要给服务端足够时间返回结果）
                if "server_judge_wait" in learned_behavior:
                    wait_range = learned_behavior["server_judge_wait"]
                    server_wait_range = (
                        max(0.8, float(wait_range[0])),
                        max(float(wait_range[0]) + 0.1, float(wait_range[1])),
                    )
                    server_wait_tempo = max(1.0, min(1.2, _tempo(8)))
                elif getattr(self, "risk_trigger_scene", None) == "token_refresh":
                    server_wait_range = (2.2, 4.2) if stable_headless_profile else (2.0, 3.6)
                    server_wait_tempo = max(1.0, min(1.2, _tempo(8)))
                else:
                    server_wait_range = (1.25, 2.10) if stable_headless_profile else (1.0, 2.0)
                    server_wait_tempo = _tempo(8)
                server_judge_wait = random.uniform(*server_wait_range) * server_wait_tempo
                slide_behavior['server_judge_wait'] = server_judge_wait
                logger.debug(f"【{self.pure_user_id}】等待服务端判定: {server_judge_wait:.2f}秒")
                time.sleep(server_judge_wait)

                elapsed_time = time.time() - start_time
                slide_behavior['total_elapsed_time'] = elapsed_time
                slide_behavior['used_learned_params'] = is_learned  # 标记是否使用了学习参数
                
                # 💾 保存滑动行为参数到轨迹数据（用于成功后学习）
                if hasattr(self, 'current_trajectory_data'):
                    self.current_trajectory_data['slide_behavior'] = slide_behavior
                    logger.debug(f"【{self.pure_user_id}】已记录{len(slide_behavior)}个滑动行为参数")
                
                learn_status = "🧠智能学习模式" if is_learned else "🎲随机模式"
                logger.info(f"【{self.pure_user_id}】滑动完成 [{learn_status}]: "
                           f"耗时={elapsed_time:.2f}秒, "
                           f"最终位置=({current_x:.1f}, {current_y:.1f}), "
                           f"行为参数={len(slide_behavior)}个")
                
                return True
                
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】执行滑动轨迹失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                # 确保释放鼠标
                try:
                    self.page.mouse.up()
                except:
                    pass
                return False
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】滑动模拟异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _simulate_human_page_behavior(self):
        """在验证码页先停留一会儿，再做轻微交互，别一上来就莽。"""
        if not self.page:
            return

        try:
            entry_ts = getattr(self, "_captcha_page_entry_ts", None)
            target_dwell = random.uniform(2.8, 4.2)
            if entry_ts:
                elapsed = time.time() - entry_ts
                if elapsed < target_dwell:
                    wait_time = target_dwell - elapsed
                    logger.info(f"【{self.pure_user_id}】验证码页预停留 {wait_time:.2f} 秒，等页面和风控脚本稳定")
                    time.sleep(wait_time)

            width = int(self.browser_features.get("viewport_width") or 1600)
            height = int(self.browser_features.get("viewport_height") or 900)
            move_count = random.randint(2, 4)
            for _ in range(move_count):
                target_x = random.randint(max(140, width // 5), max(260, width - width // 5))
                target_y = random.randint(max(180, height // 4), max(260, height - height // 4))
                self.page.mouse.move(target_x, target_y, steps=random.randint(10, 24))
                time.sleep(random.uniform(0.08, 0.22))

            if random.random() < 0.35:
                self.page.mouse.wheel(0, random.randint(50, 160))
                time.sleep(random.uniform(0.05, 0.15))
                if random.random() < 0.5:
                    self.page.mouse.wheel(0, -random.randint(30, 90))
                    time.sleep(random.uniform(0.05, 0.12))

            settle_time = random.uniform(0.35, 0.8)
            logger.info(f"【{self.pure_user_id}】验证码页行为预热完成，额外静置 {settle_time:.2f} 秒")
            time.sleep(settle_time)
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】验证码页行为预热失败，继续尝试滑块: {e}")


    def find_slider_elements(self, fast_mode=False):
        """查找滑块元素（支持在主页面和所有frame中查找）
        
        Args:
            fast_mode: 快速模式，不使用wait_for_selector，减少等待时间（当已确认滑块存在时使用）
        """
        try:
            # 快速等待页面稳定（快速模式下跳过）
            if not fast_mode:
                time.sleep(0.1)

            current_block = self._detect_special_captcha_block(self.page)
            current_block = self._wait_for_punish_slider_dom_ready_if_needed(
                self.page,
                current_block,
                "主页面滑块探测",
            )
            current_block = self._recover_punish_slider_shell_if_possible(
                self.page,
                current_block,
                "主页面滑块探测",
            )
            if current_block:
                logger.error(
                    f"【{self.pure_user_id}】当前页面命中高风险验证码页[{current_block['kind']}]: "
                    f"{current_block['message']}"
                )
                self.last_verification_feedback = {
                    "status": "hard_block",
                    "source": current_block["kind"],
                    "message": current_block["message"],
                    "url": current_block.get("url") or "",
                    "title": current_block.get("title") or "",
                }
                self._save_debug_snapshot("hard_block_page", self.page)
                return None, None, None
            
            # ===== 【优化】优先在 frames 中快速查找最常见的滑块组合 =====
            # 根据实际日志，滑块按钮和轨道通常在同一个 frame 中
            # 按钮: #nc_1_n1z, 轨道: #nc_1_n1t
            logger.debug(f"【{self.pure_user_id}】优先在frames中快速查找常见滑块组合...")
            try:
                frames = self.page.frames
                for idx, frame in enumerate(frames):
                    try:
                        frame_block = self._detect_special_captcha_block(frame)
                        frame_block = self._wait_for_punish_slider_dom_ready_if_needed(
                            frame,
                            frame_block,
                            f"Frame {idx} 滑块探测",
                        )
                        frame_block = self._recover_punish_slider_shell_if_possible(
                            frame,
                            frame_block,
                            f"Frame {idx} 滑块探测",
                        )
                        if frame_block:
                            logger.error(
                                f"【{self.pure_user_id}】Frame {idx} 命中高风险验证码页[{frame_block['kind']}]: "
                                f"{frame_block['message']}"
                            )
                            self._detected_slider_frame = frame
                            self.last_verification_feedback = {
                                "status": "hard_block",
                                "source": frame_block["kind"],
                                "message": frame_block["message"],
                                "url": frame_block.get("url") or "",
                                "title": frame_block.get("title") or "",
                                "frame_index": idx,
                            }
                            self._save_debug_snapshot("hard_block_page", frame)
                            return None, None, None

                        # 优先查找最常见的按钮选择器
                        button_element = frame.query_selector("#nc_1_n1z")
                        if button_element and button_element.is_visible():
                            # 在同一个 frame 中查找轨道
                            track_element = frame.query_selector("#nc_1_n1t")
                            if track_element and track_element.is_visible():
                                # 找到容器（可以用按钮或其他选择器）
                                container_element = frame.query_selector("#baxia-dialog-content")
                                if not container_element:
                                    container_element = frame.query_selector(".nc-container")
                                if not container_element:
                                    # 如果找不到容器，用按钮作为容器标识
                                    container_element = button_element
                                
                                logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 快速找到完整滑块组合！")
                                logger.info(f"【{self.pure_user_id}】  - 按钮: #nc_1_n1z")
                                logger.info(f"【{self.pure_user_id}】  - 轨道: #nc_1_n1t")
                                
                                # 保存frame引用
                                self._detected_slider_frame = frame
                                return container_element, button_element, track_element
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】Frame {idx} 快速查找失败: {e}")
                        continue
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】frames 快速查找出错: {e}")
            
            # ===== 如果快速查找失败，使用原来的完整查找逻辑 =====
            logger.debug(f"【{self.pure_user_id}】快速查找未成功，使用完整查找逻辑...")
            
            # 定义滑块容器选择器（支持多种类型）
            container_selectors = [
                "#nc_1_n1z",  # 滑块按钮也可以作为容器标识
                "#baxia-dialog-content",
                ".nc-container",
                ".nc_wrapper",
                ".nc_scale",
                "[class*='nc-container']",
                # 刮刮乐类型滑块
                "#nocaptcha",
                ".nc_1_nocaptcha",
                ".sm-pop-inner.nc-container",
                ".sm-btn-wrapper",
                ".scratch-captcha-container",
                ".scratch-captcha-question-bg",
                # 通用选择器
                "[class*='slider']",
                "[class*='btn_slide']"
            ]
            
            # 查找滑块容器
            slider_container = None
            found_frame = None
            
            # 🔑 优化：如果是重试且之前在"已知位置"查找失败，跳过已知位置，直接全局搜索
            skip_known_location = False
            if hasattr(self, '_slider_search_failed_in_known_location') and self._slider_search_failed_in_known_location:
                logger.warning(f"【{self.pure_user_id}】上次在已知位置查找失败，本次跳过已知位置，直接全局搜索")
                skip_known_location = True
                # 清除标记，避免影响下次验证
                self._slider_search_failed_in_known_location = False
            
            # 如果检测时已经知道滑块在哪个frame中，直接在该frame中查找
            if not skip_known_location and hasattr(self, '_detected_slider_frame'):
                if self._detected_slider_frame is not None:
                    # 在已知的frame中查找
                    logger.info(f"【{self.pure_user_id}】已知滑块在frame中，直接在frame中查找...")
                    target_frame = self._detected_slider_frame
                    for selector in container_selectors:
                        try:
                            element = target_frame.query_selector(selector)
                            if element:
                                try:
                                    if element.is_visible():
                                        logger.info(f"【{self.pure_user_id}】在已知Frame中找到滑块容器: {selector}")
                                        slider_container = element
                                        found_frame = target_frame
                                        break
                                except:
                                    # 如果无法检查可见性，也尝试使用
                                    logger.info(f"【{self.pure_user_id}】在已知Frame中找到滑块容器（无法检查可见性）: {selector}")
                                    slider_container = element
                                    found_frame = target_frame
                                    break
                        except Exception as e:
                            logger.debug(f"【{self.pure_user_id}】已知Frame选择器 {selector} 未找到: {e}")
                            continue
                else:
                    # _detected_slider_frame 是 None，表示在主页面
                    logger.info(f"【{self.pure_user_id}】已知滑块在主页面，直接在主页面查找...")
                    for selector in container_selectors:
                        try:
                            element = self.page.wait_for_selector(selector, timeout=2000)  # 增加超时时间
                            if element:
                                logger.info(f"【{self.pure_user_id}】在已知主页面找到滑块容器: {selector}")
                                slider_container = element
                                found_frame = self.page
                                break
                        except Exception as e:
                            logger.debug(f"【{self.pure_user_id}】主页面选择器 {selector} 未找到: {e}")
                            continue
            
            # 如果已知位置中没找到，或者没有已知位置，先尝试在主页面查找
            if not slider_container:
                for selector in container_selectors:
                    try:
                        element = self.page.wait_for_selector(selector, timeout=1000)  # 减少超时时间，快速跳过
                        if element:
                            logger.info(f"【{self.pure_user_id}】在主页面找到滑块容器: {selector}")
                            slider_container = element
                            found_frame = self.page
                            break
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】主页面选择器 {selector} 未找到: {e}")
                        continue
            
            # 如果主页面没找到，在所有frame中查找
            if not slider_container and self.page:
                try:
                    frames = self.page.frames
                    logger.info(f"【{self.pure_user_id}】主页面未找到滑块，开始在所有frame中查找（共{len(frames)}个frame）...")
                    for idx, frame in enumerate(frames):
                        try:
                            for selector in container_selectors:
                                try:
                                    # 在frame中使用query_selector，因为frame可能不支持wait_for_selector
                                    element = frame.query_selector(selector)
                                    if element:
                                        # 检查元素是否可见
                                        try:
                                            if element.is_visible():
                                                logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块容器: {selector}")
                                                slider_container = element
                                                found_frame = frame
                                                break
                                        except:
                                            # 如果无法检查可见性，也尝试使用
                                            logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块容器（无法检查可见性）: {selector}")
                                            slider_container = element
                                            found_frame = frame
                                            break
                                except Exception as e:
                                    logger.debug(f"【{self.pure_user_id}】Frame {idx} 选择器 {selector} 未找到: {e}")
                                    continue
                            if slider_container:
                                break
                        except Exception as e:
                            logger.debug(f"【{self.pure_user_id}】检查Frame {idx} 时出错: {e}")
                            continue
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】获取frame列表时出错: {e}")
            
            if not slider_container:
                logger.error(f"【{self.pure_user_id}】未找到任何滑块容器（主页面和所有frame都已检查）")
                return None, None, None
            
            # 定义滑块按钮选择器（支持多种类型）
            button_selectors = [
                # nc 系列滑块
                "#nc_1_n1z",
                ".nc_iconfont",
                ".btn_slide",
                # 刮刮乐类型滑块
                "#scratch-captcha-btn",
                ".scratch-captcha-slider .button",
                # 通用选择器
                "[class*='slider']",
                "[class*='btn']",
                "[role='button']"
            ]
            
            # 查找滑块按钮（在找到容器的同一个frame中查找）
            slider_button = None
            search_frame = found_frame if found_frame and found_frame != self.page else self.page
            
            # 如果容器是在主页面找到的，按钮也应该在主页面查找
            # 如果容器是在frame中找到的，按钮也应该在同一个frame中查找
            for selector in button_selectors:
                try:
                    element = None
                    if fast_mode:
                        # 快速模式：直接使用 query_selector，不等待
                        element = search_frame.query_selector(selector)
                    else:
                        # 正常模式：使用 wait_for_selector
                        if search_frame == self.page:
                            element = self.page.wait_for_selector(selector, timeout=3000)
                        else:
                            # 在frame中先尝试wait_for_selector（如果支持）
                            try:
                                # 尝试使用wait_for_selector（Playwright的frame支持）
                                element = search_frame.wait_for_selector(selector, timeout=3000)
                            except:
                                # 如果不支持wait_for_selector，使用query_selector并等待
                                time.sleep(0.5)  # 等待元素加载
                                element = search_frame.query_selector(selector)
                    
                    if element:
                        # 检查元素是否可见，但不要因为不可见就放弃
                        try:
                            is_visible = element.is_visible()
                            if not is_visible:
                                logger.debug(f"【{self.pure_user_id}】找到元素但不可见: {selector}，继续尝试其他选择器")
                                element = None
                        except Exception as vis_e:
                            # 如果无法检查可见性，仍然使用该元素
                            logger.debug(f"【{self.pure_user_id}】无法检查元素可见性: {vis_e}，继续使用该元素")
                            pass
                    
                    if element:
                        frame_info = "主页面" if search_frame == self.page else f"Frame"
                        logger.info(f"【{self.pure_user_id}】在{frame_info}找到滑块按钮: {selector}")
                        slider_button = element
                        break
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】选择器 {selector} 未找到: {e}")
                    continue

            if not slider_button and slider_container:
                if self._try_reset_slider_error_state(search_frame, slider_container):
                    logger.info(f"【{self.pure_user_id}】滑块错误态已重置，重新在当前上下文查找滑块按钮...")
                    for selector in button_selectors:
                        try:
                            element = None
                            if search_frame == self.page:
                                element = self.page.wait_for_selector(selector, timeout=1500)
                            else:
                                try:
                                    element = search_frame.wait_for_selector(selector, timeout=1500)
                                except Exception:
                                    element = search_frame.query_selector(selector)
                            if element:
                                try:
                                    if not element.is_visible():
                                        element = None
                                except Exception:
                                    pass
                            if element:
                                logger.info(f"【{self.pure_user_id}】重置错误态后找到滑块按钮: {selector}")
                                slider_button = element
                                break
                        except Exception:
                            continue
            
            # 如果在找到容器的frame中没找到按钮，尝试在所有frame中查找
            # 无论容器是在主页面还是frame中找到的，如果按钮找不到，都应该在所有frame中查找
            if not slider_button:
                logger.warning(f"【{self.pure_user_id}】在找到容器的位置未找到按钮，尝试在所有frame中查找...")
                try:
                    frames = self.page.frames
                    for idx, frame in enumerate(frames):
                        # 如果容器是在frame中找到的，跳过已经检查过的frame
                        if found_frame and found_frame != self.page and frame == found_frame:
                            continue
                        # 如果容器是在主页面找到的，跳过主页面（因为已经检查过了）
                        if found_frame == self.page and frame == self.page:
                            continue
                            
                        for selector in button_selectors:
                            try:
                                element = None
                                if fast_mode:
                                    # 快速模式：直接使用 query_selector
                                    element = frame.query_selector(selector)
                                else:
                                    # 正常模式：先尝试wait_for_selector
                                    try:
                                        element = frame.wait_for_selector(selector, timeout=2000)
                                    except:
                                        time.sleep(0.3)  # 等待元素加载
                                        element = frame.query_selector(selector)
                                
                                if element:
                                    try:
                                        is_visible = element.is_visible()
                                        if is_visible:
                                            logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块按钮: {selector}")
                                            slider_button = element
                                            found_frame = frame  # 更新found_frame
                                            break
                                        else:
                                            logger.debug(f"【{self.pure_user_id}】在Frame {idx} 找到元素但不可见: {selector}")
                                    except:
                                        # 如果无法检查可见性，仍然使用该元素
                                        logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块按钮（无法检查可见性）: {selector}")
                                        slider_button = element
                                        found_frame = frame  # 更新found_frame
                                        break
                            except Exception as e:
                                logger.debug(f"【{self.pure_user_id}】Frame {idx} 选择器 {selector} 查找失败: {e}")
                                continue
                        if slider_button:
                            break
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】在所有frame中查找按钮时出错: {e}")
            
            # 如果还是没找到，尝试在主页面查找（如果之前没在主页面查找过）
            if not slider_button and found_frame != self.page:
                logger.warning(f"【{self.pure_user_id}】在所有frame中未找到按钮，尝试在主页面查找...")
                for selector in button_selectors:
                    try:
                        element = None
                        if fast_mode:
                            # 快速模式：直接使用 query_selector
                            element = self.page.query_selector(selector)
                        else:
                            # 正常模式：使用 wait_for_selector
                            element = self.page.wait_for_selector(selector, timeout=2000)
                        
                        if element:
                            try:
                                if element.is_visible():
                                    logger.info(f"【{self.pure_user_id}】在主页面找到滑块按钮: {selector}")
                                    slider_button = element
                                    found_frame = self.page  # 更新found_frame
                                    break
                                else:
                                    logger.debug(f"【{self.pure_user_id}】在主页面找到元素但不可见: {selector}")
                            except:
                                # 如果无法检查可见性，仍然使用该元素
                                logger.info(f"【{self.pure_user_id}】在主页面找到滑块按钮（无法检查可见性）: {selector}")
                                slider_button = element
                                found_frame = self.page  # 更新found_frame
                                break
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】主页面选择器 {selector} 查找失败: {e}")
                        continue
            
            # 如果还是没找到，尝试使用更宽松的查找方式（不检查可见性）
            if not slider_button:
                logger.warning(f"【{self.pure_user_id}】使用宽松模式查找滑块按钮（不检查可见性）...")
                # 先在所有frame中查找
                try:
                    frames = self.page.frames
                    for idx, frame in enumerate(frames):
                        for selector in button_selectors[:3]:  # 只使用前3个最常用的选择器
                            try:
                                element = frame.query_selector(selector)
                                if element:
                                    logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块按钮（宽松模式）: {selector}")
                                    slider_button = element
                                    found_frame = frame
                                    break
                            except:
                                continue
                        if slider_button:
                            break
                except:
                    pass
                
                # 如果还是没找到，在主页面查找
                if not slider_button:
                    for selector in button_selectors[:3]:
                        try:
                            element = self.page.query_selector(selector)
                            if element:
                                logger.info(f"【{self.pure_user_id}】在主页面找到滑块按钮（宽松模式）: {selector}")
                                slider_button = element
                                found_frame = self.page
                                break
                        except:
                            continue
            
            if not slider_button:
                logger.error(f"【{self.pure_user_id}】未找到任何滑块按钮（主页面和所有frame都已检查，包括宽松模式）")
                return slider_container, None, None
            
            # 定义滑块轨道选择器
            track_selectors = [
                "#nc_1_n1t",
                ".nc_scale",
                ".nc_1_n1t",
                "[class*='track']",
                "[class*='scale']"
            ]
            
            # 查找滑块轨道（在找到按钮的同一个frame中查找，因为按钮和轨道应该在同一个位置）
            slider_track = None
            # 使用找到按钮的frame来查找轨道
            track_search_frame = found_frame if found_frame and found_frame != self.page else self.page
            
            for selector in track_selectors:
                try:
                    element = None
                    if fast_mode:
                        # 快速模式：直接使用 query_selector
                        element = track_search_frame.query_selector(selector)
                    else:
                        # 正常模式：使用 wait_for_selector
                        if track_search_frame == self.page:
                            element = self.page.wait_for_selector(selector, timeout=3000)
                        else:
                            # 在frame中使用query_selector
                            element = track_search_frame.query_selector(selector)
                    
                    if element:
                        try:
                            if not element.is_visible():
                                element = None
                        except:
                            pass
                    
                    if element:
                        frame_info = "主页面" if track_search_frame == self.page else f"Frame"
                        logger.info(f"【{self.pure_user_id}】在{frame_info}找到滑块轨道: {selector}")
                        slider_track = element
                        break
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】选择器 {selector} 未找到: {e}")
                    continue
            
            # 🔑 关键修复：如果在找到按钮的位置没找到轨道，尝试其他位置
            # 不再限制只在frame中才尝试其他搜索策略，主页面找不到也要尝试frame
            if not slider_track and track_search_frame:
                # 如果按钮在frame中，先点击激活
                if track_search_frame != self.page:
                    logger.warning(f"【{self.pure_user_id}】在已知Frame中未找到轨道，尝试点击frame激活后再查找...")
                    try:
                        # 点击frame以激活它，让轨道出现
                        # 尝试点击frame中的容器或按钮来激活
                        clicked_element = False
                        if slider_container:
                            try:
                                slider_container.click(timeout=1000)
                                logger.info(f"【{self.pure_user_id}】已点击滑块容器以激活frame")
                                clicked_element = True
                                time.sleep(0.3)  # 等待轨道出现
                            except:
                                pass
                        elif slider_button:
                            try:
                                slider_button.click(timeout=1000)
                                logger.info(f"【{self.pure_user_id}】已点击滑块按钮以激活frame")
                                clicked_element = True
                                time.sleep(0.3)  # 等待轨道出现
                            except:
                                pass
                        
                        # 🔑 关键修复：点击后重新查找滑块按钮，因为DOM可能已更新
                        if clicked_element:
                            logger.info(f"【{self.pure_user_id}】点击激活frame后，重新查找滑块按钮以更新元素引用...")
                            old_button = slider_button
                            for selector in button_selectors:
                                try:
                                    element = track_search_frame.query_selector(selector)
                                    if element:
                                        try:
                                            if element.is_visible():
                                                logger.info(f"【{self.pure_user_id}】重新找到滑块按钮: {selector}")
                                                slider_button = element
                                                break
                                        except:
                                            # 如果无法检查可见性，也尝试使用
                                            logger.info(f"【{self.pure_user_id}】重新找到滑块按钮（无法检查可见性）: {selector}")
                                            slider_button = element
                                            break
                                except:
                                    continue
                            
                            if slider_button != old_button:
                                logger.info(f"【{self.pure_user_id}】✅ 滑块按钮元素引用已更新")
                            else:
                                logger.warning(f"【{self.pure_user_id}】⚠️ 未能更新滑块按钮元素引用，可能导致后续操作失败")
                        
                        # 再次在同一个frame中查找轨道
                        for selector in track_selectors:
                            try:
                                element = track_search_frame.query_selector(selector)
                                if element:
                                    try:
                                        if element.is_visible():
                                            logger.info(f"【{self.pure_user_id}】点击frame后在Frame中找到滑块轨道: {selector}")
                                            slider_track = element
                                            break
                                    except:
                                        # 如果无法检查可见性，也尝试使用
                                        logger.info(f"【{self.pure_user_id}】点击frame后在Frame中找到滑块轨道（无法检查可见性）: {selector}")
                                        slider_track = element
                                        break
                            except:
                                continue
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】点击frame后查找轨道时出错: {e}")
                
                # 🔑 关键修复：无论按钮在哪里，都要在所有frame中查找轨道
                if not slider_track:
                    location_desc = "点击frame后仍" if track_search_frame != self.page else "在已知位置"
                    logger.warning(f"【{self.pure_user_id}】{location_desc}未找到轨道，尝试在所有frame中查找...")
                    try:
                        frames = self.page.frames
                        logger.info(f"【{self.pure_user_id}】开始遍历{len(frames)}个frame查找轨道...")
                        for idx, frame in enumerate(frames):
                            if frame == track_search_frame:
                                logger.debug(f"【{self.pure_user_id}】跳过Frame {idx}（已检查过）")
                                continue  # 跳过已经检查过的frame
                            logger.debug(f"【{self.pure_user_id}】检查Frame {idx}...")
                            for selector in track_selectors:
                                try:
                                    element = frame.query_selector(selector)
                                    if element:
                                        # 🔑 降低可见性要求：找到就使用，不强制检查可见性
                                        logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 找到滑块轨道: {selector}")
                                        slider_track = element
                                        # 更新found_frame为找到轨道的frame
                                        found_frame = frame
                                        break
                                except Exception as e:
                                    logger.debug(f"【{self.pure_user_id}】Frame {idx} 选择器 {selector} 出错: {e}")
                                    continue
                            if slider_track:
                                break
                        if not slider_track:
                            logger.warning(f"【{self.pure_user_id}】遍历完{len(frames)}个frame，未找到轨道")
                    except Exception as e:
                        logger.error(f"【{self.pure_user_id}】在所有frame中查找轨道时出错: {e}")
            
            # 如果还是没找到，尝试在主页面查找
            if not slider_track:
                logger.warning(f"【{self.pure_user_id}】在所有frame中未找到轨道，尝试在主页面查找...")
                for selector in track_selectors:
                    try:
                        element = self.page.wait_for_selector(selector, timeout=1000)
                        if element:
                            logger.info(f"【{self.pure_user_id}】在主页面找到滑块轨道: {selector}")
                            slider_track = element
                            break
                    except:
                        continue
            
            if not slider_track:
                logger.error(f"【{self.pure_user_id}】未找到任何滑块轨道（主页面和所有frame都已检查）")
                return slider_container, slider_button, None
            
            # 保存找到滑块的frame引用，供后续验证使用
            if found_frame and found_frame != self.page:
                self._detected_slider_frame = found_frame
                logger.info(f"【{self.pure_user_id}】保存滑块frame引用，供后续验证使用")
            elif found_frame == self.page:
                # 如果是在主页面找到的，设置为None
                self._detected_slider_frame = None
            
            return slider_container, slider_button, slider_track
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】查找滑块元素时出错: {str(e)}")
            return None, None, None
    
    def is_scratch_captcha(self):
        """检测是否为刮刮乐类型验证码"""
        try:
            page_content = self.page.content()
            # 检测刮刮乐特征（更精确的判断）
            # 必须包含明确的刮刮乐特征词
            scratch_required = ['scratch-captcha', 'scratch-captcha-btn', 'scratch-captcha-slider']
            has_scratch_feature = any(keyword in page_content for keyword in scratch_required)
            
            # 或者包含刮刮乐的指令文字
            scratch_instructions = ['Release the slider', 'pillows', 'fully appears', 'after', 'appears']
            has_scratch_instruction = sum(1 for keyword in scratch_instructions if keyword in page_content) >= 2
            
            is_scratch = has_scratch_feature or has_scratch_instruction
            
            if is_scratch:
                logger.info(f"【{self.pure_user_id}】🎨 检测到刮刮乐类型验证码")
            
            return is_scratch
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】检测刮刮乐类型时出错: {e}")
            return False
    
    def calculate_slide_distance(self, slider_button: ElementHandle, slider_track: ElementHandle):
        """计算滑动距离 - 增强精度，支持刮刮乐"""
        try:
            # 🔑 增强错误处理：检查元素是否仍然有效
            button_box = None
            track_box = None
            
            # 尝试获取滑块按钮位置和大小（增加重试机制）
            for retry in range(2):
                try:
                    button_box = slider_button.bounding_box()
                    if button_box:
                        break
                    if retry == 0:
                        logger.warning(f"【{self.pure_user_id}】第{retry+1}次获取滑块按钮位置失败，等待后重试...")
                        time.sleep(0.1)
                except Exception as e:
                    if retry == 0:
                        logger.warning(f"【{self.pure_user_id}】获取滑块按钮位置异常: {e}，等待后重试...")
                        time.sleep(0.1)
                    else:
                        logger.error(f"【{self.pure_user_id}】多次尝试后仍无法获取滑块按钮位置: {e}")
            
            if not button_box:
                logger.error(f"【{self.pure_user_id}】无法获取滑块按钮位置（元素可能已失效，建议重新查找元素）")
                return 0
            
            # 获取滑块轨道位置和大小
            track_box = slider_track.bounding_box()
            if not track_box:
                logger.error(f"【{self.pure_user_id}】无法获取滑块轨道位置")
                return 0
            
            # 🎨 检测是否为刮刮乐类型
            is_scratch = self.is_scratch_captcha()
            
            # 🔑 关键优化1：使用JavaScript获取更精确的尺寸（避免DPI缩放影响）
            try:
                precise_distance = self.page.evaluate("""
                    () => {
                        const button = document.querySelector('#nc_1_n1z') || document.querySelector('.nc_iconfont');
                        const track = document.querySelector('#nc_1_n1t') || document.querySelector('.nc_scale');
                        if (button && track) {
                            const buttonRect = button.getBoundingClientRect();
                            const trackRect = track.getBoundingClientRect();
                            // 计算实际可滑动距离（考虑padding和边距）
                            return trackRect.width - buttonRect.width;
                        }
                        return null;
                    }
                """)
                
                if precise_distance and precise_distance > 0:
                    logger.info(f"【{self.pure_user_id}】使用JavaScript精确计算滑动距离: {precise_distance:.2f}px")
                    
                    # 🎨 刮刮乐特殊处理：只滑动75-85%的距离
                    if is_scratch:
                        scratch_ratio = random.uniform(0.25, 0.35)
                        final_distance = precise_distance * scratch_ratio
                        logger.warning(f"【{self.pure_user_id}】🎨 刮刮乐模式：滑动{scratch_ratio*100:.1f}%距离 ({final_distance:.2f}px)")
                        return final_distance
                    
                    # 🔑 关键优化2：添加微小随机偏移（防止每次都完全相同）
                    # 真人操作时，滑动距离会有微小偏差
                    random_offset = random.uniform(-0.5, 0.5)
                    return precise_distance + random_offset
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】JavaScript精确计算失败，使用后备方案: {e}")
            
            # 后备方案：使用bounding_box计算
            slide_distance = track_box["width"] - button_box["width"]
            
            # 🎨 刮刮乐特殊处理：只滑动75-85%的距离
            if is_scratch:
                scratch_ratio = random.uniform(0.25, 0.35)
                slide_distance = slide_distance * scratch_ratio
                logger.warning(f"【{self.pure_user_id}】🎨 刮刮乐模式：滑动{scratch_ratio*100:.1f}%距离 ({slide_distance:.2f}px)")
            else:
                # 添加微小随机偏移
                random_offset = random.uniform(-0.5, 0.5)
                slide_distance += random_offset
            
            logger.info(f"【{self.pure_user_id}】计算滑动距离: {slide_distance:.2f}px (轨道宽度: {track_box['width']}px, 滑块宽度: {button_box['width']}px)")
            
            return slide_distance
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】计算滑动距离时出错: {str(e)}")
            return 0
    

    def click_to_reset_slider(self):
        """点击失败提示区域以重置滑块"""
        try:
            logger.info(f"【{self.pure_user_id}】尝试点击失败提示区域以重置滑块...")

            # 构建搜索 frame 列表：优先已知 frame，回退到所有 frame
            search_frames = []
            if hasattr(self, '_detected_slider_frame') and self._detected_slider_frame is not None:
                try:
                    _ = self._detected_slider_frame.url if hasattr(self._detected_slider_frame, 'url') else None
                    search_frames.append(self._detected_slider_frame)
                    logger.info(f"【{self.pure_user_id}】将在已知Frame中查找并点击")
                except Exception:
                    logger.warning(f"【{self.pure_user_id}】已知Frame已失效，回退到全局搜索")

            if not search_frames:
                search_frames.append(self.page)
                try:
                    for frame in self.page.frames:
                        if frame != self.page.main_frame:
                            search_frames.append(frame)
                except Exception:
                    pass
                logger.info(f"【{self.pure_user_id}】将在主页面和所有iframe中查找（共{len(search_frames)}个frame）")

            # 按优先级尝试点击不同的区域
            # 优先点击错误状态元素（"点击框体重试"），再尝试容器/包装器
            click_selectors = [
                (".errloading", "错误提示区域"),
                (".nc-lang-cnt .errloading", "NC错误提示"),
                ("[data-nc-status='error']", "NC错误状态元素"),
                (".nc-container", "滑块容器"),
                (".nc_wrapper", "滑块包装器"),
                (".nc_scale", "滑块轨道区域"),
                ("#baxia-dialog-content", "对话框内容"),
                ("#nc_1__bg", "背景区域"),
                ("div[class*='nc']", "NC相关元素"),
            ]

            clicked = False
            for target_frame in search_frames:
                if clicked:
                    break
                for selector, desc in click_selectors:
                    try:
                        element = target_frame.query_selector(selector)
                        if element:
                            try:
                                box = element.bounding_box()
                                if box:
                                    click_x = box['x'] + box['width'] / 2
                                    click_y = box['y'] + box['height'] / 2
                                    self.page.mouse.click(click_x, click_y)
                                    logger.info(f"【{self.pure_user_id}】✅ 已点击{desc}: {selector} (位置: {click_x:.1f}, {click_y:.1f})")
                                    clicked = True
                                    time.sleep(0.5)
                                    break
                                else:
                                    element.click(timeout=1000)
                                    logger.info(f"【{self.pure_user_id}】✅ 已点击{desc}: {selector}")
                                    clicked = True
                                    time.sleep(0.5)
                                    break
                            except Exception as click_e:
                                logger.debug(f"【{self.pure_user_id}】点击{desc} {selector} 失败: {click_e}")
                                continue
                    except Exception as find_e:
                        logger.debug(f"【{self.pure_user_id}】查找{desc} {selector} 失败: {find_e}")
                        continue
            
            if clicked:
                logger.info(f"【{self.pure_user_id}】成功点击失败提示区域，等待滑块重新加载...")
                time.sleep(0.8)  # 等待滑块重新加载（增加等待时间）
                return True
            else:
                logger.warning(f"【{self.pure_user_id}】未找到可点击的失败提示区域，滑块可能已存在")
                return False
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】点击失败提示区域时出错: {e}")
            return False
    
    def solve_slider(self, max_retries: int = 3, fast_mode: bool = False):
        """处理滑块验证（极速模式 + 自适应策略）

        Args:
            max_retries: 最大重试次数（默认3；手动调试链路允许放宽到4次兜底）
            fast_mode: 快速查找模式（当已确认滑块存在时使用，减少等待时间）

        🔧 2026-01-28 优化说明：
        - 默认减少最大重试次数（5→3），避免后台链路无效重试
        - 手动调试链路保留最多第4次兜底，用于真实浏览器单次验证
        - 增加重试间隔冷却时间，避免触发反爬机制
        - 第1次失败后等待2-3秒，第2次失败后等待3-5秒
        """
        original_max_retries = max_retries
        max_retries = max(1, min(int(max_retries or 3), 4))
        if original_max_retries != max_retries:
            logger.info(f"【{self.pure_user_id}】重试次数已收敛到 {max_retries} 次（原请求: {original_max_retries}）")

        failure_records = []
        current_strategy = 'ultra_fast_optimized'  # 优化后的极速策略
        last_attempt = 0

        def finalize_slider_success(
            attempt_no: int,
            success_note: Optional[str] = None,
            cookie_refresh_confirmed: Optional[bool] = None,
            soft_success: bool = False,
        ) -> bool:
            if success_note:
                logger.success(f"【{self.pure_user_id}】✅ {success_note}")

            logger.info(f"【{self.pure_user_id}】✅ 滑块验证成功! (第{attempt_no}次尝试)")

            strategy_stats.record_attempt(attempt_no, current_strategy, success=True)
            logger.info(f"【{self.pure_user_id}】📊 记录策略: 第{attempt_no}次-{current_strategy}策略-成功")

            if hasattr(self, 'current_trajectory_data'):
                used_strategy = self.current_trajectory_data.get("random_params", {}).get("strategy", "unknown")
                adaptive_strategy_manager.record_result(used_strategy, success=True)
                self._update_current_result_meta(
                    "success",
                    attempt=attempt_no,
                    cookie_refresh_confirmed=cookie_refresh_confirmed,
                    soft_success=soft_success,
                    note=success_note,
                )

            if self.enable_learning and hasattr(self, 'current_trajectory_data'):
                self._save_success_record(self.current_trajectory_data)
                logger.info(f"【{self.pure_user_id}】已保存成功记录用于参数优化")

            if attempt_no > 1:
                logger.info(f"【{self.pure_user_id}】经过{attempt_no}次尝试后验证成功")

            strategy_stats.log_summary()
            logger.info(adaptive_strategy_manager.get_stats_summary())
            return True

        # 快照当前 Cookie 基线（用于验证成功后判定"有意义的刷新"）
        cookie_baseline = self._snapshot_context_cookies()
        if cookie_baseline:
            x5_count = sum(1 for k in cookie_baseline if k.lower().startswith('x5'))
            key_count = sum(1 for k in self._KEY_COOKIE_NAMES if k in cookie_baseline)
            logger.info(f"【{self.pure_user_id}】Cookie 基线已快照: 共{len(cookie_baseline)}个, x5系{x5_count}个, 关键会话{key_count}个")
        else:
            logger.warning(f"【{self.pure_user_id}】Cookie 基线为空，将跳过 Cookie 刷新校验")

        for attempt in range(1, max_retries + 1):
            try:
                last_attempt = attempt
                logger.info(f"【{self.pure_user_id}】开始处理滑块验证... (第{attempt}/{max_retries}次尝试)")

                current_block = self._detect_special_captcha_block(self.page)
                current_block = self._wait_for_punish_slider_dom_ready_if_needed(
                    self.page,
                    current_block,
                    f"滑块第{attempt}次尝试起始页",
                )
                current_block = self._recover_punish_slider_shell_if_possible(
                    self.page,
                    current_block,
                    f"滑块第{attempt}次尝试起始页",
                )
                if current_block:
                    logger.error(
                        f"【{self.pure_user_id}】当前页面命中高风险验证码页[{current_block['kind']}]: "
                        f"{current_block['message']}，停止继续滑块重试"
                    )
                    self.last_verification_feedback = {
                        "status": "hard_block",
                        "source": current_block["kind"],
                        "message": current_block["message"],
                        "url": current_block.get("url") or "",
                        "title": current_block.get("title") or "",
                        "attempt": attempt,
                    }
                    self._save_debug_snapshot("hard_block_page", self.page)
                    break

                # 检测账号受限状态（如果受限则立即停止，不浪费重试机会）
                try:
                    page_text = self.page.inner_text('body', timeout=2000) if self.page else ''
                    restricted_keywords = ['账号已被限制', '限制访问', '账号异常', '账号被冻结', '暂时无法使用',
                                          '您的账号', '安全验证未通过', '账户被限制']
                    for kw in restricted_keywords:
                        if kw in page_text:
                            logger.error(f"【{self.pure_user_id}】检测到账号受限状态: '{kw}'，停止滑块处理")
                            return False
                except Exception:
                    pass

                # 如果不是第一次尝试，使用渐进式等待策略
                if attempt > 1:
                    # 🔧 优化：增加重试间隔，降低反爬触发风险
                    # 第2次等待4-6秒，第3次等待6-8秒
                    base_delay = 4.0 + (attempt - 1) * 2.0  # 基础4秒，每次增加2秒
                    retry_delay = random.uniform(base_delay, base_delay + 2.0)
                    logger.info(f"【{self.pure_user_id}】⏳ 等待{retry_delay:.1f}秒后重试...")
                    time.sleep(retry_delay)

                    # 优先点击重置滑块（不刷新页面，避免丢失已输入的表单数据）
                    logger.info(f"【{self.pure_user_id}】🔄 尝试点击重置滑块...")
                    reset_success = self.click_to_reset_slider()
                    if reset_success:
                        logger.info(f"【{self.pure_user_id}】✅ 滑块已重置，准备重新检测")
                        time.sleep(1.0)
                    else:
                        # 点击重置失败时才回退到刷新页面
                        logger.warning(f"【{self.pure_user_id}】⚠️ 点击重置失败，回退到刷新页面...")
                        try:
                            self.page.reload(wait_until='networkidle', timeout=15000)
                            time.sleep(1.0)
                            logger.info(f"【{self.pure_user_id}】✅ 页面刷新完成，准备重新检测滑块")
                        except Exception as refresh_error:
                            logger.warning(f"【{self.pure_user_id}】⚠️ 页面刷新也失败: {refresh_error}")

                    # 清除缓存的frame引用，强制重新检测滑块位置
                    if hasattr(self, '_detected_slider_frame'):
                        delattr(self, '_detected_slider_frame')
                        logger.info(f"【{self.pure_user_id}】已清除frame缓存，将重新全局搜索滑块")
                
                # 1. 查找滑块元素（使用快速模式）
                slider_container, slider_button, slider_track = self.find_slider_elements(fast_mode=fast_mode)
                if not all([slider_container, slider_button, slider_track]):
                    logger.error(f"【{self.pure_user_id}】滑块元素查找失败")
                    if (self.last_verification_feedback or {}).get("status") == "hard_block":
                        logger.error(f"【{self.pure_user_id}】当前页面已识别为高风险验证码页，停止当前滑块流程")
                        break
                    self.last_verification_feedback = {
                        "status": "page_state_changed",
                        "source": "slider_missing",
                        "message": "当前页面未找到滑块容器",
                        "attempt": attempt
                    }
                    # 🔑 关键修复：清除缓存的frame位置，下次重试时重新全局搜索
                    if hasattr(self, '_detected_slider_frame'):
                        logger.warning(f"【{self.pure_user_id}】清除缓存的滑块位置信息，下次重试将重新全局搜索")
                        delattr(self, '_detected_slider_frame')

                    context_login_success, _ = self._probe_context_login_during_slider(self.page)
                    if context_login_success:
                        return finalize_slider_success(
                            attempt,
                            "当前页面已无滑块，但上下文已确认登录",
                            cookie_refresh_confirmed=None,
                            soft_success=False,
                        )

                    logger.warning(f"【{self.pure_user_id}】当前页面已无滑块，不再继续同轮滑块重试")
                    break

                slider_search_target = getattr(self, "_detected_slider_frame", None)
                self._harden_password_slider_runtime(slider_search_target)
                
                # 2. 计算滑动距离
                slide_distance = self.calculate_slide_distance(slider_button, slider_track)
                if slide_distance <= 0:
                    logger.error(f"【{self.pure_user_id}】滑动距离计算失败")
                    continue
                
                # 3. 生成人类化轨迹（传递尝试次数以增加随机扰动）
                trajectory = self.generate_human_trajectory(slide_distance, attempt=attempt)
                if not trajectory:
                    logger.error(f"【{self.pure_user_id}】轨迹生成失败")
                    continue
                
                # 4. 模拟滑动
                if not self.simulate_slide(slider_button, trajectory):
                    logger.error(f"【{self.pure_user_id}】滑动模拟失败")
                    continue
                
                # 5. 检查验证结果（极速模式）
                verification_success = self.check_verification_success_fast(slider_button)
                if not verification_success:
                    context_login_success, _ = self._probe_context_login_during_slider(self.page)
                    if context_login_success:
                        verification_success = True
                        logger.success(f"【{self.pure_user_id}】✅ 滑块结果未明确成功，但上下文已确认登录，按成功收口")

                if verification_success:
                    # 🔑 Cookie 双重校验：页面状态通过后，轮询检查关键 Cookie 是否真正刷新
                    cookie_refresh_confirmed: Optional[bool] = None
                    soft_success = False
                    if cookie_baseline:
                        # 先等待稳定窗口（1.2 秒），给页面回写票据留时间
                        time.sleep(1.2)
                        cookie_refreshed = False
                        current_cookies = dict(cookie_baseline)
                        # 以 500ms 间隔轮询 x5/关键 Cookie 变化，最长等 10 秒
                        poll_interval = 0.5
                        max_poll_time = 10.0
                        poll_start = time.time()
                        while time.time() - poll_start < max_poll_time:
                            current_cookies = self._snapshot_context_cookies()
                            if self._has_meaningful_cookie_refresh(cookie_baseline, current_cookies):
                                cookie_refreshed = True
                                break
                            time.sleep(poll_interval)

                        if not cookie_refreshed:
                            context_login_success, confirmed_cookies = self._probe_context_login_during_slider(self.page)
                            if context_login_success:
                                logger.success(
                                    f"【{self.pure_user_id}】✅ 页面显示验证通过且上下文已确认登录，放宽 Cookie 变化校验"
                                )
                                cookie_refreshed = True
                                if confirmed_cookies:
                                    current_cookies = confirmed_cookies
                            else:
                                soft_success_allowed, soft_success_reason = self._should_accept_soft_success_without_cookie_refresh(
                                    current_cookies,
                                    self.page,
                                )
                                if soft_success_allowed:
                                    logger.success(
                                        f"【{self.pure_user_id}】✅ 页面已脱离验证态，接受软成功: {soft_success_reason}"
                                    )
                                    cookie_refresh_confirmed = False
                                    soft_success = True
                                    cookie_refreshed = True
                                    self.last_verification_feedback = {
                                        "status": "success",
                                        "source": "soft_success_cookie_pending",
                                        "message": soft_success_reason,
                                    }
                                else:
                                    logger.warning(f"【{self.pure_user_id}】⚠️ 页面显示验证通过，但等待{max_poll_time}秒后关键 Cookie 仍无变化，判定为假通过")
                                    if hasattr(self, 'current_trajectory_data'):
                                        self._update_current_result_meta(
                                            "failure",
                                            attempt=attempt,
                                            cookie_refresh_confirmed=False,
                                            soft_success=False,
                                            note="cookie_not_refreshed_after_page_success",
                                        )
                                        used_strategy = self.current_trajectory_data.get("random_params", {}).get("strategy", "unknown")
                                        adaptive_strategy_manager.record_result(used_strategy, success=False)
                                    strategy_stats.record_attempt(attempt, current_strategy, success=False)
                                    if attempt < max_retries:
                                        continue
                                    else:
                                        break

                        # Cookie 校验通过，更新基线
                        cookie_baseline = current_cookies
                        if cookie_refresh_confirmed is None:
                            cookie_refresh_confirmed = not soft_success

                    return finalize_slider_success(
                        attempt,
                        cookie_refresh_confirmed=cookie_refresh_confirmed,
                        soft_success=soft_success,
                    )
                else:
                    logger.warning(f"【{self.pure_user_id}】❌ 第{attempt}次验证失败")
                    
                    # 📊 记录策略失败
                    strategy_stats.record_attempt(attempt, current_strategy, success=False)
                    logger.info(f"【{self.pure_user_id}】📊 记录策略: 第{attempt}次-{current_strategy}策略-失败")
                    
                    # 🤖 记录到自适应策略管理器
                    if hasattr(self, 'current_trajectory_data'):
                        used_strategy = self.current_trajectory_data.get("random_params", {}).get("strategy", "unknown")
                        adaptive_strategy_manager.record_result(used_strategy, success=False)
                    
                    # 分析失败原因
                    if hasattr(self, 'current_trajectory_data'):
                        self._update_current_result_meta(
                            "failure",
                            attempt=attempt,
                            cookie_refresh_confirmed=False,
                            soft_success=False,
                            note="verification_failed",
                        )
                        failure_info = self._analyze_failure(attempt, slide_distance, self.current_trajectory_data)
                        failure_records.append(failure_info)
                        self._save_failure_record(self.current_trajectory_data, failure_info)

                    abort_retry, abort_reason = self._should_abort_slider_retry_after_failure()
                    if abort_retry:
                        logger.warning(f"【{self.pure_user_id}】{abort_reason}")
                        if hasattr(self, 'current_trajectory_data'):
                            self._update_current_result_meta(
                                "failure",
                                attempt=attempt,
                                cookie_refresh_confirmed=False,
                                soft_success=False,
                                note="token_refresh_hard_reject_abort_retry",
                            )
                        break
                    
                    # 如果不是最后一次尝试，继续
                    if attempt < max_retries:
                        continue
                
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】第{attempt}次处理滑块验证时出错: {str(e)}")
                if attempt < max_retries:
                    continue
        
        # 所有尝试都失败了
        attempts_used = max(last_attempt, len(failure_records))
        logger.error(f"【{self.pure_user_id}】滑块验证失败，已尝试{attempts_used}次")
        
        # 输出失败分析摘要
        if failure_records:
            logger.info(f"【{self.pure_user_id}】失败分析摘要:")
            for record in failure_records:
                logger.info(f"  - 第{record['attempt']}次: 距离{record['slide_distance']}px, "
                          f"步数{record['total_steps']}, 最终位置{record['final_left_px']}px")
        
        # 输出当前统计摘要
        strategy_stats.log_summary()

        self._save_debug_snapshot("solve_slider_failed", getattr(self, "_detected_slider_frame", None))
        
        return False
    
    def _release_concurrency_slot(self, reason: str = "") -> bool:
        """幂等释放并发槽位，避免清理过程卡死导致后续账号永远排队。"""
        if not getattr(self, '_concurrency_slot_registered', False):
            return False
        try:
            concurrency_manager.unregister_instance(self.user_id, self)
            self._concurrency_slot_registered = False
            stats = concurrency_manager.get_stats()
            reason_suffix = f"（{reason}）" if reason else ""
            logger.info(
                f"【{self.pure_user_id}】已释放并发槽位{reason_suffix}，当前并发: "
                f"{stats['active_count']}/{stats['max_concurrent']}，等待队列: {stats['queue_length']}"
            )
            return True
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】释放并发槽位时出错: {e}")
            return False

    def _stop_playwright_with_timeout(self, timeout_seconds: float = 5.0) -> bool:
        """best-effort 停止 Playwright，遇到跨线程 greenlet 错误时降级为引用置空。

        历史实现把 stop() 放进新 daemon 线程做超时保护——但 Playwright sync 实例
        必须在 start() 时所在的同一线程销毁，跨线程 stop() 必抛
        `Cannot switch to a different thread`，等于零保护还污染日志。
        现在改为：
        - 同线程：直接 stop()，让 Playwright 自己回收
        - 跨线程：跳过 stop()，仅返回 False，由 close_browser 负责把 self.playwright = None
        """
        if not getattr(self, 'playwright', None):
            return True

        creating_tid = getattr(self, '_playwright_thread_id', None)
        current_tid = threading.get_ident()
        if creating_tid is not None and current_tid != creating_tid:
            logger.warning(
                f"【{self.pure_user_id}】跨线程销毁 Playwright "
                f"(创建 tid={creating_tid}, 当前 tid={current_tid})，"
                f"跳过 sync stop() 以避免 greenlet 错误"
            )
            return False

        try:
            self.playwright.stop()
            return True
        except Exception as exc:
            msg = str(exc)
            if 'Cannot switch to a different thread' in msg or 'greenlet' in msg.lower():
                logger.warning(
                    f"【{self.pure_user_id}】Playwright.stop() 命中 greenlet 错误，已忽略: {msg}"
                )
                return False
            raise

    def _safe_pw_dispose(self, obj_name: str, obj, action: str = 'close') -> None:
        """统一封装 Playwright 同步资源关闭：跨线程 greenlet 错误降级为日志，不抛。"""
        if not obj:
            return
        creating_tid = getattr(self, '_playwright_thread_id', None)
        current_tid = threading.get_ident()
        if creating_tid is not None and current_tid != creating_tid:
            logger.warning(
                f"【{self.pure_user_id}】跨线程销毁 {obj_name} "
                f"(创建 tid={creating_tid}, 当前 tid={current_tid})，"
                f"跳过 sync {action}() 以避免 greenlet 错误"
            )
            return
        try:
            getattr(obj, action)()
            logger.debug(f"【{self.pure_user_id}】{obj_name} 已 {action}")
        except Exception as e:
            msg = str(e)
            if 'Cannot switch to a different thread' in msg or 'greenlet' in msg.lower():
                logger.warning(
                    f"【{self.pure_user_id}】销毁 {obj_name} 命中 greenlet 错误，已忽略: {msg}"
                )
            else:
                logger.warning(f"【{self.pure_user_id}】{action} {obj_name} 时出错: {e}")


    def _extract_browser_pid(self, runtime_obj, playwright_obj=None) -> Optional[int]:
        """尽量从 Playwright runtime 对象上提取浏览器进程 PID。"""
        try:
            process = getattr(runtime_obj, "process", None)
            pid = getattr(process, "pid", None)
            if pid:
                return int(pid)
        except Exception:
            pass
        try:
            browser = getattr(runtime_obj, "browser", None)
            process = getattr(browser, "process", None) if browser else None
            pid = getattr(process, "pid", None)
            if pid:
                return int(pid)
        except Exception:
            pass
        # Python sync API 的 Browser/Context 并不暴露 process 属性，上面两条路径
        # 实际拿不到 PID（永远返回 None），导致强杀兜底静默失效、浏览器进程常驻泄漏。
        # 回退为提取 Playwright node driver 的子进程 PID：所有 chrome 进程都挂在
        # driver 之下，按 driver 进程树清理可以等效覆盖浏览器进程树。
        try:
            pw = playwright_obj or getattr(self, "playwright", None)
            impl = getattr(pw, "_impl_obj", pw)
            proc = getattr(getattr(getattr(impl, "_connection", None), "_transport", None), "_proc", None)
            pid = getattr(proc, "pid", None)
            if proc is not None and pid:
                # sync API 下 _proc 实际是 asyncio.subprocess.Process：
                # 只有 returncode 没有 poll()，需要兼容两种存活判断
                poll = getattr(proc, "poll", None)
                if callable(poll):
                    alive = poll() is None
                else:
                    alive = getattr(proc, "returncode", None) is None
                if alive:
                    return int(pid)
        except Exception:
            pass
        return None

    def _force_kill_browser_process_tree(self, reason: str = "") -> bool:
        """兜底终止浏览器进程树，用于 close 失败或页面崩溃后的残留清理。"""
        pid = self._browser_pid
        if not pid:
            return False

        reason_suffix = f"（{reason}）" if reason else ""
        try:
            process_tree = self._collect_process_tree(int(pid))
            if not process_tree:
                return False

            logger.warning(
                f"【{self.pure_user_id}】开始兜底清理浏览器进程树{reason_suffix}: {process_tree}"
            )

            for signal_name, sig in (("TERM", signal.SIGTERM), ("KILL", signal.SIGKILL)):
                for proc_pid in process_tree:
                    try:
                        os.kill(proc_pid, sig)
                    except ProcessLookupError:
                        continue
                    except PermissionError as e:
                        logger.warning(f"【{self.pure_user_id}】终止进程 {proc_pid} 失败: {e}")
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】清理进程 {proc_pid} 时出错: {e}")

                time.sleep(0.4 if signal_name == "TERM" else 0.2)

            self._browser_pid = None
            return True
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】兜底清理浏览器进程树失败: {e}")
            return False

    def close_browser(self):
        """安全关闭浏览器并清理资源"""
        logger.info(f"【{self.pure_user_id}】开始清理资源...")

        # 先释放槽位，避免后续任一清理步骤卡死把同账号任务永久堵住。
        self._release_concurrency_slot("close_browser开始")

        # 看门狗：优雅清理超过20秒仍未完成（如 close()/stop() 同线程挂死在
        # 死循环页面上）时，直接按进程树强杀兜底。强杀会让阻塞的 sync 调用
        # 抛错并被 _safe_pw_dispose 吸收，close_browser 得以继续走完。
        close_watchdog = threading.Timer(
            20.0,
            self._force_kill_browser_process_tree,
            args=("close_browser_watchdog",),
        )
        close_watchdog.daemon = True
        close_watchdog.start()

        try:
            # 清理页面 / 上下文 / 浏览器：跨线程 greenlet 错误由 _safe_pw_dispose 统一吸收
            self._safe_pw_dispose('页面', getattr(self, 'page', None), action='close')
            self.page = None

            self._safe_pw_dispose('上下文', getattr(self, 'context', None), action='close')
            self.context = None

            self._safe_pw_dispose('浏览器', getattr(self, 'browser', None), action='close')
            self.browser = None

            # 停止 Playwright（_stop_playwright_with_timeout 内部已做跨线程保护）
            try:
                if hasattr(self, 'playwright') and self.playwright:
                    stopped = self._stop_playwright_with_timeout()
                    if stopped:
                        logger.info(f"【{self.pure_user_id}】Playwright已停止")
                    else:
                        logger.warning(f"【{self.pure_user_id}】Playwright未能在当前线程停止，已放弃 stop() 仅置空引用")
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】停止Playwright时出错: {e}")
            finally:
                # 不论 stop 成功与否，都把引用置空，避免下一次 close_browser 又对死引用操作
                self.playwright = None
                self._playwright_thread_id = None

            # 再补一层浏览器子进程兜底清理，防止 browser.close()/playwright.stop() 没有真正回收干净
            self._force_kill_browser_process_tree("close_browser")

            # 清理临时目录
            try:
                if hasattr(self, 'temp_dir') and self.temp_dir:
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                    logger.debug(f"【{self.pure_user_id}】临时目录已清理: {self.temp_dir}")
                    self.temp_dir = None  # 设置为None，防止重复清理
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】清理临时目录时出错: {e}")
        finally:
            # 放在 finally：即使清理中途抛出未捕获异常，也保证取消看门狗，
            # 避免 20 秒后按已过期的 _browser_pid 误杀后续新会话的进程树
            close_watchdog.cancel()

        # 再兜底释放一次，兼容前面提前释放失败的极端情况。
        self._release_concurrency_slot("close_browser收尾")

        logger.info(f"【{self.pure_user_id}】资源清理完成")
    
    def __del__(self):
        """析构函数，确保资源释放（保险机制）"""
        try:
            # 检查是否有未关闭的浏览器
            if hasattr(self, 'browser') and self.browser:
                logger.warning(f"【{self.pure_user_id}】析构函数检测到未关闭的浏览器，执行清理")
                self.close_browser()
        except Exception as e:
            # 析构函数中不要抛出异常
            logger.debug(f"【{self.pure_user_id}】析构函数清理时出错: {e}")
    
    # ==================== Playwright 登录辅助方法 ====================
    
    
    def _is_profile_in_use_launch_error(self, error: Exception) -> bool:
        error_text = str(error or "").lower()
        lock_markers = (
            "profile appears to be in use",
            "process_singleton",
            "chromium has locked the profile",
            "user data directory is already in use",
        )
        return any(marker in error_text for marker in lock_markers)

    def _get_current_hostname(self) -> str:
        try:
            return str(socket.gethostname() or "").strip()
        except Exception:
            return ""

    def _looks_like_docker_container_hostname(self, hostname: str) -> bool:
        normalized = str(hostname or "").strip().lower()
        return bool(re.fullmatch(r"[0-9a-f]{12}", normalized))

    def _is_process_alive(self, pid: int) -> bool:
        try:
            normalized_pid = int(pid)
        except (TypeError, ValueError):
            return False
        if normalized_pid <= 0:
            return False
        try:
            os.kill(normalized_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _parse_chromium_singleton_lock(self, profile_dir: str) -> Optional[Dict[str, Any]]:
        lock_path = os.path.join(profile_dir, "SingletonLock")
        if not os.path.islink(lock_path):
            return None
        try:
            target = os.readlink(lock_path)
        except OSError as read_error:
            logger.warning(f"【{self.pure_user_id}】读取 Chromium SingletonLock 失败: {read_error}")
            return None

        target_name = os.path.basename(str(target or "").rstrip("/\\"))
        if "-" not in target_name:
            return {
                "lock_path": lock_path,
                "target": target,
                "host": None,
                "pid": None,
            }

        lock_host, pid_text = target_name.rsplit("-", 1)
        if not lock_host or not pid_text.isdigit():
            return {
                "lock_path": lock_path,
                "target": target,
                "host": None,
                "pid": None,
            }

        return {
            "lock_path": lock_path,
            "target": target,
            "host": lock_host,
            "pid": int(pid_text),
        }

    def _try_cleanup_stale_chromium_singleton_lock(self, profile_dir: str) -> bool:
        lock_info = self._parse_chromium_singleton_lock(profile_dir)
        if not lock_info:
            logger.info(f"【{self.pure_user_id}】未发现可判定的 Chromium SingletonLock，跳过自动清理")
            return False

        current_host = self._get_current_hostname()
        lock_host = str(lock_info.get("host") or "").strip()
        lock_pid = lock_info.get("pid")
        if not current_host or not lock_host or lock_pid is None:
            logger.warning(
                f"【{self.pure_user_id}】SingletonLock 信息不足，无法证明是 stale 锁，保持原有 fallback: "
                f"host={lock_host or 'unknown'}, pid={lock_pid}"
            )
            return False

        same_host = lock_host == current_host
        same_docker_host_rollover = (
            not same_host and
            self._looks_like_docker_container_hostname(lock_host) and
            self._looks_like_docker_container_hostname(current_host)
        )
        if not same_host and not same_docker_host_rollover:
            logger.warning(
                f"【{self.pure_user_id}】SingletonLock 指向其他宿主机，拒绝自动清理: "
                f"lock_host={lock_host}, current_host={current_host}, pid={lock_pid}"
            )
            return False
        if same_docker_host_rollover:
            logger.warning(
                f"【{self.pure_user_id}】检测到 Docker 容器 hostname 漂移导致的 stale SingletonLock，"
                f"允许按失效锁清理: lock_host={lock_host}, current_host={current_host}, pid={lock_pid}"
            )

        if self._is_process_alive(lock_pid):
            logger.info(f"【{self.pure_user_id}】SingletonLock 对应进程仍存活(pid={lock_pid})，跳过自动清理")
            return False

        removed_any = False
        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock_path = os.path.join(profile_dir, lock_name)
            try:
                if os.path.lexists(lock_path):
                    os.unlink(lock_path)
                    removed_any = True
                    logger.warning(f"【{self.pure_user_id}】已清理 stale Chromium 锁文件: {lock_path}")
            except OSError as cleanup_error:
                logger.warning(f"【{self.pure_user_id}】清理 stale Chromium 锁文件失败({lock_path}): {cleanup_error}")

        return removed_any

    def _launch_clean_cookie_seeded_context(
        self,
        playwright,
        launch_options: Dict[str, Any],
        browser_features: Dict[str, Any],
    ) -> Tuple[Any, Any]:
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context(
            viewport={'width': browser_features['viewport_width'], 'height': browser_features['viewport_height']},
            user_agent=browser_features['user_agent'],
            locale=browser_features['locale'],
            accept_downloads=True,
            ignore_https_errors=True,
            extra_http_headers={
                'Accept-Language': browser_features['accept_lang']
            }
        )
        try:
            cookies_to_inject = self._build_initial_cookie_payload()
            cookie_str = ''
            if not cookies_to_inject:
                from db_manager import db_manager as _db
                cookie_info = _db.get_cookie_details(self.pure_user_id)
                if cookie_info and cookie_info.get('value'):
                    cookie_str = cookie_info['value']
            if cookie_str:
                cookies_to_inject = []
                for pair in cookie_str.split(';'):
                    pair = pair.strip()
                    if '=' in pair:
                        name, value = pair.split('=', 1)
                        name = name.strip()
                        value = value.strip()
                        if name:
                            cookies_to_inject.append({
                                'name': name,
                                'value': value,
                                'domain': '.goofish.com',
                                'path': '/',
                            })
                            if name in ('_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 'sgcookie', 'unb', 't', 'cna'):
                                cookies_to_inject.append({
                                    'name': name,
                                    'value': value,
                                    'domain': '.taobao.com',
                                    'path': '/',
                                })
            if cookies_to_inject:
                context.add_cookies(cookies_to_inject)
                logger.info(f"【{self.pure_user_id}】已注入 {len(cookies_to_inject)} 个历史 Cookie 到干净上下文")
            else:
                logger.info(f"【{self.pure_user_id}】未找到可注入的历史 Cookie，继续使用全新上下文")
        except Exception as inject_e:
            logger.warning(f"【{self.pure_user_id}】注入历史 Cookie 失败（不影响继续登录）: {inject_e}")
        return browser, context
    
    
    def run(
        self,
        url: str,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '手动导入 Cookie',
    ):
        """运行主流程，返回(成功状态, cookie数据)"""
        cookies = None
        # 每次 run() 进入都先清空内层自救兜底标记，避免上次状态残留
        self._post_recovery_success = False
        self._post_recovery_cookies = None
        try:
            # 检查日期有效性
            if not self._check_date_validity():
                logger.error(f"【{self.pure_user_id}】日期验证失败，无法执行")
                return False, None
            
            # 初始化浏览器
            self.init_browser()

            # 无头模式默认跳过额外预热，避免先访问其它页面把风控状态搞得更脏；
            # 如需回滚，可设置 XY_SLIDER_HEADLESS_WARMUP=1。
            if not (self.headless and self.disable_headless_warmup):
                self._warmup_slider_context(url)
            
            # 导航到目标URL，快速加载
            logger.info(f"【{self.pure_user_id}】导航到URL: {url}")
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】页面加载异常，尝试继续: {str(e)}")
                # 如果页面加载失败，尝试等待一下
                time.sleep(2)

            self._captcha_page_entry_ts = time.time()
            
            # 短暂延迟，快速处理
            delay = random.uniform(0.3, 0.8)
            logger.info(f"【{self.pure_user_id}】等待页面加载: {delay:.2f}秒")
            time.sleep(delay)
            
            # 初始轻微鼠标移动，避免一打开就是静止死板页
            self.page.mouse.move(
                random.randint(520, 760),
                random.randint(280, 420),
                steps=random.randint(6, 16),
            )
            time.sleep(random.uniform(0.05, 0.12))
            
            # 检查页面标题
            page_title = self.page.title()
            logger.info(f"【{self.pure_user_id}】页面标题: {page_title}")
            
            # 检查页面内容
            page_content = self.page.content()
            if any(keyword in page_content for keyword in ["验证码", "captcha", "滑块", "slider"]):
                logger.info(f"【{self.pure_user_id}】页面内容包含验证码相关关键词")

                if self._is_hard_block_page(self.page):
                    self.last_verification_feedback = {
                        "status": "hard_block",
                        "source": "deny_page",
                        "message": "当前页面是阿里处罚页/反馈二维码页，不是真正可拖动的滑块",
                    }
                    logger.error(
                        f"【{self.pure_user_id}】当前命中的是处罚页/反馈二维码页，"
                        f"{'无头' if self.headless else '有头'}环境指纹已被风控拦截，当前页面不存在可操作滑块"
                    )
                    self._save_debug_snapshot("hard_block_page", self.page)
                    monitor_page = self._select_monitor_page(self.context, self.page) or self.page
                    has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                    if has_qr:
                        verification_result = self._process_verification_requirement(
                            self.context,
                            monitor_page,
                            qr_frame,
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                        )
                        if verification_result:
                            return True, verification_result
                    return False, None

                self._simulate_human_page_behavior()

                # 处理滑块验证
                success = self.solve_slider(max_retries=self.slider_max_retries)
                
                if success:
                    logger.info(f"【{self.pure_user_id}】滑块验证成功")
                    
                    # 等待页面完全加载和跳转，让新的cookie生效（快速模式）
                    try:
                        logger.info(f"【{self.pure_user_id}】等待页面加载...")
                        time.sleep(1)  # 快速等待，从3秒减少到1秒
                        
                        # 等待页面跳转或刷新
                        self.page.wait_for_load_state("networkidle", timeout=10000)
                        time.sleep(0.5)  # 快速确认，从2秒减少到0.5秒
                        
                        logger.info(f"【{self.pure_user_id}】页面加载完成，开始获取cookie")
                    except Exception as e:
                        logger.warning(f"【{self.pure_user_id}】等待页面加载时出错: {str(e)}")

                    monitor_page = self._select_monitor_page(self.context, self.page) or self.page
                    has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                    if has_qr:
                        logger.warning(f"【{self.pure_user_id}】滑块通过后检测到身份验证页，转入验证等待流程")
                        verification_result = self._process_verification_requirement(
                            self.context,
                            monitor_page,
                            qr_frame,
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                        )
                        if verification_result:
                            return True, verification_result
                        return False, None
                    
                    # 在关闭浏览器前获取cookie
                    try:
                        cookies = self._get_cookies_after_success()
                    except Exception as e:
                        logger.warning(f"【{self.pure_user_id}】获取cookie时出错: {str(e)}")
                else:
                    logger.warning(f"【{self.pure_user_id}】滑块验证失败")
                    monitor_page = self._select_monitor_page(self.context, self.page) or self.page
                    has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                    if has_qr:
                        logger.warning(f"【{self.pure_user_id}】滑块流程结束后检测到身份验证页，转入验证等待流程")
                        verification_result = self._process_verification_requirement(
                            self.context,
                            monitor_page,
                            qr_frame,
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                        )
                        if verification_result:
                            return True, verification_result
                    # 兜底回流：_detect_qr_code_verification 内部 reload+solve_slider 自救成功时，
                    # 会把 cookies 写入 self._post_recovery_cookies 并标记 _post_recovery_success。
                    # 这里识别该信号并把 run() 主流程翻成成功，避免外层误以为失败而触发 600s 退避。
                    if self._post_recovery_success and self._post_recovery_cookies:
                        logger.success(
                            f"【{self.pure_user_id}】✅ 外层滑块判失败，但内层 _detect_qr_code_verification 自救成功，"
                            f"按 run() 成功收口"
                        )
                        return True, self._post_recovery_cookies
                    self._save_debug_snapshot("run_failed", getattr(self, "_detected_slider_frame", None))
                
                return success, cookies
            else:
                logger.info(f"【{self.pure_user_id}】页面内容不包含验证码相关关键词，可能不需要验证")
                monitor_page = self._select_monitor_page(self.context, self.page) or self.page
                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                if has_qr:
                    logger.warning(f"【{self.pure_user_id}】页面无滑块但存在身份验证页，转入验证等待流程")
                    verification_result = self._process_verification_requirement(
                        self.context,
                        monitor_page,
                        qr_frame,
                        notification_callback=notification_callback,
                        notification_scene=notification_scene,
                    )
                    if verification_result:
                        return True, verification_result
                    return False, None
                return True, None
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】执行过程中出错: {str(e)}")
            return False, None
        finally:
            # 关闭浏览器
            self.close_browser()

    async def async_run(self, url: str):
        """异步运行主流程，返回(成功状态, cookie数据)

        在独立线程中运行同步的 Playwright，避免事件循环冲突
        """
        import asyncio

        def _run_in_thread():
            """在独立线程中运行同步代码"""
            import asyncio
            # 确保线程中没有运行的事件循环
            try:
                loop = asyncio.get_running_loop()
                # 如果有运行中的循环，创建新循环
                asyncio.set_event_loop(asyncio.new_event_loop())
            except RuntimeError:
                # 没有运行中的循环，正常
                pass

            # 调用同步的 run 方法
            return self.run(url)

        # 使用 asyncio.to_thread 在独立线程中运行
        return await self._run_sync_method_on_fresh_thread(self.run, url)

    async def _run_sync_method_on_fresh_thread(self, func, *args, **kwargs):
        import asyncio
        import threading

        loop = asyncio.get_running_loop()
        result_future = loop.create_future()

        def _complete_result(value):
            if not result_future.done():
                result_future.set_result(value)

        def _complete_exception(exc: BaseException):
            if not result_future.done():
                result_future.set_exception(exc)

        def _worker():
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass

            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                loop.call_soon_threadsafe(_complete_exception, exc)
                return

            loop.call_soon_threadsafe(_complete_result, result)

        worker = threading.Thread(
            target=_worker,
            name=f"xianyu-slider-{self.pure_user_id}",
            daemon=True,
        )
        worker.start()
        return await result_future

    async def _async_close_browser(self):
        """异步版本的清理方法（兼容性保留，实际清理由同步 run 方法完成）"""
        # 由于 async_run 现在调用同步的 run 方法，清理工作已经在 run 的 finally 中完成
        pass

def get_slider_stats():
    """获取滑块验证并发统计信息"""
    return concurrency_manager.get_stats()

if __name__ == "__main__":
    # 简单的命令行示例
    import sys
    if len(sys.argv) < 2:
        print("用法: python xianyu_slider_stealth.py <URL>")
        sys.exit(1)
    
    url = sys.argv[1]
    # 第三个参数可以指定 headless 模式，默认为 True（无头）
    headless = sys.argv[2].lower() == 'true' if len(sys.argv) > 2 else True
    slider = XianyuSliderStealth("test_user", enable_learning=True, headless=headless)
    try:
        success, cookies = slider.run(url)
        print(f"验证结果: {'成功' if success else '失败'}")
        if cookies:
            print(f"获取到 {len(cookies)} 个cookies")
    except Exception as e:
        print(f"验证异常: {e}")
