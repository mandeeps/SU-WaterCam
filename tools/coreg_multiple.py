#!/usr/bin/env python

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
    FORCE_RECALCULATE_TRANSFORM = False
    ENABLE_MULTIPLE_STRATEGIES = False
    FAST_MODE = False
    PARALLEL_PROCESSING = False
    ENABLE_MEMORY_OPTIMIZATION = True
    CHUNK_SIZE = 512

config = CoregistrationConfig()

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


def validate_input_files(directory: str) -> Tuple[str, str, str]:
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
    if not lwir_image:
        missing_files.append("*.pgm")
    if missing_files:
        error_msg = f"Missing required files in {directory}: {', '.join(missing_files)}"
        print(error_msg)
        print(f"Available files: {files}")
        raise ValueError(error_msg)
    assert nir_off_image is not None
    assert nir_on_image is not None
    assert lwir_image is not None
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
        parent_directory = os.path.dirname(directory) or directory
        transform_path = os.path.join(parent_directory, config.TRANSFORM_CACHE_FILENAME)
        with open(transform_path, "w") as f:
            json.dump(transform_data, f, indent=2)
        return True
    except Exception as e:
        print(f"Failed to save transform parameters: {e}")
        return False


def load_transform_parameters(directory: str) -> Optional[tuple[sitk.Transform, str]]:
    if config.FORCE_RECALCULATE_TRANSFORM:
        return None
    parent_directory = os.path.dirname(directory) or directory
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
        transform_map = {
            "Euler2DTransform": sitk.Euler2DTransform,
            "AffineTransform": lambda: sitk.AffineTransform(2),
            "BSplineTransform": lambda: sitk.BSplineTransform(2, 3),
            "CompositeTransform": lambda: sitk.CompositeTransform(2)
        }
        if transform_type not in transform_map:
            print(f"Unknown transform type: {transform_type}")
            return None
        transform = transform_map[transform_type]()
        transform.SetParameters(parameters)
        transform.SetFixedParameters(fixed_parameters)
        return (transform, transform_path)
    except Exception as e:
        print(f"Failed to load transform parameters: {e}")
        return None


def validate_transform_compatibility(transform: sitk.Transform, fixed_image_path: str, moving_image_path: str) -> bool:
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
        print(f"Transform validation passed for image size: {expected_size}")
        return True
    except Exception as e:
        print(f"Transform validation failed: {e}")
        return False


def apply_cached_transform(fixed_image_path: str, moving_image_path: str, cached_transform: sitk.Transform, temperature_data: Optional[np.ndarray] = None) -> Tuple[sitk.Transform, np.ndarray, np.ndarray]:
    print("Applying cached transform to images...")
    fixed_image_cv = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
    moving_image_cv = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)
    if fixed_image_cv is None:
        raise ValueError(f"Could not load fixed image: {fixed_image_path}")
    if moving_image_cv is None:
        raise ValueError(f"Could not load moving image: {moving_image_path}")
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
    fixed_image_sitk = sitk.GetImageFromArray(fixed_image_cv_resized.astype(np.float32))
    moving_image_sitk = sitk.GetImageFromArray(moving_image_cv_resized.astype(np.float32))
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
    if config.FAST_MODE:
        gray = cv2.equalizeHist(gray.astype(np.uint8))
    else:
        gray = cv2.equalizeHist(gray.astype(np.uint8))
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


def try_multiple_registration_strategies(fixed_image_path: str, moving_image_path: str, directory: str = "", position_changed: bool = False, moving_image_data: Optional[np.ndarray] = None) -> Tuple[sitk.Transform, np.ndarray, np.ndarray]:
    if not config.ENABLE_MULTIPLE_STRATEGIES:
        # Using single best strategy
        try:
            original_transform = config.TRANSFORM_TYPE
            original_metric = config.REGISTRATION_METRIC
            config.TRANSFORM_TYPE = "affine"
            config.REGISTRATION_METRIC = "mutual_information"
            transform, output, four_band = mutual_information_registration(fixed_image_path, moving_image_path, directory, position_changed, moving_image_data)
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
            transform, output, four_band = mutual_information_registration(fixed_image_path, moving_image_path, directory, position_changed, moving_image_data)
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


