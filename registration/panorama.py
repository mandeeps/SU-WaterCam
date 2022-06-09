#!/usr/bin/env python3
# panorama opencv
import cv2

# Read the images to be aligned
im1 =  cv2.imread("left.jpg");
im2 =  cv2.imread("right.jpg");

stitchy=cv2.Stitcher.create()
(dummy,output)=stitchy.stitch([im1,im2])
 
if dummy != cv2.STITCHER_OK:
  # checking if the stitching procedure is successful
  # .stitch() function returns a true value if stitching is
  # done successfully
    print("not ready")
else:
    print('panorama ready')
 
# final output
cv2.imshow('final result',output)
 
cv2.waitKey(0)
