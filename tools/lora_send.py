"""One wake's LoRa sends, stopping early when the radio has stalled.

A send that hits the daemon's deadline (TRANSMIT_DEADLINE_S in
tools/lora_handler_concurrent.py) or an unresponsive daemon takes 75-90 s, and the
next one would too. After the first such failure, send() refuses the rest of the
wake without trying, so the caller queues them (tools/lora_store_forward.py)
instead of burning the awake window. Any failed send also drops the cached LoRa
health result (tools/lora_health.py), so the next check looks at the link again.
"""
import time

from tools import lora_health

try:
    from tools.lora_handler_concurrent import TRANSMIT_DEADLINE_S
except ImportError:     # the handler's own imports aren't available
    TRANSMIT_DEADLINE_S = 75

# A failed send that took this long (or longer) hit the deadline, not a quick refusal
STALL_AFTER_S = TRANSMIT_DEADLINE_S - 5


class WakeSender:
    """Wraps a LoRa handler for one wake; has the transmit() the drain loop calls."""

    def __init__(self, handler):
        self.handler = handler
        self.stalled = False

    def transmit(self, payload_hex) -> bool:
        if self.stalled:
            print("⚠️ LoRa stalled earlier this wake: not trying again")
            return False
        started = time.monotonic()
        try:
            ok = bool(self.handler.transmit(payload_hex))
        except Exception as e:
            print(f"⚠️ LoRa send raised: {e}")
            ok = False
        if not ok:
            lora_health.invalidate()
            if time.monotonic() - started >= STALL_AFTER_S:
                self.stalled = True
                print("⚠️ LoRa send ran into the deadline: the rest of this wake is queued, not sent")
        return ok

    def __getattr__(self, name):
        return getattr(self.handler, name)
