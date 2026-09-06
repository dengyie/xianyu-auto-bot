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


# ── P1 closeout: pure helpers + constants extracted from reply_server ──
from datetime import datetime
from fastapi import HTTPException
from pydantic import BaseModel
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
import os
import re


def _dedupe_int_list(values: List[Any], field_label: str) -> List[int]:
    result: List[int] = []
    for value in values or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    if not result:
        raise HTTPException(status_code=400, detail=f"{field_label}不能为空")
    return result


def _dedupe_str_list(values: List[Any], field_label: str) -> List[str]:
    result: List[str] = []
    for value in values or []:
        text = str(value or '').strip()
        if not text:
            continue
        if text not in result:
            result.append(text)
    if not result:
        raise HTTPException(status_code=400, detail=f"{field_label}不能为空")
    return result


def _parse_enabled_flag(value):
    """将不同类型的 enabled 入参统一转换为 0/1"""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) else 0
    if isinstance(value, str):
        return 1 if value.strip().lower() in {'1', 'true', 'yes', 'on'} else 0
    return 1 if value else 0


def _parse_form_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on", "y"}


def _parse_random_delay(value, default=10):
    random_delay_max = default if value is None else int(value)
    if random_delay_max < 0:
        raise ValueError("随机分钟不能小于 0")
    return random_delay_max


def _parse_run_hour(value, default=8):
    run_hour = default if value is None else int(value)
    if run_hour < 0 or run_hour > 23:
        raise ValueError("运行时间必须在 0-23 之间")
    return run_hour


def _model_to_dict(model: BaseModel, *, exclude_unset: bool = False) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=exclude_unset)
    return model.dict(exclude_unset=exclude_unset)


def _find_first_nested_value(payload: Any, keys: List[str]) -> Any:
    """从闲鱼待评价列表项中尽量提取字段。"""
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and payload[key] not in (None, ''):
                return payload[key]
        for value in payload.values():
            found = _find_first_nested_value(value, keys)
            if found not in (None, ''):
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_first_nested_value(value, keys)
            if found not in (None, ''):
                return found
    return None


def _extract_merchant_rate_item_meta(item: Dict[str, Any]) -> Dict[str, str]:
    return {
        'item_id': str(_find_first_nested_value(item, ['itemId', 'item_id', 'auctionId', 'auction_id']) or '').strip(),
        'buyer_id': str(_find_first_nested_value(item, ['buyerId', 'buyer_id', 'buyerUserId', 'userId']) or '').strip(),
        'buyer_nick': str(_find_first_nested_value(item, ['buyerNick', 'buyer_nick', 'buyerName', 'nick', 'userNick']) or '').strip(),
    }


def _extract_merchant_rate_order_id(item: Dict[str, Any]) -> str:
    return str(_find_first_nested_value(item, [
        'orderId', 'tradeId', 'bizOrderId', 'biz_order_id', 'order_id', 'trade_id'
    ]) or '').strip()


def _normalize_task_log_limit(limit: int) -> int:
    try:
        return max(1, min(int(limit or 100), 500))
    except Exception:
        return 100


def _normalize_task_log_offset(offset: int) -> int:
    try:
        return max(0, int(offset or 0))
    except Exception:
        return 0


def _normalize_task_log_row(log: Dict[str, Any], task_type: str, task_label: str = None) -> Dict[str, Any]:
    normalized = dict(log or {})
    normalized['task_type'] = task_type
    normalized['task_label'] = task_label or TASK_LOG_TYPE_LABELS.get(task_type, task_type)
    normalized.setdefault('object_id', normalized.get('order_id') or normalized.get('item_id') or normalized.get('session_id') or '')
    normalized.setdefault('status', 'failed')
    normalized.setdefault('message', '')
    normalized.setdefault('created_at', normalized.get('updated_at') or '')
    return normalized


def _task_log_created_at_sort_value(log: Dict[str, Any]) -> float:
    value = log.get('created_at') or log.get('updated_at') or ''
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value or '').strip()
        if not text:
            return 0.0
        normalized = text.replace('T', ' ')[:19]
        return datetime.strptime(normalized, '%Y-%m-%d %H:%M:%S').timestamp()
    except Exception:
        return 0.0


