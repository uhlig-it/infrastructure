# Smoke detection via an INMP441 I2S microphone

Workspace/branch: `add-smoke-detection-via-INMP441`. wtg space at `~/workspace/add-smoke-detection-via-INMP441`, repo worktree `github.com/uhlig-it/infrastructure`.

## Goal

Add INMP441 I2S MEMS microphones so Frigate can raise `smoke_detector` / `fire_alarm` audio events for the smoke detectors. Each sensor is a self-contained **ESP32 node** that publishes its microphone on the network; the single Frigate at `opus` consumes the streams as audio sources. Everything is deployed from this repo. Status: the **home** node (living room) is deployed, flashed and streaming; the **shop** node is pending hardware. **Frigate's built-in audio detection turned out not to detect our (German) smoke alarms** (see *Detection findings*), so the detection mechanism is being replaced. Two sites — the living room at home and the workshop at the shop.

## Why an ESP32 node instead of wiring the mic to the Pi

The I2S bus is a short-range link: its bit clock runs at ~3 MHz (48 kHz × 64), so the mic must sit within a few centimetres of its host — ≤10–15 cm ideal, ~20–30 cm marginal, and unreliable past ~0.5 m. Wiring the INMP441 straight to a Raspberry Pi therefore pins the microphone to wherever the Pi happens to be, which for a smoke detector is the wrong place.

An ESP32 (a few euros, Wi-Fi built in) captures the mic locally over that short I2S run and puts the audio on the network instead; only network latency remains, which at ~32 kB/s (16 kHz mono) is irrelevant. So **both** sites use an ESP32 sensor node: at home the detector is in the living room (no camera, and far from any server), and in the shop it is on the workshop WLAN, away from the Pis. The Raspberry Pi prototype recorded below proved the mic and the mic→go2rtc→RTSP→Frigate shape; the ESP32 replaces it as the deployment.

## Decisions

- **Sensor node: ESP32-DevKitC V4** (Espressif, ESP32-WROOM-32). One per site, powered from a USB supply and joined to the local WLAN.
- **Mic: INMP441**, on the ESP32's I2S0 peripheral, wired with a short harness (see the wiring table).
- **Transport: a dedicated RTSP/HTTP streamer firmware** on the ESP32 — *not* ESPHome (ESPHome reads the mic but has no audio *server*). **Frigate pulls the node's RTSP directly**: go2rtc cannot restream an audio-only source (its RTSP server carries video only), so the audio bypasses go2rtc. Selected: **`rjsachse/ESP32-RTSPServer`** (a generic-ESP32 library with RTSP Basic auth), **vendored** under `lib/` in [`uhlig-it/esp32-mic-node`](https://github.com/uhlig-it/esp32-mic-node) so we can patch it — upstream is dormant. See that repo's README and `lib/ESP32-RTSPServer/UPSTREAM.md`.
- **Auth: HTTP Basic** on the stream. Sufficient because the stream is only pulled by `opus` over the tailnet (and, in the shop, only across the shop-router WLAN), so the WLAN/tailnet is the trust boundary rather than the credential. Use an isolated/IoT SSID where practical, and upgrade to Digest if the chosen firmware supports it.
- **Stable addressing: a DHCP reservation** — at home a static reservation gives the node `192.168.10.40`; in the shop via `github.com/uhlig-it/shop-router` (`hosts.yml`, installed by `configure.sh`, one entry). The Frigate URL never moves.
- **Reachability (shop): over the tailnet, via `shop`'s subnet router.** The workshop WLAN *is* the `shop-router` LAN, `192.168.1.0/24`; `shop` advertises that route and `opus` runs `--accept-routes`, so `opus` reaches a `192.168.1.x` ESP32 directly and Frigate pulls its stream with no relay on `shop`/`pascal`.
- **Single Frigate, at `opus`.** Both sensors feed it: the living-room node as audio-only (there is no camera there), the workshop node alongside the `werkstatt` camera's audio.
- **Frigate camera vars: refactored** (done) out of the vaulted `inventory/host_vars/opus/secrets.yml` into plaintext `inventory/host_vars/opus/main.yml`, bridging only the MQTT password and the node's RTSP/HTTP passwords from the vault. Camera URLs are not secret; this avoids editing the vault on every camera change.
- **Alerting: HA emergency push.** Home Assistant (on `opus`) subscribes to Frigate's audio MQTT topics and sends a **critical iOS notification** for `smoke_detector`/`fire_alarm`, for both the living-room and the workshop camera (see Phase 5).
- **Unlock gating (shop only):** the shop stream is disabled by the same `disarmed` profile that motion already uses (Frigate profiles can override the `audio` section); `shop-alarm` already switches the profile on lock/unlock, so it needs no change. The home node listens continuously.
- **Repo: split.** The node's firmware and its wiring diagram live in [`uhlig-it/esp32-mic-node`](https://github.com/uhlig-it/esp32-mic-node); this repo keeps the plan and the Frigate/Ansible config. A PlatformIO build, a released `.bin`, and the likelihood of patching a dormant upstream make the firmware application code — which the consolidation principle puts in its own repo (`docs/consolidation-plan.md`).
- **Retire the Pi path.** The `i2s-mic` role's boot-config tasks and `ha-kiosk`'s go2rtc mic stream are superseded by the ESP32 node. Done: the role's Pi tasks are removed (its directory is gone — the node's firmware and diagram moved to `uhlig-it/esp32-mic-node`) and `ha-kiosk` is fully reverted — the `i2s-mic` and `go2rtc` roles are gone from its play, its mic stream removed, and the host cleaned up (overlay removed, `go2rtc` uninstalled, rebooted).

