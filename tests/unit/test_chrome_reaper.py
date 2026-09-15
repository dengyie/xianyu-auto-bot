"""chrome_reaper 孤儿 Chromium 回收看门狗单元测试。"""

import os
import sys
from unittest.mock import MagicMock, patch

import psutil
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils import chrome_reaper


def _proc(pid, name, ppid, status="running"):
    info = {"pid": pid, "name": name, "ppid": ppid, "create_time": 0.0}
    proc = MagicMock()
    proc.info = info
    proc.is_running.return_value = status != "zombie"
    proc.status.return_value = psutil.STATUS_ZOMBIE if status == "zombie" else psutil.STATUS_RUNNING
    return proc


class TestIsChromiumProcess:
    @pytest.mark.parametrize("name", ["chrome", "chrome.exe", "chromium", "chromium-browser",
                                      "google-chrome", "google-chrome-stable", "headless_shell"])
    def test_matches_chromium_family(self, name):
        assert chrome_reaper.is_chromium_process(name)

    @pytest.mark.parametrize("name", ["python", "python3.11", "node", "firefox", "msedge.exe", ""])
    def test_rejects_non_chromium(self, name):
        assert not chrome_reaper.is_chromium_process(name)


class TestFindOrphanChromiumPids:
    def test_orphan_chromium_detected(self):
        # Chromium 进程，父进程 PID 999（已死）
        orphan = _proc(500, "chrome", ppid=999)
        dead_parent = psutil.NoSuchProcess(999)

        def process_side_effect(pid):
            if pid == 999:
                raise dead_parent
            if pid == os.getpid():
                me = MagicMock()
                me.pid = os.getpid()
                me.parent.return_value = None
                return me
            raise psutil.NoSuchProcess(pid)

        with patch.object(psutil, "process_iter", return_value=[orphan]), \
             patch.object(psutil, "Process", side_effect=process_side_effect):
            result = chrome_reaper.find_orphan_chromium_pids()

        assert 500 in result

    def test_owned_chromium_preserved(self):
        # Chromium 进程的父进程就是当前 Python 进程（有主，业务自行回收）
        owned = _proc(501, "chrome", ppid=os.getpid())
        me = MagicMock()
        me.pid = os.getpid()
        me.parent.return_value = None

        def process_side_effect(pid):
            if pid == os.getpid():
                return me
            raise psutil.NoSuchProcess(pid)

        with patch.object(psutil, "process_iter", return_value=[owned]), \
             patch.object(psutil, "Process", side_effect=process_side_effect):
            result = chrome_reaper.find_orphan_chromium_pids()

        assert 501 not in result

    def test_alive_parent_chromium_preserved(self):
        # 父进程存活但不是本应用（例如用户自己开的 Chrome 由 explorer 拉起）——不越权
        # 注意：本用例父进程非 own tree，但存活，故不回收
        child = _proc(502, "chrome", ppid=300)
        parent = MagicMock()
        parent.is_running.return_value = True
        parent.status.return_value = psutil.STATUS_RUNNING

        def process_side_effect(pid):
            if pid == 300:
                return parent
            if pid == os.getpid():
                me = MagicMock()
                me.pid = os.getpid()
                me.parent.return_value = None
                return me
            raise psutil.NoSuchProcess(pid)

        with patch.object(psutil, "process_iter", return_value=[child]), \
             patch.object(psutil, "Process", side_effect=process_side_effect):
            result = chrome_reaper.find_orphan_chromium_pids()

        assert 502 not in result

    def test_zombie_parent_counts_as_orphan(self):
        child = _proc(503, "chromium", ppid=310)
        parent = MagicMock()
        parent.is_running.return_value = True
        parent.status.return_value = psutil.STATUS_ZOMBIE

        def process_side_effect(pid):
            if pid == 310:
                return parent
            if pid == os.getpid():
                me = MagicMock()
                me.pid = os.getpid()
                me.parent.return_value = None
                return me
            raise psutil.NoSuchProcess(pid)

        with patch.object(psutil, "process_iter", return_value=[child]), \
             patch.object(psutil, "Process", side_effect=process_side_effect):
            result = chrome_reaper.find_orphan_chromium_pids()

        assert 503 in result

    def test_container_reparented_to_pid1_is_orphan(self):
        """生产容器 init:true 场景：父进程死亡后孤儿 re-parent 到 PID 1（tini）。

        回归背景：初版把 PID 1 计入 own_pids（python 的祖先链含 tini），
        导致 re-parent 孤儿被误判为有主，reaper 在容器内永远不作为。
        """
        reparented = _proc(505, "chrome", ppid=1)

        def process_side_effect(pid):
            if pid == os.getpid():
                me = MagicMock()
                me.pid = os.getpid()
                # 容器内 python 的父进程就是 tini(PID 1)
                tini = MagicMock()
                tini.pid = 1
                me.parent.return_value = tini
                return me
            raise psutil.NoSuchProcess(pid)

        with patch.object(psutil, "process_iter", return_value=[reparented]), \
             patch.object(psutil, "Process", side_effect=process_side_effect):
            result = chrome_reaper.find_orphan_chromium_pids()

        assert 505 in result

    def test_own_tree_excludes_pid1(self):
        me = MagicMock()
        me.pid = 4242
        tini = MagicMock()
        tini.pid = 1
        me.parent.return_value = tini

        assert chrome_reaper._own_process_tree_pids(me) == {4242}

    def test_non_chromium_ignored(self):
        python_proc = _proc(504, "python3.11", ppid=999)

        def process_side_effect(pid):
            if pid == os.getpid():
                me = MagicMock()
                me.pid = os.getpid()
                me.parent.return_value = None
                return me
            raise psutil.NoSuchProcess(pid)

        with patch.object(psutil, "process_iter", return_value=[python_proc]), \
             patch.object(psutil, "Process", side_effect=process_side_effect):
            result = chrome_reaper.find_orphan_chromium_pids()

        assert result == []


