# Paseo on opus

Paseo's official Docker image runs the daemon and serves the bundled web UI from one origin. It bundles no agent CLIs, so `roles/paseo` builds a child image (`paseo-opencode:local`) that adds the OpenCode CLI. Everything runs on `opus` as the container's non-root `paseo` user (uid/gid 1000, which is also `suhlig` on the host).

Reach it at **https://paseo.tailnet-204f.ts.net**, published as the Tailscale Service `svc:paseo` from `inventory/host_vars/opus/main.yml`. The container publishes its port on loopback only (`127.0.0.1:6767`), so the LAN and the tailnet reach Paseo exclusively through that TLS endpoint.

## Layout on the host

| Path | Mounted as | Contents |
| --- | --- | --- |
| `/opt/paseo/home` | `/home/paseo` | Daemon state (`~/.paseo`), OpenCode credential, future agent credentials (`.claude`, `.codex`, …) |
| `/opt/paseo/workspace` | `/workspace` | What launched agents may read and write; Paseo creates worktrees here |
| `/opt/paseo/Dockerfile` | — | Rendered from `roles/paseo/templates/Dockerfile.j2` |
| `/opt/paseo/.build-stamp` | — | Base image id + OpenCode version of the built child image |

## The tailnet endpoint

`svc:paseo` needs three things, in this order:

1. **A Service definition in the tailnet** (one-time, admin console → Services → Advertise → Define a Service): name `paseo`, endpoint `tcp:443`. This allocates the MagicDNS name and the TailVIP — until it exists, the console only tells you the service "has no Service hosts set up", because there is nothing to host yet.
2. **An auto-approver**, so the host advertisement needs no click. In Access controls:

   ```json
   "autoApprovers": { "services": { "svc:paseo": ["tag:server"] } }
   ```

3. **The advertisement from `opus`**, which `playbooks/fleet/tailscale.yml` sends via `suhlig.foundation.tailscale_service`.

Order matters. An advertisement sent *before* the Service exists, or *before* the auto-approver is saved, is registered (`AdvertiseServices` lists it) but never approved — and re-running `tailscale serve` is a no-op because the configuration has not changed. Force a fresh advertisement by clearing only this service:

```command
$ sudo tailscale serve clear svc:paseo
$ sudo tailscale serve --service=svc:paseo --https=443 --yes http://127.0.0.1:6767
```

Then confirm the host is active — `svc:paseo` must appear in this map, with its TailVIP:

```command
$ tailscale status --json | jq '.Self.CapMap."service-host"'
```

Symptoms while unapproved: `paseo.tailnet-204f.ts.net` resolves (the VIP exists) but every connection times out. A timeout that persists *after* the host is approved means the ACL grants do not cover `svc:paseo` on 443; add `{"src": ["autogroup:member"], "dst": ["svc:paseo"], "ip": ["443"]}`.

## Connecting

The daemon is password-protected (`vault_paseo_password`). Read it with:

```command
$ ansible-vault view inventory/group_vars/all/secrets.yml | grep '^vault_paseo_password'
```

- **Browser:** open https://paseo.tailnet-204f.ts.net → **Direct connection** with host `paseo.tailnet-204f.ts.net`, port **443**, **Use SSL on**, and that password. The form defaults to port `6767` and SSL off; with those values the request never reaches the daemon (port 6767 is not published beyond loopback) and the browser refuses it first: `An insecure WebSocket connection may not be initiated from a page loaded over HTTPS`.
- **Desktop app:** Settings → Add host → Direct connection; host `paseo.tailnet-204f.ts.net`, port `443`, SSL on, same password.
- **Mobile app:** the same direct connection; the phone only needs Tailscale up.
- **CLI on the host:** `docker exec --user paseo paseo paseo --host 127.0.0.1:6767 --json project ls`. The `docker exec` shell already carries the container's `PASEO_PASSWORD`.

