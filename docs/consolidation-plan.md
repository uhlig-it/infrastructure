# Ansible Consolidation Plan

## The problem

Every project carries its own Ansible: its own `inventory.yml`, its own `ansible.cfg`, its own `requirements.yml`, its own vault, and its own playbook (sometimes at the repo root, sometimes under `deployment/`). The same machines appear over and over — `shop` in six repos, `opus` in five, `neon` in four — each time with a slightly different inventory. There is no single place to answer "what runs on `opus`?" or "where is the playbook that deploys X?".

The individual projects are fine and should stay as they are. What needs consolidating is the *deployment layer*: one inventory, one config, one dependency list, one vault, and a predictable place for every playbook.

## What exists today

Everything below was evaluated from the workspace and from the shared role/collection repos under `~/git/github.com/{suhlig,uhlig-it}/`.

### 1. Reusable building blocks (roles & collections)

These are already separate repos and should stay that way — they are the library that deployments consume. The consolidation work here is naming hygiene, not restructuring.

| Name | Repo | Notes |
| --- | --- | --- |
| `suhlig.foundation` (collection) | `suhlig/foundation` | Roles: `main`, `minimal`, `raspberry_pi`, `mediamtx`, `speedtest`, `log2ram`, `tailscale_service`, `local_user`, `atuin`, `konsible`, `picoshare`. Depends on `thorian93.main`. |
| `uhlig-it.simple_systemd_service` | `uhlig-it/ansible-role-simple-systemd-service` | The single biggest duplication axis: used by 9 deployments. Hardened systemd unit + dedicated system user. |
| `suhlig.caddy_reverse_proxy` | `suhlig/ansible-role-caddy_reverse_proxy` | Used by kehrkraft, uhlig.social, mosquitto-exporter, shorts, youtube-likes-feed, meal-tracker. |
| `suhlig.caddy_file_server` | `suhlig/ansible-role-caddy_file_server` | Used by www, speisehof, nowak-reisen. |
| `suhlig.caddy` | `suhlig/ansible-role-caddy` | Base Caddy role. |
| `uhlig-it.duckdns` | `uhlig-it/ansible-role-duckdns` | Used by opus, shop. |
| `uhlig-it.sqlite_backup_b2` | `uhlig-it/ansible-role-sqlite-backup-b2` | Used by uhlig.social, meal-tracker. |
| `uhlig-it.home-assistant-backup-b2` | `uhlig-it/ansible-role-home-assistant-backup-b2` | Used by opus. |
| `uhlig-it.ansible-role-mosquitto` | `uhlig-it/ansible-role-mosquitto` | Mosquitto broker. |
| `uhlig-it.ansible-role-mosquitto-bridge` | `uhlig-it/ansible-role-mosquitto-bridge` | Used by shop. |
| `suhlig.concourse` | `suhlig/ansible-concourse` | Fork of `troykinsella.concourse` with the removed `include` action fixed. Installed from git, not Galaxy. |

External dependencies pulled in by deployments: `artis3n.tailscale`, `thorian93.main` (`upgrade`, `journald`, `common`, `ntp`), `ansistrano.deploy`, `nerab.ruby`, `geerlingguy.docker`, `gantsign.golang`, `ANXS.postgresql`, `suhlig.concourse` (fork of `troykinsella.concourse`), `prometheus.prometheus`, `ypsman.timedatectl`.

### 2. Machine deployments (host provisioning)

One repo per machine (or small machine group). These are the natural home for "what a host is".

