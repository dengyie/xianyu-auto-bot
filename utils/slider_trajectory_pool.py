#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""滑块轨迹池管理 — 录制真实人类拖拽轨迹并回放，突破 WASM ML 检测"""

import hashlib
import json
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from loguru import logger


class SliderTrajectoryPool:
    """管理真机滑块轨迹的录制、存储、回放与轮转"""

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = str(Path(__file__).resolve().parent.parent / "trajectories")
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_per_cookie = 50
        self.min_pool_size = 5
        self.rotation_strategy = "lru"
        self.recorded_only_mode = False

    # ── cookie 子目录 ──────────────────────────────────────────
    def _cookie_dir(self, cookie_id: str) -> Path:
        d = self.base_dir / cookie_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── 保存 ──────────────────────────────────────────────────
    def save_trajectory(self, points: List[List[float]], cookie_id: str,
                        distance: float, success: bool, verify_url: str = "",
                        duration_ms: float = 0) -> Optional[str]:
        """持久化一条轨迹，返回文件名，超出上限时淘汰最旧的"""
        cdir = self._cookie_dir(cookie_id)
        existing = sorted(cdir.glob("trajectory_*.json"))
        if len(existing) >= self.max_per_cookie:
            for old in existing[: len(existing) - self.max_per_cookie + 1]:
                try:
                    os.remove(str(old))
                except Exception:
                    pass

        idx = len(existing) + 1
        fname = f"trajectory_{idx:03d}.json"
        fpath = cdir / fname
        data = {
            "cookie_id": cookie_id,
            "recorded_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "verify_url_hash": hashlib.md5(str(verify_url or "").encode()).hexdigest()[:8],
            "distance": round(float(distance), 1),
            "success": bool(success),
            "duration_ms": round(float(duration_ms), 1),
            "points": [[round(float(p[0]), 2), round(float(p[1]), 2), round(float(p[2]), 1)]
                       for p in points],
        }
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[TrajectoryPool] saved {fname} for {cookie_id} (dist={distance:.0f}px, ok={success})")
        return fname

    @classmethod
    def from_remote_recording(cls, points: List[List[float]], cookie_id: str,
                               distance: float, verify_url: str = "") -> Optional[str]:
        """快捷保存远程人工录制轨迹"""
        pool = cls()
        return pool.save_trajectory(points, cookie_id, distance, True, verify_url)

    # ── 加载 ──────────────────────────────────────────────────
    def _load_all(self, cookie_id: str) -> List[dict]:
        cdir = self._cookie_dir(cookie_id)
        records = []
        for fp in sorted(cdir.glob("trajectory_*.json")):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    d = json.load(f)
                d["_file"] = str(fp)
                records.append(d)
            except Exception as e:
                logger.debug(f"[TrajectoryPool] skip corrupt {fp}: {e}")
        return records

    def load_best_trajectory(self, cookie_id: str, target_distance: float,
                             distance_tolerance: float = 0.10) -> Optional[dict]:
        """按距离匹配最佳轨迹（优先成功过的），支持回退放宽 tolerance"""
        records = [r for r in self._load_all(cookie_id) if r.get("success")]
        if not records:
            records = self._load_all(cookie_id)
        if not records:
            return None

        def _best(tol):
            best, best_diff = None, float("inf")
            for r in records:
                d = abs(r["distance"] - target_distance)
                ratio = d / max(target_distance, 1.0)
                if ratio <= tol and d < best_diff:
                    best, best_diff = r, d
            return best

        for tol in (distance_tolerance, 0.15, 0.25, 0.40):
            best = _best(tol)
            if best:
                logger.info(f"[TrajectoryPool] best match for dist={target_distance:.0f}: "
                            f"loaded dist={best['distance']:.0f}px (tol={tol})")
                self._touch_last_used(cookie_id, best["_file"])
                return best
        return None

    def load_random_trajectory(self, cookie_id: str) -> Optional[dict]:
        """随机选取（防重放检测）"""
        records = self._load_all(cookie_id)
        if not records:
            return None
        r = random.choice(records)
        self._touch_last_used(cookie_id, r["_file"])
        return r

    # ── 轮转 ──────────────────────────────────────────────────
    def _last_used_path(self, cookie_id: str) -> Path:
        return self._cookie_dir(cookie_id) / "last_used.json"

    def _read_last_used(self, cookie_id: str) -> Dict[str, float]:
        p = self._last_used_path(cookie_id)
        if p.exists():
            try:
                with open(p, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _touch_last_used(self, cookie_id: str, filepath: str):
        lu = self._read_last_used(cookie_id)
        lu[os.path.basename(filepath)] = time.time()
        with open(self._last_used_path(cookie_id), "w") as f:
            json.dump(lu, f)

    def rotate_trajectory(self, cookie_id: str, target_distance: float) -> Optional[dict]:
        """LRU 轮转：选最久未用 + 距离匹配"""
        records = [r for r in self._load_all(cookie_id) if r.get("success")]
        if not records:
            return self.load_best_trajectory(cookie_id, target_distance)

        lu = self._read_last_used(cookie_id)
        scored = []
        for r in records:
            fname = os.path.basename(r["_file"])
            age = time.time() - lu.get(fname, 0)
            dist_err = abs(r["distance"] - target_distance) / max(target_distance, 1.0)
            score = age * 0.6 - dist_err * 0.4  # 越久未用越高分，距离越近越高分
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            best = scored[0][1]
            self._touch_last_used(cookie_id, best["_file"])
            return best
        return None

    # ── 统计 ──────────────────────────────────────────────────
    def get_pool_stats(self, cookie_id: str) -> dict:
        records = self._load_all(cookie_id)
        successful = [r for r in records if r.get("success")]
        return {
            "total": len(records),
            "successful": len(successful),
            "failed": len(records) - len(successful),
            "success_rate": len(successful) / len(records) if records else 0,
            "avg_duration_ms": (sum(r.get("duration_ms", 0) for r in records) / len(records)
                                if records else 0),
            "pool_ready": len(records) >= self.min_pool_size,
        }

    # ── 清理 ──────────────────────────────────────────────────
    def clean_stale(self, cookie_id: str = None, max_age_days: int = 7):
        """清理过期 / 无效轨迹"""
        cutoff = datetime.now() - timedelta(days=max_age_days)
        targets = [cookie_id] if cookie_id else [
            d.name for d in self.base_dir.iterdir() if d.is_dir()]
        for cid in targets:
            cdir = self._cookie_dir(cid)
            for fp in cdir.glob("trajectory_*.json"):
                try:
                    with open(fp, "r") as f:
                        d = json.load(f)
                    ts = datetime.strptime(d.get("recorded_at", "2000-01-01T00:00:00"),
                                           "%Y-%m-%dT%H:%M:%S")
                    if ts < cutoff or not d.get("success"):
                        os.remove(str(fp))
                        logger.info(f"[TrajectoryPool] cleaned: {fp}")
                except Exception:
                    pass


# 全局单例
trajectory_pool = SliderTrajectoryPool()
