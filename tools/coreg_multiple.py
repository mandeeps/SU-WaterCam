#!/usr/bin/env python

# Fatemeh Rezaei
# https://github.com/rzfatemeh
# with some edits to allow use as a module 

import cv2
import os
import numpy as np
import SimpleITK as sitk

import tifffile
import rasterio
from rasterio.transform import from_origin

def mutual_information_registration(fixed_image_path, moving_image_path):
    # Read the fixed (optical) and moving (thermal) images
    fixed_image_cv = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
    moving_image_cv = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)

    # Resize the thermal image to the same size as the optical image
    if fixed_image_cv.shape[0] > 1000 or fixed_image_cv.shape[1] > 1000:
        scale_percent = 50  # Reduce size by 50%
        width = int(fixed_image_cv.shape[1] * scale_percent / 100)
        height = int(fixed_image_cv.shape[0] * scale_percent / 100)
        dim = (width, height)
        fixed_image_cv_resized = cv2.resize(fixed_image_cv, dim)
        moving_image_cv_resized = cv2.resize(moving_image_cv, dim)
    else:
        moving_image_cv_resized = cv2.resize(
            moving_image_cv, (fixed_image_cv.shape[1], fixed_image_cv.shape[0])
        )
        fixed_image_cv_resized = fixed_image_cv

    # Convert the resized images to SimpleITK format
    fixed_image_resized = sitk.GetImageFromArray(
        cv2.cvtColor(fixed_image_cv_resized, cv2.COLOR_BGR2GRAY).astype(np.float32)
    )
    moving_image_resized = sitk.GetImageFromArray(
        moving_image_cv_resized.astype(np.float32)
    )

    # Ensure the types are the same
    if fixed_image_resized.GetPixelID() != moving_image_resized.GetPixelID():
        moving_image_resized = sitk.Cast(
            moving_image_resized, fixed_image_resized.GetPixelID()
        )

    # Initialize the transform
    initial_transform = sitk.CenteredTransformInitializer(
        fixed_image_resized,
        moving_image_resized,
        sitk.Euler2DTransform(),
        sitk.CenteredTransformInitializerFilter.GEOMETRY,
    )

    # Set up the image registration method
    registration_method = sitk.ImageRegistrationMethod()

    # Use Mattes Mutual Information as the metric
    registration_method.SetMetricAsMattesMutualInformation(numberOfHistogramBins=50)

    # Set the interpolator to linear
    registration_method.SetInterpolator(sitk.sitkLinear)

    # Use gradient descent optimizer
    registration_method.SetOptimizerAsGradientDescent(
        learningRate=1.0, numberOfIterations=100
    )

    # Set the optimizer scales from physical shift
    registration_method.SetOptimizerScalesFromPhysicalShift()

    # Set the initial transform
    registration_method.SetInitialTransform(initial_transform, inPlace=False)

    # Execute the registration
    final_transform = registration_method.Execute(
        sitk.Cast(fixed_image_resized, sitk.sitkFloat32),
        sitk.Cast(moving_image_resized, sitk.sitkFloat32),
    )

    # Print the final metric value and the optimizer's stopping condition
    print(f"Final metric value: {registration_method.GetMetricValue()}")
    print(f"Optimizer's stopping condition, {registration_method.GetOptimizerStopConditionDescription()}")

    # Resample the thermal image to align with the fixed image using the final transform
    moving_resampled = sitk.Resample(
        moving_image_resized,
        fixed_image_resized,
        final_transform,
        sitk.sitkLinear,
        0.0,
        moving_image_resized.GetPixelID(),
    )

    # Apply colormap to the thermal image to show thermal variations
    moving_resampled_np = sitk.GetArrayFromImage(moving_resampled)
    moving_resampled_np = cv2.normalize(
        moving_resampled_np, None, 0, 255, cv2.NORM_MINMAX
    )
    moving_resampled_np = 255 - moving_resampled_np  # Invert the thermal image
    moving_resampled_colored = cv2.applyColorMap(
        moving_resampled_np.astype(np.uint8), cv2.COLORMAP_JET
    )

    # Combine the thermal and optical images into a single 5-band image
    overlay = fixed_image_cv_resized.astype(np.float32)
    output = moving_resampled_colored.astype(np.float32)
    cv2.addWeighted(overlay, 0.5, output, 0.5, 0, output)

    four_band_with_thermal = np.dstack((overlay, moving_resampled_np))

    # Ensure the combined image is within the correct range
    output = np.clip(output, 0, 255).astype(np.uint8)

    # crop
    x_min, y_min, x_max, y_max = find_bounding_box(moving_resampled_colored)
    cropped_output = output[y_min:y_max, x_min:x_max]

    height, width, _ = output.shape

    return (
        final_transform,
        output,
        cropped_output,
        four_band_with_thermal,
    )  # four_band_with_thermal_cropped


