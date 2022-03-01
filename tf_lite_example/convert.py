#!/usr/bin/env python3
import tensorflow as tf

original = tf.keras.models.load_model(
    'fine_tuned_flood_detection_model', custom_objects=None, compile=True, options=None
)

# Convert the model.
converter = tf.lite.TFLiteConverter.from_keras_model(original)
tflite_model = converter.convert()

# Save the model.
with open('model.tflite', 'wb') as f:
  f.write(tflite_model)

