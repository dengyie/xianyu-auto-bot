"""画像（指纹块）健康度统计与选择策略。

背景：账号曾长期锁在同一套浏览器画像上反复失败（一次侥幸成功后就永久复用，
不看失败率）。这里锁定三件事：
1. 统计口径 —— 按 profile_id 累计成败，近窗评分优先，样本不足不判坏块；
2. 选择口径 —— 粘性保留当前画像，只有近窗明显变差才换，坏了就指数退避冻结；
3. 回填与幂等 —— 能从历史 success/failure json 回填，且不会重复回填。
"""
from __future__ import annotations

import json
import os
import time

import pytest

from utils import slider_profile_health as health


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _entry(success=0, fail=0, recent=None, now=0.0):
    flags = list(recent if recent is not None else ([1] * success + [0] * fail))
    ewma = health.PRIOR_MEAN
    for flag in flags:
        ewma = health.EWMA_ALPHA * (1.0 if flag else 0.0) + (1.0 - health.EWMA_ALPHA) * ewma
    return {
        "success": success,
        "fail": fail,
        "first_ts": now,
        "last_success_ts": 0.0,
        "last_fail_ts": 0.0,
        "recent": flags[-health.RECENT_WINDOW:],
        "ewma": ewma,
        "ewma_n": len(flags),
        "retire_count": 0,
        "retired_until": 0.0,
    }


def _state(profiles, labels=None, cooldown=0.0):
    return {
        "schema": health.STATE_SCHEMA,
        "updated_at": 0.0,
        "profiles": dict(profiles),
        "index_labels": dict(labels or {}),
        "switch_log": [],
        "cooldown_until": cooldown,
    }


class TestRecordOutcome:
    def test_counts_success_and_failure(self, tmp_path):
        path = str(tmp_path / "u_profile_health.json")
        health.record_outcome(path, "win_chrome_119_1920x1200", True, index=2)
        state = health.record_outcome(path, "win_chrome_119_1920x1200", False, index=2)
        entry = state["profiles"]["win_chrome_119_1920x1200"]
        assert entry["success"] == 1
        assert entry["fail"] == 1
        assert entry["recent"] == [1, 0]
        assert state["index_labels"]["2"] == "win_chrome_119_1920x1200"

    def test_recent_window_is_capped(self, tmp_path):
        path = str(tmp_path / "u_profile_health.json")
        state = None
        for _ in range(health.RECENT_WINDOW + 10):
            state = health.record_outcome(path, "p", True)
        entry = state["profiles"]["p"]
        assert len(entry["recent"]) == health.RECENT_WINDOW
        assert entry["success"] == health.RECENT_WINDOW + 10

    @pytest.mark.parametrize("profile_id", ["", "unknown", "unassigned"])
    def test_placeholder_profile_ids_are_ignored(self, tmp_path, profile_id):
        path = str(tmp_path / "u_profile_health.json")
        state = health.record_outcome(path, profile_id, True)
        assert state["profiles"] == {}


class TestScoring:
    def test_recent_score_beats_stale_cumulative_failures(self):
        # 累计 45 失败、近窗 13 全成功 → 近窗评分应明显偏高（不长期按住现在好用的块）
        entry = _entry(success=13, fail=45, recent=[0] * 45 + [1] * 13)
        assert health.recent_score(entry) > 0.8
        assert health.cumulative_score(entry) < 0.3

    def test_unobserved_profile_scores_at_prior(self):
        assert health.recent_score({}) == pytest.approx(
            health.PRIOR_SUCCESS / (health.PRIOR_SUCCESS + health.PRIOR_FAIL)
        )