| Repo | Host(s) | What it does |
| --- | --- | --- |
| `uhlig-it/opus` | `opus` | Home server: HA, UniFi, Mosquitto, Pi-hole, Watchtower, Uptime-Kuma (local container roles), plus Tailscale subnet router + services. |
| `uhlig-it/shop` | `shop` | Workshop Pi: mediamtx, mosquitto bridge, SwitchBot, watchdog (local roles), Tailscale subnet router. |
| `uhlig-it/pi5` | `pi5` | Raspberry Pi 5 bootstrap: foundation, Go, Docker. |
| `uhlig-it/pi0` | `pi0` | Raspberry Pi Zero camera (mediamtx). |
| `uhlig-it/pascal` | `pascal` | 3D printer server: baseline + inline TLS-cert and shop-health tasks. |
| `uhlig-it/fortress` | `hansahaus`, `fortcarsta` | Baseline hardening for two remote footholds. |
| `uhlig-it/kunakam` | `kunakam` (camera), `opus` (NVR) | Camera fleet + the `mediamtx` deployment. The Frigate NVR it once deployed is **no longer managed by any repo** — see Progress. |
| `suhlig/wordclock` | `wordclock` | Word clock app (local `fadecandy` + `wordclock` roles). |
| `uhlig-it/tailscale` | fleet | The de-facto fleet-wide Tailscale config. |

### 3. Software / service deployments

Thin playbooks that deploy one application onto an existing host. Almost all of them are a wrapper around `uhlig-it.simple_systemd_service` plus host-specific config.

| Repo | Target host(s) | Mechanism |
| --- | --- | --- |
| `uhlig-it/kehrkraft-deployment` | `neon` | systemd service + Caddy reverse proxy. |
| `uhlig-it/uhlig.social-deployment` | `neon` | GoToSocial (local role) + Caddy + sqlite backup. |
| `uhlig-it/concourse-deployment` | `soda` | Concourse web/worker + Postgres. |
| `uhlig-it/mosquitto-exporter-deployment` | `opus` | systemd service; retired, playbook now at `playbooks/services/mosquitto-exporter.yml`. |
| `uhlig-it/metrics` | `soda` + 8 node exporters | VictoriaMetrics (local role) + node_exporter. |
| `uhlig-it/mqtt-router` | `opus` | systemd service + routing config. |
| `uhlig-it/mqtt-blink1` | `shop` | systemd service + udev rule. |
| `uhlig-it/mqtt-gpio-binary-sensor` | `pi5`, `shop` | systemd service, per-host `gpio_pin`. |
| `uhlig-it/env-sensors` | `wordclock`, `shop` | systemd service, per-host sensor addresses. |
| `uhlig-it/tasmota-firmware-proxy` | `opus`, `shop` | systemd service. |
| `uhlig-it/speedtest-exporter` | `hansahaus`, `fortcarsta`, `opus` | systemd service. |
| `uhlig-it/shorts` | `neon` | systemd service + Caddy. |
| `uhlig-it/youtube-likes-feed` | **deployed nowhere** | systemd service + Caddy; old host `wg` retired. Needs resurrection. |
| `suhlig/meal-tracker` | **deployed nowhere** | Rails app via Ansistrano + Puma; the inventory repo marks it defunct. Needs resurrection. |

### 4. Static sites

| Repo | Target host | Mechanism |
| --- | --- | --- |
| `uhlig-it/www` | `neon` | Zola build + Concourse CI + rsync + `suhlig.caddy_file_server`. |
| `speisehof/www` (outside workspace) | `neon` | Jekyll build + Ansistrano rsync + Caddy. |
| `nowak-reisen/www` (outside workspace) | `neon` | Static HTML + Ansistrano rsync + Caddy. |

### 5. Stale / deprecated

| Repo | Status |
| --- | --- |
| `uhlig-it/home-automation` | 2022-era roles (python2, `jessie` repos, disabled services). Host `wordclock`. |
| `uhlig-it/inventory` | Terraform for Hetzner `soda`; README says out of sync. Contains the best written record of the fleet and DNS. |

### 6. Not deployments (test fixtures)

| Path | What it is |
| --- | --- |
| `uhlig-it/ansible-role-simple-systemd-service/tests` | Role test fixtures (Vagrant + playbooks). Belongs to the role repo, not to a deployment. |
| `suhlig/ansible-concourse/test` | Role test fixtures (RSpec). Belongs to the role repo. |

