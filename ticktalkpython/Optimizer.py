from abc import ABC, abstractmethod
from typing import List

from ticktalkpython.Graph import TTGraph


class Operations(ABC):

    def __init__(self):
        pass

    @abstractmethod
    def optimize(graph: TTGraph):
        pass

class SingleOutputMerge(Operations):

    def __init__(self):
        super().__init__()

    def optimize(graph: TTGraph):
        for sq in graph.sqs:
            # merge any single input/output sqs with sqs below
            if len(sq.ipps) < 2 and len(sq.get_opp_names()) < 2:

                pass


class Optimizer:

    def __init__(self, options: List[Operations]):
        # TODO: Decide what options to give to the optimizer
        self.options = options

    def optimize(self, graph):
        for option in self.options:
            option.optimize(graph)

