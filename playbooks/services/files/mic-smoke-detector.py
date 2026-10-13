#!/usr/bin/env python3
"""Server-side Rauchwarnmelder (German smoke alarm) detector.

Reads one or more ESP32 mic nodes' RTSP streams (L16 mono 16 kHz) via ffmpeg,
runs a two-tone Goertzel detector on each, and publishes the verdict to MQTT so
Home Assistant can raise a critical push.

The acoustic parameters live in named *profiles* (see
mic-smoke-detector-profiles.json), so a detector of a different type - or a
replacement unit - is a profile, tuned by analyze-recording.py, not a code
change.

Inputs come from a JSON file (DETECTOR_INPUTS / --inputs): a list of
{"name", "host"|"url", "profile", "topic"}. The RTSP credential is kept out of
that file and supplied via MIC_RTSP_USER / MIC_RTSP_PASSWORD, so the inputs file
needs no secret. A single --source (URL or file) still works for ad-hoc runs.

This is the server-side prototype; the same algorithm (and the same profile
numbers) is meant to be ported to the node firmware later.
"""
import argparse
import http.server
import json
import math
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import wave
from collections import deque

SR = 16000
FRAME = 320            # 20 ms analysis window
HOP = 160              # 10 ms step

DEFAULT_PROFILES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "mic-smoke-detector-profiles.json")

_publishers = []


def metric_escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


class Metrics:
    """Per-input detector state, rendered as Prometheus text. The soak relies
    on `mic_detector_on_fraction` (query max_over_time() in VictoriaMetrics to
    see how close non-alarms get)."""

    def __init__(self):
        self.state = {}
        self.clips = {}

    def register(self, name, profile):
        self.state[name] = {"profile": profile, "on_fraction": 0.0,
                            "alarm": 0, "restarts": 0, "up": 0}
        self.clips[name] = {}

    def count_clip(self, name, kind):
        if name in self.clips:
            self.clips[name][kind] = self.clips[name].get(kind, 0) + 1

    def update(self, name, **fields):
        if name in self.state:
            self.state[name].update(fields)

    def bump(self, name, field):
        if name in self.state:
            self.state[name][field] += 1

    def render(self):
        out = [
            "# HELP mic_detector_on_fraction current window 'on' fraction (0-1)",
            "# TYPE mic_detector_on_fraction gauge",
            "# HELP mic_detector_alarm current alarm state (0/1)",
            "# TYPE mic_detector_alarm gauge",
            "# HELP mic_detector_up 1 while audio is flowing for the input",
            "# TYPE mic_detector_up gauge",
            "# HELP mic_detector_ffmpeg_restarts_total ffmpeg (re)starts",
            "# TYPE mic_detector_ffmpeg_restarts_total counter",
            "# HELP mic_detector_clips_total audio clips written for analysis",
            "# TYPE mic_detector_clips_total counter",
        ]
        for name, st in sorted(self.state.items()):
            labels = 'input="%s",profile="%s"' % (metric_escape(name),
                                                     metric_escape(st["profile"]))
            out.append("mic_detector_on_fraction{%s} %s" % (labels, st["on_fraction"]))
            out.append("mic_detector_alarm{%s} %d" % (labels, st["alarm"]))
            out.append("mic_detector_up{%s} %d" % (labels, st["up"]))
            out.append("mic_detector_ffmpeg_restarts_total{%s} %d" % (labels, st["restarts"]))
            for kind, count in sorted(self.clips.get(name, {}).items()):
                out.append('mic_detector_clips_total{input="%s",kind="%s"} %d'
                           % (metric_escape(name), metric_escape(kind), count))
        return "\n".join(out) + "\n"


METRICS = Metrics()