def mutual_information_registration(fixed_image_path: str, moving_image_path: str, directory: str = "", position_changed: bool = False, moving_image_data: Optional[np.ndarray] = None) -> Tuple[sitk.Transform, np.ndarray, np.ndarray]:
    if directory and not position_changed:
        cached = load_transform_parameters(directory)
        if cached is not None:
            cached_transform, loaded_path = cached
            # Check transform type and parameter length
            expected_transform = create_transform(config.TRANSFORM_TYPE)
            if (
                type(cached_transform) == type(expected_transform)
                and len(cached_transform.GetParameters()) == len(expected_transform.GetParameters())
                and validate_transform_compatibility(cached_transform, fixed_image_path, moving_image_path)
            ):
                print(f"Using cached transform parameters from {loaded_path}")
                return apply_cached_transform(fixed_image_path, moving_image_path, cached_transform)
            else:
                print(f"Cached transform from {loaded_path} is not compatible with current config. Will generate a new transform.")
    elif position_changed:
        print("Position changed flag is True - forcing new registration")
    fixed_image_cv = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
    moving_image_cv = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)
    if fixed_image_cv is None:
        raise ValueError(f"Could not load fixed image: {fixed_image_path}")
    if moving_image_cv is None:
        raise ValueError(f"Could not load moving image: {moving_image_path}")
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
    registration_method.SetInitialTransform(initial_transform, inPlace=False)
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
    if directory:
        metadata = {"fixed_image": os.path.basename(fixed_image_path), "moving_image": os.path.basename(moving_image_path), "transform_type": config.TRANSFORM_TYPE, "metric": config.REGISTRATION_METRIC, "image_size": fixed_image_cv_resized.shape[:2]}
        save_transform_parameters(final_transform, directory, metadata)
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


def extract_nir_band(nir_on_path: str, nir_off_path: str, target_size: Tuple[int, int]) -> np.ndarray:
    nir_on = cv2.imread(nir_on_path)
    nir_off = cv2.imread(nir_off_path)
    if nir_on is None:
        raise ValueError(f"Could not load NIR-ON image: {nir_on_path}")
    if nir_off is None:
        raise ValueError(f"Could not load NIR-OFF image: {nir_off_path}")
    nir_on_rgb = cv2.cvtColor(nir_on, cv2.COLOR_BGR2RGB)
    nir_off_rgb = cv2.cvtColor(nir_off, cv2.COLOR_BGR2RGB)
    red_channel_nir = nir_on_rgb[:, :, 0]
    red_channel_off = nir_off_rgb[:, :, 0]
    nir_band = cv2.subtract(red_channel_nir, red_channel_off)
    nir_band_resized = cv2.resize(nir_band, target_size)
    return nir_band_resized


def save_multiband_tiff(image_data: np.ndarray, output_path: str, transform_params: Tuple = config.DEFAULT_TRANSFORM_ORIGIN + config.DEFAULT_TRANSFORM_SCALE) -> None:
    transform = from_origin(*transform_params)
    with rasterio.open(output_path, "w", driver="GTiff", height=image_data.shape[1], width=image_data.shape[2], count=image_data.shape[0], dtype=image_data.dtype, transform=transform) as dst:
        dst.write(image_data)
    print(f"Saved multiband TIFF: {output_path}")


def save_color_preserved_tiff(rgb_data: np.ndarray, thermal_data: np.ndarray, nir_data: np.ndarray, output_path: str, transform_params: Tuple = config.DEFAULT_TRANSFORM_ORIGIN + config.DEFAULT_TRANSFORM_SCALE) -> None:
    transform = from_origin(*transform_params)
    rgb_uint8 = np.clip(rgb_data, 0, 255).astype(np.uint8)
    thermal_uint8 = np.clip(thermal_data, 0, 255).astype(np.uint8)
    nir_uint8 = np.clip(nir_data, 0, 255).astype(np.uint8)
    stacked_data = np.stack([rgb_uint8[:, :, 2], rgb_uint8[:, :, 1], rgb_uint8[:, :, 0], thermal_uint8, nir_uint8], axis=0)
    with rasterio.open(output_path, "w", driver="GTiff", height=stacked_data.shape[1], width=stacked_data.shape[2], count=stacked_data.shape[0], dtype=stacked_data.dtype, transform=transform, photometric="rgb", compress="lzw") as dst:
        dst.write(stacked_data)
        band_descriptions = ["Red Channel (Optical)", "Green Channel (Optical)", "Blue Channel (Optical)", "Thermal Data (Normalized 0-255)", "NIR Band (NIR-ON minus NIR-OFF)"]
        for i, description in enumerate(band_descriptions, 1):
            dst.set_band_description(i, description)
        dst.update_tags(CREATOR="Coregistration Script", DESCRIPTION="Color-preserved multispectral data with RGB, Thermal, and NIR bands", BAND_COUNT=str(stacked_data.shape[0]), DATA_SOURCES="Optical (RGB), Thermal (LWIR), NIR (NIR-ON/NIR-OFF difference)")
    print(f"Saved color-preserved TIFF: {output_path}")


