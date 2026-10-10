# INMP441 ESP32 node firmware

The RTSP streamer for the ESP32-DevKitC V4 sensor node described in [`../../docs/smoke-detection-plan.md`](../../docs/smoke-detection-plan.md). The node reads the INMP441 over I2S and serves it as an **audio-only RTSP** stream that Frigate's go2rtc pulls over the tailnet.

## Selected: `rjsachse/ESP32-RTSPServer`

An Arduino library (MIT, actively maintained) that runs an RTSP server on a **generic ESP32** and can serve **audio only** from I2S. We build a small sketch around it.

Why it fits:

- **Board:** generic ESP32 (Arduino core ≥ 3.1.1), so it runs on the decided ESP32-DevKitC V4 (ESP32-WROOM-32) — no board-specific port.
- **Auth:** `setCredentials(user, pass)` turns on **RTSP Basic auth** (`401` + `WWW-Authenticate: Basic realm="ESP32"`), matching the plan's "HTTP Basic on the stream" decision.
- **Audio-only:** the `RTSPServer::AUDIO_ONLY` transport plus `sendRTSPAudio(int16_t*, len)`.
- **Protocol:** RTSP with an **`L16`** PCM audio track (`a=rtpmap:97 L16/<rate>/1`), mono, configurable rate — decodable by ffmpeg, go2rtc and Frigate.
- Multiple clients; unicast UDP, TCP and HTTP-tunnel transports.

Two practical notes from the source:

- Advertise on the **default RTSP port 554**: the server's `Content-Base` is hardcoded to `:554`, so a different port makes clients build the wrong `SETUP` URL. 554 is fine on the ESP32.
- The track is raw **PCM `L16`** (big-endian), so expect ~32 kB/s at 16 kHz mono — negligible over the tailnet.

### Alternatives considered

