# Xianyu Caddy HTTPS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve Xianyu exclusively through `https://xianyu.mangoq.ccwu.cc`, keep its Docker upstream reachable only on VPS loopback port `9000`, and preserve the existing Novel and Beszel sites.

**Architecture:** Docker Compose publishes the application container's port `8090` only on `127.0.0.1`; host Caddy terminates TLS and reverse-proxies the Xianyu hostname to `127.0.0.1:9000`. A source-level regression test locks both Compose variants to loopback bindings. Deployment recreates only `xianyu-app`, validates a staged complete Caddyfile before installation, then reloads Caddy without stopping it.

**Tech Stack:** Docker Compose, Caddy 2.11.4, systemd, pytest, SSH (`google-vps-next`)

## Global Constraints

- Public URL is exactly `https://xianyu.mangoq.ccwu.cc`.
- Caddy upstream is exactly `127.0.0.1:9000`.
- `docker-compose.yml` must publish exactly `127.0.0.1:9000:8090` for `xianyu-app`.
- `docker-compose-cn.yml` must publish exactly `127.0.0.1:8000:8090` for `xianyu-app`.
- Do not modify Cloudflare, the host firewall, Novel, Beszel, or unrelated Caddy site blocks.
- Do not build an image on the 1 GB VPS; recreate `xianyu-app` with the already deployed image.
- Preserve `/home/mango/xianyu-auto-bot/data`, logs, uploads, configuration, and all other mounted runtime data.
- Obtain explicit user approval immediately before executing each remote `sudo` command.
- Preserve the primary worktree's untracked `uv.lock`; never stage, edit, copy, or delete it.

---

### Task 1: Lock the Compose Loopback Boundary with a Regression Test

**Files:**
- Modify: `tests/smoke/test_public_boundaries.py`
- Verify: `docker-compose.yml`
- Verify: `docker-compose-cn.yml`

**Interfaces:**
- Consumes: the two Compose source files and their `xianyu-app.ports` declarations.
- Produces: `test_compose_app_ports_are_bound_to_loopback_only()` as a deployment-boundary regression test.

- [x] **Step 1: Confirm the current Compose contract**

Run:

```bash
rg -n '127\.0\.0\.1:(9000|8000):8090|0\.0\.0\.0:(9000|8000):8090' docker-compose.yml docker-compose-cn.yml
```

Expected: `docker-compose.yml` contains `127.0.0.1:9000:8090`, `docker-compose-cn.yml` contains `127.0.0.1:8000:8090`, and neither file contains a matching `0.0.0.0` binding.

- [x] **Step 2: Add the regression test**

Append to `tests/smoke/test_public_boundaries.py`:

```python
def test_compose_app_ports_are_bound_to_loopback_only():
    project_root = Path(__file__).resolve().parents[2]
    expected_bindings = {
        "docker-compose.yml": "127.0.0.1:9000:8090",
        "docker-compose-cn.yml": "127.0.0.1:8000:8090",
    }

    for compose_name, expected_binding in expected_bindings.items():
        compose = (project_root / compose_name).read_text(encoding="utf-8")
        assert f'- "{expected_binding}"' in compose
        assert '- "0.0.0.0:9000:8090"' not in compose
        assert '- "0.0.0.0:8000:8090"' not in compose
```

- [x] **Step 3: Prove the test detects a public port regression**

Temporarily change only `docker-compose.yml` from:

```yaml
- "127.0.0.1:9000:8090"
```

to:

```yaml
- "0.0.0.0:9000:8090"
```

Run:

```bash
.venv/bin/python -m pytest tests/smoke/test_public_boundaries.py::test_compose_app_ports_are_bound_to_loopback_only -q
```

Expected: FAIL because the expected loopback binding is missing and a public binding is present. Restore `docker-compose.yml` exactly to `127.0.0.1:9000:8090` immediately afterward.

- [x] **Step 4: Run the restored regression test**

Run:

```bash
.venv/bin/python -m pytest tests/smoke/test_public_boundaries.py::test_compose_app_ports_are_bound_to_loopback_only -q
```

Expected: `1 passed`.

- [x] **Step 5: Validate both resolved Compose models**

