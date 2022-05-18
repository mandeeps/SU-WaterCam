#!/usr/bin/env python3
# Align Thermal and Optical Images, Overlay and Save

import cv2
import numpy as np

# First load both images
thermal0 = cv2.imread("5-12.pgm") # thermal in pgm format
###"testThermal.pgm"###
visible = cv2.imread("5-12.jpg")
#"testVis.jpg"); # save the original

# Now convert color image to grayscale format to match thermal
# OpenCV homography based on keypoints and edges not color data
vis_gray = cv2.cvtColor(visible, cv2.COLOR_BGR2GRAY)
thermal = cv2.cvtColor(thermal0, cv2.COLOR_BGR2GRAY)
# Resize thermal to match visible - Flir Lepton 3.5 resolution is
# 160x120 and RPi camera is 2592x1944 
# opencv uses BGR not RGB and Y,X not X,Y
#thermal = cv2.resize(thermal0, (vis_gray.shape[1], vis_gray.shape[0]))

print(vis_gray.shape,thermal.shape, vis_gray.dtype,thermal.dtype)
# With thermal resized we can now find matching points to align
orb = cv2.ORB_create(500)
keypoints1, descriptors1 = orb.detectAndCompute(vis_gray, None)
keypoints2, descriptors2 = orb.detectAndCompute(thermal, None)

# with ORB we find the keypoints, then we use brute force matching
# to match the keypoints
matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
print(descriptors1, descriptors2)
matches = matcher.match(descriptors1, descriptors2)

# sort matches
matches = sorted(matches, key = lambda x:x.distance)

# only use top 90% of matches
matches = matches[:int(len(matches)*0.9)]
num_matches = len(matches)

p1 = np.zeros((num_matches, 2))
p2 = np.zeros((num_matches, 2))

for i in range(len(matches)):
    p1[i, :] = keypoints1[matches[i].queryIdx].pt
    p2[i, :] = keypoints2[matches[i].trainIdx].pt
    
# homography matrix
homography, mask = cv2.findHomography(p1, p2, cv2.RANSAC)

# transform thermal based on matrix to line up with vis_gray
align_thermal = cv2.warpPerspective(thermal, homography, (visible.shape[1], visible.shape[0]))
cv2.imwrite('transform.jpg', align_thermal)

align = cv2.cvtColor(align_thermal, cv2.COLOR_GRAY2BGR)
print(align.shape,visible.shape, align.dtype, visible.dtype)


overlay = cv2.addWeighted(visible, 0.4, align, 0.1, 0)
cv2.imwrite('overlay.jpg', overlay)



show = cv2.drawKeypoints(visible, keypoints1, None, color=(255,0,255), flags=0)
cv2.imshow('Keypoints', show)
show = cv2.drawMatches(vis_gray, keypoints1, thermal, keypoints2, matches[:500], None, flags=2)

cv2.imshow('Matches', show)
#cv2.waitKey(0)
#cv2.destroyAllWindows()


# Show final results
cv2.imshow("Aligned Image 2", align_thermal)
cv2.waitKey(0)
cv2.destroyAllWindows()
