"""Slider orchestrator: strict x5sec validation + remote/Drission fallbacks."""

from unittest import mock

from utils.slider_orchestrator import (
    extract_x5_cookies,
    has_x5_cookie,
    run_slider_with_fallback,
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
    class _PrimarySlider:
        user_id = "remote_user"
        initial_cookies = "unb=remote_user; cookie2=old"
        headless = True

        def run(self, *_args, **_kwargs):
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
        result = run_slider_with_fallback(
            _PrimarySlider(),
            "https://example.com/punish?action=captcha",
            remote_enabled=True,
            remote_config=("https://remote.example/api/captcha/slider-solve", "secret"),
        )

    assert result.success is True
    assert result.engine == "remote"
    assert result.x5_cookies == {"x5sec": "remote_ticket"}
    assert post_mock.call_args.kwargs["json"]["secret_key"] == "secret"


def test_drissionpage_fallback_can_recover_primary_failure():
    class _PrimarySlider:
        user_id = "fallback_user"
        initial_cookies = "unb=fallback_user; cookie2=old"
        headless = True

        def run(self, *_args, **_kwargs):
            return True, {"unb": "fallback_user"}

    class _FallbackHandler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_cookies(self, url, existing_cookies_str=None, cookie_id="unknown"):
            self.url = url
            self.existing_cookies_str = existing_cookies_str
            self.cookie_id = cookie_id
            return "unb=fallback_user; x5sec=fallback_ticket"

    result = run_slider_with_fallback(
        _PrimarySlider(),
        "https://example.com/punish?action=captcha",
        fallback_enabled=True,
        handler_factory=_FallbackHandler,
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
