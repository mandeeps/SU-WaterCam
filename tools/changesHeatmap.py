#!/usr/bin/env python3
import pickle
from compress_pickle import load
import seaborn as sns
import matplotlib.pyplot as plt

def main(argv):
    array = load(argv)
    color_map = 'coolwarm'
    show(array, color_map)

def show(array, color_map):
    # display frames, 32x24 res
    frame = []
    for row in range(24):
        line = []
        for pixel in range(32):
            value = array[row * 32 + pixel]*100
            line.append(value)
        frame.append(line)
    sns.heatmap(frame, cmap=color_map, annot=True)
    plt.show()

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv[1]))
