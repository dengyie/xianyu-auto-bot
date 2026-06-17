# Decisions

## 2026-06-17 - Phase 7 order-message coverage stays deterministic
- Decision: Cover delayed order-message binding and direct cancelled-message resolution with unit-shaped smoke tests around `OrderStatusHandler`, using a focused fake DB manager.
- Rationale: These branches depend on in-memory queues, strong match keys, and timestamp comparisons; deterministic fakes give stable regression coverage without live Xianyu traffic.
- Impact: Phase 7 now guards queued system-message consume, already-resolved discard, and direct cancellation backfill behavior with fast smoke tests.

## 2026-06-17 - Phase 8 smoke tests may patch unstable text-parsing seams
- Decision: Patch `_resolve_system_message_status()` and `extract_order_id()` in smoke tests when the assertion target is downstream queue behavior rather than text parsing.
- Rationale: The production code's status-text literals are currently affected by local encoding display issues; pinning the seam keeps regression tests focused on queue selection and cleanup invariants.
- Impact: Phase 8 coverage stays deterministic and valuable without coupling queue-behavior tests to fragile message text fixtures.

## 2026-06-17 - Phase 9 recent-fallback coverage should bypass stronger selectors
- Decision: Build recent-fallback smoke tests so `message_hash` and strong-key matching cannot win before the fallback selector runs.
- Rationale: The behavior under test is the timestamp-window fallback logic in `_select_terminal_pending_message_index()`, so earlier selectors must be intentionally neutralized to make the test meaningful.
- Impact: Phase 9 now directly guards the unique-bind and ambiguity-reject branches of the recent terminal fallback path.

## 2026-06-17 - Phase 10 mirrors recent-fallback coverage across queue types
- Decision: Reuse the same recent-fallback smoke pattern for `_pending_red_reminder_messages` so terminal queue behavior stays symmetric between system messages and red reminders.
- Rationale: Both queues share the same selector and delayed-binding flow, and asymmetric coverage would leave one terminal path easier to regress.
- Impact: The red-reminder delayed terminal path now has the same unique-bind and ambiguity-reject regression safety net as system messages.

## 2026-06-17 - Phase 11 should target selector disambiguation directly
- Decision: Cover the `message_hash+strong_key` branch by constructing duplicate-`message_hash` queue entries where only one candidate matches `sid`, `buyer_id`, and `item_id`.
- Rationale: This selector path sits between the simple hash match and the terminal recent-fallback path, so it needs direct regression tests that prove unique disambiguation consumes only the intended queue entry.
- Impact: The pending system-message and red-reminder queues now both guard the unique strong-key disambiguation branch against future selector regressions.

## 2026-06-17 - Phase 12 should align enqueue entrypoints with available stale cleanup
- Decision: Invoke `clear_old_pending_updates()` inside the unresolved enqueue paths for `handle_system_message(...)` and `handle_red_reminder_message(...)` before appending a new pending queue item.
- Rationale: The project already had stale cleanup logic and a direct helper test, but the real enqueue entrypoints were still able to accumulate expired in-memory state until some external caller remembered to clean it. Pulling cleanup into the entrypoints matches the actual runtime flow and closes that gap.
- Impact: Stale pending updates, system messages, and red reminders are now trimmed opportunistically during new unresolved message intake, and phase-12 smoke tests lock that behavior in.

## 2026-06-17 - Phase 13 should lock down bind-gap rejection explicitly
- Decision: Add direct smoke coverage for the branch where a uniquely matched terminal pending message is refused because its timestamp gap from the newly extracted order exceeds `pending_terminal_bind_max_gap_seconds`.
- Rationale: This guard prevents stale terminal updates from being rebound onto later orders that happen to share match keys, but it was only indirectly protected before. A focused regression test is the cheapest way to preserve that safety behavior.
- Impact: System-message and red-reminder terminal queues now both guarantee that oversized bind gaps keep the pending message queued instead of silently rebinding it.

