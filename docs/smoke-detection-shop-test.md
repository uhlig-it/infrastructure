# Shop smoke-alarm test

A short, repeatable procedure for testing the **workshop** smoke-alarm detection on the next shop visit, so the trip is a two-minute job rather than a session of rediscovery. It covers the shop node's on-device detector (the shipped path) and, separately, capturing audio to confirm or retune the `smoke_shop_v1` profile.

Everything here assumes the mic-node repo and this repo are checked out on the controller, and that `opus` is reachable over the tailnet.

## The shop node

- **What it is**: an ESP32-DevKitC V4 + INMP441 running the `shop` firmware (`-e shop`), `profile smoke_shop_v1`, publishing `mic/werkstatt/smoke_alarm` (retained `ON`/`OFF`, `OFF` last-will) to its site-local broker.
- **Address**: the node is on a **dynamic lease right now**, reachable from `opus` over the tailnet as `192.168.1.147` at the time of writing. The intended DHCP reservation is `192.168.1.40` (see the plan's open questions — pin it in `uhlig-it/shop-router/hosts.yml` to stop chasing the address). The current address is whatever `platformio.ini`'s `shop-ota` `upload_port` says.
- **Name resolution does not work** from `opus` (`getent hosts esp32-mic-node-shop` fails), so always use the IP. To re-find it from the shop LAN, read the lease table on the shop router.
- **The broker path**: the shop node publishes on the shop Pi (`shop.lan`, `192.168.1.2`), which bridges `mic/#` to `opus`. So the verdict is also visible on `opus`'s own broker, and Home Assistant (`werkstatt_alarm_fire_notify`) raises the critical push on it.

## Prerequisites (already prepared)

- `opus` runs the detector tooling: static `ffmpeg`/`ffprobe` in `/usr/local/bin`, `mosquitto_sub` on the host, and the credentials in `/etc/mic-smoke-detector.conf` (root-only, `0640`).
- `mic-smoke-test` is installed on `opus` by `playbooks/services/mic-smoke-detector.yml` (`ansible-playbook playbooks/services/mic-smoke-detector.yml` to refresh). It reads the credentials from that file, so no secret is ever typed or logged.
- The shop node is reachable from `opus` (`nc -z 192.168.1.147 554` and `:80` both succeed).

## Test A — live verdict (the actual detection test)

On `opus`, start watching the verdict, then press the workshop detector's test button while it runs:

```sh
ssh opus 'sudo mic-smoke-test watch 60'
```

Expected, within a second or two of the press: a retained `OFF` arrives immediately on subscribe, then

```
mic/werkstatt/smoke_alarm ON
...
mic/werkstatt/smoke_alarm OFF
```

and the iPhone gets the critical "🚨 Feuer/Rauch Werkstatt" push. The `ON`→`OFF` gap is the detector's `clear_hold_s` (2 s) after the press ends.

To confirm the node itself is healthy first:

```sh
ssh opus 'sudo mic-smoke-test node 192.168.1.147'   # -> realm 'esp32-mic-node'
curl -s -u monitor:PASSWORD http://192.168.1.147/metrics | grep -E 'detector|mqtt_connected'
```

(`PASSWORD` is `HTTP_PASSWORD` in the node repo's `include/secrets.h`; on the shop LAN a browser works too — open `http://192.168.1.147/` for the live status page. Expect `esp32_mic_detector_alarm 0`, `esp32_mic_detector_on_fraction 0.0`, `esp32_mic_mqtt_connected 1` at rest, and the on-fraction should climb toward `1.0` during a press.)

## Test B — capture and confirm/retune `smoke_shop_v1`

`smoke_shop_v1`'s thresholds came from a phone recording whose AGC inflated the levels, so they are provisional until confirmed against the real node. Capture 2–3 minutes with 2–3 presses of ~10 s each:

```sh
ssh opus 'sudo mic-smoke-test capture 192.168.1.147 180 /tmp/shop-det.wav'
scp opus:/tmp/shop-det.wav /tmp/
python3 playbooks/services/files/analyze-recording.py /tmp/shop-det.wav --profile smoke_shop_v1
```

The analyser prints the press regions, the tone peaks, and — with `--profile` — how `smoke_shop_v1` does on this recording: every detection, its peak window on-fraction, and any detection outside a press region (a false positive). If the profile needs adjusting, it also prints a candidate to paste into `mic-smoke-detector-profiles.json` **and** into `src/site.h`'s `DETECTOR_PROFILE` (the firmware is the shipped path).

Note nothing holds the shop node's RTSP slot, so the capture needs no service stop (the "stop the detector first" step in the plan is for the home node, whose slot the service holds).

## Pass criteria

- A press drives `esp32_mic_detector_on_fraction` (on `/metrics`) to roughly `1.0` and the MQTT verdict to `ON`, with the critical push arriving; it clears within a few seconds of the press ending.
- Nothing else in the workshop (radio, kettle, tools, speech) trips the detector in the captured minutes — check the analyser's false-positive report.

## Known state / gotchas

- **The home node is currently unreachable from `opus`** (`No route to host` for `192.168.10.40`), so the `mic-smoke-detector` service spins on ffmpeg restarts (`mic_detector_ffmpeg_restarts_total` is huge). That is unrelated to the shop test but pollutes the service log; check it before drawing conclusions from that log.
- The `/tmp` scripts on `opus` (`mon-audio.sh`, `mic-smoke.yaml`, `mqtt_probe.py`) are **stale** — they target the retired Frigate `frigate/living_room/audio/*` path, not the detector's `mic/<room>/smoke_alarm`. `mic-smoke-test` replaces them.
- The server-side detector on `opus` only watches the **home** node; the shop node is not (and need not be) a `detector_input` — the on-device detector owns `mic/werkstatt/smoke_alarm`, and Test B's capture covers the tuning. Adding a shop `detector_input` would be the retired server-side approach, not the shipped one.
- The HA automation `werkstatt_alarm_fire_notify` is suppressed while the `alarm_control_panel.werkstatt` is already `triggered`, so no push there if the shop alarm panel is up.
