# Copyright 2021 Carnegie Mellon University
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
'''
The TTPython Graph Compiler takes a TTPython source file, reads it, creates an
abstract syntax tree, walks the tree, and translates it into a TTPython graph.
Here's the compilation process, in a nutshell:

* Read the file

* Find all ``@SQify``-ed functions (and any others with valid TTPython
  decorators), including those that might be found via ``import``.
  :ref:`Instructions.py<instructions>` contains several examples.

* Build a table of them, indexed by their name, and attach to each the function
  body

* Find the ``@GRAPHify``-ed function

* Extract and record the arguments as graph inputs

* Walk the abstract syntax tree of the body, translating function calls into
  ``SQ`` instances and interconnect these with ``Port``s to represent the flow
  of values from ``SQ`` outputs to ``SQ`` inputs. There will be one ``Port``
  instance per ``SQ`` output with a value name, and a output ``Port`` will
  send its output value to all input ports sharing the value name. A ``Port``
  can be considered as an any-to-any directed connector from SQ outputs to SQ
  inputs.

* Once complete, write out a representation of the graph (``SQ`` instances and
  ``Port`` instances)
'''

import ast
import json
import pickle
import os

from .CompilerAssistVisitors import import_sqified_functions_from_module
from .CompilerRules import TTGraphCompilationVisitor
from .CompilerTypecheck import TTTypechecker
from .Error import TTSyntaxError
from .Graph import TTGraph
from . import DebugLogger
from .FiringRule import *
from . import CompilerAssistVisitors
from . import CompilerUtils

logger = DebugLogger.get_logger('Compiler')

from collections import defaultdict

# A useful tool:  https://python-ast-explorer.com


def label_dfs(graph, curr_node, curr_level, visited: set):
    '''
    Visit and label nodes in a bfs fashion from input to output direction.
    will (re)label the max path to a node. visited prevents infinite recursion
    caused by loops

    :return: Returns the max level in the graph

    :rtype: int
    '''

    # skip this node if visited more than input arcs (to prevent infinite loops)
    if curr_node in visited:
        # this node does not count in the level
        return curr_level - 1
    visited.add(curr_node)
    graph.nodes[curr_node]['level'] = max(
        curr_level, graph.nodes[curr_node].get('level', curr_level))
    max_level = curr_level

    for downstream_node in graph.successors(curr_node):
        visited.add(curr_node)
        max_level = max(
            max_level,
            label_dfs(graph, downstream_node, curr_level + 1, visited))

    # pop your node when you aren't on the path
    visited.remove(curr_node)
    return max_level


def add_topological_labels(graph, node_name_list):
    '''
    Adds topological labels to graph
    '''
    max_level = 0
    for node_name in node_name_list:
        max_level = max(max_level, label_dfs(graph, node_name, 0, set()))


