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


# Heavy CPU work and cellular transmission take turns. Together they drew
# 2.5-2.6 A from the V50 and pulled the Pi under-voltage (006, 2026-10-15,
# docs/POWER_ANALYSIS.md): within a capture iteration the IP upload already
# follows segmentation, but the next iteration's capture starts on its clock and
# can segment while the previous iteration's cellular upload is still going.
POWER_LOCK = "/tmp/watercam_power.lock"


@contextlib.contextmanager
def power_exclusive(what, timeout_s):
    """Run the body while no other heavy step (coregistration, segmentation, a
    cellular bring-up or upload) holds the power lock. Waits up to timeout_s,
    then goes ahead anyway: a stuck holder must never stall the cycle."""
    started = time.monotonic()
    cm = flock_deadline(POWER_LOCK, timeout_s)
    try:
        cm.__enter__()
    except TimeoutError:
        print(f"⚡ {what}: the power lock was still held after {timeout_s:.0f} s, going ahead")
        cm = None
    waited = time.monotonic() - started
    if cm is not None and waited >= 1:
        print(f"⚡ {what} waited {waited:.0f} s for a heavy step or cellular upload to finish")
    try:
        yield
    finally:
        if cm is not None:
            cm.__exit__(None, None, None)


class PowerHold:
    """power_exclusive() held across a stretch of code with several exits, such as
    ip_uplink_transmit while cellular is up: acquire() once, release() in finally."""

    def __init__(self):
        self._cm = None

    def acquire(self, what, timeout_s):
        if self._cm is None:
            cm = power_exclusive(what, timeout_s)
            cm.__enter__()
            self._cm = cm

    def release(self):
        if self._cm is not None:
            cm, self._cm = self._cm, None
            cm.__exit__(None, None, None)
