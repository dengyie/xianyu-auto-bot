"""滑块验证编排与严格结果判定工具。

调用现有 XianyuSliderStealth 后，必须拿到 x5/x5sec 相关 Cookie 才认为
平台真正放行，避免“视觉通过但未下发 x5sec”被误当成功而导致 token
刷新死循环；同时提供可选远程服务与 DrissionPage 兜底入口。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import requests
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, Union

from loguru import logger


DEFAULT_SLIDER_ENGINE = "playwright"
DRISSIONPAGE_ENGINE = "drissionpage"
REMOTE_ENGINE = "remote"
_COOKIE_ATTR_NAMES = {"path", "domain", "expires", "max-age", "secure", "httponly", "samesite"}


@dataclass(frozen=True)
class SliderVerificationResult:
    """标准化滑块验证结果。"""

    success: bool
    cookies: Optional[Dict[str, Any]]
    engine: str
    x5_cookies: Dict[str, Any]
    message: str

    def as_legacy_tuple(self) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """兼容旧调用方的 ``(success, cookies)`` 返回格式。"""
        return self.success, self.cookies


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _remote_config_from_env() -> Tuple[str, str]:
    return (
        os.environ.get("XY_SLIDER_REMOTE_URL", "").strip(),
        os.environ.get("XY_SLIDER_REMOTE_SECRET", "").strip(),
    )


def parse_cookie_string(cookie_text: Optional[str]) -> Dict[str, str]:
    """解析 Cookie / Set-Cookie 字符串为字典。"""
    result: Dict[str, str] = {}
    if not cookie_text:
        return result

    # Set-Cookie 可能用逗号分隔多个 cookie；这里做保守解析，只取 name=value 片段。
    normalized_text = str(cookie_text).replace("\ufeff", "").replace(", ", "; ")
    for part in normalized_text.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key and key.lower() not in _COOKIE_ATTR_NAMES:
            result[key] = value.strip()
    return result


def extract_x5_cookies(cookies: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """提取 x5/x5sec 相关 Cookie。"""
    if not isinstance(cookies, Mapping):
        return {}

    result: Dict[str, Any] = {}
    for name, value in cookies.items():
        name_lower = str(name or "").lower()
        if name_lower.startswith("x5") or "x5sec" in name_lower:
            result[str(name)] = value
    return result


def has_x5_cookie(cookies: Optional[Mapping[str, Any]]) -> bool:
    """判断浏览器返回 Cookie 中是否包含真正放行用的 x5/x5sec 票据。"""
    return bool(extract_x5_cookies(cookies))


def validate_slider_result(
    success: bool,
    cookies: Optional[Union[Mapping[str, Any], str]],
    *,
    engine: Optional[str] = DEFAULT_SLIDER_ENGINE,
) -> SliderVerificationResult:
    """严格判定滑块结果。

    旧逻辑只要页面视觉上通过且返回了任意 Cookie 就可能进入成功分支。
    闲鱼/阿里风控下，视觉通过但没有下发 x5sec 时，后续 token 接口仍会
    继续返回验证要求。因此这里强制要求 x5/x5sec 相关 Cookie。
    """
    normalized_engine = str(engine or DEFAULT_SLIDER_ENGINE).strip() or DEFAULT_SLIDER_ENGINE
    if isinstance(cookies, str):
        normalized_cookies = parse_cookie_string(cookies)
    else:
        normalized_cookies = dict(cookies or {}) if isinstance(cookies, Mapping) else None
    x5_cookies = extract_x5_cookies(normalized_cookies)

    if not success:
        return SliderVerificationResult(
            success=False,
            cookies=None,
            engine=normalized_engine,
            x5_cookies={},
            message="滑块验证失败",
        )

    if not normalized_cookies:
        return SliderVerificationResult(
            success=False,
            cookies=None,
            engine=normalized_engine,
            x5_cookies={},
            message="滑块视觉通过但未返回 Cookie，平台可能未真正放行",
        )

    if not x5_cookies:
        return SliderVerificationResult(
            success=False,
            cookies=normalized_cookies,
            engine=normalized_engine,
            x5_cookies={},
            message=(
                "滑块视觉通过但未获取到 x5sec Cookie，判定为失败；"
                "常见原因是浏览器环境/IP 仍被风控拦截"
            ),
        )

    return SliderVerificationResult(
        success=True,
        cookies=normalized_cookies,
        engine=normalized_engine,
        x5_cookies=x5_cookies,
        message="滑块验证成功并获取到 x5sec Cookie",
    )


def _call_remote_solve(
    url: str,
    *,
    user_id: str,
    remote_url: str,
    remote_secret: str,
    timeout: int = 60,
) -> SliderVerificationResult:
    """调用远程过滑块服务，返回严格判定结果。"""
    try:
        response = requests.post(
            remote_url,
            json={
                "secret_key": remote_secret,
                "account_id": user_id,
                "url": url,
                "browser_timeout": timeout,
            },
            timeout=max(10, int(timeout or 60)),
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        data = data if isinstance(data, dict) else {}
        cookies = data.get("cookies") or data.get("x5_cookies") or data.get("cookie")
        success = bool(payload.get("success") if isinstance(payload, dict) else False)
        result = validate_slider_result(success, cookies, engine=REMOTE_ENGINE)
        if not result.success and isinstance(payload, dict) and payload.get("message"):
            return SliderVerificationResult(
                success=False,
                cookies=result.cookies,
                engine=REMOTE_ENGINE,
                x5_cookies=result.x5_cookies,
                message=str(payload.get("message")),
            )
        return result
    except Exception as exc:
        return SliderVerificationResult(
            success=False,
            cookies=None,
            engine=REMOTE_ENGINE,
            x5_cookies={},
            message=f"远程滑块服务不可用: {exc}",
        )


def _run_drissionpage_fallback(
    url: str,
    *,
    user_id: str,
    existing_cookies_str: str = "",
    headless: bool = True,
    max_retries: int = 3,
    handler_factory: Optional[Callable[..., Any]] = None,
) -> SliderVerificationResult:
    """运行 DrissionPage 兜底引擎并返回严格判定结果。"""
    try:
        if handler_factory is None:
            from utils.refresh_util import DrissionHandler
            handler_factory = DrissionHandler
        handler = handler_factory(
            max_retries=max_retries,
            is_headless=headless,
            maximize_window=not headless,
            show_mouse_trace=False,
        )
        raw_cookies = handler.get_cookies(url, existing_cookies_str=existing_cookies_str, cookie_id=user_id)
        return validate_slider_result(bool(raw_cookies), raw_cookies, engine=DRISSIONPAGE_ENGINE)
    except Exception as exc:
        return SliderVerificationResult(
            success=False,
            cookies=None,
            engine=DRISSIONPAGE_ENGINE,
            x5_cookies={},
            message=f"DrissionPage兜底引擎执行失败: {exc}",
        )


def _cdp_endpoint_from_env() -> str:
    """XY_SLIDER_CDP_ENDPOINT：非空时滑块走 CDP 模式（连接外部真实浏览器）。"""
    return (os.environ.get("XY_SLIDER_CDP_ENDPOINT", "") or "").strip()


def cdp_endpoint_from_env() -> str:
    """公开别名：token 浏览器侧重试等模块复用同一开关。"""
    return _cdp_endpoint_from_env()


async def cdp_endpoint_reachable(endpoint: str, timeout: float = 3.0) -> bool:
    """快速 TCP 探测 CDP 端点（反向隧道是否在线），解析失败视为可达（不拦截）。"""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(endpoint if "//" in endpoint else f"http://{endpoint}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
    except Exception:
        return True
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _invoke_slider_async(slider: Any, url: str, **kwargs: Any) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """兼容 slider.async_run / slider.solve 异步入口。

    XY_SLIDER_CDP_ENDPOINT 非空且 solver 支持时走 CDP：连接外部真实浏览器
    （用户 PC 上的 Chrome 经 `ssh -R 9222:localhost:9222` 反向隧道）。VPS 容器内
    headless Chromium 的设备指纹与账号历史登录设备不匹配，是 code=300 的主嫌疑
    （重扫码后新登录态 3s 内仍被拦、真人轨迹也被拒）。会话 cookie 由 slidex
    在 CDP 连接后注入，外部浏览器只出设备指纹与家宽出口。"""
    cdp = _cdp_endpoint_from_env()
    if cdp and hasattr(slider, "solve_on_existing_page"):
        # 预检：隧道不在线时 connect_over_cdp 会挂满 180s 超时，快速失败并给
        # 出可操作提示，把周期还给 fallback 链路。
        if not await cdp_endpoint_reachable(cdp):
            logger.warning(
                f"CDP 端点 {cdp} 不可达——用户 PC 上的反向隧道未在线。"
                f"请在用户 PC 运行仓库 scripts/cdp_tunnel.ps1（并确认 Chrome 调试端口已启动）"
            )
            return False, None
        return await slider.solve_on_existing_page(cdp, url)
    if hasattr(slider, "async_run") and callable(getattr(slider, "async_run")):
        return await slider.async_run(url, **kwargs)
    if hasattr(slider, "solve") and callable(getattr(slider, "solve")):
        result = slider.solve(url, **kwargs)
        if hasattr(result, "__await__"):
            return await result
        return result
    raise AttributeError("slider has neither async_run() nor solve()")


async def run_slider_async_strict(
    slider: Any,
    url: str,
    *,
    engine: Optional[str] = DEFAULT_SLIDER_ENGINE,
    **kwargs: Any,
) -> SliderVerificationResult:
    """调用异步 slider.async_run/solve，并进行严格 x5sec 判定。"""
    if _cdp_endpoint_from_env() and hasattr(slider, "solve_on_existing_page"):
        engine = "cdp"
    success, cookies = await _invoke_slider_async(slider, url, **kwargs)
    return validate_slider_result(success, cookies, engine=engine)


async def run_slider_async_with_fallback(
    slider: Any,
    url: str,
    *,
    engine: Optional[str] = DEFAULT_SLIDER_ENGINE,
    fallback_enabled: Optional[bool] = None,
    remote_enabled: Optional[bool] = None,
    remote_config: Optional[Tuple[str, str]] = None,
    remote_timeout: int = 60,
    fallback_headless: Optional[bool] = None,
    fallback_max_retries: int = 3,
    handler_factory: Optional[Callable[..., Any]] = None,
    **kwargs: Any,
) -> SliderVerificationResult:
    """异步版本：远程/Playwright 严格判定失败后可运行 DrissionPage 兜底。"""
    import asyncio

    user_id = str(getattr(slider, "user_id", None) or getattr(slider, "pure_user_id", None) or "unknown")

    use_remote = _env_bool("XY_SLIDER_REMOTE_ENABLED", False) if remote_enabled is None else bool(remote_enabled)
    remote_url, remote_secret = remote_config or _remote_config_from_env()
    if use_remote and remote_url and remote_secret:
        remote_result = await asyncio.to_thread(
            _call_remote_solve,
            url,
            user_id=user_id,
            remote_url=remote_url,
            remote_secret=remote_secret,
            timeout=remote_timeout,
        )
        if remote_result.success:
            return remote_result

    primary_result = await run_slider_async_strict(slider, url, engine=engine, **kwargs)
    if primary_result.success:
        return primary_result

    enabled = _env_bool("XY_SLIDER_DRISSION_FALLBACK", True) if fallback_enabled is None else bool(fallback_enabled)
    # CDP 模式下 DrissionPage 兜底是哑炮：容器内浏览器带同一账号 cookie，
    # 同样指纹不匹配，永远拿不到 x5sec（视觉通过也会被严格判定拒掉），
    # 白耗 ~30s 与内存。默认跳过；显式设 XY_SLIDER_DRISSION_FALLBACK=1 可强制开启。
    if (
        cdp_endpoint_from_env()
        and fallback_enabled is None
        and "XY_SLIDER_DRISSION_FALLBACK" not in os.environ
    ):
        if enabled:
            logger.info(
                "CDP 模式：跳过 DrissionPage 兜底（容器内浏览器同 cookie 同指纹，必然无 x5sec；"
                "显式设 XY_SLIDER_DRISSION_FALLBACK=1 可强制开启）"
            )
        return primary_result
    if not enabled:
        return primary_result

    existing_cookies_str = str(getattr(slider, "initial_cookies", "") or "")
    headless = bool(getattr(slider, "headless", True)) if fallback_headless is None else bool(fallback_headless)
    fallback_result = await asyncio.to_thread(
        _run_drissionpage_fallback,
        url,
        user_id=user_id,
        existing_cookies_str=existing_cookies_str,
        headless=headless,
        max_retries=fallback_max_retries,
        handler_factory=handler_factory,
    )
    return fallback_result if fallback_result.success else primary_result
