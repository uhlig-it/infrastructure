#!/bin/sh
# shop-health: external health sentinel for shop (the workshop Pi).
#
# Records shop's reachability as a Prometheus metric through the node_exporter
# textfile collector and logs state transitions to the journal. Deliberately
# does NOT alert: there is no remote power control for shop and no one is going
# to walk over in the middle of the night, so a page has no action attached.
# Query (or later alert on) the metric instead.
#
# Modes:
#   lan       -- ping shop on its LAN address; run this on pascal
#                (same 192.168.1.0/24 network as shop)
#   tailscale -- check the shop node in the local tailnet; run this
#                anywhere Tailscale is installed
#
# Every run writes one textfile per mode into SHOP_HEALTH_TEXTFILE_DIR
# (default /var/lib/node_exporter, the textfile directory of pascal's
# node_exporter): shop_health_{mode}.prom with
#
#   shop_health{mode="..."} 1|0
#   shop_health_last_update_seconds{mode="..."} <unix time>
#
# The probe result is recorded as-is (no smoothing): a single failed ping is
# one minute of 0 in the series. A dead sentinel host shows up as the
# timestamp gauge going stale while the value stays at its last state.
#
# Runs once per minute via a systemd timer on pascal (shop-health.service /
# shop-health.timer in the pascal ansible repo), so the probe also survives
# reboots of pascal itself: Persistent=true fires it once after boot.
#
# On another host, install the same unit pair and point
# SHOP_HEALTH_TEXTFILE_DIR at that host's own textfile directory.

MODE="${1:-tailscale}"
LAN_IP="192.168.1.2"
TS_NODE="shop"
TEXTFILE_DIR="${SHOP_HEALTH_TEXTFILE_DIR:-/var/lib/node_exporter}"
STATE_DIR="${SHOP_HEALTH_STATE:-/tmp/shop-health}"
STATE="$STATE_DIR/$MODE"

mkdir -p "$TEXTFILE_DIR" "$STATE_DIR" 2>/dev/null || exit 1
[ -x /usr/bin/jq ] || [ -x /usr/local/bin/jq ] || [ "$MODE" = lan ] || {
  echo "jq is required for mode '$MODE'" >&2
  exit 2
}

check() {
  case "$MODE" in
    lan) ping -c 1 -W 2 "$LAN_IP" >/dev/null 2>&1 ;;
    tailscale)
      tailscale status --json 2>/dev/null |
        jq -e --arg h "$TS_NODE" '.Peer | to_entries[] | select(.value.HostName == $h) | .value.Online' >/dev/null 2>&1 ;;
    *) echo "usage: $0 lan|tailscale" >&2; exit 2 ;;
  esac
}

# Atomically rewrite the textfile so node_exporter never sees a partial file.
record() {
  tmp="$TEXTFILE_DIR/.shop_health_$MODE.$$"
  {
    echo "# HELP shop_health Whether the shop Pi answered a probe from this host."
    echo "# TYPE shop_health gauge"
    echo "shop_health{mode=\"$MODE\"} $1"
    echo "# HELP shop_health_last_update_seconds Unix time of the last probe."
    echo "# TYPE shop_health_last_update_seconds gauge"
    echo "shop_health_last_update_seconds{mode=\"$MODE\"} $(date +%s)"
  } > "$tmp" && mv "$tmp" "$TEXTFILE_DIR/shop_health_$MODE.prom"
}

consecutive() { cat "$STATE" 2>/dev/null || echo 0; }

if check "$MODE"; then
  [ "$(consecutive)" -gt 0 ] &&
    logger -t shop-health "shop reachable again via $MODE after $(( $(consecutive) * 1 ))..."
  rm -f "$STATE"
  record 1
  exit 0
fi

N=$(( $(consecutive) + 1 ))
echo "$N" > "$STATE"
record 0
logger -t shop-health "shop unreachable via $MODE ($N consecutive checks)"

exit 0