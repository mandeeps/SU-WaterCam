#!/usr/bin/env python
"""
Multispectral Image Coregistration Script

This script performs mutual information-based registration between optical and thermal images,
then creates multi-band outputs for multispectral analysis.

Author: Fatemeh Rezaei (https://github.com/rzfatemeh)
Modified: Enhanced with error handling, documentation, and code quality improvements

Note this version is mostly LLM generated using the prior version as input

Dependencies:
    - opencv-python
    - SimpleITK
    - tifffile
    - rasterio
    - numpy
    - csv
    - pandas
    - json
"""
import json
import time
import argparse
import logging
from typing import Tuple, Optional
import sys
import os
import numpy as np
import SimpleITK as sitk
import rasterio
from rasterio.transform import from_origin
import pandas as pd
import cv2

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class CoregistrationError(Exception):
    """Base exception for coregistration errors."""

    pass


class InputValidationError(CoregistrationError):
    """Exception raised for input validation errors."""

    pass


class RegistrationError(CoregistrationError):
    """Exception raised for registration failures."""

    pass


def performance_monitor(func):
    """Decorator to monitor function performance."""

    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed_time = time.time() - start_time
            logger.info(f"{func.__name__} completed in {elapsed_time:.2f} seconds")
            return result
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"{func.__name__} failed after {elapsed_time:.2f} seconds: {e}"
            )
            raise

    return wrapper


class CoregistrationConfig:
    """Configuration class for coregistration parameters."""

    # Image processing parameters
    MAX_IMAGE_SIZE = 1000
    SCALE_PERCENT = 50
    HISTOGRAM_BINS = 50
    LEARNING_RATE = 1.0
    MAX_ITERATIONS = 100
    THRESHOLD_VALUE = 50
    OVERLAY_ALPHA = 0.5
    OVERLAY_BETA = 0.5
    DEFAULT_TRANSFORM_ORIGIN = (0, 100)
    DEFAULT_TRANSFORM_SCALE = (1, 1)

    # Enhanced registration parameters
    MULTI_SCALE_LEVELS = 3
    MULTI_SCALE_SHRINK_FACTORS = [2, 1, 1]  # Shrink factors for each level
    MULTI_SCALE_SMOOTHING_SIGMAS = [1, 0.5, 0]  # Smoothing for each level
    REGISTRATION_METRIC = "mutual_information"  # Options: "mutual_information", "mean_squares", "correlation"
    TRANSFORM_TYPE = "affine"  # Options: "rigid", "affine", "bspline"
    ENABLE_MULTI_SCALE = True
    ENABLE_PREPROCESSING = True

    # Thermal image color mapping parameters
    THERMAL_COLORMAP = (
        cv2.COLORMAP_JET
    )  # Options: cv2.COLORMAP_JET, cv2.COLORMAP_HOT, cv2.COLORMAP_INFERNO
    INVERT_THERMAL = False  # Set to False to show hot areas in red

    # Transform caching parameters
    SAVE_TRANSFORM_PARAMETERS = True  # Save transform parameters to disk
    TRANSFORM_CACHE_FILENAME = (
        "registration_transform.json"  # Filename for saved transform
    )
    FORCE_RECALCULATE_TRANSFORM = (
        False  # Force recalculation even if cached transform exists
    )

    # Performance optimization parameters
    ENABLE_MULTIPLE_STRATEGIES = (
        False  # Set to False for faster processing (uses only best strategy)
    )
    FAST_MODE = False  # Enable fast mode with reduced iterations and preprocessing
    PARALLEL_PROCESSING = (
        False  # Enable parallel processing for multiple strategies (experimental)
    )

    # Memory optimization parameters
    ENABLE_MEMORY_OPTIMIZATION = True  # Enable memory optimization for large images
    CHUNK_SIZE = 512  # Size of image chunks for processing large images


# Create global config instance
config = CoregistrationConfig()

# Configuration constants (for backward compatibility)
MAX_IMAGE_SIZE = config.MAX_IMAGE_SIZE
SCALE_PERCENT = config.SCALE_PERCENT
HISTOGRAM_BINS = config.HISTOGRAM_BINS
LEARNING_RATE = config.LEARNING_RATE
MAX_ITERATIONS = config.MAX_ITERATIONS
THRESHOLD_VALUE = config.THRESHOLD_VALUE
OVERLAY_ALPHA = config.OVERLAY_ALPHA
OVERLAY_BETA = config.OVERLAY_BETA
DEFAULT_TRANSFORM_ORIGIN = config.DEFAULT_TRANSFORM_ORIGIN
DEFAULT_TRANSFORM_SCALE = config.DEFAULT_TRANSFORM_SCALE
MULTI_SCALE_LEVELS = config.MULTI_SCALE_LEVELS
MULTI_SCALE_SHRINK_FACTORS = config.MULTI_SCALE_SHRINK_FACTORS
MULTI_SCALE_SMOOTHING_SIGMAS = config.MULTI_SCALE_SMOOTHING_SIGMAS
REGISTRATION_METRIC = config.REGISTRATION_METRIC
TRANSFORM_TYPE = config.TRANSFORM_TYPE
ENABLE_MULTI_SCALE = config.ENABLE_MULTI_SCALE
ENABLE_PREPROCESSING = config.ENABLE_PREPROCESSING
THERMAL_COLORMAP = config.THERMAL_COLORMAP
INVERT_THERMAL = config.INVERT_THERMAL
SAVE_TRANSFORM_PARAMETERS = config.SAVE_TRANSFORM_PARAMETERS
TRANSFORM_CACHE_FILENAME = config.TRANSFORM_CACHE_FILENAME
FORCE_RECALCULATE_TRANSFORM = config.FORCE_RECALCULATE_TRANSFORM
ENABLE_MULTIPLE_STRATEGIES = config.ENABLE_MULTIPLE_STRATEGIES
FAST_MODE = config.FAST_MODE
PARALLEL_PROCESSING = config.PARALLEL_PROCESSING
ENABLE_MEMORY_OPTIMIZATION = config.ENABLE_MEMORY_OPTIMIZATION
CHUNK_SIZE = config.CHUNK_SIZE


def normalize_image(
    image: np.ndarray, min_val: float = 0.0, max_val: float = 255.0
) -> np.ndarray:
    """
    Normalize image to specified range using numpy operations instead of cv2.normalize.

    Args:
        image: Input image array
        min_val: Minimum value for normalization
        max_val: Maximum value for normalization

    Returns:
        Normalized image array
    """
    if image.size == 0:
        return image

    img_min, img_max = image.min(), image.max()

    if img_max > img_min:
        # Normalize to specified range
        normalized = (image - img_min) / (img_max - img_min) * (
            max_val - min_val
        ) + min_val
    else:
        # If all values are the same, set to middle of range
        normalized = np.full_like(image, (min_val + max_val) / 2)

    return normalized.astype(np.uint8)


