# Copyright 2024 Carnegie Mellon University
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import argparse
import os


def main():
    parser = argparse.ArgumentParser(
        description='Compile a TTPython program to the DFG')

    parser.add_argument('file', metavar='F', type=str, help='file to compile')
    parser.add_argument(
        '-o',
        '--out',
        nargs='?',
        default='./output',
        help='output file name of the compile TTPython program')
    parser.add_argument(
        '--ast',
        action='store_true',
        help='flag whether to print out the compiled program\'s ast')
    parser.add_argument(
        '-g',
        '--graph',
        action='store_true',
        help='flag whether to show the compiled program\'s dataflow graph')
    parser.add_argument(
        '--print_graph',
        action='store_true',
        help='flag whether to print a textual representation of the graph')
    parser.add_argument('-d',
                        '--debug',
                        action='store_true',
                        help='flag whether to show debug information')

    args = parser.parse_args()

    # TODO: allow multi-file compilation
    file_path = args.file
    is_debug = args.debug
    is_py = file_path[-3:] == '.py'
    if not is_py:
        print("file given is not a Python program")
        return 1

    file_name = file_path.split('/')[-1][:-3]

    out_path = args.out
    if out_path[-1] != '/':
        out_path = out_path + '/'
    pickle_path = f"{out_path}{file_name}.pickle"
    graph_file_path = f"{out_path}{file_name}.png"

    ast = args.ast

    show_graph = args.graph
    print_graph = args.print_graph

    from ticktalkpython.Compiler import TTCompile
    from ticktalkpython.Compiler import dump_pickle
    from ticktalkpython.Compiler import draw_graph
    from ticktalkpython.Compiler import print_text_graph

    import ticktalkpython.DebugLogger as log
    log.set_base_logger_info()
    if is_debug:
        log.set_base_logger_debug()

    print(f"Compiling '{file_name}'")
    graph = TTCompile(file_path, os.path.dirname(file_path))
    dump_pickle(graph, pickle_path)
    from runrtm import write_graph_checksum
    write_graph_checksum(pickle_path)
    print(f"Compiled output found at {pickle_path}")

    if print_graph:
        print_text_graph(graph)

    if show_graph:
        draw_graph(graph, graph_file_path)


if __name__ == "__main__":
    main()
