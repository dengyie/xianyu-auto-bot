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
- Decision: Use a non-terminal red-reminder fixture for the runtime fallback seam test instead of `浜ゆ槗鍏抽棴`.
- Rationale: `浜ゆ槗鍏抽棴` is consumed by an earlier dedicated red-reminder order-status branch inside `handle_message(...)`, so it cannot meaningfully validate the later `handle_red_reminder_message(...)` fallback seam. The adjusted fixture keeps the test aligned with the actual branch under review.
- Impact: Phase-30 runtime seam coverage now exercises the intended fallback path directly instead of asserting on a branch that production control flow never reaches.

## 2026-06-17 - Phase 31 should explicitly cover the terminal red-reminder runtime shortcut
- Decision: Add smoke coverage for the early `浜ゆ槗鍏抽棴` red-reminder branch in `XianyuLive.handle_message(...)` that calls `handle_red_reminder_order_status(...)`.
- Rationale: After phase 30 proved the later direct status-handler seams, the dedicated terminal shortcut became the remaining high-value runtime path in the same area without explicit smoke coverage. Covering it closes the last obvious runtime handoff gap before moving to broader route-level questions.
- Impact: The suite now proves the live message path handles terminal red reminders through the intended shortcut branch and does not accidentally route them through the later fallback seams.

## 2026-06-17 - Phase 32 should target the detail-refresh service seam directly
- Decision: Add a focused smoke test around the real `XianyuLive.fetch_order_detail_info(...)` method body, with external detail fetching and DB dependencies stubbed, to verify the successful write path triggers `handle_order_detail_fetched_status(...)` and then `on_order_details_fetched(...)`.
- Rationale: Route-level refresh and history-sync tests already prove higher-level success outcomes, but they do not directly guarantee that the runtime detail-refresh entrypoint bridges successful persistence into the order-status handler queue-consumption hooks. A seam test at this layer closes that gap without dragging in browser or live-platform dependencies.
- Impact: The suite now explicitly proves the successful detail-refresh service entrypoint advances into the handler follow-up hooks that unblock pending order-status updates.

## 2026-06-17 - Phase 33 should lock down detail-refresh handler failure isolation
- Decision: Add a failure-path smoke test proving `XianyuLive.fetch_order_detail_info(...)` still returns the fetched detail payload when `on_order_details_fetched(...)` raises after successful persistence.
- Rationale: The success seam from phase 32 established that the follow-up hooks run, but the adjacent production risk is silent regression in robustness: delayed refresh callers, forced refresh callers, and route-level refresh paths should not lose an otherwise successful detail fetch merely because the post-persistence handler follow-up throws. A focused seam test locks down that contract without broadening the production surface.
- Impact: The suite now explicitly proves the detail-refresh entrypoint isolates handler follow-up failures and preserves the successfully fetched detail result for its callers.

## 2026-06-17 - Phase 34 should lock down detail-refresh write-failure isolation
- Decision: Add a failure-path smoke test proving `XianyuLive.fetch_order_detail_info(...)` still returns the fetched detail payload when `insert_or_update_order(...)` returns `False`, while skipping handler follow-up hooks.
- Rationale: After proving both the success path and post-persistence handler-failure isolation, the adjacent consistency boundary is the persistence failure branch itself. If write-through fails, downstream status handlers must not run against undurable state. A focused seam test pins that contract without broadening scope into unrelated delivery or route behavior.
- Impact: The suite now explicitly proves detail-refresh callers still receive the fetched payload, but no handler-side state advancement occurs when the persistence layer declines the write.

## 2026-06-17 - Phase 35 should lock down basic-order-info handler failure isolation
- Decision: Add a focused `_auto_delivery(...)` seam test proving prepared delivery content still returns when `handle_order_basic_info_status(...)` raises after the new order shell is written.
- Rationale: The adjacent runtime seam after the detail-refresh series is the basic-order-info path inside `_auto_delivery(...)`. It shares the same production risk shape: a post-write status helper can fail even though the core business action still has enough information to continue. Locking this down prevents a helper exception from incorrectly aborting otherwise valid automatic delivery preparation.
- Impact: The suite now explicitly proves `_auto_delivery(...)` isolates basic-order-info handler failures and keeps returning prepared delivery content after successful initial order persistence.

