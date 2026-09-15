"""孤儿 Chrome/Chromium 进程回收看门狗（Orphan Browser Reaper）。

背景：密码登录/滑块验证链路曾因 Playwright greenlet 跨线程关闭失败，
在 1GB 内存的 HK VPS 上堆积 36 个孤儿 Chromium 进程，把物理内存耗尽、
1.42GB 数据挤入 Swap、系统 load 飙到 50+（大量进程 D 状态）。

本模块作为最后防线：任何代码路径（本仓库或第三方库）启动后失去管控的
Chromium 进程，都会被周期性扫描发现并强杀，防止内存被慢性耗尽。

判定为可回收的进程：
- Chromium 家族进程（chrome/chromium/chromium-browser/google-chrome*）
- 父进程已退出（孤儿）或父进程不是本应用进程树成员
- 与启动时间无关：孤儿即回收，无宽限期（父进程死了就是死了）

判定为保留的进程：
- 父进程存活且是本应用（python）进程——由业务代码自己的 finally 负责回收，
  看门狗不越权（避免误杀正在使用的活会话浏览器）
"""

import os
import time
from typing import List, Optional, Set

import psutil
from loguru import logger


CHROMIUM_PROC_NAMES = {
    'chrome', 'chromium', 'chromium-browser', 'google-chrome',
    'google-chrome-stable', 'headless_shell', 'headless-shell',
}


def is_chromium_process(proc_info_name: str) -> bool:
    """按进程名判断是否 Chromium 家族（兼容 Windows 的 .exe 后缀）。"""
    return (proc_info_name or '').lower().removesuffix('.exe') in CHROMIUM_PROC_NAMES


def _own_process_tree_pids(root: Optional[psutil.Process] = None) -> Set[int]:
    """收集当前 Python 进程自身的进程树 PID 集合（self + 祖先，**不含 PID 1**）。

    PID 1 必须排除：容器内 tini 是本进程祖先，但父进程死亡后的孤儿 Chrome
    会被内核 re-parent 到 PID 1——若把 1 计入归属，孤儿将被误判为"有主"，
    reaper 永远不会回收（生产容器 init:true 实况）。
    """
    try:
        me = root or psutil.Process(os.getpid())
        pids = {me.pid}
        parent = me.parent()
        # 沿祖先链上溯几层（容器里即 tini -> python），标记归属；PID 1 除外
        for _ in range(10):
            if parent is None or parent.pid <= 1:
                break
            pids.add(parent.pid)
            try:
                parent = parent.parent()
            except psutil.Error:
                break
        return pids
    except Exception:
        return set()


def find_orphan_chromium_pids(now: Optional[float] = None) -> List[int]:
    """扫描全系统 Chromium 进程，返回可安全回收的孤儿 PID 列表。

    孤儿定义：父进程已不存在（含父进程为 PID 1 且自身并非由本应用直启的
    情况，容器内 tini 收割后 re-parent 到 PID 1，同样视为孤儿）。
    """
    own_pids = _own_process_tree_pids()
    orphans: List[int] = []
    for proc in psutil.process_iter(['pid', 'name', 'ppid', 'create_time']):
        try:
            name = proc.info.get('name') or ''
            if not is_chromium_process(name):
                continue
            ppid = proc.info.get('ppid')
            # 排除当前应用进程树直系成员（有主，业务代码自行回收）
            # ⚠ PID 1 必须排除在 own_pids 之外：容器里 tini/init 是 python 的祖先，
            # 而父进程死亡后的孤儿会被 re-parent 到 PID 1——若把 1 计入归属，
            # reaper 会对真正的孤儿永远不作为（本仓库生产容器 init:true 的实况）。
            if ppid in own_pids and ppid != 1:
                continue
            parent_alive = False
            if ppid and ppid > 1:
                try:
                    parent = psutil.Process(ppid)
                    parent_alive = parent.is_running() and not parent.status() == psutil.STATUS_ZOMBIE
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    parent_alive = False
                except psutil.Error:
                    # 无法判断父进程状态时保守跳过，宁漏勿杀
                    continue
            if not parent_alive:
                orphans.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            continue
        except Exception as scan_err:
            logger.debug(f"[chrome-reaper] 扫描进程 {proc.info.get('pid') if proc.info else '?'} 出错: {scan_err}")
    return orphans


def kill_process_tree(pid: int) -> int:
    """按 PID 递归强杀进程树（先子后父），返回成功杀掉的进程数。"""
    killed = 0
    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return 0
    except Exception as e:
        logger.warning(f"[chrome-reaper] 检查 PID={pid} 失败: {e}")
        return 0

    try:
        children = proc.children(recursive=True)
    except Exception:
        children = []
    for child in children:
        try:
            child.kill()
            killed += 1
        except Exception:
            continue
    try:
        proc.kill()
        killed += 1
    except Exception:
        pass
    return killed


def reap_orphan_chromium() -> int:
    """执行一次孤儿 Chromium 回收，返回本次强杀的进程树数量。"""
    orphans = find_orphan_chromium_pids()
    if not orphans:
        return 0
    trees_killed = 0
    total_killed = 0
    for pid in orphans:
        n = kill_process_tree(pid)
        if n:
            trees_killed += 1
            total_killed += n
            logger.warning(f"[chrome-reaper] 强杀孤儿 Chromium 进程树 PID={pid}（{n} 个进程）")
    if trees_killed:
        logger.warning(f"[chrome-reaper] 本轮回收完成：{trees_killed} 棵进程树 / {total_killed} 个进程")
    return trees_killed


async def orphan_reaper_loop(interval_seconds: float = 600.0):
    """低频异步看门狗循环：每 10 分钟扫描回收一次孤儿 Chromium。"""
    import asyncio
    logger.info(f"[chrome-reaper] 孤儿 Chromium 看门狗已启动（每 {int(interval_seconds)} 秒扫描一次）")
    # 启动后先做一次快速首扫（延迟 90 秒，避开应用自身启动窗口内的正常浏览器）
    await asyncio.sleep(90)
    while True:
        try:
            reap_orphan_chromium()
        except Exception as e:
            logger.error(f"[chrome-reaper] 回收循环异常（不影响下次调度）: {e}")
        await asyncio.sleep(interval_seconds)