### 7. Router / network provisioning

| Repo | Target | What it does |
| --- | --- | --- |
| `uhlig-it/shop-router` | the shop Wi-Fi router (OpenWrt, TP-Link WDR4300) | Idempotent UCI provisioning over SSH (`configure.sh`), static DHCP reservations (`hosts.yml`, the source of truth for shop-LAN addresses), an end-to-end client test (`verify.sh`) and an RF site survey (`site-survey.sh`). Not an Ansible deployment, but it owns the shop LAN's addressing. |

## The machines (single source of truth)

This is the table the consolidated inventory should encode. It merges every inventory found in the workspace with the live Tailscale state.

| Host | Tailscale node | Kind | Runs |
| --- | --- | --- | --- |
| `opus` | `opus` (tagged, exit node) | Home server | HA, UniFi, Mosquitto, Pi-hole, Watchtower, Uptime-Kuma, Frigate NVR, mqtt-router, speedtest-exporter, tasmota-firmware-proxy; subnet router `192.168.10.0/24` |
| `shop` | `shop` | Workshop Pi | mediamtx, mosquitto-bridge, switchbot-mqtt, shop-watchdog, env-sensors, tasmota-firmware-proxy, mqtt-gpio-binary-sensor, mqtt-blink1; subnet router `192.168.1.0/24` |
| `neon` | `neon` (tagged) | Hetzner server | kehrkraft, GoToSocial, uhlig.it (www), speisehof.de, nowak-reisen.de |
| `soda` | `soda` (tagged) | Hetzner server | Concourse (web + worker + Postgres), VictoriaMetrics |
| `pi5` | `pi5` | Raspberry Pi 5 | Go, Docker, mqtt-gpio-binary-sensor |
| `pi0` | — (not in tailnet) | Raspberry Pi Zero | mediamtx camera |
| `kunakam` | `kunakam` (offline 374d) | Camera | mediamtx camera |
| `wordclock` | `wordclock` | Raspberry Pi | wordclock app, env-sensors, home-automation (alexa) |
| `pascal` | `pascal` | 3D printer server | is-tls-expiring, shop-health |
| `hansahaus` | `hansahaus` | Fortress | baseline hardening |
| `fortcarsta` | `fortcarsta` | Fortress | baseline hardening |

## Tailscale nodes

Full list from `tailscale status` on this machine, mapped to the plan.

| Node | Owner | OS | State | In plan as |
| --- | --- | --- | --- | --- |
| `opus` | tagged-devices | linux | idle; offers exit node | `opus` machine + exit node |
| `shop` | suhlig@ | linux | offline 17h | `shop` machine + subnet router |
| `neon` | tagged-devices | linux | — | `neon` machine (server) |
| `soda` | tagged-devices | linux | — | `soda` machine (server) |
| `pi5` | suhlig@ | linux | — | `pi5` machine |
| `wordclock` | suhlig@ | linux | — | `wordclock` machine |
| `pascal` | suhlig@ | linux | offline 17h | `pascal` machine |
| `hansahaus` | suhlig@ | linux | — | `hansahaus` machine (fortress) |
| `fortcarsta` | suhlig@ | linux | — | `fortcarsta` machine (fortress) |
| `kunakam` | suhlig@ | linux | offline 374d | `kunakam` machine (camera) |
| `tape` | suhlig@ | linux | offline 3d | unassigned — decide: machine or retire |
| `lux` | suhlig@ | macOS | offline 147d | unassigned — decide: machine or retire |
| `lima` | suhlig@ | macOS | — | dev workstation (not managed) |
| `lima-1` | suhlig@ | macOS | — | dev workstation (not managed) |
| `ipad-mini-6` | suhlig@ | iOS | — | client (not managed) |
| `iphone-steffen` | suhlig@ | iOS | — | client (not managed) |
| `iphone181` | jfuhlig@ | iOS | offline 13d | client (not managed) |
| `homeserver.tail48b8d.ts.net` | fpuhlig@ | linux | offline 30d | not ours — ignore |

