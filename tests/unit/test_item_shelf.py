"""卖家商品管理动作：set_item_shelf_state（下架/重新上架）。

downshelf api/version/payload 取自 goofish PC 商品详情页前端实证；
upshelf 为对称推断（接口不存在时网关返回 FAIL_SYS_API_NOT_FOUNDED）。
仅 token 过期重试，业务错误不重试。
"""
from utils.item_publisher import ItemPublisher


def _publisher() -> ItemPublisher:
    return ItemPublisher("unb=1; cookie2=abc", cookie_id="ck-shelf")


def _patch_post(monkeypatch, publisher: ItemPublisher, responses):
    calls = []

    async def _post(api_name, version, payload, spm_cnt, spm_pre):
        calls.append({"api": api_name, "version": version, "payload": dict(payload)})
        return responses.pop(0)

    monkeypatch.setattr(publisher, "_post_mtop", _post)
    return calls


async def test_downshelf_success_uses_evidenced_api(monkeypatch):
    publisher = _publisher()
    calls = _patch_post(monkeypatch, publisher, [{"ret": ["SUCCESS::调用成功"]}])

    result = await publisher.set_item_shelf_state("1079612103929", on_shelf=False)

    assert result["success"] is True
    assert result["action"] == "downshelf"
    assert calls[0]["api"] == "mtop.taobao.idle.item.downshelf"
    assert calls[0]["version"] == "2.0"
    assert calls[0]["payload"] == {"itemId": "1079612103929"}


async def test_upshelf_uses_symmetric_api(monkeypatch):
    publisher = _publisher()
    calls = _patch_post(monkeypatch, publisher, [{"ret": ["SUCCESS::调用成功"]}])

    result = await publisher.set_item_shelf_state("123", on_shelf=True)

    assert result["success"] is True
    assert result["action"] == "upshelf"
    assert calls[0]["api"] == "mtop.taobao.idle.item.upshelf"
    assert calls[0]["version"] == "2.0"


async def test_token_expiry_retries_then_succeeds(monkeypatch):
    publisher = _publisher()
    calls = _patch_post(monkeypatch, publisher, [
        {"ret": ["FAIL_SYS_TOKEN_EXPIRED::token过期"]},
        {"ret": ["SUCCESS::调用成功"]},
    ])

    result = await publisher.set_item_shelf_state("123", on_shelf=False)

    assert result["success"] is True
    assert len(calls) == 2


async def test_business_error_not_retried(monkeypatch):
    publisher = _publisher()
    calls = _patch_post(monkeypatch, publisher, [
        {"ret": ["FAIL_SYS_API_NOT_FOUNDED::接口不存在"]},
    ])

    result = await publisher.set_item_shelf_state("123", on_shelf=True)

    assert result["success"] is False
    assert "API_NOT_FOUNDED" in result["error"]
    assert len(calls) == 1


async def test_missing_item_id_short_circuits(monkeypatch):
    publisher = _publisher()
    calls = _patch_post(monkeypatch, publisher, [])

    result = await publisher.set_item_shelf_state("  ", on_shelf=False)

    assert result["success"] is False
    assert calls == []