Run:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose-cn.yml config --quiet
```

Expected: both commands exit `0` with no validation error.

### Task 2: Verify and Commit the Local HTTPS Boundary Change

**Files:**
- Modify: `docs/superpowers/plans/2026-08-01-xianyu-caddy-https.md`
- Modify: `tests/smoke/test_public_boundaries.py`

**Interfaces:**
- Consumes: Task 1 regression test and the existing loopback Compose configuration.
- Produces: a reviewed feature-branch commit ready to rebase and merge.

- [x] **Step 1: Run all public-boundary tests**

Run:

```bash
.venv/bin/python -m pytest tests/smoke/test_public_boundaries.py -q
```

Expected: all tests in the module pass.

- [x] **Step 2: Run the complete test suite**

Run:

```bash
.venv/bin/python -m pytest
```

Expected: all collected tests pass. If the existing `<0.2s` health timing assertion alone fails under suite load, rerun that test in isolation five times, record the evidence, and do not alter the unrelated threshold in this change.

- [x] **Step 3: Review only the intended diff**

Run:

```bash
git status --short
git diff --check
git diff -- tests/smoke/test_public_boundaries.py docs/superpowers/plans/2026-08-01-xianyu-caddy-https.md
```

Expected: only the plan and boundary test are uncommitted; `git diff --check` exits `0`.

- [x] **Step 4: Commit the implementation plan and regression test**

Run:

```bash
git add docs/superpowers/plans/2026-08-01-xianyu-caddy-https.md tests/smoke/test_public_boundaries.py
git commit -m "test: lock xianyu compose ports to loopback"
```

Expected: one new commit on `codex/deep-review`, with no `uv.lock` in the commit.

### Task 3: Rebase and Fast-Forward Merge into Local Main

**Files:**
- No file content changes expected.

**Interfaces:**
- Consumes: the verified `codex/deep-review` branch.
- Produces: local `main` containing the design, plan, and regression test.

- [ ] **Step 1: Rebase once more onto the current local main**

Run in the feature worktree:

```bash
git rebase main
```

Expected: successful rebase or an explicit conflict-resolution cycle followed by fresh verification.

- [ ] **Step 2: Re-run the focused verification after rebase**

Run:

```bash
.venv/bin/python -m pytest tests/smoke/test_public_boundaries.py -q
git diff --check main...HEAD
```

Expected: all public-boundary tests pass and diff check exits `0`.

- [ ] **Step 3: Fast-forward local main without touching `uv.lock`**

Run in the primary worktree:

```bash
git merge --ff-only codex/deep-review
```

Expected: local `main` advances by fast-forward; the untracked primary-worktree `uv.lock` remains unchanged and untracked.

### Task 4: Deploy the Loopback-Only Compose Binding

**Files:**
- Local source: `docker-compose.yml`
- Remote destination: `/home/mango/xianyu-auto-bot/docker-compose.yml`

**Interfaces:**
- Consumes: merged local `main` Compose configuration.
- Produces: `xianyu-auto-bot` listening only on VPS `127.0.0.1:9000` with preserved volumes and image.

- [ ] **Step 1: Capture the remote pre-deployment state**

Run:

```bash
ssh google-vps-next 'cd /home/mango/xianyu-auto-bot && docker compose ps && docker inspect -f "{{.State.Health.Status}} {{.RestartCount}}" xianyu-auto-bot && ss -ltn | grep ":9000"'
```

Expected before recreation: container is running/healthy and port `9000` may still be publicly bound.

- [ ] **Step 2: Copy the merged Compose file**

Run from the primary worktree:

```bash
scp docker-compose.yml google-vps-next:/home/mango/xianyu-auto-bot/docker-compose.yml
```

Expected: copy exits `0`.

- [ ] **Step 3: Validate the remote Compose model and recreate only the app**

Run:

```bash
ssh google-vps-next 'cd /home/mango/xianyu-auto-bot && docker compose config --quiet && docker compose up -d --no-build --force-recreate xianyu-app'
```

Expected: Compose validation succeeds and only `xianyu-app` is recreated without building.

- [ ] **Step 4: Verify local-only listening and application health**

Run:

```bash
ssh google-vps-next 'for i in 1 2 3 4 5 6; do status=$(docker inspect -f "{{.State.Health.Status}}" xianyu-auto-bot 2>/dev/null || true); [ "$status" = healthy ] && break; sleep 5; done; docker inspect -f "{{.State.Status}} {{.State.Health.Status}} {{.RestartCount}}" xianyu-auto-bot; ss -ltn | grep ":9000"; curl --fail --silent --show-error http://127.0.0.1:9000/health/live'
```

Expected: `running healthy 0`, only `127.0.0.1:9000` is listening, and the liveness response contains `alive`.

### Task 5: Stage, Validate, Install, and Reload Caddy

**Files:**
- Remote source: `/etc/caddy/Caddyfile`
- Remote stage: `/home/mango/Caddyfile.xianyu-staged`
- Remote backup: `/etc/caddy/Caddyfile.backup-<UTC timestamp>`

**Interfaces:**
- Consumes: the active Caddyfile and the healthy loopback upstream from Task 4.
- Produces: a validated active site block for `xianyu.mangoq.ccwu.cc`.

- [ ] **Step 1: Build a complete staged Caddyfile without sudo**

Run:

```bash
ssh google-vps-next 'python3 - <<'"'"'PY'"'"'
from pathlib import Path

