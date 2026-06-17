"""Order history sync smoke regressions."""

import asyncio
import time

import reply_server


def _add_cookie(client, headers, cookie_id):
    resp = client.post(
        "/cookies",
        headers=headers,
        json={"id": cookie_id, "value": f"unb={cookie_id}; _m_h5_tk=testtoken_12345; cookie2=test"},
    )
    assert resp.status_code == 200


class _FakeHistoryFetcher:
    def __init__(self, cookie_string, cookie_id_for_log="unknown", headless=True):
        self.cookie_string = cookie_string
        self.cookie_id_for_log = cookie_id_for_log
        self.headless = headless
        self.closed = False

    async def fetch_recent_orders(self, max_orders=100, max_scroll_rounds=12, utc_start=None, utc_end_exclusive=None):
        del max_scroll_rounds, utc_start, utc_end_exclusive
        return {
            "orders": [
                {
                    "order_id": "hist-order-1",
                    "item_id": "hist-item-1",
                    "buyer_id": "hist-buyer-1",
                    "buyer_nick": "history buyer",
                    "sid": "hist-chat-1@goofish",
                    "order_status": "pending_ship",
                    "amount": "18.50",
                    "platform_created_at": "2026-06-17 08:00:00",
                    "platform_paid_at": "2026-06-17 08:05:00",
                    "platform_completed_at": None,
                }
            ][:max_orders],
            "scanned_count": 1,
            "matched_count": 1,
            "out_of_range_count": 0,
            "pages_scanned": 1,
            "stopped_by_range": False,
        }

    async def fetch_order_detail(self, order_id, force_refresh=True):
        del force_refresh
        return {
            "order_id": order_id,
            "item_id": "hist-item-1",
            "order_status": "shipped",
            "spec_name": "规格",
            "spec_value": "默认",
            "quantity": "1",
            "amount": "18.50",
            "platform_created_at": "2026-06-17 08:00:00",
            "platform_paid_at": "2026-06-17 08:05:00",
            "platform_completed_at": "2026-06-17 08:10:00",
        }

    async def close(self):
        self.closed = True


class _SlowHistoryFetcher(_FakeHistoryFetcher):
    async def fetch_recent_orders(self, max_orders=100, max_scroll_rounds=12, utc_start=None, utc_end_exclusive=None):
        del max_orders, max_scroll_rounds, utc_start, utc_end_exclusive
        await asyncio.sleep(0.5)
        return await _FakeHistoryFetcher.fetch_recent_orders(self)


class _FailingDetailHistoryFetcher(_FakeHistoryFetcher):
    async def fetch_order_detail(self, order_id, force_refresh=True):
        del order_id, force_refresh
        raise RuntimeError("detail fetch boom")


class _FakeLiveRuntime:
    async def fetch_order_detail_info(
        self,
        order_id=None,
        item_id=None,
        buyer_id=None,
        sid=None,
        force_refresh=False,
        buyer_nick=None,
        buyer_id_source=None,
    ):
        del force_refresh, buyer_nick, buyer_id_source
        reply_server.db_manager.insert_or_update_order(
            order_id=order_id,
            item_id=item_id,
            buyer_id=buyer_id,
            sid=sid,
            buyer_nick="runtime buyer",
            cookie_id="history_cookie",
            order_status="shipped",
            quantity="2",
        )
        return {"success": True}


class _FakeCookieManager:
    def __init__(self, runtime):
        self.runtime = runtime

    def get_xianyu_instance(self, cid):
        del cid
        return self.runtime

    def get_ws_client(self, cid):
        del cid
        return None


