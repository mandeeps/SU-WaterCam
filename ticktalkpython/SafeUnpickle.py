'''
Restricted unpickling for messages that arrive on the runtime's UDP listener.

The listener (``NetworkManager.handle_network_message``) used ``pickle.loads``
on every datagram, and unpickling can call any importable function: any process
able to send a UDP packet to the port could run code as the runtime's user
(SU-WaterCam #204). This unpickler only resolves:

- classes defined in ``ticktalkpython`` (the runtime's messages, tokens, tags,
  enums), never functions; and
- an explicit list of library types that token values are made of
  (``ALLOWED``), each a type or a known-safe reconstructor.

``TT_PICKLE_POLICY`` (environment) picks what happens to anything else:

- ``warn`` (default): log it once and load it anyway, so a type the list
  misses can't break a capture while the list is being completed;
- ``enforce``: refuse the message (``pickle.UnpicklingError``);
- ``off``: plain ``pickle.loads``, as before.
'''

import io
import logging
import os
import pickle
import pickletools

logger = logging.getLogger("TTPython.SafeUnpickle")

PACKAGE = "ticktalkpython."

# (module, name) pairs outside ticktalkpython that messages may contain.
ALLOWED = {
    ("builtins", "set"), ("builtins", "frozenset"), ("builtins", "bytearray"),
    ("builtins", "complex"), ("builtins", "slice"), ("builtins", "range"),
    ("builtins", "object"),
    ("copyreg", "_reconstructor"),
    ("collections", "OrderedDict"), ("collections", "deque"),
    ("datetime", "datetime"), ("datetime", "date"), ("datetime", "time"),
    ("datetime", "timedelta"), ("datetime", "timezone"),
    ("pathlib", "PosixPath"), ("pathlib", "PurePosixPath"),
    # the runtime's time intervals
    ("intervaltree.intervaltree", "IntervalTree"), ("intervaltree.interval", "Interval"),
    # TTClock keeps these two functions as attributes: they read and format the
    # time. Named one by one: other ticktalkpython functions stay refused.
    ("ticktalkpython.Clock", "default_read_time"),
    ("ticktalkpython.Clock", "default_convert_to_readable_time"),
    # numpy arrays and scalars (numpy 1.x and 2.x module paths)
    ("numpy", "ndarray"), ("numpy", "dtype"),
    ("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
    ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"),
    ("numpy.core.numeric", "_frombuffer"), ("numpy._core.numeric", "_frombuffer"),
}

_warned = set()


def policy() -> str:
    value = (os.environ.get("TT_PICKLE_POLICY") or "warn").strip().lower()
    return value if value in ("warn", "enforce", "off") else "warn"


def allowed(module: str, name: str, obj) -> bool:
    if (module, name) in ALLOWED:
        return True
    # ticktalkpython classes only: a function there could be called by the
    # stream (REDUCE), a class can only be built
    return (module + ".").startswith(PACKAGE) and isinstance(obj, type)


class RestrictedUnpickler(pickle.Unpickler):
    def __init__(self, file, mode: str):
        super().__init__(file)
        self.mode = mode

    def find_class(self, module, name):
        if self.mode == "enforce" and not (module, name) in ALLOWED \
                and not (module + ".").startswith(PACKAGE):
            # refuse before importing anything the list doesn't name
            raise pickle.UnpicklingError(f"blocked {module}.{name}")
        obj = super().find_class(module, name)
        if allowed(module, name, obj):
            return obj
        if self.mode == "enforce":
            raise pickle.UnpicklingError(f"blocked {module}.{name}")
        if (module, name) not in _warned:
            _warned.add((module, name))
            logger.warning("unpickled %s.%s, which isn't on the allowlist "
                           "(TT_PICKLE_POLICY=enforce would refuse it; #204)", module, name)
        return obj


_MEMO_OPS = {"PUT", "BINPUT", "LONG_BINPUT", "GET", "BINGET", "LONG_BINGET"}


def check_lengths(data: bytes) -> None:
    """Refuse a stream whose opcodes claim more bytes than it holds.

    The C unpickler buffers a FRAME, and allocates a BYTES8/BYTEARRAY8/
    BINUNICODE8 argument, at its claimed size before finding the stream too
    short: one damaged or crafted datagram claiming gigabytes gets the runtime
    killed for memory (overcommit lets the allocation through). pickletools
    reads each argument from the bytes actually there, so it raises instead,
    without allocating; it doesn't check a FRAME's size, so that is done here.
    Likewise a PUT/GET to a huge memo index makes the C unpickler grow its memo
    table to that index (2**31 entries is 16 GB): a real stream can't use an
    index beyond its own length, one opcode per memo entry at least."""
    try:
        for op, arg, pos in pickletools.genops(data):
            if op.name == "FRAME" and arg > len(data) - pos:
                raise ValueError(f"frame of {arg} bytes at {pos}, but only "
                                 f"{len(data) - pos} remain")
            if op.name in _MEMO_OPS and int(arg) > len(data):
                raise ValueError(f"memo index {arg} at {pos} in a {len(data)} byte stream")
    except ValueError as e:
        raise pickle.UnpicklingError(f"malformed stream: {e}") from None


def loads(data):
    mode = policy()
    if mode == "off":
        return pickle.loads(data)
    data = bytes(data)
    if mode == "enforce":
        check_lengths(data)
    return RestrictedUnpickler(io.BytesIO(data), mode).load()
