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
TTPython Device Query Interface

Given an Ensemble or EnsembleSet, a programmer needs a method to extract
individual ``TTEnsembleInfo``s and ``TTComponent``s. This can be done with
two objects-''TTQuery'' and ''TTQueryCondition''-and their derivatives.  A
``TTQueryCondition`` is a wrapper around a singular attribute of an Ensemble or
Component, restricting it to a particular value or subset of values. A
``TTQuery`` is a set of ``TTQueryCondition``s and a "target"
``TTEnsembleInfo``or ``TTComponent`` object on which they are evaluated.
Queries can inherit their "target" from other queries, allowing for nesting,
joining, and disjoining.
'''

from enum import Enum

from .Error import TTQueryError
from .Component import TTComponent
# break a circular dependency

class TTConstraint():
    def __init__(self, name = None, components = None):
        self.name = [] if name is None else name
        self.components = [] if components is None else components

class QueryOp(Enum):
    OR = 1
    AND = 2

class TTQueryCondition:
    '''
    Given that various types of conditions may structure themselves around
    values in different ways, the only guaranteed attribute of a
    ``TTQueryCondition`` is its ability to be tested against a particular value.
    '''
    def __init__(self):
        pass
    def test(self, query_object):
        '''
        :param value: The value against which this condition will be tested.

        :type value: any

        :return: result of the query

        :rtype: bool
        '''
        pass

class TTQueryConditionRange(TTQueryCondition):
    '''
    ``TTQueryConditionRange`` indicates whether a given value is within an
    inclusive bounds.

    :param min: The inclusive minimum of the range

    :type min: float

    :param max: The inclusive maximum of the range

    :type max: float
    '''
    def __init__(self, min_val, max_val):
        super().__init__()
        if not isinstance(min_val, float) or not isinstance(float, max_val):
            raise TTQueryError("The bounds of a range must be of type float.")
        if min_val is None and max_val is None:
            raise TTQueryError(
                "A range must have at least one inclusive endpoint.")
        self.min = min_val
        self.max = max_val

    def test(self, query_object):
        '''
        Test if a given value is within the range. A range with a single defined
        boundary defaults to checking if the value is less than or equal to, or
        greater than or equal to, the given boundary

        :param value: a value to test against the range

        :type value: float

        :return: if the value was found to be within [min, max]

        :rtype: bool
        '''
        if self.min is not None:
            if self.max is not None:
                return query_object in __builtins__.range(min, max)
            else:
                return query_object >= self.min
        else:
            return query_object <= self.max

def within(min_val, max_val):
    '''
    A shorthand form of the ``TTQueryConditionRange`` constructor for use in
    complex queries.

    :param min: The inclusive minimum of the range

    :type min: float

    :param max: The inclusive maximum of the range

    :type max: float

    :return: a range condition corresponding to [min, max]

    :rtype: TTQueryConditionRange
    '''
    return TTQueryConditionRange(min_val, max_val)

def leq(max_val):
    '''
    A shorthand form of the ``TTQueryConditionRange`` constructor for use in
    complex queries.

    :param max: The inclusive maximum of the range

    :type max: float

    :return: a range condition corresponding to [-infinity, max]

    :rtype: TTQueryConditionRange
    '''
    return TTQueryConditionRange(None, max_val)

def geq(min_val):
    '''
    A shorthand form of the ``TTQueryConditionRange`` constructor for use in
    complex queries.

    :param min: The inclusive minimum of the range

    :type min: float

    :return: a range condition corresponding to [min, +infinity]

    :rtype: TTQueryConditionRange
    '''
    return TTQueryConditionRange(min_val, None)

class TTQueryConditionExclude(TTQueryCondition):
    '''
    ``TTQueryConditionExclude`` indicates whether a given value is excluded from
    a mixed set of values and conditions.

    :param value_conditions: a set of values and ``TTQueryCondition``s to test
        against a given value.

    :type value_conditions: tuple
    '''
    def __init__(self, *value_conditions):
        super().__init__()
        self.subconditions = value_conditions

    def test(self, query_object):
        '''
        Check if the given value is excluded from the set of subconditions. If a
        subcondition is a ``TTQueryCondition``, it's ``test`` function is called
        on the given value. Other types of conditions are checked for equality.

        :param value: the value to test against the set

        :type value: any

        :return: if the given value was excluded from all subconditions.

        :rtype: bool
        '''
        for condition in self.subconditions:
            if isinstance(condition, TTQueryCondition):
                if condition.test(query_object):
                    return False
            else:
                if condition == query_object:
                    return False
        return True

def excluding(*conditions):
    '''
    A shorthand form of the ``TTQueryConditionExclude`` constructor for use in
    complex queries.

    :param value_conditions: a set of values and ``TTQueryCondition``s to test
    against a given value.

    :type value_conditions: tuple

    :return: an exclude condition corresponding to the set of conditions

    :rtype: TTQueryConditionExclude

    '''
    return TTQueryConditionExclude(conditions)


class TTQCEnsembleName(TTQueryCondition):
    '''
    A ``TTQueryName`` is a ``TTQuery`` for a ``TTEnsembleInfo`` with a
    given name.

    :param condition: the name of the ``TTEnsembleInfo`` to be queried for.

    :type condition: string
    '''
    def __init__(self, name):
        super().__init__()
        self.name = name

    def test(self, query_object):
        '''
        Tests if the given ``TTEnsembleInfo`` has a matching name.

        :param query_object: the ``TTEnsembleInfo`` to be tested

        :type query_object: ``TTEnsembleInfo``
        '''
        super().test(query_object)
        if not isinstance(query_object, TTEnsembleInfo):
            raise TTQueryError(
                "Expected the queried object to be of type TT")
        return self.name == query_object.name

    def __repr__(self):
        return f"TTQCEnsembleName: {self.name}"

def ensemble_name(condition):
    '''
    A shorthand form of TTQueryName for use in complex queries.

    :param condition: the name of the ``TTEnsembleInfo`` to be queried for.

    :type condition: string

    :return: a query for a ``TTEnsembleInfo`` with the given name

    :rtype: ``TTQCEnsembleName``
    '''

    return TTQCEnsembleName(condition)


class TTQCComponentName(TTQueryCondition):
    '''
    A ``TTQCComponentName`` is a ``TTQueryCondition`` for a ``TTComponent`` with
    a given name.

    :param condition: the name of the ``TTEnsembleInfo`` or ``TTComponent``
        to be queried for.

    :type condition: string
    '''
    def __init__(self, name):
        super().__init__()
        self.name = name

    def test(self, query_object):
        '''
        Tests if the given ``TTComponent``  has a matching name.

        :param query_object: the ``TTComponent`` or ``TTEnsembleInfo`` to
            be tested

        :type query_object: ``TTComponent`` | ``TTEnsembleInfo``
        '''
        super().test(query_object)
        if (not (isinstance(query_object, TTComponent)
                 or isinstance(query_object, TTEnsembleInfo))):
            raise TTQueryError(
                "Expected the queried object to be of type TTEnsembleInfo or TTComponent."
            )
        if isinstance(query_object, TTEnsembleInfo):
            return self.name in query_object.components
        if isinstance(query_object, TTComponent):
            return self.name == query_object.name

    def __repr__(self):
        return f"TTQCComponentName: {self.name}"

    def json(self):
        return {'hasComponentName': self.name}


def component_name(condition):
    '''
    A shorthand form of TTQueryName for use in complex queries.

    :param condition: the name of the ``TTComponent`` to be queried for.

    :type condition: string

    :return: a query for a ``TTComponent`` with the given name

    :rtype: ``TTQueryName``
    '''

    return TTQCComponentName(condition)


class TTQuery():
    '''
    A ``TTQuery`` is a set of conditions to be evaluated against a given
    TTComponent or TTEnsembleInfo. The term 'conditions' is used to refer
    to any value; ``TTQueryCondition``s are evaluated using their ``test``
    functions, and all other types are checked for equality.

    :param conditions: the set of values and ``TTQueryCondition``s composing the
        query.

    :type conditions: tuple
    '''
    def __init__(self, conditions, op):
        self.conditions = conditions
        self.op = op

    def test(self, query_object):
        '''
        Ensures that the given object is of type ``TTComponent`` or
        ``TTEnsembleInfo``.

        :param query_object: The ``TTComponent`` or ``TTEnsembleInfo``
            queried against.

        :type query_object: ``TTComponent`` | ``TTEnsembleInfo``
        '''
        if not (isinstance(query_object, TTComponent)
                or isinstance(query_object, TTEnsembleInfo)):
            raise TTQueryError("Expected the queried object to be of type "
                               "TTEnsembleInfo or TTComponent.")

        logical_list = [c.test(query_object) for c in self.conditions]

        if self.op == QueryOp.AND:
            return not False in logical_list
        elif self.op == QueryOp.OR:
            return True in logical_list
        else:
            raise TTQueryError("Unexpected query operator.")


class TTEnsembleQuery(TTQuery):
    '''
    A ``TTEnsembleQuery`` is a ``TTQuery`` for exclusive use with
    ``TTEnsembleInfo`` objects.

    :param conditions: the set of values and ``TTQueryCondition``s composing the
        query.

    :type conditions: tuple
    '''
    def __init__(self, *conditions):
        super().__init__(*conditions)

    def test(self, query_object):
        if not isinstance(query_object, TTEnsembleInfo):
            raise TTQueryError("Expected 'component' to be of type TT")

class TTQComponent(TTQuery):
    def __init__(self, *conditions):
        super().__init__(*conditions)

    def test(self, query_object):
        if not isinstance(query_object, TTComponent):
            raise TTQueryError("Expected 'component' to be of type TTComponent.")

class TTQComponentCustomField(TTQComponent):
    def __init__(self, key, condition):
        super().__init__(condition)
        self.key = key
        self.condition = condition

    def test(self, query_object):
        super().test(query_object)
        return self.condition.test(query_object.get_custom_field(self.key))

def custom(key, condition):
    return TTQComponentCustomField(key, condition)


class TTEnsembleInfo():
    '''
    A lightweight representation of an ensemble, characterized by its name,
    address, and hardware properties
    '''
    def __init__(self, name, address, components):
        self.name = name
        self.address = address
        self.components = components

    def __repr__(self):
        return f"TTEnsembleInfo: {self.address} named {self.name} with components {self.components}"
