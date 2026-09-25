"""Slider orchestrator: strict x5sec validation + remote/Drission fallbacks."""

from unittest import mock

from utils.slider_orchestrator import (
    extract_x5_cookies,
    has_x5_cookie,
    validate_slider_result,
)


def test_extracts_x5_cookie_variants():
    cookies = {
        "unb": "123",
        "x5sec": "ticket",
        "X5Step": "step",
        "foo_x5sec_bar": "embedded",
    }
    assert extract_x5_cookies(cookies) == {
        "x5sec": "ticket",
        "X5Step": "step",
        "foo_x5sec_bar": "embedded",
    }
    assert has_x5_cookie(cookies) is True


def test_visual_success_without_x5_is_failure():
    result = validate_slider_result(True, {"unb": "123", "cookie2": "abc"}, engine="playwright")
    assert result.success is False
    assert result.engine == "playwright"
    assert "未获取到 x5sec" in result.message
    assert result.x5_cookies == {}


def test_success_requires_x5_cookie():
    result = validate_slider_result(True, {"unb": "123", "x5sec": "ticket"}, engine="playwright")
    assert result.success is True
    assert result.cookies["x5sec"] == "ticket"
    assert result.x5_cookies == {"x5sec": "ticket"}


def test_remote_solver_runs_before_local_slider_when_configured():
    """远程求解优先于本地滑块；远程成功即短路（异步生产入口）。"""
    import asyncio

    from utils.slider_orchestrator import run_slider_async_with_fallback

    class _PrimarySlider:
        user_id = "remote_user"
        initial_cookies = "unb=remote_user; cookie2=old"
        headless = True

        async def solve(self, *_args, **_kwargs):
            raise AssertionError("remote success should short-circuit local slider")

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "data": {"cookies": {"unb": "remote_user", "x5sec": "remote_ticket"}},
            }

    with mock.patch("utils.slider_orchestrator.requests.post", return_value=_FakeResponse()) as post_mock:
        result = asyncio.run(
            run_slider_async_with_fallback(
                _PrimarySlider(),
                "https://example.com/punish?action=captcha",
                remote_enabled=True,
                remote_config=("https://remote.example/api/captcha/slider-solve", "secret"),
            )
        )

    assert result.success is True
    assert result.engine == "remote"
    assert result.x5_cookies == {"x5sec": "remote_ticket"}
    assert post_mock.call_args.kwargs["json"]["secret_key"] == "secret"


def test_drissionpage_fallback_can_recover_primary_failure():
    """主求解视觉通过但无 x5sec（严格判定拒）→ DrissionPage 兜底可翻盘。"""
    import asyncio

    from utils.slider_orchestrator import run_slider_async_with_fallback

    class _PrimarySlider:
        user_id = "fallback_user"
        initial_cookies = "unb=fallback_user; cookie2=old"
        headless = True

        async def solve(self, *_args, **_kwargs):
            return True, {"unb": "fallback_user"}

    class _FallbackHandler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_cookies(self, url, existing_cookies_str=None, cookie_id="unknown"):
            self.url = url
            self.existing_cookies_str = existing_cookies_str
            self.cookie_id = cookie_id
            return "unb=fallback_user; x5sec=fallback_ticket"

    result = asyncio.run(
        run_slider_async_with_fallback(
            _PrimarySlider(),
            "https://example.com/punish?action=captcha",
            fallback_enabled=True,
            handler_factory=_FallbackHandler,
        )
    )

    assert result.success is True
    assert result.engine == "drissionpage"
    assert result.x5_cookies == {"x5sec": "fallback_ticket"}


def test_async_solve_adapter_accepts_slider_solver_shape():
    import asyncio
    from utils.slider_orchestrator import run_slider_async_strict

    class _Solver:
        async def solve(self, url, **_kwargs):
            assert "punish" in url
            return True, {"unb": "1", "x5sec": "ok"}

    result = asyncio.run(
        run_slider_async_strict(_Solver(), "https://example.com/punish", engine="playwright")
    )
    assert result.success is True
    assert result.x5_cookies == {"x5sec": "ok"}


def test_cdp_endpoint_env_routes_to_solve_on_existing_page(monkeypatch):
    """XY_SLIDER_CDP_ENDPOINT 非空时走 CDP 模式（外部真实浏览器），engine 标 cdp。"""
    import asyncio
    from utils.slider_orchestrator import run_slider_async_strict

    class _Solver:
        async def solve_on_existing_page(self, cdp_endpoint, page_url):
            assert cdp_endpoint == "http://localhost:9222"
            assert "punish" in page_url
            return True, {"unb": "1", "x5sec": "cdp_ticket"}

    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://localhost:9222")
    # 本测试验证 CDP 路由语义，与网络连通性无关——预检固定可达
    async def _reachable(endpoint, timeout=3.0):
        return True

    monkeypatch.setattr("utils.slider_orchestrator.cdp_endpoint_reachable", _reachable)
    result = asyncio.run(
        run_slider_async_strict(_Solver(), "https://example.com/punish", engine="playwright")
    )
    assert result.success is True
    assert result.engine == "cdp"
    assert result.x5_cookies == {"x5sec": "cdp_ticket"}


