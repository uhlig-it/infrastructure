#!/bin/sh
# shop-watchdog: keeps the remote shop Pi reachable by itself.
#
# Runs every minute via shop-watchdog.timer and escalates:
#   1. The filesystem must stay writable -- an SD card going read-only
#      is the classic reason this Pi loses the network while the kernel
#      keeps running (the hardware watchdog cannot help then).
#   2. The gateway must answer a ping.
#   3. Tailscale must report itself online.
#
# Three consecutive failures restart the wireless stack (or tailscaled);
# three failed escalation rounds trigger a reboot, since a reboot has
# proven to restore the device.

GATEWAY="192.168.1.1"
IFACE="wlan0"
LIMIT=3
LOG_FILE="/var/lib/shop-watchdog/log"
RW_PROBE="/var/lib/shop-watchdog/rw-probe"
STATE_DIR="/run/shop-watchdog"   # tmpfs: counters survive no reboot
NET_STATE="$STATE_DIR/net-failures"
TS_STATE="$STATE_DIR/ts-failures"
BOUNCE_STATE="$STATE_DIR/bounces"

umask 022
mkdir -p "$STATE_DIR" || exit 0

log() { logger -t shop-watchdog "$@"; }

# Persist evidence on disk (survives a hard power cut; fails silently
# once the filesystem is read-only, which is itself diagnostic).
persist() {
  if [ "$(wc -l < "$LOG_FILE" 2>/dev/null || echo 0)" -ge 1500 ]; then
    mv "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || rm -f "$LOG_FILE"
  fi
  echo "$(date -Is) $*" >> "$LOG_FILE" 2>/dev/null || true
}

note() { log "$@"; persist "$@"; }

file_count() { [ -f "$1" ] && cat "$1" || echo 0; }
bump() { echo "$(($(file_count "$1") + 1))" > "$1"; }
reset_count() { rm -f "$1"; }

tailscale_online() {
  command -v tailscale >/dev/null 2>&1 || return 0
  if command -v jq >/dev/null 2>&1; then
    tailscale status --json 2>/dev/null | jq -e '.Self.Online == true' >/dev/null 2>&1
  else
    tailscale status --json 2>/dev/null | grep -q '"Online":true'
  fi
}

# 1. Filesystem must be writable.
if ! touch "$RW_PROBE" 2>/dev/null; then
  note "filesystem is read-only; rebooting"
  /sbin/reboot
  exit 0
fi

# 2. Gateway must answer.
if ping -c 1 -W 1 "$GATEWAY" >/dev/null 2>&1; then
  reset_count "$NET_STATE"
elif [ "$(file_count "$NET_STATE")" -ge "$LIMIT" ]; then
  note "gateway $GATEWAY unreachable for $LIMIT checks; restarting wireless"
  # This host is managed by NetworkManager (there is no dhcpcd); bounce the
  # device so it re-associates and re-runs DHCP. Fall back to a raw link
  # bounce if nmcli is unavailable.
  if command -v nmcli >/dev/null 2>&1; then
    nmcli device disconnect "$IFACE" >/dev/null 2>&1 || true
    sleep 2
    nmcli device connect "$IFACE" >/dev/null 2>&1 || true
  else
    ip link set "$IFACE" down 2>/dev/null || true
    sleep 2
    ip link set "$IFACE" up 2>/dev/null || true
  fi
  reset_count "$NET_STATE"
  reset_count "$TS_STATE"
  bump "$BOUNCE_STATE"
else
  bump "$NET_STATE"
  reset_count "$TS_STATE"
fi

# 3. Tailscale must be online (only relevant while the gateway answers).
if ping -c 1 -W 1 "$GATEWAY" >/dev/null 2>&1 && ! tailscale_online; then
  if [ "$(file_count "$TS_STATE")" -ge "$LIMIT" ]; then
    note "tailscale offline for $LIMIT checks; restarting tailscaled"
    systemctl restart tailscaled >/dev/null 2>&1 || true
    reset_count "$TS_STATE"
    bump "$BOUNCE_STATE"
  else
    bump "$TS_STATE"
  fi
else
  reset_count "$TS_STATE"
fi

# If both checks pass again, reset the escalation counter.
if ping -c 1 -W 1 "$GATEWAY" >/dev/null 2>&1 && tailscale_online \
    && [ "$(file_count "$BOUNCE_STATE")" -gt 0 ]; then
  reset_count "$BOUNCE_STATE"
  note "all checks passed again"
fi

# Give up on restarting: a reboot reliably restores the device.
if [ "$(file_count "$BOUNCE_STATE")" -ge 3 ]; then
  note "still unhealthy after 3 restarts; rebooting"
  persist "rebooting now"
  /sbin/reboot
fi

# Warn once per boot about power/thermal problems; a weak PSU is a
# common cause of flaky Raspberry Pi networking.
if command -v vcgencmd >/dev/null 2>&1 && [ ! -f "$STATE_DIR/throttle-warned" ]; then
  THROTTLED="$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2 | tr -d '\r')"
  case "$THROTTLED" in
    ""|0x0|0) ;;
    *) note "vcgencmd get_throttled=$THROTTLED: undervoltage or thermal throttling detected"
       touch "$STATE_DIR/throttle-warned" ;;
  esac
fi

exit 0