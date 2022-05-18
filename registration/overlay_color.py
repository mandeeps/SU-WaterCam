#!/usr/bin/env python3
# Align two color images of the same size, overlay one on the other

import cv2
import numpy as np

# First load both images
im1 = cv2.imread("left.jpg")
im2 = cv2.imread("right.jpg")

orb = cv2.ORB_create(100)
keypoints1, descriptors1 = orb.detectAndCompute(im1, None)
keypoints2, descriptors2 = orb.detectAndCompute(im2, None)

# with ORB we find keypoints, then we use brute force matching
# to match the keypoints across the images
matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
matches = matcher.match(descriptors1, descriptors2)

# sort matches
matches = sorted(matches, key = lambda x:x.distance)

# only use top matches
matches = matches[:int(len(matches)*0.9)]
num_matches = len(matches)

p1 = np.zeros((num_matches, 2))
p2 = np.zeros((num_matches, 2))

for i in range(len(matches)):
    p1[i, :] = keypoints1[matches[i].queryIdx].pt
    p2[i, :] = keypoints2[matches[i].trainIdx].pt
    
# homography matrix, using RANSAC algorithm
homography, mask = cv2.findHomography(p1, p2, cv2.RANSAC)

# transform im2 to match im1
align_im2 = cv2.warpPerspective(im2, homography, (im1.shape[1], im1.shape[0]))
cv2.imwrite('transform.jpg', align_im2)

overlay = cv2.addWeighted(im1, 0.4, align_im2, 0.1, 0)
cv2.imwrite('overlay.jpg', overlay)


show = cv2.drawMatches(im1, keypoints1, im2, keypoints2, matches[:500], None, flags=2)

cv2.imshow('Matches', show)
cv2.waitKey(0)

show = cv2.drawKeypoints(im1, keypoints1, None, color=(255,0,255), flags=0)
cv2.imshow('keyponts', show)


# Show final results
#cv2.imshow("Aligned Image 2", overlay)

stitchy=cv2.Stitcher.create()
(dummy,output)=stitchy.stitch([im1,align_im2])
if dummy != cv2.STITCHER_OK:
  # checking if the stitching procedure is successful
  # .stitch() function returns a true value if stitching is
  # done successfully
    print("stitching ain't successful")
else:
    print('Your Panorama is ready!!!')
# final output
cv2.imshow('final result',output)
 
cv2.waitKey(0)


cv2.waitKey(0)
cv2.destroyAllWindows()