def _estimate_base64_bytes(value: str) -> int:
    text = str(value or '').strip()
    if not text:
        return 0
    if ',' in text and text.lower().startswith('data:'):
        text = text.split(',', 1)[1]
    text = re.sub(r'\s+', '', text)
    padding = text.count('=')
    return max(0, (len(text) * 3) // 4 - padding)


def _sanitize_material_images(images: List[Any], *, require_images: bool = True) -> List[Dict[str, Any]]:
    """素材落库前规范化图片：优先保留 URL，限制数量与 Base64 体积。"""
    if not isinstance(images, list):
        raise HTTPException(status_code=400, detail="商品图片必须是数组")
    if require_images and not images:
        raise HTTPException(status_code=400, detail="请至少提供 1 张商品图片")
    if len(images) > PRODUCT_PUBLISH_MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"单次最多支持 {PRODUCT_PUBLISH_MAX_IMAGES} 张商品图片")

    sanitized: List[Dict[str, Any]] = []
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片格式无效")

        url = str(image.get('url') or image.get('image_url') or image.get('src') or '').strip()
        item: Dict[str, Any] = {}
        if url:
            item['url'] = url
            for key in ('width', 'height', 'widthSize', 'heightSize', 'filename', 'name'):
                if image.get(key) is not None:
                    item[key] = image.get(key)
            sanitized.append(item)
            continue

        raw = image.get('content') or image.get('data') or image.get('base64')
        if raw is None:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片缺少 URL 或 Base64 内容")

        if isinstance(raw, (bytes, bytearray)):
            if len(raw) > PRODUCT_PUBLISH_MAX_IMAGE_BYTES:
                raise HTTPException(status_code=400, detail=f"第 {index} 张图片超过 {PRODUCT_PUBLISH_MAX_IMAGE_BYTES // (1024 * 1024)}MB 限制")
            # 素材库不直接存二进制，要求前端转 data URL / 先上传拿 URL
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片请使用 URL 或 Base64 文本保存到素材")

        raw_text = str(raw).strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片内容为空")
        if len(raw_text) > PRODUCT_PUBLISH_MAX_BASE64_CHARS:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片 Base64 过大，请先压缩或改用已上传 URL")
        estimated = _estimate_base64_bytes(raw_text)
        if estimated > PRODUCT_PUBLISH_MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片超过 {PRODUCT_PUBLISH_MAX_IMAGE_BYTES // (1024 * 1024)}MB 限制")

        item['data'] = raw_text
        for key in ('filename', 'name', 'type', 'size', 'width', 'height'):
            if image.get(key) is not None:
                item[key] = image.get(key)
        sanitized.append(item)
    return sanitized


def _validate_publish_images(images: List[Any]) -> List[Dict[str, Any]]:
    if not images:
        raise HTTPException(status_code=400, detail="请至少提供 1 张商品图片")
    if len(images) > PRODUCT_PUBLISH_MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"单次最多支持 {PRODUCT_PUBLISH_MAX_IMAGES} 张商品图片")

    normalized_images = []
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片格式无效")
        if not any(image.get(key) for key in ('url', 'image_url', 'src', 'content', 'data', 'base64')):
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片缺少 URL 或 Base64 内容")

        raw = image.get('content') or image.get('data') or image.get('base64')
        if isinstance(raw, str) and raw.strip():
            if len(raw) > PRODUCT_PUBLISH_MAX_BASE64_CHARS:
                raise HTTPException(status_code=400, detail=f"第 {index} 张图片 Base64 过大")
            if _estimate_base64_bytes(raw) > PRODUCT_PUBLISH_MAX_IMAGE_BYTES:
                raise HTTPException(status_code=400, detail=f"第 {index} 张图片超过 {PRODUCT_PUBLISH_MAX_IMAGE_BYTES // (1024 * 1024)}MB 限制")
        elif isinstance(raw, (bytes, bytearray)) and len(raw) > PRODUCT_PUBLISH_MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片超过 {PRODUCT_PUBLISH_MAX_IMAGE_BYTES // (1024 * 1024)}MB 限制")

        normalized_images.append(image)
    return normalized_images


def _parse_optional_non_negative_float(value: Any, field_label: str) -> Optional[float]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field_label}必须是数字")

    if parsed < 0:
        raise HTTPException(status_code=400, detail=f"{field_label}必须大于等于 0")

    return parsed


