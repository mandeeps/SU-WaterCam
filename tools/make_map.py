#!/usr/bin/env python
# -*- coding: utf-8 -*-
# From multiple data files determine the boundaries of a body of water
# and export that boundary data for transmission over radio

from datetime import datetime
import os
from statistics import median
import pandas as pd
from compress_pickle import dump

def main(args):
    print(args)
    dir_name = os.getcwd()
    frames = []

    for count,arg in enumerate(args):
        frames.append(pd.read_csv(f'{arg}', index_col=[0], parse_dates=[0]))

    # use datetime part of first and last datafiles passed as part of
    # the generated file's name
    file_name_part1 = args[0].split('-')[1:]
    file_name_part2 = args[-1].split('-')[1:]
    print(file_name_part1, file_name_part2)
    file_name = file_name_part1[0] + 'Start-' + file_name_part2[0] + 'End'

    DF = pd.concat(frames)
    deriv = pd.DataFrame()
    rates = []

    for column in DF:
        value = abs(DF[column].diff())
        deriv[f'Change {column}'] = value
        #deriv[f'Rate of Change {column}'] = value / DF.index.to_series().diff().dt.total_seconds()

    for column in deriv:
        # TODO determine with real world testing which approach works better for
        # distinguishing water from other surfaces
        #value = abs(deriv[column] / DF.index.to_series().diff().dt.total_seconds())
        value = abs(deriv[column].diff())
        deriv[f'Rate {column}'] = value

# ToDo since taking multiple readings per boot, average them out discarding outliers to get most
# accurate reading per boot. Then take that data and use as basis for further calc

    # calculate mean of rate of change per pixel
    rates = deriv.loc[:, deriv.columns.str.contains('Rate')]
    change = []
    for column in rates:
        change.append(rates[column].mean()*10)

    print('Mean Change Rate Per Pixel *10: ', change)

    # round values for readability
    DF = DF.round(2)
    deriv = deriv.round(2)

    time_val = datetime.now().strftime('%Y%m%d-%H%M')

    changes_file = os.path.join(dir_name, f'data/changes-{file_name}-{time_val}.csv')
    dump(change, changes_file, compression='lzma')

    deriv_file = os.path.join(dir_name, f'data/derivative-{file_name}-{time_val}.csv')
    deriv.to_csv(deriv_file, index=True, header=True)
    print("Deriv file name: ", deriv_file)

    # divide pixels by change rate into relatively slow/fast categories
    # each recording period
    # median_value = median(change)
    # print('Median change rate value of entire set: ', median_value)

    # instead use reference pixel cluster
    ref_value = median([change[48],change[49],change[50]])
    print('Median value of the reference pixels: ', ref_value)

    extent = []
    for pixel in change:
        if pixel < ref_value: # median_value:
            extent.append(0) # water
        else:
            extent.append(1) # land

    extent_file = os.path.join(dir_name, f'data/extent-{file_name}-processed{time_val}.p')
    extent_text = os.path.join(dir_name, f'data/extent-{file_name}-processed{time_val}.txt')

    print('Extent: ', extent)

    with open(extent_text, 'w') as file_handler:
        file_handler.write(''.join(map(str, extent)))

    dump(extent, extent_file, compression='bz2')

if __name__ == '__main__':
    import sys
    # pass change rate pickle file
    sys.exit(main(sys.argv[1:]))
