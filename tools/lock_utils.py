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


CELLULAR_USERS_LOCK = "/tmp/watercam_cellular_users.lock"
CELLULAR_TEARDOWN_OWED = "/tmp/watercam_cellular_teardown_owed"


class CellularUsers:
    """Who is using the cellular session, so the last upload to finish takes it
    down (#337). Two IP uploads can run in one wake; before, the one that brought
    cellular up took it down when it finished, even while the other was sending.

    Each upload calls join() when it starts and leave(started) when it ends. join()
    takes a shared flock on CELLULAR_USERS_LOCK for the upload's lifetime. leave()
    releases it and tries for an exclusive lock without waiting: if that succeeds,
    no other upload is running, and it returns True when this upload started
    cellular or another one left a teardown owed. If another upload is still
    running, an upload that started cellular leaves the teardown owed (a marker
    file) and returns False. Both files are in /tmp, so a boot starts clean."""

    def __init__(self, lock_path=None, owed_path=None):
        self.lock_path = lock_path or CELLULAR_USERS_LOCK
        self.owed_path = owed_path or CELLULAR_TEARDOWN_OWED
        self._fd = None

    def join(self):
        if self._fd is not None:
            return
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        except OSError:
            try:                # made by another user: a read-only fd locks just as well
                fd = os.open(self.lock_path, os.O_RDONLY)
            except OSError:
                return          # no coordination: leave() falls back
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
        except OSError:
            os.close(fd)
            return
        self._fd = fd

    def leave(self, started, teardown=None):
        """Release; return True if this upload should take cellular down now.
        teardown, if given, is called then while the exclusive lock is still
        held, so no upload can join and start using cellular in between."""
        fd, self._fd = self._fd, None
        if fd is None:
            # no coordination: still honour a teardown requested by this or any upload
            return self._finish(started, teardown)
        try:
            if started:
                # Marked before letting go, so an upload finishing at the same
                # moment that wins the exclusive lock below sees it
                self._mark_owed()
            fcntl.flock(fd, fcntl.LOCK_UN)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False    # another upload is still running: the last one out takes it down
            return self._finish(started, teardown)
        finally:
            os.close(fd)

    def _finish(self, started, teardown):
        """Run the teardown if one is due, and clear the request only after it, so
        a teardown that raises leaves the request for the next upload."""
        owed = os.path.exists(self.owed_path)
        down = owed or bool(started)
        if down and teardown:
            try:
                teardown()
            except Exception:
                if started:
                    self._mark_owed()
                return down
        if owed:
            self._take_owed()
        return down

    def _take_owed(self):
        """Whether a teardown was requested; clears the request."""
        if not os.path.exists(self.owed_path):
            return False
        try:
            os.unlink(self.owed_path)
        except OSError:
            pass
        return True

    def request_teardown(self):
        """Ask for cellular to go down without cutting off another upload using
        it: leave() then returns True for whichever upload finishes last."""
        self._mark_owed()

    def _mark_owed(self):
        try:
            with open(self.owed_path, "w"):
                pass
        except OSError:
            pass