## Architecture

```mermaid
flowchart LR
    subgraph home [home - living room]
        hmic[INMP441 I2S] --> hesp[ESP32-DevKitC V4<br/>RTSP audio + placeholder video]
    end
    subgraph shop [shop - workshop WLAN 192.168.1.0/24]
        smic[INMP441 I2S] --> sesp[ESP32-DevKitC V4<br/>RTSP audio + placeholder video]
    end
    subgraph opus [opus - NVR]
        det[purpose-built detector<br/>Goertzel tone/pattern] --> mqtt[MQTT<br/>mic/living_room/smoke_alarm]
        mqtt --> ha[HA critical iOS push]
    end
    hesp -->|RTSP audio, direct| det
    sesp -->|RTSP audio, via shop subnet router| det
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
- **Frigate:** runs `ghcr.io/blakeblackshear/frigate:stable` (**0.18.0**) with a USB Coral at `opus`. Cameras come from `frigate.cameras` in plaintext `inventory/host_vars/opus/main.yml` (refactored out of the vault) and are rendered by `roles/frigate/templates/config.yml.j2`, which now emits `detect`/`record`/`live`/`snapshots` plus optional per-camera `audio` and a placeholder `video` input. Audio labels `smoke_detector` and `fire_alarm` exist; profiles can override the `audio` section.

## Work plan

### Phase 0 — role scaffold + Pi prototype (done; reverted)

The `i2s-mic` role directory was created to hold the wiring diagram; the INMP441→Pi 3 diagram (wiregen) was added and CI-rendered. On `ha-kiosk` the boot config was set, a reboot loaded the overlay, and `arecord -l` confirmed the capture card. This validated the microphone. The diagram now targets the ESP32; it and the node's firmware have since moved to `uhlig-it/esp32-mic-node`. The Pi boot-config work is reverted.

### Phase 1 — the i2s-mic role's tasks (done; removed)

The role managed the Pi's boot config (`/boot/firmware/config.txt` on Bookworm, `/boot/config.txt` on bullseye): `dtparam=audio=off`, `dtoverlay=googlevoicehat-soundcard`, keep `i2c_arm=on`, a reboot handler, and an `arecord -l` assertion. Superseded by the ESP32 node and now removed; the role directory is gone and the node's firmware and diagram moved to `uhlig-it/esp32-mic-node`.

### Phase 2 — the Pi audio stream (done; reverted)

go2rtc on `ha-kiosk` published the mic as `rtsp://ha-kiosk:8554/mic` using an ffmpeg `exec:` source (native `alsa:` only emits raw S16LE and go2rtc's `ffmpeg:` source would not produce), AAC mono, with the listeners tailnet-bound. Superseded: with a streaming ESP32 the stream originates at the node and no go2rtc runs on the sensor side. Reverted: the `go2rtc` role and mic stream were removed from `ha-kiosk`, and on 2026-10-10 its host was cleaned up — the `config.txt` overlay removed, `dtparam=audio` restored, `go2rtc` uninstalled, and the host rebooted (confirmed: no capture card, onboard `bcm2835` audio back).

