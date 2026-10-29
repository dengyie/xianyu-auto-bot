"""浏览器画像（指纹块）健康度统计与选择策略。

背景
----
`browser_profiles` 画像库（见 utils/slider_stealth_mixins 与 slidex.stealth）原本只有
"锁一个 index、永不换"的逻辑：账号第一次落盘 `profile_index` 之后就一直用同一套画像，
既不统计它的失败率，也不会因为持续被拒而换块。结果是某个画像一旦被风控降权，账号就
长期卡在坏块上反复失败（2026-10 那次连续一周 0 token 就是这个形态）。

本模块补齐两件事：
1. 统计：按 `profile_id` 累计每次滑块的成败（成功/失败 + 最近窗口），落盘为
   `trajectory_history/{uid}_profile_health.json`，不放内存、不随容器重启丢。
2. 选择：粘性优先当前画像（**不做随机轮换**，避免"同账号多台设备来回切换"信号），
   只有当当前画像在近期样本里明显变差、且存在明显更好的可用画像时才切换，并对被
   换下的坏块做指数退避冻结（retire），防止来回抖动。

评分口径
--------
`score` 用 EWMA（指数加权）跟踪每个画像块的最近成败：越新的结果权重越高，避免
一个月前的连续失败长期按住一个现在好用的块；同时用固定的先验均值做收缩，样本少时
不会因为一两次成功就冲到满分。之所以不用"最近 N 条"窗口：窗口边缘会把一段已经过去
的惩罚期失败一直算在账上，看起来永远很差。

换块需要同时满足三条：样本足够（MIN_SAMPLES）、平滑成功率低于 BAD_RATE、并且
**最近连续失败**达到 MIN_TRAILING_FAILS。最后一条是刻意的：EWMA 掉到阈值以下可能
只是几次零星失败叠加，单凭分数就换块会让画像在"看起来都一般"的几个块之间来回抖。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

STATE_SCHEMA = 2
STATE_SUFFIX = "_profile_health.json"

# Beta 先验：均值 0.4（保守），样本少时不至于被 1 次成功吹到 100%。
PRIOR_SUCCESS = 2.0
PRIOR_FAIL = 3.0
PRIOR_MEAN = PRIOR_SUCCESS / (PRIOR_SUCCESS + PRIOR_FAIL)
SHRINK_K = 4.0              # 向先验均值收缩的等效样本数

EWMA_ALPHA = 0.15           # 单次结果的权重；越小越平滑、越看重历史
RECENT_WINDOW = 40          # 仅用于报告/审计，保留最近多少次原始结果
MIN_SAMPLES = 8             # 观测样本不足时不判"坏块"
MIN_TRAILING_FAILS = 5      # 还必须"连续"失败这么多次，避免单次抖动就换块
BAD_RATE = 0.45             # 平滑成功率低于此值视为坏块
SWITCH_MARGIN = 0.08        # 目标画像必须比当前画像高出的分数
SWITCH_COOLDOWN_S = 6 * 3600      # 两次切换之间至少间隔，防抖动
RETIRE_BASE_S = 12 * 3600         # 坏块首次冻结时长，之后指数退避
RETIRE_MAX_S = 7 * 24 * 3600

# 画像库前 4 项是 Win32（120 台式/120 笔记本/119 台式/118 标准），后 2 项是 Mac。
# 运行时 UA 一律被改写成 Windows Chrome，Mac 画像会造成 platform/UA 不一致，
# 所以候选只取前 WIN32_PROFILE_LIMIT 个 index。
WIN32_PROFILE_LIMIT = 4

_LOCK = threading.Lock()


def state_path(base_dir: str, user_id: str) -> str:
    """画像健康度状态文件路径（与 *_success.json 同目录）。"""
    directory = str(base_dir or "trajectory_history")
    return os.path.join(directory, f"{user_id}{STATE_SUFFIX}")


def _empty_state() -> Dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "updated_at": 0.0,
        "profiles": {},
        "index_labels": {},
        "switch_log": [],
        "cooldown_until": 0.0,
    }


def load_state(path: str) -> Dict[str, Any]:
    """读取状态；文件缺失/损坏时返回空状态（绝不抛）。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception:
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    state = _empty_state()
    state.update(raw)
    if not isinstance(state.get("profiles"), dict):
        state["profiles"] = {}
    if not isinstance(state.get("index_labels"), dict):
        state["index_labels"] = {}
    if not isinstance(state.get("switch_log"), list):
        state["switch_log"] = []
    return state