## 2026-06-17 - Phase 36 should lock down basic-order-info write-failure isolation
- Decision: Add a focused `_auto_delivery(...)` seam test proving prepared delivery content still returns when initial basic-order-info persistence returns `False`, while `handle_order_basic_info_status(...)` is not called.
- Rationale: Phase 35 covered the post-write handler failure branch. The symmetric production boundary is the write-failure branch: automatic delivery preparation can continue, but status helpers must not run against an order shell that did not persist. Locking both sides keeps the runtime contract explicit.
- Impact: The suite now explicitly proves `_auto_delivery(...)` does not advance basic order status when prewrite fails, while still returning the prepared delivery content to its caller.

## 2026-06-17 - Phase 37 should lock down existing-order basic-info bypass
- Decision: Add a focused `_auto_delivery(...)` seam test proving existing orders skip basic-order-info prewrite and `handle_order_basic_info_status(...)`, while still returning prepared delivery content.
- Rationale: After covering new-order write success, handler failure, and write failure, the remaining adjacent branch is the already-persisted order bypass. This branch prevents duplicate writes and duplicate status-helper side effects, so it should be directly guarded before moving to data-card-specific behavior.
- Impact: The suite now explicitly proves `_auto_delivery(...)` leaves existing order shells untouched during delivery-content preparation.

## 2026-06-17 - Phase 38 should lock down data-card reservation metadata
- Decision: Add focused `_auto_delivery(...)` seam tests for data-card reservation success and reservation failure instead of broadening into live message sending.
- Rationale: The reservation branch is the contract that prevents duplicate batch-data delivery and produces metadata needed by later mark-sent/release hooks. A deterministic seam test can prove the reservation call, returned content, and metadata without involving websocket delivery or live platform state.
- Impact: The suite now explicitly proves data-card preparation returns reserved content only after a successful reservation and leaves pending-consume metadata empty when no reservation is available.

## 2026-06-17 - Phase 39 should cover reservation closure at the delivery route
- Decision: Cover reservation mark-sent and release behavior through the manual delivery route smoke tests instead of adding a narrower helper-only unit test.
- Rationale: The production contract is not just the helper return value; it is the route-level behavior that threads `_auto_delivery(...)` metadata through message sending, reservation closure, delivery logs, and finalization progress. A route smoke test catches metadata wiring regressions that a helper-only test would miss.
- Impact: The suite now explicitly proves reservation-backed manual delivery both closes the reservation on success and releases it when post-send mark-sent handling fails.

## 2026-06-18 - Phase 40 should lock down pending-finalize state after send-side finalization failure
- Decision: Add route-level smoke coverage for the branch where manual delivery sends successfully, reservation mark-sent succeeds, but `finalize_delivery_after_send(...)` returns a failure payload.
- Rationale: This branch carries a subtle but important contract: once the buyer has already received the content, the system must preserve a recoverable pending-finalize state instead of reverting to pending ship or falsely claiming delivery is complete. The route owns that state transition, so the test should live there.
- Impact: The suite now explicitly proves finalize-after-send failures keep reservation-backed units recoverable in `partial_pending_finalize` with a visible failure log.

## 2026-06-18 - Phase 41 should prove pending-finalize replay recovers without duplicate sends
- Decision: Add route-level smoke coverage for manual delivery retry when a unit already has persisted `sent` finalization state and must replay only the finalize hook.
- Rationale: After phase 40 established the recoverable state, the adjacent contract is recovery itself. The riskiest regression here is duplicate delivery to the buyer or silent failure to consume the saved pending-finalize record. A route smoke test can prove both the absence of re-send behavior and the correctness of the replay result.
- Impact: The suite now explicitly proves pending-finalize recovery uses persisted metadata to complete side effects without re-sending content, and that replay failures remain visible and recoverable.

