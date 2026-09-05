"""API 层共享纯函数（自 reply_server.py 下沉，P2-x #3）。

无状态、无 DB 依赖；reply_server 反向 re-export 保持既有引用面不变。
"""
from typing import Any, Dict, Optional
import json
import re


def mask_sensitive_text(text: Any) -> str:
    raw_text = str(text or '')
    masked_text = raw_text

    def _mask_match(match):
        prefix = match.group(1)
        secret = match.group(2)
        if len(secret) <= 8:
            masked = '***'
        else:
            masked = f"{secret[:3]}***{secret[-2:]}"
        return f"{prefix}{masked}"

    for pattern in SENSITIVE_FIELD_PATTERNS:
        masked_text = pattern.sub(_mask_match, masked_text)

    return masked_text

def mask_cookie_value(cookie_value: str) -> str:
    cookie_value = str(cookie_value or '')
    if not cookie_value:
        return ''
    if len(cookie_value) <= 16:
        return '***'
    return f"{cookie_value[:8]}...{cookie_value[-8:]}"

def mask_secret_value(secret_value: str) -> str:
    secret_value = str(secret_value or '')
    if not secret_value:
        return ''
    if len(secret_value) <= 8:
        return '***'
    return f"{secret_value[:2]}***{secret_value[-2:]}"

def safe_client_error(message: str = '操作失败，请稍后重试') -> str:
    return message

def normalize_order_status_value(status: Any) -> str:
    normalized = str(status or '').strip().lower()
    if not normalized:
        return 'unknown'
    return ORDER_STATUS_ALIASES.get(normalized, normalized)

def is_sales_eligible_order_status(status: Any) -> bool:
    return normalize_order_status_value(status) in SALES_ELIGIBLE_ORDER_STATUSES

def parse_order_amount_value(raw_amount: Any) -> Optional[float]:
    if raw_amount is None:
        return None

    amount_text = str(raw_amount).strip()
    if not amount_text or amount_text.lower() in {'none', 'null', 'nan'}:
        return None

    normalized = re.sub(r'[^\d.-]', '', amount_text)
    if normalized in {'', '-', '.', '-.'}:
        return None

    try:
        return float(normalized)
    except (TypeError, ValueError):
        return None

def format_sse_event(event_name: str, data: Dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


ORDER_STATUS_ALIASES = {
    'success': 'completed',
    'finished': 'completed',
    'pending_delivery': 'pending_ship',
    'delivered': 'shipped',
    'closed': 'cancelled',
    'refunded': 'cancelled',
    'canceled': 'cancelled',
    '处理中': 'processing',
    '待付款': 'pending_payment',
    '待发货': 'pending_ship',
    '部分发货': 'partial_success',
    '部分待收尾': 'partial_pending_finalize',
    '已发货': 'shipped',
    '已完成': 'completed',
    '退款中': 'refunding',
    '退款撤销': 'refund_cancelled',
    '已关闭': 'cancelled',
}


SALES_ELIGIBLE_ORDER_STATUSES = {
    'pending_ship',
    'partial_success',
    'partial_pending_finalize',
    'shipped',
    'completed',
}


SENSITIVE_FIELD_PATTERNS = [
    re.compile(r'((?:api[_-]?key|secret|token|cookie|password|proxy_pass)\s*[=:]\s*)([^\s,;]+)', re.IGNORECASE),
    re.compile(r'([?&](?:api[_-]?key|secret|token|cookie|password|proxy_pass)=)([^&\s]+)', re.IGNORECASE),
]