### Discrepancies to fix

- The `tailscale` repo inventory still lists `ci`, `wg`, and `pi4`. None of these exist in the tailnet any more; the `inventory` repo README says they were retired. Remove them.
- The `tailscale` repo inventory is missing `soda`, `hansahaus`, `fortcarsta`, `pascal`, `tape`, and `lux`, all of which are live tailnet nodes.
- `pi0` runs the Tailscale role but has no node in the tailnet — either it never joined or it was removed. Decide whether it should be a managed node.
- `opus` is the exit node now, but the old inventory still marks `wg` as the exit node. Update.
- `neon`, `soda`, and `opus` are `tagged-devices`, consistent with the `tag:server` model described in `suhlig.foundation.tailscale_service`. Keep tagging them deliberately.

## Proposed target structure

Consolidate all Ansible into a single **infrastructure** repo (this workspace is a good candidate; otherwise `uhlig-it/infrastructure`). Application repos keep their code and CI that publishes releases; the infrastructure repo owns every playbook, the inventory, and the vault.

```
infrastructure/
├── ansible.cfg                 # one config for everything
├── requirements.yml            # every collection + role, one list
├── inventory/
│   ├── hosts.yml               # THE inventory (machines + groups)
│   ├── group_vars/
│   │   ├── all/
│   │   │   ├── main.yml
│   │   │   └── secrets.yml     # one vault
│   │   ├── raspberry_pi.yml
│   │   ├── servers.yml
│   │   └── tailscale.yml
│   └── host_vars/
│       ├── opus.yml
│       ├── shop.yml
│       └── ...
├── playbooks/
│   ├── site.yml                # runs machines + fleet + services
│   ├── machines/               # host provisioning (one file per host/group)
│   │   ├── opus.yml
│   │   ├── shop.yml
│   │   ├── pi5.yml
│   │   ├── pi0.yml
│   │   ├── pascal.yml
│   │   ├── wordclock.yml
│   │   ├── fortress.yml
│   │   ├── kunakam.yml
│   │   └── neon.yml
│   ├── fleet/                  # cross-host concerns
│   │   ├── tailscale.yml       # ALL node config: tags, routes, exit node, services
│   │   └── metrics.yml         # VictoriaMetrics + node exporters
│   └── services/               # application deployments
│       ├── kehrkraft.yml
│       ├── gotosocial.yml
│       ├── concourse.yml
│       ├── mosquitto-exporter.yml
│       ├── mqtt-router.yml
│       ├── mqtt-blink1.yml
│       ├── mqtt-gpio-binary-sensor.yml
│       ├── env-sensors.yml
│       ├── tasmota-firmware-proxy.yml
│       ├── speedtest-exporter.yml
│       ├── shorts.yml
│       ├── youtube-likes-feed.yml
│       ├── www.yml
│       ├── speisehof.yml
│       ├── nowak-reisen.yml
│       └── meal-tracker.yml
└── roles/                      # host-specific roles that are not shared library
    ├── frigate/
    ├── gotosocial/
    ├── victoriametrics/
    ├── opus/                   # homeassistant, mosquitto, pi-hole, unifi, uptime-kuma, watchtower
    ├── shop-watchdog/
    ├── switchbot-mqtt/
    ├── fadecandy/
    └── rails-puma/
```

### Why this shape

