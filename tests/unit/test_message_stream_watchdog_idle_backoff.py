"""业务流"假在线"看门狗的空闲自适应退避。

背景：看门狗原先只有"距最近非心跳业务包 >= 固定 30 分钟"这一个判据，无法区分
"账号本来就没消息"和"连接半死"，于是空闲挂机号每 ~30 分钟被无谓重连一次并伴随
"疑似假在线"通知刷屏。

本模块锁定改进后的行为：
- 逐帧热路径 `_mark_stream_frame_arrival` 只刷新时间戳、**不复位退避**——服务端每次
  重连都会在数秒内重放历史引导包，若在此复位退避则退避恒为 0 档（线上实测）；
- 真实业务帧 `_mark_non_heartbeat_message` 才复位退避（账号转活跃立即恢复基准节奏）；
- 重连"事后举证"：窗口内无业务帧 → 退避 +1；有业务帧 → 复位；
- 有效阈值 = 基础阈值 × min(1+退避档位, 上限倍数)，空闲越久检测越稀疏；
- 会话引导消息按 (sessionId, arouseTimeStamp) 判重，重复重放直接忽略；
- 已判定空闲的账号默认不再发假在线通知；
- 阈值解析优先级 env > DB > RISK_CONTROL > 默认，并做最小值夹取。
"""
import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

import xianyu_messaging_mixins as mixins
from xianyu_messaging_mixins import MessagePipelineMixin


DEFAULT_SETTINGS = {
    'timeout_seconds': 3600,
    'max_backoff_multiplier': 4,
    'justify_window_seconds': 90,
    'notify_on_idle': False,
}


class _FakePipeline(MessagePipelineMixin):
    def __init__(self, **overrides):
        self.cookie_id = 'test_cid'
        self.last_non_heartbeat_message_time = 0
        self.last_sync_package_time = 0
        self.last_user_chat_time = 0
        self.stream_watchdog_trigger_times = deque(maxlen=8)
        self.stream_watchdog_idle_streak = 0
        self.stream_watchdog_pending_justify_deadline = 0
        self.last_stream_watchdog_reconnect_time = 0
        self.message_stream_notification_window = 3600
        self._stream_watchdog_settings = dict(DEFAULT_SETTINGS)
        self.sent_notifications = []
        for key, value in overrides.items():
            setattr(self, key, value)

    async def send_token_refresh_notification(self, message, notification_type):
        self.sent_notifications.append((notification_type, message))
        return True


# --------------------------------------------------------------------------
# 有效阈值
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    'idle_streak,expected',
    [(0, 3600), (1, 7200), (2, 10800), (3, 14400), (4, 14400), (9, 14400)],
)
def test_effective_timeout_grows_then_caps(idle_streak, expected):
    assert MessagePipelineMixin._stream_watchdog_effective_timeout(DEFAULT_SETTINGS, idle_streak) == expected


def test_effective_timeout_respects_configured_cap():
    settings = dict(DEFAULT_SETTINGS, max_backoff_multiplier=2)
    assert MessagePipelineMixin._stream_watchdog_effective_timeout(settings, 1) == 7200
    assert MessagePipelineMixin._stream_watchdog_effective_timeout(settings, 5) == 7200


def test_effective_timeout_tolerates_missing_settings():
    # 缺配置时退回内置默认,不抛异常
    assert MessagePipelineMixin._stream_watchdog_effective_timeout(None, 0) == 3600


# --------------------------------------------------------------------------
# 举证结算
# --------------------------------------------------------------------------

def test_settle_counts_idle_when_window_expired():
    p = _FakePipeline(stream_watchdog_pending_justify_deadline=1000)
    assert p._stream_watchdog_settle_previous_fire(1001) == 1
    assert p.stream_watchdog_pending_justify_deadline == 0


def test_settle_keeps_streak_while_window_open():
    p = _FakePipeline(stream_watchdog_pending_justify_deadline=1000)
    assert p._stream_watchdog_settle_previous_fire(999) == 0
    assert p.stream_watchdog_pending_justify_deadline == 1000


def test_settle_noop_without_pending_window():
    p = _FakePipeline()
    assert p._stream_watchdog_settle_previous_fire(10_000) == 0


# --------------------------------------------------------------------------
# 业务帧举证
# --------------------------------------------------------------------------

