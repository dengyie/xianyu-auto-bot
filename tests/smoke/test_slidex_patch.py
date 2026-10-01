"""Tests for utils/slidex_patch: human-like trajectory + idempotent patch."""

from __future__ import annotations

import builtins

from utils.slidex_patch import apply_slidex_patch, human_like_trajectory


def test_human_like_trajectory_reaches_target_with_overshoot():
    dist = 120.0
    traj = human_like_trajectory(dist, attempt=1)
    assert isinstance(traj, list) and len(traj) >= 20

    # 每个点都是 (dx, dy, delay_ms)，延迟为正
    for x, y, d in traj:
        assert isinstance(d, (int, float)) and d > 0
        assert isinstance(x, (int, float)) and isinstance(y, (int, float))

    # 起手停顿在原点
    assert traj[0][0] == 0.0 and traj[0][1] == 0.0

    xs = [p[0] for p in traj[1:]]
    # 出现过冲（存在 > dist 的点）
    assert max(xs) >= dist + 2.0
    # 末点（释放点）回拉校正到目标 ±2px 内
    assert abs(traj[-1][0] - dist) <= 2.0
    # 总时长落在真人量级（>500ms），而非 slidex 原实现 ~500-700ms 的机器人区间
    total_ms = sum(p[2] for p in traj)
    assert total_ms > 500


def test_human_like_trajectory_zero_distance_edge():
    traj = human_like_trajectory(0.0)
    assert isinstance(traj, list) and len(traj) >= 2
    assert all(d > 0 for _, _, d in traj)


def test_human_like_trajectory_attempt_scales_noise():
    # attempt 增大时轨迹应不同（噪声尺度随 attempt 增加），但形状约束不变
    a1 = human_like_trajectory(100.0, attempt=1)
    a3 = human_like_trajectory(100.0, attempt=3)
    assert a1 != a3
    assert abs(a1[-1][0] - 100.0) <= 2.0
    assert abs(a3[-1][0] - 100.0) <= 2.0


def test_apply_slidex_patch_is_idempotent():
    first = apply_slidex_patch()
    second = apply_slidex_patch()
    assert isinstance(first, bool)
    assert second == first


def test_apply_slidex_patch_replaces_slidex_generators():
    try:
        import slidex._provider_mixin
        import slidex.solver
    except Exception:  # slidex 未安装：直接跳过（补丁本身会静默降级）
        import pytest

        pytest.skip("slidex not installed")

    assert apply_slidex_patch() is True
    assert slidex.solver.generate_trajectory is human_like_trajectory
    assert slidex._provider_mixin.generate_trajectory is human_like_trajectory


def test_apply_slidex_patch_returns_false_when_slidex_missing(monkeypatch):
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "slidex.solver":
            raise ImportError("slidex not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert apply_slidex_patch() is False
