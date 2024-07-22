#!/bin/sh
./capture &
./venv/bin/python ./tools/take_nir_photos.py
