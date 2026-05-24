#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NoCaptcha ?????? - ?????
???????????????????Y????? + ????
"""

import random
import math
from typing import List, Tuple
from loguru import logger


def generate_trajectory(
    distance: float,
    attempt: int = 1,
) -> List[Tuple[float, float, float]]:
    """
    ?????????
    
    ??: [(x, y, delay_ms), ...]  ???? + ???????(ms)
    x: ?????? X ????
    y: ?????? Y ????  
    delay_ms: ???????????
    """
    traj = []
    
    # === ????? (100-200ms) ===
    traj.append((0, 0, random.uniform(100, 200)))
    
    # === ???? ===
    steps = random.randint(10, 15)
    jitter = 2.0 + attempt * 0.8
    
    # ?????slow_start(0-20%) -> fast(20-60%) -> medium(60-85%) -> fine_tune(85-100%)
    for i in range(steps):
        p = (i + 1) / steps
        
        if p <= 0.20:
            # ?????
            t = p / 0.20
            eased = 0.02 + 0.13 * (t ** 1.8)
        elif p <= 0.60:
            # ?????
            t = (p - 0.20) / 0.40
            eased = 0.15 + 0.60 * t
        elif p <= 0.85:
            # ???
            t = (p - 0.60) / 0.25
            eased = 0.75 + 0.20 * t
        else:
            # ?????
            t = (p - 0.85) / 0.15
            eased = 0.95 + 0.05 * t
        
        x = distance * eased
        
        # Y???????????????+ ????
        drift = -0.5 - p * 4.0  # 0 -> -4.5px ????
        y = drift + math.sin(p * math.pi * random.uniform(1.5, 3.5)) * jitter * (0.4 + 0.6 * p)
        
        # ????
        if random.random() < 0.06:
            y += random.uniform(-jitter * 1.8, jitter * 1.8)
        
        # ????????? 30-40ms?????
        if i == 0:
            delay = random.uniform(35, 55)
        elif i >= steps - 2:
            delay = random.uniform(40, 60)
        elif random.random() < 0.07:
            delay = random.uniform(55, 75)  # ????
        else:
            delay = random.uniform(25, 45)
        
        traj.append((x, y, delay))
    
    # === ????? ===
    traj.append((distance, 0, random.uniform(50, 120)))
    
    total_ms = sum(d for _, _, d in traj)
    logger.debug(
        f"trajectory: dist={distance:.0f}px, steps={len(traj)}, "
        f"time={total_ms:.0f}ms, attempt={attempt}"
    )
    
    return traj


def trajectory_to_points(
    trajectory: List[Tuple[float, float, float]],
    start_x: float,
    start_y: float,
) -> List[Tuple[float, float, float]]:
    """???????????"""
    return [(start_x + x, start_y + y, d) for x, y, d in trajectory]


__all__ = ['generate_trajectory', 'trajectory_to_points']