### Phase 3 — ESP32 sensor node (done)

- Firmware built and flashed on **home site** (living room). ESP32-DevKitC V4 (ESP32-WROOM-32) at `192.168.10.40` (a static reservation on the home router), WLAN `uhlig.wlan`. Stream URL: `rtsp://frigate:PASSWORD@192.168.10.40:554/` (the credential is in `include/secrets.h`). Stream format: L16 mono PCM, 16 kHz, 256 kb/s. Verified with ffplay on Mac.
- Three bugs fixed in `rjsachse/ESP32-RTSPServer` and committed as three separate commits (PR #35): https://github.com/rjsachse/ESP32-RTSPServer/pull/35

  1. **`1c1064` — Null deref in `handleOptions`** — `pathStart` used before NULL check when the request doesn't contain `rtsp://` (e.g. `OPTIONS * RTSP/1.0`).
  2. **`36f777` — `ps_malloc()` on chips without PSRAM** — falls back to `malloc()` (internal SRAM 320 KB); without this the server silently drops all connections on ESP32-WROOM-32.
  3. **`f121ef` — Re-authentication on every request** — skips auth for established sessions (`session.sessionID != 0`), matching RFC 2326 session semantics so ffplay/vlc don't get 401 on SETUP/PLAY.

- **README.md** in `uhlig-it/esp32-mic-node` documents the working `ffplay` commands (no `-rtsp_transport tcp` — the transport flag causes failures with this RTSP server implementation) and all prerequisites.
- The node also runs a **Basic-auth HTTP server on port 80** (`src/web.cpp`): a live status page at `/` (server-sent events), Prometheus metrics at `/metrics` (`esp32_mic_*`, incl. the active `ssid`, the `network` role and a `build` stamp) for VictoriaMetrics to scrape, a firmware-upload form at `/update` (plus ArduinoOTA), and the placeholder frame at `/dummy.mjpg`. It joins a **secondary Wi-Fi** if the primary is unreachable within 10 s.
- A parametric **3D-printable enclosure** for the node ships in `design/` (FreeCAD model → STL, plus a tuned Cura profile).
- **Shop site:** not yet deployed (needs INMP441 hardware + wiring + shop-router DHCP reservation).
- **Stable address (shop):** DHCP reservation in `shop-router/hosts.yml` is one entry — pending hardware.
- **Reachability test:** the home node is verified reachable from this Mac (`ffplay`) *and* from `opus` — Frigate's stats report the `living_room` camera live with audio (the home node shares `opus`'s LAN, so no relay is involved).
- **Upstream PR:** PR #35 against `rjsachse/ESP32-RTSPServer` contains the three commits. (Pushed to fork and submitted — pending upstream review.)

### Phase 4 — Frigate audio plumbing (done; home node)

