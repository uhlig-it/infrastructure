#!/usr/bin/env python3
"""Server-side Rauchwarnmelder (German smoke alarm) detector.

Reads the ESP32 mic node's RTSP stream (L16 mono 16 kHz) via ffmpeg, runs a
two-tone Goertzel detector, and publishes the verdict to MQTT so Home
Assistant can raise a critical push.

The acoustic parameters live in a named *profile* (see
mic-smoke-detector-profiles.json), not in this code: a detector of a different
type - or a replacement unit - is a new profile, tuned by analyze-recording.py.

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
import time
from collections import deque

SR = 16000
FRAME = 320            # 20 ms analysis window
HOP = 160              # 10 ms step

DEFAULT_PROFILES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "mic-smoke-detector-profiles.json")


def load_profile(name, profiles_path):
    """Load a profile by name from the profiles file, or a path to a JSON file."""
    if not name:
        raise SystemExit("no detector profile given (--profile / $DETECTOR_PROFILE)")
    if os.path.sep in name or name.endswith(".json"):
        with open(name) as fh:
            profile = json.load(fh)
    else:
        with open(profiles_path) as fh:
            profiles = json.load(fh)
        if name not in profiles:
            raise SystemExit("profile %r not in %s (have: %s)"
                             % (name, profiles_path, ", ".join(sorted(profiles))))
        profile = profiles[name]
    # Resolve the fields the detector needs once, here.
    step = profile.get("band_step_hz", 50)
    profile["_bands"] = [list(range(int(lo), int(hi) + 1, step))
                         for lo, hi in profile["bands_hz"]]
    profile["_ratios"] = profile["ratio_min"]
    profile["_floor"] = 10.0 ** (profile["tone_floor_db"] / 10.0)
    profile["_name"] = name
    return profile


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
        self.enabled = enabled
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
        log("%s %s -> %s" % ("state" if self.client is None else "MQTT",
                               self.topic, state))
        if self.client is not None:
            self.client.publish(self.topic, state, qos=1, retain=True)


def ffmpeg_command(source):
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "warning"]
    if source.startswith(("rtsp://", "rtsps://")):
        cmd += ["-rtsp_transport", "tcp"]
    cmd += ["-i", source, "-f", "s16le", "-ar", str(SR), "-ac", "1", "-"]
    return cmd


def run(source, profile, publisher, verbose=False):
    frame_bytes = FRAME * 2
    hop_bytes = HOP * 2
    window_frames = int(profile["window_s"] * SR / HOP)
    clear_frames = int(profile["clear_hold_s"] * SR / HOP)
    min_on = profile["on_fraction_min"]
    flags = deque(maxlen=window_frames)
    since_clear = 0
    alarm = False

    while True:
        log("starting ffmpeg: %s" % " ".join(ffmpeg_command(source)))
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
                            log("ALARM on (window %.0f%% on)" % (100 * frac))
                            publisher.publish("ON")
                            alarm = True
                    else:
                        since_clear += 1
                        if alarm and since_clear >= clear_frames:
                            log("alarm cleared")
                            publisher.publish("OFF")
                            alarm = False
                    if verbose:
                        sys.stderr.write("on=%.0f%%\n" % (100 * frac))
        except Exception as exc:  # noqa: BLE001 - keep the service alive
            log("read loop error: %r" % exc)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if not source.startswith(("rtsp://", "rtsps://")):
            log("input exhausted; exiting")
            return
        log("ffmpeg exited; reconnecting in 3 s")
        time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.environ.get("RTSP_URL"),
                    help="RTSP URL or media file (default: $RTSP_URL)")
    ap.add_argument("--profile", default=os.environ.get("DETECTOR_PROFILE"),
                    help="profile name (in the profiles file) or a JSON path")
    ap.add_argument("--profiles", default=os.environ.get(
        "DETECTOR_PROFILES", DEFAULT_PROFILES))
    ap.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "localhost"))
    ap.add_argument("--mqtt-port", type=int,
                    default=int(os.environ.get("MQTT_PORT", "1883")))
    ap.add_argument("--mqtt-user", default=os.environ.get("MQTT_USER"))
    ap.add_argument("--mqtt-pass", default=os.environ.get("MQTT_PASS"))
    ap.add_argument("--topic", default=os.environ.get(
        "MQTT_TOPIC", "mic/living_room/smoke_alarm"))
    ap.add_argument("--no-mqtt", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.source:
        ap.error("no --source and no $RTSP_URL")

    profile = load_profile(args.profile, args.profiles)
    log("profile %s: bands=%s require=%s window=%.1fs on>=%.2f"
        % (profile["_name"], profile["bands_hz"], profile.get("require", "all"),
           profile["window_s"], profile["on_fraction_min"]))

    publisher = Publisher(args.mqtt_host, args.mqtt_port, args.mqtt_user,
                          args.mqtt_pass, args.topic, enabled=not args.no_mqtt)

    def shutdown(_signum, _frame):
        log("stopping")
        if publisher.client is not None:
            publisher.client.publish(args.topic, "OFF", qos=1, retain=True)
            publisher.client.loop_stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    run(args.source, profile, publisher, verbose=args.verbose)


if __name__ == "__main__":
    main()