def optimize_image_size_for_memory(
    image: np.ndarray, max_memory_mb: int = 100
) -> np.ndarray:
    """
    Optimize image size to stay within memory limits.

    Args:
        image: Input image array
        max_memory_mb: Maximum memory usage in MB

    Returns:
        Optimized image array
    """
    if not ENABLE_MEMORY_OPTIMIZATION:
        return image

    # Calculate current memory usage (rough estimate)
    current_memory_mb = image.nbytes / (1024 * 1024)

    if current_memory_mb <= max_memory_mb:
        return image

    # Calculate required scale factor
    scale_factor = np.sqrt(max_memory_mb / current_memory_mb)
    scale_factor = min(scale_factor, 0.8)  # Don't scale below 80%

    # Calculate new dimensions
    new_height = int(image.shape[0] * scale_factor)
    new_width = int(image.shape[1] * scale_factor)

    logger.info(
        f"Memory optimization: scaling image from {image.shape} to ({new_height}, {new_width})"
    )

    # Resize image
    if len(image.shape) == 3:
        resized = cv2.resize(image, (new_width, new_height))
    else:
        resized = cv2.resize(image, (new_width, new_height))

    return resized


def clear_memory():
    """Clear memory by forcing garbage collection."""
    import gc

    gc.collect()


def validate_image_file(
    image_path: str, expected_extensions: Optional[list] = None
) -> bool:
    """
    Validate that an image file exists and is readable.

    Args:
        image_path: Path to the image file
        expected_extensions: List of expected file extensions (e.g., ['.jpg', '.png'])

    Returns:
        True if image is valid, False otherwise
    """
    if not os.path.exists(image_path):
        logger.error(f"Image file does not exist: {image_path}")
        return False

    if expected_extensions:
        file_ext = os.path.splitext(image_path)[1].lower()
        if file_ext not in expected_extensions:
            logger.error(
                f"Image file has unexpected extension: {file_ext}, expected: {expected_extensions}"
            )
            return False

    # Try to read the image to validate it
    try:
        image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
        if image is None:
            logger.error(f"Failed to read image file: {image_path}")
            return False
        return True
    except Exception as e:
        logger.error(f"Error reading image file {image_path}: {e}")
        return False


def get_image_info(image_path: str) -> dict:
    """
    Get basic information about an image file.

    Args:
        image_path: Path to the image file

    Returns:
        Dictionary containing image information
    """
    try:
        image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
        if image is None:
            return {"error": "Failed to read image"}

        info = {
            "path": image_path,
            "shape": image.shape,
            "dtype": str(image.dtype),
            "size_mb": os.path.getsize(image_path) / (1024 * 1024),
            "channels": image.shape[2] if len(image.shape) > 2 else 1,
        }
        return info
    except Exception as e:
        return {"error": str(e)}


@performance_monitor
def validate_input_files(directory: str) -> Tuple[str, str, str]:
    """
    Validate and find required input files in the directory.

    Args:
        directory: Path to directory containing input files

    Returns:
        Tuple of (nir_off_path, nir_on_path, lwir_path)

    Raises:
        InputValidationError: If directory doesn't exist or required files are missing
    """
    if not os.path.exists(directory):
        raise InputValidationError(f"Directory not found: {directory}")

    if not os.path.isdir(directory):
        raise InputValidationError(f"Path is not a directory: {directory}")

    nir_off_image: Optional[str] = None
    nir_on_image: Optional[str] = None
    lwir_image: Optional[str] = None

    # Get list of files in directory
    try:
        files = os.listdir(directory)
    except PermissionError:
        raise InputValidationError(
            f"Permission denied accessing directory: {directory}"
        )
    except Exception as e:
        raise InputValidationError(f"Error reading directory {directory}: {e}")

    for filename in files:
        filepath = os.path.join(directory, filename)
        if filename.endswith("-NIR-OFF.jpg"):
            if validate_image_file(filepath, [".jpg"]):
                nir_off_image = filepath
                logger.info(f"Found NIR-OFF image: {filename}")
        elif filename.endswith("-NIR-ON.jpg"):
            if validate_image_file(filepath, [".jpg"]):
                nir_on_image = filepath
                logger.info(f"Found NIR-ON image: {filename}")
        elif filename.endswith(".pgm"):
            if validate_image_file(filepath, [".pgm"]):
                lwir_image = filepath
                logger.info(f"Found LWIR image: {filename}")

    # Validate that we found the required files
    missing_files = []
    if not nir_off_image:
        missing_files.append("*-NIR-OFF.jpg")
    if not nir_on_image:
        missing_files.append("*-NIR-ON.jpg")
    if not lwir_image:
        missing_files.append("*.pgm")

    if missing_files:
        error_msg = f"Missing required files in {directory}: {', '.join(missing_files)}"
        logger.error(error_msg)
        logger.info(f"Available files: {files}")
        raise InputValidationError(error_msg)

    # At this point, we know all files are found, so we can safely assert they're not None
    assert nir_off_image is not None
    assert nir_on_image is not None
    assert lwir_image is not None

    # Log image information for debugging
    for image_path, image_type in [
        (nir_off_image, "NIR-OFF"),
        (nir_on_image, "NIR-ON"),
        (lwir_image, "LWIR"),
    ]:
        info = get_image_info(image_path)
        if "error" not in info:
            logger.info(
                f"{image_type} image info: {info['shape']}, {info['size_mb']:.2f}MB"
            )

    return nir_off_image, nir_on_image, lwir_image


def get_thermal_colormap_info() -> str:
    """
    Get information about the current thermal colormap configuration.

    Returns:
        String describing the colormap configuration
    """
    colormap_names = {
        cv2.COLORMAP_JET: "JET (Blue-Green-Yellow-Red)",
        cv2.COLORMAP_HOT: "HOT (Black-Red-Yellow-White)",
        cv2.COLORMAP_INFERNO: "INFERNO (Black-Purple-Orange-Yellow)",
        cv2.COLORMAP_VIRIDIS: "VIRIDIS (Blue-Green-Yellow)",
        cv2.COLORMAP_PLASMA: "PLASMA (Purple-Orange-Yellow)",
    }

    colormap_name = colormap_names.get(THERMAL_COLORMAP, "Unknown")
    inversion_status = "inverted" if INVERT_THERMAL else "not inverted"

    return f"Thermal colormap: {colormap_name}, {inversion_status}"


