"""闲鱼出站代理的统一取值入口。

优先级：调用方显式传入的账号级代理 > 环境变量 XIANYU_GLOBAL_PROXY_URL（compose
注入 mihomo-home:7891，覆盖扫码登录/商品/订单/评价等所有无账号上下文的出站）。
凡目标域名是闲鱼/淘宝系（goofish.com / taobao / dingtalk）的出站必须走其一。
"""
import os
from typing import Dict, Optional


def get_global_proxy_url() -> Optional[str]:
    """全局闲鱼出站代理 URL（未配置时返回 None=直连）。"""
    return (os.getenv("XIANYU_GLOBAL_PROXY_URL") or "").strip() or None


def resolve_proxy_url(explicit: Optional[str] = None) -> Optional[str]:
    """显式代理优先，否则回退全局代理。"""
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    return get_global_proxy_url()


def requests_proxies(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    """requests/httpx 风格的 proxies 字典（httpx 也可直接用 URL 字符串）。"""
    if not proxy_url:
        return None
    return {"http://": proxy_url, "https://": proxy_url}


def playwright_proxy(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    """Playwright launch/context 的 proxy 参数。"""
    if not proxy_url:
        return None
    return {"server": proxy_url}