def save_state(path: str, state: Dict[str, Any]) -> None:
    """原子落盘（先写临时文件再 replace），失败只记不抛。

    临时文件名带 pid + 线程号：web 进程与 bot 进程可能同时写同一个账号的统计，
    共用 ``xxx.tmp`` 会互相覆盖成半截 JSON。``os.replace`` 本身是原子的，
    各自换成自己的完整副本，最坏情况是后写覆盖先写（统计丢一条，不会损坏文件）。
    """
    directory = os.path.dirname(path) or "."
    tmp_path = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        os.makedirs(directory, exist_ok=True)
        state["schema"] = STATE_SCHEMA
        state["updated_at"] = time.time()
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _new_entry(ts: float) -> Dict[str, Any]:
    return {
        "success": 0,
        "fail": 0,
        "first_ts": ts,
        "last_success_ts": 0.0,
        "last_fail_ts": 0.0,
        "recent": [],
        "ewma": PRIOR_MEAN,
        "ewma_n": 0,
        "retire_count": 0,
        "retired_until": 0.0,
    }


def _apply_outcome(entry: Dict[str, Any], success: bool, ts: float) -> None:
    """把一次结果并进画像条目（累计计数 + EWMA + 审计用近窗）。"""
    if success:
        entry["success"] = int(entry.get("success") or 0) + 1
        entry["last_success_ts"] = max(float(entry.get("last_success_ts") or 0.0), ts)
    else:
        entry["fail"] = int(entry.get("fail") or 0) + 1
        entry["last_fail_ts"] = max(float(entry.get("last_fail_ts") or 0.0), ts)
    try:
        ewma = float(entry.get("ewma"))
    except (TypeError, ValueError):
        ewma = PRIOR_MEAN
    entry["ewma"] = EWMA_ALPHA * (1.0 if success else 0.0) + (1.0 - EWMA_ALPHA) * ewma
    entry["ewma_n"] = int(entry.get("ewma_n") or 0) + 1
    recent = list(entry.get("recent") or [])
    recent.append(1 if success else 0)
    entry["recent"] = recent[-RECENT_WINDOW:]


def record_outcome(
    path: str,
    profile_id: str,
    success: bool,
    *,
    index: Optional[int] = None,
    ts: Optional[float] = None,
) -> Dict[str, Any]:
    """累计一次滑块结果。返回更新后的 state（便于调用方直接复用）。"""
    profile_id = str(profile_id or "").strip()
    if not profile_id or profile_id in {"unknown", "unassigned"}:
        return load_state(path)
    now = float(ts if ts is not None else time.time())
    with _LOCK:
        state = load_state(path)
        profiles = state["profiles"]
        entry = profiles.get(profile_id)
        if not isinstance(entry, dict):
            entry = _new_entry(now)
            profiles[profile_id] = entry
        _apply_outcome(entry, success, now)
        if index is not None:
            try:
                entry["index"] = int(index)
                state["index_labels"][str(int(index))] = profile_id
            except (TypeError, ValueError):
                pass
        save_state(path, state)
        return state


def _recent_flags(entry: Dict[str, Any]) -> List[int]:
    flags = [1 if int(x) else 0 for x in (entry.get("recent") or [])]
    if len(flags) > RECENT_WINDOW:
        flags = flags[-RECENT_WINDOW:]
    return flags


def _smoothed(success: int, fail: int) -> float:
    return (success + PRIOR_SUCCESS) / (success + fail + PRIOR_SUCCESS + PRIOR_FAIL)


def recent_score(entry: Dict[str, Any]) -> float:
    """时间衰减后的平滑成功率（EWMA 向先验均值收缩）。"""
    try:
        ewma = float(entry.get("ewma"))
    except (TypeError, ValueError):
        ewma = PRIOR_MEAN
    weight = int(entry.get("ewma_n") or 0)
    return (ewma * weight + PRIOR_MEAN * SHRINK_K) / (weight + SHRINK_K)


def cumulative_score(entry: Dict[str, Any]) -> float:
    return _smoothed(int(entry.get("success") or 0), int(entry.get("fail") or 0))


def trailing_fails(entry: Dict[str, Any]) -> int:
    """最近连续失败次数（从末尾往前数）。"""
    count = 0
    for flag in reversed(_recent_flags(entry)):
        if flag:
            break
        count += 1
    return count


def sample_count(entry: Dict[str, Any]) -> int:
    return int(entry.get("ewma_n") or 0)


def _entry_for(state: Dict[str, Any], index: int) -> Dict[str, Any]:
    label = (state.get("index_labels") or {}).get(str(int(index)))
    if not label:
        return {}
    entry = (state.get("profiles") or {}).get(label)
    return entry if isinstance(entry, dict) else {}


