import queue
import threading
import time
from collections import defaultdict
from typing import Any, Dict, Optional

from loguru import logger


class OrderEventHub:
    """进程内订单事件中心，按 user_id 广播订单更新。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers = defaultdict(set)

    def subscribe(self, user_id: int, maxsize: int = 100):
        subscriber = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers[user_id].add(subscriber)
        return subscriber

    def unsubscribe(self, user_id: int, subscriber):
        with self._lock:
            subscribers = self._subscribers.get(user_id)
            if not subscribers:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscribers.pop(user_id, None)

    def publish(self, user_id: int, event: Dict[str, Any]):
        with self._lock:
            subscribers = list(self._subscribers.get(user_id, set()))

        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                # 背压策略：drop-oldest。订阅者消费慢时，丢掉最旧的事件让最新事件入队，
                # 保证 UI 拿到的是"最新状态"而不是队列头的陈旧状态。
                # 注意：hub lock 已在上面 list() 后释放，与其它 publisher 并发时，
                # 下面的 get_nowait/put_nowait 之间可能被别的线程再灌满队列，
                # 因此第二次 put_nowait 仍可能 Full——这是可接受的最后兜底：记日志+丢弃当前事件。
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass

                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    logger.warning(f"订单事件队列仍然已满，丢弃事件: user_id={user_id}")


order_event_hub = OrderEventHub()


def build_order_update_event(order: Dict[str, Any], source: str = "unknown") -> Dict[str, Any]:
    return {
        "type": "order.updated",
        "source": source,
        "timestamp": int(time.time() * 1000),
        "order": order,
    }


def publish_order_update_event(order_id: str, source: str = "unknown") -> Optional[Dict[str, Any]]:
    from db_manager import db_manager

    order = db_manager.get_order_by_id(order_id)
    if not order:
        return None

    cookie_id = order.get('cookie_id')
    if not cookie_id:
        return None

    cookie_info = db_manager.get_cookie_details(cookie_id)
    user_id = cookie_info.get('user_id') if cookie_info else None
    if user_id is None:
        return None

    event = build_order_update_event(order, source=source)
    order_event_hub.publish(user_id, event)
    return event
