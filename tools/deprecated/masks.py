#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# masks.py
# Create an image mask file for every category in a corresponding image
# using x/y coordinates from a JSON file which defines the category boundaries.
# Pass the specific JSON file as an argument to the script using -f
# Example: python masks.py -f 2018-01-01.json

from PIL import Image, ImageDraw
import json
import numpy as np
import argparse

# Parse command line arguments
parser = argparse.ArgumentParser(description='Create image masks from JSON file.')
parser.add_argument('-f', help='JSON file with x/y coordinates')
args = parser.parse_args()

# Open the JSON file containing the sets of x and y coordinates for a specific image
with open(args.f) as json_file:
    data = json.load(json_file)

# Get original image name and size
image_name = data['filename']
width = data['width']
height = data['height']

# Loop through each category in the JSON file
i = 0
for category in data['instance_list']:
    i += 1
    # get category name
    category_name = category['label_file_id']
    # get x and y coordinates
    points = category['points']
    # convert to list of tuples
    points = [(point['x'], point['y']) for point in points]
    # for each category, create a new transparent image of original image size
    mask = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    # create a drawing context
    draw = ImageDraw.Draw(mask)
    # Draw the polygon using the x and y coordinates from the JSON file
    draw.polygon(points, fill=(255, 0, 0, 255))
    # Save the mask image with the original filename and category name plus a 
    # number to avoid overwriting files in case of multiple masks of the same category
    mask.save(image_name + '_' + category_name + '_' + str(i) + '.png')
    # Save a binary mask / 2d numpy array (inefficient on disk space, fix later)
    # binary_mask = np.array(mask)
    # np.save(image_name + '_' + category_name + '.npy', binary_mask)
