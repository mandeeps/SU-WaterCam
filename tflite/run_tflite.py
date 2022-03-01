#!/usr/bin/env python3
# Glue code to run TF Lite inferences on images from Raspberry Pi
# optical and thermal cameras

# Images directory contains both types of images when using Flir Lepton
# only optical photos when using MLX90640, that stores temperature data
# in data directory

from os import path, listdir
import time
from tflite_runtime.interpreter import Interpreter 
from PIL import Image
import numpy as np

DIR = '/home/pi/SU-HotwaterCam/'
IMG = path.join(DIR, 'images')
DATA = path.join(DIR, 'data')

model = 'ei-flood2-transfer-learning-tensorflow-lite-int8-quantized-model.lite'
labels = [line.rstrip('\n') for line in open('labels.txt')]
interpreter = Interpreter(model)
print('Model Loaded Successfully.')

def set_input_tensor(interpreter, image):
  tensor_index = interpreter.get_input_details()[0]['index']
  input_tensor = interpreter.tensor(tensor_index)()[0]
  input_tensor[:, :] = image

def classify_image(interpreter, image, top_k=1):
  set_input_tensor(interpreter, image)

  interpreter.invoke()
  output_details = interpreter.get_output_details()[0]
  output = np.squeeze(interpreter.get_tensor(output_details['index']))

  scale, zero_point = output_details['quantization']
  output = scale * (output - zero_point)

  ordered = np.argpartition(-output, 1)
  return [(i, output[i]) for i in ordered[:top_k]][0]

interpreter.allocate_tensors()
_, height, width, _ = interpreter.get_input_details()[0]['shape']
print('Image Shape (', width, ',', height, ')')

def run(DIR):
    for photo in listdir(IMG):
        f = path.join(IMG, photo)
        print(f)
        image = Image.open(f).convert('RGB').resize((width, height))
        # Classify the image.
        time1 = time.time()
        label_id, prob = classify_image(interpreter, image)
        time2 = time.time()
        classification_time = np.round(time2-time1, 3)
        print('Classification Time =', classification_time, 'seconds.')
        #print(label_id, prob)
        # Return the classification label of the image.
        #classification_label = labels[label_id]
        print('Image Label is :', label_id, ', with Accuracy :', np.round(prob*100, 2), '%.')

print('Testing on photos')
run(DIR)
