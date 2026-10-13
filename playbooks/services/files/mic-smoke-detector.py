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
import json
import math
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from collections import deque

SR = 16000
FRAME = 320            # 20 ms analysis window
HOP = 160              # 10 ms step

DEFAULT_PROFILES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "mic-smoke-detector-profiles.json")

_publishers = []


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


def run_input(inp, profile, publisher, verbose=False):
    """One input: (re)spawn ffmpeg, frame the audio, drive the state machine."""
    source = inp["url"]
    tag = inp["name"]
    frame_bytes = FRAME * 2
    hop_bytes = HOP * 2
    window_frames = int(profile["window_s"] * SR / HOP)
    clear_frames = int(profile["clear_hold_s"] * SR / HOP)
    min_on = profile["on_fraction_min"]
    flags = deque(maxlen=window_frames)
    since_clear = 0
    alarm = False

    while True:
        log("[%s] starting ffmpeg: %s" % (tag, " ".join(ffmpeg_command(source))))
        proc = subprocess.Popen(ffmpeg_command(source), stdout=subprocess.PIPE)
        raw = bytearray()
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                raw += chunk
                while len(raw) >= frame_bytes:
                    win = struct.unpack_from("<%dh" % FRAME, raw, 0)
                    win = [v / 32768.0 for v in win]
                    flags.append(frame_is_on(win, profile))
                    del raw[:hop_bytes]
                    frac = sum(flags) / len(flags)
                    if frac >= min_on:
                        since_clear = 0
                        if not alarm:
                            log("[%s] ALARM on (window %.0f%% on)" % (tag, 100 * frac))
                            publisher.publish("ON")
                            alarm = True
                    else:
                        since_clear += 1
                        if alarm and since_clear >= clear_frames:
                            log("[%s] alarm cleared" % tag)
                            publisher.publish("OFF")
                            alarm = False
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
    ap.add_argument("--no-mqtt", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    inputs = build_inputs(args)
    profiles = load_profiles(args.profiles)

    threads = []
    for inp in inputs:
        if inp["profile"] not in profiles:
            log("[%s] profile %r not in %s - skipping"
                % (inp["name"], inp["profile"], args.profiles))
            continue
        profile = prepare(profiles[inp["profile"]], inp["profile"])
        publisher = Publisher(args.mqtt_host, args.mqtt_port, args.mqtt_user,
                              args.mqtt_pass, inp["topic"], enabled=not args.no_mqtt)
        _publishers.append(publisher)
        log("[%s] profile %s: bands=%s require=%s window=%.1fs on>=%.2f -> %s"
            % (inp["name"], inp["profile"], profile["bands_hz"],
               profile.get("require", "all"), profile["window_s"],
               profile["on_fraction_min"], inp["topic"]))
        thread = threading.Thread(
            target=run_input, name=inp["name"], daemon=True,
            args=(inp, profile, publisher, args.verbose))
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