- Refactored `frigate.cameras` (with `mqtt.host`/`user`) out of the vault into plaintext `inventory/host_vars/opus/main.yml`; the vault keeps only `vault_frigate_mqtt_password` and the nodes' passwords. Camera URLs are not secret, so a camera change no longer touches the vault.
- The home node (`192.168.10.40`) is a Frigate camera, `living_room`: its audio arrives over RTSP (`roles: [audio]`), with a global `audio.listen: [smoke_detector, fire_alarm]` and an RMS gate (`min_volume`). Confirmed live — Frigate's stats report a low `audio_rms` (~6–11) for the quiet room.
- `roles/frigate/templates/config.yml.j2` grew optional per-camera audio (`mic`, `audio_only`, `disarm_audio`) plus the global `audio:` block.
- **Frigate needs a video stream for every camera** — it "always decodes a stream ... even if [object detection is] disabled", and has no video-less camera mode. So `living_room` also gets a `detect` input that is a **placeholder MJPEG**: a fixed 640×480 FuBK test card captioned "This stream is audio-only" (~1 fps) that the mic node itself serves at `http://<node>/dummy.mjpg`. `detect`/`record`/`snapshots` are off for it and `hwaccel_args` is cleared (the global QSV preset does not apply to MJPEG). go2rtc cannot restream an audio-only source (its RTSP server carries video only), which is why Frigate reads the node directly. The node credits the card's Wikimedia original (CC BY-SA 3.0) and shows it in a Video section on the status page.
- **Shop node: pending hardware.** `werkstatt` will take its audio through the same `mic` mechanism; `disarm_audio` is already supported so the shop stream mutes with the `disarmed` profile.
- **`min_volume`** is 200 (lowered from the 500 default so a real alarm horn opens the gate; ambient peaks ~45). In practice moot — the model does not detect our alarms (see *Detection findings*).

### Phase 5 — alerting (done; home + shop fire/smoke)

Home Assistant (on `opus`, `2026.10.1`) pushes an **emergency (critical) iOS notification** when Frigate hears a smoke/fire alarm. Two automations, triggered by Frigate's audio MQTT topics (`frigate/<camera>/audio/{smoke_detector,fire_alarm}` = `ON`), call the mobile-app **service** `notify.mobile_app_iphone_15_pro_steffen` — the newer notify *entity* action supports only `message`/`title`, so it cannot carry the critical-push payload (`data.push.sound.critical`):

- `mic_living_room_smoke_emergency` — the living-room mic node.
- `werkstatt_alarm_fire_notify` — the workshop (parity with the home node; suppressed while the shop alarm panel is already `triggered`).

