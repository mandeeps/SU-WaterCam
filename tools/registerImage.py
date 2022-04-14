#!/usr/bin/env python3
#
import numpy as np
import imutils
import argparse
import cv2

def align(image, template, maxFeatures=500, keepPercent=0.2, 
    debug=True):
    # start by converting both images to grayscale
    imageGray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    templateGray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    # keypoint detection
    orb = cv2.ORB_create(maxFeatures)
    kpsA, descsA = orb.detectAndCompute(imageGray, None)
    kpsB, descsB = orb.detectAndCompute(templateGray, None)
    
    # match by using distances between features
    method = cv2.DESCRIPTOR_MATCHER_BRUTEFORCE_HAMMING
    matcher = cv2.DescriptorMatcher_create(method)
    matches = matcher.match(descsA, descsB, None)
    
    matches = sorted(matches, key=lambda x:x.distance)
    keep = int(len(matches) * keepPercent)
    matches = matches[:keep]
    
    if debug:
        matchedVis = cv2.drawMatches(image, kpsA, template, kpsB,
            matches, None)
        matchedVis = imutils.resize(matchedVis, width=1000)
        cv2.imshow("Matched points", matchedVis)
        cv2.waitKey(0)
        
    ptsA = np.zeros((len(matches), 2), dtype="float")
    ptsB = np.zeros((len(matches), 2), dtype="float")
    
    # loop
    for (i,m) in enumerate(matches):
        ptsA[i] = kpsA[m.queryIdx].pt
        ptsB[i] = kpsB[m.trainIdx].pt
    (H, mask) = cv2.findHomography(ptsA, ptsB, cv2.RANSAC)
    (h,w) = template.shape[:2]
    aligned = cv2.warpPerspective(image, H, (w,h))
    return aligned

ap = argparse.ArgumentParser()
ap.add_argument("-i", "--image", required=True, help="image path")
ap.add_argument("-t", "--template", required=True, help="template path")
args = vars(ap.parse_args())

image = cv2.imread(args["image"])
template = cv2.imread(args["template"])

# alignment
aligned = align(image, template, debug=True)

aligned = imutils.resize(aligned, width=600)
template = imutils.resize(template, width=600)
overlay = template.copy()
output = aligned.copy()
cv2.addWeighted(overlay, 0.5, output, 0.5, 0, output)
cv2.imshow(output)
cv2.waitKey(0)
