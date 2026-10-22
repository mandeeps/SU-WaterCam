#!/home/pi/SU-WaterCam/venv/bin/python

"""Multispectral Image Coregistration Script"""
import argparse
import json
import os
import sys
from typing import Optional, Tuple
import SimpleITK as sitk
import cv2
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin

try:
    from tools import capture_status
except ImportError:                      # run as a script from tools/
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools import capture_status

class CoregistrationConfig:
    MAX_IMAGE_SIZE = 1000
    SCALE_PERCENT = 50
    HISTOGRAM_BINS = 50
    LEARNING_RATE = 1.0
    MAX_ITERATIONS = 100
    OVERLAY_ALPHA = 0.5
    OVERLAY_BETA = 0.5
    DEFAULT_TRANSFORM_ORIGIN = (0, 100)
    DEFAULT_TRANSFORM_SCALE = (1, 1)
    MULTI_SCALE_LEVELS = 3
    MULTI_SCALE_SHRINK_FACTORS = [2, 1, 1]
    MULTI_SCALE_SMOOTHING_SIGMAS = [1, 0.5, 0]
    REGISTRATION_METRIC = "mutual_information"
    TRANSFORM_TYPE = "affine"
    ENABLE_MULTI_SCALE = True
    ENABLE_PREPROCESSING = True
    THERMAL_COLORMAP = cv2.COLORMAP_JET
    INVERT_THERMAL = False
    SAVE_TRANSFORM_PARAMETERS = True
    TRANSFORM_CACHE_FILENAME = "registration_transform.json"
    #: sitk serialisation of the same transform. JSON carries only
    #: GetParameters(), which for a CompositeTransform is just its active
    #: component, so the JSON alone cannot round-trip what registration
    #: actually returns.
    TRANSFORM_FILE_FILENAME = "registration_transform.tfm"
    CALIBRATION_FILE = "camera_calibration.json"
    FORCE_RECALCULATE_TRANSFORM = False
    #: A fresh solve is cached (and so reused by every later capture) only if
    #: the final metric is at least this good AND the transform stays within
    #: MAX_TRANSFORM_DRIFT_PX of the one already cached. Mattes mutual
    #: information is negative and more negative is better; a failed solve sits
    #: near 0. Not gated yet: solves on the field units (before the thermal
    #: frame was scaled properly) had a median of about -0.006 and a best of
    #: -0.18, so a threshold needs measuring with the fixed preprocessing (the
    #: "Final metric value" lines). Until then only the drift check applies.
    #: A metric absent from the map is not gated.
    MIN_ACCEPT_METRIC: dict = {}
    #: Largest movement of any point of the (resized) fixed frame between the
    #: cached transform and a new solve. The cameras are bolted together, so the
    #: true transform is a constant; the same scene has solved 100 px apart.
    #: None disables the check (a camera remounted on purpose: --no-drift-check).
    MAX_TRANSFORM_DRIFT_PX = 25.0
    ENABLE_MULTIPLE_STRATEGIES = False
    FAST_MODE = False
    PARALLEL_PROCESSING = False
    ENABLE_MEMORY_OPTIMIZATION = True
    CHUNK_SIZE = 512
    # Resolution written to final_5_band.tiff.
    #
    # The two five-band TIFFs are NOT the same image, and the difference is easy
    # to miss because only one of them is ever looked at:
    #
    #   final_5_band.tiff           B, G, R, thermal, NIR   512x512, aspect squashed
    #   color_preserved_5_band.tiff R, G, B, thermal, NIR   native 972x1296
    #
    # final_5_band carries OpenCV's native channel order straight from
    # cv2.imread; save_color_preserved_tiff() is the one that reverses the
    # optical channels, which is what "color preserved" refers to.
    #
    # **Inference and training both use color_preserved_5_band.tiff.**
    # ticktalk_main.segformer() serves it and photo_processing/export_dataset.py
    # trains on it. final_5_band is kept for backward compatibility with older
    # captures and tooling; do not feed it to a model.
    INFERENCE_HEIGHT = 512
    INFERENCE_WIDTH = 512

    #: The one five-band format every consumer reads. Written by
    #: save_color_preserved_tiff().
    MODEL_INPUT_TIFF = "color_preserved_5_band.tiff"
    #: What segment_tiff_5band.py writes for that input — it names its output
    #: `<input stem>_segmentation.png` and takes no output argument, so this
    #: name follows from MODEL_INPUT_TIFF and cannot be chosen independently.
    #: segformer_5band/batch_segformer.sh already expects this name.
    SEGMENTATION_PNG = "color_preserved_5_band_segmentation.png"
    #: Superseded names. Still written (the TIFF) and still read (the mask), so
    #: captures made before the standardisation stay usable.
    LEGACY_TIFF = "final_5_band.tiff"
    LEGACY_SEGMENTATION_PNG = "final_5_band_segmentation.png"

    #: Band order, written into every TIFF as the BAND_ORDER tag. The format is
    #: the same comma-separated canonical names the exported .onnx carries in
    #: its `bands` metadata (photo_processing/training/models/segformer.py), so
    #: a runtime can compare the two directly instead of parsing prose band
    #: descriptions and guessing.
    BAND_ORDER = ("red", "green", "blue", "thermal", "nir")
    LEGACY_BAND_ORDER = ("blue", "green", "red", "thermal", "nir")

config = CoregistrationConfig()


def segmentation_path(directory: str) -> str:
    """The segmentation mask in `directory`, preferring the standard name.

    Falls back to the superseded name so a directory segmented before the
    standardisation is still readable. Returns the standard path when neither
    exists, so callers report the name they should have been given.
    """
    std = os.path.join(directory, config.SEGMENTATION_PNG)
    if os.path.exists(std):
        return std
    legacy = os.path.join(directory, config.LEGACY_SEGMENTATION_PNG)
    return legacy if os.path.exists(legacy) else std


def _default_calibration_path() -> str:
    """Resolve config/camera_calibration.json relative to the repo root, so calibration works without copying it into every capture directory."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, "config", config.CALIBRATION_FILE)


def _load_intrinsics(calib_path: str, w: int, h: int):
    """(K scaled to w x h, D) from camera_calibration.json, or None when it can't be used."""
    if not calib_path or not os.path.exists(calib_path):
        return None
    try:
        with open(calib_path) as f:
            cal = json.load(f)
        mtx = np.array(cal["K"], dtype=np.float64)
        dist = np.array(cal["D"], dtype=np.float64).reshape(-1, 1)

        # K is only valid at the resolution it was calibrated at: fx, fy, cx and
        # cy are all in pixels. Applied to a differently sized frame it silently
        # mis-centres and mis-scales the correction rather than failing. The
        # distortion coefficients are in normalised coordinates and do not scale.
        cal_w, cal_h = (cal.get("img_size") or [w, h])[:2]
        if (cal_w, cal_h) != (w, h):
            sx, sy = w / float(cal_w), h / float(cal_h)
            if abs(sx - sy) > 1e-3:
                print(f"Warning: calibration {cal_w}x{cal_h} has a different aspect ratio "
                      f"than the {w}x{h} frame; skipping undistort")
                return None
            print(f"Scaling calibration from {cal_w}x{cal_h} to {w}x{h} (factor {sx:.3f})")
            mtx = mtx.copy()
            mtx[0, 0] *= sx; mtx[0, 2] *= sx
            mtx[1, 1] *= sy; mtx[1, 2] *= sy
        return mtx, dist
    except Exception as e:
        print(f"Warning: calibration load failed ({e}), skipping undistort")
        return None


