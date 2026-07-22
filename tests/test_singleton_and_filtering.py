"""Regression tests for the LoRa handler singleton and listener classification fixes.

Covers three areas flagged in Copilot review of PR #81, plus a leaked-FD fix:

1. get_lora_handler() thread-safety — N concurrent callers must produce exactly
   one LoRaHandler instance and the global must not be published until after
   refresh_size_limit() and start_listening() both complete.

2. _is_transmission_response() classification — bare 'OK'/'ERROR' must not match
   (they are AT echo responses, not TX outcomes); real TX status patterns must.

3. A failed construction (flock conflict, or a later step like
   start_listening() raising) must not leak the already-opened serial port --
   otherwise a failed attempt keeps an open FD (and, via the app-level flock,
   the lock itself) alive for as long as the exception's traceback keeps the
   discarded handler referenced, causing later attempts to also spuriously
   conflict. Suspected contributor to LoRa handler conflicts recurring across
   many iterations on UFO010 (2026-07-22).

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
        # Timeout on barrier.wait() so a deadlock fails the test instead of hanging CI.
        barrier = threading.Barrier(n, timeout=5)
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
                t.join(timeout=10)

        # Fail fast if any thread is still alive (deadlock / hang).
        hung = [t for t in threads if t.is_alive()]
        assert not hung, f"{len(hung)} thread(s) did not finish within timeout"
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


class TestGetLoraHandlerRetry:
    """get_lora_handler() retries a flock conflict a few times with a short
    backoff before giving up, since TickTalk's own runtime spawns separate OS
    processes to run graph nodes -- a sibling process racing for the same
    hardware may simply finish and release the port moments later (confirmed
    on UFO010, 2026-07-22, via a live process-tree/lsof capture)."""

    def test_succeeds_on_a_later_attempt_after_conflicts(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(lhc.time, "sleep", lambda s: sleeps.append(s))

        good_handler = MagicMock()
        construction_attempts = {"count": 0}

        def fake_new(*a, **kw):
            construction_attempts["count"] += 1
            if construction_attempts["count"] < 3:
                raise lhc.LoRaSerialPortConflict("busy")
            return good_handler

        monkeypatch.setattr(lhc, "LoRaHandler", fake_new)

        result = lhc.get_lora_handler()

        assert result is good_handler
        assert construction_attempts["count"] == 3
        assert sleeps == [1.0, 1.0]  # one delay between each of the 2 failed attempts

    def test_gives_up_after_max_attempts(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(lhc.time, "sleep", lambda s: sleeps.append(s))

        attempts = {"count": 0}

        def always_conflicts(*a, **kw):
            attempts["count"] += 1
            raise lhc.LoRaSerialPortConflict("busy")

        monkeypatch.setattr(lhc, "LoRaHandler", always_conflicts)

        result = lhc.get_lora_handler()

        assert result is None
        assert attempts["count"] == 3  # max_attempts
        assert sleeps == [1.0, 1.0]  # no sleep after the final failed attempt
        assert lhc._lora_handler is None


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


# ---------------------------------------------------------------------------
# Leaked-FD-on-conflict regression
# ---------------------------------------------------------------------------

class TestConflictDoesNotLeakSerialPort:

    def test_serial_port_closed_when_flock_conflicts(self, monkeypatch):
        """LoRaHandler.__init__ opens the serial port before checking the
        inter-process flock. If the flock check fails, the already-open
        serial port must be closed before raising -- previously it was left
        open, leaking an FD on /dev/ttyAMA5 for every failed attempt."""
        created_sers = []

        def fake_serial(*a, **kw):
            m = MagicMock()
            m.is_open = True
            created_sers.append(m)
            return m

        monkeypatch.setattr(lhc.serial, "Serial", fake_serial)
        monkeypatch.setattr(lhc.fcntl, "flock", MagicMock(side_effect=OSError("locked")))

        with pytest.raises(lhc.LoRaSerialPortConflict):
            lhc.LoRaHandler()

        assert len(created_sers) == 1
        created_sers[0].close.assert_called_once()

    def test_handler_closed_when_a_later_init_step_fails(self, monkeypatch):
        """If LoRaHandler() itself succeeds (flock + serial both acquired)
        but a later step in get_lora_handler() -- e.g. start_listening() --
        raises, the fully-flock-holding handler must be closed rather than
        discarded still holding the real lock."""
        with patch.object(lhc.LoRaHandler, "refresh_size_limit", autospec=True, return_value=True), \
             patch.object(lhc.LoRaHandler, "start_listening", autospec=True,
                          side_effect=RuntimeError("boom")), \
             patch.object(lhc.LoRaHandler, "close", autospec=True) as mock_close:
            with pytest.raises(RuntimeError):
                lhc.get_lora_handler()

        mock_close.assert_called_once()
        assert lhc._lora_handler is None
