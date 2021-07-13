#!/usr/bin/env python3
import pickle
import seaborn as sns
import matplotlib.pyplot as plt

def main(argv):    
    array = pickle.load(open(argv[1], 'rb'))  
    colorMap = 'coolwarm_r'
    show(array, colorMap)	

def show(array, colorMap):
     # display frames, 32x24 res
     frame = []
     for row in range(24):
         line = []
         for pixel in range(32):
             value = array[row * 32 + pixel]
             line.append(value)
         frame.append(line)
     sns.heatmap(frame, cmap=colorMap, annot=True)
     plt.show()

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
