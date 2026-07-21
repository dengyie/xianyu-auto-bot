# Production Hardening And Layered Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复已确认的公网安全、启动可复现性、数据库恢复一致性和运行时生命周期问题，并把后续单体拆分约束为可独立验收的分层迁移。

**Architecture:** 先在现有 FastAPI 单体内建立安全边界、lifespan、维护锁和配置契约，再逐步把路由迁移到 API/application/domain/infrastructure 四层。闲鱼、Playwright、AI 和 SQLite 通过适配器接入；业务用例不直接依赖 FastAPI、全局字典或具体客户端。

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Pydantic 2, asyncio, SQLite, Playwright, pytest, Docker Compose, GitHub Actions.

## Global Constraints

- 不在主 `main` 工作树开发；所有修改先在 `codex/deep-review` 验证。
- P1 安全问题修复必须先有回归测试，再改实现。
- `/xianyu/reply` 只能接受内部服务认证；不允许通过固定测试密钥绕过校验。
- `/api/captcha` 的 HTTP 和 WebSocket 入口都必须校验部署级 `CAPTCHA_CONTROL_API_KEY`；后续再升级为一次性、短时、绑定 session 的控制令牌。
- 数据库恢复必须可暂停运行态、原子替换、完整性检查、刷新缓存并撤销旧会话。
- 健康检查不能阻塞事件循环；应用创建的后台任务必须可取消、可观测、可清理。
- 生产依赖必须可复现安装；CI 必须执行测试和静态检查，不只构建镜像。

## Baseline Evidence

- `reply_server.py` 约 14,000 行、`XianyuAutoAsync.py` 约 16,500 行，当前已有 236 个 smoke tests。
- 干净工作树导入 `reply_server.py` 曾因 `logs/analytics.log` 不存在而失败；创建忽略目录后为 `236 passed, 4 warnings`。
- `/xianyu/reply` 匿名请求可返回配置回复；`/send-message` 接受 `zhinina_test_key` 并返回成功。
- `static/login.html`、`README.md` 和 `run_web_only.py` 展示 `admin/admin123`，实际初始化逻辑生成随机密码。
- 本地工作树的 `slidex` 未安装；标准 `requirements.txt` 会安装它，因此 captcha router 必须按“依赖存在时启用”路径测试。

---

### Task 1: Establish A Reproducible Security Test Harness

**Files:**
- Create: `tests/smoke/test_public_boundaries.py`
- Modify: `tests/smoke/conftest.py`
- Modify: `pyproject.toml`
- Modify: `.gitignore`

**Interfaces:**
- Tests call the ASGI app through the existing `client` fixture.
- The security implementation must expose one internal verifier with a stable signature: `verify_internal_service_key(request: Request, expected_scope: str) -> None`.

- [x] **Step 1: Add failing boundary tests**

```python
def test_xianyu_reply_rejects_anonymous(client):
    response = client.post("/xianyu/reply", json=valid_reply_payload())
    assert response.status_code == 401

def test_send_message_test_key_is_rejected(client):
    response = client.post("/send-message", json={**valid_send_payload(), "api_key": "zhinina_test_key"})
    assert response.json()["success"] is False

def test_login_page_does_not_publish_default_password(client):
    response = client.get("/login.html")
    assert "admin123" not in response.text
```

- [x] **Step 2: Run the tests and confirm failure**

Run: `pytest tests/smoke/test_public_boundaries.py -q`

Expected: the anonymous reply and hard-coded key tests fail against the current implementation.

- [x] **Step 3: Track pytest configuration**

Create `pyproject.toml` with the existing test settings:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
timeout = 30
testpaths = ["tests"]
addopts = "-v --tb=short"

