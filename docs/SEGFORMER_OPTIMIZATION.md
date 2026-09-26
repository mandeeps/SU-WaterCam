# SegFormer 5-Band Performance Optimization

## Current Architecture

### Inference Pipeline

**As of 2026-09-26** `ticktalk_main.segformer()` sends the request to the resident daemon over
`/run/segformer/segformer.sock` and only falls back to a subprocess if the socket is absent. Both
paths are fed `color_preserved_5_band.tiff` and both write
`color_preserved_5_band_segmentation.png`. Measured on node ufo-01-01-005: **2.2 s** via the
daemon, **39.8 s** via the fallback.

The original design, which the strategies below were written against, invoked a fresh subprocess
every wake cycle:

```python
# the historical call
subprocess.Popen(
    ["/home/pi/miniforge3/envs/5band/bin/python",
     "/home/pi/segformer_5band/segment_tiff_5band.py",
     filepath + "/final_5_band.tiff"],
    cwd="/home/pi/segformer_5band"
).wait()
```

Each call was synchronous (`.wait()`), so the entire WaterCam pipeline stalled for the duration of
inference — which is what the daemon removed.

The mask name is not a free choice: `segment_tiff_5band.py` takes no output argument and writes
`<input stem>_segmentation.png`, so it follows from the TIFF name.

### Input Assembly

Co-registration (`tools/coreg_multiple.py`) produces the 5-band TIFF the model reads,
`color_preserved_5_band.tiff`:

| Band | Source | Content |
|------|--------|---------|
| 1 (R) | NIR-OFF optical | Red channel |
| 2 (G) | NIR-OFF optical | Green channel |
| 3 (B) | NIR-OFF optical | Blue channel |
| 4 | FLIR Lepton 3.5 | LWIR thermal, normalized 8-bit |
| 5 | NIR-ON red − NIR-OFF red | Near-infrared reflectance difference |

Band numbers here are 1-based; `photo_processing/training/` numbers the same bands 0-based.
Every TIFF written from 2026-09-26 states its own order in a `BAND_ORDER` tag
(`red,green,blue,thermal,nir`), in the same format the exported `.onnx` declares in its `bands`
metadata, so `tools/segformer_daemon.py` compares file against model rather than assuming.

Co-registration also writes `final_5_band.tiff`, which is **B, G, R** at a squashed 512×512 —
OpenCV's native order straight from `cv2.imread`. It is legacy output that nothing reads any more.
An earlier revision of this table described that file rather than this one.
`docs/CAPTURE_PIPELINE_NOTES.md` is the canonical reference.

The optical image at 2592×1944 is downsampled by 50% (`SCALE_PERCENT = 50`,
`MAX_IMAGE_SIZE = 1000`) during co-registration, so the TIFF passed to
SegFormer is approximately **1296×972 pixels**.

### Earlier observation (undated, config not recorded)

| Stage | Duration | Power |
|-------|----------|-------|
| Python interpreter + model load | ~10–20 s | 5.5 W |
| Image preprocessing | ~5–10 s | 5.5 W |
| Forward pass (inference) | ~90–150 s | 7.0–7.5 W |
| Postprocessing + PNG write | ~5–10 s | 5.5 W |
| **Total** | **~120–180 s** | **~6.5–7.5 W avg** |

### Measured on node ufo-01-01-005, 2026-09-24

Measured over Tailscale on an idle node (no capture service running), through the deployed
`SingleFileInference5Band` class with the repository-default B0 config and `iter_100` checkpoint.
**No thermal throttling at any point** (`get_throttled=0x0` throughout, peak 69.6 C). The node is
**overclocked (`arm_freq=2000`, `over_voltage=0`)**, so these do not transfer unchanged to a stock
Pi 4B. Environment: torch 1.7.1, onnxruntime 1.19.2, mmcv 1.3.0, mmseg 0.11.0, 4 torch threads.

Real capture (`lake.tiff`, 1296×972), timing `_inference_single` only, so process and model load
are excluded:

| Path | Inference | vs deployed |
|---|---|---|
| torch fp32 (current deployment) | **27.98 s** | 1.00× |
| onnxruntime fp32 (`segformer_5band_dyn.onnx`) | **1.80 s** | **15.5×** |

