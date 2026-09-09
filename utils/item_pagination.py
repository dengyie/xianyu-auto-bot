"""商品列表分页参数归一化（移植上游 9739cc5）。

闲鱼网页端在售商品列表接口的 pageSize 实测上限为 20，超限会被服务端
拒绝或静默截断，导致分页终止判断失真。
"""
from typing import Any


DEFAULT_ITEM_LIST_PAGE_SIZE = 20
MAX_ITEM_LIST_PAGE_SIZE = 20


def normalize_item_list_page_size(value: Any) -> int:
    """将商品列表分页大小限制在闲鱼网页端接口允许的范围内。"""
    try:
        page_size = int(value)
    except (TypeError, ValueError):
        page_size = DEFAULT_ITEM_LIST_PAGE_SIZE

    return min(max(page_size, 1), MAX_ITEM_LIST_PAGE_SIZE)
