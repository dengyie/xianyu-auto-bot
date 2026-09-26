# 测试套件说明

> 维护：2026-09-27（消息管线覆盖加深批）。跑法与套件地图、消息链路覆盖地图、fixture 模式与坑。

## 跑法

```bash
python -m pytest tests/smoke -q          # 冒烟套件（PR/发版前必跑，~3 分钟）
python -m pytest tests/unit -q           # 单元套件
python -m pytest -q                      # 全量（CI Test job 同款）
```

- `pyproject.toml` 配置 `asyncio_mode = "auto"`——async def 测试**不需要** `@pytest.mark.asyncio`（写了也不报错）。
- pytest 退出码会被管道吞（`pytest | tail`）——判定结果必须 `> log 2>&1; rc=$?`。
- CI：`.github/workflows/docker-image.yml` 的 Test job（pytest + F821 门禁 + lock 校验）→ build-and-push → deploy-to-hk。**测试/文档类提交想跳过 CI 用提交信息尾缀 `[skip ci]`**。

## 套件地图（按域）

| 域 | 关键文件 |
|---|---|
| 消息管线（拆帧/门控/去重/防抖） | `test_multi_item_sync_split.py` · `test_message_pipeline_gates.py` · `test_message_dedupe_and_route.py` · `test_xianyu_order_status_runtime_seam.py` |
| AI 引擎（重试/异步/超时） | `../tests/unit/test_ai_provider_retry.py` · `test_ai_reply_engine_async.py` · `test_ai_reply_pre_send_filter.py` |
| 认证恢复/Token | `test_browser_token_retry.py` · `test_xianyu_token_refresh_request.py` · `test_show_browser_default.py` |
| 滑块编排 | `test_slider_orchestrator.py` · `test_slider_human_fallback.py` · `test_slider_verification_guards.py` |
| Web/API（authz/备份/文件） | `test_auth.py` · `test_authz_matrix.py` · `test_backup_restore_runtime.py` · `test_files.py` |
| 运维兜底 | `test_chrome_reaper.py` · `test_logging_hygiene.py` · `test_system_settings.py` |

## 消息链路覆盖地图（收到 → 回复）

```
WS 帧 → 拆帧 → 解密 → 提取 → 分类(route) → 门控 → 防抖 → AI → 发送 → 落库
        │        │       │        │          │       │       │      │
        │        │       │        │          │       │       │      └─ gates 测试断言 save_chat_message(direction=2)
        │        │       │        │          │       │       └─ test_ai_provider_retry（重试链/空回复/超时）
        │        │       │        │          │       └─ gates 测试 _schedule_debounced_reply 调度断言
        │        │       │        │          └─ gates 测试门控矩阵 ×3（auto_reply 关/过滤规则/非本账号）
        │        │       │        └─ order_status_runtime_seam（route 分类契约）
        │        │       └─ gates 测试（提取失败路径 + fixtures）
        │        └─ dedupe_and_route（解密/unwrap/消息ID提取）
        └─ multi_item_sync_split（多消息帧逐条处理 + 单次 ack + 空包护栏）
```

每一段都有独立断言；**端到端验收以 `test_message_pipeline_gates.py::test_multi_item_frame_with_buyer_chat_persists_and_schedules_reply` 为准**（积压帧 → 买家聊天落库 + 防抖调度，旧 data[0]-only 实现下必红）。

## fixture 模式与坑（写消息管线测试前必读）

1. **裸实例驱动全路径**：`MessagePipelineMixin.__new__(MessagePipelineMixin)` + 手工补成员。handle_message 依赖生产由 `__init__`/兄弟 Mixin 提供的成员，缺哪个外层 `except` 就在哪静默收口（症状：断言"后续步骤没发生"）。已知必补：`_cookie_mgr` · `myid` · `_safe_str` · `is_sync_package` · `pause_manager` · `order_status_handler` · `yifan_account_lock` · `yifan_account_waiting` · `_extract_order_id` · `extract_item_id_from_message` · `_sanitize_buyer_nick`。
2. **`_host` 代理是模块级**：`_host.X` → `XianyuAutoAsync.X`（模块属性，不是实例属性）。涉及它的断言 patch `XianyuAutoAsync` 模块属性（如 `pause_manager`），不要 patch 实例。
3. **同步帧 fixture 结构**：每 item 是 `{"data": base64(json.loads 后的内部结构)}`；买家聊天内部结构 = `{"1": {"2": "chat@goofish", "5": ms, "10": {reminderContent/reminderTitle/senderUserId/senderNick/bizTag}}}`。`is_chat_message` 只认 `"1"→"10"→reminderContent`。
4. **loguru 断言**：sink 回调里 `str(msg)`，断言用子串（级别对齐 `{level: <8}` 格式）。
5. **DB**：conftest 走 `:memory:` 库（每测试重建）；patch 点是 `db_manager.db_manager` 单例实例的方法（`save_chat_message`/`update_buyer_nick_by_buyer_id`）。
6. **测试目录就是工作目录**：`open("ai_reply_engine.py")` 这类相对路径在 pytest 根目录下成立；新增文件名避免与既有测试重名。

## 生产联调对照（跑测试之外的最后一步）

测试全绿 ≠ 线上语义恢复。生产验证三件套：
1. `chat_messages` 表是否增长（买家消息落库）；
2. 日志链路标记（`消息分类` → `创建防抖任务` → `AI回复生成成功` → `发出`）；
3. 真人小号端到端（收到回复为准）。
典型案例：data[0]-only 丢消息 bug（2026-09-26）——测试曾全绿、"帧被处理"日志齐全，但买家文本从未落库。