source = Path("/etc/caddy/Caddyfile").read_text(encoding="utf-8").rstrip()
site = """

xianyu.mangoq.ccwu.cc {
\tencode gzip
\treverse_proxy 127.0.0.1:9000
}
"""
if "xianyu.mangoq.ccwu.cc" in source:
    raise SystemExit("xianyu site already exists; refusing duplicate insertion")
Path("/home/mango/Caddyfile.xianyu-staged").write_text(source + site, encoding="utf-8")
PY
cat /home/mango/Caddyfile.xianyu-staged'
```

Expected: staged output contains unchanged Novel and Beszel blocks plus exactly one Xianyu block.

- [ ] **Step 2: With explicit approval, back up the active Caddyfile**

After obtaining user permission for this exact command, run:

```bash
ssh google-vps-next 'sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.backup-$(date -u +%Y%m%dT%H%M%SZ)'
```

Expected: exit `0`.

- [ ] **Step 3: With explicit approval, validate the staged file**

After obtaining user permission for this exact command, run:

```bash
ssh google-vps-next 'sudo caddy validate --config /home/mango/Caddyfile.xianyu-staged'
```

Expected: configuration is valid.

- [ ] **Step 4: With explicit approval, install the staged file**

After obtaining user permission for this exact command, run:

```bash
ssh google-vps-next 'sudo install -o root -g root -m 644 /home/mango/Caddyfile.xianyu-staged /etc/caddy/Caddyfile'
```

Expected: exit `0`.

- [ ] **Step 5: With explicit approval, validate the active file**

After obtaining user permission for this exact command, run:

```bash
ssh google-vps-next 'sudo caddy validate --config /etc/caddy/Caddyfile'
```

Expected: configuration is valid.

- [ ] **Step 6: With explicit approval, reload Caddy**

After obtaining user permission for this exact command, run:

```bash
ssh google-vps-next 'sudo systemctl reload caddy'
```

Expected: reload exits `0`; if it fails, stop and restore the timestamped backup before any retry.

### Task 6: End-to-End Production Verification

**Files:**
- No file changes.

**Interfaces:**
- Consumes: the deployed loopback Compose service and reloaded Caddy configuration.
- Produces: evidence that HTTPS, redirects, port isolation, health, and existing sites remain intact.

- [ ] **Step 1: Verify Caddy and container state on the VPS**

Run:

```bash
ssh google-vps-next 'systemctl is-active caddy; docker inspect -f "{{.State.Status}} {{.State.Health.Status}} {{.RestartCount}}" xianyu-auto-bot; ss -ltn | grep ":9000"; curl --fail --silent --show-error http://127.0.0.1:9000/health/live'
```

Expected: `active`, `running healthy 0`, only `127.0.0.1:9000`, and liveness contains `alive`.

- [ ] **Step 2: Verify Xianyu HTTPS health endpoints and HTTP redirect externally**

Run locally:

```bash
curl --fail --silent --show-error https://xianyu.mangoq.ccwu.cc/health/live
curl --fail --silent --show-error https://xianyu.mangoq.ccwu.cc/health/ready
curl --silent --show-error --head http://xianyu.mangoq.ccwu.cc/
```

Expected: live contains `alive`, ready contains `healthy`, and HTTP returns a `3xx` redirect whose `Location` uses `https://xianyu.mangoq.ccwu.cc/`.

- [ ] **Step 3: Verify the VPS port is not reachable directly**

Run locally:

```bash
if curl --connect-timeout 5 --silent --show-error http://35.212.179.13:9000/health/live; then
  echo "ERROR: public port 9000 is reachable"
  exit 1
else
  echo "PASS: public port 9000 is unreachable"
fi
```

Expected: connection fails and the command prints `PASS: public port 9000 is unreachable`.

- [ ] **Step 4: Verify existing Novel and Beszel sites still respond over HTTPS**

Run locally:

```bash
curl --fail --silent --show-error --output /dev/null https://novel.mangoq.ccwu.cc/
curl --fail --silent --show-error --output /dev/null https://beszel.mangoq.ccwu.cc/
```

Expected: both commands exit `0`.

- [ ] **Step 5: Record final Git and production evidence**

Run:

```bash
git status --short
git log --oneline --decorate -5
```

Expected: local `main` contains the HTTPS design and regression-test commits; the only allowed primary-worktree status entry is the pre-existing untracked `uv.lock`.