## 2026-06-18 - Phase 42 should lock down replay-only early return after pending-finalize recovery
- Decision: Add route-level smoke coverage for the manual delivery branch where pending-finalize replay succeeds and no unsent units remain, rather than adding a helper-only assertion around `remaining_unit_indexes`.
- Rationale: The production contract lives at the delivery route boundary: after replaying saved finalization work, the route must stop before any new send or `_auto_delivery(...)` preparation if there is nothing left to ship. A route smoke test is the smallest place that can prove the absence of duplicate buyer-visible actions.
- Impact: The suite now explicitly proves replay-only retries return success immediately after consuming the saved pending-finalize record, with no new delivery preparation or resend side effects.

## 2026-06-18 - Phase 43 should lock down refresh-route soft failure semantics
- Decision: Add route-level smoke coverage for `/api/orders/{order_id}/refresh` when `fetch_order_detail_info(...)` returns a falsey result instead of raising.
- Rationale: The success path and ownership rejection path were already covered, but the adjacent production contract is the soft-failure branch where the live refresh cannot produce detail data. Without a focused test, the route could regress into silently reporting success or mutating stored state after a no-result refresh.
- Impact: The suite now explicitly proves refresh failures with no returned detail leave order state unchanged and surface a user-visible `updated=False` response.

## 2026-06-18 - Phase 44 should lock down history-sync job ownership boundaries
- Decision: Add route-level smoke coverage for foreign-user access to `GET /api/orders/history-sync/{job_id}` and `POST /api/orders/history-sync/{job_id}/cancel`.
- Rationale: The history-sync route cluster already had lifecycle and business-result coverage, but the background job object itself carries account scope, progress, and warning details. A focused ownership test is the cheapest way to prevent regressions that would expose or cancel another user's sync task.
- Impact: The suite now explicitly proves history-sync job status and cancellation stay scoped to the creating user.

## 2026-06-18 - Phase 45 should lock down password-login session ownership boundaries
- Decision: Add route-level smoke coverage for foreign-user access to `GET /password-login/check/{session_id}` and `POST /password-login/cancel/{session_id}`.
- Rationale: The password-login flow already has session lifecycle code, but the route surfaces live verification and error details and can mutate shared session state on cancel. A focused ownership test is the cheapest way to prevent regressions that would expose or cancel another user's login session.
- Impact: The suite now explicitly proves password-login session status and cancellation stay scoped to the creating user.

## 2026-06-18 - Phase 46 should lock down manual-cookie-import session ownership boundaries
- Decision: Add route-level smoke coverage for foreign-user access to `GET /manual-cookie-import/check/{session_id}`.
- Rationale: The manual cookie import flow uses the same session-scoped status pattern as password-login, but current smoke coverage only checked request validation. A focused ownership test is the cheapest way to prevent regressions that would expose another user's import-session verification state.
- Impact: The suite now explicitly proves manual-cookie-import session status stays scoped to the creating user.

## 2026-06-18 - Phase 47 should lock down qr-login session ownership boundaries
- Decision: Persist the creating user on generated qr-login sessions and reject foreign users from `GET /qr-login/check/{session_id}`.
- Rationale: The standard QR login flow already had a session status endpoint, but it lacked an ownership boundary even though the flow is user-scoped like password-login and qr-login-lite. Adding the owner field and gate keeps the live session status from leaking across accounts.
- Impact: The suite now explicitly proves qr-login session status stays scoped to the creating user.

## 2026-06-18 - Phase 48 should lock down face-verification screenshot ownership boundaries
- Decision: Add route-level smoke coverage for both `GET /face-verification/screenshot/{account_id}` and `DELETE /face-verification/screenshot/{account_id}`, using a second non-admin user fixture to model foreign access.
- Rationale: The screenshot routes expose or destroy sensitive verification artifacts keyed by account id. Admin access is intentionally broader, so the foreign-user regression test needs a true non-owner principal instead of the existing admin fixture.
- Impact: The suite now explicitly proves face-verification screenshots remain scoped to the owning user for both read and delete flows.

