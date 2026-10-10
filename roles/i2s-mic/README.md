# i2s-mic

Ansible role for the **INMP441 I2S MEMS microphone** on a Raspberry Pi 3, part of the smoke-detection work (see [`../../docs/smoke-detection-plan.md`](../../docs/smoke-detection-plan.md)). Phase 0 creates this directory and adds the wiring diagram; a later phase adds the tasks that manage `/boot/firmware/config.txt` (`dtparam=audio=off`, `dtoverlay=googlevoicehat-soundcard`).

## Wiring diagram

- `inmp441-pi3.yaml` — the diagram source (wiregen's declarative YAML).
- `inmp441-pi3.svg` / `inmp441-pi3.png` — the rendered diagram (vector, plus a raster preview).

Render it with [wiregen](https://github.com/WeebLabs/wiregen):

```sh
pipx install --editable /path/to/suhlig-wiregen   # install the fork, editable
wiregen render roles/i2s-mic/inmp441-pi3.yaml --table
```

`render` validates first and refuses to write on error; `--table` also prints the from→to wiring checklist, and `--theme light` (or `carbon`, `midnight`) switches the colour scheme.

We use wiregen via the **fork until the INMP441 and Raspberry Pi 3 parts land upstream** (`github.com/suhlig/wiregen`, branch `add-smoke-detection-via-INMP441`). There is already an unmerged `add-pcm5102A` branch on the fork, and the upstream author is responsive, so the parts are meant as contributions rather than a permanent fork. Two parts were added to the fork's library (`wiregen/parts/`):

- `inmp441` — INMP441 breakout (VDD, GND, L/R, SCK, WS, SD).
- `raspberry-pi-3` — Raspberry Pi 3 Model B, the 40-pin J8 header drawn as its two physical rows (odd pins left, even pins right).

Confirm they load with `wiregen list-parts` / `wiregen describe-part inmp441`.

## Why wiregen

The requirement was a wiring diagram that is **text-source, diffable, and renders a real board** — the INMP441 wired to a Pi 3, kept alongside the role that deploys it. wiregen fits that exactly: a small YAML describes parts and pin-to-pin wiring, the engine owns every pixel, the part library is easy to extend, and buses collapse into one shorthand with role-coloured wires and an auto-generated checklist. It keeps the diagram in git next to the Ansible code, with no GUI round-trip and no cloud.

## Why not the alternatives

- **WireViz** produces a harness/pinout drawing, not a board picture — you describe connectors by hand and get no board likeness, so it reads as a connector table rather than "how this hooks up on the Pi".
- **Wokwi** renders realistic breadboards, but its only Raspberry Pi is the **Pi Pico** (RP2040); it does not model a Pi 3, and the INMP441 is not in its catalog. A dead end for this hardware.
- **Fritzing** gives the closest visual result and a large parts library, but it is GUI-first with an XML project format, so the diagram cannot live as reviewable source in the repo.

The one trade-off: wiregen is young. That is why the two parts are contributed upstream rather than vendored, and why the diagram source is kept trivial (one `bus: i2s` shorthand plus three explicit wires).
