#!/usr/bin/env python3
"""Analyse a recording of smoke alarms and suggest a detector profile.

    analyze-recording.py RECORDING [--profile NAME] [--profiles PATH]
                                    [--emit-name NAME]

RECORDING is a 16 kHz mono WAV, or an RTSP URL (captured with ffmpeg).

It
  1. finds the loudest region (a press) and the quiet background,
  2. finds the narrowband tone peaks that dominate the press but not the
     background,
  3. keeps only the peaks that are (a) a large share of the frame energy and
     (b) amplitude-gated - the signature a hum or speech does not have,
  4. prints a detector profile derived from the measurements,
  5. evaluates that profile - and, with --profile, an existing one - against
     the recording: the detections it yields, each one's peak window
     on-fraction, and any that fall outside a press region (false positives).

Run it on a recording of a new detector type (or a replacement unit) to get the
profile to put in mic-smoke-detector-profiles.json. See
docs/smoke-detection-plan.md for the recalibration runbook.
"""
import argparse
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave

SR = 16000
FRAME = 320            # 20 ms, matches the detector
HOP = 160              # 10 ms
FFT_N = 1024           # 64 ms, Hann-windowed, for tone peak finding
GRID_LO, GRID_HI = 300, 6000
FWHM = 175             # suggested band half-width (Hz)
MIN_LEVEL = 0.03       # a gated tone must hold >=3% of the frame energy
MIN_GATE_HZ = 1.5      # and be gated at least this fast
STRONG_LEVEL = 0.20    # ... or hold this much continuously (a steady tone)


# --- spectrum ---------------------------------------------------------------

def fft(a):
    """In-place iterative radix-2 FFT (len must be a power of two)."""
    n = len(a)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        ang = -2.0 * math.pi / length
        wlen = complex(math.cos(ang), math.sin(ang))
        half = length >> 1
        for i in range(0, n, length):
            w = 1 + 0j
            for k in range(i, i + half):
                u = a[k]
                v = a[k + half] * w
                a[k] = u + v
                a[k + half] = u - v
                w *= wlen
        length <<= 1
    return a


def hann(n):
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)) for i in range(n)]