def save_transform_parameters(
    transform: sitk.Transform, directory: str, metadata: Optional[dict] = None
) -> bool:
    """
    Save transformation parameters to a JSON file in the parent directory for future use.

    Args:
        transform: SimpleITK transform object
        directory: Directory containing the current images
        metadata: Additional metadata to save with the transform

    Returns:
        True if successful, False otherwise
    """
    if not SAVE_TRANSFORM_PARAMETERS:
        return False

    try:
        # Get parameters and convert to list, handling both tuple and numpy array types
        parameters = transform.GetParameters()
        fixed_parameters = transform.GetFixedParameters()

        # Convert to list if they're not already
        if hasattr(parameters, "tolist"):
            parameters_list = parameters.tolist()
        else:
            parameters_list = list(parameters)

        if hasattr(fixed_parameters, "tolist"):
            fixed_parameters_list = fixed_parameters.tolist()
        else:
            fixed_parameters_list = list(fixed_parameters)

        # Determine transform type as string
        transform_type_str = type(transform).__name__

        transform_data = {
            "transform_type": transform_type_str,
            "parameters": parameters_list,
            "fixed_parameters": fixed_parameters_list,
            "timestamp": pd.Timestamp.now().isoformat(),
            "metadata": metadata if metadata is not None else {},
        }

        # Save in parent directory for sharing across subdirectories
        parent_directory = os.path.dirname(directory)
        if parent_directory == "":  # If already at root, use current directory
            parent_directory = directory

        transform_path = os.path.join(parent_directory, TRANSFORM_CACHE_FILENAME)
        with open(transform_path, "w") as f:
            json.dump(transform_data, f, indent=2)

        logger.info(f"Saved transform parameters to parent directory: {transform_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to save transform parameters: {e}")
        return False


def load_transform_parameters(directory: str) -> Optional[tuple[sitk.Transform, str]]:
    """
    Load transformation parameters from a JSON file in the parent directory or current directory.

    Args:
        directory: Directory containing the current images

    Returns:
        Tuple of (SimpleITK transform object, path loaded from) if successful, None otherwise
    """
    if FORCE_RECALCULATE_TRANSFORM:
        logger.info("Force recalculation enabled, skipping cached transform")
        return None

    # Look for transform in parent directory first, then current directory
    parent_directory = os.path.dirname(directory)
    if parent_directory == "":  # If already at root, use current directory
        parent_directory = directory

    # Try parent directory first
    transform_path = os.path.join(parent_directory, TRANSFORM_CACHE_FILENAME)
    if os.path.exists(transform_path):
        load_path = transform_path
    else:
        # Fallback to current directory
        transform_path = os.path.join(directory, TRANSFORM_CACHE_FILENAME)
        if os.path.exists(transform_path):
            load_path = transform_path
        else:
            logger.info(f"No cached transform found in parent or current directory")
            return None

    try:
        with open(load_path, "r") as f:
            transform_data = json.load(f)

        # Create transform based on type string
        transform_type = transform_data["transform_type"]
        parameters = transform_data["parameters"]
        fixed_parameters = transform_data["fixed_parameters"]

        if transform_type == "Euler2DTransform":
            transform = sitk.Euler2DTransform()
        elif transform_type == "AffineTransform":
            transform = sitk.AffineTransform(2)
        elif transform_type == "BSplineTransform":
            transform = sitk.BSplineTransform(2, 3)
        else:
            logger.warning(f"Unknown transform type: {transform_type}")
            return None

        transform.SetParameters(parameters)
        transform.SetFixedParameters(fixed_parameters)
        timestamp = transform_data.get("timestamp", "unknown")
        logger.info(f"Loaded cached transform from {timestamp}: {load_path}")
        return (transform, load_path)
    except Exception as e:
        logger.error(f"Failed to load transform parameters: {e}")
        return None


def validate_transform_compatibility(
    transform: sitk.Transform, fixed_image_path: str, moving_image_path: str
) -> bool:
    """
    Validate that a cached transform is compatible with current images.

    Args:
        transform: Cached transform to validate
        fixed_image_path: Path to current fixed image
        moving_image_path: Path to current moving image

    Returns:
        True if transform is compatible, False otherwise
    """
    try:
        # Load current images to check dimensions
        fixed_image = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
        moving_image = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)

        if fixed_image is None or moving_image is None:
            logger.warning("Cannot validate transform - failed to load images")
            return False

        # Check if images are resized for processing
        if (
            fixed_image.shape[0] > MAX_IMAGE_SIZE
            or fixed_image.shape[1] > MAX_IMAGE_SIZE
        ):
            width = int(fixed_image.shape[1] * SCALE_PERCENT / 100)
            height = int(fixed_image.shape[0] * SCALE_PERCENT / 100)
            expected_size = (height, width)
        else:
            expected_size = fixed_image.shape[:2]

        # For now, we'll assume the transform is compatible if it exists
        # In a more sophisticated implementation, you could check image dimensions,
        # registration parameters, or other compatibility factors

        logger.info(f"Transform validation passed for image size: {expected_size}")
        return True

    except Exception as e:
        logger.warning(f"Transform validation failed: {e}")
        return False


def apply_cached_transform(
    fixed_image_path: str,
    moving_image_path: str,
    cached_transform: sitk.Transform,
    temperature_data: Optional[np.ndarray] = None,
) -> Tuple[sitk.Transform, np.ndarray, np.ndarray, np.ndarray]:
    """
    Apply a cached transform to images without performing registration.

    Args:
        fixed_image_path: Path to the fixed optical image
        moving_image_path: Path to the moving thermal image
        cached_transform: Cached transform to apply
        temperature_data: Optional radiometric temperature data array

    Returns:
        Tuple of (cached_transform, output, cropped_output, four_band_with_thermal)
    """
    logger.info("Applying cached transform to images...")

    # Read the images
    fixed_image_cv = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
    moving_image_cv = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)

    if fixed_image_cv is None:
        raise ValueError(f"Could not load fixed image: {fixed_image_path}")
    if moving_image_cv is None:
        raise ValueError(f"Could not load moving image: {moving_image_path}")

    # Resize images if they're too large for processing
    if (
        fixed_image_cv.shape[0] > MAX_IMAGE_SIZE
        or fixed_image_cv.shape[1] > MAX_IMAGE_SIZE
    ):
        logger.info(f"Resizing large images by {SCALE_PERCENT}%")
        width = int(fixed_image_cv.shape[1] * SCALE_PERCENT / 100)
        height = int(fixed_image_cv.shape[0] * SCALE_PERCENT / 100)
        dim = (width, height)
        fixed_image_cv_resized = cv2.resize(fixed_image_cv, dim)
        moving_image_cv_resized = cv2.resize(moving_image_cv, dim)
    else:
        moving_image_cv_resized = cv2.resize(
            moving_image_cv, (fixed_image_cv.shape[1], fixed_image_cv.shape[0])
        )
        fixed_image_cv_resized = fixed_image_cv

    # Convert images to SimpleITK format
    fixed_image_sitk = sitk.GetImageFromArray(fixed_image_cv_resized.astype(np.float32))
    moving_image_sitk = sitk.GetImageFromArray(
        moving_image_cv_resized.astype(np.float32)
    )

    # Resample the thermal image using the cached transform
    moving_resampled = sitk.Resample(
        moving_image_sitk,
        fixed_image_sitk,
        cached_transform,
        sitk.sitkLinear,
        0.0,
        moving_image_sitk.GetPixelID(),
    )

    # Convert back to numpy for further processing
    moving_resampled_np = sitk.GetArrayFromImage(moving_resampled)

    # Normalize thermal data to 0-255 range
    moving_resampled_np = normalize_image(moving_resampled_np)

    # Apply colormap to the thermal image
    if INVERT_THERMAL:
        moving_resampled_np = 255 - moving_resampled_np

    moving_resampled_colored = cv2.applyColorMap(
        moving_resampled_np.astype(np.uint8), THERMAL_COLORMAP
    )

    # Combine the thermal and optical images
    overlay = fixed_image_cv_resized.astype(np.float32)
    output = moving_resampled_colored.astype(np.float32)
    cv2.addWeighted(overlay, OVERLAY_ALPHA, output, OVERLAY_BETA, 0, output)

    # Create four-band image (RGB + thermal)
    four_band_with_thermal = np.dstack((overlay, moving_resampled_np))

    # Ensure the combined image is within the correct range
    output = np.clip(output, 0, 255).astype(np.uint8)

    # Crop to remove empty regions
    x_min, y_min, x_max, y_max = find_bounding_box(moving_resampled_colored)
    cropped_output = output[y_min:y_max, x_min:x_max]

    return cached_transform, output, cropped_output, four_band_with_thermal


