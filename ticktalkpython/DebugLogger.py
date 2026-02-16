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
Logging functionality that augments the builtin logging library.
'''

import logging

# setup default logging characteristics
default_level = logging.INFO
logging.basicConfig()
logger = logging.getLogger('TTPython')
logger.setLevel(default_level)
logger.handlers.clear()

# set up a handler for the console window
ch = logging.StreamHandler()
ch.setLevel(default_level)
# setup a handler for files
fh = None
formatter = logging.Formatter('%(name)s:%(levelname)s:: %(message)s')
ch.setFormatter(formatter)

logger.addHandler(ch)
logger.propagate = False


def addLoggingLevel(levelName, levelNum, methodName=None):
    """
    Comprehensively adds a new logging level to the `logging` module and the
    currently configured logging class.

    `levelName` becomes an attribute of the `logging` module with the value
    `levelNum`. `methodName` becomes a convenience method for both `logging`
    itself and the class returned by `logging.getLoggerClass()` (usually just
    `logging.Logger`). If `methodName` is not specified, `levelName.lower()` is
    used.

    To avoid accidental clobberings of existing attributes, this method will
    raise an `AttributeError` if the level name is already an attribute of the
    `logging` module or if the method name is already present

    Example
    -------
    >>> addLoggingLevel('TRACE', logging.DEBUG - 5)
    >>> logging.getLogger(__name__).setLevel("TRACE")
    >>> logging.getLogger(__name__).trace('that worked')
    >>> logging.trace('so did this')
    >>> logging.TRACE
    5

    """
    if not methodName:
        methodName = levelName.lower()

    if hasattr(logging, levelName):
        raise AttributeError(
            '{} already defined in logging module'.format(levelName))
    if hasattr(logging, methodName):
        raise AttributeError(
            '{} already defined in logging module'.format(methodName))
    if hasattr(logging.getLoggerClass(), methodName):
        raise AttributeError(
            '{} already defined in logger class'.format(methodName))

    # This method was inspired by the answers to Stack Overflow post
    # http://stackoverflow.com/q/2183233/2988730, especially
    # http://stackoverflow.com/a/13638084/2988730
    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)

    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, *args, **kwargs)

    logging.addLevelName(levelNum, levelName)
    setattr(logging, levelName, levelNum)
    setattr(logging.getLoggerClass(), methodName, logForLevel)
    setattr(logging, methodName, logToRoot)


PROFILE_LEVEL = logging.DEBUG + 5
addLoggingLevel('PROFILE', PROFILE_LEVEL)


def set_base_logger_profiling():
    # logging.DEBUG is at 10, logging.INFO at 20
    # we will use this for execution info since it may be noisy
    # for normal execution
    set_base_logger_level(PROFILE_LEVEL)


def get_logger(module_name):
    '''
    Create a logger for another module, which uses 'TTPython' as the root level
    name

    :param module_name: The name of the module to use this logger; it will be
        included in the header of each message

    :type module_name: string

    :return: the logger to be used for printing output to the console, file,
        etc. File output is diabled by default

    :rtype: ``logging.Logger``

    '''
    return logging.getLogger('TTPython.' + module_name)


def get_base_logger():
    '''
    Use this to get the base logger and set configurations to it. The base name
    is 'TTPython'

    If this is going to modified, it should be done very early in the import
    sequence.

    :return: the logger to be used for printing output to the console, file,
        etc. File output is diabled by default

    :rtype: ``logging.Logger``
    '''
    return logger


def set_base_logger_debug():
    '''
    '''
    set_base_logger_level(logging.DEBUG)


def set_base_logger_info():
    '''
    '''
    set_base_logger_level(logging.INFO)


def set_base_logger_level(level):
    '''
    Set the level of the base logger, which dictates what messages are displayed
    and which are hidden

    :param level: The logging level (nominally between 0 and 50)

    :type level: int

    :return: None
    '''
    if level == 0:
        logger.warning(
            'Note that setting the logger level to zero automatically uses '
            'the "WARNING" level')

    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)


def set_console_level(level):
    '''
    Set the logging level of the console handler (prints to terminal)

    :param level: The logging level (nominally between 0 and 50)

    :type level: int

    :return: None
    '''
    global ch
    ch.setLevel(level)


def setup_file_handler(path, level=logging.DEBUG):
    '''
    Configure a file handler so that logger also prints output to file. It will
    use the same format as the console logger

    This may use a different logging level than the console handler to print
    more or less to file

    :param path: The file path of the log file

    :type path: string

    :param level: The logging level, specifically for the file handler
        (nominally a value between 0 and 50)

    :type level: int
    '''

    global fh
    fh = logging.FileHandler(path)
    fh.setLevel(level)
    fh.setFormatter(formatter)
    logger.addHandler(fh)


def set_file_level(level):
    '''
    Set the logging level of the file handler (prints to file)


    :param level: The logging level, specifically for the file handler
        (nominally a value between 0 and 50)

    :type level: int
    '''
    global fh
    assert fh is not None, 'File handler is none; \
        setup_file_handler should be run first'

    fh.setLevel(level)


if __name__ == "__main__":

    setup_file_handler('./test.log', level=1)
    logger.critical('critical message')
    logger.error('error message')
    logger.warning('warning message')
    logger.info('info message')
    logger.debug('debug message')
    logger.log(2, 'minimal level')