## 2026-06-18 - Phase 50 should lock down zero-candidate unmatched cancellation fallback
- Decision: Add smoke coverage for cancelled red reminders and cancelled system messages when strong match keys exist but no historical order candidates can be found.
- Rationale: The existing message-binding suite already covered ambiguous, missing-strong-key, and failed-direct-update fallback behavior, but the zero-candidate unmatched-cancellation branch was still only implied. This is the cleanest remaining fallback path in the delayed terminal-resolution flow.
- Impact: The suite now explicitly proves unmatched cancellation messages stay in the strict pending queues instead of mutating unrelated orders when no direct backfill target exists.

## 2026-06-18 - Phase 51 should lock down qr-login cooldown ownership boundaries
- Decision: Require cookie ownership on `POST /qr-login/reset-cooldown/{cookie_id}` and `GET /qr-login/cooldown-status/{cookie_id}`, then cover both routes with smoke tests.
- Rationale: These qr-login cooldown endpoints expose or mutate account-scoped runtime state keyed only by `cookie_id`, and they sat adjacent to the already-hardened refresh route. Without matching route guards, another authenticated user could inspect or reset another account's cooldown state.
- Impact: Cooldown status and reset behavior are now scoped to the owning user, and the accounts smoke suite protects both foreign-user denial and owner success.
## 2026-06-18 - Phase 52 should lock the existing download-token contract
- Decision: Keep the current `/api/files/{file_id}/download-token` behavior unchanged and add a focused smoke regression around the existing forbidden outcome for a missing file id.
- Rationale: During phase-52 exploration, the suspected production bug turned out to be a false alarm after reconciling the live worktree against git history. The useful remaining work was to preserve the current route contract with a direct regression test instead of changing access semantics.
- Impact: The file-download token flow now has explicit smoke coverage for the missing-file branch without expanding the production change surface.
## 2026-06-18 - Phase 53 should lock notification-channel read ownership
- Decision: Add smoke coverage for `GET /notification-channels/{channel_id}` so foreign users cannot read another user's channel details.
- Rationale: The update/delete paths for notification channels were already protected, but the read path remained the last unverified owner-scoped route in that cluster. A small read regression keeps the access model consistent without changing runtime behavior.
- Impact: Notification channel details are now explicitly guarded on read as well as mutation, and the smoke suite covers the foreign-user denial path.

## 2026-06-18 - Phase 54 should lock message-notification read ownership
- Decision: Add smoke coverage for `GET /message-notifications/{cid}` so foreign users cannot read another user's account notification config.
- Rationale: The endpoint already checks cookie ownership in production code, but the read path had not yet been pinned by a regression test. A narrow smoke case keeps the notification cluster's account-bound reads covered without broadening runtime behavior.
- Impact: Account notification reads are now explicitly guarded by ownership, and the smoke suite covers both denial and owner success paths.

## 2026-06-18 - Phase 55 should lock message-notification account-delete ownership
- Decision: Add smoke coverage for `DELETE /message-notifications/account/{cid}` so foreign users cannot clear another user's account notification config.
- Rationale: The delete route already threads `user_id` through both route and data-layer filters, but it remained an unverified owner-scoped mutation in the same notification cluster. A focused regression keeps the delete contract aligned with the read and create protections already covered.
- Impact: Account-level notification deletion is now explicitly guarded by ownership, and the smoke suite covers both foreign-user denial and owner cleanup success.

## 2026-06-18 - Phase 56 should lock single message-notification delete ownership
- Decision: Add smoke coverage for `DELETE /message-notifications/{notification_id}` so foreign users cannot delete another user's individual notification row.
- Rationale: The delete-by-id route already filters by the current user's owned channels at the data layer, but it was still unpinned by a regression test. A focused smoke case covers the last mutation seam in the notification cluster without broadening runtime behavior.
- Impact: Single notification deletion is now explicitly guarded by ownership, and the smoke suite covers both foreign-user denial and owner cleanup success.

