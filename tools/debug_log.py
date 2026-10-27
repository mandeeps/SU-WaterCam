"""Optional debug output, off on production units.

On when `"debug_logs": true` is in runtime_config.json (the unit's own config in
the app directory, kept across software updates, so test and problem units keep
their debug lines) or WATERCAM_DEBUG=1 is set in the service's environment.
Read once per process: a change applies at the next start.
"""
import json
import os

RUNTIME_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "runtime_config.json")


def _enabled(path: str = RUNTIME_CONFIG) -> bool:
    if os.environ.get("WATERCAM_DEBUG") == "1":
        return True
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("debug_logs") is True
    except (OSError, ValueError, AttributeError):
        return False


ENABLED = _enabled()


def debug(*args, **kwargs) -> None:
    """print() when debug output is on, otherwise nothing."""
    if ENABLED:
        print(*args, **kwargs)
