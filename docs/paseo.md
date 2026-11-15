# Paseo on opus

Paseo's official Docker image runs the daemon and serves the bundled web UI from one origin. It bundles no agent CLIs, so `roles/paseo` builds a child image (`paseo-opencode:local`) that adds the OpenCode and GitHub CLIs. Everything runs on `opus` as the container's non-root `paseo` user (uid/gid 1000, which is also `suhlig` on the host).

Reach it at **https://paseo.tailnet-204f.ts.net**, published as the Tailscale Service `svc:paseo` from `inventory/host_vars/opus/main.yml`. The container publishes its port on loopback only (`127.0.0.1:6767`), so the LAN and the tailnet reach Paseo exclusively through that TLS endpoint.

## Layout on the host

| Path | Mounted as | Contents |
| --- | --- | --- |
| `/opt/paseo/home` | `/home/paseo` | Daemon state (`~/.paseo`), OpenCode credential, git identity (`.gitconfig`), GitHub credential (`.config/gh/hosts.yml`), future agent credentials (`.claude`, `.codex`, …) |
| `/opt/paseo/workspace` | `/workspace` | What launched agents may read and write; Paseo creates worktrees here |
| `/opt/paseo/Dockerfile` | — | Rendered from `roles/paseo/templates/Dockerfile.j2` |
| `/opt/paseo/.build-stamp` | — | Base image id + OpenCode and `gh` versions of the built child image |

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

## Git and GitHub access

Agents commit and push from inside the container through two independent pieces, deliberately kept apart:

- **Commit identity** comes from `/opt/paseo/home/.gitconfig`, rendered by the role. Every commit is authored as `Steffen Uhlig <steffen@familie-uhlig.net>`; GitHub links it to that account by email, so the history reads as your work rather than a bot's.
- **Push authorization** comes from `vault_paseo_github_token`, a classic PAT owned by `suhlig` with `repo` scope. `roles/paseo` renders it into `/opt/paseo/home/.config/gh/hosts.yml` (mode `0600`), the file `gh auth login --with-token` would have written, and `.gitconfig` names `gh auth git-credential` as the HTTPS helper. The token is therefore usable both by `git push` and by `gh pr create`.

Classic rather than fine-grained: a fine-grained PAT is scoped to exactly one resource owner, and agents push to both `suhlig` and `uhlig-it`. One classic PAT covers both owners, at the cost of reaching every repository you can push to. Create it once under GitHub → Settings → Developer settings → Personal access tokens (classic), with the `repo` scope. Reusing `github.access_token` would also have worked, but it is the OAuth token CI relies on — sharing it with an agent container means one credential with two blast radii and no way to rotate either alone. The dedicated token is revocable on its own.

The token is a file in the mounted home, never an environment variable, so it does not show up in `docker inspect` — the same rule the OpenCode key follows. It is also not in the B2 backups: only `/opt/homeassistant` is backed up on this host.

Remotes must be HTTPS. The container holds no GitHub SSH key and no `known_hosts` entry, so a `git@github.com:owner/repo.git` remote cannot push. `gh repo clone` and `gh pr checkout` already use HTTPS, because `hosts.yml` sets `git_protocol: https`.

Verify from the host:

```command
$ docker exec --user paseo paseo gh auth status
$ docker exec --user paseo paseo git config --global --get user.email   # steffen@familie-uhlig.net
```

If agents are to edit `.github/workflows`, add the `workflow` scope to the token as well. Without it GitHub rejects those pushes with a 403 that does not mention the scope.

Rotate by updating `vault_paseo_github_token` and re-running `ansible-playbook playbooks/machines/opus.yml --tags paseo`. The credential file is rewritten in place on the host, so the daemon needs no restart and the next `gh` or `git` invocation reads the new token. Commits already pushed keep their authorship — only the push credential changes.

A fine-grained alternative exists but needs two tokens (one per owner) and a per-owner `credential.helper`, because `gh` stores a single token per host. That is the tradeoff the classic PAT buys you out of.

## Updates

`ghcr.io/getpaseo/paseo:latest` follows stable Paseo releases. Watchtower is disabled for this container (the image is built locally, so there is nothing to pull), and the child image is rebuilt only when the upstream digest, `paseo_opencode_version` or `paseo_gh_version` changes:

```command
$ ansible-playbook playbooks/machines/opus.yml --tags paseo
```

That pull-and-rebuild step runs on every deploy, so a new upstream image lands with the next playbook run. To move an agent CLI, change the pinned defaults in `roles/paseo/defaults/main.yml` — the OpenCode and `gh` versions are part of the build stamp, which is what makes a rebuild happen:

```command
$ ansible-playbook playbooks/machines/opus.yml --tags paseo -e paseo_opencode_version=1.20.0
$ ansible-playbook playbooks/machines/opus.yml --tags paseo -e paseo_gh_version=2.105.0
```

## Deploying

```command
$ ansible-playbook playbooks/machines/opus.yml --tags paseo
$ ansible-playbook playbooks/fleet/tailscale.yml --limit opus --tags tailscale,service   # publish svc:paseo
```

The role asserts that all three secrets exist before it touches anything, and it waits for the web UI to answer on loopback, so a failed deploy does not leave a silently broken container. Re-runs are idempotent when nothing upstream changed.

## Not set up yet

- `/opt/paseo/workspace` starts empty. Add projects in the app, or clone repos into it on the host.
- Paseo shares an 8 GB host with Home Assistant and Frigate. Frigate is the memory and CPU hog; check `docker stats` before running many agents in parallel.
- Nothing rotates `daemon.log`; if it grows, drop `PASEO_LOG_LEVEL` from `info` to `warn` in the role's container env.