## 2026-06-17 - Phase 14 should protect terminal discard-on-resolution behavior
- Decision: Add smoke tests for the path where a terminal pending message is not bound because another recent order with the same strong match key already consumed that terminal outcome.
- Rationale: The discard branch is the last safety valve after bind-gap rejection, and it is easy to regress when tuning terminal matching or resolution-status lookup.
- Impact: The system-message `refund_cancelled` path and the red-reminder `cancelled` path now both prove that already-consumed terminal updates are dropped and cleaned up correctly.

## 2026-06-17 - Phase 15 should lock down multi-update pending consumption
- Decision: Add direct smoke coverage for `on_order_details_fetched(...)` consuming multiple pending updates for one order in sequence.
- Rationale: The single-update case was already covered, but the real queue consumer loops over an arbitrary list of updates. A sequential progression test guards against future changes that might prematurely stop after the first update or leave stale queue entries behind.
- Impact: The detail-fetched entrypoint now explicitly proves it can drain a multi-step pending queue and leave the order in the final expected state.

## 2026-06-17 - Phase 16 should prove batch queue draining across multiple orders
- Decision: Add direct smoke coverage for `process_all_pending_updates()` draining more than one order bucket in the same pass.
- Rationale: The per-order pending consumer was already covered, but the batch wrapper is the higher-level queue drain path and needs proof that it iterates through all queued order IDs rather than stopping after the first processed bucket.
- Impact: The pending-update batch processor now explicitly proves it can clear multiple queued orders and leave the in-memory queue empty afterward.

## 2026-06-17 - Phase 17 should prove batch draining survives mixed success
- Decision: Add direct smoke coverage for `process_all_pending_updates()` when one queued order still requeues while a later order bucket can be processed successfully.
- Rationale: The all-success path is useful, but the batch wrapper's real production value is that it keeps making progress even when one bucket cannot be applied yet. Without direct coverage, a future refactor could accidentally stop iteration after the first failed bucket.
- Impact: The batch queue processor now explicitly proves it continues through mixed-success pending work and preserves the failed bucket for later retry.

## 2026-06-17 - Phase 18 should prove detail-fetched queue draining survives a failed update
- Decision: Add direct smoke coverage for `on_order_details_fetched()` when one queued update fails validation but a later queued update for the same order is still valid.
- Rationale: The detail-fetched entrypoint uses its own out-of-lock consumer path instead of `process_pending_updates()`, so the mixed-result behavior needed independent proof. Otherwise a refactor could accidentally stop after the first failed update and strand later valid work.
- Impact: The detail-fetched queue consumer now explicitly proves it keeps draining the fetched order's updates and applies later valid transitions even when an earlier queued update fails.

## 2026-06-17 - Phase 19 should prove direct system updates cannot roll back a shipped order
- Decision: Add direct smoke coverage for `handle_system_message()` when a lower-priority system update arrives for an order already in a later state.
- Rationale: The priority guard sits outside the pending-queue paths and protects already-advanced orders from unrelated regression or replay noise. It needs a focused regression test so a refactor does not accidentally remove the guard or start surfacing rollback side effects.
- Impact: Direct system-message handling now explicitly proves a lower-priority update is treated as handled without mutating the stored higher-priority order state.

## 2026-06-17 - Phase 20 should prove completed-terminal delayed messages are discarded after prior consumption
- Decision: Add direct smoke coverage for `on_order_id_extracted()` when a queued terminal system message with `new_status == "completed"` should be discarded because another recent order with the same strong key is already completed.
- Rationale: The delayed-binding discard logic already had focused coverage for `refund_cancelled` and red-reminder `cancelled`, but the `completed` branch in `_get_terminal_resolution_statuses()` was still unproven. Locking it down reduces the chance of silently rebinding already-consumed completion outcomes onto later orders.
- Impact: Delayed terminal system-message handling now explicitly proves completed outcomes are discarded and cleaned up once a matching recent order has already consumed them.