def undistorted_camera_matrix(calib_path: str, w: int, h: int) -> Optional[np.ndarray]:
    """The camera matrix of a frame that went through _undistort_if_calibrated.

    cv2.undistort(..., new_mtx) leaves the pixels described by the matrix from
    getOptimalNewCameraMatrix, not by the calibrated K: with alpha=1 the focal
    length shrinks to keep every source pixel. Anything downstream that needs
    intrinsics for these frames (georeferencing) must use this one.
    """
    loaded = _load_intrinsics(calib_path, w, h)
    if loaded is None:
        return None
    mtx, dist = loaded
    new_mtx, _ = cv2.getOptimalNewCameraMatrix(mtx, dist, (w, h), 1, (w, h))
    return new_mtx


def _undistort_if_calibrated(image: np.ndarray, calib_path: str) -> np.ndarray:
    """Apply lens undistortion using camera_calibration.json if it exists; otherwise return image unchanged.

    Only valid for OV5647 (NIR) frames — the Lepton (LWIR) camera has different optics
    and has no calibration data of its own, so it must never be passed through this.
    """
    h, w = image.shape[:2]
    loaded = _load_intrinsics(calib_path, w, h)
    if loaded is None:
        return image
    mtx, dist = loaded
    try:
        new_mtx, _ = cv2.getOptimalNewCameraMatrix(mtx, dist, (w, h), 1, (w, h))
        return cv2.undistort(image, mtx, dist, None, new_mtx)
    except Exception as e:
        print(f"Warning: undistort failed ({e}), skipping")
        return image


def normalize_image(image: np.ndarray, min_val: float = 0.0, max_val: float = 255.0) -> np.ndarray:
    if image.size == 0:
        return image
    img_min, img_max = image.min(), image.max()
    if img_max > img_min:
        return ((image - img_min) / (img_max - img_min) * (max_val - min_val) + min_val).astype(np.uint8)
    return np.full_like(image, (min_val + max_val) / 2, dtype=np.uint8)

def validate_image_file(image_path: str, expected_extensions: Optional[list] = None) -> bool:
    if not os.path.exists(image_path):
        print(f"Image file does not exist: {image_path}")
        return False
    if expected_extensions and os.path.splitext(image_path)[1].lower() not in expected_extensions:
        print(f"Image file has unexpected extension: {os.path.splitext(image_path)[1].lower()}, expected: {expected_extensions}")
        return False
    try:
        return cv2.imread(image_path, cv2.IMREAD_UNCHANGED) is not None
    except Exception as e:
        print(f"Error reading image file {image_path}: {e}")
        return False


def get_image_info(image_path: str) -> dict:
    try:
        image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
        if image is None:
            return {"error": "Failed to read image"}
        return {
            "path": image_path,
            "shape": image.shape,
            "dtype": str(image.dtype),
            "size_mb": os.path.getsize(image_path) / (1024 * 1024),
            "channels": image.shape[2] if len(image.shape) > 2 else 1,
        }
    except Exception as e:
        return {"error": str(e)}


def validate_input_files(directory: str, allow_missing_thermal: bool = False) -> Tuple[str, str, Optional[str]]:
    if not os.path.exists(directory):
        raise ValueError(f"Directory not found: {directory}")
    if not os.path.isdir(directory):
        raise ValueError(f"Path is not a directory: {directory}")
    nir_off_image: Optional[str] = None
    nir_on_image: Optional[str] = None
    lwir_image: Optional[str] = None
    try:
        files = os.listdir(directory)
    except PermissionError:
        raise ValueError(f"Permission denied accessing directory: {directory}")
    except Exception as e:
        raise ValueError(f"Error reading directory {directory}: {e}")
    for filename in files:
        filepath = os.path.join(directory, filename)
        if filename.endswith("-NIR-OFF.jpg") and validate_image_file(filepath, [".jpg"]):
            nir_off_image = filepath
        elif filename.endswith("-NIR-ON.jpg") and validate_image_file(filepath, [".jpg"]):
            nir_on_image = filepath
        elif filename.endswith(".pgm") and validate_image_file(filepath, [".pgm"]):
            lwir_image = filepath
    missing_files = []
    if not nir_off_image:
        missing_files.append("*-NIR-OFF.jpg")
    if not nir_on_image:
        missing_files.append("*-NIR-ON.jpg")
    if not lwir_image and not allow_missing_thermal:
        missing_files.append("*.pgm")
    if missing_files:
        error_msg = f"Missing required files in {directory}: {', '.join(missing_files)}"
        print(error_msg)
        print(f"Available files: {files}")
        raise ValueError(error_msg)
    assert nir_off_image is not None
    assert nir_on_image is not None
    return nir_off_image, nir_on_image, lwir_image


def get_thermal_colormap_info() -> str:
    colormap_names = {
        cv2.COLORMAP_JET: "JET (Blue-Green-Yellow-Red)",
        cv2.COLORMAP_HOT: "HOT (Black-Red-Yellow-White)",
        cv2.COLORMAP_INFERNO: "INFERNO (Black-Purple-Orange-Yellow)",
        cv2.COLORMAP_VIRIDIS: "VIRIDIS (Blue-Green-Yellow)",
        cv2.COLORMAP_PLASMA: "PLASMA (Purple-Orange-Yellow)",
    }
    colormap_name = colormap_names.get(config.THERMAL_COLORMAP, "Unknown")
    inversion_status = "inverted" if config.INVERT_THERMAL else "not inverted"
    return f"Thermal colormap: {colormap_name}, {inversion_status}"