Synthetic `1×5×512×1024`, repeated runs:

| Path | n | mean | sd | min | max | vs deployed |
|---|---|---|---|---|---|---|
| torch fp32 | 3 | 46.52 s | 1.14 | 45.22 | 47.35 | 1.00× |
| torch int8 dynamic (qnnpack) | 3 | 40.02 s | 1.09 | 38.90 | 41.08 | 1.16× |
| onnxruntime fp32 | 10 | 2.76 s | 0.03 | 2.74 | 2.84 | 16.86× |

The real-capture numbers are lower than the synthetic ones because the test config applies `Resize`
at `img_scale=(1024, 512)`, so inference runs at 512×683 rather than on the full frame.

**The 90–150 s forward pass in the table above is not reproducible here.** That entry is undated and
does not record which config produced it. This configuration resizes before inference and measures
28 s. Treat **28 s as the current deployed baseline**, and the older figure as describing a
whole-frame path rather than this one.

**torch dynamic INT8 is not worth deploying.** It returns 1.16×, against the 3–4× INT8 is usually
quoted at, because `quantize_dynamic` covers only `nn.Linear` and most of this model's cost sits
elsewhere. INT8 through ONNX Runtime is a different mechanism and is still unmeasured.

**Output fidelity: ONNX is faithful, padding is not.** On the real capture, torch and ONNX argmax
agree on only 94.29% of pixels (water fraction 13.724% against 9.285%). That is a padding effect,
not a conversion error:

- On identical padded input, ONNX matches torch to a max absolute logit difference of **5.111e-06**
  and **99.9997%** argmax agreement.
- torch against itself, unpadded 512×683 against padded 512×704, agrees on only **94.33%** with the
  same water-fraction shift, reproducing the entire gap with ONNX absent. The ONNX path pads width
  to a multiple of 32.
- It is amplified by `iter_100` being barely trained: `|logit(water) − logit(bg)|` has median
  0.3942, with **6.72%** of pixels under 0.05 and 1.40% under 0.01. Decisions that marginal flip in
  bulk under any perturbation.

The padding is worth fixing on its own merits, and a properly trained checkpoint should be far less
sensitive to it.

The deployed model is **SegFormer-B0**, not a larger variant. `segment_tiff_5band.py`
defaults to `local_configs/segformer/B0/segformer.b0.512x512.flood_5band_2cls.65k.test.py`
(`type='mit_b0'`, `pretrained='pretrained/mit_b0.pth'`) with the checkpoint
`work_dirs/segformer.b0.512x512.flood_5band_2cls.65k_huantao/iter_100.pth`, and `work_dirs/`
contains only B0 runs. That checkpoint is 44 MB, which is a B0 model carried with optimizer
state; a B2 checkpoint holds roughly seven times the parameters and would be far larger.

The timing is therefore not explained by model capacity, and the earlier inference of "B2 or
larger" from the timing profile was wrong. The config runs `test_cfg=dict(mode='whole')` at
`img_scale=(1024, 512)` over a 1296×972 TIFF, under CPU PyTorch through mmsegmentation, paying a
cold process start every cycle. Input resolution and framework overhead account for the gap, which
is why pre-resizing is Strategy 1 below.

One residual uncertainty: this is the repository default, and the checkpoint actually sitting in
`/home/pi/segformer_5band/` on a deployed node has not been inspected. If a node is reachable,
confirm there.

---

## Bottleneck Analysis

### 1. Cold Process Start (10–20 s per cycle)

Every invocation of `segment_tiff_5band.py` pays the full cost of:
- Python interpreter startup
- Conda environment activation and import resolution
- PyTorch initialization (~3–5 s on ARM)
- Model checkpoint loading from microSD (~5–10 s for B2: 84 MB on-disk)
- GPU/CPU device setup

This overhead is fixed regardless of image size or model variant.

### 2. Unoptimized PyTorch Inference (~90–150 s)

PyTorch on ARM Cortex-A72 (Pi 4B):
- No JIT compilation by default
- Operations execute sequentially through PyTorch's Python dispatcher
- Memory layout (NCHW) is not optimal for ARM NEON SIMD
- Float32 throughout — no half-precision or integer acceleration