## 2026-06-18 - Phase 58 should lock notification test-send success
- Decision: Add smoke coverage for `POST /notification-templates/test` using a local webhook recorder to prove the owner can send a test notification through their own enabled channel.
- Rationale: The route already had failure coverage for missing channels, but its success path still lacked direct proof and depended on real outbound delivery if exercised naively. A local recorder keeps the test deterministic while verifying the real send flow and current-user channel scoping.
- Impact: Notification test-send success is now explicitly covered without external network dependence, and the suite proves the current user's enabled channel is used.

## 2026-06-18 - Phase 59 should lock default-reply clear-records ownership
- Decision: Add smoke coverage for `POST /default-replies/{cid}/clear-records` so foreign users cannot clear another user's default-reply records.
- Rationale: The route already enforced cookie ownership, but the clear-records mutation remained unpinned by a regression test despite being part of the same default-reply ownership surface as read/update/delete.
- Impact: Default-reply record cleanup is now explicitly scoped to the owning user, and the smoke suite covers both foreign-user denial and owner cleanup success.

## 2026-06-18 - Phase 60 should lock item-scoped keyword ownership
- Decision: Add smoke coverage for `GET /keywords-with-item-id/{cid}` and `POST /keywords-with-item-id/{cid}` so foreign users cannot read or overwrite another user's item-scoped keyword rules.
- Rationale: The plain `/keywords/{cid}` ownership surface was already covered, but the item-scoped keyword variant uses a separate request model and response shape. A focused regression is the cheapest way to keep both read and mutation semantics aligned without changing runtime behavior.
- Impact: Item-scoped keyword reads and writes are now explicitly guarded by ownership, and the smoke suite covers both foreign-user denial and owner success.

## 2026-06-18 - Phase 61 should lock cookie remark ownership
- Decision: Add smoke coverage for `GET /cookies/{cid}/remark` and `PUT /cookies/{cid}/remark` so foreign users cannot read or overwrite another user's account remark.
- Rationale: The remark surface is a small but distinct account-scoped endpoint pair, and it had no direct regression coverage even though the route already enforces ownership. A single focused test keeps the access model consistent without broadening behavior.
- Impact: Cookie remarks are now explicitly guarded by ownership, and the smoke suite covers both foreign-user denial and owner success.

## 2026-06-18 - Phase 62 should lock cookie pause-duration ownership
- Decision: Add smoke coverage for `GET /cookies/{cid}/pause-duration` and `PUT /cookies/{cid}/pause-duration` so foreign users cannot read or overwrite another user's auto-reply pause duration.
- Rationale: Pause-duration is another narrow cookie-metadata surface with its own read/write route pair and owner gate, but it had no direct regression protection. A focused owner-boundary test keeps the cookie-metadata cluster consistent without expanding runtime behavior.
- Impact: Cookie pause-duration reads and writes are now explicitly guarded by ownership, and the smoke suite covers both foreign-user denial and owner success.

## 2026-06-18 - Phase 63 should lock cookie auto-confirm ownership
- Decision: Add smoke coverage for `GET /cookies/{cid}/auto-confirm` and `PUT /cookies/{cid}/auto-confirm` so foreign users cannot read or overwrite another user's auto-confirm setting.
- Rationale: Auto-confirm is another cookie-scoped settings pair with an ownership check that had no direct regression coverage. A single focused test keeps the cookie-settings cluster coherent without changing runtime behavior.
- Impact: Cookie auto-confirm reads and writes are now explicitly guarded by ownership, and the smoke suite covers both foreign-user denial and owner success.