def save_transform_parameters(transform: sitk.Transform, directory: str, metadata: Optional[dict] = None) -> bool:
    if not config.SAVE_TRANSFORM_PARAMETERS:
        return False
    try:
        parameters = transform.GetParameters()
        fixed_parameters = transform.GetFixedParameters()
        parameters_list = parameters.tolist() if hasattr(parameters, "tolist") else list(parameters)
        fixed_parameters_list = fixed_parameters.tolist() if hasattr(fixed_parameters, "tolist") else list(fixed_parameters)
        transform_data = {
            "transform_type": type(transform).__name__,
            "parameters": parameters_list,
            "fixed_parameters": fixed_parameters_list,
            "timestamp": pd.Timestamp.now().isoformat(),
            "metadata": metadata if metadata is not None else {},
        }
        normed = os.path.normpath(directory)
        parent_directory = os.path.dirname(normed) or normed
        transform_path = os.path.join(parent_directory, config.TRANSFORM_CACHE_FILENAME)
        tfm_path = os.path.join(parent_directory, config.TRANSFORM_FILE_FILENAME)
        try:
            sitk.WriteTransform(transform, tfm_path)
            transform_data["transform_file"] = os.path.basename(tfm_path)
        except Exception as e:                      # noqa: BLE001
            print(f"Could not serialise transform to {tfm_path}: {e}")
        with open(transform_path, "w") as f:
            json.dump(transform_data, f, indent=2)
        return True
    except Exception as e:
        print(f"Failed to save transform parameters: {e}")
        return False


def load_transform_parameters(directory: str) -> Optional[tuple[sitk.Transform, str, Optional[list]]]:
    if config.FORCE_RECALCULATE_TRANSFORM:
        return None
    normed = os.path.normpath(directory)
    parent_directory = os.path.dirname(normed) or normed
    transform_path = os.path.join(parent_directory, config.TRANSFORM_CACHE_FILENAME)
    if not os.path.exists(transform_path):
        transform_path = os.path.join(directory, config.TRANSFORM_CACHE_FILENAME)
        if not os.path.exists(transform_path):
            return None
    try:
        with open(transform_path, "r") as f:
            transform_data = json.load(f)
        transform_type = transform_data["transform_type"]
        parameters = transform_data["parameters"]
        fixed_parameters = transform_data["fixed_parameters"]
        saved_image_size = transform_data.get("metadata", {}).get("image_size")
        #: the TRANSFORM_TYPE config that produced this, not the concrete sitk
        #: class, which is what should be compared against the current config
        saved_config_type = transform_data.get("metadata", {}).get("transform_type")

        tfm_name = transform_data.get("transform_file")
        if tfm_name:
            tfm_path = os.path.join(os.path.dirname(transform_path), tfm_name)
            if os.path.exists(tfm_path):
                return (sitk.ReadTransform(tfm_path), transform_path,
                        saved_image_size, saved_config_type)

        transform_map = {
            "Euler2DTransform": sitk.Euler2DTransform,
            "AffineTransform": lambda: sitk.AffineTransform(2),
            "BSplineTransform": lambda: sitk.BSplineTransform(2, 3),
            "CompositeTransform": lambda: sitk.CompositeTransform(2)
        }
        if transform_type not in transform_map:
            print(f"Unknown transform type: {transform_type}")
            return None
        if transform_type == "CompositeTransform":
            # GetParameters() held only the active component, so this file
            # predates the .tfm companion and cannot be restored faithfully.
            print(f"{transform_path} stores a CompositeTransform without a "
                  f"{config.TRANSFORM_FILE_FILENAME} companion; re-registering once "
                  f"to regenerate it.")
            return None
        transform = transform_map[transform_type]()
        transform.SetParameters(parameters)
        transform.SetFixedParameters(fixed_parameters)
        return (transform, transform_path, saved_image_size, saved_config_type)
    except Exception as e:
        print(f"Failed to load transform parameters: {e}")
        return None


def validate_transform_compatibility(transform: sitk.Transform, fixed_image_path: str, moving_image_path: str, saved_size: Optional[list] = None) -> bool:
    try:
        fixed_image = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
        moving_image = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)
        if fixed_image is None or moving_image is None:
            print("Cannot validate transform - failed to load images")
            return False
        if (fixed_image.shape[0] > config.MAX_IMAGE_SIZE or fixed_image.shape[1] > config.MAX_IMAGE_SIZE):
            width = int(fixed_image.shape[1] * config.SCALE_PERCENT / 100)
            height = int(fixed_image.shape[0] * config.SCALE_PERCENT / 100)
            expected_size = (height, width)
        else:
            expected_size = fixed_image.shape[:2]
        if saved_size is not None:
            saved_hw = tuple(int(x) for x in saved_size)
            if saved_hw != expected_size:
                print(f"Transform invalid: saved for {saved_hw[1]}×{saved_hw[0]}, "
                      f"current images are {expected_size[1]}×{expected_size[0]}")
                return False
        print(f"Transform validation passed for image size: {expected_size}")
        return True
    except Exception as e:
        print(f"Transform validation failed: {e}")
        return False


def apply_cached_transform(fixed_image_path: str, moving_image_path: str, cached_transform: sitk.Transform, temperature_data: Optional[np.ndarray] = None, calibration_file: str = "") -> Tuple[sitk.Transform, np.ndarray, np.ndarray]:
    print("Applying cached transform to images...")
    calib_path = calibration_file or _default_calibration_path()
    fixed_image_cv = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
    moving_image_cv = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)
    if fixed_image_cv is None:
        raise ValueError(f"Could not load fixed image: {fixed_image_path}")
    if moving_image_cv is None:
        raise ValueError(f"Could not load moving image: {moving_image_path}")
    # calib_path holds OV5647 intrinsics only; the moving image is the Lepton (LWIR) frame,
    # which has different optics, so it must not be undistorted with this calibration.
    fixed_image_cv = _undistort_if_calibrated(fixed_image_cv, calib_path)
    if (fixed_image_cv.shape[0] > config.MAX_IMAGE_SIZE or fixed_image_cv.shape[1] > config.MAX_IMAGE_SIZE):
        print(f"Resizing large images by {config.SCALE_PERCENT}%")
        width = int(fixed_image_cv.shape[1] * config.SCALE_PERCENT / 100)
        height = int(fixed_image_cv.shape[0] * config.SCALE_PERCENT / 100)
        dim = (width, height)
        fixed_image_cv_resized = cv2.resize(fixed_image_cv, dim)
        moving_image_cv_resized = cv2.resize(moving_image_cv, dim)
    else:
        moving_image_cv_resized = cv2.resize(moving_image_cv, (fixed_image_cv.shape[1], fixed_image_cv.shape[0]))
        fixed_image_cv_resized = fixed_image_cv
    # sitk.GetImageFromArray treats a 3-channel (H, W, 3) array as a 3D volume, not a 2D
    # image, which breaks Resample against the 2D cached_transform. Collapse to a single
    # band first, matching how preprocess_image_for_registration() feeds the fresh path.
    fixed_image_gray = cv2.cvtColor(fixed_image_cv_resized, cv2.COLOR_BGR2GRAY) if fixed_image_cv_resized.ndim == 3 else fixed_image_cv_resized
    moving_image_gray = moving_image_cv_resized[:, :, 0] if moving_image_cv_resized.ndim == 3 else moving_image_cv_resized
    fixed_image_sitk = sitk.GetImageFromArray(fixed_image_gray.astype(np.float32))
    moving_image_sitk = sitk.GetImageFromArray(moving_image_gray.astype(np.float32))
    moving_resampled = sitk.Resample(moving_image_sitk, fixed_image_sitk, cached_transform, sitk.sitkLinear, 0.0, moving_image_sitk.GetPixelID())
    moving_resampled_np = sitk.GetArrayFromImage(moving_resampled)
    moving_resampled_np = normalize_image(moving_resampled_np)
    if config.INVERT_THERMAL:
        moving_resampled_np = 255 - moving_resampled_np
    moving_resampled_colored = cv2.applyColorMap(moving_resampled_np.astype(np.uint8), config.THERMAL_COLORMAP)
    overlay = fixed_image_cv_resized.astype(np.float32)
    output = moving_resampled_colored.astype(np.float32)
    cv2.addWeighted(overlay, config.OVERLAY_ALPHA, output, config.OVERLAY_BETA, 0, output)
    four_band_with_thermal = np.dstack((overlay, moving_resampled_np))
    output = np.clip(output, 0, 255).astype(np.uint8)
    return cached_transform, output, four_band_with_thermal