- **[`Sukecz/esp32-birdnet-mic`](https://github.com/Sukecz/esp32-birdnet-mic)** — the most turnkey option: purpose-built RTSP I2S mic firmware with a Web UI, MQTT / Home Assistant discovery, auto-recovery, thermal protection and a stream schedule. But it builds only for **Seeed XIAO ESP32-C3/S3/C5/C6** (no WROOM-32 target) and has **no stream or Web authentication**. Use it only if we switch the node to a XIAO C6, or accept an unauthenticated stream.
- **[`cfp02/esp32_rtsp`](https://github.com/cfp02/esp32_rtsp)** — exactly our ESP32 + INMP441 combination, but unmaintained and undocumented (0 stars). Not a foundation.
- **A minimal RTSP server written from scratch** — unnecessary given the library above.

## Configuration

- **Wiring:** INMP441 → ESP32-DevKitC V4, per [`inmp441-esp32.svg`](inmp441-esp32.svg): `VDD` 3V3, `GND` GND, `L/R` GND (left channel), `BCLK` IO26, `LRCLK` IO25, `SD` IO33.
- **I2S:** Philips / standard mode, 16 kHz, mono, 16-bit.
- **RTSP:** audio-only, port 554, Basic auth.
- **URL:** `rtsp://<node-ip>:554/` (the audio track is `a=control:audio`).

## Sample sketch

A starting point — install the `ESP32-RTSPServer` library, build for **ESP32 Dev Module**, and prove it on the bench before wiring it into Frigate:

```cpp
#include <WiFi.h>
#include <ESP32-RTSPServer.h>
#include <ESP_I2S.h>

const char *ssid     = "workshop-iot";
const char *password = "**********";
const char *rtspUser = "frigate";
const char *rtspPass = "**********";

const int    I2S_SCK   = 26;      // BCLK
const int    I2S_WS    = 25;      // LRCLK
const int    I2S_SDI   = 33;      // mic SD / DOUT
const int    SAMPLE_HZ = 16000;

RTSPServer rtspServer;
I2SClass   I2S;
int16_t   *buf = nullptr;
const size_t BUF_BYTES = 1024;
TaskHandle_t audioTask = NULL;

void sendAudio(void *) {
  while (true) {
    if (rtspServer.readyToSendAudio()) {
      size_t n = I2S.readBytes((char *)buf, BUF_BYTES);
      if (n) rtspServer.sendRTSPAudio(buf, n);
    }
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) delay(1000);

  I2S.setPins(I2S_SCK, I2S_WS, -1, I2S_SDI, -1);   // BCLK, LRCLK, DOUT, DIN, MCLK
  if (!I2S.begin(I2S_MODE_STD, SAMPLE_HZ, I2S_DATA_BIT_WIDTH_16BIT,
                 I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT)) {
    Serial.println("I2S mic init failed");
  }
  buf = (int16_t *)malloc(BUF_BYTES);
  xTaskCreate(sendAudio, "audio", 8192, NULL, 8, &audioTask);

  rtspServer.setCredentials(rtspUser, rtspPass);
  rtspServer.maxRTSPClients = 2;
  if (rtspServer.init(RTSPServer::AUDIO_ONLY, 554, SAMPLE_HZ)) {
    Serial.printf("RTSP: rtsp://%s:554/\n", WiFi.localIP().toString().c_str());
  }
}

void loop() { delay(1000); }
```

Bench notes:

- The INMP441 delivers 24-bit samples in a 32-bit frame; if the 16-bit path above is quiet or clipped, read `I2S_DATA_BIT_WIDTH_32BIT` and shift right to match the level (the mic's data is MSB-aligned). Tune gain here.
- Verify before touching Frigate: `ffprobe -rtsp_transport tcp rtsp://<user>:<pass>@<node-ip>:554/` (and `ffplay`) should report an `L16` mono audio stream.

## Bench checklist

Fast path from parts to a tested stream (see the wiring diagram and the sketch above).

1. **Wire** the INMP441 to the DevKitC per *Configuration*: `VDD`→3V3, `GND`→GND, `L/R`→GND (left channel), `SCK`→IO26, `WS`→IO25, `SD`→IO33. Six short (< 10 cm) jumpers, routed away from the antenna. Power the mic from **3V3 only — never 5 V**.
2. **Toolchain:** Arduino IDE 2.x (or `arduino-cli`), Espressif **ESP32** boards **≥ 3.1.1** (the sketch uses the `I2SClass` API), and the **`ESP32-RTSPServer`** library. Board: *ESP32 Dev Module*.
3. **Fill in** the sketch: Wi-Fi SSID/password and the RTSP user/password. Power the board from a USB supply (≥ 1 A) and use **2.4 GHz** Wi-Fi.
4. **Flash** over USB and open the serial monitor at **115200** — it prints `RTSP: rtsp://<node-ip>:554/`.
5. **Quick test** from a machine on the same network (or over the tailnet from `opus`):
   ```sh
   ffprobe -rtsp_transport tcp rtsp://frigate:PASSWORD@<node-ip>:554/
   ffplay  -rtsp_transport tcp rtsp://frigate:PASSWORD@<node-ip>:554/
   ```
   Expect one **mono `pcm_s16be` (L16)** audio stream at ~16 kHz; clap or whistle to hear it. A `401` means bad credentials; nothing means Wi-Fi/RTSP.
6. **Level check** (10 s, no playback):
   ```sh
   ffmpeg -rtsp_transport tcp -i rtsp://frigate:PASSWORD@<node-ip>:554/ -t 10 -af volumedetect -f null - 2>&1 | grep -i volume
   ```
   `mean_volume` well above -90 dB means real audio; if it is silent or clipped, read the mic as **32-bit and shift** (see the bench note above) and re-test.
7. Aim for Wi-Fi RSSI better than -75 dBm; lower TX power if the audio is noisy while the link is otherwise stable.
8. **Pin the address** once it works — a DHCP reservation (shop: `shop-router/hosts.yml`; home: the router) so the URL never moves — then move on to Frigate (Phase 4).

## Pulling it into Frigate

go2rtc on `opus` reads the node as an RTSP source and Frigate takes an audio-only input. The shop / `werkstatt` shape:

```yaml
# frigate.cameras.<name>.ffmpeg.inputs (extra input):
#   - path: rtsp://frigate:**********@<node-ip>:554/
#     roles: [audio]
# frigate.cameras.<name>.audio:
audio:
  enabled: true
  listen: [smoke_detector, fire_alarm]
  min_volume: 500   # tune on the node
```

The camera-less living-room node still needs the "audio-only camera" question settled; see the plan's Phase 4 and Open questions.
