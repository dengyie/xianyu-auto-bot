"""AI provider 统一重试（_invoke_provider）：上游瞬时 503/空回复不放弃生成。

实测 CPA 网关约 25% 瞬时失败；默认 3 次尝试、0.8s 间隔（测试置 0）。
"""
import pytest

from ai_reply_engine import AIReplyEngine, ProviderClientError


@pytest.fixture
def engine():
    return AIReplyEngine()


def _patch_once(monkeypatch, engine, outcomes):
    """outcomes: 依次弹出的结果——Exception 实例则抛出，None/空串视为空回复。"""
    calls = []

    def _once(settings, messages):
        calls.append(1)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(engine, "_invoke_provider_once", _once)
    monkeypatch.setattr(engine, "PROVIDER_RETRY_BACKOFF_SECONDS", 0)
    return calls


def test_retry_recovers_from_transient_exception(monkeypatch, engine):
    calls = _patch_once(monkeypatch, engine, [
        RuntimeError("503 Internal Server Error"),
        RuntimeError("503 Service Unavailable"),
        "有货的，拍下后会自动发送邀请码哦。",
    ])

    reply = engine._invoke_provider({"api_type": "openai"}, [{"role": "user", "content": "x"}])

    assert reply == "有货的，拍下后会自动发送邀请码哦。"
    assert len(calls) == 3


def test_retry_recovers_from_empty_reply(monkeypatch, engine):
    calls = _patch_once(monkeypatch, engine, [None, "  ", "ok"])

    reply = engine._invoke_provider({"api_type": "openai"}, [{"role": "user", "content": "x"}])

    assert reply == "ok"
    assert len(calls) == 3


def test_gives_up_after_max_attempts(monkeypatch, engine):
    calls = _patch_once(monkeypatch, engine, [
        RuntimeError("503"), RuntimeError("503"), RuntimeError("503"),
    ])

    reply = engine._invoke_provider({"api_type": "openai"}, [{"role": "user", "content": "x"}])

    assert reply is None
    assert len(calls) == 3


def test_success_on_first_attempt_stops_early(monkeypatch, engine):
    calls = _patch_once(monkeypatch, engine, ["直接成功"])

    reply = engine._invoke_provider({"api_type": "openai"}, [{"role": "user", "content": "x"}])

    assert reply == "直接成功"
    assert len(calls) == 1


def test_client_error_4xx_not_retried(monkeypatch, engine):
    calls = _patch_once(monkeypatch, engine, [ProviderClientError("401 Unauthorized")])

    reply = engine._invoke_provider({"api_type": "openai"}, [{"role": "user", "content": "x"}])

    assert reply is None
    assert len(calls) == 1