Auto-connect to the page's own origin cannot carry the password, so an explicit direct connection is the supported path once `PASEO_PASSWORD` is set. The daemon still has to trust the proxy, or the app cannot tell that it is behind TLS: Tailscale serve reaches the published port from the Docker bridge gateway (`172.17.0.1`) rather than from loopback, so `roles/paseo` sets `PASEO_TRUSTED_PROXIES=loopback,172.16.0.0/12` (override with `paseo_trusted_proxies`). Measured, with the pin below as default:

| `paseo_trusted_proxies` | WebSocket the served UI opens |
| --- | --- |
| `loopback` | only `ws://localhost:6767/ws` — its probe for a local daemon; the page never connects |
| `loopback,172.16.0.0/12` | `wss://paseo.tailnet-204f.ts.net/ws` plus the local probe |

That local probe is expected noise on a machine that runs no daemon of its own; it fails with `Transport closed (code 1006)` and is not the tailnet endpoint failing.

Verify the endpoint without a browser:

```command
$ curl -sS -o /dev/null -w '%{http_code}\n' https://paseo.tailnet-204f.ts.net/   # 200
$ curl -sS -o /dev/null --max-time 3 --http1.1 -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
    -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
    -w '%{http_code}\n' https://paseo.tailnet-204f.ts.net/ws                    # 101
```

The second command prints `101` and then exits on its own timeout — the upgrade succeeded, and curl is just waiting for frames on a socket nobody authenticated.

## OpenCode Go

The key belongs to a service account, not to a person: OpenCode Console → subscribe to Go → copy the API key. It lives in the vault as `vault_opencode_go_api_key` and is rendered into `/opt/paseo/home/.local/share/opencode/auth.json`, which is what `opencode auth login` would have written. That file is the only place the key sits on the host — it is not passed as an environment variable, so it does not show up in `docker inspect`.

Notes on the plan: service accounts are not covered by *Go Teams*; Go and Go Plus are single-member workspaces. Pick models in Paseo's model picker; OpenCode references Go models as `opencode-go/<model-id>` (for example `opencode-go/kimi-k3`).

Verify the credential from inside the container:

```command
$ docker exec --user paseo paseo opencode auth list          # OpenCode Go  api
$ docker exec --user paseo paseo opencode models | grep -c opencode-go
$ docker exec --user paseo --workdir /tmp paseo opencode run -m opencode-go/deepseek-v4.1-flash 'Reply with exactly: OK'
```

Rotate it by updating the vault entry and re-running the playbook.

## Updates

`ghcr.io/getpaseo/paseo:latest` follows stable Paseo releases. Watchtower is disabled for this container (the image is built locally, so there is nothing to pull), and the child image is rebuilt only when the upstream digest or `paseo_opencode_version` changes:

```command
$ ansible-playbook playbooks/machines/opus.yml --tags paseo
```

That pull-and-rebuild step runs on every deploy, so a new upstream image lands with the next playbook run. To move the agent CLI, change the pinned default in `roles/paseo/defaults/main.yml` — the OpenCode version is part of the build stamp, which is what makes a rebuild happen:

```command
$ ansible-playbook playbooks/machines/opus.yml --tags paseo -e paseo_opencode_version=1.20.0
```

## Deploying

```command
$ ansible-playbook playbooks/machines/opus.yml --tags paseo
$ ansible-playbook playbooks/fleet/tailscale.yml --limit opus --tags tailscale,service   # publish svc:paseo
```

The role asserts that both secrets exist before it touches anything, and it waits for the web UI to answer on loopback, so a failed deploy does not leave a silently broken container. Re-runs are idempotent when nothing upstream changed.

## Not set up yet

- `/opt/paseo/workspace` starts empty. Add projects in the app, or clone repos into it on the host.
- Agents have no git identity or GitHub credentials yet. Add a `~/.gitconfig` and an SSH key or `gh` login under `/opt/paseo/home` when the first agent needs to push.
- Paseo shares an 8 GB host with Home Assistant and Frigate. Frigate is the memory and CPU hog; check `docker stats` before running many agents in parallel.
- Nothing rotates `daemon.log`; if it grows, drop `PASEO_LOG_LEVEL` from `info` to `warn` in the role's container env.
