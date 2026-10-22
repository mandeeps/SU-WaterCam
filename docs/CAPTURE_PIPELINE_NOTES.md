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

This applies to the subprocess fallback (`segment_tiff_5band.py`). `segformer_daemon.service`, the
normal path, needs only onnxruntime, cv2, rasterio and PIL, so it runs in `SU-WaterCam/venv`.

## The two five-band TIFFs are not interchangeable

Co-registration writes both, and they differ in channel order and geometry:

| file | channels | size |
|---|---|---|
| `color_preserved_5_band.tiff` | **R, G, B**, thermal, NIR | native 972x1296, 4:3 |
| `final_5_band.tiff` | **B, G, R**, thermal, NIR | 512x512, aspect squashed |

`final_5_band` carries OpenCV's native order straight from `cv2.imread`.
`save_color_preserved_tiff()` is the function that reverses the optical channels, which is what
"color preserved" refers to. Verified against the source JPEG read as true RGB: `color_preserved[0]`
correlates 1.0000 with true red, `final_5_band[0]` correlates 1.0000 with true blue.

**Everything that feeds or reads a model uses `color_preserved_5_band.tiff`**:
`ticktalk_main.segformer()` and `tools/watercam.py` serve it, `photo_processing/export_dataset.py`
trains on it, and `tools/export_segformer_onnx.py` calibrates INT8 on it. The names live in
`CoregistrationConfig` (`MODEL_INPUT_TIFF`, `SEGMENTATION_PNG`) rather than as string literals.

`final_5_band.tiff` is **legacy output, no longer read by any UFONet software.** It is still
written so older captures and external tooling keep working. **Do not feed it to a model.**

### Every TIFF states its own band order

Both writers now set a `BAND_ORDER` tag — `red,green,blue,thermal,nir` for `color_preserved`,
`blue,green,red,thermal,nir` for `final_5_band`, which also carries a `BAND_ORDER_NOTE` saying it is
legacy. The value is the same comma-separated format the exported `.onnx` declares in its `bands`
metadata, so `tools/segformer_daemon.py` compares the file against the model directly instead of
keyword-matching prose band descriptions. `final_5_band` previously carried no band descriptions and
no tags at all, which is how the file easiest to confuse was also the one that said nothing about
itself.

Captures written before the tag existed carry only the prose descriptions, and
`segformer_preprocess.tiff_band_order()` still reads those, so nothing already captured is
invalidated. An audit of all 6489 `color_preserved_5_band.tiff` in the archive found **zero** files
with the bands in the wrong order.

### Models that want fewer bands, or another resize

A model that declares fewer `bands` than the TIFF has gets those bands, picked by name in the
model's order (`segformer_preprocess.model_band_indices()`). An RGB model that declares
`bands=red,green,blue` therefore runs on `color_preserved_5_band.tiff` unchanged. If the TIFF doesn't
name each wanted band exactly once, every band is fed as before and the band-order check warns.

Input bands are resized with `INTER_AREA` unless the model declares `resize_interpolation=bilinear`.
mmcv's `Resize`, used by the training repos' test pipelines, is bilinear. For the RGB
`segformer_type_pool` model, `INTER_AREA` changed 0.4–2.6% of pixels and found up to 1.6× the water;
with `bilinear` declared, the daemon matches that model's pipeline on every pixel of six 006
captures (2026-10-11). The 5-band `iter_100` model declares nothing and keeps `INTER_AREA`.

### Mask name

The segmentation mask is `color_preserved_5_band_segmentation.png`. It is named after its input and
cannot be named independently: `segment_tiff_5band.py` takes no output argument and writes
`<input stem>_segmentation.png`. While the mask was still called `final_5_band_segmentation.png`,
the daemon path wrote that name but the subprocess fallback wrote the stem-derived name and then
returned the name it had not written. Masks are resolved through
`coreg_multiple.segmentation_path()`, which accepts the superseded name so directories segmented
earlier stay readable.

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

## Repeatable co-registration on bench units (`COREG_SINGLE_THREAD`)

A fresh registration (no cached transform, or the camera has moved) isn't repeatable by
default: SimpleITK's mutual-information metric runs on several threads and adds up their
partial results in whatever order they finish, so two runs on the same capture differ
(2–9% of pixels, masks 0.1–0.3%, on 005 in 2026-10). No random seed is involved; every
pixel is used. Cached transforms are repeatable either way.

