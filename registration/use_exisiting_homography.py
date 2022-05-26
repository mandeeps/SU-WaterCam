#!/usr/bin/env python3
# use existing homography file to transform and overlay images
import cv2
import numpy as np

h = np.load('homography.npy')
src = cv2.imread('v640.jpg', cv2.IMREAD_COLOR)
dst = cv2.imread('t640.jpg', cv2.IMREAD_COLOR)
warp = cv2.warpPerspective(src, h, (dst.shape[1], dst.shape[0]))

cv2.imshow('warp', warp)
cv2.waitKey(0)
