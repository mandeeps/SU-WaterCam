# Copyright 2021 Carnegie Mellon University
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

'''
Mapping relates the graph of SQs to the physical system: SQs are assigned to
ensembles and told where their outputs should be sent. Mapping is done after
compilation and before graph interpretation/execution.

Mapping is done based for a known network of ensembles. There may be constraints
on mapping from the graph itself, and it is the Mapper's responsibility to
satisfy these constraints. These constraints may restrict SQs to only run on
specific Ensembles (or types of Ensembles) or may specify some multi-SQ
constraints like an upper bound on latency.

Within the runtime environment, the mapping should be done using either a static
system description or a runtime-generated description provided by the runtime
manager (RTM). The RTM should handle mapping as part of the graph instantiation
process before it kicks off interpretation by injecting initial input tokens.
'''

from typing import Union, List
import random

from . import Port
from . import Graph
from . import DebugLogger
from . import Query
from . import Constants

logger = DebugLogger.get_logger('Mapper')

def static_mapping(graph, ensemble_infos):
    '''
    Given that the graph has SQs with annotated constraints (from TTQuery
    within the program), the mapping returned will ensure that mapped SQs have
    a corresponding compatible ensemble

    :param graph: The graph to map entirely onto a singular ensemble
    :type graph: TTGraph

    :param ensemble: The ensembles to map the entire graph onto
    :type ensemble: [TTEnsembleInfo]

    :return: A dictionary using SQ names as keys and ensemble names as values,
        to represent which SQ the ensemble is mapped onto. This is used to
        instantiate all the SQs on their corresponding ensemble. An SQ is
        uniquely named and uniquely mapped to one ensemble.
    :rtype: dict
    '''
    static_map = {}

    for this_sq in graph.sqs:
        constraints = this_sq.constraints
        filtered_ens = [
            ens_info.name for ens_info in ensemble_infos
            if len(constraints) == 0
            or Query.TTQuery(constraints, Query.QueryOp.AND).test(ens_info)
        ]

        if not filtered_ens:
            logger.warning(
                "Could not find an ensemble satisfying constraints: "
                f"{constraints} for SQ {this_sq}")
            filtered_ens = [Constants.RUNTIME_MANAGER_ENSEMBLE_NAME]
        # TODO: pick the mapped ensemble through heuristics instead of
        # arbitrarily
        static_map[this_sq.sq_name] = filtered_ens[0]

    return static_map


# creates list of arc destinations
def generate_mapping(graph: Graph.TTGraph, mapping) -> List[Port.TTMappedPort]:
    '''
    Updates the arcs in the given graph with the provided mapping

    :param graph: The graph to map entirely onto a singular ensemble
    :type graph: TTGraph

    :param mapping: The ensemble to map the entire graph onto
    :type mapping: TTEnsemble

    :return: list of TTMappedPort
    :rtype: list
    '''
    ipp_to_sq = graph.get_ipp_to_sq_dict()
    arc_dests = {}

    for sq in graph.sqs:
        dest_list = []
        for opp in sq.get_opps():
            dest_sqs_set = ipp_to_sq[opp.data_name]
            opp_arcs = [
                Port.TTMappedPort(mapping[dest_sq.sq_name], dest_sq.sq_name,
                                     pn) for dest_sq, pn in dest_sqs_set
            ]
            dest_list.append(opp_arcs)

        arc_dests[sq] = dest_list

    return arc_dests

