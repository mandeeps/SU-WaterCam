#!/usr/bin/env python3
import pickle
import lzma
import seaborn as sns
import matplotlib.pyplot as plt

def main(argv):
    rates_file = lzma.open(argv[1], 'rb')
    array = pickle.load(rates_file)
    color_map = 'coolwarm_r'
    show(array, color_map)

def show(array, color_map):
    # display frames, 32x24 res
    frame = []
    for row in range(24):
        line = []
        for pixel in range(32):
            value = array[row * 32 + pixel]
            line.append(value)
        frame.append(line)
    sns.heatmap(frame, cmap=color_map, annot=True)
    plt.show()

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
