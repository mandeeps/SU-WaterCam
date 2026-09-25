# Capture pipeline: detection, gotchas, and a reference run

Operational notes from running the pipeline end to end on node `ufo-01-01-005`, 2026-09-25.
Everything here is measured or reproduced on hardware.

---

## Detecting the cameras

**Do not use `vcgencmd get_camera`.** It belongs to the legacy Broadcom camera stack and returns
nothing useful on the libcamera stack, which is what these nodes run (Debian 13, kernel 6.18). It
reports silence whether or not a camera is attached, so a missing camera and a working one look
identical.

```bash
rpicam-still --list-cameras
```

```
0 : ov5647 [2592x1944 10-bit GBRG] (/base/soc/i2c0mux/i2c@1/ov5647@36)
```

For the thermal side, the Lepton needs SPI:

```bash
ls /dev/spidev*            # expect /dev/spidev0.0 and /dev/spidev0.1
grep -i '^dtparam=spi' /boot/firmware/config.txt
```

`ls /dev/video*` is also a poor test on its own: the libcamera stack exposes `/dev/media*` nodes and
the mapping to `/dev/video*` is not what it was under the legacy stack.

## The Lepton `capture` binary must be found by walking up from the capture directory

`flir()` in `tt_take_photos.py` locates the binary with `_find_project_root()`, which walks **up from
the directory it is given** looking for a file named `capture`. The binaries live in the project
root:

    SU-WaterCam/capture
    SU-WaterCam/lepton

So the capture directory has to sit under the project tree. Production satisfies this by writing to
`images/<timestamp>/`. Capturing somewhere else fails with

    Check Lepton state - capture binary not found: /tmp/capture

and the optical pair still succeeds, so the failure is easy to miss — you get a scene with no
thermal band rather than an error.

## Driving the capture functions directly

`take_two_photos` and `flir` are `@SQify`-decorated for the TickTalk runtime. The plain functions
are reachable through `__wrapped__`:

```python
import tt_take_photos as T
T.take_two_photos.__wrapped__(None, directory)   # optical pair, IR-cut filter toggled between them
T.flir.__wrapped__(directory)                    # Lepton frame + temperatures CSV
```

## The pipeline spans two interpreters

This is by design and matches what `ticktalk_main.segformer()` does with a subprocess:

| stage | interpreter | why |
|---|---|---|
| capture, co-registration | `SU-WaterCam/venv/bin/python3` | picamera2, gpiozero, SimpleITK |
| segmentation | `~/miniforge3/envs/5band/bin/python` | mmseg, mmcv, onnxruntime |

Neither environment can run the other's stage. `mmseg` also only imports with the working directory
set to `segformer_5band`, because the package is vendored there.

## Reference run, sensors through to mask

Node 005, indoors, `iter_100.pth`:

| stage | time | output |
|---|---|---|
| optical pair | 7.9 s | `*-NIR-OFF.jpg` 1,146,071 B, `*-NIR-ON.jpg` 1,062,783 B |
| FLIR Lepton | 3.0 s | `lepton_*.pgm` 64,063 B, `temperatures_*.csv` 96,122 B |
| co-registration | 51.85 s | `final_5_band.tiff` 1,312,028 B, `color_preserved_5_band.tiff` 6,284,508 B |
| segmentation, torch | 28.79 s | mask (972, 1296), water 32.932% |
| segmentation, ONNX | 1.81 s | mask (972, 1296), water 32.891% |

5-band stack `(5, 972, 1296) uint8`, band means R=74.9 G=65.0 B=70.3 TH=97.3 NIR=47.1.

Two things worth reading off this:

- **torch against ONNX agreed on 98.39% of pixels here**, against 94.29% on `lake.tiff` and 93.35%
  on a 23:45 night frame. The disagreement is padding sensitivity scaled by how marginal the
  decisions are, so a well-exposed scene disagrees less. See `segformer_5band/NEXT_CHECKPOINT.md`.
- **Co-registration took 51.85 s here, against 24.20 s and 10.11 s on comparable scenes.** The
  solver's convergence time varies run to run, not just its answer. Once the transform cache is
  seeded this drops to about 1 s regardless.

The 32.9% water on an indoor room is not meaningful. That is a 100-iteration checkpoint, not the
pipeline.