### 3. Oversized Input Resolution (1296×972)

SegFormer's MiT encoder uses overlapping patch embeddings on the input.
Processing 1296×972 pixels creates a feature map ~4× larger than the
512×512 resolution the model was likely trained on. The excess resolution
does not improve accuracy but multiplies compute proportionally.

### 4. Subprocess Communication Overhead

The orchestration layer and the inference script share no state — they
communicate only via files on microSD. Each cycle writes a TIFF (~15–25 MB
uncompressed for 5-band 1296×972), which is read back immediately by the
inference script. The SD card I/O adds latency and wear.

---

## Optimization Strategies

### Strategy 1: ONNX Export + ONNX Runtime (Highest Impact, No Hardware Change)

**Expected speedup: 2–4× over PyTorch baseline**
**Expected inference time: 40–75 s (B2) or 8–20 s (B0)**

ONNX Runtime for ARM64 uses ahead-of-time graph optimization and
platform-specific kernel selection (MLAS on ARM, which uses NEON intrinsics
directly). Unlike PyTorch, it does not incur Python dispatch overhead per
operator.

#### Export the model (run once on any machine with PyTorch):

```python
import torch
from transformers import SegformerForSemanticSegmentation

model = SegformerForSemanticSegmentation.from_pretrained(
    "/home/pi/segformer_5band/checkpoint"
)
model.eval()

# Match the 5-band input: batch=1, channels=5, H=512, W=512 (inference resolution)
dummy = torch.randn(1, 5, 512, 512)

torch.onnx.export(
    model,
    dummy,
    "segformer_5band.onnx",
    input_names=["pixel_values"],
    output_names=["logits"],
    dynamic_axes={
        "pixel_values": {0: "batch", 2: "height", 3: "width"},
        "logits": {0: "batch", 2: "height", 3: "width"},
    },
    opset_version=17,
)
```

#### Run inference with ONNX Runtime on the Pi:

```bash
pip install onnxruntime  # arm64 wheel available from PyPI
```

```python
import onnxruntime as ort
import numpy as np
import rasterio
from PIL import Image

sess_opts = ort.SessionOptions()
sess_opts.intra_op_num_threads = 4  # use all Pi 4B cores
sess_opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

session = ort.InferenceSession(
    "segformer_5band.onnx",
    sess_options=sess_opts,
    providers=["CPUExecutionProvider"],
)

with rasterio.open("color_preserved_5_band.tiff") as src:
    img = src.read().astype(np.float32)  # (5, H, W)

# Resize to training resolution before inference
from torchvision.transforms.functional import resize
import torch
img_t = torch.from_numpy(img)
img_t = resize(img_t, [512, 512])  # antialias resize outside the model

# Normalize per-band to [0, 1]
for i in range(5):
    img_t[i] = (img_t[i] - img_t[i].min()) / (img_t[i].max() - img_t[i].min() + 1e-8)

input_array = img_t.unsqueeze(0).numpy()  # (1, 5, 512, 512)
logits = session.run(["logits"], {"pixel_values": input_array})[0]

pred = np.argmax(logits[0], axis=0).astype(np.uint8)
Image.fromarray(pred).save("color_preserved_5_band_segmentation.png")
```

---

### Strategy 2: INT8 Static Quantization (Multiplies ONNX Gains)

**Expected additional speedup over ONNX FP32: 2–3×**
**Expected total inference time from baseline: 15–30 s (B2), 4–8 s (B0)**

ONNX Runtime's quantization tool converts FP32 weights and activations to
INT8. ARM Cortex-A72 supports 8-bit SIMD natively; this maps directly to
vectorized NEON operations that run ~3× faster than FP32 NEON.

Static quantization requires a small calibration dataset (~50–100 images
representative of deployment scenes — flood vs. dry land images).

```bash
pip install onnxruntime onnx
python -m onnxruntime.quantization.preprocess --input segformer_5band.onnx --output segformer_5band_prep.onnx
```