def choose_index(
    state: Dict[str, Any],
    current_index: Optional[int],
    candidate_count: int,
    *,
    now: Optional[float] = None,
) -> Tuple[int, str, bool]:
    """挑一个画像 index。返回 ``(index, reason, changed)``。

    规则（粘性，不随机轮换）：
    - 当前画像没被判坏 → 原样保留；
    - 样本 < MIN_SAMPLES 或连续失败不足 MIN_TRAILING_FAILS → 保留（先攒数据/别被抖动骗）；
    - 当前画像平滑成功率 < BAD_RATE，且存在可用竞品比它高 SWITCH_MARGIN 分以上、
      且不在 cooldown 内 → 换块；被换下的块做指数退避冻结。
    """
    now = float(now if now is not None else time.time())
    limit = max(1, min(int(candidate_count or 0), WIN32_PROFILE_LIMIT))
    candidates = list(range(limit))

    if current_index is None or int(current_index) not in candidates:
        if not candidates:
            return 0, "no_candidate", False
        best = max(
            candidates,
            key=lambda i: (recent_score(_entry_for(state, i)), -sample_count(_entry_for(state, i))),
        )
        return best, "no_current", True

    current_index = int(current_index)
    current_entry = _entry_for(state, current_index)
    current_score = recent_score(current_entry)
    samples = sample_count(current_entry)
    streak = trailing_fails(current_entry)

    if samples < MIN_SAMPLES:
        return current_index, "warmup(%d/%d)" % (samples, MIN_SAMPLES), False
    if current_score >= BAD_RATE:
        return current_index, "keep(score=%.2f n=%d)" % (current_score, samples), False
    if streak < MIN_TRAILING_FAILS:
        # 分数已经很低但只是零星失败：等证据够了再动，避免一次网络抖动就换指纹
        return current_index, "hold(streak=%d/%d, score=%.2f)" % (streak, MIN_TRAILING_FAILS, current_score), False
    if now < float(state.get("cooldown_until") or 0.0):
        return current_index, "hold(cooldown, score=%.2f)" % current_score, False

    rivals = [
        i
        for i in candidates
        if i != current_index
        and float((_entry_for(state, i) or {}).get("retired_until") or 0.0) <= now
    ]
    if not rivals:
        return current_index, "keep(no_rival, score=%.2f)" % current_score, False

    best = max(
        rivals,
        key=lambda i: (recent_score(_entry_for(state, i)), -sample_count(_entry_for(state, i))),
    )
    best_score = recent_score(_entry_for(state, best))
    if best_score < current_score + SWITCH_MARGIN:
        return current_index, "keep(margin %.2f->%.2f)" % (current_score, best_score), False

    return best, "switch(bad %.2f streak=%d -> best %.2f)" % (current_score, streak, best_score), True


def apply_switch(
    state: Dict[str, Any],
    from_index: int,
    to_index: int,
    reason: str,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """记录一次切换：冻结坏块（指数退避）+ 设置 cooldown。"""
    now = float(now if now is not None else time.time())
    from_label = (state.get("index_labels") or {}).get(str(int(from_index)))
    to_label = (state.get("index_labels") or {}).get(str(int(to_index)))
    profiles = state.setdefault("profiles", {})
    if from_label and isinstance(profiles.get(from_label), dict):
        entry = profiles[from_label]
        entry["retire_count"] = int(entry.get("retire_count") or 0) + 1
        backoff = RETIRE_BASE_S * (2 ** (int(entry["retire_count"]) - 1))
        entry["retired_until"] = now + min(backoff, RETIRE_MAX_S)
    state["cooldown_until"] = now + SWITCH_COOLDOWN_S
    log = state.setdefault("switch_log", [])
    log.append(
        {
            "ts": now,
            "from_index": int(from_index),
            "to_index": int(to_index),
            "from": from_label or "",
            "to": to_label or "",
            "reason": reason,
        }
    )
    state["switch_log"] = log[-50:]
    return state


def label_for_index(state: Dict[str, Any], index: int) -> str:
    return str((state.get("index_labels") or {}).get(str(int(index))) or "")


def format_report(state: Dict[str, Any], *, title: str = "", now: Optional[float] = None) -> str:
    """把状态渲染成等宽表格，供日志/CLI 直接打印。"""
    now = float(now if now is not None else time.time())
    lines: List[str] = []
    if title:
        lines.append(title)
    profiles = dict(state.get("profiles") or {})
    valid = {k: v for k, v in profiles.items() if isinstance(v, dict)}
    if not valid:
        lines.append("（暂无画像统计数据）")
        return "\n".join(lines)
    cooldown_until = float(state.get("cooldown_until") or 0.0)
    lines.append(
        "%-30s %6s %6s %9s %9s %9s %8s  %-16s %s"
        % ("profile_id", "succ", "fail", "total", "cum_rate", "ewma_rate", "samples", "last", "state")
    )
    rows = sorted(
        valid.items(),
        key=lambda kv: -(int(kv[1].get("success") or 0) + int(kv[1].get("fail") or 0)),
    )
    for profile_id, entry in rows:
        succ = int(entry.get("success") or 0)
        fail = int(entry.get("fail") or 0)
        total = succ + fail
        cum = (succ / total * 100.0) if total else 0.0
        recent = recent_score(entry) * 100.0
        last_ts = max(float(entry.get("last_success_ts") or 0), float(entry.get("last_fail_ts") or 0))
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(last_ts)) if last_ts else "-"
        retired_until = float(entry.get("retired_until") or 0.0)
        status = "-"
        if retired_until > now:
            status = "冻结 %.1fh" % ((retired_until - now) / 3600.0)
        lines.append(
            "%-30s %6d %6d %9d %8.1f%% %8.1f%% %8d  %-16s %s"
            % (profile_id, succ, fail, total, cum, recent, sample_count(entry), last, status)
        )
    index_labels = state.get("index_labels") or {}
    if index_labels:
        mapping = ", ".join("%s=%s" % (k, v) for k, v in sorted(index_labels.items(), key=lambda kv: int(kv[0])))
        lines.append("index -> profile: " + mapping)
    if cooldown_until > now:
        lines.append(
            "换块冷却中至 %s（此期间即使判坏也保持当前画像）"
            % time.strftime("%Y-%m-%d %H:%M", time.localtime(cooldown_until))
        )
    switches = state.get("switch_log") or []
    if switches:
        lines.append("最近切换:")
        for item in switches[-5:]:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(item.get("ts") or 0)))
            lines.append(
                "  %s  %s -> %s  (%s)"
                % (stamp, item.get("from") or item.get("from_index"), item.get("to") or item.get("to_index"), item.get("reason"))
            )
    return "\n".join(lines)


