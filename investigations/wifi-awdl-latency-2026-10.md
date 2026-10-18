# WiFi Woes — October 2026 Diagnosis

## Symptom

For the last couple of days, YouTube and Instagram videos on WiFi devices (Mac "lima", iPad-mini, iPhones) start playing and then stall after ~10 seconds, despite a 1 Gbps fiber line. Restarting the UniFi APs used to help temporarily, but the problem comes back.

## Status (2026-10-18): root cause identified and mitigated

Root cause: Apple Wireless Direct Link (AWDL), which periodically interrupts the radio (bursts every ~3.6–4.6 s). Mitigated on Mac "lima" (AWDL down + known-networks cleanup, see Fix applied); the Mac-side fix also quieted the iPad (AWDL cluster sync). Not yet applied to the iPhones; the Mac fix resets on reboot. The APs/gateway/WAN were never at fault.

## Infrastructure

- WAN: Telekom fiber via Speedport Smart 4 gateway (192.168.10.1), 1.1 Gbit/s down / 549.8 Mbit/s up, external modem. The gateway's own WiFi (2.4/5 GHz) is OFF.
- WiFi is provided by 3 UniFi AC-Lite APs (U7LT, fw 6.8.2), all wired GbE: Wohnzimmer (192.168.10.169, ~21 clients), Kinderzimmer (192.168.10.194, 3 clients), Arbeitszimmer (192.168.10.197, 3 clients).
- SSIDs: `uhlig.wlan` (26 clients, all phones/laptops/IoT mixed), `uhlig.iot` (1 client only).
- Monitoring: VictoriaMetrics at https://metrics.tailnet-204f.ts.net (speedtest + node_exporter on opus, wired at 192.168.10.164). UniFi console at https://unifi.tailnet-204f.ts.net.

## What has been verified HEALTHY

- WAN: opus (wired) speedtest ~936 Mbps down / ~4 ms ping, stable over the last 7 days (VictoriaMetrics). Gateway shows WAN up since 2026-09-25, 1.1 Gbit/s down / 549.8 Mbit/s up.
- Gateway: wired ping 0.3–0.8 ms, 0% packet loss, no bufferbloat even under full load.

## What is actually wrong (WiFi layer)

- Mac "lima" on AP Wohnzimmer 5 GHz ch36: 400 Mbps PHY, only ~2–4% TX retries, good signal (-50 to -56 dBm) — yet real throughput is only ~131 Mbps and there are periodic latency spikes.
- Ping Mac → gateway: avg 9–19 ms with periodic spikes to 30–130 ms (one outlier at ~1061 ms) roughly every 1–3 seconds. The iPad-mini shows the same pattern.
- Both problem devices are on AP Wohnzimmer; the other two APs have very few clients and look normal.

## Hypotheses that have been RULED OUT (with evidence)

- WAN / gateway issue — disproven: wired path is perfect end to end.
- AP CPU overload — disproven: CPU stayed flat at 29.6% even while pulling a 200 MB × 3 download over 5 GHz.
- 2.4 GHz radio retry storm (48% TX retries from ~16 IoT/ESP clients) causing the 5 GHz spikes — disproven: disabling the 2.4 GHz radio on Wohnzimmer did NOT remove the 5 GHz latency spikes.
- AP background RF scanning — ruled out: RF scan cron is `0 3 * * *` (only daily at 3 AM).
- Channel / channel width — changing to 5 GHz ch36/44/100 at 40 MHz and 2.4 GHz ch1/6/11 at 20 MHz produced no improvement.

## Root cause (confirmed 2026-10-18): AWDL on Apple devices

The cause is Apple Wireless Direct Link (AWDL) — the background mechanism behind AirDrop/Handoff/Continuity — which periodically switches the radio off the main channel to advertise, producing latency bursts every ~3.6–4.6 s. Confirmed on Mac "lima": with the AWDL interface down (`sudo ifconfig awdl0 down`) the periodic spikes disappear entirely (0/300 ≥20 ms, avg 3.7 ms, max 17 ms) and the Mac performs like the Linux client. Network-join scanning was a minor secondary contributor only: raising `WifiJoinScanInterval` cut the spike count from 62/300 to 38/300 before AWDL was identified.

The Mac's AWDL also affects the iPad: AWDL devices in proximity synchronize their radio sync-windows (AWDL cluster sync), so an actively advertising Mac pulls nearby Apple devices into its cadence. Measured on the iPad-mini: paired latency bursts every ~3–6 s and avg 8.3 ms (worst 373 ms) while the Mac's AWDL was up, vs clean (avg ~3 ms, no bursts) with it down — verified twice (A/B/A); the only residual spike was the first-ping artifact.

## Config changes applied so far (all live)