class TTMapper():
    '''
    The TTMapper handles mapping based on a system description (a set of
    ensembles) and a graph. The exact format of the system description is
    subject to change, and will likely become more complex as mapping algorithms
    become more sophisticated

    :param graph: The compiled graph representing a TTPython program, which is
        ready to be mapped to the set of ensembles
    :type graph: TTGraph

    :param ensembles: A set of ensembles composing the system; this is the system
        description
    :type ensembles: list(TTEnsembles)
    '''

    def __init__(self, graph: Graph.TTGraph, ensembles=None):
        self.graph = graph
        self.ensembles = [] if ensembles is None else ensembles

    @staticmethod
    def trivial_mapping(graph, ensemble):
        '''
        Trivial mapping puts all SQs onto the same ensemble. It is the simplest
        form of mapping, and is useful for testing basic elements of the graph
        interpretation and/or code execution. Most of the work here is setting
        the arc destinations so that we know how to tag output tokens.

        :param graph: The graph to map entirely onto a singular ensemble
        :type graph: TTGraph

        :param ensemble: The ensemble to map the entire graph onto
        :type ensemble: TTEnsemble

        :return: A dictionary using SQ names as keys and ensemble names as
            values, to represent which SQ the ensemble is mapped onto. This is
            used to instantiate all the SQs on their corresponding ensemble. An
            SQ is uniquely named and uniquely mapped to one ensemble.
        :rtype: dict
        '''
        mapped_graph = {}

        ensemble_name = ensemble.name

        # Setup destination mappings for the input arcs
        input_arc_dict = graph.input_arc_dict()
        for input_symbol in input_arc_dict:
            input_arc = input_arc_dict[input_symbol]

            for dest_sq in input_arc.dest_sq_list:
                port_number = dest_sq.port_number_of_input_symbol(input_arc.symbol)
                for this_port_number in port_number:
                    arc_destination = Port.TTMappedPort(
                        ensemble_name,
                        dest_sq.sq_name,
                        this_port_number)
                    logger.debug(
                        'Adding arc_destination %s to input-arc symbol %s' %
                        (arc_destination, input_arc.symbol))
                    input_arc.dest_mapping.append(arc_destination)

        for this_sq in graph.sq_list:
            for dest_sq in this_sq.output_arc.dest_sq_list:
                port_number = dest_sq.port_number_of_input_symbol(this_sq.output_arc.symbol)
                for this_port_number in port_number:
                    arc_destination = Port.TTMappedPort(
                        ensemble_name,
                        dest_sq.sq_name,
                        this_port_number)
                    if arc_destination not in this_sq.output_arc.dest_mapping:
                        logger.debug(
                            'Adding arc_destination %s to intermediate-arc symbol %s'
                            % (arc_destination, this_sq.output_arc.symbol))
                        this_sq.output_arc.dest_mapping.append(arc_destination)

            mapped_graph[this_sq.sq_name] =  ensemble_name

        return mapped_graph

    @staticmethod
    def random_mapping(graph: Graph.TTGraph, ensembles: Union[dict, list]):
        '''
        Produce a random mapping of the graph onto the ensembles

        :param graph: The graph to map
        :type graph: TTGraph

        :param ensembles: The set of ensembles to map the graph onto; this is
            the system description
        :type ensembles: list(TTEnsemble) | dict
        '''
        graph_levels = {0:[]}
        graph_node_to_levels = {}
        up_to_down = {}
        down_to_up = {}
        mapping = {}

        for arc in graph.symbol_table.values():
            source_sq = arc.source_sq
            if not source_sq and len(arc.dest_sq_list) > 0:
                graph_levels[0].append(arc.symbol)
                graph_node_to_levels[arc.symbol] = 0
                up_to_down[arc.symbol] = [sq.sq_name for sq in arc.dest_sq_list]
                for this_sq in arc.dest_sq_list:
                    if this_sq.sq_name not in down_to_up:
                        down_to_up[this_sq.sq_name] = []
                    down_to_up[this_sq.sq_name].append(arc.symbol)
            elif len(arc.dest_sq_list) > 0:
                up_to_down[source_sq.sq_name] = [sq.sq_name for sq in arc.dest_sq_list]
                for this_sq in arc.dest_sq_list:
                    if this_sq.sq_name not in down_to_up:
                        down_to_up[this_sq.sq_name] = []
                    down_to_up[this_sq.sq_name].append(source_sq.sq_name)
            elif not source_sq and len(arc.dest_sq_list) == 0:
                raise Exception("TopologicalError")

        def helper(node):
            if node in graph_node_to_levels:
                return graph_node_to_levels[node]

            level = max([helper(n) for n in down_to_up[node]]) + 1
            graph_node_to_levels[node] = level
            if level not in graph_levels:
                graph_levels[level] = []
            graph_levels[level].append(node)
            return level

        for this_sq in down_to_up:
            if this_sq not in graph_node_to_levels:
                helper(this_sq)

        def random_ensemble_selector(ensembles):
            index = random.randrange(0, len(ensembles))
            if isinstance(ensembles, dict):
                ens = ensembles[list(ensembles.keys())[index]]
            else:
                ens = ensembles[index]

            ensemble_name = ens.name

            return ensemble_name

        sq_names = [sq.sq_name for sq in graph.sq_list]
        for level in sorted(graph_levels.keys()):
            if level > 0:
                for this_sq in graph_levels[level]:
                    assert this_sq in sq_names
                    if level == 1:
                        mapping[this_sq] = random_ensemble_selector(ensembles)
                    else:
                        upstreams = set(down_to_up[this_sq])
                        upstream_ensembles = set(
                            [mapping[upstream] for upstream in upstreams])
                        if len(upstream_ensembles) == 1:
                            mapping[this_sq] = list(upstream_ensembles)[0]
                        else:
                            mapping[this_sq] = random_ensemble_selector(
                                ensembles)

        return mapping
