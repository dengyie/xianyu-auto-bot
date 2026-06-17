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