- Wohnzimmer: 5 GHz ch36 / 40 MHz, 2.4 GHz ch1 / 20 MHz.
- Kinderzimmer: 5 GHz ch100 / 40 MHz (DFS), 2.4 GHz ch11 / 20 MHz.
- Arbeitszimmer: 5 GHz ch44 / 40 MHz, 2.4 GHz ch6 / 20 MHz.
- Note: the 2.4 GHz radio on Wohnzimmer was briefly disabled for the test above and has been re-enabled; service is back to normal.

## Result: Linux test device — points to client side (root cause: AWDL)

Test device: `tape` (Pop!_OS 24.04, x86_64, Broadcom BCM43602 via `brcmfmac`), connected to `uhlig.wlan` and pinned to AP Wohnzimmer 5 GHz ch36 (BSSID `B4:FB:E4:C8:E9:98`, ~-46 to -51 dBm) — same AP, radio, and channel as the Mac/iPad. The BSSID pin was reverted after the test.

How it was tested (2026-10-18): 300 pings to the gateway at 0.2 s intervals while downloading from proof.ovh.net — once capped at ~16 Mbps (video-like, 75 s) and once uncapped (60 s). The identical runs were repeated on the Mac "lima" for comparison.

Result:
- tape at ~16 Mbps: max RTT 18.4 ms, 0/300 spikes ≥30 ms, 0 TX retries, steady throughput.
- tape uncapped: 246 Mbps sustained (1 GiB in 60 s); spikes only under airtime saturation (91% of the 270 Mbps PHY).
- Mac at ~16 Mbps: 62/300 spikes ≥30 ms, worst 330 ms — the periodic spikes are unchanged.
- Mac uncapped: 195 Mbps sustained, 102/300 spikes ≥30 ms at only ~49% of the 400 Mbps PHY.

Interpretation: Linux on the same AP/radio/channel is clean at video bitrates while the Mac still spikes — the problem is client-side on the Apple devices, not the AP or the RF environment. Follow-up isolation on the Mac identified the exact mechanism: AWDL (see Root cause above); background join scanning was a secondary factor.

Caveat: the BCM43602/`brcmfmac` driver behaved well in this test; if anything, the Linux client proved more robust than the Apple clients.

## Fix applied on Mac "lima" (2026-10-18)

- Known Networks cleanup: removed `uhlig.gast`, `uhlig.iot`, and neighbor SSIDs; kept only `uhlig.wlan` (fewer preferred networks to probe).
- Raised the join-scan interval (secondary factor): `sudo /usr/bin/defaults write /Library/Preferences/com.apple.AirPort.wifi WifiJoinScanInterval 300` (revert: `sudo defaults delete /Library/Preferences/com.apple.AirPort.wifi WifiJoinScanInterval`). Halved the spike count (62 → 38) but did not fix it.
- AWDL disabled (the actual fix): `sudo ifconfig awdl0 down`. Tradeoff: AirDrop, Handoff, Continuity, and AirPlay-to-Mac stop working until re-enabled (`sudo ifconfig awdl0 up`) or until reboot (awdl0 comes back automatically). Making it persist across reboots requires a launchd agent.
- iPad-mini: its paired bursts (every ~3–6 s, worst 373 ms) were measured only while the Mac's AWDL was up and disappeared as soon as it went down — AWDL cluster sync, so the Mac-side fix helps the iPad too. iOS/iPadOS has no user-facing AWDL toggle; apply known-networks hygiene plus Auto-Join Hotspot = Never and Ask to Join Networks = Off. iPhones not yet verified.

## Key data points

- opus wired speedtest (7-day): download ~936 Mbps, ping ~4 ms — WAN is not the problem.
- Gateway wired ping: 0.3–0.8 ms, 0% loss.
- Mac over WiFi: 400 Mbps PHY, 2–4% retries, -50 to -56 dBm signal, ~131 Mbps throughput, ping spikes to 30–130 ms every 1–3 s.
- AP Wohnzimmer 2.4 GHz: 16–48% TX retry rate (retry storm, but proven NOT the cause of the 5 GHz spikes).
- AP Wohnzimmer CPU: 29.6% under full load (not overloaded).
- AP Wohnzimmer 5 GHz: 2–7% retries, channel utilization 11% idle → 56% during download.
- Linux test (tape, Wohnzimmer 5G ch36, ~16 Mbps): max RTT 18.4 ms, 0/300 spikes ≥30 ms, 0 retries — clean.
- Mac (same AP/radio/channel, ~16 Mbps): 62/300 spikes ≥30 ms, worst 330 ms — unchanged.
- Mac uncapped: 195 Mbps sustained, 102/300 spikes; tape uncapped: 246 Mbps sustained, spikes only under saturation.
- Mac after fix (known-networks cleanup + `WifiJoinScanInterval` 300 + `awdl0` down, ~16 Mbps): 0/300 spikes ≥20 ms, max RTT 17.3 ms, avg 3.7 ms — matches the Linux client.
- iPad link (opus → iPad-mini): clean avg ~3 ms with Mac AWDL down; paired bursts every ~3–6 s, worst 373 ms, with Mac AWDL up — the Mac's AWDL drives the cluster.
