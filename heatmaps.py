#!/usr/bin/env python3
# passed deriv csv, display heatmap of rate-of-change values
import sys
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

def main(args):
    print(args[1])
    data = pd.read_csv('%s' % args[1])

    rates = data.loc[:, data.columns.str.contains('Rate')]
    #print(rates)
    avgs = []
    for column in rates:
    # get average of change rate for each pixel
        avgs.append(rates[column].mean())

    #print(avgs)
    
    # save each pixel avg in 32x24 frame
    frame = []
    for row in range(24):
        line = []
        for pixel in range(32):
            temp = avgs[row * 32 + pixel]
            line.append(temp)
        frame.append(line)

    sns.heatmap(frame, cmap='coolwarm', annot=True)
    plt.show()

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