## 2026-06-18 - Phase 64 should lock cookie auto-comment ownership
- Decision: Add smoke coverage for `GET /cookies/{cid}/auto-comment` and `PUT /cookies/{cid}/auto-comment` so foreign users cannot read or overwrite another user's auto-comment setting.
- Rationale: Auto-comment is another cookie-scoped setting pair with an ownership check but no direct regression coverage. A single focused test keeps the cookie-settings cluster coherent without changing runtime behavior.
- Impact: Cookie auto-comment reads and writes are now explicitly guarded by ownership, and the smoke suite covers both foreign-user denial and owner success.
## 2026-06-18 - Phase 65 should bind comment-template mutations to cid
- Decision: Require comment-template update/delete/activate operations to prove `template_id` belongs to the URL `cid` before mutating state.
- Rationale: Route-level cookie ownership alone was insufficient because a user could combine their own `cid` with another cookie's `template_id`. Binding at the DB helper boundary keeps route and data-layer behavior aligned.
- Impact: Cross-cookie template update, delete, and activation attempts now fail as not found, and smoke coverage locks the boundary.
## 2026-06-18 - Phase 66 should lock comment-template list/create ownership
- Decision: Add smoke coverage for `GET /cookies/{cid}/comment-templates` and `POST /cookies/{cid}/comment-templates` so foreign users cannot list or create templates under another user's cookie.
- Rationale: Phase 65 closed template-id mutation risks, but the list/create route pair also needs direct regression coverage for the route-level cookie owner gate.
- Impact: Comment-template list/create ownership is now explicitly covered for foreign-user denial and owner success.
## 2026-06-18 - Phase 67 should lock typed keyword reads
- Decision: Add smoke coverage for `GET /keywords-with-type/{cid}` so foreign users cannot read another user's typed keyword rules.
- Rationale: Plain keyword and item-scoped keyword routes already had owner-boundary coverage, but the typed keyword read endpoint has a distinct response path and owner check.
- Impact: Typed keyword reads are now explicitly covered for foreign-user denial and owner success.
## 2026-06-18 - Phase 68 should lock item-info route ownership
- Decision: Add smoke coverage for `GET /items/cookie/{cookie_id}`, `GET /items/{cookie_id}/{item_id}`, `PUT /items/{cookie_id}/{item_id}`, and `DELETE /items/{cookie_id}/{item_id}`.
- Rationale: The item-info routes already enforce cookie ownership and use `cookie_id` in data-layer item operations, but the route cluster lacked focused cross-user regression coverage. A route-level smoke test is the smallest way to prove foreign users cannot list, read, update, or delete another user's item records while the owner path still works.
- Impact: Item-info route ownership is now explicitly covered without changing production behavior.
## 2026-06-18 - Phase 69 should bind item-reply metadata joins to cookie
- Decision: Update `get_itemReplays_by_cookie(...)` so its `item_info` join matches both `cookie_id` and `item_id`, and add route smoke coverage for item-reply list/read/update/delete/batch-delete ownership.
- Rationale: Route-level owner checks prevented direct foreign mutations, but the list query could attach metadata from another cookie when two accounts had the same `item_id`. Binding the join to the cookie keeps reply data and item metadata in the same ownership scope.
- Impact: Item-reply list responses no longer leak or mis-associate cross-cookie item metadata, and the smoke suite protects both owner boundaries and same-`item_id` isolation.
## 2026-06-18 - Phase 70 should require ownership on item flag mutations
- Decision: Add cookie ownership checks to the item multi-spec and multi-quantity delivery update routes before calling the item flag data-layer helpers.
- Rationale: The helpers update by `cookie_id` and `item_id`, but the API routes previously accepted any authenticated user's request for another user's `cookie_id`. Route-level authorization keeps these item-scoped mutations aligned with the surrounding item-info and item-reply route clusters.
- Impact: Foreign users now receive `403` before item flag mutation, owner updates still work, and smoke coverage protects both route surfaces.
## 2026-06-18 - Phase 71 should lock chat keyword item route ownership
- Decision: Add smoke coverage for chat keyword item read/save/copy and chat item list routes while keeping existing production behavior unchanged.
- Rationale: The routes already delegate cookie authorization to `_ensure_cookie_access(...)` and DB helpers scope keyword operations by `cookie_id`, but this chat-specific API cluster had no focused owner-boundary regression.
- Impact: Foreign-user denial and owner success paths for chat item keyword workflows are now explicitly covered.
## 2026-06-18 - Phase 72 should require ownership before AI reply tests
- Decision: Add a current-user cookie ownership check to `POST /ai-reply-test/{cookie_id}` and smoke coverage for AI reply settings read/update/list/test ownership.
- Rationale: AI reply test generation is an account-scoped operation that can expose account behavior and consume configured AI resources. The route previously trusted global cookie-manager existence instead of proving that the authenticated user owned the cookie, unlike adjacent AI settings routes.
- Impact: Foreign users now receive `403` before AI reply test generation for another user's cookie, aggregate settings stay filtered to the caller's cookies, and owner settings/test flows remain covered.
## 2026-06-18 - Phase 73 should lock account runtime route ownership
- Decision: Add focused smoke coverage for account runtime/config routes: account-info, details, runtime-status, conversation history, session keepalive, and proxy read/update.
- Rationale: These routes already enforce ownership, but they expose sensitive account configuration or operational account state and lacked the same focused cross-user regression coverage as nearby cookie settings and AI reply routes.
- Impact: Foreign-user denial and owner success paths are now explicitly covered for the account runtime/config cluster without changing production behavior.
## 2026-06-18 - Phase 74 should preserve user-scoped missing-resource status
- Decision: Re-raise explicit `HTTPException` values in single-card and single-delivery-rule read routes, and add focused ownership coverage for cards and delivery rules.
- Rationale: Foreign or missing user-scoped cards/rules should return the intended `404` contract instead of being wrapped as `500`. The surrounding data-layer helpers already bind these resources to `user_id`, so smoke coverage should lock that boundary.
- Impact: Foreign reads now return `404`, owner operations remain functional, list endpoints are explicitly filtered by user, and delivery rules cannot be created with another user's card.