class TestChooseIndex:
    def test_warmup_keeps_current(self):
        state = _state({"a": _entry(success=1, fail=2)}, {"0": "a"})
        index, reason, changed = health.choose_index(state, 0, 4)
        assert (index, changed) == (0, False)
        assert "warmup" in reason

    def test_bad_block_switches_to_better_candidate(self):
        state = _state(
            {
                "bad": _entry(success=1, fail=12, recent=[0] * 12 + [1]),
                "good": _entry(success=10, fail=1, recent=[1] * 10 + [0]),
            },
            {"0": "bad", "3": "good"},
        )
        index, reason, changed = health.choose_index(state, 0, 4)
        assert changed is True
        assert index == 3
        assert "switch" in reason

    def test_margin_prevents_churn_when_candidates_are_close(self):
        state = _state(
            {
                "cur": _entry(success=4, fail=6, recent=[0] * 6 + [1] * 4),
                "rival": _entry(success=5, fail=5, recent=[0] * 5 + [1] * 5),
            },
            {"0": "cur", "1": "rival"},
        )
        index, reason, changed = health.choose_index(state, 0, 4)
        assert (index, changed) == (0, False)
        assert "keep" in reason

    def test_cooldown_holds_current(self):
        now = time.time()
        state = _state(
            {
                "bad": _entry(success=0, fail=12),
                "good": _entry(success=12, fail=0),
            },
            {"0": "bad", "1": "good"},
            cooldown=now + 3600,
        )
        index, reason, changed = health.choose_index(state, 0, 4, now=now)
        assert (index, changed) == (0, False)
        assert "cooldown" in reason

    def test_retired_block_is_not_a_switch_target(self):
        now = time.time()
        retired = _entry(success=12, fail=0)
        retired["retired_until"] = now + 3600
        state = _state(
            {"bad": _entry(success=0, fail=12), "retired": retired},
            {"0": "bad", "1": "retired"},
        )
        index, reason, changed = health.choose_index(state, 0, 2, now=now)
        assert changed is False
        assert index == 0
        assert "no_rival" in reason

    def test_candidate_range_excludes_mac_profiles(self):
        # 画像库后 2 项是 Mac，UA 会被改写成 Windows（platform 却是 MacIntel），
        # 一致性崩坏 → 只能从 Win32 的 4 个 index 里挑。
        state = _state({"bad": _entry(success=0, fail=12)}, {"0": "bad"})
        index, _, changed = health.choose_index(state, 0, 6)
        assert changed is True
        assert index < health.WIN32_PROFILE_LIMIT


class TestApplySwitch:
    def test_switch_retires_old_block_with_backoff_and_sets_cooldown(self):
        now = time.time()
        state = _state({"bad": _entry(success=0, fail=12), "good": _entry(success=12, fail=0)},
                       {"0": "bad", "1": "good"})
        state = health.apply_switch(state, 0, 1, "switch(test)", now=now)
        entry = state["profiles"]["bad"]
        assert entry["retire_count"] == 1
        assert entry["retired_until"] == pytest.approx(now + health.RETIRE_BASE_S)
        assert state["cooldown_until"] == pytest.approx(now + health.SWITCH_COOLDOWN_S)
        assert state["switch_log"][-1]["to_index"] == 1

    def test_backoff_doubles_and_caps(self):
        now = time.time()
        state = _state({"bad": _entry(success=0, fail=12), "good": _entry(success=12, fail=0)},
                       {"0": "bad", "1": "good"})
        for expected_multiplier in (1, 2, 4, 8):
            state["cooldown_until"] = 0.0
            state = health.apply_switch(state, 0, 1, "switch(test)", now=now)
            expected = min(health.RETIRE_BASE_S * expected_multiplier, health.RETIRE_MAX_S)
            assert state["profiles"]["bad"]["retired_until"] == pytest.approx(now + expected)
        state["cooldown_until"] = 0.0
        state = health.apply_switch(state, 0, 1, "switch(test)", now=now)
        assert state["profiles"]["bad"]["retired_until"] == pytest.approx(now + health.RETIRE_MAX_S)


class TestBackfill:
    def test_backfills_from_success_and_failure_history(self, tmp_path):
        base = tmp_path
        success_file = str(base / "1926782908_success.json")
        failure_file = str(base / "1926782908_failure.json")
        state_file = str(base / "1926782908_profile_health.json")
        _write_json(success_file, [
            {"profile_id": "win_chrome_119_1920x1200", "success": True, "timestamp": 100.0},
            {"profile_id": "win_chrome_119_1920x1200", "success": True, "timestamp": 200.0},
        ])
        _write_json(failure_file, [
            {"profile_id": "win_chrome_119_1920x1200", "success": False, "timestamp": 300.0},
        ])
        state = health.backfill_from_history(state_file, success_file, failure_file, index=2)
        entry = state["profiles"]["win_chrome_119_1920x1200"]
        assert entry["success"] == 2
        assert entry["fail"] == 1
        assert entry["recent"] == [1, 1, 0]
        assert state["index_labels"]["2"] == "win_chrome_119_1920x1200"

    def test_backfill_is_skipped_when_state_exists(self, tmp_path):
        base = tmp_path
        success_file = str(base / "u_success.json")
        failure_file = str(base / "u_failure.json")
        state_file = str(base / "u_profile_health.json")
        _write_json(success_file, [
            {"profile_id": "p", "success": True, "timestamp": 1.0},
        ])
        _write_json(state_file, _state({"p": _entry(success=7, fail=0)}))
        state = health.backfill_from_history(state_file, success_file, failure_file)
        assert state["profiles"]["p"]["success"] == 7

    def test_missing_history_files_do_not_raise(self, tmp_path):
        state_file = str(tmp_path / "u_profile_health.json")
        state = health.backfill_from_history(
            state_file, str(tmp_path / "none_success.json"), str(tmp_path / "none_failure.json")
        )
        assert state["profiles"] == {}


