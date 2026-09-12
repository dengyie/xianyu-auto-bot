"""商品编辑（edit_item）：editDetail 拉表单 → 浅合并 mutations → edit 提交。

封装字段（uniqueCode/sourceId/bizcode/publishScene）取自 goofish PC 编辑页
前端实证；submit=False 仅返回合并预览，绝不触碰 edit 提交。
"""
from utils.item_publisher import ItemPublisher


def _publisher() -> ItemPublisher:
    return ItemPublisher("unb=1; cookie2=abc", cookie_id="ck-edit")


def _patch_post(monkeypatch, publisher: ItemPublisher, responses):
    calls = []

    async def _post(api_name, version, payload, spm_cnt, spm_pre):
        calls.append({"api": api_name, "version": version, "payload": dict(payload)})
        return responses.pop(0)

    monkeypatch.setattr(publisher, "_post_mtop", _post)
    return calls


def _edit_detail_response(form: dict):
    return {"ret": ["SUCCESS::调用成功"], "data": form}


async def test_submit_merges_mutations_and_envelope(monkeypatch):
    publisher = _publisher()
    calls = _patch_post(monkeypatch, publisher, [
        _edit_detail_response({"itemId": "123456789", "itemTextDTO": {"title": "T"}, "itemPriceDTO": {"price": "500"}}),
        {"ret": ["SUCCESS::调用成功"]},
    ])

    result = await publisher.edit_item("123456789", {"itemPriceDTO": {"price": "460"}})

    assert result["success"] is True and result["submitted"] is True
    assert len(calls) == 2
    assert calls[0]["api"] == "mtop.idle.pc.idleitem.editDetail"
    assert calls[1]["api"] == "mtop.idle.pc.idleitem.edit"
    edit_payload = calls[1]["payload"]
    assert edit_payload["itemPriceDTO"] == {"price": "460"}
    assert edit_payload["sourceId"] == "123456789"
    assert edit_payload["bizcode"] == "pcMainPublish"
    assert edit_payload["publishScene"] == "pcMainPublish"
    assert edit_payload.get("uniqueCode")


async def test_dry_run_returns_preview_without_submit(monkeypatch):
    publisher = _publisher()
    calls = _patch_post(monkeypatch, publisher, [
        _edit_detail_response({"itemPriceDTO": {"price": "500"}}),
    ])

    result = await publisher.edit_item("123456789", {"itemPriceDTO": {"price": "460"}}, submit=False)

    assert result["success"] is True and result["submitted"] is False
    assert result["payload_preview"]["itemPriceDTO"] == {"price": "460"}
    # 干跑必须带回原始表单，调用方可 diff 确认只改了目标字段
    assert result["data_original"]["itemPriceDTO"] == {"price": "500"}
    # 干跑只调 editDetail，绝不触发 edit
    assert len(calls) == 1 and calls[0]["api"].endswith("editDetail")


async def test_edit_detail_failure_aborts(monkeypatch):
    publisher = _publisher()
    calls = _patch_post(monkeypatch, publisher, [
        {"ret": ["FAIL_SYS_TOKEN_EXPIRED::token过期"]},
    ])

    result = await publisher.edit_item("123456789", {"itemPriceDTO": {"price": "460"}})

    assert result["success"] is False
    assert len(calls) == 1


async def test_invalid_item_id_short_circuits(monkeypatch):
    publisher = _publisher()
    calls = _patch_post(monkeypatch, publisher, [])

    result = await publisher.edit_item("abc", {"itemPriceDTO": {"price": "460"}})

    assert result["success"] is False
    assert calls == []