def _normalize_product_publish_data(data: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}

    for field in ('title', 'description', 'category', 'brand', 'condition', 'remark'):
        if field in data or not partial:
            value = data.get(field)
            if value is None:
                normalized[field] = None
            else:
                normalized[field] = str(value).strip()

    if not partial:
        if not normalized.get('title'):
            raise HTTPException(status_code=400, detail="商品标题不能为空")
        if not normalized.get('description'):
            raise HTTPException(status_code=400, detail="商品描述不能为空")
    else:
        if 'title' in normalized and not normalized.get('title'):
            raise HTTPException(status_code=400, detail="商品标题不能为空")
        if 'description' in normalized and not normalized.get('description'):
            raise HTTPException(status_code=400, detail="商品描述不能为空")

    if 'price' in data or not partial:
        normalized['price'] = _parse_optional_non_negative_float(data.get('price'), "现价")
    if 'original_price' in data or not partial:
        normalized['original_price'] = _parse_optional_non_negative_float(data.get('original_price'), "原价")
    if 'postage' in data or not partial:
        normalized['postage'] = _parse_optional_non_negative_float(data.get('postage'), "邮费")

    current_price = normalized.get('price') if 'price' in normalized else data.get('price')
    original_price = normalized.get('original_price') if 'original_price' in normalized else data.get('original_price')
    if original_price is not None and current_price is None:
        raise HTTPException(status_code=400, detail="填写原价时必须同时填写现价")

    if 'delivery_method' in data or not partial:
        delivery_method = str(data.get('delivery_method') or '包邮').strip() or '包邮'
        if delivery_method not in PRODUCT_PUBLISH_DELIVERY_CHOICES:
            raise HTTPException(status_code=400, detail="不支持的运费方式")
        normalized['delivery_method'] = delivery_method
        if delivery_method == '一口价' and normalized.get('postage') is None:
            raise HTTPException(status_code=400, detail="运费方式为一口价时必须填写邮费")

    if 'can_self_pickup' in data or not partial:
        normalized['can_self_pickup'] = _parse_form_bool(data.get('can_self_pickup'))

    if 'images' in data or not partial:
        images = data.get('images') or []
        if not isinstance(images, list):
            raise HTTPException(status_code=400, detail="商品图片必须是数组")
        normalized['images'] = images

    return normalized


def _is_sensitive_admin_data_field(table_name: str, column_name: str) -> bool:
    normalized = str(column_name or '').lower()
    if normalized in SENSITIVE_ADMIN_DATA_FIELDS:
        return True
    if any(part in normalized for part in ('password', 'secret', 'token', 'api_key', 'cookie', 'proxy_pass')):
        return True
    return table_name in {'cookies', 'system_settings', 'ai_reply_settings', 'notification_channels'} and normalized in {'value', 'config'}


SENSITIVE_ADMIN_DATA_FIELDS = {
    'password',
    'password_hash',
    'proxy_pass',
    'smtp_password',
    'api_key',
    'secret',
    'token',
    'value',
    'config',
}


def _redact_admin_table_data(table_name: str, data: List[Dict[str, Any]], columns: List[str]) -> List[Dict[str, Any]]:
    redacted_rows = []
    for row in data:
        redacted = {}
        for column in columns:
            value = row.get(column)
            redacted[column] = '***REDACTED***' if value not in (None, '') and _is_sensitive_admin_data_field(table_name, column) else value
        redacted_rows.append(redacted)
    return redacted_rows


def _is_password_login_verification_timeout_message(message: str) -> bool:
    normalized = str(message or '').strip()
    if not normalized:
        return False

    if ('超时' in normalized or '失效' in normalized) and '重新发起验证' in normalized:
        return True

    timeout_markers = (
        '验证超时',
        '二维码已失效',
        '请重新扫码',
    )
    return any(marker in normalized for marker in timeout_markers)


