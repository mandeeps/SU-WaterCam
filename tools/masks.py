#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# masks.py
# Create an image mask file for every category in a corresponding image
# using x/y coordinates from a JSON file which defines the category boundaries.

from PIL import Image, ImageDraw
import json
import numpy as np

# Open the JSON file containing the sets of x and y coordinates for a specific image
with open('image.json') as json_file:
    data = json.load(json_file)

# Get original image name and size
image_name = data['filename']
width = data['width']
height = data['height']

# Loop through each category in the JSON file
for category in data['instance_list']:
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
    # Save the mask image with the original filename and category name
    mask.save(image_name + '_' + category_name + '.png')
    # Save a binary mask / 2d numpy array (inefficient on disk space, fix later)
    # binary_mask = np.array(mask)
    # np.save(image_name + '_' + category_name + '.npy', binary_mask)
