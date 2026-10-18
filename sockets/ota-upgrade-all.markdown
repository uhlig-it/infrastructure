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
