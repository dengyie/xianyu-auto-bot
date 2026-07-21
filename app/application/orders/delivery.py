"""Order delivery context assembly outside HTTP route handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class OrderRepository(Protocol):
    def get_order_by_id(self, order_id: str) -> dict[str, Any] | None: ...

    def get_cookie_details(self, cookie_id: str) -> dict[str, Any] | None: ...


class DeliveryContextError(Exception):
    pass


class OrderNotFound(DeliveryContextError):
    pass


class MissingOrderAccount(DeliveryContextError):
    pass


class ForbiddenOrder(DeliveryContextError):
    pass


@dataclass(frozen=True)
class ManualDeliveryContext:
    order: dict[str, Any]
    cookie_id: str


class ManualDeliveryContextLoader:
    def __init__(self, repository: OrderRepository):
        self.repository = repository

    def load(self, order_id: str, user_id: int) -> ManualDeliveryContext:
        order = self.repository.get_order_by_id(order_id)
        if not order:
            raise OrderNotFound
        cookie_id = order.get("cookie_id")
        if not cookie_id:
            raise MissingOrderAccount
        cookie_info = self.repository.get_cookie_details(cookie_id)
        if not cookie_info or cookie_info.get("user_id") != user_id:
            raise ForbiddenOrder
        return ManualDeliveryContext(order=order, cookie_id=cookie_id)