## 2026-06-18 - Phase 75 should treat user backup imports as scoped restores
- Decision: Restrict user-level backup imports to user-owned tables, skip global `system_settings`, and rewrite imported `user_id` values for cookies, cards, delivery rules, and notification channels to the authenticated user.
- Rationale: `POST /backup/import` is a user endpoint, so it must not trust ownership values from a backup file or mutate global settings during a user restore.
- Impact: User backup restores can no longer inject resources under another `user_id` or change system settings, while system-level backup behavior remains unchanged.
## 2026-06-18 - Phase 76 should normalize update-management admin checks
- Decision: Add `_ensure_update_admin(...)` for `/api/update/*` management endpoints and use the same compatibility rule as the rest of the admin surface: `is_admin=True` or `username == admin`.
- Rationale: Several update-management endpoints previously accepted only the literal username `admin`, while restart accepted only `is_admin`; this could incorrectly reject delegated admins or legacy admin tokens depending on endpoint.
- Impact: Regular users remain forbidden from update-management operations, delegated admins can operate consistently, and legacy admin username compatibility is preserved.

## 2026-06-18 - Phase 77 should gate live account item operations by cookie owner
- Decision: Add `_ensure_cookie_access(...)` to the account item sync, paged item fetch, and polish routes before reading cookie data or constructing `XianyuLive`.
- Rationale: These routes execute live account-scoped operations from caller-supplied cookie ids, so route-level ownership must be proved before any external side effect or secret access.
- Impact: Foreign users now receive the standard cookie-access `403`, owner operations remain compatible, and smoke coverage prevents the authorization boundary from regressing.

## 2026-06-18 - Phase 78 should pin chat runtime owner gates with smoke coverage
- Decision: Add route-level smoke coverage for chat session list, chat message list, and chat send using local DB rows plus a fake connected live instance.
- Rationale: The existing `_ensure_cookie_access(...)` gates were correct, but this high-value chat API cluster lacked focused cross-user regression coverage for both read and send paths.
- Impact: Foreign users are now explicitly proven unable to read another account's chat state or send through another account, while owner read/send behavior remains covered.
