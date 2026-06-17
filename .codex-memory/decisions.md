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
