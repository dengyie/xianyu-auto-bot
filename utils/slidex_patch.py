"""slidex 运行时增强补丁：用更接近真人的滑块轨迹替换 slidex 的合成轨迹。

背景（诊断结论）：
- slidex 自带的 ``generate_trajectory``（slidex/_trajectory.py）在 X 轴上完全
  确定性（``x = distance * eased(p)``，零噪声），步长恒定 25~45ms、总时长
  ~500-700ms、单调无过冲回拉、末点精确落在 distance——是典型机器人信号。
- 闲鱼/阿里风控的主因通常是"设备指纹 + 出口 IP"的环境信任问题，本补丁无法
  解决那一层（见 utils/slider_orchestrator 注释）。它只在环境已放行的前提下，
  提高滑块本身的通过率、减少对人工兜底的依赖。

覆盖 slidex 的两条合成轨迹链路：
- legacy 模式：``slidex.solver.generate_trajectory``
- provider 模式（``provider="auto"`` 的实际路径）：``slidex._provider_mixin.generate_trajectory``

注意：provider 模式会优先回放轨迹池里的真人录制轨迹（见 slidex._provider_mixin），
本补丁只负责"池子里还没有真人轨迹时"的合成兜底。真人轨迹由人工面板拖拽后通过
``SliderTrajectoryPool.save_trajectory`` 落库（见 utils/slider_human_fallback）。

幂等、可重复调用；slidex 未安装 / 导入失败时静默跳过，不影响主流程。
"""

from __future__ import annotations

import random
from typing import List, Tuple


def human_like_trajectory(distance: float, attempt: int = 1) -> List[Tuple[float, float, float]]:
    """生成人类化滑动轨迹，返回 ``[(dx, dy, delay_ms), ...]``（相对位移 + 步间延迟）。

    与 slidex 原实现的关键差异：
    1. X 轴逐点叠加高斯噪声（原实现 X 上零噪声）；
    2. 末段"过冲-回拉校正"（真人常冲过头再拉回），末点不精确落在目标；
    3. 步间延迟呈"多数快、偶发停顿"分布，总时长更接近真人（~0.9-1.4s）；
    4. 步数更多（主滑 22~28 步，原 10~15 步）。

    返回的 ``delay_ms`` 单位为毫秒，与 slidex 原接口一致（legacy CDP 与 provider
    两条链路都按毫秒消费）。
    """
    dist = float(distance)
    if dist <= 0:
        return [(0.0, 0.0, 150.0), (0.0, 0.0, 60.0)]

    rng = random.Random()
    pts: List[Tuple[float, float, float]] = []

    # 起手停顿：按下滑块前的反应时间
    pts.append((0.0, 0.0, round(rng.uniform(120, 260), 1)))

    # ── 主滑动：先加速后减速的平滑曲线 + 逐点 X 噪声 ──
    main_steps = rng.randint(22, 28)
    # 失败重试时加大噪声，增加多样性（attempt 从 1 开始）
    noise_scale = max(1.0, dist * 0.012) * (1.0 + 0.15 * max(0, int(attempt) - 1))
    for i in range(1, main_steps + 1):
        p = i / main_steps
        # smoothstep 缓动，再乘轻微扰动避免过于规整
        eased = (3 * p * p - 2 * p * p * p) ** rng.uniform(0.92, 1.08)
        x = dist * eased + rng.gauss(0.0, noise_scale)
        x = max(0.0, min(dist, x))
        # Y 轴：整体轻微下漂 + 抖动
        y = (-0.4 - 0.8 * p) * rng.uniform(0.5, 1.5) + rng.gauss(0.0, 1.3)
        # 延迟：多数 18~38ms，偶发 55~120ms 停顿（手抖/思考）
        if rng.random() < 0.12:
            delay = rng.uniform(55, 120)
        else:
            delay = rng.uniform(18, 38)
        pts.append((round(x, 2), round(y, 2), round(delay, 1)))

    # ── 过冲：略超目标，模拟"滑过头" ──
    overshoot = rng.uniform(2.0, 6.0)
    pts.append((round(dist + overshoot, 2), round(rng.gauss(0.0, 1.6), 2), round(rng.uniform(40, 90), 1)))

    # ── 回拉校正：回到目标附近（末点是释放点，±1.5px 内） ──
    settle = rng.uniform(-1.5, 1.5)
    pts.append((round(dist + settle, 2), round(rng.gauss(0.0, 1.0), 2), round(rng.uniform(50, 130), 1)))
    return pts


_PATCH_FLAG = "__xianyu_slidex_trajectory_patched"


def apply_slidex_patch() -> bool:
    """幂等地把 slidex 的合成轨迹生成器替换为人类化版本。返回是否已生效。

    只改"合成轨迹"这一层；不触碰 slidex 的 stealth / 启动参数（改动风险高，
    且并非过风控的主因）。调用方在求解前调用一次即可，重复调用无副作用。
    """
    try:
        import slidex.solver as _solver
    except Exception:
        return False

    if getattr(_solver, _PATCH_FLAG, False):
        return True

    patched = False
    try:
        _solver.generate_trajectory = human_like_trajectory
        patched = True
    except Exception:
        pass

    try:
        import slidex._provider_mixin as _provider_mixin
        _provider_mixin.generate_trajectory = human_like_trajectory
        patched = True
    except Exception:
        pass

    if patched:
        try:
            setattr(_solver, _PATCH_FLAG, True)
        except Exception:
            pass
    return patched


__all__ = ["human_like_trajectory", "apply_slidex_patch"]