```python
from onnxruntime.quantization import quantize_static, CalibrationDataReader, QuantType
import numpy as np

class WaterCamCalibrationReader(CalibrationDataReader):
    def __init__(self, calibration_tiffs):
        self.images = calibration_tiffs
        self.idx = 0

    def get_next(self):
        if self.idx >= len(self.images):
            return None
        img = load_and_preprocess(self.images[self.idx])  # same logic as inference
        self.idx += 1
        return {"pixel_values": img}

quantize_static(
    model_input="segformer_5band_prep.onnx",
    model_output="segformer_5band_int8.onnx",
    calibration_data_reader=WaterCamCalibrationReader(calibration_tiffs),
    quant_type=QuantType.QInt8,
    per_channel=True,
)
```

**Accuracy note**: INT8 quantization typically reduces mIoU by 0.5–2% on
well-calibrated data. For flood segmentation with clear class boundaries
(water vs. land), this degradation is acceptable. Validate on held-out
captures before deploying.

---

### Strategy 3: Model Variant Reduction

**SegFormer variant comparison on ARM (estimated with ONNX FP32):**

| Variant | Parameters | FLOPs (512×512) | Est. Pi 4B ONNX FP32 | Est. Pi 4B ONNX INT8 |
|---------|------------|------------------|-----------------------|-----------------------|
| B5 | 82M | 79.6G | ~90–120 s | ~30–40 s |
| B2 | 25M | 62.4G | ~40–60 s | ~15–25 s |
| B1 | 14M | 15.9G | ~20–30 s | ~8–12 s |
| **B0** | **3.7M** | **8.4G** | **~10–18 s** | **~4–7 s** |

FLOPs are from the original SegFormer paper (Xie et al. 2021). Pi 4B
estimates are extrapolated from Cortex-A72 GFLOPS benchmarks.

If the deployment model is currently B2 or B5, **retraining with B0** is
the single highest-impact change for inference speed. B0's MiT-B0 encoder
uses 4 transformer stages with [32, 64, 160, 256] channel widths vs B2's
[64, 128, 320, 512], reducing both parameter count and memory bandwidth.

For a 5-class flood segmentation problem (e.g., water/dry_land/vegetation/
infrastructure/shadow), B0 is likely sufficient — the task does not require
ImageNet-scale feature diversity.

---

### Strategy 4: Persistent Inference Daemon (Eliminates Cold-Start Cost)

**Expected savings: 10–20 s per cycle (fixed overhead elimination)**

Instead of spawning a new Python process each wake cycle, run SegFormer as
a long-lived daemon that accepts inference requests via a Unix domain socket
or a named pipe. The model stays loaded in RAM for the duration of the Pi's
active window.

#### Daemon implementation sketch:

```python
# /home/pi/segformer_5band/segformer_daemon.py
import socket, os, sys, json
import numpy as np
import onnxruntime as ort

MODEL_PATH = "/home/pi/segformer_5band/segformer_5band_int8.onnx"
SOCKET_PATH = "/tmp/segformer.sock"

session = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
print("Model loaded, ready", flush=True)

if os.path.exists(SOCKET_PATH):
    os.unlink(SOCKET_PATH)

server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(SOCKET_PATH)
server.listen(1)

while True:
    conn, _ = server.accept()
    data = b""
    while chunk := conn.recv(4096):
        data += chunk
    req = json.loads(data.decode())

    tiff_path = req["tiff_path"]
    output_path = req["output_path"]

    # run inference (load → infer → save)
    result = run_inference(session, tiff_path)
    save_result(result, output_path)

    conn.sendall(b"done")
    conn.close()
```

```python
# In ticktalk_main.py — replace subprocess.Popen with:
import socket, json

def call_segformer_daemon(tiff_path, output_path):
    req = json.dumps({"tiff_path": tiff_path, "output_path": output_path})
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect("/tmp/segformer.sock")
        s.sendall(req.encode())
        s.shutdown(socket.SHUT_WR)
        s.recv(64)  # wait for "done"
```

The daemon is started once by the systemd service (`ticktalk.service`) and
stays alive for the entire wake window. With 3 iterations per wake
(`shutdown_iteration_limit=3`), this saves ~30–60 s per wake event.

---

### Strategy 5: Pre-resize Before the TIFF Write

**Withdrawn 2026-09-26. Do not implement as written.**