def _is_timed_out_verification_risk_log(log: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(log, dict):
        return False

    result_code = str(log.get('result_code') or '').strip().lower()
    if result_code == 'verification_timed_out' or result_code.endswith('_timed_out'):
        return True

    for field in ('error_message', 'processing_result', 'event_description'):
        if _is_password_login_verification_timeout_message(log.get(field)):
            return True

    return False


def _normalize_history_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _empty_slider_session_stats() -> Dict[str, Any]:
    return {
        'has_data': False,
        'total_sessions': 0,
        'total_attempts': 0,
        'success_count': 0,
        'failure_count': 0,
        'processing_count': 0,
        'completed_sessions': 0,
        'success_rate': 0.0,
        'recent_success': None,
        'recent_failure': None,
        'accounts_with_sessions': 0,
        'accounts_with_failures': 0,
        'stats_mode': 'session',
        'summary_text': '暂无滑块验证记录',
        'selected_range': 'all',
        'range_label': '所有',
    }


def _evaluate_screenshot_freshness(latest_file: str, latest_risk_epoch: Optional[float]) -> Tuple[str, Optional[str]]:
    """判断 glob 到的历史截图是否仍应展示。抽成纯函数便于单测。

    Returns (status, message):
      - ('ok', None)           截图有效，可展示
      - ('stale', msg)         有更新的风控事件，截图已过期
      - ('unavailable', msg)   截图 mtime 读取失败（文件被并发删除等），不可用
    """
    if latest_risk_epoch is None:
        return ('ok', None)
    try:
        screenshot_mtime = os.path.getmtime(latest_file)
    except OSError:
        # 不能默认 0，否则任何近期风控都会把它误判为"过期"；明确报"不可用"
        return ('unavailable', '验证截图读取失败或已被清理，请重新发起验证')
    if latest_risk_epoch > screenshot_mtime + _SCREENSHOT_STALE_GAP_SECONDS:
        return ('stale', '当前没有待处理的验证截图（最近一次风控可能是滑块/Token刷新，已自动处理或需等待风控冷却）')
    return ('ok', None)


def _build_face_verification_screenshot_info(account_id: str, file_path: str) -> Dict[str, Any]:
    from datetime import datetime

    normalized_path = str(file_path or '').replace('\\', '/')
    filename = os.path.basename(normalized_path)
    stat = os.stat(normalized_path)
    return {
        'filename': filename,
        'account_id': account_id,
        'path': f'/static/uploads/images/{filename}',
        'size': stat.st_size,
        'created_time': stat.st_ctime,
        'created_time_str': datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
    }


def _validate_system_setting_value(key: str, value: str) -> str:
    if key == 'risk_control_night_mode_enabled':
        normalized = str(value).strip().lower()
        if normalized in {'true', '1', 'yes', 'on'}:
            return 'true'
        if normalized in {'false', '0', 'no', 'off'}:
            return 'false'
        raise HTTPException(status_code=400, detail='夜间降频开关只能为 true 或 false')

    if key in {'risk_control_night_start_hour', 'risk_control_night_end_hour'}:
        try:
            hour = int(str(value).strip())
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail='夜间时间必须是 0-23 的整数')
        if hour < 0 or hour > 23:
            raise HTTPException(status_code=400, detail='夜间时间必须是 0-23 的整数')
        return str(hour)

    return value


TASK_LOG_TYPE_LABELS = {
    'auto_comment': '自动评价',
    'item_polish': '商品擦亮',
    'login_renew': '登录续期',
    'cookie_refresh': 'Cookie刷新',
    'other_task': '其他任务',
}


PRODUCT_PUBLISH_DELIVERY_CHOICES = {"包邮", "按距离计费", "一口价", "无需邮寄"}


PRODUCT_PUBLISH_MAX_BASE64_CHARS = 12 * 1024 * 1024


PRODUCT_PUBLISH_MAX_IMAGES = 9


PRODUCT_PUBLISH_MAX_IMAGE_BYTES = 8 * 1024 * 1024


ORDER_SALES_TIME_SQL = "COALESCE(NULLIF(platform_paid_at, ''), NULLIF(platform_created_at, ''), created_at)"


CAPTCHA_EXPIRE_SECONDS = 300


NIGHT_MODE_SYSTEM_SETTING_KEYS = {
    'risk_control_night_mode_enabled',
    'risk_control_night_start_hour',
    'risk_control_night_end_hour',
}


PASSWORD_LOGIN_TERMINAL_STATUSES = {'success', 'failed', 'cancelled'}


_SCREENSHOT_STALE_GAP_SECONDS = 60
