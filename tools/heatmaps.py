#!/usr/bin/env python3
# passed deriv csv, display heatmap of rate-of-change values
# passed data csv, display frames as heatmaps
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

def main(args):
    df = pd.read_csv('%s' % args[1])

    if args[1].__contains__('deriv'):
        rates = df.loc[:, df.columns.str.contains('Rate')]

        avgs = []
        for column in rates:
        # get average of change rate for each pixel
            avgs.append(rates[column].mean())

        color_map = 'coolwarm_r'
        array = avgs
        show(array, color_map)

    else:
        color_map = 'coolwarm'

        # process each frame within the dataframe
        for i in range(len(df)):
            temps = df.values[i]

            array = []

            for num in range(1, temps.size): # strip datetime values out
                array.append(temps[num])

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