[tool.pytest.ini_options.markers]
smoke = "Smoke tests for critical path coverage"
```

- [x] **Step 4: Run the focused tests again**

Run: `pytest tests/smoke/test_public_boundaries.py -q`

Expected: tests still fail only on implementation assertions, proving the tests exercise the intended boundary.

---

### Task 2: Close Public API Boundaries

**Files:**
- Modify: `reply_server.py:2541-2745`
- Modify: `api_captcha_remote.py:1-270`
- Modify: `config.py` and `global_config.yml`
- Modify: `Start.py:445-475`
- Modify: `XianyuAutoAsync.py:14063-14095`
- Test: `tests/smoke/test_public_boundaries.py`

**Interfaces:**
- `SEND_MESSAGE_API_KEY` is the configured secret source; use `secrets.compare_digest`.
- `XIANYU_REPLY_API_KEY` authenticates the internal reply callback.
- Captcha control routes use the deployment-level `CAPTCHA_CONTROL_API_KEY` for both HTTP and WebSocket handshakes. Session-bound short-lived tokens remain a follow-up hardening item.

- [x] **Step 1: Remove bypasses and require the reply service key**

Delete the `zhinina_test_key` branch. Add a dependency that reads `XIANYU_REPLY_API_KEY`, rejects an unset key with a startup warning and rejects missing/incorrect request credentials with HTTP 401. Update `Start.py` and `XianyuAutoAsync.py` to send the configured key in the internal callback request.

- [x] **Step 2: Protect captcha HTTP and WebSocket routes**

Require `X-Captcha-Control-Key` (or `Authorization: Bearer`) for every HTTP route and accept the same credential during WebSocket handshake. Never expose `/sessions` without an authenticated control scope. Return 401/403 without leaking screenshots or session metadata. Track the session-bound token migration separately because the current caller contract only distributes a deployment key.

- [x] **Step 3: Remove default-password copy**

Replace the visible login hint with “首次启动密码请查看启动日志或 `ADMIN_PASSWORD` 配置”。`run_web_only.py` must never print a fixed credential. README must document random initialization and explicit `ADMIN_PASSWORD` usage.

- [x] **Step 4: Run focused verification**

Run: `pytest tests/smoke/test_public_boundaries.py tests/smoke/test_security_hardening.py -q`

Expected: all focused tests pass; anonymous reply, missing captcha token, wrong token and test key all fail closed.

---

### Task 3: Make Startup And Lifespan Deterministic

**Files:**
- Modify: `reply_server.py:59-75, 1296-1324, 1398-1445`
- Modify: `file_log_collector.py:57-90`
- Modify: `run_web_only.py`
- Modify: `tests/conftest.py`
- Test: `tests/smoke/test_startup_and_health.py`

**Interfaces:**
- Add `ensure_runtime_directories(root: Path) -> None`.
- Add an application lifespan that owns `scheduled_task_checker` and cancels it on shutdown.
- `/health/live` is a constant-time process check; `/health/ready` checks database and CookieManager state without sleeping.

- [x] **Step 1: Add clean-clone tests**

Test importing `reply_server` from a temporary directory without `logs/`, assert import succeeds; test `/health/live` completes under 100 ms; test application shutdown cancels the scheduled task.

- [x] **Step 2: Move directory creation before file handlers**

Create `logs`, `data`, `backups` and upload directories before constructing handlers. Use paths derived from `Path(__file__).resolve().parent`, not the process working directory.

- [x] **Step 3: Replace deprecated startup event and blocking health logic**

Use FastAPI lifespan context. Remove `psutil.cpu_percent(interval=1)` from readiness. Put metrics collection behind a separate endpoint or use `interval=None`.

- [x] **Step 4: Verify startup behavior**

Run: `pytest tests/smoke/test_startup_and_health.py -q && python -m compileall -q -f .`

Expected: clean import passes, live health is fast, scheduled task is cancelled, compile exit is zero.

---

### Task 4: Make Database Restore Transactional And Runtime-Aware

**Files:**
- Modify: `reply_server.py:11288-11396`
- Modify: `cookie_manager.py:45-58`
- Modify: `db_manager/base.py` (backup validation helper)
- Test: `tests/smoke/test_backup_restore_runtime.py`

**Interfaces:**
- Add `asyncio.Lock`/maintenance state owned by the application, not a module-local boolean.
- Add `validate_backup_database(path: Path) -> None` using required tables and `PRAGMA integrity_check`.
- Add `CookieManager.reload_from_db()` invocation after a successful restore.

- [x] **Step 1: Write restore failure and success tests**

Cover concurrent request rejection with 503, malformed SQLite rejection, successful restore followed by a fresh database read, CookieManager reload, and token revocation.

- [x] **Step 2: Validate before touching the live database**

Stream upload to a unique temporary file, enforce size and extension limits, check required schema and integrity, and clean the temporary file in `finally`.

- [x] **Step 3: Enter maintenance mode and atomically replace**

Acquire the maintenance lock, stop or pause account tasks, close the DB under its lock, copy the current DB to a timestamped backup, `os.replace` the validated file, reinitialize the manager, reload CookieManager, clear `SESSION_TOKENS` and `DOWNLOAD_TOKENS`, then release maintenance mode.

- [x] **Step 4: Add rollback verification**

If reinitialization or post-restore read fails, atomically restore the previous backup, reinitialize again, reload runtime state, and return a generic 500 response without filesystem paths.

---

### Task 5: Establish Reproducible Delivery Checks

**Files:**
- Modify: `requirements.txt`
- Create: `requirements.lock`
- Modify: `.github/workflows/docker-image.yml`
- Create: `.github/workflows/test.yml`
- Modify: `README.md`

**Interfaces:**
- `requirements.txt` remains the human-maintained compatibility range.
- `requirements.lock` is generated by a documented command and used by CI/release builds.
- CI runs `pytest`, `python -m compileall`, and a Docker Compose parser check where Docker is available.

- [x] **Step 1: Pin and document dependency resolution**

Generate a lock file with hashes where supported, pin the `slidex` commit, and document Python/Playwright versions.

- [x] **Step 2: Add CI test workflow**

Run on push and pull request: install lock dependencies, create runtime directories, run all tests, compile Python, and upload test logs on failure.

- [ ] **Step 3: Verify from a clean checkout**

Run: `git clean -xfd` only in a disposable CI checkout, then install and run `pytest -q`.

- [x] **Step 4: Update operational documentation**

Document required secrets, internal callback authentication, backup maintenance mode, health endpoints and the fact that multi-worker deployment is not supported until external session storage is introduced.

---

### Task 6: Layered Refactor After Stabilization

**Files:**
- Create: `app/api/routers/{auth,accounts,orders,replies,admin}.py`
- Create: `app/application/{auth,accounts,orders,replies,backups}/`
- Create: `app/domain/{accounts,orders,replies,security}/`
- Create: `app/infrastructure/{db,xianyu,ai,browser,storage}/`
- Deprecate and remove after import migration: `db_manager.py`
- Split incrementally: `reply_server.py`, `XianyuAutoAsync.py`

**Interfaces:**
- Routers depend on application services only.
- Application services depend on domain policies and infrastructure protocols.
- Infrastructure adapters implement protocols and contain FastAPI/SQLite/Playwright/vendor details.

- [x] **Step 1: Extract one vertical slice**

Move authentication first, preserving response shapes and adding contract tests. The session application service and `/verify`/`/logout` router are now extracted; remaining login/register flows stay in the legacy module for the next migration.

- [x] **Step 2: Extract account ownership policy**

Replace repeated `get_all_cookies(user_id)` checks with one policy that returns an owned account or raises a typed authorization error. The shared `_ensure_cookie_access` path now uses this policy; legacy routes with custom response shapes remain to be migrated incrementally.

- [x] **Step 3: Extract order delivery use case**

Move order context ownership checks into `ManualDeliveryContextLoader`; delivery side effects and state transitions remain in the existing engine and are covered by contract tests pending the next vertical slice.

- [ ] **Step 4: Delete the duplicate DB implementation**

After import graph verification and one release cycle, remove the root `db_manager.py` and update README structure documentation. This remains intentionally pending because the compatibility module is still tracked and requires a separate release migration.

---

## Release Gates

- No P1 findings open.
- `pytest -q` passes from a clean checkout.
- Anonymous requests cannot access internal reply or captcha control operations.
- Restore failure leaves the original database and running tasks intact.
- `/health/live` and `/health/ready` behavior is documented and monitored.
- No fixed default password or test credential appears in HTML, logs, README or code.

## Rollback Strategy

- Security boundary changes are feature-gated by explicit environment keys; unset keys fail closed.
- Database restore keeps a timestamped pre-restore copy and supports atomic rollback.
- Lifespan changes can be reverted independently from route extraction.
- Layered refactor is merged vertical-slice by vertical-slice; old route behavior remains covered until each slice is removed.

## Self-Review Checklist

- [x] Every confirmed review finding has a task, file location, regression test and acceptance command.
- [x] No task depends on an undefined function or unspecified configuration name.
- [x] The plan does not claim architecture refactoring is complete before the security and runtime gates pass.
- [x] Clean-checkout, failure-path and rollback scenarios are explicit.
