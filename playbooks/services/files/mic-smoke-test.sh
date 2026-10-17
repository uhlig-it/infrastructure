#!/bin/sh
# Smoke-alarm test helper for the INMP441 mic nodes.
#
# Installed to /usr/local/bin/mic-smoke-test on opus by
# playbooks/services/mic-smoke-detector.yml; run it there as root, because it
# reads the detector's 0640 environment file for the broker and RTSP
# credentials. Keeping the secrets in that file (rather than on the command
# line) is deliberate: nothing sensitive lands in shell history or logs.
#
#   mic-smoke-test watch   [seconds] [topic]              # live verdict
#   mic-smoke-test capture <node-ip> <seconds> [out.wav]  # audio for tuning
#   mic-smoke-test node    <node-ip>                      # is that a mic node?
#
# Defaults target the shop node: it publishes mic/werkstatt/smoke_alarm on its
# site-local broker (shop.lan), which bridges mic/# to opus, so the verdict is
# visible on opus's own broker too. See docs/smoke-detection-shop-test.md.
#
# Credentials come from MIC_SMOKE_CONF (default /etc/mic-smoke-detector.conf);
# any of MQTT_* / MIC_RTSP_* may instead be preset in the environment.
set -eu

conf=${MIC_SMOKE_CONF:-/etc/mic-smoke-detector.conf}
if [ -r "$conf" ]; then
  # shellcheck disable=SC1090
  . "$conf"
fi

cmd=${1:-watch}
case "$cmd" in
  watch)
    secs=${2:-30}
    topic=${3:-mic/werkstatt/smoke_alarm}
    : "${MQTT_HOST:=127.0.0.1}" "${MQTT_PORT:=1883}"
    echo "watching '$topic' for ${secs}s on ${MQTT_HOST}:${MQTT_PORT} - press the detector now"
    exec mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" \
      -u "${MQTT_USER:?}" -P "${MQTT_PASS:?}" -v -q 1 -W "$secs" -t "$topic"
    ;;
  capture)
    ip=${2:?usage: mic-smoke-test capture <node-ip> <seconds> [out.wav]}
    secs=${3:-60}
    out=${4:-/tmp/smoke-capture.wav}
    echo "capturing ${secs}s from rtsp://${ip}:554/ -> $out"
    exec ffmpeg -hide_banner -nostdin -loglevel warning -rtsp_transport tcp \
      -i "rtsp://${MIC_RTSP_USER:?}:${MIC_RTSP_PASSWORD:?}@${ip}:554/" \
      -t "$secs" -ac 1 -ar 16000 -y "$out"
    ;;
  node)
    ip=${2:?usage: mic-smoke-test node <node-ip>}
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 4 "http://${ip}/metrics" || true)
    realm=$(curl -s -D - -o /dev/null -m 4 "http://${ip}/" \
      | sed -n 's/^WWW-Authenticate: Basic realm="\(.*\)".*/\1/p' | tr -d '\r')
    echo "$ip: http ${code:-000}, realm '${realm:-none}'"
    if [ "${realm:-}" = "esp32-mic-node" ]; then
      echo "  -> that is a mic node"
    else
      echo "  -> not a mic node (or not up)"
    fi
    ;;
  *)
    echo "usage: $0 watch [secs] [topic] | capture <ip> <secs> [out.wav] | node <ip>" >&2
    exit 2
    ;;
esac
