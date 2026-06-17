# TODO

## In Progress
- [ ] Stage, commit, and push phase 12 queue-cleanup coverage updates to the draft GitHub PR

## Next
- [ ] Evaluate whether `on_order_id_extracted` still needs an additional regression around duplicate message hashes after the queue-level coverage is now complete
- [ ] Evaluate whether any pending-queue behavior also needs a broader service/route integration entrypoint test beyond the current handler-focused smoke coverage

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
