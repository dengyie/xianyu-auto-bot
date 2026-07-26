"""Token 刷新等入口在自动滑块失败后的统一人工 captcha 收口。

设计目标（开源大众路径）：
1. Cookie 登录 + 自动滑块（严格 x5sec）+ 可选 Drission
2. 仍失败时打开与 /api/captcha 同单例的人工面板
3. 人工完成后再次强制校验 x5/x5sec，没有票据不算成功

不依赖 Solver 内部 _fallback_to_remote：orchestrator 在「视觉通过无 x5」时
会跳过 Solver human；且 solve() finally 会关掉浏览器。本模块自备 page。
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from loguru import logger

from utils.slider_orchestrator import (
    SliderVerificationResult,
    validate_slider_result,
)

HUMAN_ENGINE = "human_captcha"
NotificationCallback = Callable[[str, str], Awaitable[None]]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def resolve_captcha_public_host() -> str:
    """解析人工面板可达 host（优先 SERVER_HOST/PUBLIC_IP）。"""
    host = (os.getenv("SERVER_HOST") or os.getenv("PUBLIC_IP") or "").strip()
    if host:
        return host

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        host = sock.getsockname()[0]
        sock.close()
        if host.startswith("172.") or host.startswith("10.") or host.startswith("127."):
            return "localhost"
        return host or "localhost"
    except Exception:
        return "localhost"


def build_captcha_control_url(session_id: str, token: str = "") -> str:
    """构造完整人工控制 URL（含端口与 session token）。

    优先 ``CAPTCHA_PUBLIC_BASE_URL``（反代场景）；否则
    ``{scheme}://{host}:{API_PORT}/api/captcha/control/{session_id}?token=...``。
    """
    session_id = str(session_id or "").strip()
    token = str(token or "").strip()
    path = f"/api/captcha/control/{session_id}"
    query = f"?token={token}" if token else ""

    public_base = (os.getenv("CAPTCHA_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if public_base:
        return f"{public_base}{path}{query}"

    host = resolve_captcha_public_host()
    scheme = (os.getenv("CAPTCHA_CONTROL_SCHEME") or "http").strip() or "http"
    # host 已带端口时不再追加 API_PORT；IPv6 请用 CAPTCHA_PUBLIC_BASE_URL
    host_has_port = False
    if host.startswith("[") and "]" in host:
        host_has_port = host.split("]", 1)[-1].startswith(":")
    elif host.count(":") == 1:
        maybe_port = host.rsplit(":", 1)[-1]
        host_has_port = maybe_port.isdigit()

    if host_has_port:
        return f"{scheme}://{host}{path}{query}"

    port = _env_int("API_PORT", 8090)
    return f"{scheme}://{host}:{port}{path}{query}"


def _human_enabled() -> bool:
    raw = os.environ.get("XY_SLIDER_HUMAN_FALLBACK")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


async def _load_slider_solver_class():
    """优先与 token 刷新相同的 slidex 运行时。"""
    try:
        from slidex import SlidexConfig
        from slidex.solver import SliderSolver

        return SlidexConfig, SliderSolver, "slidex"
    except Exception:
        from utils.slider_solver import SliderSolver  # type: ignore

        return None, SliderSolver, "legacy"


async def run_human_captcha_session(
    *,
    cookie_id: str,
    cookies_str: str,
    verification_url: str,
    headless: bool = True,
    proxy: Optional[Mapping[str, Any]] = None,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    notification_callback: Optional[NotificationCallback] = None,
) -> SliderVerificationResult:
    """打开验证页并挂到 slidex.remote 人工面板，完成后强制 x5 校验。"""
    if not _human_enabled():
        return SliderVerificationResult(
            success=False,
            cookies=None,
            engine=HUMAN_ENGINE,
            x5_cookies={},
            message="人工 captcha 兜底已关闭 (XY_SLIDER_HUMAN_FALLBACK=0)",
        )

    verification_url = str(verification_url or "").strip()
    if not verification_url:
        return SliderVerificationResult(
            success=False,
            cookies=None,
            engine=HUMAN_ENGINE,
            x5_cookies={},
            message="缺少 verification_url，无法启动人工 captcha",
        )

    try:
        from slidex.remote import captcha_controller
    except Exception as import_e:
        return SliderVerificationResult(
            success=False,
            cookies=None,
            engine=HUMAN_ENGINE,
            x5_cookies={},
            message=f"slidex.remote 不可用: {import_e}",
        )

    timeout = float(timeout if timeout is not None else _env_float("SLIDEX_REMOTE_TIMEOUT", 180.0))
    poll_interval = float(
        poll_interval if poll_interval is not None else _env_float("SLIDEX_REMOTE_POLL", 2.0)
    )
    timeout = max(30.0, timeout)
    poll_interval = max(0.5, poll_interval)

    SlidexConfig, SliderSolver, runtime = await _load_slider_solver_class()
    solver = None
    session_id = ""
    try:
        kwargs: Dict[str, Any] = {
            "cookie_id": cookie_id,
            "cookies_str": cookies_str or "",
            "headless": headless,
            "proxy": dict(proxy or {}),
        }
        if SlidexConfig is not None:
            try:
                kwargs["config"] = SlidexConfig()
            except Exception:
                pass

        try:
            solver = SliderSolver(**kwargs)
        except TypeError:
            # legacy 构造签名可能不同
            solver = SliderSolver(
                cookie_id=cookie_id,
                cookies_str=cookies_str or "",
                headless=headless,
            )

        logger.info(f"[{cookie_id}] human captcha bootstrap via {runtime}")
        await solver._init_browser()  # noqa: SLF001 — 产品路径需要 live page
        await solver._load_page(verification_url)  # noqa: SLF001
        try:
            await solver._wait_slider()  # noqa: SLF001
        except Exception as wait_e:
            logger.warning(f"[{cookie_id}] wait slider soft-fail (仍启动人工面板): {wait_e}")

        if not getattr(solver, "page", None):
            return SliderVerificationResult(
                success=False,
                cookies=None,
                engine=HUMAN_ENGINE,
                x5_cookies={},
                message="人工 captcha 浏览器 page 未就绪",
            )

        session_id = f"human_{cookie_id}_{int(time.time())}"
        session_info = await captcha_controller.create_session(
            session_id,
            solver.page,
            cookie_id=str(cookie_id or "default"),
        )
        session_token = str((session_info or {}).get("token") or "")
        control_url = build_captcha_control_url(session_id, session_token)

        logger.warning("=" * 60)
        logger.warning(f"[{cookie_id}] 自动滑块失败，已启动人工 captcha 面板")
        logger.warning(f"[{cookie_id}] session={session_id}")
        logger.warning(f"[{cookie_id}] control_url={control_url}")
        logger.warning("=" * 60)

        if notification_callback is not None:
            try:
                await notification_callback(control_url, session_id)
            except Exception as notify_e:
                logger.warning(f"[{cookie_id}] human captcha 通知失败: {notify_e}")

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                completed = False
                if hasattr(captcha_controller, "check_completion"):
                    completed = bool(await captcha_controller.check_completion(session_id))
                if not completed and hasattr(captcha_controller, "is_completed"):
                    completed = bool(captcha_controller.is_completed(session_id))
                if completed:
                    cookies = {}
                    if hasattr(solver, "_get_cookies"):
                        cookies = await solver._get_cookies()  # noqa: SLF001
                    else:
                        try:
                            all_c = await solver.context.cookies()
                            cookies = {c["name"]: c["value"] for c in all_c}
                        except Exception:
                            cookies = {}

                    try:
                        if hasattr(captcha_controller, "finish_recording"):
                            captcha_controller.finish_recording(session_id)
                    except Exception:
                        pass
                    try:
                        await captcha_controller.close_session(session_id)
                    except Exception:
                        pass
                    session_id = ""

                    result = validate_slider_result(True, cookies, engine=HUMAN_ENGINE)
                    if result.success:
                        logger.success(f"[{cookie_id}] human captcha 通过且含 x5sec")
                    else:
                        logger.error(
                            f"[{cookie_id}] human captcha 完成但未拿到 x5sec: {result.message}"
                        )
                    return result
            except Exception as poll_e:
                logger.warning(f"[{cookie_id}] human captcha poll error: {poll_e}")
            await asyncio.sleep(poll_interval)

        return SliderVerificationResult(
            success=False,
            cookies=None,
            engine=HUMAN_ENGINE,
            x5_cookies={},
            message=f"人工 captcha 超时 ({int(timeout)}s)，session={session_id}",
        )
    except Exception as e:
        logger.error(f"[{cookie_id}] human captcha 异常: {e}")
        return SliderVerificationResult(
            success=False,
            cookies=None,
            engine=HUMAN_ENGINE,
            x5_cookies={},
            message=f"人工 captcha 异常: {e}",
        )
    finally:
        if session_id:
            try:
                await captcha_controller.close_session(session_id)
            except Exception:
                pass
        if solver is not None:
            try:
                if hasattr(solver, "close"):
                    await solver.close()
                elif hasattr(solver, "_close"):
                    await solver._close()  # noqa: SLF001
            except Exception as close_e:
                logger.debug(f"[{cookie_id}] human captcha solver close: {close_e}")
