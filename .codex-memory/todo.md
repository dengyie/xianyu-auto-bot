# TODO

## In Progress
- [ ] Stage, commit, and push phase 14 terminal discard coverage updates to the draft GitHub PR

## Next
- [ ] Evaluate whether any pending-queue behavior also needs a broader service/route integration entrypoint test beyond the current handler-focused smoke coverage
- [ ] Evaluate whether any additional delayed-binding branches around alternate status transitions still deserve direct regression coverage

## Done
- [x] Add authz/cookie isolation/file token/system settings smoke coverage
- [x] Add notification channel/template API regression tests
- [x] Add keyword/default reply matching regression tests
- [x] Add scheduled task ownership and validation tests
- [x] Add order delivery state transition integration tests
- [x] Add order history sync lifecycle smoke tests
- [x] Add refund/detail-failure order lifecycle smoke tests
- [x] Add pending-queue/live-instance order lifecycle smoke tests
- [x] Add system-message binding and unmatched cancellation resolution smoke tests
- [x] Add ambiguity rejection and stale pending cleanup smoke tests
- [x] Add terminal recent-fallback binding and ambiguity smoke tests
- [x] Add red-reminder terminal recent-fallback binding and ambiguity smoke tests
- [x] Add duplicate-`message_hash` plus unique-`strong_key` queue binding smoke tests for system messages and red reminders
- [x] Add enqueue-entry cleanup coverage for stale system-message and red-reminder pending state
- [x] Add bind-gap rejection coverage so old terminal pending messages stay queued instead of binding across large time gaps
- [x] Add terminal discard coverage for refund-cancelled system messages and cancelled red reminders already consumed by a different recent order
