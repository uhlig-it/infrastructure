# Smoke detection via an INMP441 I2S microphone

Workspace/branch: `add-smoke-detection-via-INMP441`. wtg space at `~/workspace/add-smoke-detection-via-INMP441`, repo worktree `github.com/uhlig-it/infrastructure`.

## Goal

Add INMP441 I2S MEMS microphones so Frigate can raise `smoke_detector` / `fire_alarm` audio events for the smoke detectors. Each sensor is a self-contained **ESP32 node** that publishes its microphone on the network; the single Frigate at `opus` consumes the streams as audio sources. Everything is deployed from this repo. Status: planned; two sites — the living room at home and the workshop at the shop.

## Why an ESP32 node instead of wiring the mic to the Pi

The I2S bus is a short-range link: its bit clock runs at ~3 MHz (48 kHz × 64), so the mic must sit within a few centimetres of its host — ≤10–15 cm ideal, ~20–30 cm marginal, and unreliable past ~0.5 m. Wiring the INMP441 straight to a Raspberry Pi therefore pins the microphone to wherever the Pi happens to be, which for a smoke detector is the wrong place.

An ESP32 (a few euros, Wi-Fi built in) captures the mic locally over that short I2S run and puts the audio on the network instead; only network latency remains, which at ~32 kB/s (16 kHz mono) is irrelevant. So **both** sites use an ESP32 sensor node: at home the detector is in the living room (no camera, and far from any server), and in the shop it is on the workshop WLAN, away from the Pis. The Raspberry Pi prototype recorded below proved the mic and the mic→go2rtc→RTSP→Frigate shape; the ESP32 replaces it as the deployment.

## Decisions

- **Sensor node: ESP32-DevKitC V4** (Espressif, ESP32-WROOM-32). One per site, powered from a USB supply and joined to the local WLAN.
- **Mic: INMP441**, on the ESP32's I2S0 peripheral, wired with a short harness (see the wiring table).
- **Transport: a dedicated RTSP/HTTP streamer firmware** on the ESP32 — *not* ESPHome (ESPHome reads the mic but has no audio *server*), chosen so Frigate's bundled go2rtc can pull it like any camera. Selected: an audio-only RTSP sketch on **`rjsachse/ESP32-RTSPServer`** (a generic-ESP32 library with RTSP Basic auth); see [`uhlig-it/esp32-mic-node`](https://github.com/uhlig-it/esp32-mic-node).
- **Auth: HTTP Basic** on the stream. Sufficient because the stream is only pulled by `opus` over the tailnet (and, in the shop, only across the shop-router WLAN), so the WLAN/tailnet is the trust boundary rather than the credential. Use an isolated/IoT SSID where practical, and upgrade to Digest if the chosen firmware supports it.
- **Stable addressing: a DHCP reservation** — in the shop via `github.com/uhlig-it/shop-router` (`hosts.yml`, installed by `configure.sh`); at home via the router's reservation or a static IP — so the Frigate URL never moves.
- **Reachability (shop): over the tailnet, via `shop`'s subnet router.** The workshop WLAN *is* the `shop-router` LAN, `192.168.1.0/24`; `shop` advertises that route and `opus` runs `--accept-routes`, so `opus` reaches a `192.168.1.x` ESP32 directly and Frigate pulls its stream with no relay on `shop`/`pascal`.
- **Single Frigate, at `opus`.** Both sensors feed it: the living-room node as audio-only (there is no camera there), the workshop node alongside the `werkstatt` camera's audio.
- **Frigate camera vars: refactor** them out of the vaulted `inventory/host_vars/opus/secrets.yml` into plaintext `inventory/host_vars/opus/main.yml`, bridging only the MQTT password from the vault. Camera URLs are not secret; this avoids editing the vault on every camera change.
- **Detection only** for now; no alerting integration yet.
- **Unlock gating (shop only):** the shop stream is disabled by the same `disarmed` profile that motion already uses (Frigate profiles can override the `audio` section); `shop-alarm` already switches the profile on lock/unlock, so it needs no change. The home node listens continuously.
- **Repo: split.** The node's firmware and its wiring diagram live in [`uhlig-it/esp32-mic-node`](https://github.com/uhlig-it/esp32-mic-node); this repo keeps the plan and the Frigate/Ansible config. A PlatformIO build, a released `.bin`, and the likelihood of patching a dormant upstream make the firmware application code — which the consolidation principle puts in its own repo (`docs/consolidation-plan.md`).
- **Retire the Pi path.** The `i2s-mic` role's boot-config tasks and `ha-kiosk`'s go2rtc mic stream are superseded by the ESP32 node. Done: the role's Pi tasks are removed (its directory is gone — the node's firmware and diagram moved to `uhlig-it/esp32-mic-node`) and `ha-kiosk` is fully reverted — the `i2s-mic` and `go2rtc` roles are gone from its play, its mic stream removed, and the host cleaned up (overlay removed, `go2rtc` uninstalled, rebooted).