def test_business_frame_within_window_resets_streak():
    p = _FakePipeline(stream_watchdog_idle_streak=3, stream_watchdog_pending_justify_deadline=1000)
    p._mark_non_heartbeat_message(900)
    assert p.stream_watchdog_idle_streak == 0
    assert p.stream_watchdog_pending_justify_deadline == 0


def test_business_frame_outside_window_still_resets_streak():
    # 账号转为活跃后必须立即恢复基准检测，不能因窗口已过期而卡在最大退避
    p = _FakePipeline(stream_watchdog_idle_streak=3, stream_watchdog_pending_justify_deadline=1000)
    p._mark_non_heartbeat_message(1001)
    assert p.stream_watchdog_idle_streak == 0
    assert p.stream_watchdog_pending_justify_deadline == 0


def test_business_frame_records_activity_and_clears_trigger_window():
    p = _FakePipeline()
    p.stream_watchdog_trigger_times.extend([1.0, 2.0])
    p._mark_non_heartbeat_message(500, is_sync_package=True)
    assert p.last_non_heartbeat_message_time == 500
    assert p.last_sync_package_time == 500
    assert list(p.stream_watchdog_trigger_times) == []


# --------------------------------------------------------------------------
# 逐帧热路径:只刷新时间戳,不复位退避(重放引导包不得证明链路健康)
# --------------------------------------------------------------------------

def test_frame_arrival_refreshes_timestamps_without_resetting_backoff():
    p = _FakePipeline(
        stream_watchdog_idle_streak=3,
        stream_watchdog_pending_justify_deadline=1000,
        stream_watchdog_trigger_times=deque([1.0], maxlen=8),
    )
    p._mark_stream_frame_arrival(1200, is_sync_package=True)
    assert p.last_non_heartbeat_message_time == 1200   # 展示/诊断时间戳刷新
    assert p.last_sync_package_time == 1200
    assert p.stream_watchdog_idle_streak == 3          # 退避保持不变
    assert p.stream_watchdog_pending_justify_deadline == 1000
    assert list(p.stream_watchdog_trigger_times) == [1.0]


def test_replayed_guidance_frames_do_not_hold_backoff_at_zero():
    """回归线上 bug:空闲号每次重连后 3~8 秒到达重放引导包,逐帧热路径若复位退避,
    则 idle_streak 恒为 0、每 ~30 分钟无谓重连。改用 _mark_stream_frame_arrival 后,
    重放帧不再清零退避,结算得以推进。"""
    p = _FakePipeline(stream_watchdog_pending_justify_deadline=1000)
    p._mark_stream_frame_arrival(1003, is_sync_package=True)   # 重放引导帧落在窗口内
    assert p.stream_watchdog_idle_streak == 0
    # 窗口过期后结算:重放帧不能替这次重连举证成功
    assert p._stream_watchdog_settle_previous_fire(1200) == 1


# --------------------------------------------------------------------------
# 会话引导消息判重(按 sessionId + arouseTimeStamp)
# --------------------------------------------------------------------------

def test_guidance_dedupe_first_seen_then_duplicate():
    p = _FakePipeline()
    assert p._is_duplicate_session_guidance('60736454871', 1777110481086) is False
    assert p._is_duplicate_session_guidance('60736454871', 1777110481086) is True
    # 不同会话或不同时间戳视为新消息
    assert p._is_duplicate_session_guidance('60736454872', 1777110481086) is False
    assert p._is_duplicate_session_guidance('60736454871', 1777110481087) is False


def test_guidance_dedupe_ignores_missing_keys():
    # 旧格式/测试构造缺 sessionId 或 arouseTimeStamp 时不判重,始终按新消息处理
    p = _FakePipeline()
    assert p._is_duplicate_session_guidance(None, 123) is False
    assert p._is_duplicate_session_guidance(None, 123) is False
    assert p._is_duplicate_session_guidance('sid', '') is False
    assert p._is_duplicate_session_guidance('sid', '') is False


def test_guidance_dedupe_cache_is_bounded():
    p = _FakePipeline()
    for i in range(700):
        p._is_duplicate_session_guidance(f'sid-{i}', i)
    cache = p._session_guidance_seen
    assert len(cache) <= 512
    # 最近写入的键仍在缓存中(旧的被 FIFO 淘汰),重复到达可命中
    assert p._is_duplicate_session_guidance('sid-699', 699) is True


# --------------------------------------------------------------------------
# 通知降噪
# --------------------------------------------------------------------------