def _wait_for_job_state(job_id, expected_status, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = reply_server.order_history_sync_jobs.get(job_id)
        if job and job.get("status") == expected_status:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach {expected_status}")


def test_history_sync_job_lifecycle_and_cancel(client, user_auth, mocker):
    _add_cookie(client, user_auth, "history_cookie")
    mocker.patch("utils.order_history_sync.OrderHistoryPageFetcher", _SlowHistoryFetcher)

    create = client.post(
        "/api/orders/history-sync",
        headers=user_auth,
        json={
            "cookie_id": "history_cookie",
            "start_date": "2026-06-16",
            "end_date": "2026-06-18",
            "max_orders": 5,
            "fetch_details": False,
        },
    )
    assert create.status_code == 200
    body = create.json()["data"]
    assert body["status"] == "pending"
    assert body["request"]["cookie_id"] == "history_cookie"

    status = client.get(f"/api/orders/history-sync/{body['job_id']}", headers=user_auth)
    assert status.status_code == 200
    assert status.json()["data"]["job_id"] == body["job_id"]

    cancelled = client.post(f"/api/orders/history-sync/{body['job_id']}/cancel", headers=user_auth)
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"


def test_history_sync_success_persists_orders_and_completes(client, user_auth, mocker):
    _add_cookie(client, user_auth, "history_cookie")
    mocker.patch("utils.order_history_sync.OrderHistoryPageFetcher", _FakeHistoryFetcher)

    create = client.post(
        "/api/orders/history-sync",
        headers=user_auth,
        json={
            "cookie_id": "history_cookie",
            "start_date": "2026-06-16",
            "end_date": "2026-06-18",
            "max_orders": 5,
            "fetch_details": False,
        },
    )
    assert create.status_code == 200
    job_id = create.json()["data"]["job_id"]

    job = _wait_for_job_state(job_id, "completed")
    assert job["orders_saved"] == 1
    assert job["matched_orders"] == 1

    order = reply_server.db_manager.get_order_by_id("hist-order-1")
    assert order["cookie_id"] == "history_cookie"
    assert order["order_status"] == "pending_ship"


def test_history_sync_falls_back_to_candidate_when_detail_refresh_fails(client, user_auth, mocker):
    _add_cookie(client, user_auth, "history_cookie")
    mocker.patch("utils.order_history_sync.OrderHistoryPageFetcher", _FailingDetailHistoryFetcher)

    create = client.post(
        "/api/orders/history-sync",
        headers=user_auth,
        json={
            "cookie_id": "history_cookie",
            "start_date": "2026-06-16",
            "end_date": "2026-06-18",
            "max_orders": 5,
            "fetch_details": True,
        },
    )
    assert create.status_code == 200
    job_id = create.json()["data"]["job_id"]

    job = _wait_for_job_state(job_id, "completed")
    assert job["orders_saved"] == 1
    assert any("详情刷新失败" in warning for warning in job["warnings"])

    order = reply_server.db_manager.get_order_by_id("hist-order-1")
    assert order["cookie_id"] == "history_cookie"
    assert order["order_status"] == "pending_ship"
    assert order["buyer_id"] == "hist-buyer-1"


def test_history_sync_uses_live_instance_detail_refresh_when_available(client, user_auth, mocker):
    _add_cookie(client, user_auth, "history_cookie")
    mocker.patch("utils.order_history_sync.OrderHistoryPageFetcher", _FakeHistoryFetcher)
    mocker.patch.object(reply_server.cookie_manager, "manager", _FakeCookieManager(_FakeLiveRuntime()))

    create = client.post(
        "/api/orders/history-sync",
        headers=user_auth,
        json={
            "cookie_id": "history_cookie",
            "start_date": "2026-06-16",
            "end_date": "2026-06-18",
            "max_orders": 5,
            "fetch_details": True,
        },
    )
    assert create.status_code == 200
    job_id = create.json()["data"]["job_id"]

    job = _wait_for_job_state(job_id, "completed")
    assert job["orders_saved"] == 1
    assert job["warnings"] == []

    order = reply_server.db_manager.get_order_by_id("hist-order-1")
    assert order["order_status"] == "shipped"
    assert order["buyer_nick"] == "runtime buyer"
    assert str(order["quantity"]) == "2"