def save_metadata_summary(directory: str, output_paths: dict, image_info: dict) -> None:
    metadata_file = os.path.join(directory, "coregistration_metadata.json")
    metadata = {"timestamp": pd.Timestamp.now().isoformat(), "output_files": output_paths, "image_info": image_info, "processing_parameters": {"max_image_size": config.MAX_IMAGE_SIZE, "scale_percent": config.SCALE_PERCENT, "registration_metric": config.REGISTRATION_METRIC, "transform_type": config.TRANSFORM_TYPE, "thermal_colormap": str(config.THERMAL_COLORMAP), "invert_thermal": config.INVERT_THERMAL}, "band_descriptions": {"final_5_band.tiff": ["Band 1: Blue Channel (Optical)", "Band 2: Green Channel (Optical)", "Band 3: Red Channel (Optical)", "Band 4: Thermal Data (Normalized 0-255)", "Band 5: NIR Band (NIR-ON minus NIR-OFF)"], "color_preserved_5_band.tiff": ["Band 1: Red Channel (Optical)", "Band 2: Green Channel (Optical)", "Band 3: Blue Channel (Optical)", "Band 4: Thermal Data (Normalized 0-255)", "Band 5: NIR Band (NIR-ON minus NIR-OFF)"]}}
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Saved metadata summary: {metadata_file}")


def coreg(directory: str, position_changed: bool = False) -> str:
    try:
        nir_off_path, nir_on_path, lwir_path = validate_input_files(directory)
        # Loading LWIR image
        lwir_image = cv2.imread(lwir_path, cv2.IMREAD_UNCHANGED)
        if lwir_image is None:
            raise RuntimeError(f"Could not load LWIR image: {lwir_path}")
        lwir_normalized = normalize_image(lwir_image, 0, 255)
        output_filenames = {"registered": "registered.jpg", "nir_band": "NIR_band.png", "five_band": "final_5_band.tiff", "color_preserved": "color_preserved_5_band.tiff", "metadata": "coregistration_metadata.json"}
        output_paths = {name: os.path.join(directory, filename) for name, filename in output_filenames.items()}
        if not all(os.path.exists(path) for path in [output_paths["five_band"], output_paths["color_preserved"], output_paths["nir_band"], output_paths["metadata"]]):
            # Performing image registration
            print(get_thermal_colormap_info())
            try:
                transform, output, four_band = try_multiple_registration_strategies(nir_off_path, lwir_path, directory, position_changed, lwir_normalized)
                cv2.imwrite(output_paths["registered"], output)
                # Registered output saved
            except Exception as e:
                raise RuntimeError(f"Registration failed: {e}")
            # Extracting NIR band
            try:
                width, height = four_band.shape[1], four_band.shape[0]
                nir_band = extract_nir_band(nir_on_path, nir_off_path, (width, height))
                cv2.imwrite(output_paths["nir_band"], nir_band)
                # NIR band saved
            except Exception as e:
                raise RuntimeError(f"Failed to extract/save NIR band: {e}")
            # Creating five-band image
            try:
                final_five_band = np.dstack((four_band, nir_band))
                final_five_band = np.clip(final_five_band, 0, 255).astype(np.uint8)
                final_five_band_reordered = np.transpose(final_five_band, (2, 0, 1))
                save_multiband_tiff(final_five_band_reordered, output_paths["five_band"])
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
                image_info = {"nir_off": get_image_info(nir_off_path), "nir_on": get_image_info(nir_on_path), "lwir": get_image_info(lwir_path), "output_shape": four_band.shape, "registration_transform": list(transform.GetParameters()) if hasattr(transform, "GetParameters") else None}
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
    args = parser.parse_args()
    directory = args.directory
    position_changed = args.position_changed
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
        coreg(directory, position_changed)
        print("Coregistration completed successfully!")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