def test_notify_suppressed_for_known_idle_account():
    p = _FakePipeline(stream_watchdog_idle_streak=2)
    # 连续两次触发达到通知门槛，但已判定空闲且未开启 notify_on_idle
    asyncio.run(p._maybe_notify_message_stream_stale(100, 200, 100))
    asyncio.run(p._maybe_notify_message_stream_stale(200, 300, 200))
    assert p.sent_notifications == []


def test_notify_sent_when_not_idle():
    p = _FakePipeline()
    asyncio.run(p._maybe_notify_message_stream_stale(100, 200, 100))
    assert p.sent_notifications == []  # 首次单发不通知
    asyncio.run(p._maybe_notify_message_stream_stale(200, 300, 200))
    assert len(p.sent_notifications) == 1
    assert p.sent_notifications[0][0] == 'message_stream_stale'


def test_notify_sent_for_idle_when_explicitly_enabled():
    p = _FakePipeline(
        stream_watchdog_idle_streak=2,
        _stream_watchdog_settings=dict(DEFAULT_SETTINGS, notify_on_idle=True),
    )
    asyncio.run(p._maybe_notify_message_stream_stale(100, 200, 100))
    asyncio.run(p._maybe_notify_message_stream_stale(200, 300, 200))
    assert len(p.sent_notifications) == 1


# --------------------------------------------------------------------------
# 配置解析优先级 + 夹取
# --------------------------------------------------------------------------

class _StubDB:
    def __init__(self, values=None):
        self.values = values or {}

    def get_system_setting(self, key):
        return self.values.get(key)


