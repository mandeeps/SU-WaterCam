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
TTPython-specific error handling
'''

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class TTSyntaxError(Exception):
    def __init__(self, message, lineno, source=None, pathname=None):
        super().__init__(message, lineno, source, pathname)
        self.message = message
        self.source = source
        self.pathname = pathname
        self.lineno = lineno

    def __repr__(self):
        string = "\n"
        if self.source is not None and self.pathname is not None:
            string += (f'{bcolors.OKBLUE}File {bcolors.OKGREEN}'
                       f'"{self.pathname}"{bcolors.OKBLUE}, '
                       f'line {bcolors.OKGREEN}{self.lineno}\n'
                       f'{bcolors.FAIL}{self.source.rstrip()}\n')
        elif self.source is not None:
            string += (
                f"{bcolors.OKBLUE}line {bcolors.OKGREEN}"
                f"{self.lineno}\n{bcolors.FAIL}{self.source.rstrip()}\n")
        else:
            string += f"{bcolors.OKBLUE}line {bcolors.OKGREEN}{self.lineno}\n"
        string += f"\n{bcolors.FAIL}SyntaxError: {bcolors.ENDC}{self.message}"
        return string


class TTCompilerError(Exception):
    def __init__(self, message, lineno=None, source=None, pathname=None):
        super().__init__(message)
        self.message = message
        self.source = source
        self.pathname = pathname
        self.lineno = lineno

    def __repr__(self):
        string = "\n"
        if (self.lineno is not None and self.source is not None
                and self.pathname is not None):
            string += (f'{bcolors.OKBLUE}File {bcolors.OKGREEN}'
                       f'"{self.pathname}"{bcolors.OKBLUE}, '
                       f'line {bcolors.OKGREEN}{self.lineno}\n'
                       f'{bcolors.FAIL}{self.source.rstrip()}\n')
        elif self.lineno is not None and self.source is not None:
            string += (
                f"{bcolors.OKBLUE}line {bcolors.OKGREEN}"
                f"{self.lineno}\n{bcolors.FAIL}{self.source.rstrip()}\n")
        elif self.lineno is not None:
            string += f"{bcolors.OKBLUE}line {bcolors.OKGREEN}{self.lineno}\n"
        string += f"\n{bcolors.FAIL}SyntaxError: {bcolors.ENDC}{self.message}"
        return string


class TTComponentError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message

    def __repr__(self):
        string = "\n"
        string += f"\n{bcolors.FAIL}ComponentError: {bcolors.ENDC}{self.message}"
        return string


class TTQueryError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message

    def __repr__(self):
        string = "\n"
        string += f"\n{bcolors.FAIL}QueryError: {bcolors.ENDC}{self.message}"
        return string


class TTConstraintError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message

    def __repr__(self):
        string = "\n"
        string += f"\n{bcolors.FAIL}ConstraintError: {bcolors.ENDC}{self.message}"
        return string
