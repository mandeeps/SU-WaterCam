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

from .Error import TTCompilerError

from enum import Enum
from . import DebugLogger
import pprint
import copy
from .Port import Port

logger = DebugLogger.get_logger('CompilerUtils')


class SymbolType(Enum):
    '''
    Symbol Types used in SymbolTable
    '''
    SQ = 1
    PORT = 2
    GRAPH = 3


class SymbolRecord:

    def __init__(self, type: SymbolType, val):
        self.type = type
        self.val = val

    def __repr__(self):
        return f'({self.type}, {self.val})'


class SymbolRecordPort(SymbolRecord):

    def __init__(self, val, is_streaming: bool):
        super().__init__(SymbolType.PORT, val)
        self.is_streaming = is_streaming

    def __repr__(self):
        return f'({self.type}, {self.val}, is_streaming:{self.is_streaming})'


# This describes the symbol table of
# creating a new table will shallow copy
# specify which node is outputting the value
class SymbolTable:

    def __init__(self, table=None):
        self.sym_table = dict(table) if table is not None else {}

    # note that this a shallow copy
    def __or__(self, other: 'SymbolTable') -> 'SymbolTable':
        return SymbolTable(dict(self.sym_table).update(dict(other.sym_table)))

    def __contains__(self, key):
        return key in self.sym_table

    def __getitem__(self, key):
        return self.sym_table[key]

    def __setitem__(self, key, value):
        self.sym_table[key] = value

    def __str__(self):
        return pprint.pformat(self.sym_table)

    def __naming_sanity_check__(self, name):
        if name not in self.sym_table:
            raise TTCompilerError(
                f"Cannot alpha rename {name} as it does not exist in "
                "the symbol table.")

    #### Port operations

    # only changes the var's name in value
    # useful for variable shadowing
    def update_port(self, name, new_name):
        self.__naming_sanity_check__(name)
        if new_name in self.sym_table:
            logger.debug(
                f'Output port {name} overwrites input port {new_name} selection'
            )

        lookup = self.sym_table[name]
        if lookup.type is not SymbolType.PORT:
            raise TTCompilerError(f"Cannot rename {lookup.type} type")
        lookup.val.data_name = new_name

    # changes the var's name in key and value
    def alpha_rename(self, name, new_name):
        self.__naming_sanity_check__(name)
        if new_name in self.sym_table:
            raise TTCompilerError(
                f"Cannot alpha rename to {new_name} as it already "
                "exists in the symbol table")

        # lookup of symbol records must be renamed
        lookup = self.sym_table[name]
        if lookup.type is not SymbolType.PORT:
            raise TTCompilerError(f"Cannot rename {lookup.type} type")
        lookup.val.data_name = new_name
        del self.sym_table[name]
        self.sym_table[new_name] = lookup

    def to_list(self):
        return [self.sym_table]

    #### insertion

    def insert_symbol(self, symbol, type, val):
        self.sym_table[symbol] = SymbolRecord(type, val)

    def insert_port_symbol(self, symbol, val, is_streaming):
        self.sym_table[symbol] = SymbolRecordPort(val, is_streaming)


class Context:

    def __init__(self,
                 trigger_var=None,
                 sym_table: SymbolTable = None,
                 clock_dictionary: dict = None):
        self.trigger_var = trigger_var if trigger_var is not None else {}
        self.sym_table = (sym_table
                          if sym_table is not None else SymbolTable())
        self.clock_dictionary = (clock_dictionary
                                 if clock_dictionary is not None else {})

    @classmethod
    def from_context(cls, class_instance: 'Context'):
        new_trigger_var = copy.copy(class_instance.trigger_var)
        new_sym_table = copy.deepcopy(class_instance.sym_table)
        new_clock_dict = copy.copy(class_instance.clock_dictionary)

        return cls(new_trigger_var, new_sym_table, new_clock_dict)

    # TODO: slow, should only add new output ports to make faster
    def merge_shadow_sym_table(self, sym_table: SymbolTable):
        '''
        Will merge the two sym_tables. A shadow sym table is where the key for
        the arc might not match its symbol in the arc. Will throw an error if
        there is any overlap
        '''
        # TODO: add iterator on values
        for val in sym_table.sym_table.keys():
            if val in self.sym_table:
                logger.debug(f'not inserting {val} into sym table')
            else:
                logger.debug(f'inserting {val} into sym table')
                self.sym_table[val] = sym_table[val]

    def __contains__(self, key):
        return (key is self.trigger_var or key in self.sym_table
                or key in self.clock_dictionary)

    def __getitem__(self, key):
        return self.sym_table[key]

    def insert_symbol(self, symbol, type, val):
        return self.sym_table.insert_symbol(symbol, type, val)

    def insert_port_symbol(self, symbol, val, is_streaming=False):
        return self.sym_table.insert_port_symbol(symbol, val, is_streaming)

    def create_opp(self, symbol=None, is_streaming=False):
        opp = Port(symbol)
        self.insert_port_symbol(opp.data_name, opp, is_streaming)
        return opp

    def update_port(self, name, new_name):
        if name is self.trigger_var:
            self.trigger_var = new_name
            self.alpha_rename(name, new_name)
        else:
            self.sym_table.update_port(name, new_name)

    def alpha_rename(self, name, new_name):
        if name is self.trigger_var:
            raise TTCompilerError(
                "Renaming the graph's source vars is not allowed")
        else:
            self.sym_table.alpha_rename(name, new_name)

    def __str__(self):
        return pprint.pformat(f"{str(self.trigger_var)}, " +
                              str(self.sym_table))


class ShadowEnv:

    def __init__(self, env, trigger_name, has_constants: bool, rename: str):
        self.env = env
        self.ordered_env = list(env)
        self.has_constants = has_constants
        self.rename = rename
        self.trigger_name = trigger_name

    def get_env_list(self):
        return self.get_renamed_env_list("")

    # the normal use of this func is to leave the param rename None
    def get_renamed_env_list(self, rename=None):
        prepended_name = self.rename
        # allow "" to override rename
        if rename is not None:
            prepended_name = rename

        l = []
        # add trigger if has_constants
        if self.has_constants:
            l = [self.trigger_name]
        return [prepended_name + sym for sym in self.ordered_env + l]

    def get_trigger_name(self):
        return self.rename + self.trigger_name


class TTBlockInfo:
    '''
    An SQ Context refers to contextual information that surrounds the SQ,
    similar to scoping in more traditional sequential programming languages.

    The mechanisms within the context are based on TTPython synxtax from 'with'
    constructs, which are used to specify clocks, backup (plan B)
    callbacks/handlers, deadlines, mapping constraints, etc. Multiple SQs may
    use the same context
    '''

    def __init__(self,
                 name="(no name)",
                 clockspec=None,
                 base_context=None,
                 constraints=None):
        if base_context is not None:
            self.name = base_context.name
            self.clockspec = base_context.clockspec
            self.constraints = base_context.constraints
        else:
            self.name = name
            self.clockspec = clockspec
            self.constraints = [] if constraints is None else constraints

    def id(self):
        return id(self)

    def __repr__(self):
        return f"<TTBlockInfo {self.name} {self.id()}>"

    def json(self):
        j = {}
        j['name'] = self.name
        if self.clockspec is not None:
            # print(f"Here is the clockspec: {self.clockspec}")
            j['clockspec'] = self.clockspec.json()
        if self.planB_handler is not None:
            j['planB_handler'] = self.planB_handler.json()
        if self.deadline is not None:
            j['deadline'] = self.deadline.json()
        return j