## Architecture

```mermaid
flowchart LR
    subgraph home [home - living room]
        hmic[INMP441 I2S] --> hesp[ESP32-DevKitC V4<br/>RTSP/HTTP stream]
    end
    subgraph shop [shop - workshop WLAN 192.168.1.0/24]
        smic[INMP441 I2S] --> sesp[ESP32-DevKitC V4<br/>RTSP/HTTP stream]
    end
    subgraph opus [opus - NVR]
        fg2r[Frigate go2rtc] --> ad[audio detector]
        ad --> mqtt[Frigate MQTT<br/>smoke_detector / fire_alarm]
    end
    hesp -->|stream over tailnet| fg2r
    sesp -->|stream over tailnet, via shop subnet router| fg2r
```

## INMP441 wiring (ESP32-DevKitC V4)

| INMP441 | ESP32-DevKitC V4 | Notes |
| --- | --- | --- |
| VDD | 3V3 | 3V3 only — never 5V |
| GND | GND | |
| L/R | GND | selects the left channel |
| SCK (BCLK) | IO26 | bit clock (ESP32 output) |
| WS (LRCLK) | IO25 | word select (ESP32 output) |
| SD (DIN) | IO33 | mic data into the ESP32 |

The diagram is rendered by [wiregen](https://github.com/WeebLabs/wiregen) from [`diagrams/inmp441-esp32.yaml`](https://github.com/uhlig-it/esp32-mic-node/blob/main/diagrams/inmp441-esp32.yaml) in [`uhlig-it/esp32-mic-node`](https://github.com/uhlig-it/esp32-mic-node), using the `suhlig/wiregen` fork until its new INMP441, Raspberry Pi 3 and ESP32-DevKitC V4 parts are merged upstream. The node's firmware lives there too; the earlier INMP441→Pi 3 diagram is superseded.

## Facts already gathered

- **Pi prototype (superseded):** `ha-kiosk` (Pi 4, Debian trixie) ran the INMP441 directly — the `i2s-mic` role set `dtoverlay=googlevoicehat-soundcard` + `dtparam=audio=off` in `/boot/firmware/config.txt`, `arecord -l` showed `card 0: snd_rpi_googlevoicehat_soundcard`, and go2rtc published `rtsp://ha-kiosk:8554/mic` (AAC 48 kHz mono, left channel) with its listeners bound to the tailnet address. This proved the mic and the mic→go2rtc→RTSP→Frigate shape. Under the ESP32 decision it is retired: the Pi-side setup has been removed and `ha-kiosk` reverted.
- **Workshop WLAN / reachability (verified):** the workshop WLAN is the `shop-router` LAN, `192.168.1.0/24`. `shop` advertises that route (`--advertise-routes=192.168.1.0/24 --accept-routes`) and `opus` accepts routes (`--accept-routes`), so `opus` reaches it. `http://192.168.1.10/` (a Tasmota plug on that LAN) answers `401` with `WWW-Authenticate: Basic realm="Login Required"`, confirming the route is approved *and* that a Basic-auth device is reachable from the tailnet. `shop`'s `net.ipv4.ip_forward` is `1`.
- **DHCP reservations (shop):** `github.com/uhlig-it/shop-router` holds `hosts.yml` (`name`/`mac`/`ip`), applied by `configure.sh`; adding the ESP32 is one entry.
- **Frigate:** runs `ghcr.io/blakeblackshear/frigate:stable` (0.15) with a USB Coral at `opus`; cameras come from `frigate.cameras` (currently vaulted in `inventory/host_vars/opus/secrets.yml`) and are rendered by `roles/frigate/templates/config.yml.j2`, which emits only `detect` + `record`. Audio labels `smoke_detector` and `fire_alarm` exist; profiles can override the `audio` section.

## Work plan

### Phase 0 — role scaffold + Pi prototype (done; reverted)

The `i2s-mic` role directory was created to hold the wiring diagram; the INMP441→Pi 3 diagram (wiregen) was added and CI-rendered. On `ha-kiosk` the boot config was set, a reboot loaded the overlay, and `arecord -l` confirmed the capture card. This validated the microphone. The diagram now targets the ESP32; it and the node's firmware have since moved to `uhlig-it/esp32-mic-node`. The Pi boot-config work is reverted.

### Phase 1 — the i2s-mic role's tasks (done; removed)

The role managed the Pi's boot config (`/boot/firmware/config.txt` on Bookworm, `/boot/config.txt` on bullseye): `dtparam=audio=off`, `dtoverlay=googlevoicehat-soundcard`, keep `i2c_arm=on`, a reboot handler, and an `arecord -l` assertion. Superseded by the ESP32 node and now removed; the role directory is gone and the node's firmware and diagram moved to `uhlig-it/esp32-mic-node`.

### Phase 2 — the Pi audio stream (done; reverted)

go2rtc on `ha-kiosk` published the mic as `rtsp://ha-kiosk:8554/mic` using an ffmpeg `exec:` source (native `alsa:` only emits raw S16LE and go2rtc's `ffmpeg:` source would not produce), AAC mono, with the listeners tailnet-bound. Superseded: with a streaming ESP32 the stream originates at the node and no go2rtc runs on the sensor side. Reverted: the `go2rtc` role and mic stream were removed from `ha-kiosk`, and on 2026-10-10 its host was cleaned up — the `config.txt` overlay removed, `dtparam=audio` restored, `go2rtc` uninstalled, and the host rebooted (confirmed: no capture card, onboard `bcm2835` audio back).

### Phase 3 — ESP32 sensor node (done)

- Firmware built and flashed on **home site** (living room). ESP32-DevKitC V4
  (ESP32-WROOM-32) at `192.168.10.243`, WLAN `uhlig.iot`. Stream URL:
  `rtsp://frigate:PASSWORD@192.168.10.243:554/`.
  Stream format: L16 mono PCM, 16 kHz, 256 kb/s. Verified with ffplay on Mac.
- Three bugs fixed in `rjsachse/ESP32-RTSPServer` and committed as three separate
  commits (PR #35): https://github.com/rjsachse/ESP32-RTSPServer/pull/35

  1. **`1c1064` — Null deref in `handleOptions`** — `pathStart` used before NULL
     check when the request doesn't contain `rtsp://` (e.g. `OPTIONS * RTSP/1.0`).
  2. **`36f777` — `ps_malloc()` on chips without PSRAM** — falls back to
     `malloc()` (internal SRAM 320 KB); without this the server silently drops
     all connections on ESP32-WROOM-32.
  3. **`f121ef` — Re-authentication on every request** — skips auth for
     established sessions (`session.sessionID != 0`), matching RFC 2326 session
     semantics so ffplay/vlc don't get 401 on SETUP/PLAY.

- **README.md** in `uhlig-it/esp32-mic-node` documents the working `ffplay`
  commands (no `-rtsp_transport tcp` — the transport flag causes failures with
  this RTSP server implementation) and all prerequisites.
- **Shop site:** not yet deployed (needs INMP441 hardware + wiring + shop-router
  DHCP reservation).
- **Stable address (shop):** DHCP reservation in `shop-router/hosts.yml` is one
  entry — pending hardware.
- **Reachability test:** home node verified reachable from this Mac over the
  local network (`ffplay rtsp://frigate:PASSWORD@192.168.10.243:554/`).
  Tailnet reachability from `opus` will be confirmed when the node is deployed
  and the `--accept-routes` / tailnet path is in place.
- **Upstream PR:** PR #35 against `rjsachse/ESP32-RTSPServer` contains the three
  commits. (Pushed to fork and submitted — pending upstream review.)

### Phase 4 — Frigate audio detection

In progress. Steps:

- Refactor `frigate.cameras` (currently vaulted in `inventory/host_vars/opus/secrets.yml`)
  into plaintext `inventory/host_vars/opus/main.yml`, bridging only the MQTT
  password from the vault.
- Add the living-room node (`192.168.10.243`) to `opus` as an **audio-only** source
  (go2rtc source + `ffmpeg` input with `roles: [audio]`, `audio: { enabled: true,
  listen: [smoke_detector, fire_alarm], min_volume: <tuned> }`).
- Attach the workshop node to the `werkstatt` camera's `audio` role the same way.
- Extend `roles/frigate/templates/config.yml.j2` to emit optional per-camera audio
  inputs.
- Add `audio: { enabled: false }` to the `disarmed` profile (shop only).
- Deploy and tune `min_volume`.

### Phase 5 — alerting (later)

Wire `shop-alarm`/HA to act on Frigate `smoke_detector`/`fire_alarm` events. Depends on the separate alerting work already noted in `docs/consolidation-plan.md`.

## Open questions

- **Audio-only in Frigate:** Frigate normally ties audio to a camera that also has video, but the living room has no camera. Decide how to expose the node to `opus` — a synthetic/placeholder detect input, or an audio-only camera if Frigate 0.15 accepts one.
- **`min_volume`:** unknown until we can sample the detectors; tune on the node.

## Related / out of scope

- The TSL2561 lux sensor moved to `uhlig-it/lux-sensor` (an ESPHome config); `env-sensors` and `host_vars/shop` still carry stale TSL2561 wiring. See `docs/consolidation-plan.md`.
