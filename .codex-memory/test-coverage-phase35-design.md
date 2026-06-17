# Phase 35 Design - basic-order-info handler failure isolation

## Goal

Add a focused smoke regression proving `_auto_delivery(...)` still returns prepared delivery content when the basic-order-info status handler raises after successful initial order persistence.

## Scope

- Extend the runtime seam smoke suite with a narrow `_auto_delivery(...)` test.
- Stub the rule lookup and content preparation dependencies to keep the branch deterministic.
- Target the branch where:
  - a new order is inserted successfully
  - `handle_order_basic_info_status(...)` raises
  - delivery content generation should still continue

## Why This Phase

The neighboring `fetch_order_detail_info(...)` seams now cover:

- success handoff
- post-persistence handler failure
- persistence failure

The adjacent unguarded runtime handoff is the basic-order-info path inside `_auto_delivery(...)`, which writes a new order shell before invoking `handle_order_basic_info_status(...)`. A regression here could make automatic delivery fail purely because the post-write status helper throws.

## Test Shape

- Construct `XianyuLive` via `__new__` with only the attributes `_auto_delivery(...)` needs.
- Patch DB lookups to yield:
  - an item config with a normal title/detail
  - one matching text delivery rule
  - successful cookie lookup
  - no existing order
  - successful `insert_or_update_order(...)`
- Stub `fetch_order_detail_info(...)` to avoid extra browser work.
- Stub `_build_delivery_steps(...)` to return one text step.
- Configure `order_status_handler.handle_order_basic_info_status(...)` to raise.
- Assert:
  - `_auto_delivery(..., include_meta=True)` returns a successful result
  - order insertion is attempted once
  - `handle_order_basic_info_status(...)` is attempted once
  - the raised exception does not prevent delivery content preparation

## Acceptance

- Targeted seam tests pass.
- Full smoke suite passes.
- `compileall` passes for touched Python modules.
- Production review reports no new blocking findings for the phase-35 diff.