- **One inventory** answers "what is `opus`?" and "which hosts exist?" in a single file. Host-specific values move to `host_vars/<host>.yml` (GPIO pins, sensor addresses, camera tuning, ports), so playbooks stop carrying per-host inline vars.
- **`playbooks/machines/` vs `playbooks/services/`** makes the machine/software split explicit — the exact distinction the current layout blurs.
- **`playbooks/fleet/tailscale.yml`** becomes the only place Tailscale is configured. Machine playbooks stop installing `artis3n.tailscale` themselves; they only declare `tag:server` membership. All `--advertise-routes`, `--advertise-exit-node`, and `tailscale_service` definitions live here.
- **`roles/`** holds only roles that are genuinely tied to this infrastructure. Reusable roles stay in their own repos and are pulled via `requirements.yml`.
- **One vault** (`inventory/group_vars/all/secrets.yml`) replaces the per-repo vaults.

### How application repos fit

Application repos stay code-only. Their CI keeps building and publishing releases (GitHub Actions or Concourse), then triggers the matching `playbooks/services/<app>.yml` in the infrastructure repo. Two options:

1. The app's deploy job checks out the infrastructure repo and runs the service playbook (simplest; keeps "build + deploy" in one pipeline).
2. The infrastructure repo runs Concourse pipelines that watch the app repos (more central, more setup).

Either way, the playbook lives in exactly one place.

## Migration path

Do it in phases so nothing breaks in between.