Set `COREG_SINGLE_THREAD=1` to run SimpleITK on one thread: the same capture then gives the
same output every time, which bench comparisons and regression tests need, at about twice
the time per fresh registration (~150 s vs ~72 s on 005). Bench units only; deployed units
leave it unset and keep the speed. On a bench unit:

```bash
sudo mkdir -p /etc/systemd/system/ticktalk.service.d
printf '[Service]\nEnvironment=COREG_SINGLE_THREAD=1\n' | \
    sudo tee /etc/systemd/system/ticktalk.service.d/bench-coreg-single-thread.conf
sudo systemctl daemon-reload
```

For a one-off run: `COREG_SINGLE_THREAD=1 venv/bin/python tools/coreg_multiple.py <capture dir> --position-changed`.


## Thermal registration: what gets cached

The Lepton's PGM is 16-bit whenever its span exceeds 255 (`tools/capture.c` writes
`maxval = max - min`). Registration scales it between the 1st and 99th percentile
before the 8-bit step; casting straight to 8 bits wrapped it modulo 256.

A fresh solve is written to `images/registration_transform.json` (the cache every later
capture reuses) only if both hold, in `tools/coreg_multiple.py`:

- the final mutual-information metric is a number, and at most `MIN_ACCEPT_METRIC` once
  that is set (more negative is better, a failed solve sits near 0). It is empty for now:
  solves on the field units, before the thermal frame was scaled properly, had a median of
  about -0.006 and a best of -0.18, so a threshold has to be measured from the `Final metric
  value` lines of good captures with the fixed preprocessing;
- no point of the registration frame moves more than `MAX_TRANSFORM_DRIFT_PX` (25 px, at
  the resized registration resolution) from the transform already cached. The cameras are
  bolted together, so the true transform is a constant. After deliberately remounting a
  camera, run `tools/coreg_multiple.py DIR --position-changed --no-drift-check`, or re-seed
  with `tools/seed_registration_cache.py` (which does not use this gate).

A rejected solve is not cached and the capture uses the previous cached transform (or, with
none, the rejected solve for that capture only). The verdict is in the capture's
`coregistration_metadata.json` under `image_info.registration_solve`
(`accepted`, `reasons`, `metric`, `drift_px`, `used`). `tools/registration_orientation.py`
then never adopts a pose for that capture (`no_reference`, nothing sent).

The NIR band is built from frames undistorted like the registered one (same
`config/camera_calibration.json`). `image_info.undistorted_camera_matrix` in the same file is
the camera matrix of those frames (from `getOptimalNewCameraMatrix`), which is not the
calibrated `K`.

## A capture with no thermal frame

`flir()` runs the Lepton capture, and if it fails (the failure resets the Lepton) runs it once
more. With still no frame it writes `thermal: "missing"` to `capture_status.json` in the
capture directory, and then:

- co-registration builds the TIFFs from RGB and NIR with an all-zero thermal band (no
  registration, no cache change);
- the segmentation step asks the daemon for `rgb_only`. The daemon runs only a model that
  declares `bands=red,green,blue` (so a thermal-free model never sees the placeholder band)
  and refuses any other, in which case segmentation is skipped, the cold-load fallback is
  not tried, `capture_status.json` gets `segmentation: "skipped_no_thermal"`, and there is
  no mask, so `camera_water_pct` is absent and the bitmap empty. An RGB-only run is recorded
  as `segmentation: "rgb_only"`.

The graph's deadline for the thermal step is 80 s (was 30 s) so the retry fits.

## Housekeeping

- `tools/data_retention.py` removes whole capture directories (and loose photos at the top
  of `images/`), judged by the newest file in each, under the same age and free-space rules.
- A failed `get_time` directory creation uses `images/<timestamp>-fallback`, created fresh,
  never a shared `images/fallback`, so outputs from an earlier failure cannot be resent.
- `data/metadata_log.txt` is in `config/logrotate-watercam.conf`.
- The segformer daemon hashes its model in the background after the socket is serving, and
  `segformer_daemon.service` waits up to 60 s for the socket.
- Flood flags (`tools/flood_baseline.py`) need two captures in a row over their condition,
  and ignore frames darker than 40 or brighter than 225 (mean grey level).
- A home position (`tools/gps_home.py`) is learned only from fixes whose reported error is at
  most 15 m, at least 30 s apart.
