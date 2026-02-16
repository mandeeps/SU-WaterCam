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
import sys
import importlib
import os

from typing import Set
from functools import reduce

from .Error import TTSyntaxError
from . import DebugLogger
from . import Port
from .CompilerUtils import SymbolType
from .CompilerUtils import Context

logger = DebugLogger.get_logger('CompilerVisitor')

package_name = "ticktalkpython"


class TTImportVisitor(ast.NodeVisitor):
    ''''''
    def __init__(self, modules_imported, lib_path, context):
        self.modules_imported = modules_imported
        self.lib_path = lib_path
        self.context:Context = context

    def visit_ImportFrom(self, node):
        module = node.module
        if node.module is None:
            module = node.names[0].name

        import_sqified_functions_from_module(module, self.lib_path,
                                             self.modules_imported,
                                             self.context)

    def visit_FunctionDef(self, node):
        for decorator in node.decorator_list:
            if (isinstance(decorator, type(ast.Name()))
                    and (decorator.id == 'SQify' or decorator.id == 'STREAMify'
                         or decorator.id == "Resampler")):

                self.context.insert_symbol(node.name, SymbolType.SQ, node)


def get_deps(node):
    _, statements = next(ast.iter_fields(node))

    full_graph = {
        assign.targets[0].id:
        [d.id for d in ast.walk(assign) if isinstance(d, ast.Name)]
        for assign in statements
    }
    # full_graph also contains `range` and `i`. Keep only top levels var
    restricted = {}
    for var in full_graph:
        restricted[var] = [
            d for d in full_graph[var] if d in full_graph and d != var
        ]
    return restricted


# Search for a module but do not load it.
# Return the path if it is found
#
# Follow Python-ish rules for locating a module
#   * "from . import TTComponent":             Look for TTComponent in
#                                              the package's directory
#   * "from .TTComponent import foo":          Look for TTComponent in
#                                              the package's directory
#   * "from packagename import foo":           If packagename==ticktalkpython,
#                                              look for foo in the package's
#                                              directory
#   * "from packagename.component import foo": If packagename==ticktalkpython,
#                                              look for component in the
#                                              package's directory
#
def pathname_for_module(module, library_path=None):
    # Case 1: a file included in this package
    #
    # The absolute path to the package directory is the same
    # as the absolute path to this source file: __file__
    if library_path is None:
        package_path = os.path.dirname(__file__)
    else:
        package_path = library_path

    # Case 1: Implicit package-local ref, e.g., ".Instructions"
    # This does not properly handle .foo.bar
    if module[0] == ".":
        pathname = f"{package_path}/{module[1:]}.py"
        if os.path.exists(pathname):
            return pathname

    split = module.split('.')
    if (len(split) == 2) and (split[0] == package_name):
        # Case 2: Explicit package-local ref, e.g.,
        # "ticktalkpython.Instructions"
        pathname = f"{package_path}/{split[1]}.py"
    else:
        pathname = f"{package_path}/{module}.py"
    logger.debug(f"      |___ Trying {pathname}")
    if os.path.exists(pathname):
        return pathname

    # Try sys.path() prefixes
    if library_path is None:
        for path_prefix in sys.path:
            pathname = f"{path_prefix}/{module}.py"
            logger.debug(f"      |___ Trying {pathname}")
            if os.path.exists(pathname):
                return pathname
        if len(split) > 1:
            logger.debug(f"          {module}: multi-part module")
            package = '.'.join(split[0:len(split) - 1])
            spec = importlib.util.find_spec(split[-1], package=package)
        else:
            spec = importlib.util.find_spec(module)
        if spec is None:
            return None
        else:
            return spec.origin
    else:
        return None


# Enter with the name of a module from an "import" statement.
#
# If found, read the file and generate an AST.
# Visit the AST for SQified functions.
def import_sqified_functions_from_module(module, lib_path, visited_modules,
                                         context):
    '''
    Find the module and import any SQify or STREAMify-decorated functions
    '''
    logger.debug(f"  |___ {module}: Looking for it from file's local scope")
    module_path = pathname_for_module(module, library_path=lib_path)
    if module_path is None:
        logger.debug(f"       {module}: Looking for it from ticktalkpython scope")
        module_path = pathname_for_module(module, library_path=None)
    if module_path is None:
        logger.debug(f"       {module}: no path to it -- skipping")
        return
    logger.debug(f"       {module}: found it at {module_path}")
    if module_path == 'built-in':
        logger.debug(f"       {module}: built-in module -- skipping")
        return

    # module names do not include module_path
    if module_path in visited_modules:
        logger.debug(f"already visited {module}")
        return

    with open(module_path, "r", encoding='utf-8') as source:
        visited_modules.add(module_path)
        try:
            imported_module = ast.parse(source.read())
        except Exception as e:
            logger.warning(e)
            logger.warning(f"       {module}: import failed -- skipping")
            return
        TTImportVisitor(visited_modules, lib_path,
                        context).visit(imported_module)
        logger.debug(f"       {module}: import succeeded")