def serve_metrics(addr):
    host, port = addr.rsplit(":", 1)
    host = host or "0.0.0.0"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0] != "/metrics":
                self.send_error(404)
                return
            body = METRICS.render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # quiet
            pass

    server = http.server.ThreadingHTTPServer((host, int(port)), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log("metrics on http://%s:%s/metrics" % (host, port))


class Clipper:
    """A rolling audio window per input; writes a WAV clip when triggered, so a
    near-miss or a test press can be analysed after the fact. The node serves a
    single RTSP client, so the detector itself has to do the capturing."""

    def __init__(self, name, clip_dir, pre_s, post_s, cooldown_s, max_clips):
        self.name = name
        self.safe = name.replace("/", "_").strip("_")
        self.dir = clip_dir
        self.pre_bytes = int(pre_s * SR * 2)
        self.total_bytes = int((pre_s + post_s) * SR * 2)
        self.cooldown_frames = int(cooldown_s * SR / HOP)
        self.max_clips = max_clips
        self.ring = bytearray()
        self.rec = None
        self.rec_kind = None
        self.seq = 0
        self.since_end = 10 ** 9
        os.makedirs(self.dir, exist_ok=True)

    def tick(self):
        if self.rec is None:
            self.since_end += 1

    def feed(self, chunk):
        self.ring += chunk
        if len(self.ring) > self.pre_bytes:
            del self.ring[:len(self.ring) - self.pre_bytes]
        if self.rec is not None:
            self.rec += chunk
            if len(self.rec) >= self.total_bytes:
                self._finish()

    def trigger(self, kind):
        if self.rec is not None:
            # An alarm that starts during a near-miss clip relabels it.
            if kind == "alarm" and self.rec_kind != "alarm":
                self.rec_kind = "alarm"
            return
        if self.since_end >= self.cooldown_frames:
            self.rec = bytearray(self.ring)
            self.rec_kind = kind
            log("[%s] capturing a %r clip" % (self.name, kind))

    def _finish(self):
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self.seq += 1
        path = os.path.join(self.dir, "%s-%s-%03d-%s.wav"
                            % (self.safe, stamp, self.seq, self.rec_kind))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(bytes(self.rec))
        log("[%s] wrote clip %s (%d KiB)" % (self.name, path, len(self.rec) // 1024))
        METRICS.count_clip(self.name, self.rec_kind)
        self.rec = None
        self.since_end = 0
        self._prune()

    def _prune(self):
        try:
            files = sorted(f for f in os.listdir(self.dir)
                           if f.startswith(self.safe + "-") and f.endswith(".wav"))
        except OSError:
            return
        for old in files[:max(0, len(files) - self.max_clips)]:
            try:
                os.remove(os.path.join(self.dir, old))
            except OSError:
                pass


def goertzel_power(samples, f, sr=SR):
    """Power at frequency f; a pure sine of amplitude A yields A*A/4."""
    n = len(samples)
    k = int(0.5 + n * f / sr)
    w = 2.0 * math.pi * k / n
    coeff = 2.0 * math.cos(w)
    s1 = s2 = 0.0
    for v in samples:
        s0 = v + coeff * s1 - s2
        s2 = s1
        s1 = s0
    return (s1 * s1 + s2 * s2 - coeff * s1 * s2) / (n * n)


def load_profiles(path):
    with open(path) as fh:
        return json.load(fh)


def prepare(profile, name):
    """Resolve the fields the detector needs once, on a copy of the profile."""
    profile = dict(profile)
    step = profile.get("band_step_hz", 50)
    profile["_bands"] = [list(range(int(lo), int(hi) + 1, step))
                         for lo, hi in profile["bands_hz"]]
    profile["_ratios"] = profile["ratio_min"]
    profile["_floor"] = 10.0 ** (profile["tone_floor_db"] / 10.0)
    profile["_name"] = name
    return profile


def frame_is_on(win, profile):
    """A frame is 'on' when every required band holds enough of its energy and
    the tone power clears the absolute floor."""
    powers = [max(goertzel_power(win, f) for f in band)
              for band in profile["_bands"]]
    tot = sum(v * v for v in win) / len(win)
    present = [p > r * tot for p, r in zip(powers, profile["_ratios"])]
    ok = all(present) if profile.get("require", "all") == "all" else any(present)
    return ok and sum(powers) > profile["_floor"]


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


class Publisher:
    """Thin MQTT wrapper: publishes ON/OFF retained, with an LWT of OFF."""

    def __init__(self, host, port, user, password, topic, enabled=True):
        self.topic = topic
        self.state = None
        self.client = None
        if not enabled:
            return
        import paho.mqtt.client as mqtt
        self.client = mqtt.Client()
        if user:
            self.client.username_pw_set(user, password)
        self.client.will_set(topic, "OFF", qos=1, retain=True)
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()

    def publish(self, state):
        if state == self.state:
            return
        self.state = state
        log("%s %s -> %s" % (self.topic, "state" if self.client is None
                             else "MQTT", state))
        if self.client is not None:
            self.client.publish(self.topic, state, qos=1, retain=True)

    def off(self):
        if self.client is not None:
            self.client.publish(self.topic, "OFF", qos=1, retain=True)


def ffmpeg_command(source):
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "warning"]
    if source.startswith(("rtsp://", "rtsps://")):
        cmd += ["-rtsp_transport", "tcp"]
    cmd += ["-i", source, "-f", "s16le", "-ar", str(SR), "-ac", "1", "-"]
    return cmd


def run_input(inp, profile, publisher, clipper=None, near_miss=1.0,
              record_trigger=None, verbose=False):
    """One input: (re)spawn ffmpeg, frame the audio, drive the state machine."""
    source = inp["url"]
    tag = inp["name"]
    tick_count = 0
    frame_bytes = FRAME * 2
    hop_bytes = HOP * 2
    window_frames = int(profile["window_s"] * SR / HOP)
    clear_frames = int(profile["clear_hold_s"] * SR / HOP)
    min_on = profile["on_fraction_min"]
    flags = deque(maxlen=window_frames)
    since_clear = 0
    alarm = False

    while True:
        METRICS.bump(tag, "restarts")
        log("[%s] starting ffmpeg: %s" % (tag, " ".join(ffmpeg_command(source))))
        proc = subprocess.Popen(ffmpeg_command(source), stdout=subprocess.PIPE)
        raw = bytearray()
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                raw += chunk
                if clipper is not None:
                    clipper.feed(chunk)
                while len(raw) >= frame_bytes:
                    win = struct.unpack_from("<%dh" % FRAME, raw, 0)
                    win = [v / 32768.0 for v in win]
                    flags.append(frame_is_on(win, profile))
                    del raw[:hop_bytes]
                    tick_count += 1
                    frac = sum(flags) / len(flags)
                    METRICS.update(tag, on_fraction=round(frac, 4), up=1)
                    if clipper is not None:
                        clipper.tick()
                        if record_trigger and tick_count % 100 == 0 \
                                and os.path.exists(record_trigger):
                            try:
                                os.remove(record_trigger)
                            except OSError:
                                pass
                            clipper.trigger("manual")
                    if frac >= min_on:
                        since_clear = 0
                        if not alarm:
                            log("[%s] ALARM on (window %.0f%% on)" % (tag, 100 * frac))
                            publisher.publish("ON")
                            alarm = True
                            METRICS.update(tag, alarm=1)
                            if clipper is not None:
                                clipper.trigger("alarm")
                    else:
                        since_clear += 1
                        if alarm and since_clear >= clear_frames:
                            log("[%s] alarm cleared" % tag)
                            publisher.publish("OFF")
                            alarm = False
                            METRICS.update(tag, alarm=0)
                        elif clipper is not None and frac >= near_miss:
                            clipper.trigger("nearmiss")
                    if verbose:
                        sys.stderr.write("[%s] on=%.0f%%\n" % (tag, 100 * frac))
        except Exception as exc:  # noqa: BLE001 - keep the service alive
            log("[%s] read loop error: %r" % (tag, exc))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            METRICS.update(tag, up=0)
        if not source.startswith(("rtsp://", "rtsps://")):
            log("[%s] input exhausted" % tag)
            return
        log("[%s] ffmpeg exited; reconnecting in 3 s" % tag)
        time.sleep(3)


def build_inputs(args):
    """Inputs from DETECTOR_INPUTS/--inputs (a JSON list), else a single
    --source. A 'host' entry is turned into an RTSP URL with the credential from
    MIC_RTSP_USER/MIC_RTSP_PASSWORD; a 'url' entry is used as-is."""
    if args.inputs and os.path.exists(args.inputs):
        with open(args.inputs) as fh:
            items = json.load(fh)
        user = args.rtsp_user or "frigate"
        inputs = []
        for item in items:
            url = item.get("url")
            if not url:
                if not args.rtsp_pass:
                    raise SystemExit("input %r has a host but MIC_RTSP_PASSWORD "
                                     "is not set" % item.get("name"))
                url = "rtsp://%s:%s@%s:554/" % (user, args.rtsp_pass, item["host"])
            inputs.append({
                "name": item.get("name", url),
                "url": url,
                "profile": item["profile"],
                "topic": item.get("topic", "mic/%s/smoke_alarm" % item.get("name", "")),
            })
        return inputs
    if not args.source:
        raise SystemExit("no DETECTOR_INPUTS and no --source/$RTSP_URL")
    return [{"name": args.source, "url": args.source,
             "profile": args.profile, "topic": args.topic}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", default=os.environ.get("DETECTOR_INPUTS"),
                    help="JSON list of inputs (default: $DETECTOR_INPUTS)")
    ap.add_argument("--source", default=os.environ.get("RTSP_URL"),
                    help="single RTSP URL or media file (ad-hoc; default: $RTSP_URL)")
    ap.add_argument("--profiles", default=os.environ.get(
        "DETECTOR_PROFILES", DEFAULT_PROFILES))
    ap.add_argument("--profile", default=os.environ.get("DETECTOR_PROFILE"),
                    help="profile for the single --source")
    ap.add_argument("--topic", default=os.environ.get(
        "MQTT_TOPIC", "mic/living_room/smoke_alarm"))
    ap.add_argument("--rtsp-user", default=os.environ.get("MIC_RTSP_USER"))
    ap.add_argument("--rtsp-pass", default=os.environ.get("MIC_RTSP_PASSWORD"))
    ap.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "localhost"))
    ap.add_argument("--mqtt-port", type=int,
                    default=int(os.environ.get("MQTT_PORT", "1883")))
    ap.add_argument("--mqtt-user", default=os.environ.get("MQTT_USER"))
    ap.add_argument("--mqtt-pass", default=os.environ.get("MQTT_PASS"))
    ap.add_argument("--metrics-addr", default=os.environ.get("METRICS_ADDR"),
                    help="host:port to serve /metrics on (default: $METRICS_ADDR)")
    ap.add_argument("--clip-dir", default=os.environ.get("CLIP_DIR"),
                    help="directory for near-miss/alarm audio clips (default: $CLIP_DIR; empty disables)")
    ap.add_argument("--clip-pre", type=float,
                    default=float(os.environ.get("CLIP_PRE_S", "15")))
    ap.add_argument("--clip-post", type=float,
                    default=float(os.environ.get("CLIP_POST_S", "10")))
    ap.add_argument("--near-miss", type=float,
                    default=float(os.environ.get("NEAR_MISS_FRACTION", "0.15")),
                    help="on-fraction below the alarm that still captures a clip")
    ap.add_argument("--clip-cooldown", type=float,
                    default=float(os.environ.get("CLIP_COOLDOWN_S", "60")))
    ap.add_argument("--clip-max", type=int,
                    default=int(os.environ.get("CLIP_MAX", "300")))
    ap.add_argument("--record-trigger", default=os.environ.get("RECORD_TRIGGER"),
                    help="path that, when created, records a clip (default: $RECORD_TRIGGER)")
    ap.add_argument("--no-mqtt", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    inputs = build_inputs(args)
    profiles = load_profiles(args.profiles)

    if args.metrics_addr:
        serve_metrics(args.metrics_addr)

    threads = []
    for inp in inputs:
        if inp["profile"] not in profiles:
            log("[%s] profile %r not in %s - skipping"
                % (inp["name"], inp["profile"], args.profiles))
            continue
        profile = prepare(profiles[inp["profile"]], inp["profile"])
        METRICS.register(inp["name"], inp["profile"])
        clipper = None
        if args.clip_dir:
            try:
                clipper = Clipper(inp["name"], args.clip_dir, args.clip_pre,
                                  args.clip_post, args.clip_cooldown, args.clip_max)
            except OSError as exc:
                log("[%s] clips disabled (%r)" % (inp["name"], exc))
        publisher = Publisher(args.mqtt_host, args.mqtt_port, args.mqtt_user,
                              args.mqtt_pass, inp["topic"], enabled=not args.no_mqtt)
        _publishers.append(publisher)
        log("[%s] profile %s: bands=%s require=%s window=%.1fs on>=%.2f -> %s"
            % (inp["name"], inp["profile"], profile["bands_hz"],
               profile.get("require", "all"), profile["window_s"],
               profile["on_fraction_min"], inp["topic"]))
        thread = threading.Thread(
            target=run_input, name=inp["name"], daemon=True,
            args=(inp, profile, publisher, clipper, args.near_miss,
                  args.record_trigger, args.verbose))
        thread.start()
        threads.append(thread)

    if not threads:
        raise SystemExit("nothing to monitor")

    def shutdown(_signum, _frame):
        log("stopping")
        for pub in _publishers:
            pub.off()
        for pub in _publishers:
            if pub.client is not None:
                pub.client.loop_stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