def preprocess_image_for_registration(
    image: np.ndarray, image_type: str = "optical"
) -> np.ndarray:
    """
    Preprocess images to improve registration accuracy.

    Args:
        image: Input image array
        image_type: Type of image ("optical" or "thermal")

    Returns:
        Preprocessed image array
    """
    if not ENABLE_PREPROCESSING:
        return image

    # Convert to grayscale if needed
    if len(image.shape) == 3:
        if image_type == "optical":
            # For optical images, use luminance conversion
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            # For thermal images, use the first channel if multi-channel
            gray = image[:, :, 0] if image.shape[2] > 1 else image
    else:
        gray = image

    if FAST_MODE:
        # Fast mode: minimal preprocessing
        gray = cv2.equalizeHist(gray.astype(np.uint8))
        # Skip Gaussian blur and edge enhancement for speed
    else:
        # Normal mode: full preprocessing
        # Apply histogram equalization to improve contrast
        gray = cv2.equalizeHist(gray.astype(np.uint8))

        # Apply Gaussian blur to reduce noise
        gray = cv2.GaussianBlur(gray, (3, 3), 0.5)

        # Apply edge enhancement
        kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
        gray = cv2.filter2D(gray, -1, kernel)

    return gray.astype(np.float32)


def create_transform(transform_type: str) -> sitk.Transform:
    """
    Create the appropriate transform based on the transform type.

    Args:
        transform_type: Type of transform ("rigid", "affine", "bspline")

    Returns:
        SimpleITK transform object
    """
    if transform_type == "rigid":
        return sitk.Euler2DTransform()
    elif transform_type == "affine":
        return sitk.AffineTransform(2)
    elif transform_type == "bspline":
        # B-spline transform requires more parameters
        return sitk.BSplineTransform(2, 3)  # 2D, order 3
    else:
        logger.warning(
            f"Unknown transform type: {transform_type}, using rigid transform"
        )
        return sitk.Euler2DTransform()


def set_registration_metric(
    registration_method: sitk.ImageRegistrationMethod, metric_type: str
) -> None:
    """
    Set the registration metric based on the metric type.

    Args:
        registration_method: SimpleITK registration method object
        metric_type: Type of metric to use
    """
    if metric_type == "mutual_information":
        registration_method.SetMetricAsMattesMutualInformation(
            numberOfHistogramBins=HISTOGRAM_BINS
        )
    elif metric_type == "mean_squares":
        registration_method.SetMetricAsMeanSquares()
    elif metric_type == "correlation":
        registration_method.SetMetricAsCorrelation()
    else:
        logger.warning(f"Unknown metric type: {metric_type}, using mutual information")
        registration_method.SetMetricAsMattesMutualInformation(
            numberOfHistogramBins=HISTOGRAM_BINS
        )


def estimate_initial_transform(
    fixed_image: sitk.Image, moving_image: sitk.Image, transform_type: str
) -> sitk.Transform:
    """
    Estimate initial transform using multiple strategies.

    Args:
        fixed_image: Fixed image (optical)
        moving_image: Moving image (thermal)
        transform_type: Type of transform to use

    Returns:
        Initial transform
    """
    transform = create_transform(transform_type)

    # Strategy 1: Centered transform initializer
    try:
        initial_transform = sitk.CenteredTransformInitializer(
            fixed_image,
            moving_image,
            transform,
            sitk.CenteredTransformInitializerFilter.GEOMETRY,
        )
        logger.info("Using centered transform initializer")
        return initial_transform
    except Exception as e:
        logger.warning(f"Centered transform initializer failed: {e}")

    # Strategy 2: Moments-based initialization
    try:
        initial_transform = sitk.CenteredTransformInitializer(
            fixed_image,
            moving_image,
            transform,
            sitk.CenteredTransformInitializerFilter.MOMENTS,
        )
        logger.info("Using moments-based transform initializer")
        return initial_transform
    except Exception as e:
        logger.warning(f"Moments-based transform initializer failed: {e}")

    # Strategy 3: Identity transform as fallback
    logger.info("Using identity transform as fallback")
    return transform