def test_cdp_endpoint_empty_env_keeps_browser_mode(monkeypatch):
    """开关为空时保持原浏览器模式，即使 solver 恰好带 CDP 方法。"""
    import asyncio
    from utils.slider_orchestrator import run_slider_async_strict

    class _Solver:
        async def solve_on_existing_page(self, cdp_endpoint, page_url):  # pragma: no cover
            raise AssertionError("must not use CDP when endpoint empty")

        async def solve(self, url, **_kwargs):
            return True, {"x5sec": "browser_ticket"}

    monkeypatch.delenv("XY_SLIDER_CDP_ENDPOINT", raising=False)
    result = asyncio.run(
        run_slider_async_strict(_Solver(), "https://example.com/punish", engine="playwright")
    )
    assert result.success is True
    assert result.engine == "playwright"


def test_token_refresh_path_imports_orchestrator():
    from pathlib import Path

    src = Path("XianyuAutoAsync.py").read_text(encoding="utf-8")
    assert "run_slider_async_with_fallback" in src
    assert "strict_result.success and strict_result.cookies" in src
    # 失败分支不再是 return 之后的死代码
    fail_idx = src.find("slider_fail_v2")
    success_return = src.find("return cookies_str", src.find("slider cookie merge"))
    assert fail_idx > 0 and success_return > 0
    assert fail_idx > success_return


def test_cdp_preflight_unreachable_fails_fast(monkeypatch):
    """隧道不在线时快速失败（不挂 180s connect 超时），并给出可操作提示。"""
    import asyncio
    from utils.slider_orchestrator import _invoke_slider_async

    class _Solver:
        def __init__(self):
            self.called = False

        async def solve_on_existing_page(self, cdp, url):
            self.called = True
            return True, {"x5sec": "ok"}

    async def _unreachable(endpoint, timeout=3.0):
        return False

    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")
    monkeypatch.setattr("utils.slider_orchestrator.cdp_endpoint_reachable", _unreachable)

    solver = _Solver()
    ok, cookies = asyncio.run(_invoke_slider_async(solver, "https://example.com/punish"))
    assert (ok, cookies) == (False, None)
    assert solver.called is False


def test_cdp_preflight_reachable_calls_solve(monkeypatch):
    import asyncio
    from utils.slider_orchestrator import _invoke_slider_async

    class _Solver:
        def __init__(self):
            self.args = None

        async def solve_on_existing_page(self, cdp, url):
            self.args = (cdp, url)
            return True, {"x5sec": "ok"}

    async def _reachable(endpoint, timeout=3.0):
        return True

    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")
    monkeypatch.setattr("utils.slider_orchestrator.cdp_endpoint_reachable", _reachable)

    solver = _Solver()
    ok, cookies = asyncio.run(_invoke_slider_async(solver, "https://example.com/punish"))
    assert ok is True and cookies == {"x5sec": "ok"}
    assert solver.args == ("http://172.19.0.1:9222", "https://example.com/punish")


def test_cdp_mode_skips_drissionpage_fallback_async(monkeypatch):
    """CDP 模式下 DrissionPage 兜底默认跳过：同 cookie 同指纹必败，白耗资源。"""
    import asyncio
    from utils.slider_orchestrator import run_slider_async_with_fallback

    class _PrimarySlider:
        user_id = "cdp_user"

        async def solve_on_existing_page(self, cdp_endpoint, page_url):
            return False, None

    class _MustNotRun:
        def __init__(self, **kwargs):
            raise AssertionError("DrissionPage fallback must not run in CDP mode")

        def get_cookies(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("DrissionPage fallback must not run in CDP mode")

    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")
    monkeypatch.delenv("XY_SLIDER_DRISSION_FALLBACK", raising=False)

    result = asyncio.run(
        run_slider_async_with_fallback(
            _PrimarySlider(),
            "https://example.com/punish?action=captcha",
            handler_factory=_MustNotRun,
        )
    )
    assert result.success is False


def test_cdp_mode_drissionpage_fallback_env_override_async(monkeypatch):
    """显式设 XY_SLIDER_DRISSION_FALLBACK=1 可在 CDP 模式强制开启兜底。"""
    import asyncio
    from utils.slider_orchestrator import run_slider_async_with_fallback

    class _PrimarySlider:
        user_id = "cdp_user"

        async def solve_on_existing_page(self, cdp_endpoint, page_url):
            return False, None

    class _FallbackHandler:
        def __init__(self, **kwargs):
            pass

        def get_cookies(self, url, existing_cookies_str=None, cookie_id="unknown"):
            return "unb=cdp_user; x5sec=dp_ticket"

    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")
    monkeypatch.setenv("XY_SLIDER_DRISSION_FALLBACK", "1")

    result = asyncio.run(
        run_slider_async_with_fallback(
            _PrimarySlider(),
            "https://example.com/punish?action=captcha",
            handler_factory=_FallbackHandler,
        )
    )
    assert result.success is True
    assert result.engine == "drissionpage"
    assert result.x5_cookies == {"x5sec": "dp_ticket"}
