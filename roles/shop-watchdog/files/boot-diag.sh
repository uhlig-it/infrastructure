#!/bin/sh
# One-shot boot diagnostics, logged to journald so the reason for a
# crash can be determined on the next visit via:
#   journalctl -b -1 -t shop-boot-diag
{
  echo "== boot diagnostics $(date -Is)"
  uname -a
  uptime
  if command -v vcgencmd >/dev/null 2>&1; then
    echo "throttled:   $(vcgencmd get_throttled 2>/dev/null)"
    echo "temperature: $(vcgencmd measure_temp 2>/dev/null)"
  fi
  echo "--- root filesystem:"
  findmnt -n -o SOURCE,FSTYPE,OPTIONS /
  echo "--- filesystem state:"
  tune2fs -l "$(findmnt -n -o SOURCE /)" 2>/dev/null | grep -E "Filesystem state|error behavior|Mount count" || true
  echo "--- wifi:"
  iw dev wlan0 link 2>/dev/null || echo "wlan0 has no link"
  iw dev wlan0 get power_save 2>/dev/null || true
  echo "--- tailscale:"
  tailscale status --json 2>/dev/null | jq -r '"self online: \(.Self.Online)"' 2>/dev/null || echo "tailscaled not responding"
  echo "--- kernel issues:"
  dmesg 2>/dev/null | grep -iE "mmc0|brcmfmac|under-voltage|failed" | tail -20
} | logger -t shop-boot-diag