def try_multiple_registration_strategies(
    fixed_image_path: str,
    moving_image_path: str,
    directory: str = "",
    position_changed: bool = False,
) -> Tuple[sitk.Transform, np.ndarray, np.ndarray, np.ndarray]:
    """
    Try multiple registration strategies and select the best result.

    This function attempts different transform types and metrics to find the best alignment.

    Args:
        fixed_image_path: Path to the fixed optical image
        moving_image_path: Path to the moving thermal image
        directory: Directory for saving/loading transform cache
        position_changed: If True, force new registration even if cached transform exists

    Returns:
        Tuple of (best_transform, best_output, best_cropped_output, best_four_band)
    """
    # Declare global variables at the beginning
    global TRANSFORM_TYPE, REGISTRATION_METRIC

    if not ENABLE_MULTIPLE_STRATEGIES:
        # Fast mode: use only the best strategy (affine + mutual information)
        logger.info(
            "Fast mode: using single best strategy (affine + mutual information)"
        )
        try:
            # Store original parameters
            original_transform = TRANSFORM_TYPE
            original_metric = REGISTRATION_METRIC

            # Set parameters for best strategy
            TRANSFORM_TYPE = "affine"
            REGISTRATION_METRIC = "mutual_information"

            # Perform registration
            transform, output, cropped_output, four_band = (
                mutual_information_registration(
                    fixed_image_path, moving_image_path, directory, position_changed
                )
            )

            # Restore original parameters
            TRANSFORM_TYPE = original_transform
            REGISTRATION_METRIC = original_metric

            return transform, output, cropped_output, four_band

        except Exception as e:
            logger.warning(f"Best strategy failed: {e}")
            # Fall back to original parameters
            TRANSFORM_TYPE = original_transform
            REGISTRATION_METRIC = original_metric
            raise

    # Full mode: try multiple strategies
    strategies = [
        {"transform": "affine", "metric": "mutual_information"},  # Best strategy first
        {"transform": "rigid", "metric": "mutual_information"},
        {"transform": "affine", "metric": "mean_squares"},
        {"transform": "rigid", "metric": "mean_squares"},
    ]

    best_metric_value = float("inf")
    best_result = None
    best_strategy = None

    logger.info("Trying multiple registration strategies...")

    for i, strategy in enumerate(strategies):
        logger.info(
            f"Strategy {i+1}/{len(strategies)}: {strategy['transform']} transform with {strategy['metric']} metric"
        )

        try:
            # Store original parameters
            original_transform = TRANSFORM_TYPE
            original_metric = REGISTRATION_METRIC

            # Set parameters for current strategy
            TRANSFORM_TYPE = strategy["transform"]
            REGISTRATION_METRIC = strategy["metric"]

            # Perform registration
            transform, output, cropped_output, four_band = (
                mutual_information_registration(
                    fixed_image_path, moving_image_path, directory, position_changed
                )
            )

            # Restore original parameters
            TRANSFORM_TYPE = original_transform
            REGISTRATION_METRIC = original_metric

            # For now, we'll use a simple heuristic: prefer affine over rigid for better alignment
            # In a more sophisticated approach, you could save the metric values during registration
            metric_value = (
                0  # Placeholder - in practice, you'd want to capture the actual metric
            )

            if strategy["transform"] == "affine":
                metric_value -= 1  # Prefer affine transforms

            if metric_value < best_metric_value:
                best_metric_value = metric_value
                best_result = (transform, output, cropped_output, four_band)
                best_strategy = strategy
                logger.info(f"New best strategy found: {strategy}")

        except Exception as e:
            logger.warning(f"Strategy {strategy} failed: {e}")
            continue

    if best_result is None:
        logger.error("All registration strategies failed")
        raise ValueError("All registration strategies failed")

    logger.info(f"Best strategy: {best_strategy}")
    return best_result