# crop
def find_bounding_box(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    retval, thresh = cv2.threshold(gray, 50, 255, cv2.THRESH_BINARY)
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


def coreg(directory):
    # operate on single directory of images at a time
    nir_on_image = None
    nir_off_image = None
    lwir_image = None

    for filename in os.listdir(directory):
        if filename.endswith("-NIR-OFF.jpg"):
            nir_off_image = os.path.join(directory, filename)
        elif filename.endswith("-NIR-ON.jpg"):
            nir_on_image = os.path.join(directory, filename)
        elif filename.endswith(".pgm"):
            lwir_image = os.path.join(directory, filename)

    # Convert the PGM image to JPG with proper normalization
    image = cv2.imread(lwir_image, cv2.IMREAD_UNCHANGED)
    image_normalized = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)
    image_normalized = image_normalized.astype(np.uint8)
    jpg_path = os.path.splitext(lwir_image)[0] + ".jpg"
    cv2.imwrite(jpg_path, image_normalized)

    # Save the output images
    output_filename = f"registered.jpg"
    cropped_output_filename = f"cropped.jpg"
    final_image_filename = f"4_band_with_thermal.tiff"
    # final_cropped_filename = f"4_band_with_thermal_cropped.tiff"

    if not os.path.exists(output_filename) or not os.path.exists(
        cropped_output_filename
    ):
        # Perform the registration and get the final transform
        transform, output, cropped_output, four_band = mutual_information_registration(
            nir_off_image, jpg_path
        )

        cv2.imwrite(os.path.join(directory, output_filename), output)
        cv2.imwrite(os.path.join(directory, cropped_output_filename), cropped_output)
        cv2.imwrite(os.path.join(directory, final_image_filename), four_band)
        # cv2.imwrite(os.path.join(subdirectory, final_cropped_filename), four_band_cropped)
        W = four_band.shape[1]
        H = four_band.shape[0]

        # extract NIR and add as band
        NIR = cv2.imread(nir_on_image)
        NIR = cv2.cvtColor(NIR, cv2.COLOR_BGR2RGB)
        red_channel_NIR = NIR[:, :, 0]
        without_NIR = cv2.imread(nir_off_image)
        without_NIR = cv2.cvtColor(without_NIR, cv2.COLOR_BGR2RGB)
        red_channel = without_NIR[:, :, 0]
        Nir_band = cv2.subtract(red_channel_NIR, red_channel)
        Nir_band_resized = cv2.resize(Nir_band, (W, H))
        cv2.imwrite(os.path.join(directory, "NIR band.png"), Nir_band_resized)

        final_five_band = np.dstack((four_band, Nir_band_resized))
        final_five_band = np.clip(final_five_band, 0, 255).astype(np.uint8)
        final_five_band_reordered = np.transpose(final_five_band, (2, 0, 1))

        transform = from_origin(0, 100, 1, 1)  # Adjust as needed
        final_path = os.path.join(directory, "final_5_band.tiff")

        # Save the final 5-band image as a TIFF
        with rasterio.open(
            final_path,
            "w",
            driver="GTiff",
            height=final_five_band_reordered.shape[1],
            width=final_five_band_reordered.shape[2],
            count=final_five_band_reordered.shape[0],
            dtype=final_five_band_reordered.dtype,
            transform=transform,
        ) as dst:
            dst.write(final_five_band_reordered)

#        print("final_five_band dtype:", final_five_band_reordered.dtype)

#        print("final_five_band shape:", final_five_band.shape)
        
#        print(final_five_band[100:110,100:110,4])
#        print("Min value in final_five_band:", np.mean(final_five_band))
#        print("Max value in final_five_band:", np.median(final_five_band))
#        print(final_five_band)

#        tifffile.imwrite(final_path, final_five_band)


if __name__ == "__main__":
    import sys
    directory = sys.argv[1]
    print(f"Running coregistration on files in directory: {directory}")
    coreg(directory)
