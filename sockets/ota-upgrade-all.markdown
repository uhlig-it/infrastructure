# OTA Updates via MQTT

# Watch for OTA status updates

```command
$ mosquitto_sub --url "mqtts://$MQTT_USER:$MQTT_USER_PASSWORD@mqtt.uhlig.it/+/+/stat/UPGRADE"
```

# Set the OTA URL for all sockets

```command
$ mosquitto_pub \
    --url "mqtts://$MQTT_USER:$MQTT_USER_PASSWORD@mqtt.uhlig.it/werkstatt/sockets/cmnd/otaurl" \
    --message http://ota.tasmota.com/tasmota/release/tasmota.bin
```

# Upgrade all sockets

```command
$ mosquitto_pub \
  --url "mqtts://$MQTT_USER:$MQTT_USER_PASSWORD@mqtt.uhlig.it/werkstatt/sockets/cmnd/upgrade" \
  --message 1
```

# After the upgrade: repair the retained LWT

Tasmota publishes `tele/<topic>/LWT` as a retained availability topic. During an OTA the old and new firmware briefly share the same MQTT client ID, and the resulting session takeover can leave the old connection's retained `Offline` behind after the new firmware has already connected. Home Assistant then shows every entity unavailable even though the device is healthy.

After a batch upgrade, republish `Online` for every plug:

```command
$ for i in 0 1 2 3 4 5 6 7; do
    mosquitto_pub \
      --url "mqtts://$MQTT_USER:$MQTT_USER_PASSWORD@mqtt.uhlig.it/home/nousA8T-$i/tele/LWT" \
      --retain \
      --message Online
  done
```

Use `Online` with a capital O: Home Assistant compares the value advertised in Tasmota's discovery config literally, so the lowercase `online` used by other guards is rejected. `Restart 1` on each device also republishes `Online` on boot, but it briefly interrupts the connected load. Verify with `mosquitto_sub ... -t 'home/+/tele/LWT'` and in Home Assistant. The broker's `mosquitto` role includes guard entries for these topics (see `roles/mosquitto/defaults/main.yml`, `mosquitto_lwt_guards`); after the next `playbooks/machines/opus.yml` run it republishes `Online` every two minutes while the plug's web UI answers, so a stale `Offline` self-heals.
