"""MRO 完备性回归测试：防止 Mixin 拆分时静默丢失继承链成员或方法。

背景：P2 拆分期间 MRO 字符串替换曾把 TokenMixin 从 XianyuLive 继承链中
丢掉（被单元测试偶然抓住）。本文件把它变成确定性断言。
"""
import ast
from pathlib import Path

import pytest

MIXIN_FILES = [
    "xianyu_token_mixins.py",
    "xianyu_messaging_mixins.py",
    "xianyu_trading_mixins.py",
    "xianyu_auth_recovery.py",
    "xianyu_cookie_mixin.py",
    "xianyu_delivery_mixin.py",
]
MIXIN_CLASSES = [
    "TokenMixin", "MessagePipelineMixin", "SendMixin", "NotificationMixin",
    "OrderMixin", "ItemMixin", "XianyuAuthRecoveryMixin", "CookieMixin", "DeliveryMixin",
]
ROOT = Path(__file__).resolve().parents[2]


def _live_cls():
    import XianyuAutoAsync

    return XianyuAutoAsync.XianyuLive


def test_mro_contains_all_mixins():
    mro = {c.__name__ for c in _live_cls().__mro__}
    for name in MIXIN_CLASSES:
        assert name in mro, f"XianyuLive.__mro__ 缺少 {name}"


def test_mro_has_no_shadowing_duplicates():
    """同一方法名不得在多个 Mixin 中重复定义（MRO 靠前者会静默遮蔽后者）。"""
    seen = {}
    dups = []
    for fname in MIXIN_FILES:
        tree = ast.parse((ROOT / fname).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin"):
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if m.name in seen and seen[m.name] != fname:
                            dups.append((m.name, seen[m.name], fname))
                        seen[m.name] = fname
    assert not dups, f"跨 Mixin 重复定义: {dups}"


def test_all_mixin_methods_resolve_on_live():
    """每个 Mixin 中定义的方法都必须能从 XianyuLive 解析到（防 MRO 断链）。"""
    live = _live_cls()
    for fname in MIXIN_FILES:
        tree = ast.parse((ROOT / fname).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin"):
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        assert hasattr(live, m.name), f"{fname}: {m.name} 不可达"


def test_no_module_level_cycle_imports():
    """Mixin 模块不得在模块级 import XianyuAutoAsync（调用期惰性导入除外）。"""
    for fname in MIXIN_FILES:
        tree = ast.parse((ROOT / fname).read_text(encoding="utf-8"))
        for node in tree.body:
            assert not (isinstance(node, ast.Import)
                        and any(a.name == "XianyuAutoAsync" for a in node.names)), fname
            assert not (isinstance(node, ast.ImportFrom) and node.module == "XianyuAutoAsync"), fname


def test_slider_mixins_present():
    from utils.xianyu_slider_stealth import XianyuSliderStealth
    from utils.slider_stealth_mixins import PasswordLoginMixin, StealthScriptMixin

    mro = {c.__name__ for c in XianyuSliderStealth.__mro__}
    assert {"PasswordLoginMixin", "StealthScriptMixin"} <= mro
    # 代表性方法（各簇一个）必须可达
    for name in ("login_with_password_playwright", "_get_stealth_script", "solve_slider"):
        assert hasattr(XianyuSliderStealth, name), name
