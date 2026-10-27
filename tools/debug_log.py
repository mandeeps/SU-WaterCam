"""Optional debug output, off on production units.

On when /etc/watercam/debug-logs exists (`sudo touch` it; it survives software
updates, so test and problem units keep their debug lines) or WATERCAM_DEBUG=1
is set. Read once per process: a change applies at the next start.
"""
import os

DEBUG_FLAG = "/etc/watercam/debug-logs"


def _enabled() -> bool:
    return os.environ.get("WATERCAM_DEBUG") == "1" or os.path.exists(DEBUG_FLAG)


ENABLED = _enabled()


def debug(*args, **kwargs) -> None:
    """print() when debug output is on, otherwise nothing."""
    if ENABLED:
        print(*args, **kwargs)