## 2026-06-17 - Phase 21 should prove shipped terminal messages are discarded after prior consumption
- Decision: Add direct smoke coverage for `on_order_id_extracted()` when a queued terminal system message with `new_status == "shipped"` should be discarded because another recent order with the same strong key is already in a shipment-compatible resolved state.
- Rationale: The shipped terminal branch in `_get_terminal_resolution_statuses()` is symmetric with the completed branch but had not yet been directly locked down. This keeps the discard behavior consistent across terminal outcomes and prevents rebinding already-consumed shipment events to later orders.
- Impact: Delayed terminal system-message handling now explicitly proves shipped outcomes are discarded and cleaned up once a matching recent order has already consumed them.

## 2026-06-17 - Phase 22 should prove failed direct backfill falls through to pending queue
- Decision: Add direct smoke coverage for `handle_red_reminder_message()` when no order id can be extracted, a unique old order is found, but updating that old order fails.
- Rationale: The no-order-id fast path is only safe if a failed direct backfill does not silently drop the event. The implementation already falls through to the pending-queue path when `_try_resolve_cancelled_message_without_order_id()` returns `False`, so a focused regression test is the cleanest way to lock that fallback behavior in.
- Impact: No-order-id red-reminder handling now explicitly proves failed direct backfill attempts still preserve the event by queueing a temporary pending update and reminder entry.

## 2026-06-17 - Phase 23 should prove failed direct system backfill falls through to pending queue
- Decision: Add direct smoke coverage for `handle_system_message()` when no order id can be extracted, the resolved status is `cancelled`, a unique old order is found, but updating that old order fails.
- Rationale: This is the system-message twin of the red-reminder fallback path. Locking both sides down keeps no-order-id cancellation handling behaviorally symmetric and reduces the chance that one path silently drops events while the other preserves them.
- Impact: No-order-id system-message handling now explicitly proves failed direct backfill attempts still preserve the event by queueing a temporary pending update and system-message entry.

## 2026-06-17 - Phase 24 should prove direct cancelled system backfill succeeds without queueing
- Decision: Add direct smoke coverage for `handle_system_message()` when no order id can be extracted, the resolved status is `cancelled`, and a unique old order can be updated successfully by strong match key.
- Rationale: After locking down the failure fallback in phase 23, the matching success path also needed direct proof so the no-order-id system-message branch is covered on both sides of the decision. This confirms the handler can resolve the event immediately and avoid unnecessary pending-queue churn.
- Impact: No-order-id system-message handling now explicitly proves a unique cancelled message can be backfilled straight onto the old order without creating temporary pending entries.

## 2026-06-17 - Phase 25 should prove ambiguous direct system backfill falls through to queueing
- Decision: Add direct smoke coverage for `handle_system_message()` when no order id can be extracted, the resolved status is `cancelled`, and more than one old order matches the strong key.
- Rationale: `_try_resolve_cancelled_message_without_order_id()` has an ambiguity guard that must refuse direct mutation when multiple candidates are plausible. Without a focused regression test, a later refactor could silently update the first matched order and corrupt order history.
- Impact: No-order-id system-message handling now explicitly proves ambiguous direct backfill attempts preserve the event by queueing it for later binding instead of mutating an arbitrary old order.

## 2026-06-17 - Phase 26 should mirror ambiguity fallback coverage for red reminders
- Decision: Add direct smoke coverage for `handle_red_reminder_message()` when no order id can be extracted and more than one old order matches the strong key.
- Rationale: The red-reminder path shares the same `_try_resolve_cancelled_message_without_order_id()` ambiguity guard as system messages, so asymmetrical coverage would leave one entrypoint free to regress into mutating an arbitrary old order.
- Impact: No-order-id red-reminder handling now explicitly proves ambiguous direct backfill attempts preserve the event by queueing it for later binding instead of mutating one of several plausible old orders.

## 2026-06-17 - Phase 27 should lock down missing-strong-key fallthrough for system messages
- Decision: Add direct smoke coverage for `handle_system_message()` when no order id can be extracted but the strong key is incomplete.
- Rationale: `_try_resolve_cancelled_message_without_order_id()` requires `has_strong_match_key`; without a focused regression test, a later refactor could accidentally try to backfill from partial match data instead of falling back to the pending queue.
- Impact: No-order-id system-message handling now explicitly proves incomplete match context preserves the event by queueing it for later binding instead of attempting direct backfill.