def to_uint8(image: np.ndarray, low_pct: float = 1.0, high_pct: float = 99.0) -> np.ndarray:
    """An 8-bit view of `image` that keeps its contrast.

    uint8 passes through. Anything wider is stretched between robust percentiles
    (a lone hot pixel must not set the scale) and clipped. A plain
    `astype(np.uint8)` wraps modulo 256, which is what the Lepton's PGM does to
    the registration input whenever its span exceeds 255: tools/capture.c
    writes maxval = max - min, so almost every real frame is 16-bit.
    """
    if image.dtype == np.uint8:
        return image
    data = image.astype(np.float32)
    if data.size == 0:
        return data.astype(np.uint8)
    lo, hi = np.percentile(data, [low_pct, high_pct])
    if hi <= lo:
        lo, hi = float(data.min()), float(data.max())
    if hi <= lo:
        return np.full(image.shape, 128, dtype=np.uint8)
    return np.clip((data - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)


def preprocess_image_for_registration(image: np.ndarray, image_type: str = "optical") -> np.ndarray:
    if not config.ENABLE_PREPROCESSING:
        return image
    if len(image.shape) == 3:
        if image_type == "optical":
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image[:, :, 0] if image.shape[2] > 1 else image
    else:
        gray = image
    gray = to_uint8(gray)
    if config.FAST_MODE:
        gray = cv2.equalizeHist(gray)
    else:
        gray = cv2.equalizeHist(gray)
        gray = cv2.GaussianBlur(gray, (3, 3), 0.5)
        kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
        gray = cv2.filter2D(gray, -1, kernel)
    return gray.astype(np.float32)


def create_transform(transform_type: str) -> sitk.Transform:
    transform_map = {
        "rigid": sitk.Euler2DTransform,
        "affine": lambda: sitk.AffineTransform(2),
        "bspline": lambda: sitk.BSplineTransform(2, 3)
    }
    return transform_map.get(transform_type, sitk.Euler2DTransform)()


def set_registration_metric(registration_method: sitk.ImageRegistrationMethod, metric_type: str) -> None:
    metric_map = {
        "mutual_information": lambda: registration_method.SetMetricAsMattesMutualInformation(numberOfHistogramBins=config.HISTOGRAM_BINS),
        "mean_squares": registration_method.SetMetricAsMeanSquares,
        "correlation": registration_method.SetMetricAsCorrelation
    }
    metric_map.get(metric_type, lambda: registration_method.SetMetricAsMattesMutualInformation(numberOfHistogramBins=config.HISTOGRAM_BINS))()


def estimate_initial_transform(fixed_image: sitk.Image, moving_image: sitk.Image, transform_type: str) -> sitk.Transform:
    transform = create_transform(transform_type)
    initializers = [
        sitk.CenteredTransformInitializerFilter.GEOMETRY,
        sitk.CenteredTransformInitializerFilter.MOMENTS
    ]
    for initializer in initializers:
        try:
            return sitk.CenteredTransformInitializer(fixed_image, moving_image, transform, initializer)
        except Exception as e:
            print(f"Transform initializer failed: {e}")
    return transform


def try_multiple_registration_strategies(fixed_image_path: str, moving_image_path: str, directory: str = "", position_changed: bool = False, moving_image_data: Optional[np.ndarray] = None, calibration_file: str = "") -> Tuple[sitk.Transform, np.ndarray, np.ndarray]:
    if not config.ENABLE_MULTIPLE_STRATEGIES:
        # Using single best strategy
        try:
            original_transform = config.TRANSFORM_TYPE
            original_metric = config.REGISTRATION_METRIC
            config.TRANSFORM_TYPE = "affine"
            config.REGISTRATION_METRIC = "mutual_information"
            transform, output, four_band = mutual_information_registration(fixed_image_path, moving_image_path, directory, position_changed, moving_image_data, calibration_file)
            config.TRANSFORM_TYPE = original_transform
            config.REGISTRATION_METRIC = original_metric
            return transform, output, four_band
        except Exception as e:
            print(f"Best strategy failed: {e}")
            config.TRANSFORM_TYPE = original_transform
            config.REGISTRATION_METRIC = original_metric
            raise
    strategies = [{"transform": "affine", "metric": "mutual_information"}, {"transform": "rigid", "metric": "mutual_information"}, {"transform": "affine", "metric": "mean_squares"}, {"transform": "rigid", "metric": "mean_squares"}]
    best_metric_value = float("inf")
    best_result = None
    best_strategy = None
    # Trying multiple registration strategies
    for i, strategy in enumerate(strategies):
        print(f"Strategy {i+1}/{len(strategies)}: {strategy['transform']} transform with {strategy['metric']} metric")
        try:
            original_transform = config.TRANSFORM_TYPE
            original_metric = config.REGISTRATION_METRIC
            config.TRANSFORM_TYPE = strategy["transform"]
            config.REGISTRATION_METRIC = strategy["metric"]
            transform, output, four_band = mutual_information_registration(fixed_image_path, moving_image_path, directory, position_changed, moving_image_data, calibration_file)
            config.TRANSFORM_TYPE = original_transform
            config.REGISTRATION_METRIC = original_metric
            metric_value = 0
            if strategy["transform"] == "affine":
                metric_value -= 1
            if metric_value < best_metric_value:
                best_metric_value = metric_value
                best_result = (transform, output, four_band)
                best_strategy = strategy
                # New best strategy found
        except Exception as e:
            print(f"Strategy {strategy} failed: {e}")
            continue
    if best_result is None:
        print("All registration strategies failed")
        raise ValueError("All registration strategies failed")
    # Best strategy selected
    return best_result


#: What the last fresh solve decided, for coreg() to record in the capture's
#: coregistration_metadata.json. Reset by mutual_information_registration.
last_solve: dict = {}


def transform_drift_px(a: sitk.Transform, b: sitk.Transform, width: int, height: int, n: int = 5) -> float:
    """Largest distance, in pixels, between where two transforms send a grid of points."""
    worst = 0.0
    for y in np.linspace(0, height - 1, n):
        for x in np.linspace(0, width - 1, n):
            pa, pb = a.TransformPoint((float(x), float(y))), b.TransformPoint((float(x), float(y)))
            worst = max(worst, float(np.hypot(pa[0] - pb[0], pa[1] - pb[1])))
    return worst


def judge_solve(metric_value: float, solved: sitk.Transform, previous: Optional[sitk.Transform], width: int, height: int) -> dict:
    """Decide whether a fresh solve may be cached; see MIN_ACCEPT_METRIC and MAX_TRANSFORM_DRIFT_PX."""
    verdict = {"accepted": True, "reasons": [], "metric": float(metric_value), "drift_px": None}
    limit = config.MIN_ACCEPT_METRIC.get(config.REGISTRATION_METRIC)
    if not np.isfinite(metric_value):
        verdict["reasons"].append(f"metric {metric_value} is not a number")
    elif limit is not None and not metric_value <= limit:
        verdict["reasons"].append(f"metric {metric_value:.4f} is worse than {limit}")
    if previous is not None and config.MAX_TRANSFORM_DRIFT_PX is not None:
        try:
            drift = transform_drift_px(previous, solved, width, height)
            verdict["drift_px"] = round(drift, 1)
            if drift > config.MAX_TRANSFORM_DRIFT_PX:
                verdict["reasons"].append(f"moved {drift:.1f} px from the cached transform (limit {config.MAX_TRANSFORM_DRIFT_PX})")
        except Exception as e:
            print(f"Could not compare with the cached transform: {e}")
    verdict["accepted"] = not verdict["reasons"]
    return verdict


def mutual_information_registration(fixed_image_path: str, moving_image_path: str, directory: str = "", position_changed: bool = False, moving_image_data: Optional[np.ndarray] = None, calibration_file: str = "") -> Tuple[sitk.Transform, np.ndarray, np.ndarray]:
    last_solve.clear()
    if directory and not position_changed:
        cached = load_transform_parameters(directory)
        if cached is not None:
            cached_transform, loaded_path, saved_size, saved_config_type = cached
            # Compare against the TRANSFORM_TYPE that produced the cache, not the
            # concrete sitk class: registration returns a CompositeTransform even
            # when TRANSFORM_TYPE is "affine", so comparing classes rejected every
            # cache entry and the transform was re-solved on every capture.
            if (
                (saved_config_type is None or saved_config_type == config.TRANSFORM_TYPE)
                and cached_transform.GetDimension() == 2
                and validate_transform_compatibility(cached_transform, fixed_image_path, moving_image_path, saved_size)
            ):
                print(f"Using cached transform parameters from {loaded_path}")
                return apply_cached_transform(fixed_image_path, moving_image_path, cached_transform, calibration_file=calibration_file)
            else:
                print(f"Cached transform from {loaded_path} is not compatible with current config. Will generate a new transform.")
    elif position_changed:
        print("Position changed flag is True - forcing new registration")
    calib_path = calibration_file or _default_calibration_path()
    fixed_image_cv = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
    moving_image_cv = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)
    if fixed_image_cv is None:
        raise ValueError(f"Could not load fixed image: {fixed_image_path}")
    if moving_image_cv is None:
        raise ValueError(f"Could not load moving image: {moving_image_path}")
    # calib_path holds OV5647 intrinsics only; the moving image is the Lepton (LWIR) frame,
    # which has different optics, so it must not be undistorted with this calibration.
    fixed_image_cv = _undistort_if_calibrated(fixed_image_cv, calib_path)
    original_fixed_size = fixed_image_cv.shape[:2]
    if (fixed_image_cv.shape[0] > config.MAX_IMAGE_SIZE or fixed_image_cv.shape[1] > config.MAX_IMAGE_SIZE):
        print(f"Resizing large images by {config.SCALE_PERCENT}%")
        width = int(fixed_image_cv.shape[1] * config.SCALE_PERCENT / 100)
        height = int(fixed_image_cv.shape[0] * config.SCALE_PERCENT / 100)
        dim = (width, height)
        fixed_image_cv_resized = cv2.resize(fixed_image_cv, dim)
        moving_image_cv_resized = cv2.resize(moving_image_cv, dim)
    else:
        moving_image_cv_resized = cv2.resize(moving_image_cv, (fixed_image_cv.shape[1], fixed_image_cv.shape[0]))
        fixed_image_cv_resized = fixed_image_cv
    # Using visual thermal image for registration
    moving_image_for_registration = moving_image_cv_resized
    original_thermal_image = moving_image_cv_resized
    # Preprocessing images for registration
    fixed_preprocessed = preprocess_image_for_registration(fixed_image_cv_resized, "optical")
    moving_preprocessed = preprocess_image_for_registration(moving_image_for_registration, "thermal")
    fixed_image_sitk = sitk.GetImageFromArray(fixed_preprocessed.astype(np.float32))
    moving_image_sitk = sitk.GetImageFromArray(moving_preprocessed.astype(np.float32))
    if fixed_image_sitk.GetPixelID() != moving_image_sitk.GetPixelID():
        moving_image_sitk = sitk.Cast(moving_image_sitk, fixed_image_sitk.GetPixelID())
    registration_method = sitk.ImageRegistrationMethod()
    set_registration_metric(registration_method, config.REGISTRATION_METRIC)
    registration_method.SetInterpolator(sitk.sitkLinear)
    if config.FAST_MODE:
        registration_method.SetOptimizerAsGradientDescent(learningRate=config.LEARNING_RATE * 2, numberOfIterations=config.MAX_ITERATIONS // 2, convergenceMinimumValue=1e-4, convergenceWindowSize=5)
    else:
        registration_method.SetOptimizerAsGradientDescent(learningRate=config.LEARNING_RATE, numberOfIterations=config.MAX_ITERATIONS, convergenceMinimumValue=1e-6, convergenceWindowSize=10)
    registration_method.SetOptimizerScalesFromPhysicalShift()
    initial_transform = estimate_initial_transform(fixed_image_sitk, moving_image_sitk, config.TRANSFORM_TYPE)
    registration_method.SetInitialTransform(initial_transform, inPlace=True)
    if config.ENABLE_MULTI_SCALE:
        if config.FAST_MODE:
            # Performing fast multi-scale registration
            fast_shrink_factors = [2, 1]
            fast_smoothing_sigmas = [1, 0]
            registration_method.SetShrinkFactorsPerLevel(fast_shrink_factors)
            registration_method.SetSmoothingSigmasPerLevel(fast_smoothing_sigmas)
        else:
            # Performing multi-scale registration
            registration_method.SetShrinkFactorsPerLevel(config.MULTI_SCALE_SHRINK_FACTORS)
            registration_method.SetSmoothingSigmasPerLevel(config.MULTI_SCALE_SMOOTHING_SIGMAS)
        registration_method.SmoothingSigmasAreSpecifiedInPhysicalUnitsOff()
    # Starting registration
    final_transform = registration_method.Execute(sitk.Cast(fixed_image_sitk, sitk.sitkFloat32), sitk.Cast(moving_image_sitk, sitk.sitkFloat32))
    print(f"Final metric value: {registration_method.GetMetricValue()}")
    print(f"Optimizer's stopping condition: {registration_method.GetOptimizerStopConditionDescription()}")
    if hasattr(final_transform, "GetParameters"):
        params = final_transform.GetParameters()
        print(f"Transform parameters: {params}")
    use_transform = final_transform
    if directory:
        height, width = fixed_image_cv_resized.shape[:2]
        previous = None
        cached = load_transform_parameters(directory)
        if cached is not None and cached[0].GetDimension() == 2 and validate_transform_compatibility(cached[0], fixed_image_path, moving_image_path, cached[2]):
            previous = cached[0]
        verdict = judge_solve(registration_method.GetMetricValue(), final_transform, previous, width, height)
        last_solve.update(verdict)
        if verdict["accepted"]:
            metadata = {"fixed_image": os.path.basename(fixed_image_path), "moving_image": os.path.basename(moving_image_path), "transform_type": config.TRANSFORM_TYPE, "metric": config.REGISTRATION_METRIC, "metric_value": verdict["metric"], "image_size": fixed_image_cv_resized.shape[:2]}
            save_transform_parameters(final_transform, directory, metadata)
            print(f"Registration accepted and cached (metric {verdict['metric']:.4f}, drift {verdict['drift_px']} px)")
        else:
            print(f"Registration REJECTED, not cached: {'; '.join(verdict['reasons'])}")
            if previous is not None:
                print("Using the previous cached transform for this capture")
                last_solve["used"] = "previous_cached"
                return apply_cached_transform(fixed_image_path, moving_image_path, previous, calibration_file=calibration_file)
            print("No previous cached transform: using the rejected solve for this capture only")
            last_solve["used"] = "rejected_solve"
    original_thermal_sitk = sitk.GetImageFromArray(original_thermal_image.astype(np.float32))
    moving_resampled = sitk.Resample(original_thermal_sitk, fixed_image_sitk, final_transform, sitk.sitkLinear, 0.0, original_thermal_sitk.GetPixelID())
    moving_resampled_np = sitk.GetArrayFromImage(moving_resampled)
    moving_resampled_np = normalize_image(moving_resampled_np)
    if config.INVERT_THERMAL:
        moving_resampled_np = 255 - moving_resampled_np
    moving_resampled_colored = cv2.applyColorMap(moving_resampled_np.astype(np.uint8), config.THERMAL_COLORMAP)
    overlay = fixed_image_cv_resized.astype(np.float32)
    output = moving_resampled_colored.astype(np.float32)
    cv2.addWeighted(overlay, config.OVERLAY_ALPHA, output, config.OVERLAY_BETA, 0, output)
    four_band_with_thermal = np.dstack((overlay, moving_resampled_np))
    output = np.clip(output, 0, 255).astype(np.uint8)
    return final_transform, output, four_band_with_thermal


def rgb_only_registration(fixed_image_path: str, calibration_file: str = "") -> Tuple[None, np.ndarray, np.ndarray]:
    """Stand-in for registration when the capture has no thermal frame.

    Same undistortion and resizing as the registered path, with an all-zero
    thermal band, so the TIFFs keep their five-band layout. Only a model that
    reads the RGB bands alone may be run on the result (tools/segformer_daemon.py
    refuses anything else when asked for rgb_only).
    """
    fixed = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
    if fixed is None:
        raise ValueError(f"Could not load fixed image: {fixed_image_path}")
    fixed = _undistort_if_calibrated(fixed, calibration_file or _default_calibration_path())
    if fixed.shape[0] > config.MAX_IMAGE_SIZE or fixed.shape[1] > config.MAX_IMAGE_SIZE:
        fixed = cv2.resize(fixed, (int(fixed.shape[1] * config.SCALE_PERCENT / 100), int(fixed.shape[0] * config.SCALE_PERCENT / 100)))
    overlay = fixed.astype(np.float32)
    thermal = np.zeros(fixed.shape[:2], dtype=np.uint8)
    return None, np.clip(fixed, 0, 255).astype(np.uint8), np.dstack((overlay, thermal))


def extract_nir_band(nir_on_path: str, nir_off_path: str, target_size: Tuple[int, int], calibration_file: str = "") -> np.ndarray:
    nir_on = cv2.imread(nir_on_path)
    nir_off = cv2.imread(nir_off_path)
    if nir_on is None:
        raise ValueError(f"Could not load NIR-ON image: {nir_on_path}")
    if nir_off is None:
        raise ValueError(f"Could not load NIR-OFF image: {nir_off_path}")
    # Same undistortion as the frame the other bands are registered to
    # (the NIR-OFF image in registration), so the NIR band lines up with them.
    # Both frames come from the same OV5647, so the same calibration applies.
    calib_path = calibration_file or _default_calibration_path()
    nir_on = _undistort_if_calibrated(nir_on, calib_path)
    nir_off = _undistort_if_calibrated(nir_off, calib_path)
    nir_on_rgb = cv2.cvtColor(nir_on, cv2.COLOR_BGR2RGB)
    nir_off_rgb = cv2.cvtColor(nir_off, cv2.COLOR_BGR2RGB)
    red_channel_nir = nir_on_rgb[:, :, 0]
    red_channel_off = nir_off_rgb[:, :, 0]
    nir_band = cv2.subtract(red_channel_nir, red_channel_off)
    nir_band_resized = cv2.resize(nir_band, target_size)
    return nir_band_resized


#: Human-readable band descriptions, keyed by the canonical name used in
#: BAND_ORDER. Both TIFF writers use these so the prose and the machine-readable
#: tag can never drift apart.
_BAND_DESCRIPTIONS = {
    "red": "Red Channel (Optical)",
    "green": "Green Channel (Optical)",
    "blue": "Blue Channel (Optical)",
    "thermal": "Thermal Data (Normalized 0-255)",
    "nir": "NIR Band (NIR-ON minus NIR-OFF)",
}


def save_multiband_tiff(image_data: np.ndarray, output_path: str, transform_params: Tuple = config.DEFAULT_TRANSFORM_ORIGIN + config.DEFAULT_TRANSFORM_SCALE) -> None:
    """Write final_5_band.tiff — **legacy output, no longer read by anything.**

    Kept so older captures and external tooling keep working. It is B,G,R at a
    squashed 512x512; `save_color_preserved_tiff` writes the file the model and
    the annotator actually use. Until now this wrote no band descriptions and no
    tags at all, which is exactly how a file this easy to confuse should not be
    written, so it now states its own band order.
    """
    transform = from_origin(*transform_params)
    with rasterio.open(output_path, "w", driver="GTiff", height=image_data.shape[1], width=image_data.shape[2], count=image_data.shape[0], dtype=image_data.dtype, transform=transform) as dst:
        dst.write(image_data)
        if image_data.shape[0] == len(config.LEGACY_BAND_ORDER):
            for i, name in enumerate(config.LEGACY_BAND_ORDER, 1):
                dst.set_band_description(i, _BAND_DESCRIPTIONS[name])
            dst.update_tags(
                BAND_ORDER=",".join(config.LEGACY_BAND_ORDER),
                BAND_ORDER_NOTE="Legacy output, no longer read by any UFONet software. "
                                "B,G,R at a squashed 512x512 — OpenCV's native channel "
                                "order. Use color_preserved_5_band.tiff instead.",
                CREATOR="Coregistration Script",
            )
    print(f"Saved multiband TIFF: {output_path}")


def save_color_preserved_tiff(rgb_data: np.ndarray, thermal_data: np.ndarray, nir_data: np.ndarray, output_path: str, transform_params: Tuple = config.DEFAULT_TRANSFORM_ORIGIN + config.DEFAULT_TRANSFORM_SCALE) -> None:
    transform = from_origin(*transform_params)
    rgb_uint8 = np.clip(rgb_data, 0, 255).astype(np.uint8)
    thermal_uint8 = np.clip(thermal_data, 0, 255).astype(np.uint8)
    nir_uint8 = np.clip(nir_data, 0, 255).astype(np.uint8)
    # rgb_data arrives BGR from cv2.imread; [2],[1],[0] is what makes this file
    # R,G,B — the "color preserved" in the name. Must stay in step with
    # config.BAND_ORDER.
    stacked_data = np.stack([rgb_uint8[:, :, 2], rgb_uint8[:, :, 1], rgb_uint8[:, :, 0], thermal_uint8, nir_uint8], axis=0)
    with rasterio.open(output_path, "w", driver="GTiff", height=stacked_data.shape[1], width=stacked_data.shape[2], count=stacked_data.shape[0], dtype=stacked_data.dtype, transform=transform, photometric="rgb", compress="lzw") as dst:
        dst.write(stacked_data)
        for i, name in enumerate(config.BAND_ORDER, 1):
            dst.set_band_description(i, _BAND_DESCRIPTIONS[name])
        # BAND_ORDER is the machine-readable one. The descriptions above are
        # prose a reader has to keyword-match; this is the same string format
        # the exported .onnx declares in its `bands` metadata, so a runtime can
        # compare the file against the model directly.
        dst.update_tags(
            BAND_ORDER=",".join(config.BAND_ORDER),
            CREATOR="Coregistration Script",
            DESCRIPTION="Color-preserved multispectral data with RGB, Thermal, and NIR bands",
            BAND_COUNT=str(stacked_data.shape[0]),
            DATA_SOURCES="Optical (RGB), Thermal (LWIR), NIR (NIR-ON/NIR-OFF difference)")
    print(f"Saved color-preserved TIFF: {output_path}")


def save_metadata_summary(directory: str, output_paths: dict, image_info: dict) -> None:
    metadata_file = os.path.join(directory, "coregistration_metadata.json")
    processing_parameters = {
        "max_image_size": config.MAX_IMAGE_SIZE,
        "scale_percent": config.SCALE_PERCENT,
        "registration_metric": config.REGISTRATION_METRIC,
        "transform_type": config.TRANSFORM_TYPE,
        "thermal_colormap": str(config.THERMAL_COLORMAP),
        "invert_thermal": config.INVERT_THERMAL,
        "inference_height": config.INFERENCE_HEIGHT,
        "inference_width": config.INFERENCE_WIDTH,
    }
    file_info = {
        "final_5_band.tiff": {
            "resolution": f"{config.INFERENCE_WIDTH}×{config.INFERENCE_HEIGHT}",
            "note": "resized for SegFormer inference",
        },
        "color_preserved_5_band.tiff": {
            "resolution": "full co-registration resolution",
            "note": "archival",
        },
    }
    band_descriptions = {
        "final_5_band.tiff": [
            "Band 1: Blue Channel (Optical)",
            "Band 2: Green Channel (Optical)",
            "Band 3: Red Channel (Optical)",
            "Band 4: Thermal Data (Normalized 0-255)",
            "Band 5: NIR Band (NIR-ON minus NIR-OFF)",
        ],
        "color_preserved_5_band.tiff": [
            "Band 1: Red Channel (Optical)",
            "Band 2: Green Channel (Optical)",
            "Band 3: Blue Channel (Optical)",
            "Band 4: Thermal Data (Normalized 0-255)",
            "Band 5: NIR Band (NIR-ON minus NIR-OFF)",
        ],
    }
    metadata = {
        "timestamp": pd.Timestamp.now().isoformat(),
        "output_files": output_paths,
        "image_info": image_info,
        "processing_parameters": processing_parameters,
        "file_info": file_info,
        "band_descriptions": band_descriptions,
    }
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Saved metadata summary: {metadata_file}")


def _intrinsics_for(image_path: str, calibration_file: str = ""):
    """The camera matrix of `image_path` after undistortion, as nested lists, or None."""
    try:
        frame = cv2.imread(image_path)
        if frame is None:
            return None
        h, w = frame.shape[:2]
        k = undistorted_camera_matrix(calibration_file or _default_calibration_path(), w, h)
        return None if k is None else [[float(v) for v in row] for row in k]
    except Exception:
        return None


def apply_thread_setting() -> Optional[int]:
    """Run SimpleITK single-threaded when COREG_SINGLE_THREAD=1 (bench units only).

    A fresh registration isn't repeatable on several threads: ITK's mutual-information
    metric sums per-thread partial results in whatever order the threads finish, and
    100 optimizer iterations amplify the floating-point differences (2-9% of pixels
    between two runs on 005, 2026-10-12). There is no random seed involved: no metric
    sampling is set, so every pixel is used. On one thread the same capture gives the
    same output every time, which is what bench comparisons and regression tests need,
    at about twice the time per fresh registration (~160 s vs ~72 s on 005). Cached
    transforms are repeatable either way. Deployed units leave this unset.
    Returns the thread count it set, or None.
    """
    if os.environ.get("COREG_SINGLE_THREAD", "").strip().lower() in ("1", "true", "yes"):
        sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(1)
        return 1
    return None


def coreg(directory: str, position_changed: bool = False, calibration_file: str = "") -> str:
    try:
        last_solve.clear()
        if apply_thread_setting():
            print("Co-registration: SimpleITK single-threaded (COREG_SINGLE_THREAD=1)")
        nir_off_path, nir_on_path, lwir_path = validate_input_files(directory, allow_missing_thermal=True)
        lwir_normalized = None
        if lwir_path is None:
            print("⚠️ No thermal frame in this capture: building the RGB and NIR bands only (thermal band zero)")
            capture_status.mark(directory, thermal="missing")
        else:
            lwir_image = cv2.imread(lwir_path, cv2.IMREAD_UNCHANGED)
            if lwir_image is None:
                raise RuntimeError(f"Could not load LWIR image: {lwir_path}")
            lwir_normalized = normalize_image(lwir_image, 0, 255)
        output_filenames = {"registered": "registered.jpg", "nir_band": "NIR_band.png", "five_band": "final_5_band.tiff", "color_preserved": "color_preserved_5_band.tiff", "metadata": "coregistration_metadata.json"}
        output_paths = {name: os.path.join(directory, filename) for name, filename in output_filenames.items()}
        five_band_stale = False
        if os.path.exists(output_paths["five_band"]):
            try:
                with rasterio.open(output_paths["five_band"]) as _src:
                    if _src.height != config.INFERENCE_HEIGHT or _src.width != config.INFERENCE_WIDTH:
                        print(f"Regenerating: final_5_band.tiff is {_src.width}×{_src.height}, expected {config.INFERENCE_WIDTH}×{config.INFERENCE_HEIGHT}")
                        five_band_stale = True
            except Exception:
                five_band_stale = True
        if five_band_stale or not all(os.path.exists(path) for path in [output_paths["five_band"], output_paths["color_preserved"], output_paths["nir_band"], output_paths["metadata"]]):
            # Performing image registration
            print(get_thermal_colormap_info())
            try:
                if lwir_path is None:
                    transform, output, four_band = rgb_only_registration(nir_off_path, calibration_file)
                else:
                    transform, output, four_band = try_multiple_registration_strategies(nir_off_path, lwir_path, directory, position_changed, lwir_normalized, calibration_file)
                cv2.imwrite(output_paths["registered"], output)
                # Registered output saved
            except Exception as e:
                raise RuntimeError(f"Registration failed: {e}")
            # Extracting NIR band
            try:
                width, height = four_band.shape[1], four_band.shape[0]
                nir_band = extract_nir_band(nir_on_path, nir_off_path, (width, height), calibration_file)
                cv2.imwrite(output_paths["nir_band"], nir_band)
                # NIR band saved
            except Exception as e:
                raise RuntimeError(f"Failed to extract/save NIR band: {e}")
            # Creating five-band image
            try:
                final_five_band = np.dstack((four_band, nir_band))
                final_five_band = np.clip(final_five_band, 0, 255).astype(np.uint8)
                final_five_band_reordered = np.transpose(final_five_band, (2, 0, 1))

                # Resize each band to inference resolution before writing.
                # This avoids the model processing an oversized input (~1296×972)
                # when it was trained at 512×512, cutting inference time ~40%.
                # color_preserved_5_band.tiff retains full resolution for archival.
                infer_wh = (config.INFERENCE_WIDTH, config.INFERENCE_HEIGHT)
                inference_bands = np.stack([
                    cv2.resize(final_five_band_reordered[i], infer_wh, interpolation=cv2.INTER_AREA)
                    for i in range(final_five_band_reordered.shape[0])
                ], axis=0)
                save_multiband_tiff(inference_bands, output_paths["five_band"])
                # Five-band GeoTIFF saved
            except Exception as e:
                raise RuntimeError(f"Failed to create/save five-band image: {e}")
            # Creating color-preserved version
            try:
                rgb_data = four_band[:, :, :3]
                thermal_data = four_band[:, :, 3]
                save_color_preserved_tiff(rgb_data, thermal_data, nir_band, output_paths["color_preserved"])
                # Color-preserved TIFF saved
            except Exception as e:
                raise RuntimeError(f"Failed to create/save color-preserved image: {e}")
            # Saving metadata summary
            try:
                image_info = {"nir_off": get_image_info(nir_off_path), "nir_on": get_image_info(nir_on_path), "lwir": get_image_info(lwir_path) if lwir_path else None, "output_shape": four_band.shape, "registration_transform": list(transform.GetParameters()) if hasattr(transform, "GetParameters") else None, "registration_solve": dict(last_solve) or None, "undistorted_camera_matrix": _intrinsics_for(nir_off_path, calibration_file)}
                save_metadata_summary(directory, output_paths, image_info)
                # Metadata summary saved
            except Exception as e:
                print(f"Failed to save metadata summary: {e}")

            print("Coregistration completed successfully!")
            return directory
        else:
            print("All output files already exist. Skipping processing.")
    except ValueError:
        raise
    except Exception as e:
        print(f"Unexpected error during coregistration: {str(e)}")
        raise RuntimeError(f"Coregistration failed: {str(e)}")


def main():
    parser = argparse.ArgumentParser(description="Multispectral Image Coregistration Script")
    parser.add_argument("directory", help="Path to directory containing input images")
    parser.add_argument("--position-changed", action="store_true", help="Force new registration even if cached transform exists")
    parser.add_argument("--fast", action="store_true", help="Enable fast mode with reduced iterations and preprocessing")
    parser.add_argument("--single-strategy", action="store_true", help="Use only the best registration strategy (faster)")
    parser.add_argument("--multiple-strategies", action="store_true", help="Try multiple registration strategies (slower but potentially better)")
    parser.add_argument("--no-drift-check", action="store_true", help="Cache a new solve even if it moved far from the cached transform (camera remounted on purpose)")
    parser.add_argument("--calibration-file", default="", help="Path to camera_calibration.json (default: config/camera_calibration.json in repo root)")
    args = parser.parse_args()
    directory = args.directory
    position_changed = args.position_changed
    calibration_file = args.calibration_file
    if args.no_drift_check:
        config.MAX_TRANSFORM_DRIFT_PX = None
    if args.fast:
        config.FAST_MODE = True
        print("Fast mode enabled")
    if args.single_strategy:
        config.ENABLE_MULTIPLE_STRATEGIES = False
        print("Single strategy mode enabled")
    elif args.multiple_strategies:
        config.ENABLE_MULTIPLE_STRATEGIES = True
        print("Multiple strategies mode enabled")
    print(f"Running coregistration on files in directory: {directory}")
    if position_changed:
        print("Position changed flag is set - will force new registration")
    try:
        coreg(directory, position_changed, calibration_file)
        print("Coregistration completed successfully!")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
