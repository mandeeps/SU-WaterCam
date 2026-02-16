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

import ast

from inspect import getmembers, isfunction
from os.path import basename

from . import DebugLogger
from .SQ import TTSQDecorator, TTSQArgument, identify_decorator, \
    identify_parameter, SQDecorator_LOOKUP, SQParameter_LOOKUP
from .Error import TTSyntaxError
from .CompilerUtils import SymbolRecord, Context, SymbolType

logger = DebugLogger.get_logger(basename(__file__))


class TTTypechecker(ast.NodeVisitor):
    '''
    '''

    def __init__(self, source, library_path, context: Context, pathname=None):
        self.source = source
        self.library_path = library_path
        self.context = context
        self.pathname = pathname

    def typecheck(self, module):
        return self.visit(module)

    def source_line(self, lineno):
        return self.source[lineno - 1]

    # don't visit any user defined functions yet
    def visit_FunctionDef(self, node):
        has_tt_decorator = False
        uniq_tt_decorator = None
        for decorator in node.decorator_list:
            identified_decorator = identify_decorator(decorator.id)

            # first decorator must be a TTSQDecorator
            if (identified_decorator is TTSQDecorator.Unknown
                    and not has_tt_decorator):
                raise TTSyntaxError(
                    'Outermost function decorator must be a valid TT Function '
                    f'decorator {list(SQDecorator_LOOKUP.keys())}',
                    node.lineno, self.source_line(node.lineno), self.pathname)

            # do not allow multiple TTSQDecorators
            if (identified_decorator is not TTSQDecorator.Unknown
                    and has_tt_decorator):
                raise TTSyntaxError(
                    f'Function cannot have both {uniq_tt_decorator} and '
                    f'{decorator} decorators', node.lineno,
                    self.source_line(node.lineno), self.pathname)

            if identified_decorator is not TTSQDecorator.Unknown:
                has_tt_decorator = True

            # check the body if is the main GRAPHified function
            if identified_decorator is TTSQDecorator.GRAPHify:
                for child in node.body:
                    self.visit(child)

    def visit_With(self, node):
        # ignoring calls in context manager for now
        for child in node.body:
            self.visit(child)

    def visit_Call(self, node: ast.Call):
        # TODO: node.func may either be Attribute or Name
        # following code may fail
        func_name = node.func.id

        # check if the function has been SQified or STREAMified
        if func_name in self.context:
            func_record: SymbolRecord = self.context[func_name]

            if func_record.type is not SymbolType.SQ:
                raise TTSyntaxError(f"cannot use {func_record.type.name} "
                                    f"'{func_name}' as an SQ")

            func_def = func_record.val

            # must have a top_level decorator or not allowed
            top_decorator = identify_decorator(func_def.decorator_list[0].id)
            logger.debug(
                f"func '{func_name}' has decorator {top_decorator.name}")

            # NOTE: could change this to be a set of args, only call for
            # `get_close_matches` until later.
            func_kwargs = [
                identify_parameter(kwarg.arg) for kwarg in node.keywords
            ]

            # a STREAMify requires the following kwargs to function correctly
            if top_decorator is TTSQDecorator.STREAMify:
                required_kwargs = [
                    TTSQArgument.TTClock, TTSQArgument.TTPeriod,
                    TTSQArgument.TTPhase, TTSQArgument.TTDataIntervalWidth
                ]

                for required_kwarg in required_kwargs:
                    if required_kwarg not in func_kwargs:
                        raise TTSyntaxError(
                            f"STREAMified function '{func_name}' requires "
                            f"the '{required_kwarg.name}' keyword argument.",
                            node.lineno, self.source_line(node.lineno),
                            self.pathname)

            # TTClock used in some SQs.
            # SQify does not need the following specific parameters uesd in
            # STREAMify
            if top_decorator is TTSQDecorator.SQify:
                disallowed_kwargs = [
                    TTSQArgument.TTPeriod, TTSQArgument.TTPhase,
                    TTSQArgument.TTDataIntervalWidth
                ]

                for kwarg in disallowed_kwargs:
                    if kwarg in func_kwargs:
                        raise TTSyntaxError(
                            f"SQified functions ('{func_name}') should not use "
                            f"the '{kwarg.name}' keyword argument.",
                            node.lineno, self.source_line(node.lineno),
                            self.pathname)

        else:
            # function call lookup failed, but it might be in
            # CompoundInstructions. ignore any function made in
            # CompoundInstructions.
            import ticktalkpython.CompoundInstructions as CompoundInstructions
            funcs = getmembers(CompoundInstructions, isfunction)
            if not any([func_tuple[0] == func_name for func_tuple in funcs]):
                raise TTSyntaxError(
                    f"'{func_name}' was not defined, SQified, or STREAMified.",
                    node.lineno, self.source_line(node.lineno), self.pathname)
