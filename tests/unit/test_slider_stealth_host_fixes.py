"""slider_stealth_mixins 潜伏 NameError 修复的回归测试（production review 批）。

背景：Mixin 提取与历史拆分曾在未测试路径留下未定义名 ——
- `_collect_process_tree` 引用 `subprocess` 但模块未导入（进程树收割静默降级）；
- `_page_has_keep_login_prompt` 调用已不存在的 `_mark_detached_runtime`
  （选择器循环在首个异常处中断，而非继续尝试）。
"""
import subprocess

import pytest

import utils.xianyu_slider_stealth  # noqa: F401  (host must load first; mixins import back from it)
from utils.slider_stealth_mixins import PasswordLoginMixin, SliderHarvestMixin


class _HarvestHost(SliderHarvestMixin):
    def __init__(self):
        self.pure_user_id = "tester"


class _LoginHost(PasswordLoginMixin):
    def __init__(self):
        self.pure_user_id = "tester"


def test_collect_process_tree_walks_descendants(monkeypatch):
    ps_output = "  100     1\n  105   100\n  106   105\n  200     1\n"

    def fake_check_output(argv, text=False, stderr=None):
        assert argv == ["ps", "-eo", "pid=,ppid="]
        return ps_output

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    tree = _HarvestHost()._collect_process_tree(100)
    assert tree == [100, 105, 106]


def test_collect_process_tree_degrades_to_root_when_ps_unavailable(monkeypatch):
    def boom(argv, text=False, stderr=None):
        raise FileNotFoundError("ps")

    monkeypatch.setattr(subprocess, "check_output", boom)
    assert _HarvestHost()._collect_process_tree(4242) == [4242]


def test_page_has_keep_login_prompt_continues_past_failing_selector():
    class _Element:
        def is_visible(self):
            return True

    class _Page:
        def __init__(self):
            self.calls = []

        def query_selector(self, selector):
            self.calls.append(selector)
            if selector == 'text=保持登录':
                raise RuntimeError("selector failed")
            return _Element()

    page = _Page()
    assert _LoginHost()._page_has_keep_login_prompt(page) is True
    assert page.calls == ['text=保持登录', 'text=不保持']