> Resizing the capture to a square 512×512 destroys the 4:3 aspect ratio, and the mask is
> georeferenced downstream from IMU pose, so a distorted mask distorts the georeferencing —
> silently, since every intermediate still has the shape it should. The deployed graph is now
> exported with **dynamic height and width**, and `tools/segformer_daemon.py` rescales the capture
> keeping its aspect ratio (972×1296 → 512×683), pads to a multiple of 32, runs, then crops the
> padding back off and resizes the logits to source resolution. That gets the inference-resolution
> saving this strategy was after without squaring the frame.
>
> The file-size half of the argument also no longer applies to anything live: it proposed resizing
> `final_5_band.tiff`, which is legacy output nothing reads. `color_preserved_5_band.tiff` stays at
> native resolution deliberately — it is what the annotator draws masks on.
>
> Retained below as written, for the record.

**Original text — expected savings: 20–40% of inference time (input resolution reduction)**

The co-registration pipeline writes a 1296×972 TIFF but SegFormer inference
should operate at 512×512 or 640×640. Moving the resize to before the TIFF
write avoids the model internally upsampling patch embeddings from an
oversized input.

In `tools/coreg_multiple.py`, `save_color_preserved_tiff()` (line 466):

```python
# Current: writes at full co-registration resolution (~1296×972)
# Add resize before stacking bands:

TARGET_H, TARGET_W = 512, 512

def save_color_preserved_tiff(output_path, rgb, thermal, nir):
    # resize each band to inference resolution
    rgb_r    = cv2.resize(rgb,     (TARGET_W, TARGET_H), interpolation=cv2.INTER_AREA)
    thermal_r = cv2.resize(thermal, (TARGET_W, TARGET_H), interpolation=cv2.INTER_AREA)
    nir_r    = cv2.resize(nir,     (TARGET_W, TARGET_H), interpolation=cv2.INTER_AREA)
    # ... rest of band stacking unchanged
```

This also reduces the TIFF file size from ~15–20 MB to ~1.5–2 MB, cutting
microSD write time by ~10× and reducing the file read time inside the
inference script.

Note: keep the original-resolution `color_preserved_5_band.tiff` and
`registered.jpg` for archival if needed; only resize `final_5_band.tiff`.

---

### Strategy 6: Overlap Co-registration with FLIR Capture

**Expected savings: 15–25 s per cycle (pipeline overlap)**

The current call order in `ttmain()` is sequential:

```
take_two_photos() → flir() → coregistration() → segformer()
```

The FLIR capture (`flir()`) takes ~10–20 s while blocking the CPU. The
optical photos are already captured before FLIR starts, so co-registration
could begin on the optical pair while the FLIR binary runs.

Modified pipeline:

```
take_two_photos()
      ↓
  ┌── flir()             (FLIR binary: ~10–15 s, mostly waiting on subprocess)
  │   coregistration()   (CPU work on optical images: ~20 s)
  └─→ wait for both, then finalize 5-band TIFF with thermal
segformer()
```

This requires extracting the thermal registration step as a separate
finalizer that runs after both branches complete. In TickTalkPython,
this can be modelled with a branching graph topology rather than a linear
chain.

---

### Strategy 7: Hardware Acceleration (Largest Possible Speedup)

The Pi 4B's Cortex-A72 has no neural network accelerator. The options below
require hardware changes.

> **Options B and C are ruled out on power (2026-09-24).** Both need a Pi 5, whose draw is
> unacceptable for a solar and battery field node. **Option A is the only accelerator option still
> consistent with the power budget**, since the Coral is a USB device on the existing Pi 4B, though
> its effort remains high because it needs quantization-aware retraining. With ONNX fp32 already
> measured at 15.5× on the Pi 4B, none of this section is on the critical path.

#### Option A: Google Coral USB Edge TPU

- **Hardware**: Coral USB Accelerator (~$60), plugs into Pi 4B USB 3.0
- **Performance**: 4 TOPS INT8; SegFormer-B0 at 512×512 estimated **1–3 s**
- **Requirements**: model must be fully quantized INT8 and compiled with the
  Coral `edgetpu_compiler`. Transformer attention layers may not map well to
  the Edge TPU; convolutional fallback to CPU is possible for unsupported ops.