1. **Scaffold** the infrastructure repo with `ansible.cfg`, `requirements.yml`, and `inventory/hosts.yml` built from the machine table above. Add all hosts and groups, including the missing Tailscale nodes.
2. **Move the fleet concerns first** — `tailscale.yml` and `metrics.yml`. These are already fleet-wide and have the most duplication. Retire the standalone `tailscale` repo's inventory in favour of the shared one.
3. **Move machine playbooks** one host at a time (`pi5`, `pi0`, `pascal`, `fortress`, `wordclock`, `kunakam`, `shop`, `opus`, `neon`). Pull host-specific vars into `host_vars/`. Verify each with `--check` before deleting the old playbook.
4. **Move service playbooks** one app at a time. Keep the app repo's CI, but point its deploy step at the infrastructure playbook.
5. **Move the static sites** (www, speisehof, nowak-reisen) last — they are the most self-contained.
6. **Retire** `home-automation`; fold anything still wanted into `playbooks/machines/wordclock.yml`. (`shop-alarm` was reactivated, not retired — it now lives in `playbooks/services/shop-alarm.yml`; see Progress.)
7. **Clean up** the shared library: pick one namespace for the systemd role (`uhlig-it.simple_systemd_service` — fix `youtube-likes-feed`'s old `suhlig.simple_systemd_service` reference), add the missing `requirements.yml` entries (`uhlig-it.duckdns`, `uhlig-it.home-assistant-backup-b2`, `uhlig-it.sqlite_backup_b2`), and fix the broken `~/.ansible/roles/suhlig.foundation` symlink (it points at `ansible-role-foundation`, but the repo is now `foundation`).

## Conventions to adopt

- **Naming:** roles are `<namespace>.<role>`; playbooks are named after the host or the service they deploy.
- **Secrets:** one vaulted `group_vars/all/secrets.yml`; never inline secrets in role tasks (opus currently hardcodes a Pi-hole password and a Watchtower token — move these to the vault).
- **Dependencies:** every collection and role used anywhere is declared in the single `requirements.yml`; no more undeclared roles.
- **Idempotence:** every playbook must pass `--check` on a converged host.
- **CI:** every playbook gets `ansible-playbook --syntax-check` in CI, as kehrkraft and several others already do.

## Open questions

- Should the infrastructure repo be this workspace, or a fresh `uhlig-it/infrastructure`?
- Are `tape` and `lux` still wanted? If not, remove them from the tailnet.
- Should `pi0` be a managed Tailscale node, or is it intentionally off the tailnet?
- Do the app repos keep a thin `deployment/` shim, or does all Ansible move into the infrastructure repo?
- Keep `uhlig-it/inventory` (Terraform for `soda`) as the bootstrap for new Hetzner servers, or fold it into the infrastructure repo?

## Progress so far

The scaffold lives at `github.com/uhlig-it/infrastructure/` in this workspace.

- **Inventory, config, dependencies:** `inventory/hosts.yml` (all machines and groups, plus an `unassigned` parking group), `ansible.cfg`, `requirements.yml`, and a single `secrets.yml.example`.
- **Playbooks:** `playbooks/fleet/` (tailscale, metrics), `playbooks/machines/` (9 hosts), `playbooks/services/` (16 services), and `site.yml`.
- **Local roles migrated** into `roles/`: `frigate`, `GoToSocial`, `victoriametrics`, the opus container roles (`homeassistant`, `unifi`, `mosquitto`, `pi-hole`, `watchtower`, `uptime-kuma`, `shop-alarm`), `shop-watchdog`, `switchbot-mqtt`, `fadecandy`, `wordclock`, `rails-puma`.
- **Service playbooks migrated** with their real variables: `mqtt-router`, `speedtest-exporter`, `tasmota-firmware-proxy`, `mqtt-blink1`, `shorts`, `mqtt-gpio-binary-sensor`, `env-sensors`, `kehrkraft`, `gotosocial`, `concourse`, `www`, `speisehof`, `nowak-reisen`, `shop-alarm`, plus the parked `youtube-likes-feed` and `meal-tracker`.
- **shop-alarm folded in** (from `uhlig-it/opus`, branch `redesign-shop-alarm`): `roles/shop-alarm` plus `playbooks/services/shop-alarm.yml`. The mosquitto role also gained the retained-LWT availability guard (`roles/mosquitto/defaults/main.yml`, `roles/mosquitto/templates/availability-guard.sh.j2`) covering `frigate/available` and `werkstatt/alarm/available`.
- **CI wired up:**
  - `infrastructure/.github/workflows/ci.yml` syntax-checks every playbook.
  - `infrastructure/.github/workflows/deploy.yml` is a reusable workflow that checks out the calling app repo plus this repo, installs the toolchain, and runs `playbooks/services/<service>.yml`.
  - The release workflows of `mqtt-router`, `speedtest-exporter`, `tasmota-firmware-proxy`, `mqtt-blink1`, `shorts`, `mqtt-gpio-binary-sensor`, and `mosquitto-prometheus-exporter` now call it from a `deploy` job.
  - The Concourse pipelines of `env-sensors` and `www` now point their `playbook` resource at this repo.
  - `mosquitto-exporter-deployment` is retired; its playbook now builds from source and deploys to `opus`.
- **CI green:** the infrastructure repo's syntax-check passes on `main`. Getting there required installing collections as well as roles; moving `artis3n.tailscale` to `roles:`; installing `suhlig.foundation` from git (its new roles are not released to Galaxy); excluding `playbooks/services/files/` from the loop; and adding `community.docker`, `community.general`, `community.postgresql`, and `hifis.toolkit`.
- **Parked services:** `youtube-likes-feed` and `meal-tracker` target the `unassigned` group and are marked "CURRENTLY DEPLOYED NOWHERE" in their playbooks. They need a host before they can run.
- **Duplication removed:** the `deployment/` directories were deleted from the nine app repos (`mqtt-router`, `speedtest-exporter`, `mqtt-gpio-binary-sensor`, `env-sensors`, `shorts`, `youtube-likes-feed`, `meal-tracker`, `wordclock`, `www`), and their `syntax-check` CI jobs were removed (this repo's CI covers playbook syntax now). The vault-encrypted secrets from those directories were rescued into `secrets-import/`.
- **Concourse role:** switched from `troykinsella.concourse` to `suhlig.concourse`, a fork with the removed `include` action fixed, installed from git (not Galaxy). `site.yml` now syntax-checks cleanly.
- **Secrets consolidated:** all 27 `secrets-import/` files are merged. `inventory/group_vars/all/secrets.yml` holds the host-independent secrets (CI tokens, TLS email, metrics passwords, kehrkraft, uhlig.social, concourse, shorts, ytlf, meal-tracker, mosquitto-exporter, shop-alarm). Host-specific secrets live in `inventory/host_vars/<host>/secrets.yml` for opus, shop, pi5, pi0, kunakam, hansahaus, fortcarsta, and wordclock; those hosts now use a `host_vars/<host>/` directory (`main.yml` + `secrets.yml`). The MQTT URLs are service-specific (`mqtt_router_mqtt_url`, `mqtt_gpio_binary_sensor_mqtt_url`, `mqtt_blink1_mqtt_url`) because `mqtt-blink1` and `mqtt-gpio-binary-sensor` use different brokers on `shop`. All nine vault files are encrypted, and `secrets-import/` has been deleted.
- **Frigate brought under management (2026-10-09):** the 2026-10-08 audit found `opus`'s `/opt/frigate/config/config.yml` was hand-maintained and reproduced by no repo (not even the old `uhlig-it/opus`; its `# Ansible managed` header was stale). `roles/frigate` now renders it: the camera comes from `frigate.cameras` (name → go2rtc source, currently `werkstatt` → `ffmpeg:rtsp://192.168.1.2:8554/cam`), plus the go2rtc restream, `profiles`, `snapshots` and per-camera review. The config is bind-mounted as the `/opt/frigate/config` **directory** (was a single file), the `frigate`/`cameras` vars moved from `host_vars/kunakam` to `host_vars/opus`, and the play moved from `machines/kunakam.yml` to `machines/opus.yml`.
- **kunakam is obsolete as a camera** and will be retired; the `mediamtx` deployment aspect stays (used by `shop` and `pi0`, from `suhlig.foundation`).
- **Shop LAN addressing lives outside this repo:** static DHCP reservations are in `uhlig-it/shop-router/hosts.yml` (OpenWrt `configure.sh`), pinning `shop` → `192.168.1.2`, `pascal` → `.3`, `gosund-*` → `.10/.11/.12`, `sonoff-*` → `.20/.21/.22`, `irlight` → `.30`. The router's dnsmasq also serves those names in LAN DNS (`shop.lan`, `gosund-0.lan`, …) but **not** `.local` (mDNS).

### Still to do

- Move the remaining machine playbook inline tasks (`pascal`'s TLS-cert and shop-health tasks).
- Configure the `vault_password` secret in each app repo that calls the deploy workflow (the SSH deploy key is now read from the vault, so no `ssh_key` secret is needed).
- Give the two parked services a host and re-enable their deploys.
- Retire the deployment-only repos whose playbooks now live here (`kehrkraft-deployment`, `uhlig.social-deployment`, `concourse-deployment`, `tailscale`, and the per-host repos `pi5`, `pi0`, `fortress`, `opus`, `pascal`, `kunakam`, `shop`). `opus` is unblocked (its `shop-alarm` role and the mosquitto guard are folded in) but its GitHub repo is still private and not archived.
- Delete `deployment/` from the two out-of-workspace site repos (`speisehof/www`, `nowak-reisen/www`).
- Delete the root-level `playbook.yml` (and `files/`) from `mqtt-blink1` and `tasmota-firmware-proxy`, the two app repos that keep their playbook at the repo root rather than in `deployment/`.
- Retire `kunakam` as a camera: drop it from `inventory/hosts.yml`, `host_vars/kunakam/`, and the camera play in `machines/kunakam.yml`; keep the `mediamtx` deployment for `shop`/`pi0`.
- Fix `roles/shop-watchdog/files/watchdog.sh`: it restarts `wpa_supplicant dhcpcd`, but the shop Pi runs NetworkManager and `dhcpcd` is not installed.
- Drop the shop broker's hardcoded IP: Tasmota `MqttHost` is `192.168.1.2`. The shop router already answers `shop`/`shop.lan` in LAN DNS, so a hostname (not an IP) removes the dependency on the reservation.
