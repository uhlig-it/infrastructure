# INMP441 wiring diagram

The wiring for the INMP441 → Raspberry Pi 3 hookup lives in [`wiregen/inmp441-pi3.yaml`](wiregen/inmp441-pi3.yaml), rendered by **[wiregen](https://github.com/WeebLabs/wiregen)**.

We use a **fork until the INMP441 and Raspberry Pi 3 parts land upstream** (`github.com/suhlig/wiregen`, branch `add-smoke-detection-via-INMP441`). There is already an unmerged `add-pcm5102A` branch on the fork, and the upstream author is responsive, so the parts are intended as contributions rather than a permanent fork.

## Contents

- `wiregen/inmp441-pi3.yaml` — the diagram source (wiregen's declarative YAML).
- `wiregen/inmp441-pi3.svg` — rendered diagram (vector; the artefact to link in docs).
- `wiregen/inmp441-pi3.png` — raster preview for quick viewing.
- `wireviz/` and `wokwi/` — earlier attempts, superseded (see [Why not WireViz or Wokwi](#why-not-wireviz-or-wokwi)).

## New parts

Two parts were added to the wiregen part library (`wiregen/parts/` in the fork):

- `inmp441` — INMP441 I2S MEMS microphone breakout (6 pins: VDD, GND, L/R, SCK, WS, SD).
- `raspberry-pi-3` — Raspberry Pi 3 Model B, the 40-pin J8 header drawn as its two physical rows (odd pins left, even pins right).

They follow the same schema as the bundled parts; confirm they load with `wiregen list-parts` / `wiregen describe-part inmp441`.

## Rendering

Install the fork (editable, so local part edits are picked up) and render:

```sh
pipx install --editable /path/to/suhlig-wiregen   # or: pip install -e .
wiregen render docs/wiring/wiregen/inmp441-pi3.yaml --table
```

`render` validates first and refuses to write on error; `--table` also prints the from→to wiring checklist, and `--theme light` (or `carbon`, `midnight`) switches the colour scheme. The diagram is a plain `from instance.pin → to instance.pin` list plus one `bus: i2s` shorthand, so it diffs cleanly in git.

## Why wiregen

wiregen fills the gap the other tools left: a **text-source diagram that draws real boards** (not a bare pinout), with a built-in part library you can extend, role-coloured buses, and no cloud or GUI round-trip. It is young, but the data model is small and the parts are easy to add — which is exactly what this exercise needed.

## Why not WireViz or Wokwi

- **WireViz** produces a harness/pinout drawing, not a board picture; the connectors have to be described by hand and it has no board likeness. Kept in the tree only for comparison.
- **Wokwi** renders realistic breadboards, but its only Raspberry Pi is the **Pi Pico** (RP2040) — it does not model a Pi 3 — and the INMP441 is not in its catalog. A dead end for this project.

Both earlier outputs remain committed under this directory so the comparison is visible; they can be deleted if unwanted.
