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
These rules are automatically called as the ast (abstract syntax tree) library
'visits' syntactic structures within the TTPython file to compile.

The ``TTSQ`` constructor is also an especially essential component of the
compilation process!
'''

import ast
import itertools
import astor  # be sure to pip install this...
import astunparse

from typing import Any, List

from .SQ import TTSQ
from .Stack import TTStack
from .Clock import TTClock
from .Error import TTSyntaxError
from .Error import TTCompilerError
from . import Query
from . import DebugLogger
from .Graph import TTGraph
from .FiringRule import TTFiringRuleType
from .CompilerUtils import Context, SymbolType, SymbolRecord, SymbolRecordPort, ShadowEnv, TTBlockInfo
from .CompilerAssistVisitors import TTEnvVisitor

logger = DebugLogger.get_logger('CompilerRules')


class CompilationArtifact:
    def __init__(self, sqs, context, output_names):
        self.sqs: List[TTSQ] = sqs
        self.context: Context = context
        self.opp_names: List[str] = output_names

    def __repr__(self):
        return (
            f'CA(sqs: {self.sqs}, opp_names: {self.opp_names}, context: {self.context}'
        )


class TTGraphCompilationVisitor(ast.NodeVisitor):
    '''
    A child class of ast.NodeVisitor, which walks through the ast and calls
    specific handlers for each syntactic construct

    :param graph: An instantiated but empty graph, ready to be filled in as the
        AST is walked
    :type graph: TTGraph
    '''
    func_iter = itertools.count()
    sq_iter = itertools.count()

    def __init__(self, context: Context, source=None, pathname=None):
        self.context = context
        # TODO: block_info and (type_)context seem to do similar things
        # should we consolidate them?
        self.block_info_stack = TTStack()
        self.source = [] if source is None else source
        self.pathname = pathname
        self.flattened_constraints = []

    def compile_graph(self, module):
        artifact: CompilationArtifact = self.visit(module)

        # may return None if no GRAPHified function
        if self.graph is None:
            raise TTSyntaxError(
                'Compiled module does not have a function '
                'annotated with @GRAPHify.', module.lineno,
                self.source_line(module.lineno), self.pathname)

        self.clean_unused_control_ports(self.graph)
        return self.graph

    def clean_unused_control_ports(self, graph: TTGraph):
        ipp_to_sq = graph.get_ipp_to_sq_dict()
        for sq in graph.sqs:
            # clean all STREAMified nodes in case they don't use the control port
            if sq.is_periodic():
                tag_name = sq.get_tag_opp_name()
                logger.debug(f'{sq} is periodic, has tag var name {tag_name}')
                if tag_name not in ipp_to_sq:
                    logger.debug(f'Removing unused port {tag_name} from SQ {sq}')
                    sq.remove_unused_tag_var()

    def get_uniq_sq_name(self, sq_name):
        return sq_name + "-" + str(next(TTGraphCompilationVisitor.sq_iter))

    def get_uniq_func_name(self, func_name):
        return func_name + "_" + str(next(TTGraphCompilationVisitor.func_iter))

    def source_line(self, lineno):
        return self.source[lineno - 1]

    def n_compare_generator(self, context, ipp_records: List[SymbolRecordPort],
                            bool_type) -> TTSQ:
        if type(bool_type) is ast.And:
            func_name = 'ANDN'
            join_op = ' and '
        elif type(bool_type) is ast.Or:
            func_name = 'ORN'
            join_op = ' or '
        else:
            raise TTCompilerError(f"Unfamiliar boolean n-type {bool_type}")

        # params are [b0, b1, ..., bn]
        bool_param_list = ["b" + str(i) for i in range(0, len(ipp_records))]
        # create an meta boolean node, inputs are defined by input_arcs
        bool_py_func_str = "@SQify\ndef " + func_name + "(" + \
            ', '.join(bool_param_list) + "):\n\t return " + join_op.join(bool_param_list)

        bool_call_node = ast.Call(ast.Name(id=func_name), ipp_records, [])
        # returns a module with a single function def. We only want the func def
        bool_py_func = ast.parse(bool_py_func_str).body[0]

        sq_name = self.get_uniq_sq_name(func_name)

        sq = self.generate_sq(bool_call_node, bool_py_func, sq_name,
                              ipp_records, context)

        return sq

    def generate_sq(self,
                    node: ast.Call,
                    func: ast.FunctionDef,
                    name: str,
                    ipp_records: List[SymbolRecordPort],
                    context_env,
                    is_singleton=False,
                    input_ctrl_port=None,
                    firing_rule: TTFiringRuleType = None,
                    num_opps=1) -> TTSQ:
        '''
        Preps the input arcs, creates SQ, and adds it to the sq_list
        '''
        firing_rule_type = firing_rule
        if firing_rule_type is None:
            firing_rule_type = self.determine_firing_rule(ipp_records, func)

        block_info = self.block_info_stack.tos()
        sq = TTSQ(node,
                  func,
                  block_info,
                  name,
                  firing_rule_type,
                  context_env,
                  ipp_records,
                  num_opps,
                  input_control_port=input_ctrl_port,
                  is_singleton=is_singleton,
                  clock_dict=self.context.clock_dictionary)

        return sq

    def generate_merge_sq(self, ipp_records: List[SymbolRecordPort],
                          context_env: Context):
        merge_func_name = "MERGE"
        args = [
            ast.Name(id=ipp_record.val.data_name) for ipp_record in ipp_records
        ]
        merge_call_node = ast.Call(ast.Name(id=merge_func_name), args, [])
        merge_py_func = self.context[merge_func_name].val
        merge_sq_name = self.get_uniq_sq_name(merge_func_name)

        merge_sq = self.generate_sq(merge_call_node,
                                    merge_py_func,
                                    merge_sq_name,
                                    ipp_records,
                                    context_env,
                                    firing_rule=TTFiringRuleType.Immediate)

        return merge_sq

    def determine_firing_rule(self, ipp_records: List[SymbolRecordPort],
                              func: ast.FunctionDef) -> TTFiringRuleType:
        function_source = astunparse.unparse(func)

        # the default firing rule
        firing_rule_type = TTFiringRuleType.Timed

        # streaming designation should propagate; check by tracing to the
        # input arc, assuming the graph is constructed s.t. every new SQ
        # receives input from SQs that have already been created (and their
        # arcs defined)
        is_streaming = any([ipp.is_streaming for ipp in ipp_records])

        # if this is decorated with 'STREAMify', then it should be expected
        # to retrigger itself periodically such that it generates a stream
        # of data. The parameters (e.g., period, phase) will be extracted
        # while analyzing keyword arguemnts
        if any(
            [decorator.id == 'STREAMify'
             for decorator in func.decorator_list]):
            firing_rule_type = TTFiringRuleType.TimedRetrigger

        # If a node has global sq_state and is not streamify, it must have the
        # SequentialRetrigger rule
        elif "global sq_state" in function_source and is_streaming:
            firing_rule_type = TTFiringRuleType.SequentialRetrigger

        return firing_rule_type

    def scrape_kwarg(self, s, node: ast.Call):
        '''
        Searches for the value of key s in kwargs.
        Note that the node needs keywords
        '''
        name = node.func.id
        check = [
            kwarg.value
            for kwarg in [kw for kw in node.keywords if kw.arg == s]
        ]

        if len(check) != 1:
            raise TTSyntaxError(f"{name} requires the {s} keyword",
                                node.lineno, self.source_line(node.lineno),
                                self.pathname)

        return check[0]

    def compile_with_shadow_table(
            self, node, names: List[str], renamed_names: List[str], ipps,
            context_to_hide: Context) -> CompilationArtifact:
        '''
        Compiles node with a shadow table. The table is created by the given
        syms and output_arcs.
        '''
        assert len(names) == len(renamed_names)
        assert len(names) == len(ipps)

        # deepcopy context and rename OPP names
        # trigger arcs included
        shadow_context = Context.from_context(context_to_hide)
        for i in range(len(ipps)):
            ipp = ipps[i]
            renamed_name = renamed_names[i]
            orig_name = names[i]
            ipp.data_name = renamed_name
            shadow_context.update_port(orig_name, renamed_name)

        compile_visitor = TTGraphCompilationVisitor(shadow_context,
                                                    source=self.source,
                                                    pathname=self.pathname)
        compile_visitor.block_info_stack = self.block_info_stack
        artifact: CompilationArtifact = compile_visitor.visit(node)

        # need to put back newly generated opps from the shadowed environment
        self.context.merge_shadow_sym_table(shadow_context.sym_table)

        # rewrite the shadowed names by returning the unmodified context
        artifact.context = self.context

        return artifact

    ###################### Visitor functions #####################
    # wrapper functions as cannot modify ast.visitor signatures
    def visit_with_context(self, node, context) -> CompilationArtifact:
        self.context = context
        return (self.visit(node), context)

    def visit_with_context_pop(self, node, context) -> CompilationArtifact:
        orig_context = self.context
        visited_output = self.visit_with_context(node, context)
        self.context = orig_context
        return (visited_output, orig_context)

    #####

    def visit_ImportFrom(self, node):
        return CompilationArtifact([], self.context, [])

    def visit_Import(self, node):
        return CompilationArtifact([], self.context, [])

    def visit_FunctionDef(self, node) -> CompilationArtifact:
        logger.debug(f"*** function: {node.name}")
        for decorator in node.decorator_list:
            logger.debug(f"*** decorator: {decorator.id}")
            if decorator.id == 'GRAPHify':
                self.graph_name = node.name
                # Create distinguished ports in the symbol table for the graph's inputs
                logger.debug(astor.dump_tree(node))

                # Recursively descend into the body
                self.block_info_stack.push(TTBlockInfo("root"))
                sqs = []
                for child in node.body:
                    artifact = self.visit(child)
                    self.context = artifact.context
                    sqs = sqs + artifact.sqs

                self.graph = TTGraph(node.name, self.context.trigger_var,
                                     [arg.arg for arg in node.args.args], sqs,
                                     self.context.clock_dictionary,
                                     self.flattened_constraints)
                self.block_info_stack.pop()
                return CompilationArtifact(sqs, self.context, [])

    def visit_With(self, node) -> CompilationArtifact:
        # Handle the ast within a 'with' specifier, which are used in TTPython
        # to specify things like clocks or mapping constraints Clone the
        # current context as a starting point
        new_context = TTBlockInfo(base_context=self.block_info_stack.tos())
        # Iterate over the context modifications
        for item in node.items:
            # **TEMPORARY**  -- instead, do a pass on the AST first to
            # build the clock tree.  Then insert the TTClock object here
            # instead of the name.
            if not isinstance(item.context_expr, type(ast.Call())):
                err = TTSyntaxError('Illegal context specifier', item.lineno)
                err.source = self.source_line(item.lineno)
                err.pathname = self.pathname
                raise err
            # withitem(context_expr=Call(func=Attribute(value=Name(id='TTClock'),
            #                                           attr='root'),
            #                            args=[],
            #                            keywords=[]),
            #          optional_vars=Name(id='CLOCK'))
            # Could be TTClock(...)
            #   function_id = item.context_expr.func.id
            # or it could be TTClock.root()
            #   function_id = item.context_expr.func.value.id

            root_clock = False
            # Hackish way to do this:
            try:
                function_id = item.context_expr.func.id
            except:
                function_id = item.context_expr.func.value.id
                root_clock = True

            new_context.name = function_id
            if function_id == "TTClock":
                clock_var_name = item.optional_vars.id  # e.g., 'CLOCK'
                if root_clock:
                    # assume the print name as 'ROOT' so it doesn't have to be specified
                    clock_print_name = 'ROOT'
                else:
                    # e.g., 'local_root'
                    clock_print_name = item.context_expr.args[0].s
                new_context.name = f'with TTClock({clock_print_name})'

                if clock_var_name in self.context.clock_dictionary.keys():
                    err = TTSyntaxError(
                        f"Clock name {clock_var_name} is being re-defined here",
                        item.lineno)
                    err.source = self.source_line(item.lineno)
                    err.pathname = self.pathname
                    raise err

                n_args = 1 if root_clock else len(item.context_expr.args)
                if n_args == 1:
                    # if there are no other arugments, this must be the root cock
                    new_clock = TTClock.root()
                    new_clock.name = clock_print_name
                elif n_args == 4:
                    # there should be 4 args for non-root clocks: name,
                    # parent, period, epoch
                    parent_clock = item.context_expr.args[1].id
                    try:
                        self.context.clock_dictionary[parent_clock]
                    except KeyError:
                        raise TTSyntaxError(
                            f"Attemped to create a clock {clock_print_name} "
                            f"for a parent '{parent_clock}' that has not been specified",
                            node.lineno)
                    # **Check** should only be a Num node
                    period = item.context_expr.args[2].n
                    # **Check** should only be a Num node
                    epoch = item.context_expr.args[3].n

                    # search for the parent clock based on variable name
                    parent_clock = self.context.clock_dictionary[
                        item.context_expr.args[1].id]
                    new_clock = TTClock(clock_print_name, parent_clock, period,
                                        epoch)
                else:
                    err = TTSyntaxError("Incorrect arglist for clock spec",
                                        item.lineno)
                    err.source = self.source_line(item.lineno)
                    err.pathname = self.pathname
                    raise err

                self.context.clock_dictionary[clock_var_name] = new_clock
                new_context.clock = new_clock

            elif function_id == "TTConstraint":
                # TTQuery to apply mapping constraints to any SQs that appear
                # within this block
                new_context.name = 'with TTConstraint'
                kwargs = item.context_expr.keywords
                # we assume the 'components' kwarg will point to a list
                component_list_search = [
                    k.value.elts for k in kwargs if k.arg == 'components'
                ]
                component_list = component_list_search[0] if 0 < len(
                    component_list_search) else []

                # if expr required for 3.7 ast parsing
                ens_name_list = (lambda l: l if len(l) == 1 else [])([
                    Query.TTQCEnsembleName(k.value.s if isinstance(
                        k.value, ast.Str) else k.value.value) for k in kwargs
                    if k.arg == 'name'
                ])

                # assumes args are string constants
                # if expr required for 3.7 ast parsing
                name_query_list = [
                    Query.TTQCComponentName(
                        c.s if isinstance(c, ast.Str) else c.value)
                    for c in component_list
                ]
                constraints = name_query_list + ens_name_list

                new_context.constraints = constraints
                self.flattened_constraints.append(constraints)
            else:
                if function_id[0:2] == 'TT':
                    raise TTSyntaxError(
                        f"Unknown TT construct '{function_id} "
                        "used in with statement", node.lineno,
                        self.source_line(node.lineno), self.pathname)
                raise TTSyntaxError(
                    f"Illegal context specifier '{function_id}' "
                    "in with statement", node.lineno,
                    self.source_line(node.lineno), self.pathname)
        self.block_info_stack.push(new_context)

        sqs = []

        # Recursively descend into the body
        for child in node.body:  # ast.iter_child_nodes(node.body):
            logger.debug('Child of functionDef visit: %s' % child)
            artifact = self.visit(child)
            self.context = artifact.context
            sqs = sqs + artifact.sqs

        self.block_info_stack.pop()
        return CompilationArtifact(sqs, self.context, [])

    def visit_Assign(self, node) -> CompilationArtifact:
        assignment_list = node.targets
        # greater than 1 means multiple names being assigned (a = b = 1)
        if 1 < len(assignment_list):
            err = TTSyntaxError(
                'Multiple variable assignment is not allowed.',
                node.lineno)
            err.source = self.source_line(node.lineno)
            err.pathname = self.pathname
            raise err

        # lhs is singleton list
        if isinstance(assignment_list[0], ast.Tuple):
            new_vars = [ast_name.id for ast_name in assignment_list[0].elts]
        else:  # Single element with a name
            new_vars = [assignment_list[0].id]

        # Symbols may be re-used, and the usage in a scope may shadow a
        # different usage in an outer scope. Disallow re-definititions within
        # a scope, and re-name shadowing symbols to dis-ambiguate them from
        # shadowed definitions in outer scopes

        # TODO: allow variable shadowing
        for unique_symbol in new_vars:
            if unique_symbol in self.context:
                err = TTSyntaxError(
                    f"'{unique_symbol}' Variable shadowing "
                    "not implemented yet.", node.lineno)
                err.source = self.source_line(node.lineno)
                err.pathname = self.pathname
                raise err

        # Expecting an Expr
        # TODO: this will return 2 opp_names for STREAMified nodes. Either
        # force ignore and fix the SQ later or fix it here.
        # this is a bit tricky, we want the form (tag, data_port) or data_port
        # eitherway, the port semantics are [data_port, control_port]
        # so, we purposely reorder the new_vars
        compile_output: CompilationArtifact = self.visit(node.value)
        if len(compile_output.opp_names) < len(new_vars):
            raise TTSyntaxError('unpacking from a tuple is not supported',
                                node.lineno, self.source_line(node.lineno),
                                self.pathname)
        for new_var, opp_name in zip(new_vars[1:] + new_vars[:1],
                                     compile_output.opp_names):
            logger.debug(f'{new_var}, {opp_name}')

            # has this been already used? if so, we can just rename it
            logger.debug(f'renaming {opp_name} with {new_var}')
            compile_output.context.alpha_rename(opp_name, new_var)

        # NOTE: use the last port visited as the representative port
        data_port_name = new_vars[-1]
        return CompilationArtifact(
            compile_output.sqs, compile_output.context,
            [compile_output.context[data_port_name].val])

    def visit_AnnAssign(self, node):
        # override the type with a variable that listens to the control token
        lhs = node.target
        if not isinstance(lhs, ast.Name):
            raise TTSyntaxError('lhs var expr should only be a Var')
        data_var_name = lhs.id

        # Symbols may be re-used, and the usage in a scope may shadow a
        # different usage in an outer scope. Disallow re-definititions within
        # a scope, and re-name shadowing symbols to dis-ambiguate them from
        # shadowed definitions in outer scopes
        timestamp_var_name = node.annotation.id

        # **Temporary**
        if data_var_name in self.context:
            err = TTSyntaxError('Multiple assignments to the same symbol',
                                node.lineno)
            err.source = self.source_line(node.lineno)
            err.pathname = self.pathname
            raise err
        # Expecting an Expr
        compile_output: CompilationArtifact = self.visit(node.value)
        if 2 != len(compile_output.opp_names):
            raise TTSyntaxError(
                'rhs expr does not have enough ports to support timestamp notation',
                node.lineno, self.source_line(node.lineno), self.pathname)

        data_opp_name = compile_output.opp_names[0]
        timestamp_opp_name = compile_output.opp_names[1]

        # rename the data and timestamp opps
        logger.debug(f'renaming {data_opp_name} with {data_var_name}')
        compile_output.context.alpha_rename(data_opp_name, data_var_name)

        logger.debug(f'renaming {timestamp_opp_name} with {timestamp_var_name}')
        compile_output.context.alpha_rename(timestamp_opp_name, timestamp_var_name)

        return CompilationArtifact(compile_output.sqs, compile_output.context,
                                   [compile_output.context[data_var_name].val])

    def visit_Return(self, node) -> CompilationArtifact:
        return self.visit(node.value)  # Expecting an Expr

    def visit_UnaryOp(self, node) -> CompilationArtifact:
        if isinstance(node.op, ast.USub):
            func_string = 'NEG'
        elif isinstance(node.op, ast.Not):
            func_string = 'NOT'
            pass
        else:
            raise TTSyntaxError('Unrecognized unary operation', node.lineno,
                                self.source_line(node.lineno), self.pathname)
        func = ast.Name(id=func_string)
        expr_output: CompilationArtifact = self.visit(node.operand)
        if len(expr_output.opp_names) != 1:
            raise TTSyntaxError('Unary operation received more than 1 expr',
                                node.lineno)

        opp_name = expr_output.opp_names[0]
        args = [ast.Name(id=opp_name)]
        call_node = ast.Call(func, args, [])
        not_output: CompilationArtifact = self.visit_Call(call_node)
        # Process the new Call node -- it should return
        return CompilationArtifact(expr_output.sqs + not_output.sqs,
                                   self.context, not_output.opp_names)

    def visit_BinOp(self, node) -> CompilationArtifact:
        # In:  BinOp(left=BinOp(left=Name(id='a'), op=Add, right=Name(id='b')),
        #           op=Mult,
        #           right=BinOp(left=Name(id='a'), op=Sub, right=Name(id='b'))))
        if isinstance(node.op, type(ast.Add())):
            func_string = 'ADD'
        elif isinstance(node.op, type(ast.Sub())):
            func_string = 'SUB'
        elif isinstance(node.op, type(ast.Mult())):
            func_string = 'MULT'
        elif isinstance(node.op, type(ast.Div())):
            func_string = 'DIV'
        else:
            raise TTSyntaxError('Unrecognized binary operation', node.lineno,
                                self.source_line(node.lineno), self.pathname)
        func = ast.Name(id=func_string)

        lhs_compile_output: CompilationArtifact = self.visit(node.left)
        rhs_compile_output: CompilationArtifact = self.visit(node.right)

        if len(lhs_compile_output.opp_names) != 1:
            raise TTSyntaxError(
                'Binary operation received more than 1 expr on lhs',
                node.lineno)
        if len(rhs_compile_output.opp_names) != 1:
            raise TTSyntaxError(
                'Binary operation received more than 1 expr on rhs',
                node.lineno)

        lhs_opp_name = lhs_compile_output.opp_names[0]
        rhs_opp_name = rhs_compile_output.opp_names[0]
        args = [ast.Name(id=lhs_opp_name), ast.Name(id=rhs_opp_name)]
        call_node = ast.Call(func, args, [])
        sq_output = self.visit_Call(call_node)
        return CompilationArtifact(
            lhs_compile_output.sqs + rhs_compile_output.sqs + sq_output.sqs,
            sq_output.context, sq_output.opp_names)

    def visit_BoolOp(self, node: ast.BoolOp) -> CompilationArtifact:
        compile_artifacts = [self.visit(clause) for clause in node.values]
        n_compare_sq = self.n_compare_generator(self.context, [
            self.context[opp_name] for artifact in compile_artifacts
            for opp_name in artifact.opp_names
        ], node.op)

        return CompilationArtifact(
            [sq for artifact in compile_artifacts
             for sq in artifact.sqs] + [n_compare_sq], self.context,
            n_compare_sq.get_opp_names())

    def visit_Compare(self, node: ast.Compare) -> CompilationArtifact:
        # assumes the context is being updated between visits
        # true because self.context is global for these visit
        cmp_artifacts: List[CompilationArtifact] = [self.visit(
            node.left)] + [self.visit(comp) for comp in node.comparators]

        cmp_operator_artifacts = []
        for i in range(0, len(cmp_artifacts) - 1):
            left_opp_name = cmp_artifacts[i].opp_names[0]
            right_opp_name = cmp_artifacts[i + 1].opp_names[0]

            if isinstance(node.ops[i], type(ast.Eq())):
                func_string = 'EQ'
            elif isinstance(node.ops[i], type(ast.NotEq())):
                func_string = 'NEQ'
            elif isinstance(node.ops[i], type(ast.Lt())):
                func_string = 'LT'
            elif isinstance(node.ops[i], type(ast.LtE())):
                func_string = 'LTE'
            elif isinstance(node.ops[i], type(ast.Gt())):
                func_string = 'GT'
            elif isinstance(node.ops[i], type(ast.GtE())):
                func_string = 'GTE'
            else:
                raise TTSyntaxError(
                    f'Compare operator {node.ops[i]} not implemented',
                    node.lineno)

            func = ast.Name(id=func_string)
            args = [ast.Name(id=left_opp_name), ast.Name(id=right_opp_name)]
            call_node = ast.Call(func, args, [])

            cmp_operator_artifacts.append(self.visit_Call(call_node))

        child_cmp_sq = self.n_compare_generator(self.context, [
            self.context[opp_name] for artifact in cmp_operator_artifacts
            for opp_name in artifact.opp_names
        ], ast.And())

        # return all sqs from data and operators sqs
        sqs = [sq for artifact in cmp_artifacts for sq in artifact.sqs] + [
            sq for artifact in cmp_operator_artifacts for sq in artifact.sqs
        ] + [child_cmp_sq]

        return CompilationArtifact(sqs, self.context,
                                   child_cmp_sq.get_opp_names())

    # Create an SQ for this call and a list of Port.
    def visit_Call(self, node):
        # Ignore top-level calls in the source file (e.g. print(main(...)))
        # "top-level" is recognizable because the context has not yet been defined.
        if self.block_info_stack.tos() is None:
            return

        # Create the SQ for this Call
        func_name = node.func.id

        logger.debug(astor.dump_tree(node))

        # special named functions create a subgraph of execution
        if func_name == 'TTSingleRunTimeout':
            timeout_node = self.scrape_kwarg("TTTimeout", node)
            timeout = timeout_node.n if isinstance(
                timeout_node, ast.Num) else timeout_node.value
            if not type(timeout) is int:
                raise TTSyntaxError(f"Timeout must be an integer.",
                                    node.lineno, self.source_line(node.lineno),
                                    self.pathname)

            srun_sq_name = self.get_uniq_sq_name("SINGLETON")
            srun_func_name = srun_sq_name.replace("-", "_")

            free_vars_visitor = TTEnvVisitor(node)

            srun_shadow_env = ShadowEnv(free_vars_visitor.get_env(),
                                        self.context.trigger_var,
                                        free_vars_visitor.has_constants,
                                        srun_func_name + "_")
            srun_env = srun_shadow_env.get_env_list()

            # * Step 2:
            # add outputs to triggers
            srun_py_func_hdr = ""
            if free_vars_visitor.has_constants:
                srun_py_func_hdr = ("\t" + srun_shadow_env.get_trigger_name() +
                                    " = 0xDEADBEEF\n")

            # TODO: Fails under nested conditions
            # (TTFinishByOtherwise -> TTPlanB=TTSingleRunTimeout) and has
            # a parameterless function
            # TODO: param_list is lacking the constant check.
            param_list = ['TTLock'] + list(free_vars_visitor.get_env())
            srun_py_func_str = ("@SQify\ndef " + srun_func_name + '(' +
                                ', '.join(param_list) + "):\n" +
                                srun_py_func_hdr + '\treturn ' +
                                ', '.join(param_list[1:]))
            srun_py_func = ast.parse(srun_py_func_str).body[0]

            # NOTE: get a separated trigger
            id_func = ast.Name(id='NOT')
            id_args = [ast.Name(id=self.context.trigger_var)]
            # make this node the keyword value of the id() call
            id_call_node = ast.Call(id_func, id_args, [])
            id_artifact: CompilationArtifact = self.visit_Call(id_call_node)

            # lookup the SQs for each free_var
            srun_circ_name = id_artifact.opp_names[0]
            srun_ipp_names = [srun_circ_name] + srun_env

            srun_call_node = ast.Call(ast.Name(id=srun_func_name),
                                      srun_ipp_names, [])

            srun_sq = self.generate_sq(
                srun_call_node,
                srun_py_func,
                srun_sq_name, [self.context[name] for name in srun_ipp_names],
                self.context,
                is_singleton=True,
                num_opps=len(srun_env))
            # srun_circ_name.source_sq = srun_sq

            # * Step 3: compile the associated single-run graph
            srun_opps = srun_sq.get_opps()

            for name, srun_output_arc in zip(srun_env, srun_opps):
                srun_output_arc.symbol = name

            # TODO: change hardcoded compile of first arg
            srun_exp_artifact = self.compile_with_shadow_table(
                node.args[0], srun_env, srun_shadow_env.get_renamed_env_list(),
                srun_opps, self.context)

            # * Step 4: feed back into the parent SQ
            if len(srun_exp_artifact.opp_names) != 1:
                raise TTSyntaxError(
                    f'The expression in TTSingleRunTimeout must '
                    'have a single output.', node.lineno,
                    self.source_line(node.lineno), self.pathname)

            delay_func = ast.Name(id='ADD_TIME_DELAY')
            delay_args = [
                ast.Name(id=name) for name in srun_exp_artifact.opp_names
            ]
            # make this node the keyword value of the TIME_DELAY() call
            delay_kwarg = ast.keyword(arg='delay',
                                      value=ast.Constant(value=timeout))
            delay_keywords = [delay_kwarg]
            delay_call_node = ast.Call(delay_func, delay_args, delay_keywords)
            delay_artifact = self.visit_Call(delay_call_node)

            timeout_func = ast.Name(id='SET_TIMEOUT')
            timeout_args = [
                ast.Name(id=name) for name in delay_artifact.opp_names
            ]

            # TODO: what if the root clock is not ID'd by `root_clock`?
            clock_kwarg = ast.keyword(arg='TTClock',
                                      value=ast.Name(id='root_clock'))
            timeout_keywords = [clock_kwarg]
            timeout_call_node = ast.Call(timeout_func, timeout_args,
                                         timeout_keywords)
            timeout_artifact = self.visit_Call(timeout_call_node)

            # need to update port to
            timeout_artifact.context.update_port(timeout_artifact.opp_names[0],
                                                 srun_circ_name)

            return CompilationArtifact(
                id_artifact.sqs + [srun_sq] + srun_exp_artifact.sqs +
                delay_artifact.sqs + timeout_artifact.sqs,
                timeout_artifact.context, srun_exp_artifact.opp_names)

        # special translation for TTFinishByOtherwise
        if func_name == 'TTFinishByOtherwise':
            if len(node.args) != 1:
                raise TTSyntaxError(
                    f"TTFinishByOtherwise {func_name} has too many parameters",
                    node.lineno, self.source_line(node.lineno), self.pathname)

            planB = self.scrape_kwarg("TTPlanB", node)
            will_ret = self.scrape_kwarg("TTWillContinue", node)
            time_deadline = self.scrape_kwarg("TTTimeDeadline", node)

            # need to relax ast.Constant for backwards compatibility of ast
            # generation
            if type(will_ret.value) is not bool:
                raise TTSyntaxError(
                    f"TTFinishByOtherwise: TTWillContinue requires a boolean value",
                    node.lineno, self.source_line(node.lineno), self.pathname)

            time_control_artifact: CompilationArtifact = self.visit(
                time_deadline)
            data_artifact: CompilationArtifact = self.visit(node.args[0])

            if len(time_control_artifact.opp_names) != 1:
                raise TTSyntaxError(
                    "TTFinishByOtherwise only can operate on one output",
                    node.lineno, self.source_line(node.lineno), self.pathname)

            deadline_base_func_name = 'DEADLINE'
            deadline_sq_name = self.get_uniq_sq_name(deadline_base_func_name)
            inner_func_name = deadline_sq_name.replace("-", "_")

            # Instructions.py should have been loaded by now
            deadline_func = self.context[deadline_base_func_name].val

            deadline_node = ast.Call(ast.Name(id=deadline_base_func_name),
                                     data_artifact.opp_names, [])

            deadline_sq: TTSQ = self.generate_sq(
                deadline_node,
                deadline_func,
                deadline_sq_name,
                [self.context[opp] for opp in data_artifact.opp_names],
                self.context,
                input_ctrl_port=self.context[
                    time_control_artifact.opp_names[0]],
                firing_rule=TTFiringRuleType.Deadline,
                num_opps=2)

            planB_visitor = TTEnvVisitor(planB)
            planB_shadow_env = ShadowEnv(planB_visitor.get_env(),
                                         self.context.trigger_var,
                                         planB_visitor.has_constants,
                                         inner_func_name + '_')

            planB_env = planB_shadow_env.get_env_list()
            free_var_len = len(planB_env) - (1 if planB_visitor.has_constants
                                             else 0)

            # may catch some bugs if we visit first
            data_opp_name, deadline_opp_name = deadline_sq.get_opp_names()
            _, deadline_opp = deadline_sq.get_opps()

            planB_artifact: CompilationArtifact = self.compile_with_shadow_table(
                planB, [self.context.trigger_var],
                planB_shadow_env.get_renamed_env_list(), [deadline_opp],
                self.context)

            # TODO: allow relaxation of this later
            # May want to do analysis of local scoping of Plan B?
            # we can guarantee that data generation does come from local SQs
            # only
            # if 0 < free_var_len:
            #     raise TTCompilerError(
            #         'cannot compile Plan B that is not guaranteed '
            #         'to be called at the specified deadline')

            created_sqs = data_artifact.sqs + time_control_artifact.sqs + [
                deadline_sq
            ] + planB_artifact.sqs
            if will_ret.value:  # create a merge SQ like an if/else branch
                if len(planB_artifact.opp_names) != 1:
                    raise TTSyntaxError(
                        f"Plan B for {func_name} does not "
                        "have a single return value", node.lineno,
                        self.source_line(node.lineno), self.pathname)

                merge_ipp_records = [
                    self.context[name]
                    for name in [data_opp_name] + planB_artifact.opp_names
                ]
                merge_sq = self.generate_merge_sq(merge_ipp_records,
                                                  planB_artifact.context)
                output_names = merge_sq.get_opp_names()
                created_sqs.append(merge_sq)
            else:
                output_names = [data_opp_name]

            return CompilationArtifact(created_sqs, planB_artifact.context,
                                       output_names)

        # Regular function compilation
        compile_artifacts = [self.visit(arg) for arg in node.args]

        ipp_names = [artifact.opp_names[0] for artifact in compile_artifacts]
        if len(ipp_names) == 0:
            logger.debug(f'{func_name} has no arguments! '
                         'substituting with global trigger')
            ipp_names = [self.context.trigger_var]

        try:
            sq_func = self.context[func_name]
        except KeyError:
            raise TTSyntaxError(
                f'The function {func_name} cannot be found or is missing '
                'a TTPython function decorator.', node.lineno,
                self.source_line(node.lineno), self.pathname)

        # TODO: move this checking to CompilerTypecheck
        ipp_records = []
        for name in ipp_names:
            try:
                ipp_records.append(self.context[name])
            except KeyError:
                if name in self.context.clock_dictionary:
                    raise TTSyntaxError((
                        f"Defined TTClocks '{name}' can only be used with 'TTClock' keywords"
                    ), node.lineno, self.source_line(node.lineno),
                                        self.pathname)
                raise TTSyntaxError(
                    f"var '{name}' was used before definition!", node.lineno,
                    self.source_line(node.lineno), self.pathname)

        sq_name = self.get_uniq_sq_name(func_name)

        sq = self.generate_sq(node, sq_func.val, sq_name, ipp_records,
                              self.context)
        return_sqs = [
            sq for artifact in compile_artifacts for sq in artifact.sqs
        ] + [sq]
        logger.debug(f'returning {return_sqs}')
        return CompilationArtifact(return_sqs, self.context,
                                   sq.get_opp_names())

    def visit_IfExp(self, node) -> CompilationArtifact:
        # Expression(
        # body=IfExp(
        #     test=Name(id='b', ctx=Load()),
        #     body=Name(id='a', ctx=Load()),
        #     orelse=Name(id='c', ctx=Load())))
        # The if expr has 4 stages of compilation: test, if SQ, then and
        # else branch, and merge SQ
        # * Stage 1: test
        test_artifact: CompilationArtifact = self.visit(node.test)

        # * Stage 2: if SQ
        func_name = "IF"
        if_sq_name = self.get_uniq_sq_name(func_name)
        # vars are named are named uniquely by the sq_name, but hyphens aren't
        # supported in Python var names
        if_func_name = if_sq_name.replace("-", "_")

        # find all free vars in the then and else expressions
        then_visitor = TTEnvVisitor(node.body)
        else_visitor = TTEnvVisitor(node.orelse)

        then_shadow_env = ShadowEnv(then_visitor.get_env(),
                                    self.context.trigger_var,
                                    then_visitor.has_constants,
                                    if_func_name + "_then_")
        else_shadow_env = ShadowEnv(else_visitor.get_env(),
                                    self.context.trigger_var,
                                    else_visitor.has_constants,
                                    if_func_name + "_else_")
        then_env = then_shadow_env.get_env_list()
        else_env = else_shadow_env.get_env_list()

        free_vars = list(then_visitor.get_env() | else_visitor.get_env())

        param_list = ["_check"] + free_vars

        then_param_list = ', '.join(then_env) + ', TTEmpty()' * len(else_env)
        else_param_list = 'TTEmpty(), ' * len(then_env) + ', '.join(else_env)

        then_branch_text = ("\tif (_check):\n\t\treturn " + then_param_list +
                            "\n")
        else_branch_text = "\telse:\n\t\treturn " + else_param_list

        if_py_func_hdr = "\tfrom ticktalkpython.Empty import TTEmpty\n"
        # add outputs to triggers
        if then_visitor.has_constants:
            if_py_func_hdr += ("\t" + then_shadow_env.get_trigger_name() +
                               " = None\n")
        if else_visitor.has_constants:
            if_py_func_hdr += ("\t" + else_shadow_env.get_trigger_name() +
                               " = None\n")

        if_py_func_str = ("@SQify\ndef " + if_func_name + "(" +
                          ', '.join(param_list) + "):\n" + if_py_func_hdr +
                          then_branch_text + else_branch_text)
        if_py_func = ast.parse(if_py_func_str).body[0]

        # lookup the opp_name for each free_var
        if_input_vars = test_artifact.opp_names + free_vars

        if_call_node = ast.Call(ast.Name(id=if_func_name), if_input_vars, [])

        if_sq = self.generate_sq(if_call_node,
                                 if_py_func,
                                 if_sq_name,
                                 [self.context[var] for var in if_input_vars],
                                 self.context,
                                 num_opps=len(then_env) + len(else_env))

        # * Step 3: then and else branches
        if_opps = if_sq.get_opps()

        for name, if_opp in zip(then_env + else_env, if_opps):
            if_opp.symbol = name

        then_ipps = if_opps[:len(then_env)]
        else_ipps = if_opps[len(then_env):]

        then_artifact = self.compile_with_shadow_table(
            node.body, then_env, then_shadow_env.get_renamed_env_list(),
            then_ipps, self.context)
        else_artifact = self.compile_with_shadow_table(
            node.orelse, else_env, else_shadow_env.get_renamed_env_list(),
            else_ipps, self.context)

        # technically the merge SQ is part of the shadow compilation for the
        # if node. However, the compilation for the merge SQ does not
        # introduce new symbols and does not use the trigger, so we may
        # compile it independently
        # * Step 4: merge SQ
        # create the join node to merge the if and else branches
        merge_ipp_records = [
            self.context[opp]
            for opp in then_artifact.opp_names + else_artifact.opp_names
        ]
        merge_sq = self.generate_merge_sq(merge_ipp_records, self.context)

        all_sqs = test_artifact.sqs + [
            if_sq
        ] + then_artifact.sqs + else_artifact.sqs + [merge_sq]

        return CompilationArtifact(all_sqs, self.context,
                                   merge_sq.get_opp_names())

    def visit_Name(self, node) -> CompilationArtifact:
        try:
            name_record:SymbolRecord = self.context[node.id]
            if name_record.type is SymbolType.PORT:
                return CompilationArtifact([], self.context, [node.id])
            else:
                raise TTSyntaxError(
                    f'cannot use {name_record.type.name} '
                    f'{node.id} as a variable', node.lineno,
                    self.source_line(node.lineno), self.pathname)
        except KeyError:
            # I have purposefully put Clock variable lookup here because I
            # don't think Clocks are useful in any regard.
            if node.id in self.context.clock_dictionary:
                raise TTSyntaxError(
                    f"TTClocks ('{node.id}') can only be used in"
                    "TTClock kwarg assignment!", node.lineno,
                    self.source_line(node.lineno), self.pathname)
            raise TTSyntaxError(f"var '{node.id}' was used before definition!",
                                node.lineno, self.source_line(node.lineno),
                                self.pathname)

    def visit_Constant(self, node: ast.Constant) -> CompilationArtifact:
        # create a CONST call node with the value partially evaluated as a
        # kwarg
        func = ast.Name(id='CONST')
        args = [ast.Name(id=self.context.trigger_var)]
        kwargs = [ast.keyword(arg='const', value=node)]
        call_node = ast.Call(func, args, kwargs)
        return self.visit_Call(call_node)

    def visit_Module(self, node: ast.Module) -> Any:
        return super().generic_visit(node)

    # 3.7 compatibility
    def visit_Num(self, node) -> CompilationArtifact:
        new_node = ast.Constant(node.n)
        return self.visit_Constant(new_node)

    def visit_Str(self, node) -> CompilationArtifact:
        new_node = ast.Constant(node.s)
        return self.visit_Constant(new_node)

    def visit_NameConstant(self,
                           node: ast.NameConstant) -> CompilationArtifact:
        return self.visit_Constant(node)

    def visit_Expr(self, node):
        raise TTSyntaxError(
            f'Top level expressions must be assigned to variable or returned.',
            node.lineno, self.source_line(node.lineno), self.pathname)

    def generic_visit(self, node) -> CompilationArtifact:
        raise TTSyntaxError(
            f'This structure ({type(node)}) is not currently supported in TTPython',
            node.lineno, self.source_line(node.lineno), self.pathname)