def backfill_from_history(
    path: str,
    success_file: str,
    failure_file: str,
    *,
    index: Optional[int] = None,
) -> Dict[str, Any]:
    """用已有的 */success.json / failure.json 回填统计（仅在状态文件不存在时调用）。

    历史记录里带 ``profile_id`` 与 ``success``，时间戳是秒级 epoch。
    """
    if os.path.exists(path):
        return load_state(path)
    state = load_state(path)
    observations: List[Tuple[float, str, bool]] = []
    for file_path, default_success in ((success_file, True), (failure_file, False)):
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                records = json.load(handle)
        except Exception:
            continue
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            verification = record.get("verification_result") or {}
            profile_id = str(record.get("profile_id") or verification.get("profile_id") or "").strip()
            if not profile_id:
                continue
            success = record.get("success")
            success = bool(default_success if success is None else success)
            ts = record.get("timestamp")
            try:
                ts = float(ts) if ts is not None else time.time()
            except (TypeError, ValueError):
                ts = time.time()
            observations.append((ts, profile_id, success))
    # 按时间顺序回放，EWMA 才有"越新越重"的意义
    observations.sort(key=lambda item: item[0])
    for ts, profile_id, success in observations:
        entry = state["profiles"].get(profile_id)
        if not isinstance(entry, dict):
            entry = _new_entry(ts)
            state["profiles"][profile_id] = entry
        _apply_outcome(entry, success, ts)
    if index is not None:
        # 只有当历史里只出现过一个画像时，"当前 index 就是这个画像"才是确定事实。
        # 出现多个画像时猜哪个属于哪个 index 会污染映射，宁可等运行时观测再绑定
        # （record_outcome 的 index 参数来自页面实际下发的 profile_id）。
        distinct = [pid for pid, entry in state["profiles"].items() if isinstance(entry, dict)]
        if len(distinct) == 1:
            state["index_labels"][str(int(index))] = distinct[0]
    with _LOCK:
        save_state(path, state)
    return state


def _cli(argv: Optional[Iterable[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="打印浏览器画像（指纹块）健康度统计")
    parser.add_argument("user_id", help="账号 / pure_user_id")
    parser.add_argument(
        "--dir",
        default="trajectory_history",
        dest="base_dir",
        help="记录目录（默认 trajectory_history）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    path = state_path(args.base_dir, args.user_id)
    state = load_state(path)
    print(format_report(state, title=f"{args.user_id} 画像健康度（{path}）"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())


__all__ = [
    "BAD_RATE",
    "MIN_SAMPLES",
    "MIN_TRAILING_FAILS",
    "RECENT_WINDOW",
    "WIN32_PROFILE_LIMIT",
    "apply_switch",
    "backfill_from_history",
    "choose_index",
    "cumulative_score",
    "format_report",
    "label_for_index",
    "load_state",
    "recent_score",
    "record_outcome",
    "sample_count",
    "save_state",
    "state_path",
    "trailing_fails",
]
