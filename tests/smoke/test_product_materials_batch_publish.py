"""Product materials + batch publish: API + source contract."""

import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

import reply_server


def _primary_user_id(db) -> int:
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
    if not row:
        db.create_user("product_publish_owner", "product-publish@example.com", "pass12345")
        with db.lock:
            cur = db.conn.cursor()
            cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
    return int(row[0])


def _create_user(db, username: str) -> int:
    email = f"{username}@example.com"
    existing = db.get_user_by_username(username)
    if existing:
        return int(existing["id"] if isinstance(existing, dict) else existing[0])
    ok = db.create_user(username, email, "pass12345")
    assert ok is True or ok
    user = db.get_user_by_username(username)
    if isinstance(user, dict):
        return int(user["id"])
    if isinstance(user, (list, tuple)):
        return int(user[0])
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
    return int(row[0])


def _seed_cookie(db, cookie_id: str, user_id: int):
    ok = db.save_cookie(
        cookie_id,
        f"unb={cookie_id}; _m_h5_tk=token_1; cookie2=x",
        user_id=user_id,
    )
    assert ok is True


def _auth_headers(user_id: int, username: str = "product_publish_user") -> dict:
    token = f"product-publish-token-{user_id}-{username}"
    reply_server.SESSION_TOKENS[token] = {
        "user_id": user_id,
        "username": username,
        "is_admin": True,
        "timestamp": time.time(),
    }
    return {"Authorization": f"Bearer {token}"}