- **Effort**: high — requires retraining with QAT (quantization-aware training)
  and verifying that enough ops can be delegated. The Coral runtime is
  separate from ONNX Runtime.

#### Option B: Raspberry Pi 5 + Hailo-8L HAT — RULED OUT

> **Project decision, 2026-09-24: not pursuing Hailo.** The Hailo-8L HAT requires a Pi 5, and the
> Pi 5 draws substantially more power than the Pi 4B. That is disqualifying for a
> solar and battery powered field node, independent of how fast the accelerator is.

Retained for reference only:

- **Hardware**: Pi 5 + Hailo-8L AI HAT (~$70); 13 TOPS NPU
- **Performance**: **2–5 s** fully delegated. That figure was estimated for a B2 model.
- **Requirements**: export to Hailo Dataflow Compiler format (`.hef`).
- **Power**: the figures previously recorded here (Pi 5 idle ~3 W against Pi 4B ~2.7 W, Hailo-8L
  active ~2.5 W) were never checked against this deployment's actual power budget, and the
  conclusion drawn from them, that net energy per cycle would be much lower, rested on active time
  dropping 30–40×. That premise is gone: **ONNX fp32 on the existing Pi 4B already measures 15.5×**
  (27.98 s → 1.80 s), so the accelerator's remaining margin is a fraction of what this section
  assumed when it called Hailo the best long-term path.

#### Option C: Raspberry Pi 5 CPU Only (No Accelerator) — RULED OUT

> **Same objection as Option B.** This is still a Pi 5, so the power draw that rules out Hailo
> rules this out too. Recorded here because the decision was stated in terms of Hailo; if the
> concern is ever specifically the HAT rather than the Pi 5, this option can come back.

Retained for reference only:

- **Performance**: Cortex-A76 at 2.4 GHz is ~2–3× faster than the Pi 4B's Cortex-A72 for ML
  workloads. SegFormer-B0 ONNX INT8 estimated **2–4 s** — note the Pi 4B already measures 1.80 s
  at ONNX fp32, so this estimate is no longer an improvement on the current path.
- **Power**: ~7–9 W under load, unverified against this deployment's budget.

---

## Combined Optimization Impact Summary

Starting from the **measured** baseline of **27.98 s** on a real capture (Pi 4B, PyTorch fp32,
node ufo-01-01-005, 2026-09-24):

| Optimization | Cumulative Inference Time | Source | Effort |
|---|---|---|---|
| Baseline (PyTorch fp32, resized to 512×683) | 27.98 s | measured | — |
| + ONNX Runtime FP32 | **1.80 s** | measured | Medium |
| + ONNX Runtime INT8 | not measured | — | Medium |
| + Persistent daemon (removes cold start) | see end-to-end table below | measured | Medium |
| ~~+ Pi 5 (no accelerator)~~ | ruled out | power | — |
| ~~+ Pi 5 + Hailo-8L~~ | ruled out | power | — |

What the measurement changes about the plan:

- **ONNX fp32 alone does most of the available work**, taking inference from 28 s to 1.8 s. The
  remaining software optimizations are worth far less than the earlier estimates implied.
- **The 10–20 s cold start is now the dominant per-cycle cost**, several times the 1.8 s of
  inference it wraps. That makes the **persistent daemon the highest-value remaining change**, not
  INT8 quantization.
- **The "pre-resize TIFF to 512×512" row is gone.** The test config already resizes via
  `img_scale=(1024, 512)`, so the model never sees the full frame; pre-resizing saves file I/O and
  one CPU resize, not model compute.
- Rows still marked "not measured" are exactly that. Do not substitute the old estimates for them.

### End to end, including process start and model load (2026-09-24)

The table above times inference alone. This one times a whole segmentation the way a capture cycle
pays for it:

| Path | mean | steady state | vs legacy |
|---|---|---|---|
| Legacy subprocess, torch (running today) | 34.60 s | — | 1.0× |
| Subprocess, ONNX | 9.12 s | — | 3.8× |
| **Persistent daemon, ONNX** | **1.77 s** | **1.66 s** | **19.5×** |