class TTGraphContextCreation(ast.NodeVisitor):
    '''
    '''

    # Ideally context would be be passed along with the function,
    # but the visitor pattern must follow a form (self, node)
    # to avoid this, we'll just make new visitors to make new contexts!
    def __init__(self, source, library_path, context: Context, pathname=None):
        self.source = source
        self.library_path = library_path
        self.context = context
        self.pathname = pathname
        self.visited_modules = set()
        self.source_vars = []

    def source_line(self, lineno):
        return self.source[lineno - 1]

    def visit_ImportFrom(self, node):
        logger.debug(
            f"___ Attempting to import SQ definitions from module {node.module}"
        )
        # Checks whole module rather than just the specified alias
        import_sqified_functions_from_module(node.module, self.library_path,
                                             self.visited_modules,
                                             self.context)
        return node

    def visit_Import(self, node):
        for alias in node.names:
            logger.debug(
                f"___ Attempting to import SQ definitions from module {alias.name}"
            )
            import_sqified_functions_from_module(alias.name, self.library_path,
                                                 self.visited_modules,
                                                 self.context)
        return node

    def visit_FunctionDef(self, node):
        logger.debug(f"*** function: {node.name}")
        for decorator in node.decorator_list:
            logger.debug(f"*** decorator: {decorator.id}")
            if decorator.id == 'SQify' or decorator.id == 'STREAMify':
                self.context.insert_symbol(node.name, SymbolType.SQ, node)
                break

            elif decorator.id == 'GRAPHify':
                self.context.insert_symbol(node.name, SymbolType.GRAPH, node)

                if len(node.args.args) == 0:
                    # TODO: Silently add trigger arcs instead of throwing
                    raise TTSyntaxError(
                        "Lacking a parameter in the function signature",
                        node.lineno, self.source_line(node.lineno),
                        self.pathname)

                # TODO: what should be used with trigger_var
                trigger_var = node.args.args[0].arg
                self.context.trigger_var = trigger_var

                self.source_vars_ports = [
                    Port.Port(arg.arg) for arg in node.args.args
                ]

                for port in self.source_vars_ports:
                    self.context.insert_port_symbol(port.data_name, port)


# gets all free variables in an expression
class TTEnvVisitor(ast.NodeVisitor):
    def __init__(self, node):
        self.node = node
        self.has_constants = False
        self.env: Set[str] = self.visit(node)

    def get_env(self):
        return self.env

    def visit_BinOp(self, node: ast.BinOp) -> set:
        lhs = self.visit(node.left)
        rhs = self.visit(node.right)
        return lhs | rhs

    def visit_UnaryOp(self, node: ast.UnaryOp) -> set:
        return self.visit(node.operand)

    def visit_Name(self, node: ast.Name) -> set:
        return {node.id}

    def visit_Call(self, node: ast.Call) -> set:
        # If a func has 0 arguments, it still needs a trigger
        if len(node.args) == 0:
            self.has_constants = True

        return reduce((lambda acc, arg: acc | self.visit(arg)), node.args,
                      set())

    def visit_IfExp(self, node: ast.IfExp) -> set:
        return self.visit(node.test) | self.visit(node.body) | self.visit(
            node.orelse)

    def visit_Compare(self, node: ast.Compare) -> set:
        return reduce((lambda acc, c: acc | self.visit(c)), node.comparators,
                      self.visit(node.left))

    def visit_BoolOp(self, node: ast.BoolOp) -> set:
        return reduce(
            (lambda acc, clause: acc | self.visit(clause), node.values, set()))

    def visit_Constant(self, node: ast.Constant) -> set:
        self.has_constants = True
        return set()

    # backward compatibility with 3.7
    def visit_Num(self, node: ast.Constant) -> set:
        return self.visit_Constant(node)

    def visit_NameConstant(self, node: ast.NameConstant) -> set:
        return self.visit_Constant(node)

    def generic_visit(self, node: ast.AST) -> set:
        logger.debug(f"TTEnvVisitor visited {node}")
        return set()