class TestKillProcessTree:
    def test_kills_children_then_parent(self):
        parent = MagicMock()
        parent.name.return_value = "chrome"
        child = MagicMock()
        parent.children.return_value = [child]

        with patch.object(psutil, "Process", return_value=parent):
            killed = chrome_reaper.kill_process_tree(700)

        assert killed == 2
        child.kill.assert_called_once()
        parent.kill.assert_called_once()

    def test_missing_process_returns_zero(self):
        with patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(701)):
            assert chrome_reaper.kill_process_tree(701) == 0

    def test_non_chromium_root_skipped_pid_reuse_guard(self):
        """quit 后 PID 被回收复用为无关进程时不得误杀。"""
        parent = MagicMock()
        parent.name.return_value = "python3"

        with patch.object(psutil, "Process", return_value=parent):
            killed = chrome_reaper.kill_process_tree(702)

        assert killed == 0
        parent.kill.assert_not_called()

    def test_name_check_failure_skips_conservatively(self):
        """reaper 是旁观者：进程名无法核验时（可能已被 PID 复用）宁漏勿杀。"""
        parent = MagicMock()
        parent.name.side_effect = OSError("read failed")
        parent.children.return_value = []

        with patch.object(psutil, "Process", return_value=parent):
            killed = chrome_reaper.kill_process_tree(703)

        assert killed == 0
        parent.kill.assert_not_called()


class TestReapOrphanChromium:
    def test_reap_reports_killed_trees(self):
        with patch.object(chrome_reaper, "find_orphan_chromium_pids", return_value=[800, 801]), \
             patch.object(chrome_reaper, "kill_process_tree", side_effect=[3, 0]) as kill_mock:
            trees = chrome_reaper.reap_orphan_chromium()

        assert trees == 1
        assert kill_mock.call_count == 2

    def test_reap_no_orphans(self):
        with patch.object(chrome_reaper, "find_orphan_chromium_pids", return_value=[]):
            assert chrome_reaper.reap_orphan_chromium() == 0