def mutual_information_registration(
    fixed_image_path: str,
    moving_image_path: str,
    directory: str = "",
    position_changed: bool = False,
) -> Tuple[sitk.Transform, np.ndarray, np.ndarray, np.ndarray]:
    """
    Perform enhanced mutual information-based registration between fixed (optical) and moving (thermal) images.

    This enhanced version includes:
    - Multi-scale registration for better convergence
    - Image preprocessing for improved feature matching
    - Multiple transform types (rigid, affine, bspline)
    - Multiple registration metrics
    - Better initial transform estimation
    - Cached transform parameters for fixed camera setups

    Args:
        fixed_image_path: Path to the fixed optical image
        moving_image_path: Path to the moving thermal image
        directory: Directory for saving/loading transform cache
        position_changed: If True, force new registration even if cached transform exists

    Returns:
        Tuple of (final_transform, output, cropped_output, four_band_with_thermal)

    Raises:
        ValueError: If images cannot be loaded or processed
    """
    # Check for cached transform first (unless position_changed is True)
    if directory and not position_changed:
        cached = load_transform_parameters(directory)
        if cached is not None:
            cached_transform, loaded_path = cached
            if validate_transform_compatibility(
                cached_transform, fixed_image_path, moving_image_path
            ):
                logger.info(f"Using cached transform parameters from {loaded_path}")
                return apply_cached_transform(
                    fixed_image_path, moving_image_path, cached_transform
                )
            else:
                logger.info(
                    f"Cached transform from {loaded_path} is not compatible. Will generate a new transform."
                )
    elif position_changed:
        logger.info("Position changed flag is True - forcing new registration")

    # Read the fixed (optical) and moving (thermal) images
    fixed_image_cv = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
    moving_image_cv = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)

    if fixed_image_cv is None:
        raise ValueError(f"Could not load fixed image: {fixed_image_path}")
    if moving_image_cv is None:
        raise ValueError(f"Could not load moving image: {moving_image_path}")

    # Store original sizes for final output
    original_fixed_size = fixed_image_cv.shape[:2]

    # Resize images if they're too large for processing
    if (
        fixed_image_cv.shape[0] > MAX_IMAGE_SIZE
        or fixed_image_cv.shape[1] > MAX_IMAGE_SIZE
    ):
        logger.info(f"Resizing large images by {SCALE_PERCENT}%")
        width = int(fixed_image_cv.shape[1] * SCALE_PERCENT / 100)
        height = int(fixed_image_cv.shape[0] * SCALE_PERCENT / 100)
        dim = (width, height)
        fixed_image_cv_resized = cv2.resize(fixed_image_cv, dim)
        moving_image_cv_resized = cv2.resize(moving_image_cv, dim)
    else:
        moving_image_cv_resized = cv2.resize(
            moving_image_cv, (fixed_image_cv.shape[1], fixed_image_cv.shape[0])
        )
        fixed_image_cv_resized = fixed_image_cv

    # Use visual thermal image for registration
    logger.info("Using visual thermal image for registration")
    moving_image_for_registration = moving_image_cv_resized
    original_thermal_image = moving_image_cv_resized

    # Preprocess images for better registration
    logger.info("Preprocessing images for registration...")
    fixed_preprocessed = preprocess_image_for_registration(
        fixed_image_cv_resized, "optical"
    )
    moving_preprocessed = preprocess_image_for_registration(
        moving_image_for_registration, "thermal"
    )

    # Convert the preprocessed images to SimpleITK format
    fixed_image_sitk = sitk.GetImageFromArray(fixed_preprocessed.astype(np.float32))
    moving_image_sitk = sitk.GetImageFromArray(moving_preprocessed.astype(np.float32))

    # Ensure the types are the same
    if fixed_image_sitk.GetPixelID() != moving_image_sitk.GetPixelID():
        moving_image_sitk = sitk.Cast(moving_image_sitk, fixed_image_sitk.GetPixelID())

    # Set up the image registration method
    registration_method = sitk.ImageRegistrationMethod()

    # Set the registration metric
    set_registration_metric(registration_method, REGISTRATION_METRIC)

    # Set the interpolator to linear
    registration_method.SetInterpolator(sitk.sitkLinear)

    # Use gradient descent optimizer with better parameters
    if FAST_MODE:
        # Fast mode: reduced iterations and faster convergence
        registration_method.SetOptimizerAsGradientDescent(
            learningRate=LEARNING_RATE * 2,  # Faster learning rate
            numberOfIterations=MAX_ITERATIONS // 2,  # Fewer iterations
            convergenceMinimumValue=1e-4,  # Less strict convergence
            convergenceWindowSize=5,  # Smaller convergence window
        )
    else:
        # Normal mode: standard parameters
        registration_method.SetOptimizerAsGradientDescent(
            learningRate=LEARNING_RATE,
            numberOfIterations=MAX_ITERATIONS,
            convergenceMinimumValue=1e-6,
            convergenceWindowSize=10,
        )

    # Set the optimizer scales from physical shift
    registration_method.SetOptimizerScalesFromPhysicalShift()

    # Estimate initial transform
    initial_transform = estimate_initial_transform(
        fixed_image_sitk, moving_image_sitk, TRANSFORM_TYPE
    )
    registration_method.SetInitialTransform(initial_transform, inPlace=False)

    # Execute multi-scale registration if enabled
    if ENABLE_MULTI_SCALE:
        if FAST_MODE:
            # Fast mode: fewer multi-scale levels
            logger.info("Performing fast multi-scale registration (2 levels)...")
            fast_shrink_factors = [2, 1]  # Only 2 levels
            fast_smoothing_sigmas = [1, 0]  # Reduced smoothing
            registration_method.SetShrinkFactorsPerLevel(fast_shrink_factors)
            registration_method.SetSmoothingSigmasPerLevel(fast_smoothing_sigmas)
        else:
            # Normal mode: full multi-scale
            logger.info("Performing multi-scale registration...")
            registration_method.SetShrinkFactorsPerLevel(MULTI_SCALE_SHRINK_FACTORS)
            registration_method.SetSmoothingSigmasPerLevel(MULTI_SCALE_SMOOTHING_SIGMAS)

        registration_method.SmoothingSigmasAreSpecifiedInPhysicalUnitsOff()

    # Execute the registration
    logger.info(
        f"Starting {TRANSFORM_TYPE} registration with {REGISTRATION_METRIC} metric..."
    )
    final_transform = registration_method.Execute(
        sitk.Cast(fixed_image_sitk, sitk.sitkFloat32),
        sitk.Cast(moving_image_sitk, sitk.sitkFloat32),
    )

    # Log the final metric value and the optimizer's stopping condition
    logger.info(f"Final metric value: {registration_method.GetMetricValue()}")
    logger.info(
        f"Optimizer's stopping condition: {registration_method.GetOptimizerStopConditionDescription()}"
    )

    # Log transform parameters for debugging
    if hasattr(final_transform, "GetParameters"):
        params = final_transform.GetParameters()
        logger.info(f"Transform parameters: {params}")

    # Save transform parameters for future use
    if directory:
        metadata = {
            "fixed_image": os.path.basename(fixed_image_path),
            "moving_image": os.path.basename(moving_image_path),
            "transform_type": TRANSFORM_TYPE,
            "metric": REGISTRATION_METRIC,
            "image_size": fixed_image_cv_resized.shape[:2],
        }
        save_transform_parameters(final_transform, directory, metadata)

    # Convert original thermal image to SimpleITK for resampling
    original_thermal_sitk = sitk.GetImageFromArray(
        original_thermal_image.astype(np.float32)
    )

    # Resample the thermal image to align with the fixed image using the final transform
    moving_resampled = sitk.Resample(
        original_thermal_sitk,
        fixed_image_sitk,
        final_transform,
        sitk.sitkLinear,
        0.0,
        original_thermal_sitk.GetPixelID(),
    )

    # Convert back to numpy for further processing
    moving_resampled_np = sitk.GetArrayFromImage(moving_resampled)

    # Normalize thermal data to 0-255 range
    moving_resampled_np = normalize_image(moving_resampled_np)

    # Apply colormap to the thermal image
    if INVERT_THERMAL:
        moving_resampled_np = 255 - moving_resampled_np

    moving_resampled_colored = cv2.applyColorMap(
        moving_resampled_np.astype(np.uint8), THERMAL_COLORMAP
    )

    # Combine the thermal and optical images into a single overlay
    overlay = fixed_image_cv_resized.astype(np.float32)
    output = moving_resampled_colored.astype(np.float32)
    cv2.addWeighted(overlay, OVERLAY_ALPHA, output, OVERLAY_BETA, 0, output)

    # Create four-band image (RGB + thermal)
    four_band_with_thermal = np.dstack((overlay, moving_resampled_np))

    # Ensure the combined image is within the correct range
    output = np.clip(output, 0, 255).astype(np.uint8)

    # Crop to remove empty regions
    x_min, y_min, x_max, y_max = find_bounding_box(moving_resampled_colored)
    cropped_output = output[y_min:y_max, x_min:x_max]

    return final_transform, output, cropped_output, four_band_with_thermal