def _tiny_png_b64() -> str:
    # 1x1 PNG
    return (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


def test_product_material_crud(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)

    create = client.post(
        "/product-materials",
        headers=headers,
        json={
            "title": "素材标题A",
            "description": "素材描述A",
            "price": 12.5,
            "images": [{"url": "https://example.com/a.jpg", "width": 800, "height": 800}],
            "delivery_method": "包邮",
            "postage": 0,
            "can_self_pickup": False,
            "condition": "全新",
        },
    )
    assert create.status_code == 200, create.text
    body = create.json()
    assert body["success"] is True
    material = body["material"]
    material_id = material["id"]
    assert material["title"] == "素材标题A"

    listed = client.get("/product-materials?page=1&page_size=20", headers=headers)
    assert listed.status_code == 200
    assert any(int(x["id"]) == int(material_id) for x in listed.json().get("list") or [])

    updated = client.put(
        f"/product-materials/{material_id}",
        headers=headers,
        json={"title": "素材标题B", "description": "素材描述B"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["material"]["title"] == "素材标题B"

    deleted = client.delete(f"/product-materials/{material_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True


def test_product_material_requires_images(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)

    resp = client.post(
        "/product-materials",
        headers=headers,
        json={
            "title": "无图素材",
            "description": "描述",
            "images": [],
            "delivery_method": "包邮",
        },
    )
    assert resp.status_code == 400
    assert "图片" in (resp.json().get("detail") or "")


def test_product_publish_json_writes_log_and_summarizes(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "publish-cookie-json"
    _seed_cookie(db, cookie_id, user_id)
    material_id = db.add_product_material(
        user_id,
        {
            "title": "关联素材",
            "description": "关联描述",
            "price": 1,
            "images": [{"url": "https://example.com/m.jpg"}],
            "delivery_method": "包邮",
            "postage": 0,
            "can_self_pickup": False,
        },
    )
    assert material_id
    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)

    class FakePublisher:
        def __init__(self, *args, **kwargs):
            self.cookies_str = f"unb={cookie_id}; refreshed=1"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def publish_item(self, **kwargs):
            return {
                "ret": ["SUCCESS::调用成功"],
                "data": {"itemId": "9988776655", "noise": "x" * 5000},
                "_uploaded_images": [{"url": "https://cdn.example/a.jpg"}] * 5,
            }

        def extract_published_item_id(self, payload):
            return "9988776655"

        def is_success_response(self, payload):
            return True

        def extract_error_message(self, payload):
            return "ok"

    async def fake_sync(cookie_id, cookies_str, published_item_id=None):
        return {
            "success": True,
            "message": "已同步",
            "published_item_id": published_item_id,
            "item_synced": True,
            "page_sync": {"success": True, "current_count": 1, "saved_count": 1, "error": None},
            "full_sync": {"used": False, "success": False, "total_count": 0, "total_saved": 0, "error": None},
        }

    with mock.patch("utils.item_publisher.ItemPublisher", FakePublisher), \
         mock.patch.object(reply_server, "_sync_items_after_publish", side_effect=fake_sync):
        resp = client.post(
            "/product-publish",
            headers=headers,
            json={
                "account_id": cookie_id,
                "title": "发布标题",
                "description": "发布描述",
                "price": 9.9,
                "images": [{"data": _tiny_png_b64()}],
                "delivery_method": "包邮",
                "postage": 0,
                "can_self_pickup": False,
                "material_id": material_id,
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["published_item_id"] == "9988776655"
    assert body.get("log_id")
    assert body.get("item_url")
    assert body.get("material_id") == material_id
    # 出站只给摘要，不回传巨型 noise / 全量 uploaded images
    publish_result = body.get("publish_result") or {}
    assert "noise" not in str(publish_result.get("data") or {})
    assert publish_result.get("uploaded_image_count") == 5

    logs = db.list_publish_logs(user_id=user_id, page=1, page_size=10, include_raw_response=False)
    assert logs["total"] >= 1
    success_logs = [x for x in logs["list"] if x.get("status") == "success"]
    assert success_logs
    assert all("raw_response" not in x for x in success_logs)
    assert any(x.get("material_id") == material_id for x in success_logs)

    logs_raw = db.list_publish_logs(user_id=user_id, page=1, page_size=10, include_raw_response=True)
    raw_item = next(x for x in logs_raw["list"] if x.get("id") == success_logs[0]["id"])
    raw = raw_item.get("raw_response") or {}
    if isinstance(raw, str):
        assert "noise" not in raw
    else:
        assert "noise" not in str((raw.get("data") or {}))


def test_batch_publish_starts_jobs_and_aggregates(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "publish-cookie-batch"
    _seed_cookie(db, cookie_id, user_id)
    material_id = db.add_product_material(
        user_id,
        {
            "title": "批量素材",
            "description": "批量描述",
            "price": 1,
            "images": [{"url": "https://example.com/b.jpg"}],
            "delivery_method": "包邮",
            "postage": 0,
            "can_self_pickup": False,
        },
    )
    assert material_id

    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)

    async def fake_publish(**kwargs):
        log_id = kwargs.get("log_id")
        if log_id:
            db.update_publish_log(
                log_id,
                status="success",
                item_id="123",
                item_url="https://www.goofish.com/item?id=123",
                user_id=user_id,
            )
        return {
            "success": True,
            "message": "ok",
            "published_item_id": "123",
            "item_url": "https://www.goofish.com/item?id=123",
            "log_id": log_id,
            "batch_id": kwargs.get("batch_id"),
        }

    with mock.patch.object(reply_server, "_publish_product_to_account", side_effect=fake_publish):
        resp = client.post(
            "/product-publish/batch",
            headers=headers,
            json={"account_ids": [cookie_id], "material_ids": [material_id]},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["batch_id"]
    assert body["total"] == 1

    status = client.get(f"/product-publish/batch/{body['batch_id']}", headers=headers)
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["batch_id"] == body["batch_id"]
    assert status_body["total"] == 1
    assert status_body["success"] == 1
    assert status_body["finished"] is True
    assert status_body["logs"]
    assert all("raw_response" not in (log or {}) for log in status_body["logs"])


def test_publish_logs_api_strips_raw_response(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "publish-cookie-log-strip"
    _seed_cookie(db, cookie_id, user_id)
    log_id = db.add_publish_log(
        user_id,
        cookie_id,
        "日志标题",
        description="d",
        status="success",
        raw_response={"ret": ["SUCCESS"], "data": {"itemId": "1", "blob": "x" * 100}},
    )
    assert log_id
    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)
    resp = client.get("/publish-logs?page=1&page_size=20", headers=headers)
    assert resp.status_code == 200
    items = resp.json().get("list") or []
    assert items
    assert all("raw_response" not in item for item in items)


def test_material_cross_user_isolation(_db):
    db = reply_server.db_manager
    user_a = _primary_user_id(db)
    user_b = _create_user(db, "product_publish_other")
    assert user_a != user_b

    material_id = db.add_product_material(
        user_a,
        {
            "title": "A的素材",
            "description": "仅A可见",
            "price": 1,
            "images": [{"url": "https://example.com/a-only.jpg"}],
            "delivery_method": "包邮",
        },
    )
    assert material_id

    client = TestClient(reply_server.app)
    headers_b = _auth_headers(user_b, username="product_publish_other")
    listed = client.get("/product-materials?page=1&page_size=50", headers=headers_b)
    assert listed.status_code == 200
    assert all(int(x["id"]) != int(material_id) for x in listed.json().get("list") or [])

    got = client.get(f"/product-materials/{material_id}", headers=headers_b)
    assert got.status_code == 404


def test_product_publish_source_contract(api_source):
    # @router./@app. are equivalent for these contracts: the split moved handlers
    # from the global app onto domain APIRouters; normalize before asserting.
    runtime = api_source.replace("@router.", "@app.").replace("ctx.", "")
    assert "class ProductMaterialRequest" in runtime
    assert '@app.get("/product-materials")' in runtime
    assert '@app.post("/product-publish")' in runtime
    assert '@app.post("/product-publish/batch")' in runtime
    assert "async def _publish_product_to_account" in runtime
    assert "return await _publish_product_to_account(" in runtime
    assert "def _sanitize_material_images" in runtime
    assert "def _summarize_publish_result_for_client" in runtime
    assert "material_id: Optional[int] = None" in runtime

    db_mixin = Path("db_manager/product_publish.py").read_text(encoding="utf-8")
    assert "def add_product_material" in db_mixin
    assert "def get_publish_batch_status" in db_mixin
    assert "def _summarize_publish_raw_response" in db_mixin
    assert "include_raw_response" in db_mixin
    assert "GROUP BY status" in db_mixin
    assert "CREATE TABLE IF NOT EXISTS product_materials" in db_mixin
    assert "user_id: int = None" in db_mixin  # update_publish_log scope

    publisher = Path("utils/item_publisher.py").read_text(encoding="utf-8")
    assert "async def prepare_image_for_publish" in publisher
    assert "def _decode_base64_image" in publisher

    items_js = Path("static/js/app-items.js").read_text(encoding="utf-8")
    assert "async function saveItemPublishMaterial" in items_js
    assert "async function loadItemPublishMaterials" in items_js
    assert "async function submitItemPublishBatch" in items_js
    assert "function updateItemPublishImageRequiredState" in items_js
    assert "/product-publish" in items_js
    assert "/product-publish/batch" in items_js
    assert "/product-materials" in items_js
    assert "material_id" in items_js

    html = Path("static/index.html").read_text(encoding="utf-8")
    assert "publishMaterialList" in html
    assert "publishLogList" in html
    assert "itemPublishSaveMaterialBtn" in html
    assert "publishBatchAccountList" in html
    assert "itemPublishBatchSubmitBtn" in html
    # P0: 不能再对 file input 强制 required，否则素材直发被 HTML5 拦截
    publish_images_block = html.split('id="publishImages"', 1)[1].split(">", 1)[0]
    assert "required" not in publish_images_block

    css = Path("static/css/items.css").read_text(encoding="utf-8")
    assert ".item-publish-side-list" in css
    assert ".item-publish-check-list" in css

    core = Path("static/js/app-core.js").read_text(encoding="utf-8")
    assert "itemPublishBatchId" in core
    assert "itemPublishBatchSelectedMaterialIds" in core