def mean_spectrum(samples, centers, window):
    """Average power spectrum (|X|^2) over windows at the given sample indices."""
    acc = [0.0] * (FFT_N // 2)
    for c in centers:
        start = max(0, min(c - FFT_N // 2, len(samples) - FFT_N))
        buf = [samples[start + i] * window[i] for i in range(FFT_N)]
        spec = fft([complex(v, 0.0) for v in buf])
        for i in range(FFT_N // 2):
            acc[i] += spec[i].real ** 2 + spec[i].imag ** 2
    return [v / max(1, len(centers)) for v in acc]


# --- detector frame rule (mirrors mic-smoke-detector.py) ---------------------

def goertzel_power(samples, f, sr=SR):
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


def internalize(profile):
    step = profile.get("band_step_hz", 50)
    profile["_bands"] = [list(range(int(lo), int(hi) + 1, step))
                         for lo, hi in profile["bands_hz"]]
    profile["_ratios"] = profile["ratio_min"]
    profile["_floor"] = 10.0 ** (profile["tone_floor_db"] / 10.0)
    return profile


def frame_ratios(win, bands):
    """Per-band (max Goertzel power / frame power) and the band powers."""
    powers = [max(goertzel_power(win, f) for f in band) for band in bands]
    tot = sum(v * v for v in win) / len(win)
    return powers, tot


def frame_is_on(win, profile):
    powers, tot = frame_ratios(win, profile["_bands"])
    present = [p > r * tot for p, r in zip(powers, profile["_ratios"])]
    ok = all(present) if profile.get("require", "all") == "all" else any(present)
    return ok and sum(powers) > profile["_floor"]


def frames(samples):
    for i in range(0, len(samples) - FRAME, HOP):
        yield i / SR, [v / 32768.0 for v in samples[i:i + FRAME]]


def percentile(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1)))]


# --- region + tone analysis -------------------------------------------------

def segment(samples):
    """Energy-based press regions: frames >+10 dB over the 20th percentile,
    gaps under 0.5 s merged, runs under 1.5 s dropped. Returns regions and the
    per-frame power list."""
    powers = [sum(v * v for v in win) / len(win) for _, win in frames(samples)]
    if not powers:
        return [], powers, []
    floor = percentile(powers, 0.20) * 10.0 ** (10.0 / 10.0)
    active = [p > floor for p in powers]
    regions, start, gap = [], None, 0
    for i, p in enumerate(powers):
        if p > floor:
            start = i if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap > int(0.5 * SR / HOP):
                regions.append([start * HOP / SR, (i - gap) * HOP / SR])
                start = None
    if start is not None:
        regions.append([start * HOP / SR, len(powers) * HOP / SR])
    return [r for r in regions if r[1] - r[0] >= 1.5], powers, active


def region_power(powers, region):
    a = int(region[0] * SR / HOP)
    b = int(region[1] * SR / HOP)
    seg = powers[a:b] or [0.0]
    return sum(seg) / len(seg)


def find_peaks(loud, quiet, bin_hz):
    """Narrowband peaks present in the press spectrum but not the background."""
    median = percentile(loud, 0.5) or 1e-30
    found = []
    for i in range(max(1, int(GRID_LO / bin_hz)),
                   min(len(loud) - 1, int(GRID_HI / bin_hz))):
        if loud[i] < 16.0 * median:
            continue
        if loud[i] < max(loud[i - 1], loud[i + 1]):
            continue
        if quiet[i] > 0 and loud[i] < 10.0 * quiet[i]:
            continue
        found.append((loud[i], i * bin_hz))
    found.sort(reverse=True)
    clusters = []
    for power, freq in found:
        for c in clusters:
            if abs(freq - c["freq"]) < 300:
                c["freq"] = (c["freq"] * c["n"] + freq) / (c["n"] + 1)
                c["n"] += 1
                c["power"] = max(c["power"], power)
                break
        else:
            clusters.append({"freq": freq, "power": power, "n": 1})
    top = clusters[:4]
    if top:
        top = [c for c in top if c["power"] > top[0]["power"] / 100.0]
    return top


def band_for(freq):
    return [int(round((freq - FWHM) / 25.0)) * 25,
            int(round((freq + FWHM) / 25.0)) * 25]


def band_stats(samples, band_range, region, step=50):
    """Return (level, gate_hz) for a band (a [lo, hi] range) inside a region.
    'level' is the median share of the frame energy while the band sounds
    (used for the ratio threshold); 'gate_hz' is the on/off rate of the band's
    amplitude envelope - measured on the amplitude, not the ratio, because a
    recording whose gaps keep a residual tone (e.g. an AGC'd phone recording)
    still gates in amplitude while its ratio barely moves."""
    band = list(range(int(band_range[0]), int(band_range[1]) + 1, step))
    sub = samples[int(region[0] * SR):int(region[1] * SR)]
    band_powers, ratios = [], []
    for _, win in frames(sub):
        powers, tot = frame_ratios(win, [band])
        band_powers.append(powers[0])
        ratios.append(powers[0] / (tot + 1e-30))
    present = [r for r in ratios if r > 0.01]
    level = percentile(present, 0.5) if present else 0.0
    peak = percentile(band_powers, 0.9) if band_powers else 0.0
    thr = 0.25 * peak
    edges, prev = 0, False
    for p in band_powers:
        on = p > thr
        edges += 1 if (on and not prev) else 0
        prev = on
    dur = max(0.001, region[1] - region[0])
    return level, edges / dur


def detect(samples, profile):
    """Sliding-window detections: intervals where the on-fraction holds."""
    flags = [frame_is_on(win, profile) for _, win in frames(samples)]
    wf = int(profile["window_s"] * SR / HOP)
    step = int(0.5 * SR / HOP)
    out = []
    for i in range(0, max(0, len(flags) - wf), step):
        frac = sum(flags[i:i + wf]) / wf
        if frac < profile["on_fraction_min"]:
            continue
        t = i * HOP / SR
        if out and t - out[-1][1] <= profile["window_s"]:
            out[-1][1] = t
            out[-1][2] = max(out[-1][2], frac)
        else:
            out.append([t, t, frac])
    return [[a, b + profile["window_s"], f] for a, b, f in out]


# --- io ---------------------------------------------------------------------

def read_samples(path):
    if path.startswith(("rtsp://", "rtsps://")):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        print("capturing %s -> %s" % (path, tmp.name), flush=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning",
                        "-rtsp_transport", "tcp", "-i", path, "-t", "180",
                        "-y", tmp.name], check=True)
        path = tmp.name
    with wave.open(path, "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1, \
            "need %d Hz mono; got %d Hz, %d ch" % (SR, w.getframerate(),
                                                   w.getnchannels())
        raw = w.readframes(w.getnframes())
    return [s for s in struct.unpack("<%dh" % (len(raw) // 2), raw)]


def load_profile(name, profiles_path):
    if os.path.sep in name or name.endswith(".json"):
        with open(name) as fh:
            return json.load(fh)
    with open(profiles_path) as fh:
        return json.load(fh)[name]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("--profile", help="also evaluate this existing profile "
                                      "(name in --profiles, or a JSON path)")
    ap.add_argument("--profiles", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "mic-smoke-detector-profiles.json"))
    ap.add_argument("--emit-name", default="smoke_new_v1")
    args = ap.parse_args()

    samples = read_samples(args.recording)
    print("recording: %.1f s, %d Hz mono" % (len(samples) / SR, SR))

    regions, powers, active = segment(samples)
    print("press regions: %s"
          % (", ".join("%.1f-%.1f" % (a, b) for a, b in regions) or "(none)"))
    if not regions:
        print("no loud regions found - is the recording level reasonable?")
        return
    loudest = max(regions, key=lambda r: region_power(powers, r))
    print("loudest region (analysed): %.1f-%.1f s" % (loudest[0], loudest[1]))

    # Press spectrum from inside the loudest region; background spectrum from
    # the frames the segmentation calls inactive (so a long, continuous press
    # does not contaminate the background with its own tone).
    quiet_idx = [i * HOP for i, a in enumerate(active) if not a][::3]
    loud_idx = [int(t * SR) for t in
                [loudest[0] + 0.05 * k for k in
                 range(int((loudest[1] - loudest[0]) / 0.05))]]
    win = hann(FFT_N)
    loud_spec = mean_spectrum(samples, loud_idx, win)
    quiet_spec = mean_spectrum(samples, quiet_idx, win)
    peaks = find_peaks(loud_spec, quiet_spec, SR / FFT_N)
    print("tone peaks: %s"
          % (", ".join("%.0f Hz" % p["freq"] for p in peaks) or "(none)"))

    # Keep only the peaks that are tonal AND (gated - the pulsed alarm's
    # signature - or continuously strong, which is how a steady-tone detector
    # looks). Both are far above what hum or speech reach.
    keep = []
    print("candidate bands (level = share of frame energy, gate = on/off rate):")
    for peak in peaks:
        band = band_for(peak["freq"])
        level, gate = band_stats(samples, band, loudest)
        gated = level >= MIN_LEVEL and gate >= MIN_GATE_HZ
        steady = level >= STRONG_LEVEL
        ok = gated or steady
        print("  %4.0f-%.0f Hz  level=%.3f  gate=%.1f Hz  %s"
              % (band[0], band[1], level, gate,
                 "keep" + (" (steady)" if steady and not gated else "")
                 if ok else "drop"))
        if ok:
            keep.append((level, band))
    keep = sorted(keep, key=lambda lb: lb[1][0])[:3]
    bands = [band for _, band in keep]
    ratios = [round(max(0.01, 0.2 * level), 2) for level, _ in keep]

    press_s = loudest[1] - loudest[0]
    window = min(8.0, max(3.0, round(0.6 * press_s, 1)))
    suggested = {
        "description": ("Suggested from %s: %s; press ~%.1f s"
                        % (os.path.basename(args.recording),
                           ", ".join("%.0f Hz" % p["freq"] for p in peaks
                                     if band_for(p["freq"]) in bands),
                           press_s)),
        "bands_hz": bands,
        "band_step_hz": 50,
        "ratio_min": ratios,
        "require": "all" if len(bands) > 1 else "any",
        "tone_floor_db": -80,
        "window_s": window,
        "on_fraction_min": 0.30,
        "clear_hold_s": 2.0,
    }
    print("\nsuggested profile:")
    print(json.dumps({args.emit_name: suggested}, indent=2))

    to_eval = [(args.emit_name, suggested)]
    if args.profile:
        to_eval.append((args.profile, load_profile(args.profile, args.profiles)))
    for name, prof in to_eval:
        profile = internalize(dict(prof))
        hits = detect(samples, profile)
        print("\nevaluation of %r (bands %s, window %.1fs, on>=%.2f):"
              % (name, profile["bands_hz"], profile["window_s"],
                 profile["on_fraction_min"]))
        if not hits:
            print("  no detections")
        for a, b, frac in hits:
            inside = any(not (b < r0 or a > r1) for r0, r1 in regions)
            print("  %.1f .. %.1f s  peak %.0f%% on  %s"
                  % (a, b, 100 * frac,
                     "" if inside else "<- outside a press region"))


if __name__ == "__main__":
    main()
