"""Regression tests for the LoRa handler singleton and listener classification fixes.

Covers two areas flagged in Copilot review of PR #81:

1. get_lora_handler() thread-safety — N concurrent callers must produce exactly
   one LoRaHandler instance and the global must not be published until after
   refresh_size_limit() and start_listening() both complete.

2. _is_transmission_response() classification — bare 'OK'/'ERROR' must not match
   (they are AT echo responses, not TX outcomes); real TX status patterns must.

Hardware is provided by the autouse _mock_serial_port fixture in conftest.py.
"""
import threading
from unittest.mock import patch, MagicMock
import pytest

import tools.lora_handler_concurrent as lhc


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Clear the module-level singleton before and after every test in this file."""
    lhc._lora_handler = None
    yield
    lhc._lora_handler = None


@pytest.fixture
def handler():
    """Bare LoRaHandler instance — serial patched by conftest, no listener started."""
    return lhc.LoRaHandler()


# ---------------------------------------------------------------------------
# Singleton thread-safety
# ---------------------------------------------------------------------------

class TestGetLoraHandlerSingleton:

    def test_single_construction_under_concurrency(self):
        """20 concurrent threads calling get_lora_handler() must build one instance."""
        n = 20
        barrier = threading.Barrier(n)
        results = []
        errors = []

        with patch.object(lhc.LoRaHandler, 'refresh_size_limit', autospec=True, return_value=True), \
             patch.object(lhc.LoRaHandler, 'start_listening', autospec=True):

            def _call():
                try:
                    barrier.wait()
                    results.append(lhc.get_lora_handler())
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=_call) for _ in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"Thread(s) raised: {errors}"
        assert len(results) == n
        # Every caller must receive the identical object.
        assert all(r is results[0] for r in results), (
            f"Got {len({id(r) for r in results})} distinct instances, expected 1"
        )

    def test_global_published_only_after_full_init(self):
        """_lora_handler must stay None while refresh_size_limit and start_listening run.

        This guards against the race where a fast-path caller on another thread
        receives a partially-initialised handler because the global was written
        before start_listening() returned.
        """
        lora_during_refresh = [object()]  # sentinel — must be replaced
        lora_during_start = [object()]

        def capture_refresh(self_):
            lora_during_refresh[0] = lhc._lora_handler

        def capture_start(self_):
            lora_during_start[0] = lhc._lora_handler

        with patch.object(lhc.LoRaHandler, 'refresh_size_limit', autospec=True,
                          side_effect=capture_refresh), \
             patch.object(lhc.LoRaHandler, 'start_listening', autospec=True,
                          side_effect=capture_start):
            handler = lhc.get_lora_handler()

        assert lora_during_refresh[0] is None, (
            "_lora_handler was set before refresh_size_limit() returned"
        )
        assert lora_during_start[0] is None, (
            "_lora_handler was set before start_listening() returned"
        )
        assert lhc._lora_handler is handler

    def test_second_call_returns_same_instance(self):
        """Calling get_lora_handler() twice returns the cached singleton."""
        with patch.object(lhc.LoRaHandler, 'refresh_size_limit', autospec=True, return_value=True), \
             patch.object(lhc.LoRaHandler, 'start_listening', autospec=True):
            h1 = lhc.get_lora_handler()
            h2 = lhc.get_lora_handler()

        assert h1 is h2


# ---------------------------------------------------------------------------
# _is_transmission_response classification
# ---------------------------------------------------------------------------

class TestTransmissionResponseClassification:
    """
    After removing bare 'OK'/'ERROR' from _is_transmission_response(), those
    strings must fall through to _is_at_response() (which returns True via its
    final fallback) rather than being logged as transmission successes.
    """

    # Bare AT echoes — must NOT be treated as TX responses.
    @pytest.mark.parametrize("msg", ["OK", "ERROR", "ok", "error", "Ok", "Error"])
    def test_bare_at_responses_are_not_tx_responses(self, handler, msg):
        assert not handler._is_transmission_response(msg), (
            f"'{msg}' should not match _is_transmission_response "
            "(it is an AT echo, not a TX outcome)"
        )

    # Real mDot TX status lines — must match.
    @pytest.mark.parametrize("msg", [
        "SENDB: 0",
        "SEND: ok",
        "TX: complete",
        "TRANSMIT: done",
        "PACKET: info",
        "PAYLOAD: 42",
        "TRANSMITTED",
        "SENT",
        "DELIVERED",
        "ACK received",
    ])
    def test_real_tx_patterns_are_tx_responses(self, handler, msg):
        assert handler._is_transmission_response(msg), (
            f"'{msg}' should match _is_transmission_response"
        )

    def test_ok_falls_through_to_at_response(self, handler):
        """'OK' must be classified as an AT response (ignored), not a TX response."""
        assert not handler._is_transmission_response("OK")
        assert handler._is_at_response("OK")

    def test_error_falls_through_to_at_response(self, handler):
        """'ERROR' must be classified as an AT response (ignored), not a TX response."""
        assert not handler._is_transmission_response("ERROR")
        assert handler._is_at_response("ERROR")
