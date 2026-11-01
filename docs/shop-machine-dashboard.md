# VMUI Predashboards (shop machine reliability)

`shop-machine.json` is a [VMUI predefined dashboard](https://github.com/VictoriaMetrics/VictoriaMetrics/tree/master/app/vmui/packages/vmui/public/dashboards) for the shop machine (the Raspberry Pi in the workshop) and its reliability infrastructure: the `shop_health` LAN probe that runs on `pascal`, the node scrapes of both Pis, and the Tasmota power plugs that monitor the workshop mains.

It is built from the reliability metrics added in September 2026 (see the `shop` repo README): `shop_health{mode="lan"}` / `shop_health_last_update_seconds` written every minute by `monitor/shop-health.sh` on `pascal` (systemd timer, node_exporter textfile collector), plus `up` and node_exporter series for `shop:9100` and `pascal:9100` (scrape target added in `uhlig-it/metrics` @2fa58b5).

## At a glance

The first row is the whole point: three 0/1-style panels that answer "what works, what not" without reading anything.

1. **Shop reachable (LAN probe)** – shop answered a ping from pascal in the last minute. Sustained 0 = outage; disappearing line = probe pipeline broken.
2. **Node scrape status** – `up` for both Pis. shop=0 means no data from the machine at all.
3. **Probe freshness** – seconds since pascal last wrote the probe result. Growing beyond ~2 min means the probe itself (pascal) is dead and `shop_health` is stale.

Rule of thumb: shop below pascal = the shop box itself is the problem (WiFi wedge, SD card, PSU); shop and pascal both down = WiFi or mains. That distinction is what made the 09-11 -> 09-18 post-mortem conclusive.

## Deploy

VictoriaMetrics only serves these dashboards if started with `-vmui.customDashboardsPath` pointing at a directory of dashboard JSON files (one dashboard per file, `*.json`):

```command
victoria-metrics -promscrape.config=... -vmui.customDashboardsPath=/usr/local/share/victoria-metrics/vmui-dashboards
```

The flag is read at startup, so the service must be restarted after changing it (data is not lost – stop with SIGINT, then start again). How to do that depends on the deployment (the VM instance itself is not managed by this repository; the `metrics` repo only holds the Grafana and backup roles). After the restart, the dashboard appears in the dropdown at the top left of [vmui](https://metrics.tailnet-204f.ts.net/vmui) under "Dashboards" -> "Shop machine reliability".

Verify the server side is serving it: `curl -s -u suhlig:$PASSWORD 'https://metrics.tailnet-204f.ts.net/vmui/custom-dashboards'` should return the dashboard JSON in `dashboardsSettings`.

## File format

The schema is the VMUI predefined-dashboard format (see `dashboardSettings`/`panelSettings` in `app/vmselect/vmui.go`): top-level `title` and `rows`, each row has `title` and `panels`. Panels support `title`, `description` (markdown), `expr` (array of MetricsQL queries plotted on one chart), `alias` (display name per query), `unit` (suffix shown next to values), `showLegend` and `width` (grid columns out of 12). All panels render as line charts – there are no stat/gauge panel types in this format, so "at a glance" signals are 0/1 series.

## Metrics semantics worth knowing

* `shop_health` is recorded as-is, no smoothing: one failed ping = one minute of 0. Do not alert on a single 0, do alert (or look) at 3+ consecutive zeros.
* `shop_health_last_update_seconds` is a timestamp gauge; staleness detection is done by comparing it with `time()`, not by the series going stale.
* Reboots show up in `time() - node_boot_time_seconds`, not in the probe (a boot within the 1-minute probe interval can stay green).
* Known gaps: shop's own WiFi signal strength and `vcgencmd get_throttled` are not exported as metrics yet (the `shop-watchdog` role is committed but not deployed), and `shop:9103` currently exposes no custom metrics. The dashboard will light up for those once they exist.