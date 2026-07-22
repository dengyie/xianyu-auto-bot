# Xianyu Caddy HTTPS Design

## Objective

Expose the Xianyu application only through `https://xianyu.mangoq.ccwu.cc`.
The application port must remain reachable by Caddy on the VPS loopback
interface but must not be directly reachable from the public Internet.

## Current State

- Caddy 2.11.4 runs as a systemd service and already serves
  `novel.mangoq.ccwu.cc` and `beszel.mangoq.ccwu.cc`.
- The Xianyu container publishes `0.0.0.0:9000` and `[::]:9000` to container
  port `8090`.
- `xianyu.mangoq.ccwu.cc` is already routed through Cloudflare. Requests reach
  the existing HTTPS edge but do not currently reach the Xianyu application.
- The application health endpoints pass on port `9000`.

## Design

### Public Traffic

Caddy will own the Xianyu public endpoint:

```caddyfile
xianyu.mangoq.ccwu.cc {
	encode gzip
	reverse_proxy 127.0.0.1:9000
}
```

Caddy will preserve the existing automatic HTTP-to-HTTPS redirect and TLS
handling. The existing Novel and Beszel site blocks remain unchanged.

### Private Upstream

Both Compose variants will publish the application only on loopback while
retaining their existing host ports:

```yaml
# docker-compose.yml
ports:
  - "127.0.0.1:9000:8090"

# docker-compose-cn.yml
ports:
  - "127.0.0.1:8000:8090"
```

The VPS deployment uses the default Compose file, so Caddy proxies to
`127.0.0.1:9000`. This prevents direct IPv4 and IPv6 access to either published
port. No new Docker network coupling to the host Caddy service is required.

### Deployment Sequence

1. Update and test both Compose variants locally.
2. Merge the tested change into local `main` and synchronize the Compose files.
3. Recreate only `xianyu-app` without rebuilding its image.
4. Back up `/etc/caddy/Caddyfile`.
5. Add the Xianyu site block to a staged Caddyfile.
6. Validate the staged configuration before replacing the active file.
7. Reload Caddy through systemd without stopping it.

### Failure Handling

- If Compose recreation fails, the existing image and persistent data remain
  available; inspect container logs before retrying.
- If Caddy validation fails, keep the active Caddyfile unchanged.
- If reload fails, restore the backup and validate before another reload.
- Do not modify Cloudflare, the host firewall, or unrelated Caddy sites.

## Verification

- Local regression tests confirm both Compose files bind `127.0.0.1:9000`.
- `caddy validate` succeeds for the staged and active configuration.
- `systemctl is-active caddy` remains `active` after reload.
- `https://xianyu.mangoq.ccwu.cc/health/live` returns `alive`.
- `https://xianyu.mangoq.ccwu.cc/health/ready` returns `healthy`.
- HTTP redirects to HTTPS.
- `http://35.212.179.13:9000` is unreachable from outside the VPS.
- The container remains healthy with zero restarts.

## Out Of Scope

- Changing Cloudflare proxy or TLS settings.
- Adding a second reverse proxy or application-level TLS.
- Modifying the existing Novel or Beszel services.
