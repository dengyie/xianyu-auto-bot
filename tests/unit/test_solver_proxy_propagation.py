"""账号代理必须传递到 slidex 求解器：密码登录浏览器从 solver.proxy_config
构建 launch proxy（xianyu_auth_recovery 创建 XianyuSliderStealth 后赋值）。
缺失时浏览器直连机房 IP，滑块被环境分硬拒（2026-09-13 21:05 实证）。
"""
import pytest

pytest.importorskip("slidex", reason="slidex 由 Dockerfile 单独安装")


def test_slider_stealth_consumes_account_proxy_config():
    from slidex.stealth import XianyuSliderStealth

    solver = XianyuSliderStealth(user_id="c1", enable_learning=False, headless=True)
    # 未赋值时默认无代理（保持旧行为）
    assert not solver._build_playwright_proxy_settings()

    solver.proxy_config = {"proxy_type": "http", "proxy_host": "mihomo-home", "proxy_port": 7891}
    settings = solver._build_playwright_proxy_settings()
    assert settings == {"server": "http://mihomo-home:7891"}

    solver.proxy_config = {"proxy_type": "socks5", "proxy_host": "p", "proxy_port": 1080,
                           "proxy_user": "u", "proxy_pass": "pw"}
    settings = solver._build_playwright_proxy_settings()
    assert settings == {"server": "socks5://p:1080", "username": "u", "password": "pw"}


def test_password_login_flow_propagates_proxy_config():
    """源契约：xianyu_auth_recovery 创建求解器后必须把账号 proxy_config 赋给它。"""
    from pathlib import Path

    src = Path("xianyu_auth_recovery.py").read_text(encoding="utf-8")
    assert "slider.proxy_config = dict(getattr(self, 'proxy_config', None) or {})" in src