def draw_graph(ttgraph: TTGraph, output_file_name):
    import networkx as nx
    '''
    Create and display a TTGraph
    '''
    graph_input_nodes = []
    graph_output_nodes = []
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(ttgraph.sqs)  # internal nodes
    graph_edge_label_dict = {}

    ipp_to_sq = ttgraph.get_ipp_to_sq_dict()

    # Every arc in the symbol table should either have
    #   a source and at least one destination:  normal SQ
    #   a source but no destination:            graph output
    #   a destination but no source:            graph input

    source_vars = ttgraph.source_var_names()

    logger.debug(f'sources are {ttgraph.source_var_names()}')
    for source_var in source_vars:
        graph_input_nodes.append(source_var)
        graph.add_node(source_var, level=0)

        for dest_sq, port_num in ipp_to_sq[source_var]:
            graph.add_edge(source_var, dest_sq, label=source_var)
            graph_edge_label_dict[(source_var, dest_sq)] = source_var

    for sq in ttgraph.sqs:
        for opp in sq.get_opps():
            name = opp.data_name
            logger.debug(f"looking for {name} in ipps")

            if name in ipp_to_sq:
                logger.debug(f"{name} has dests {ipp_to_sq[name]}")

                # all inter-SQ links for the opp
                for dest_sq, port_num in ipp_to_sq[name]:
                    graph.add_edge(sq, dest_sq, label=name)
                    graph_edge_label_dict[(sq, dest_sq)] = name

            # sink opp
            else:
                logger.debug(f"{name} is a sink var")
                graph_output_nodes.append(name)
                graph.add_node(name, level=-1)
                graph.add_edge(sq, name, label=name)
                graph_edge_label_dict[(sq, name)] = name

    max_level = add_topological_labels(graph, graph_input_nodes)

    # color all nodes green first
    nx.set_node_attributes(graph, "green", name="fillcolor")
    # color nodes that will periodically execute
    nx.set_node_attributes(
        graph,
        {node: "lightblue"
         for node in ttgraph.sqs if node.is_streaming},
        name="fillcolor")
    # color streaming source nodes
    nx.set_node_attributes(graph, {
        node: 'orange'
        for node in ttgraph.sqs
        if node.firing_rule_type is TTFiringRuleType.TimedRetrigger
    },
                           name="fillcolor")
    nx.set_node_attributes(graph, {
        node: 'yellow'
        for node in ttgraph.sqs
        if node.firing_rule_type is TTFiringRuleType.SequentialRetrigger
    },
                           name="fillcolor")
    # all input output arcs painted in red
    nx.set_node_attributes(
        graph,
        {node: "red"
         for node in graph_input_nodes + graph_output_nodes},
        name="fillcolor")
    nx.set_node_attributes(graph, "filled", name="style")

    # switch to pygraphviz
    py_graph = nx.drawing.nx_agraph.to_agraph(graph)

    # set level of SQs (equivalent to rank) to be the same
    ranks = defaultdict(list)
    for node in py_graph.iternodes():
        rank = node.attr['level']
        ranks[rank].append(node)

    for node_list in ranks.values():
        py_graph.add_subgraph(node_list, rank='same')

    py_graph.layout('dot')
    py_graph.draw(output_file_name)
    logger.info(f"Saved graph image to {output_file_name}")

    # to allow base requirements
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg
    img = mpimg.imread(output_file_name)
    plt.imshow(img, aspect='equal')
    plt.axis('off')
    plt.show()


def print_text_graph(graph: TTGraph):
    for sq in graph.sqs:
        print(f"{sq}: IPPs={sq.get_ipps()}; OPPs={sq.get_opps()}")


def TTCompile(ttpython_path, library_path=None):
    '''
    Read a TTPython file and convert it to a ``TTGraph`` . Save the graph in one
    or several output formats.

    :param ttpython_path: the TTPython source file.

    :type ttpython_path: string

    :return: Returns the compiled graph

    :rtype: TTGraph
    '''

    if len(ttpython_path) == 0:
        raise Exception("The filename cannot be blank")

    with open(ttpython_path, "r") as source:
        module = ast.parse(source.read())

    with open(ttpython_path, "r") as source:
        source_list = source.readlines()

    # build an initial context
    context_creator = CompilerAssistVisitors.TTGraphContextCreation(
        source_list, library_path, CompilerUtils.Context(), ttpython_path)
    context_creator.visit(module)

    # Import the core BinOp library
    logger.debug("Importing the Instructions library")
    import_sqified_functions_from_module('.Instructions', library_path,
                                         context_creator.visited_modules,
                                         context_creator.context)

    typechecker = TTTypechecker(source_list, library_path,
                                context_creator.context, ttpython_path)
    typechecker.typecheck(module)

    try:
        # TTGraph holds the state of the translation
        compile_visitor = TTGraphCompilationVisitor(context_creator.context,
                                                    source=source_list,
                                                    pathname=ttpython_path)
        graph = compile_visitor.compile_graph(module)

        for sq in graph.sqs:
            logger.debug(f"SQ {sq} will be assigned to {sq.constraints}")

        logger.info("Compilation successful")
        return graph

    except TTSyntaxError as error:
        logger.error(repr(error))
        raise error


def dump_json(graph, json_path):
    '''
    Dumps a TTGraph out as JSON
    '''
    logger.info(f"Writing {json_path}")
    message = "JSON output may fail if keyword arguments contain "
    message += "objects that lack a JSON serialization."
    logger.warning(message)
    with open(json_path, "w") as json_out:
        json.dump(graph.json(), json_out, indent=4)


def dump_pickle(graph, pickle_path):
    '''
    Dumps a TTGraph out as Pickle
    '''
    logger.info(f"Writing {pickle_path}")
    with safe_open(pickle_path, 'wb') as pickle_out:
        pickle.dump(graph, pickle_out)


def safe_open(path, args):
    '''
    Open "path" for writing, creating any parent directories as needed.
    '''
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return open(path, args)