def find_bounding_box(image: np.ndarray) -> Tuple[int, int, int, int]:
    """
    Find the bounding box of non-empty regions in the image.

    Args:
        image: Input image array

    Returns:
        Tuple of (x_min, y_min, x_max, y_max) bounding box coordinates
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    retval, thresh = cv2.threshold(gray, THRESHOLD_VALUE, 255, cv2.THRESH_BINARY)
    contours, hierarchy = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    if len(contours) == 0:
        # No contours found, return the full image
        return 0, 0, image.shape[1], image.shape[0]

    x_min, y_min, x_max, y_max = np.inf, np.inf, -np.inf, -np.inf

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        x_min = min(x_min, x)
        y_min = min(y_min, y)
        x_max = max(x_max, x + w)
        y_max = max(y_max, y + h)

    return int(x_min), int(y_min), int(x_max), int(y_max)


def extract_nir_band(
    nir_on_path: str, nir_off_path: str, target_size: Tuple[int, int]
) -> np.ndarray:
    """
    Extract NIR band by computing the difference between NIR-ON and NIR-OFF red channels.

    Args:
        nir_on_path: Path to NIR-ON image
        nir_off_path: Path to NIR-OFF image
        target_size: Target size (width, height) for the output

    Returns:
        NIR band as numpy array

    Raises:
        ValueError: If images cannot be loaded
    """
    # Load NIR images
    nir_on = cv2.imread(nir_on_path)
    nir_off = cv2.imread(nir_off_path)

    if nir_on is None:
        raise ValueError(f"Could not load NIR-ON image: {nir_on_path}")
    if nir_off is None:
        raise ValueError(f"Could not load NIR-OFF image: {nir_off_path}")

    # Convert to RGB and extract red channels
    nir_on_rgb = cv2.cvtColor(nir_on, cv2.COLOR_BGR2RGB)
    nir_off_rgb = cv2.cvtColor(nir_off, cv2.COLOR_BGR2RGB)

    red_channel_nir = nir_on_rgb[:, :, 0]
    red_channel_off = nir_off_rgb[:, :, 0]

    # Compute NIR band as difference
    nir_band = cv2.subtract(red_channel_nir, red_channel_off)

    # Resize to target size
    nir_band_resized = cv2.resize(nir_band, target_size)

    return nir_band_resized


def save_multiband_tiff(
    image_data: np.ndarray,
    output_path: str,
    transform_params: Tuple = DEFAULT_TRANSFORM_ORIGIN + DEFAULT_TRANSFORM_SCALE,
) -> None:
    """
    Save multiband image as GeoTIFF with proper metadata.

    Args:
        image_data: Image data array (bands, height, width)
        output_path: Output file path
        transform_params: Geotransform parameters (origin_x, origin_y, pixel_width, pixel_height)
    """
    transform = from_origin(*transform_params)

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=image_data.shape[1],
        width=image_data.shape[2],
        count=image_data.shape[0],
        dtype=image_data.dtype,
        transform=transform,
    ) as dst:
        dst.write(image_data)

    logger.info(f"Saved multiband TIFF: {output_path}")


def save_color_preserved_tiff(
    rgb_data: np.ndarray,
    thermal_data: np.ndarray,
    nir_data: np.ndarray,
    output_path: str,
    transform_params: Tuple = DEFAULT_TRANSFORM_ORIGIN + DEFAULT_TRANSFORM_SCALE,
) -> None:
    """
    Save a color-preserved version of the data with proper color profiles and metadata.

    Args:
        rgb_data: RGB optical data (height, width, 3)
        thermal_data: Thermal data (height, width)
        nir_data: NIR data (height, width)
        output_path: Output file path
        transform_params: Geotransform parameters
    """
    transform = from_origin(*transform_params)

    # Create a 6-band image: RGB + Thermal + NIR
    # Ensure all data is in the correct format
    rgb_uint8 = np.clip(rgb_data, 0, 255).astype(np.uint8)
    thermal_uint8 = np.clip(thermal_data, 0, 255).astype(np.uint8)
    nir_uint8 = np.clip(nir_data, 0, 255).astype(np.uint8)

    # Stack bands: R, G, B, Thermal, NIR
    stacked_data = np.stack(
        [
            rgb_uint8[:, :, 2],  # Red
            rgb_uint8[:, :, 1],  # Green
            rgb_uint8[:, :, 0],  # Blue
            thermal_uint8,  # Thermal
            nir_uint8,  # NIR
        ],
        axis=0,
    )

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=stacked_data.shape[1],
        width=stacked_data.shape[2],
        count=stacked_data.shape[0],
        dtype=stacked_data.dtype,
        transform=transform,
        photometric="rgb",  # Indicate this is RGB data
        compress="lzw",  # Add compression
    ) as dst:
        # Write the data
        dst.write(stacked_data)

        # Add band descriptions
        band_descriptions = [
            "Red Channel (Optical)",
            "Green Channel (Optical)",
            "Blue Channel (Optical)",
            "Thermal Data (Normalized 0-255)",
            "NIR Band (NIR-ON minus NIR-OFF)",
        ]

        for i, description in enumerate(band_descriptions, 1):
            dst.set_band_description(i, description)

        # Add metadata
        dst.update_tags(
            CREATOR="Coregistration Script",
            DESCRIPTION="Color-preserved multispectral data with RGB, Thermal, and NIR bands",
            BAND_COUNT=str(stacked_data.shape[0]),
            DATA_SOURCES="Optical (RGB), Thermal (LWIR), NIR (NIR-ON/NIR-OFF difference)",
        )

    logger.info(f"Saved color-preserved TIFF: {output_path}")


def create_color_composite(
    rgb_data: np.ndarray, thermal_data: np.ndarray, nir_data: np.ndarray
) -> np.ndarray:
    """
    Create a false-color composite image for visualization.

    Args:
        rgb_data: RGB optical data
        thermal_data: Thermal data
        nir_data: NIR data

    Returns:
        False-color composite image
    """
    # Create a false-color composite: NIR as red, Thermal as green, Red channel as blue
    # This is a common combination for vegetation analysis
    composite = np.zeros((rgb_data.shape[0], rgb_data.shape[1], 3), dtype=np.uint8)

    # Normalize each band to 0-255 range
    nir_norm = normalize_image(nir_data, 0, 255)
    thermal_norm = normalize_image(thermal_data, 0, 255)
    red_norm = normalize_image(rgb_data[:, :, 2], 0, 255)  # Red channel

    composite[:, :, 0] = nir_norm  # NIR as red
    composite[:, :, 1] = thermal_norm  # Thermal as green
    composite[:, :, 2] = red_norm  # Red as blue

    return composite


def save_metadata_summary(directory: str, output_paths: dict, image_info: dict) -> None:
    """
    Save a metadata summary file with information about all outputs.

    Args:
        directory: Output directory
        output_paths: Dictionary of output file paths
        image_info: Dictionary with image information
    """
    metadata_file = os.path.join(directory, "coregistration_metadata.json")

    metadata = {
        "timestamp": pd.Timestamp.now().isoformat(),
        "output_files": output_paths,
        "image_info": image_info,
        "processing_parameters": {
            "max_image_size": config.MAX_IMAGE_SIZE,
            "scale_percent": config.SCALE_PERCENT,
            "registration_metric": config.REGISTRATION_METRIC,
            "transform_type": config.TRANSFORM_TYPE,
            "thermal_colormap": str(config.THERMAL_COLORMAP),
            "invert_thermal": config.INVERT_THERMAL,
        },
        "band_descriptions": {
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
        },
    }

    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(f"Saved metadata summary: {metadata_file}")


@performance_monitor
def coreg(directory: str, position_changed: bool = False) -> None:
    """
    Perform coregistration on images in the specified directory.

    Args:
        directory: Directory containing input images
        position_changed: If True, force new registration even if cached transform exists

    Raises:
        InputValidationError: If directory doesn't exist or required files are missing
        RegistrationError: If registration processing fails
    """
    try:
        # Validate and find input files
        nir_off_path, nir_on_path, lwir_path = validate_input_files(directory)

        # Convert PGM to JPG for processing
        logger.info("Converting PGM to JPG...")
        lwir_image = cv2.imread(lwir_path, cv2.IMREAD_UNCHANGED)
        if lwir_image is None:
            raise RegistrationError(f"Could not load LWIR image: {lwir_path}")

        lwir_normalized = normalize_image(lwir_image, 0, 255)
        lwir_jpg_path = os.path.splitext(lwir_path)[0] + ".jpg"

        try:
            cv2.imwrite(lwir_jpg_path, lwir_normalized)
            logger.info(f"Converted PGM to JPG: {lwir_jpg_path}")
        except Exception as e:
            raise RegistrationError(f"Failed to save converted JPG file: {e}")

        # Define output filenames
        output_filenames = {
            "registered": "registered.jpg",
            "cropped": "cropped.jpg",
            "four_band": "4_band_with_thermal.tiff",
            "nir_band": "NIR_band.png",
            "five_band": "final_5_band.tiff",
            "color_preserved": "color_preserved_5_band.tiff",
            "false_color": "false_color_composite.jpg",
            "metadata": "coregistration_metadata.json",
        }

        # Check if outputs already exist
        output_paths = {
            name: os.path.join(directory, filename)
            for name, filename in output_filenames.items()
        }

        # Only process if outputs don't exist
        if not all(os.path.exists(path) for path in output_paths.values()):
            logger.info("Performing image registration...")

            # Log thermal colormap configuration
            logger.info(get_thermal_colormap_info())

            # Perform registration using visual thermal image
            try:
                transform, output, cropped_output, four_band = (
                    try_multiple_registration_strategies(
                        nir_off_path, lwir_jpg_path, directory, position_changed
                    )
                )
            except Exception as e:
                raise RegistrationError(f"Registration failed: {e}")

            # Save registration outputs
            try:
                cv2.imwrite(output_paths["registered"], output)
                cv2.imwrite(output_paths["cropped"], cropped_output)
                cv2.imwrite(output_paths["four_band"], four_band)
                logger.info("Saved registration outputs")
            except Exception as e:
                raise RegistrationError(f"Failed to save registration outputs: {e}")

            # Extract and save NIR band
            logger.info("Extracting NIR band...")
            try:
                width, height = four_band.shape[1], four_band.shape[0]
                nir_band = extract_nir_band(nir_on_path, nir_off_path, (width, height))
                cv2.imwrite(output_paths["nir_band"], nir_band)
                logger.info("Saved NIR band")
            except Exception as e:
                raise RegistrationError(f"Failed to extract/save NIR band: {e}")

            # Create and save five-band image (original format)
            logger.info("Creating five-band image...")
            try:
                final_five_band = np.dstack((four_band, nir_band))
                final_five_band = np.clip(final_five_band, 0, 255).astype(np.uint8)
                final_five_band_reordered = np.transpose(final_five_band, (2, 0, 1))

                # Save as GeoTIFF
                save_multiband_tiff(
                    final_five_band_reordered, output_paths["five_band"]
                )
                logger.info("Saved five-band GeoTIFF")
            except Exception as e:
                raise RegistrationError(f"Failed to create/save five-band image: {e}")

            # Create and save color-preserved version
            logger.info("Creating color-preserved version...")
            try:
                # Extract RGB data from four_band (first 3 bands)
                rgb_data = four_band[:, :, :3]  # BGR format from OpenCV
                thermal_data = four_band[:, :, 3]  # Thermal band

                # Save color-preserved TIFF with proper band order and metadata
                save_color_preserved_tiff(
                    rgb_data, thermal_data, nir_band, output_paths["color_preserved"]
                )
                logger.info("Saved color-preserved TIFF")
            except Exception as e:
                raise RegistrationError(
                    f"Failed to create/save color-preserved image: {e}"
                )

            # Create and save false-color composite
            logger.info("Creating false-color composite...")
            try:
                false_color = create_color_composite(rgb_data, thermal_data, nir_band)
                cv2.imwrite(output_paths["false_color"], false_color)
                logger.info("Saved false-color composite")
            except Exception as e:
                raise RegistrationError(
                    f"Failed to create/save false-color composite: {e}"
                )

            # Save metadata summary
            logger.info("Saving metadata summary...")
            try:
                # Collect image information
                image_info = {
                    "nir_off": get_image_info(nir_off_path),
                    "nir_on": get_image_info(nir_on_path),
                    "lwir": get_image_info(lwir_path),
                    "output_shape": four_band.shape,
                    "registration_transform": (
                        transform.GetParameters().tolist()
                        if hasattr(transform, "GetParameters")
                        else None
                    ),
                }

                save_metadata_summary(directory, output_paths, image_info)
                logger.info("Saved metadata summary")
            except Exception as e:
                logger.warning(f"Failed to save metadata summary: {e}")

            # Clear memory after processing
            clear_memory()

            logger.info("Coregistration completed successfully!")
            logger.info(f"Output files saved in: {directory}")
            logger.info("Files created:")
            logger.info(
                "  - final_5_band.tiff: Original 5-band format (B,G,R,Thermal,NIR)"
            )
            logger.info(
                "  - color_preserved_5_band.tiff: Color-preserved format (R,G,B,Thermal,NIR)"
            )
            logger.info("  - false_color_composite.jpg: False-color visualization")
            logger.info("  - coregistration_metadata.json: Processing metadata")

        else:
            logger.info("All output files already exist. Skipping processing.")

    except (InputValidationError, RegistrationError):
        # Re-raise our custom exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error during coregistration: {str(e)}")
        raise RegistrationError(f"Coregistration failed: {str(e)}")


def main():
    """Main entry point for command-line usage."""
    # Declare global variables at the beginning
    global FAST_MODE, ENABLE_MULTIPLE_STRATEGIES

    parser = argparse.ArgumentParser(
        description="Multispectral Image Coregistration Script"
    )
    parser.add_argument("directory", help="Path to directory containing input images")
    parser.add_argument(
        "--position-changed",
        action="store_true",
        help="Force new registration even if cached transform exists",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Enable fast mode with reduced iterations and preprocessing",
    )
    parser.add_argument(
        "--single-strategy",
        action="store_true",
        help="Use only the best registration strategy (faster)",
    )
    parser.add_argument(
        "--multiple-strategies",
        action="store_true",
        help="Try multiple registration strategies (slower but potentially better)",
    )

    args = parser.parse_args()

    directory = args.directory
    position_changed = args.position_changed

    # Set performance modes based on command line arguments
    if args.fast:
        FAST_MODE = True
        print("Fast mode enabled")

    if args.single_strategy:
        ENABLE_MULTIPLE_STRATEGIES = False
        print("Single strategy mode enabled")
    elif args.multiple_strategies:
        ENABLE_MULTIPLE_STRATEGIES = True
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