@pytest.fixture
def resolver_env(monkeypatch):
    """默认：无 env、DB 空、RISK_CONTROL 空 → 内置默认。"""
    monkeypatch.setattr(mixins, '_host', SimpleNamespace(RISK_CONTROL={}))
    monkeypatch.setattr(mixins, '_db_package', lambda: _StubDB())
    for name in (
        'XY_MESSAGE_STREAM_WATCHDOG_TIMEOUT',
        'XY_MESSAGE_STREAM_WATCHDOG_MAX_MULTIPLIER',
        'XY_MESSAGE_STREAM_WATCHDOG_JUSTIFY_WINDOW',
        'XY_MESSAGE_STREAM_WATCHDOG_NOTIFY_ON_IDLE',
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_resolver_defaults(resolver_env):
    assert _FakePipeline()._resolve_message_stream_watchdog_settings() == DEFAULT_SETTINGS


def test_resolver_reads_risk_control(resolver_env):
    resolver_env.setattr(mixins, '_host', SimpleNamespace(RISK_CONTROL={
        'message_stream_watchdog_timeout_seconds': 900,
        'message_stream_watchdog_max_backoff_multiplier': 2,
        'message_stream_watchdog_justify_window_seconds': 30,
        'message_stream_watchdog_notify_on_idle': True,
    }))
    settings = _FakePipeline()._resolve_message_stream_watchdog_settings()
    assert settings == {
        'timeout_seconds': 900,
        'max_backoff_multiplier': 2,
        'justify_window_seconds': 30,
        'notify_on_idle': True,
    }


def test_resolver_db_overrides_risk_control(resolver_env):
    resolver_env.setattr(mixins, '_host', SimpleNamespace(RISK_CONTROL={
        'message_stream_watchdog_timeout_seconds': 900,
    }))
    resolver_env.setattr(mixins, '_db_package', lambda: _StubDB({
        'risk_control_message_stream_watchdog_timeout_seconds': '2400',
    }))
    assert _FakePipeline()._resolve_message_stream_watchdog_settings()['timeout_seconds'] == 2400


def test_resolver_env_overrides_db(resolver_env):
    resolver_env.setattr(mixins, '_db_package', lambda: _StubDB({
        'risk_control_message_stream_watchdog_timeout_seconds': '2400',
    }))
    resolver_env.setenv('XY_MESSAGE_STREAM_WATCHDOG_TIMEOUT', '600')
    assert _FakePipeline()._resolve_message_stream_watchdog_settings()['timeout_seconds'] == 600


def test_resolver_clamps_below_minimum(resolver_env):
    resolver_env.setenv('XY_MESSAGE_STREAM_WATCHDOG_TIMEOUT', '100')
    resolver_env.setenv('XY_MESSAGE_STREAM_WATCHDOG_MAX_MULTIPLIER', '0')
    resolver_env.setenv('XY_MESSAGE_STREAM_WATCHDOG_JUSTIFY_WINDOW', '1')
    settings = _FakePipeline()._resolve_message_stream_watchdog_settings()
    assert settings['timeout_seconds'] == 300
    assert settings['max_backoff_multiplier'] == 1
    assert settings['justify_window_seconds'] == 10


def test_resolver_notify_on_idle_from_env(resolver_env):
    resolver_env.setenv('XY_MESSAGE_STREAM_WATCHDOG_NOTIFY_ON_IDLE', 'true')
    assert _FakePipeline()._resolve_message_stream_watchdog_settings()['notify_on_idle'] is True


# --------------------------------------------------------------------------
# 端到端(离线模拟):空闲号重连节奏被拉长;有积压业务帧则维持基准节奏
# --------------------------------------------------------------------------

def _simulate_reconnects(*, horizon=60000, step=15, business_frames=(), replay_frames=()):
    """按真实看门狗分支条件驱动时间轴，返回各次强制重连的时刻。

    - business_frames: 真实业务帧到达时刻 → 走 _mark_non_heartbeat_message(复位退避);
    - replay_frames:   重连后重放的引导/同步帧 → 走 _mark_stream_frame_arrival(不复位)。
    """
    p = _FakePipeline()
    settings = dict(DEFAULT_SETTINGS)
    p._stream_watchdog_settings = settings
    p.last_successful_connection = 0.0
    p.last_non_heartbeat_message_time = 0.0
    p.last_heartbeat_response = 0.0
    grace = 120
    heartbeat_stale = 45
    pending_business = deque(business_frames)
    pending_replay = deque(replay_frames)

    now = 0.0
    reconnects = []
    while now < horizon:
        now += step
        p.last_heartbeat_response = now  # 心跳始终正常
        if now - p.last_successful_connection < grace:
            continue
        idle_streak = p._stream_watchdog_settle_previous_fire(now)
        effective = p._stream_watchdog_effective_timeout(settings, idle_streak)
        if now - p.last_heartbeat_response > heartbeat_stale:
            continue
        business_idle = now - (p.last_non_heartbeat_message_time or p.last_successful_connection)
        if business_idle < effective:
            continue
        last_reconnect = p.last_stream_watchdog_reconnect_time
        if last_reconnect and now - last_reconnect < effective / 2:
            continue
        p.last_stream_watchdog_reconnect_time = now
        p.stream_watchdog_pending_justify_deadline = now + settings['justify_window_seconds']
        reconnects.append(now)
        # 模拟重连后的重放帧(不举证)与真实业务帧(举证)
        while pending_replay and pending_replay[0] <= p.stream_watchdog_pending_justify_deadline:
            p._mark_stream_frame_arrival(pending_replay.popleft(), is_sync_package=True)
        while pending_business and pending_business[0] <= p.stream_watchdog_pending_justify_deadline:
            p._mark_non_heartbeat_message(pending_business.popleft(), is_sync_package=True)
    return reconnects


def test_idle_account_reconnect_cadence_lengthens():
    reconnects = _simulate_reconnects()
    intervals = [round(b - a) for a, b in zip(reconnects, reconnects[1:])]
    # 首次仍按基础 60 分钟触发,随后逐步拉长
    assert reconnects[0] == 3600
    assert intervals and max(intervals) > 3600
    # 相同时间窗内重连次数应显著少于"固定基础阈值"的基线
    baseline = len(range(3600, 60000, 3600))
    assert len(reconnects) < baseline


def test_replayed_frames_alone_do_not_justify_reconnect():
    """重放帧落在举证窗口内也不算"确有积压":退避照常推进,重连节奏被拉长。"""
    replays = [t + 5 for t in range(3600, 60000, 3600)]
    reconnects = _simulate_reconnects(replay_frames=replays)
    intervals = [round(b - a) for a, b in zip(reconnects, reconnects[1:])]
    assert intervals and max(intervals) > 3600


def test_justified_reconnect_keeps_baseline_cadence():
    # 每次重连后 30 秒内都有真实业务帧 → 举证成立,退避始终复位
    frames = [t + 30 for t in range(3600, 60000, 3600)]
    reconnects = _simulate_reconnects(business_frames=frames)
    intervals = [round(b - a) for a, b in zip(reconnects, reconnects[1:])]
    assert intervals, 'expected repeated reconnects'
    assert all(interval <= 3700 for interval in intervals)
