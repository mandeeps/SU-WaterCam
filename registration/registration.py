#!/usr/bin/env python3

import numpy as np
import imutils
import cv2

MAX_FEATURES = 500
GOOD_MATCH_PERCENT = 0.15

# function to align the thermal and visible image
# it returns the homography matrix 
def alignImages(crop_img, template):
  print(crop_img.shape, template.shape)
  # Convert color image to grayscale
  crop_gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)

  # SIFT algorithm
  sift = cv2.SIFT_create()
  keypoints1, descriptors1 = sift.detectAndCompute(crop_gray, None)
  keypoints2, descriptors2 = sift.detectAndCompute(template, None)
  
  # FLANN matching
  index_params = dict(algorithm = 1, trees=5)
  search_params = dict(checks = 50)
  flann = cv2.FlannBasedMatcher(index_params, search_params)
  matches = flann.knnMatch(descriptors1, descriptors2, k=2)
  
  # sort and discard matches by quality
  good = []
  for m,n in matches:
      if m.distance < 0.75*n.distance:
          good.append(m)

  print('number of matches: ', len(good))
  points1 = np.float32([keypoints1[m.queryIdx].pt for m in good]).reshape(-1,1,2)
  points2 = np.float32([keypoints2[m.trainIdx].pt for m in good]).reshape(-1,1,2)
 
  # Find homography
  h, mask = cv2.findHomography(points2,points1, cv2.RANSAC, 5.0)
  matchesMask = mask.ravel().tolist()
  
  # show matching points
  draw_params = dict(matchColor = (0,255,0), singlePointColor=None, matchesMask=matchesMask, flags = 2)
  matches = cv2.drawMatches(crop_img, keypoints1, template, keypoints2, good, None, **draw_params)
  cv2.imshow('matches', matches)
  
  # Use homography
  height, width, channels = template.shape
  #pts = np.float32([[0,0],[0,height],[width,height],[width,0] ]).reshape(-1,1,2)
  #dst = cv2.perspectiveTransform(pts, h)
  #img2 = cv2.polylines(template,[np.int32(dst)],True,255,3, cv2.LINE_AA)
  #cv2.imwrite('img2.jpg', img2)
  registered = cv2.warpPerspective(crop_img, h, (width, height))

  cv2.imshow('registered', registered)
  cv2.waitKey(0) 
  return registered, h

# load the thermal image, convert it to grayscale, and detect edges
thermal = cv2.imread('5-12.jpg', cv2.IMREAD_COLOR)
template = cv2.cvtColor(thermal, cv2.COLOR_BGR2GRAY)
template = cv2.Canny(template, 50, 200)
(tH, tW) = template.shape[:2]

# load the image, convert it to grayscale, and initialize the
# bookkeeping variable to keep track of the matched region
image = cv2.imread('test.jpg')
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
found = None

# loop over the scales of the image
for scale in np.linspace(0.2, 1.0, 20)[::-1]:
    # resize the image according to the scale, and keep track
    # of the ratio of the resizing
    resized = imutils.resize(gray, width = int(gray.shape[1] * scale))
    r = gray.shape[1] / float(resized.shape[1])

    # detect edges in the resized, grayscale image and apply template
    # matching to find the template in the image
    edged = cv2.Canny(resized, 50, 200)
    result = cv2.matchTemplate(edged, template, cv2.TM_CCOEFF)
    (_, maxVal, _, maxLoc) = cv2.minMaxLoc(result)

    # draw a bounding box around the detected region
    clone = np.dstack([edged, edged, edged])
    cv2.rectangle(clone, (maxLoc[0], maxLoc[1]),
        (maxLoc[0] + tW, maxLoc[1] + tH), (0, 0, 255), 2)

    # if we have found a new maximum correlation value, then update
    # the bookkeeping variable
    if found is None or maxVal > found[0]:
        found = (maxVal, maxLoc, r)

# unpack the bookkeeping variable and compute the (x, y) coordinates
# of the bounding box based on the resized ratio
(_, maxLoc, r) = found
(startX, startY) = (int(maxLoc[0] * r), int(maxLoc[1] * r))
(endX, endY) = (int((maxLoc[0] + tW) * r), int((maxLoc[1] + tH) * r))

# draw a bounding box around the detected result
cv2.rectangle(image, (startX, startY), (endX, endY), (0, 0, 255), 2)
crop_img = image[startY:endY, startX:endX]

# cropping out the matched part of the thermal image
crop_img = cv2.resize(crop_img, (thermal.shape[1], thermal.shape[0]))

# both images are concatenated horizontally and saved
final = np.concatenate((crop_img, thermal), axis = 1)
cv2.imwrite('concat.jpg', final)
overlay1 = cv2.addWeighted(crop_img, 0.5, thermal, 0.5, 0)
cv2.imwrite('unalignedOverlay.jpg', overlay1)

final, homography = alignImages(crop_img, thermal)
print("Estimated homography : \n",  homography)
print(final.shape, thermal.shape)
cv2.imshow('Registered Image Overlay', final)
overlay = cv2.addWeighted(thermal, 0.1, final, 0.9, 0)
cv2.imwrite('aligned.jpg', overlay)
cv2.waitKey(0)