They are **codified**: the `homeassistant` role templates [`roles/homeassistant/templates/packages/smoke_detection.yaml.j2`](../roles/homeassistant/templates/packages/smoke_detection.yaml.j2) to `opus:/opt/homeassistant/packages/smoke_detection.yaml`, and ensures `configuration.yaml` loads the `packages/` directory, so the alerting survives an HA rebuild. (The rest of HA's `automations.yaml` — the alarm-panel logic and everything the UI manages — stays hand-managed.)

**Note (2026-10-11):** these automations are wired but never fire in practice — Frigate's audio model does not detect our alarms (see *Detection findings* below); Phase 6 replaces the trigger.

### Phase 6 — purpose-built smoke-alarm detector (in progress; server-side prototype)

Replace Frigate's audio detection with a detector tuned to the alarms' actual acoustic signature, publishing an event the HA automation acts on (reusing the critical-push package above). **Decided: build and tune it server-side on `opus` first** (a small service reading the node's RTSP → MQTT `mic/living_room/smoke_alarm`), **then port the same algorithm to the node firmware**; on-node detection would let the RTSP server be removed entirely. Server-side is fastest to iterate (no reflashing per threshold tweak).

The `living_room` camera was dropped from Frigate so the node's single RTSP client slot is free for the detector (go2rtc cannot restream an audio-only source, so the detector pulls the node directly).

**Measured acoustic signature** (a real Rauchwarnmelder, via the node's stream, 16 kHz mono, captured 2026-10-12):

| Feature | Value |
| --- | --- |
| Tones | two **simultaneous** narrowband lines, ~3.08 kHz and ~4.92 kHz |
| Gating | amplitude-pulsed at ~3 Hz (~0.19 s on / ~0.13 s off) → periodic broadband clicks |
| Duration | ~6.5–8 s per button press |
| Level | ~−57 dB (loud press) / ~−67 dB (quiet press), 20 ms frame tone power |

**Validated detector** (`detect_proto.py`, pure stdlib; see *Open questions* for where this reference lives): per 20 ms frame (10 ms hop) run a Goertzel bank per band (max over 2950–3250 Hz and 4700–5150 Hz, to tolerate detector-to-detector drift) plus the frame's total power. A frame is "on" when **both** bands hold a large share of the energy (`low_ratio > 0.03` and `high_ratio > 0.02`) **and** the absolute tone power clears a −80 dB floor. A 5 s sliding window is an alarm when ≥30% of its frames are "on".

Results against the reference recording: fires on both presses (~11–20 s and ~39–47 s) and nowhere else. Speech, background and silence give `on = 0%` (their band ratios stay ≤0.005 vs ~0.16/~0.08 during a press), and a single pure tone is correctly ignored — the dual-tone requirement is what separates the alarm from beeps.

## Detection findings: Frigate's audio model does not detect our alarms

Established 2026-10-11 against the home node. Frigate's built-in audio detector is a generic **AudioSet** classifier (521 classes; `/cpu_audio_model.tflite`, 2023) whose `smoke_detector`/`fire_alarm` classes were trained on (mostly US/UK) YouTube clips. German Rauchwarnmelder have **no standardised tone**: EN 14604 / DIN 14676 fix only the loudness (≥85 dB(A) at 3 m) and that the low-battery chirp differs from the alarm — the alarm waveform is manufacturer-specific (~3–4 kHz piezo, continuous or pulsed).

Evidence (Frigate's `audio.living_room` debug log; `listen: [smoke_detector, fire_alarm]`, `min_volume: 200`):

| Source (all sounded loudly) | Peak RMS | Model's best labels |
| --- | --- | --- |
| Smoke-detector test button (×2) | ~987 | `ringtone` 0.59, `telephone` 0.5, `glass` 0.5 |
| Known Rauchmelder recording (played from a phone) | ~1015 | `jingle_bell` 0.59, `alarm` 0.5, `ringtone` 0.5 |
| **Speech control** (someone talking at the mic) | ~795 | **`speech` 0.74–0.996** |

Neither alarm source ever produced `smoke_detector` (or `fire_alarm`): the model's best guesses are generic beep classes around 0.5 — below Frigate's 0.5 floor, far below the 0.8 filter threshold. The speech control shows the mic → node → Frigate audio path is clean and the classifier works on it, so this is a **model-coverage limit, not our hardware** (and the node's 16-bit I²S read is *not* hurting us — no firmware change needed).

**Consequence:** Frigate's audio detection cannot be tuned into a reliable smoke-alarm detector for our devices, so the detection mechanism is replaced by the purpose-built detector in Phase 6. (Its two tones land in exactly the band its `smoke_detector` class ignores.)

## Open questions

- **Server-side detector runtime / dependencies** — where to run the detector is now decided (server-side on `opus`); the remaining choice is how to read the node's stream. `opus` is Debian 11 with **no `ffmpeg`** (apt has 4.3.9) and Python 3.9. Options: (a) a Python detector plus `ffmpeg` (fastest, reuses the validated prototype); (b) a Go service using an RTSP library (no external binary, matches the repo's other Go services); (c) a small pure-Python RTSP/RTP client (no `ffmpeg`, tiny deps). The node streams **L16 PCM**, so no codec is needed either way — only RTP depacketisation.
- **Where the detector reference lives** — the validated prototype is currently an untracked scratch file; once the runtime is chosen it should live in this repo (its role/files) so the algorithm is versioned and can be ported to the node.

## Related / out of scope

- The TSL2561 lux sensor moved to `uhlig-it/lux-sensor` (an ESPHome config); `env-sensors` and `host_vars/shop` still carry stale TSL2561 wiring. See `docs/consolidation-plan.md`.