**The cold start measures 7.35 s**, not the 10–20 s recorded elsewhere in this document. It is still
the single largest remaining cost, being roughly four times the 1.8 s of inference it wraps, which
is why the daemon is the change worth making next.

The daemon's output was verified against the subprocess ONNX path at source resolution:
**99.58% pixel agreement**, both (972, 1296), water fraction 9.328% against 9.285%.

> **Two bugs had to be fixed before these numbers could be taken.** `run_inference()` fell back to a
> 512×512 square whenever the ONNX graph had dynamic height and width, which squashed the 4:3
> capture and so distorted the georeferencing computed from the mask; it now reproduces the mmseg
> `img_scale=(1024, 512)` keep-ratio resize, pads to a multiple of 32, crops, and resizes the logits
> back to source resolution before the argmax. Separately, `segformer_preprocess.py` and
> `segformer_daemon.py` used PEP 604 unions (`dict | None`) that **cannot be imported on the node's
> Python 3.9.19**; both now carry `from __future__ import annotations`. The node had been running
> older copies, which is why this had not surfaced.

> **History.** Before 2026-09-23 this table carried a "+ SegFormer-B0 retraining" step credited
> with taking 12–22 s down to 4–8 s. The deployed model was already B0, so that speedup never
> existed and every row below it had inherited it. The estimates that replaced it were withdrawn
> rather than adjusted, and the measurements above now supersede the whole table.

---

## Recommended Implementation Order

1. ~~**Pre-resize `final_5_band.tiff` to 512×512** in `coreg_multiple.py` before writing.~~
   **Withdrawn 2026-09-26** — see Strategy 5. It would square a 4:3 frame whose mask is
   georeferenced from IMU pose, and it targets a file nothing reads. The dynamic-shape export plus
   the daemon's keep-ratio path take the same saving without the distortion.

2. ~~**Export current model to ONNX and benchmark** on the Pi.~~ **Done 2026-09-24**:
   measured **15.5×** on a real capture (27.98 s → 1.80 s) on node ufo-01-01-005, detailed in the
   measured section above. The ONNX path is now the default in `ticktalk_main.py` (2026-09-26) and
   the width padding is fixed — the daemon pads to a multiple of 32 and crops it back off.

3. **INT8 static quantization** of the ONNX model using a calibration set
   of ~50 real captures. Validate mIoU is acceptable on a held-out set.

4. ~~**Implement persistent daemon** (Strategy 4) to eliminate the 10–20 s cold-start per
   cycle.~~ **Done 2026-09-25.** `config/segformer_daemon.service` runs it and
   `ticktalk_main.segformer()` prefers the socket. Measured **19.5×** end to end at the
   segmentation stage (34.60 s → 1.77 s), removing 7.35 s of cold start. On 005 the full
   capture → co-registration → segmentation cycle now completes in **2.2 s** at the segmentation
   step, against 39.8 s if the daemon is stopped and the subprocess fallback takes over.

5. ~~**Retrain or fine-tune with SegFormer-B0** if the current model is B2+.~~
   **Not applicable.** The deployed model is already B0, the smallest variant, so there is no
   capacity reduction left to take. If inference is still too slow after steps 1 to 4, the
   remaining levers are resolution, quantization, and hardware, not a smaller backbone.

6. ~~**Evaluate Pi 5 upgrade** once software optimizations are in place.~~ **Ruled out
   2026-09-24 on power.** Any Pi 5 based path, with or without the Hailo HAT, is off the table for
   a solar and battery node. The remaining headroom on the Pi 4B is the daemon, INT8 through ONNX,
   and, if an accelerator is ever revisited, Option A, which is a Pi 4B USB device rather than a
   board swap.

---

## Notes on the Segformer Repo

The inference script (`/home/pi/segformer_5band/segment_tiff_5band.py`) and
model checkpoint are not version-controlled in this repository. Before
beginning any optimization work:

- Commit the inference script and model config to a branch (or submodule)
  so changes are tracked.
- Record the exact model variant, number of classes, and class mapping in
  a config file or in this document.
- Store a reference set of input TIFFs and expected output PNGs to validate
  that optimized models produce equivalent results.

Without this, it is impossible to validate that optimizations preserve
segmentation quality.