## 2026-06-17 - Phase 28 should mirror missing-strong-key fallthrough coverage for red reminders
- Decision: Add direct smoke coverage for `handle_red_reminder_message()` when no order id can be extracted but the strong key is incomplete.
- Rationale: The red-reminder entrypoint shares the same strong-key gate as system messages, so asymmetrical coverage would still leave one no-order-id cancellation path open to accidental direct backfill from partial match data.
- Impact: No-order-id red-reminder handling now explicitly proves incomplete match context preserves the event by queueing it for later binding instead of attempting direct backfill.

## 2026-06-17 - Phase 29 should target the live runtime seam before more handler branches
- Decision: Add smoke coverage for `XianyuLive.handle_message(...)` forwarding parsed `sid`, `buyer_id`, and `item_id` into `order_status_handler.on_order_id_extracted(...)`.
- Rationale: Handler-level delayed-binding tests were already strong, but they could not detect a regression where the runtime entrypoint stopped passing `match_context` through. A focused seam test closes that gap with much less noise than a broader end-to-end automation harness.
- Impact: The test suite now proves the production live-message path preserves delayed-binding context at the handoff between `XianyuAutoAsync` and `OrderStatusHandler`.

## 2026-06-17 - Phase 30 should extend runtime seam coverage to direct status handler entrypoints
- Decision: Add smoke coverage for `XianyuLive.handle_message(...)` forwarding parsed match context into both `handle_system_message(...)` and the later `handle_red_reminder_message(...)` fallback seam.
- Rationale: After phase 29 proved the order-id extraction handoff, the next meaningful runtime gap was the pair of direct status-handler entrypoints living later in the same live method. Covering both keeps the runtime seam aligned with the existing handler-focused queueing guarantees.
- Impact: The suite now proves the live message path preserves `sid`, `buyer_id`, and `item_id` across all major order-status handoffs currently used in `XianyuAutoAsync`.

## 2026-06-17 - Runtime seam tests should avoid earlier dedicated shortcut branches
- Decision: Use a non-terminal red-reminder fixture for the runtime fallback seam test instead of `交易关闭`.
- Rationale: `交易关闭` is consumed by an earlier dedicated red-reminder order-status branch inside `handle_message(...)`, so it cannot meaningfully validate the later `handle_red_reminder_message(...)` fallback seam. The adjusted fixture keeps the test aligned with the actual branch under review.
- Impact: Phase-30 runtime seam coverage now exercises the intended fallback path directly instead of asserting on a branch that production control flow never reaches.

## 2026-06-17 - Phase 31 should explicitly cover the terminal red-reminder runtime shortcut
- Decision: Add smoke coverage for the early `交易关闭` red-reminder branch in `XianyuLive.handle_message(...)` that calls `handle_red_reminder_order_status(...)`.
- Rationale: After phase 30 proved the later direct status-handler seams, the dedicated terminal shortcut became the remaining high-value runtime path in the same area without explicit smoke coverage. Covering it closes the last obvious runtime handoff gap before moving to broader route-level questions.
- Impact: The suite now proves the live message path handles terminal red reminders through the intended shortcut branch and does not accidentally route them through the later fallback seams.

## 2026-06-17 - Phase 32 should target the detail-refresh service seam directly
- Decision: Add a focused smoke test around the real `XianyuLive.fetch_order_detail_info(...)` method body, with external detail fetching and DB dependencies stubbed, to verify the successful write path triggers `handle_order_detail_fetched_status(...)` and then `on_order_details_fetched(...)`.
- Rationale: Route-level refresh and history-sync tests already prove higher-level success outcomes, but they do not directly guarantee that the runtime detail-refresh entrypoint bridges successful persistence into the order-status handler queue-consumption hooks. A seam test at this layer closes that gap without dragging in browser or live-platform dependencies.
- Impact: The suite now explicitly proves the successful detail-refresh service entrypoint advances into the handler follow-up hooks that unblock pending order-status updates.
