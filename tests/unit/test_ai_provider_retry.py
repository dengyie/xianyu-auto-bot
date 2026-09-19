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


def test_retry_backoff_is_exponential(monkeypatch, engine):
    """2026-09-19 实测网关闪断窗口 >12s，固定 0.8s 间隔的 3 连重试全落在窗口里。"""
    sleeps = []

    def _fake_sleep(seconds):
        sleeps.append(seconds)

    def _once(settings, messages):
        raise RuntimeError("OpenAI Chat API响应缺少choices: 'choices' - body: {...}")

    monkeypatch.setattr(engine, "_invoke_provider_once", _once)
    monkeypatch.setattr(engine, "PROVIDER_RETRY_BACKOFF_SECONDS", 0.8)
    monkeypatch.setattr("ai_reply_engine.time.sleep", _fake_sleep)

    reply = engine._invoke_provider({"api_type": "openai"}, [{"role": "user", "content": "x"}])

    assert reply is None
    assert sleeps == [0.8, 1.6]


def _fake_response(monkeypatch, status_code, payload):
    import json as _json

    import ai_reply_engine as mod

    class _Resp:
        def __init__(self):
            self.status_code = status_code
            self.text = payload if isinstance(payload, str) else _json.dumps(payload, ensure_ascii=False)

        def json(self):
            if isinstance(payload, str):
                raise ValueError("not json")
            return payload

    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp())


def test_chat_api_200_without_choices_raises_body_in_error(monkeypatch, engine):
    """new-api 类网关上游闪断时返回 200 + 无 choices 的 JSON：错误必须带上
    body，否则日志只剩 KeyError('choices') 没有上下文（2026-09-19 22:38 实况）。"""
    monkeypatch.setattr(engine, "PROVIDER_RETRY_BACKOFF_SECONDS", 0)
    _fake_response(monkeypatch, 200, {"error": {"message": "quota exceeded for channel"}})
    calls = []

    def _once(settings, messages):
        calls.append(1)
        return engine._call_openai_chat_api(
            {"base_url": "https://cpa.example.com/v1", "api_key": "k", "model_name": "m"},
            [{"role": "user", "content": "x"}],
        )

    monkeypatch.setattr(engine, "_invoke_provider_once", _once)

    reply = engine._invoke_provider({"api_type": "openai"}, [{"role": "user", "content": "x"}])

    assert reply is None
    assert len(calls) == 3  # 200-缺choices 属瞬时故障，走重试


def test_chat_api_missing_choices_error_carries_body(monkeypatch, engine):
    _fake_response(monkeypatch, 200, {"error": {"message": "quota exceeded"}})

    with pytest.raises(Exception, match="缺少choices.*quota exceeded"):
        engine._call_openai_chat_api(
            {"base_url": "https://cpa.example.com/v1", "api_key": "k", "model_name": "m"},
            [{"role": "user", "content": "x"}],
        )
