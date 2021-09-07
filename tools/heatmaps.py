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

        color_map = 'coolwarm'
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

    cmd(array)

def show(array, color_map):
    # display frames, 32x24 res
    frame = []
    for row in range(24):
        line = []
        for pixel in range(32):
            value = array[row * 32 + pixel]
            line.append(value)
        frame.append(line)
    sns.heatmap(frame, cmap=color_map, annot=True).invert_xaxis()
    plt.show()

def cmd(frame):
    for h in range(24):
        for w in range(32):
            t = frame[h * 32 + w]
            c = "&"
            # pylint: disable=multiple-statements
            if t < 20:
                c = " "
            elif t < 23:
                c = "."
            elif t < 25:
                c = "-"
            elif t < 27:
                c = "*"
            elif t < 29:
                c = "+"
            elif t < 31:
                c = "x"
            elif t < 33:
                c = "%"
            elif t < 35:
                c = "#"
            elif t < 37:
                c = "X"
            # pylint: enable=multiple-statements
            print(c, end="")
        print()
    print()

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
