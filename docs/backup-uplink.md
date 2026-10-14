# Backup uplink — options (undecided)

**Status: open / undecided.** This captures the discussion after the 2026-09-27 outage. No option has been chosen; it exists so the trade-offs are not lost.

## Why

The site's internet arrives over a single **upstream WLAN**. On 2026-09-27 that uplink vanished and took the whole site off Tailscale for ~17 h — `shop` and `pascal` offline together, the alarm tripped on `bridge_loss` — while the Pi, the LAN and the plugs stayed healthy. It is a single point of failure, and the only thing that detects it is the alarm's 5-minute bridge escalation (see `docs/on-site-recovery.md` and the workspace `docs/verification-log.md`, 2026-09-27).

## Two independent decisions

1. **Who provides the internet** — cellular as the primary (replacing the flaky WLAN), or WLAN-primary with cellular as a backup.
2. **Who runs Tailscale** — the router, or the Pi (which already does).

## Constraint: the current router

The **TL-WDR4300** (OpenWrt, ath79) is ~8 MB flash / 128 MB RAM, 2× USB 2.0, 5× GbE, dual radio. Its only job today is *WLAN-client uplink + AP*. The ~8 MB flash is the binding constraint for anything that needs USB-modem kernel modules (plan on extroot or an Image Builder image). Because the primary uplink is WLAN, the Ethernet WAN port is probably free.

## Options

| # | Approach | What it needs | Trade-offs |
|---|---|---|---|
| **A** | External LTE/5G router on a second WAN port | Dedicate one port as `wan2` (VLAN) + `mwan3` | No flash pressure; backup box owns the modem; extra box + double NAT |
| **B** | USB cellular modem on the WDR4300 | `kmod-usb-net-{qmi-wwan,cdc-mbim,rndis}`, `usb-modeswitch`, `uqmi`/`umbim` + `mwan3` | Single box, cheapest; tight on 8 MB flash (extroot/Image Builder); USB 2.0 power (powered hub) |
| **C** | Phone USB tethering | Android `kmod-usb-net-rndis` / iPhone `kmod-usb-net-ipheth`+`usbmuxd` | Emergency stopgap only |
| **D** | Second WLAN client (other radio as STA) | Nothing extra | Weak: same failure class as what broke |
| **E** | Second fixed line / ISP | A second circuit | Best diversity if the site can get one |

For **B**, prefer HiLink/RNDIS dongles (e.g. Huawei E3372h in "stick" mode) — fewest modules; QMI/MBIM (Quectel EC25 etc.) need more packages.

## The "one box" idea

If the WDR4300 only does *WLAN-client + AP*, then a **4G/5G WiFi router** does both jobs in one box and replaces it — dropping the second WAN, `mwan3`, the double NAT and the flash fight. The only thing given up is the OpenWrt tinkering, on a box that does very little.

Tailscale on the router is useful (manage the router over the tailnet; optional subnet router) but **not required for the Pi** — the Pi already runs `tailscaled`, so any router that provides working internet is enough for the Pi's connectivity. Treat "Tailscale on the box" as a convenience, not a hard requirement.

| Box | OS | Tailscale | Notes |
|---|---|---|---|
| GL.iNet (GL-X300B 4G, GL-X3000 "Spitz AX" 5G, GL-X750, GL-E750) | OpenWrt-based | Yes, in the GL.iNet UI | Closest to the current setup; keeps OpenWrt; multi-WAN failover in the UI |
| Teltonika RUT (RUT241/956/XR) | RUTOS | Yes (recent firmware) | Industrial, robust, pricier; not OpenWrt |
| OpenWrt 4G/5G device (Cudy LT, Zbt, …) | OpenWrt | Yes (`tailscale` package) | More flash than the WDR4300, so it fits |
| MikroTik (Chateau 5G, LtAP) | RouterOS | Only via container (DIY) | Powerful, not turnkey |
| Huawei / Zyxel / TP-Link / Netgear 4G/5G | vendor | No | Fine as a dumb modem, not as the Tailscale node |

## The caveat that does not go away

A Tailscale-capable cellular router does **not** fix the camera. The opus↔shop video path needs a **direct** Tailscale connection (the DERP limitation from 2026-09-25), and carriers' **CGNAT** usually will not give the port-preserving mapping that `PORT=41642` relies on. Over a cellular uplink the shop's Tailscale node — router or Pi — will very likely be **relayed**, and the H.264 stream degrades and trips `camera_loss`. The MQTT bridge, supervision and remote management all work fine over CGNAT (outbound); the **video** is the casualty.

So the goal decides the answer:

* **Supervision / management / alarm continuity** → a single 4G/5G router is a clean win; the bridge stays up and `bridge_loss` never trips.
* **Full video continuity during an uplink outage** → cellular will not deliver it; a **wired second ISP** (Option E) is needed, with cellular only as a management fallback.

## Open questions (to decide)

* Goal: supervision/management continuity, or full video continuity?
* Replace the WDR4300, or keep it and augment?
* Cellular as primary, or WLAN-primary with cellular backup?
* Tailscale on the router, or keep it on the Pi?
* Which model, SIM plan, antenna/signal at the site, and data budget (if the camera ever rides the backup)?

## See also

* `docs/on-site-recovery.md` — the on-site procedure and the telemetry-gap TODO.
* `README.markdown` — current state and "Recommended Next Steps".
* workspace `docs/verification-log.md` — the 2026-09-27 incident.
* workspace `DESIGN.md` — the Tailscale direct-path requirement.
