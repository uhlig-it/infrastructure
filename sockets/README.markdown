# Workshop Sockets

> TODO Set OtaUrl to http://shop.lan:3333/tasmota.bin.gz (workshop) or http://192.168.10.164:3333/tasmota32-nousa8t.bin (home)

# Gosund Smart Plug with Tasmota Firmware

* Based on [this article](https://blog.koehntopp.info/2020/05/20/gosund-and-tasmota.html)
* Time zone and DST from [Tasmota Timezone Table](https://tasmota.github.io/docs/Commands/timezone_table)

1. Upgrade via OTA. Accept all the defaults.
1. Set the device template. Via web console, go to _Configuration_ / _Other_, and enter this template string:

  ```
  {"NAME":"SP111 v1.1","GPIO":[56,0,158,0,132,134,0,0,131,17,0,21,0],"FLAG":0,"BASE":45}
  ```

1. Change to the directory of the location (`home` or `werkstatt`)
1. Run `../print-config gosund-3 ../common.commands common.commands groups/gosunds.commands | pbcopy` (replace `gosund-3` with the proper device name) to generate the [`commands`](https://tasmota.github.io/docs/Commands).
1. Paste the result into the console of the Gosund device, or do it using MQTT (if configured already):

    ```command
    $ mosquitto_pub \
      --host "$MQTT_HOST" \
      --port "$MQTT_PORT" \
      --username "$MQTT_USER" \
      --pw "$MQTT_USER_PASSWORD" \
      --topic werkstatt/irlight/cmnd/backlog \
      --message "$(./print-config irlight common.commands irlight.commands)"
    ```

  assuming that `werkstatt/irlight/cmnd/backlog` is the topic where the device under configuration accepts commands.

# IR Light

A Sonoff will control the IR light to turn it off during the day. It's also configured just like the Gosunds.

# Calibration

Nous A8T come without calibration. Compare to another device and set

```
backlog VoltageSet 227; CurrentSet 42
```

# Device Registry

TODO A better script (or ansible playbook?) would be called like

```command
$ print-commands irlight --location home --include sonoffs
```

where `irlight` is the mandatory name, `common.commands` would always be included (if present), and all groups would be included as per `--include` statements.

A properly encrypted `secrets.yml` could hold the variables that need to be protected.

Live fleet as of 2026-10-18:

```command
# Home fleet (9 devices): nousA8T-0..7 (group nous-A8T) and gosund-3 (group gosunds)
( cd home;      ../print-config nousA8T-0 ../common.commands common.commands groups/nous-A8T.commands )
( cd home;      ../print-config nousA8T-1 ../common.commands common.commands groups/nous-A8T.commands )
( cd home;      ../print-config nousA8T-2 ../common.commands common.commands groups/nous-A8T.commands )
( cd home;      ../print-config nousA8T-3 ../common.commands common.commands groups/nous-A8T.commands )
( cd home;      ../print-config nousA8T-4 ../common.commands common.commands groups/nous-A8T.commands )
( cd home;      ../print-config nousA8T-5 ../common.commands common.commands groups/nous-A8T.commands )
( cd home;      ../print-config nousA8T-6 ../common.commands common.commands groups/nous-A8T.commands )
( cd home;      ../print-config nousA8T-7 ../common.commands common.commands groups/nous-A8T.commands )
( cd home;      ../print-config gosund-3 ../common.commands common.commands groups/gosunds.commands )

# Workshop fleet (4 devices): gosund-0..2 (group gosunds) and irlight (device file)
( cd werkstatt; ../print-config gosund-0 ../common.commands common.commands groups/gosunds.commands )
( cd werkstatt; ../print-config gosund-1 ../common.commands common.commands groups/gosunds.commands )
( cd werkstatt; ../print-config gosund-2 ../common.commands common.commands groups/gosunds.commands )
( cd werkstatt; ../print-config irlight  ../common.commands common.commands devices/irlight.commands )
```

The `sonoff-*` entries that used to be listed here are retired: the home sonoffs are offline and replaced by the `nousA8T-*` generation, and the workshop sonoffs (`192.168.1.20-.22`) do not answer either. Their group and device command files can be dropped once the hardware is gone.

`nousA8T-6` and `nousA8T-7` were powered up on 2026-10-18 and still need the full `print-config` push (to set `Topic`/`FullTopic`) plus the `tasmota32-nousa8t.bin` OTA; `nousA8T-0..5` already match this config.


TODO The new project `tasmota-discover` will show which devices are online.

# Neue Geräte

- Anstecken
- WLAN verbinden (reboot)
- Firmware Update (reboot)
- calibrate power

  backlog VoltageSet 228; CurrentSet 44

- batch config via console (reboot)

in HA

- Rename to "Steckdose Something"
- Add label NousA8T