class TestReport:
    def test_report_lists_profiles_and_switches(self):
        state = _state({"win_chrome_119_1920x1200": _entry(success=13, fail=45)}, {"2": "win_chrome_119_1920x1200"})
        state["switch_log"] = [{"ts": 0.0, "from_index": 2, "to_index": 3, "from": "a", "to": "b", "reason": "switch(test)"}]
        text = health.format_report(state, title="t")
        assert "win_chrome_119_1920x1200" in text
        assert "index -> profile: 2=win_chrome_119_1920x1200" in text
        assert "switch(test)" in text

    def test_report_on_empty_state(self):
        assert "暂无" in health.format_report(_state({}))


class TestSlidexPatchIntegration:
    """用假 slidex.stealth 模块验证运行时 patch 真的接管了取画像与记结果。"""

    def _install_fake_slidex(self, monkeypatch, tmp_path, identity_index=0):
        import sys
        import types

        calls = {"identity": 0, "recorded": []}

        class XianyuSliderStealth:
            def __init__(self):
                self.pure_user_id = "u1"
                self.success_history_file = str(tmp_path / "u1_success.json")
                self.failure_history_file = str(tmp_path / "u1_failure.json")
                self.browser_profile_file = str(tmp_path / "u1_browser_profile.json")
                self.browser_identity = {"profile_index": identity_index, "language_index": 0}
                self.profile_id = "unknown"
                self.browser_features = {"profile_id": "unknown"}

            def _load_or_create_browser_identity(self, profile_count, language_count, profile_version=2):
                calls["identity"] += 1
                return dict(self.browser_identity)

            def _save_success_record(self, trajectory_data):
                calls["recorded"].append("success")

            def _save_failure_record(self, trajectory_data, failure_info):
                calls["recorded"].append("failure")

            def _get_random_browser_features(self):
                return {"profile_id": self.profile_id}

        module = types.ModuleType("slidex.stealth")
        module.XianyuSliderStealth = XianyuSliderStealth
        package = types.ModuleType("slidex")
        package.__path__ = []
        monkeypatch.setitem(sys.modules, "slidex", package)
        monkeypatch.setitem(sys.modules, "slidex.stealth", module)
        monkeypatch.delenv("XY_SLIDER_PROFILE_POLICY", raising=False)
        return XianyuSliderStealth, calls

    def test_patch_switches_bad_block_and_records_outcomes(self, monkeypatch, tmp_path):
        slider_cls, calls = self._install_fake_slidex(monkeypatch, tmp_path, identity_index=0)
        state_file = str(tmp_path / "u1_profile_health.json")
        _write_json(state_file, _state(
            {
                "bad": _entry(success=0, fail=12, recent=[0] * 12),
                "good": _entry(success=12, fail=0, recent=[1] * 12),
            },
            {"0": "bad", "3": "good"},
        ))

        from utils.slidex_patch import apply_slidex_profile_policy_patch

        assert apply_slidex_profile_policy_patch() is True
        slider = slider_cls()
        identity = slider._load_or_create_browser_identity(4, 3, 3)
        assert identity["profile_index"] == 3
        assert identity["profile_policy"]["from"] == 0
        # 换块结果写回了 browser_profile.json，供重启/legacy 链路复用
        with open(slider.browser_profile_file, "r", encoding="utf-8") as handle:
            persisted = json.load(handle)
        assert persisted["profile_index"] == 3
        # 坏块被冻结
        with open(state_file, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        assert saved["profiles"]["bad"]["retired_until"] > 0

        slider._save_success_record({"verification_result": {"profile_id": "good"}})
        slider._save_failure_record({"verification_result": {"profile_id": "good"}}, {})
        assert calls["recorded"] == ["success", "failure"]
        with open(state_file, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        assert saved["profiles"]["good"]["success"] == 13
        assert saved["profiles"]["good"]["fail"] == 1

    def test_patch_respects_kill_switch(self, monkeypatch, tmp_path):
        slider_cls, _ = self._install_fake_slidex(monkeypatch, tmp_path)
        monkeypatch.setenv("XY_SLIDER_PROFILE_POLICY", "0")
        from utils.slidex_patch import apply_slidex_profile_policy_patch

        assert apply_slidex_profile_policy_patch() is False
        # 原方法保持未被替换
        assert "patched" not in slider_cls._load_or_create_browser_identity.__qualname__
