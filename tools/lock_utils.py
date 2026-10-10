"""A cross-process lock that gives up after a deadline.

The capture graph runs its steps in series up to call_shutdown(). A step that
waits forever on a lock (because a previous holder hung) blocks every later step,
so the unit stays awake and never uploads or shuts down. Waiting with a deadline
turns that into one skipped step.
"""
import contextlib
import fcntl
import os
import time


@contextlib.contextmanager
def flock_deadline(path, timeout_s, poll_s=0.5):
    """Hold an exclusive flock on path; raise TimeoutError after timeout_s."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{path} still locked after {timeout_s:.0f} s") from None
                time.sleep(poll_s)
        yield
    finally:
        os.close(fd)                    # closing releases the lock
