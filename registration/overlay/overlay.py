#!/usr/bin/env python
# overlay thermal data onto photo without alignment

import cv2

img = cv2.imread('source_new.jpg', 1)
gray_img = cv2.imread('target_new.jpg', 1)

heatmap_img = cv2.applyColorMap(gray_img, cv2.COLORMAP_JET)

fin = cv2.addWeighted(heatmap_img, 0.7, img, 0.9, 0)
cv2.imshow('overlay', fin)
cv2.waitKey()
