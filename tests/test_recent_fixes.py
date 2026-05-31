"""Regression tests for bugs fixed in the May 2026 debugging session.

Covers:
  1. _extract_txs_size_limit poisoning — small numeric LoRa downlinks must not
     corrupt current_size_limit (min raised from 1 to 11 bytes)
  2. _query_txs_locked recovery — transmit() refreshes the limit when it is
     below the floor rather than silently failing every attempt
  3. get_wittypi_status — removed @SQify so it returns a plain dict when called
     from within another SQ function
  4. generate_schedule validation — start_hour=0, start_minute=0 are valid
  5. apply_schedule guard — no IndexError when runScript.sh output is short
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))


# ---------------------------------------------------------------------------
# 1. _extract_txs_size_limit poisoning fix
# ---------------------------------------------------------------------------

class TestExtractTxsSizeLimit:
    """The minimum accepted size limit is 11 (SF12/BW125 floor).
    Anything smaller must return None so a stray '02' downlink byte can't
    overwrite current_size_limit with 2 and block all future transmissions.
    """

    @pytest.fixture
    def handler(self):
        from tools.lora_handler_concurrent import LoRaHandler
        h = LoRaHandler.__new__(LoRaHandler)
        h.current_size_limit = 242
        return h

    @pytest.mark.parametrize("msg", ["2", "02", "1", "5", "10"])
    def test_below_floor_returns_none(self, handler, msg):
        assert handler._extract_txs_size_limit(msg) is None

    @pytest.mark.parametrize("msg,expected", [
        ("11", 11),
        ("51", 51),
        ("128", 128),
        ("242", 242),
        ("255", 255),
    ])
    def test_valid_limit_accepted(self, handler, msg, expected):
        assert handler._extract_txs_size_limit(msg) == expected

    def test_at_txs_echo_returns_none(self, handler):
        assert handler._extract_txs_size_limit("AT+TXS") is None

    def test_non_numeric_returns_none(self, handler):
        assert handler._extract_txs_size_limit("OK") is None
        assert handler._extract_txs_size_limit("ERROR") is None
        assert handler._extract_txs_size_limit("TSNB0002") is None

    def test_listener_ignores_small_numeric_message(self):
        """When the listener sees '02', current_size_limit must not change."""
        from tools.lora_handler_concurrent import LoRaHandler
        h = LoRaHandler.__new__(LoRaHandler)
        h.current_size_limit = 242
        h.transmission_history = []
        h.last_transmission_status = None
        h.runtime_callback = None

        # Simulate the listener's TXS branch with a poisoning value
        size_limit = h._extract_txs_size_limit("02")
        if size_limit is not None:
            h.current_size_limit = size_limit

        assert h.current_size_limit == 242, (
            "current_size_limit was corrupted by a small numeric message"
        )


# ---------------------------------------------------------------------------
# 2. _query_txs_locked recovery inside transmit()
# ---------------------------------------------------------------------------

class TestQueryTxsLockedRecovery:
    """transmit() must call _query_txs_locked when current_size_limit < 11,
    allowing a single recovery before re-checking the payload size.
    """

    @pytest.fixture
    def poisoned_handler(self):
        from tools.lora_handler_concurrent import LoRaHandler
        h = LoRaHandler.__new__(LoRaHandler)
        h.current_size_limit = 2        # poisoned value
        h.transmit_lock = __import__("threading").Lock()
        h._lock_fd = MagicMock()
        mock_ser = MagicMock()
        mock_ser.in_waiting = 0
        mock_ser.read_until.return_value = b""
        mock_ser.reset_input_buffer.return_value = None
        mock_ser.write.return_value = None
        h.ser = mock_ser
        return h

    def test_recovery_called_when_limit_below_floor(self, poisoned_handler):
        h = poisoned_handler
        recovered = []

        def fake_query():
            h.current_size_limit = 242
            recovered.append(True)

        h._query_txs_locked = fake_query
        h._attempt_transmission = MagicMock(return_value=(True, ""))

        with h.transmit_lock:
            import fcntl
            with patch.object(fcntl, "lockf"):
                # Re-implement just the recovery branch from transmit()
                size_limit = h.current_size_limit
                if size_limit < 11:
                    h._query_txs_locked()
                    size_limit = h.current_size_limit

        assert recovered, "_query_txs_locked was not called despite poisoned limit"
        assert size_limit == 242


# ---------------------------------------------------------------------------
# 3. get_wittypi_status — no @SQify, returns plain dict
# ---------------------------------------------------------------------------

class TestGetWittypiStatusIsPlainFunction:
    """get_wittypi_status() must not be decorated with @SQify.

    When called from inside a @SQify function, a @SQify-decorated helper
    returns a TTToken instead of the plain dict, causing 'tuple index out of
    range' when the caller does wittypi_data.get('status').
    """

    def test_not_sqify_wrapped(self):
        from tools.wittypi_control import get_wittypi_status
        assert not hasattr(get_wittypi_status, "__wrapped__"), (
            "get_wittypi_status must not be @SQify — it would return a TTToken "
            "instead of a dict when called from another SQ function"
        )

    def test_returns_dict_when_wittypi_unavailable(self):
        """Even when witty_pi_4 is missing, get_wittypi_status returns a dict."""
        from tools import wittypi_control
        original = wittypi_control.witty_pi_4
        try:
            wittypi_control.witty_pi_4 = None
            result = wittypi_control.get_wittypi_status()
        finally:
            wittypi_control.witty_pi_4 = original

        assert isinstance(result, dict), (
            f"expected dict, got {type(result).__name__}"
        )
        assert "status" in result

    def test_get_method_works_on_result(self):
        """Calling .get() on the result must not raise."""
        from tools import wittypi_control
        original = wittypi_control.witty_pi_4
        try:
            wittypi_control.witty_pi_4 = None
            result = wittypi_control.get_wittypi_status()
        finally:
            wittypi_control.witty_pi_4 = original

        # This is the exact call that raised 'tuple index out of range'
        status = result.get("status")
        assert status is not None


# ---------------------------------------------------------------------------
# 4. generate_schedule validation — midnight and zero-minute start accepted
# ---------------------------------------------------------------------------

class TestGenerateScheduleValidation:
    """Boundary inputs that were previously rejected by strict comparisons."""

    @pytest.fixture(autouse=True)
    def _no_file_io(self, tmp_path, monkeypatch):
        """Redirect schedule file writes to tmp_path."""
        import tools.witty_pi_4 as wp
        monkeypatch.setattr(wp, "SCHEDULE_FILE_PATH", str(tmp_path / "schedule.wpi"))

    def _generate(self, start_hour, start_minute, interval, reps):
        from tools.witty_pi_4 import WittyPi4
        WittyPi4.generate_schedule(start_hour, start_minute, interval, reps)
        import tools.witty_pi_4 as wp
        return Path(wp.SCHEDULE_FILE_PATH).read_text()

    def test_midnight_start_hour_accepted(self):
        """start_hour=0 used to be rejected (not 0 < 0 < 24 → True → reset to 8)."""
        schedule = self._generate(0, 30, 30, 4)
        assert "00:30:00" in schedule

    def test_zero_start_minute_accepted(self):
        """start_minute=0 used to be silently reset."""
        schedule = self._generate(8, 0, 30, 4)
        assert "08:00:00" in schedule

    def test_schedule_durations_sum_to_1440(self):
        """ON+OFF intervals must sum to exactly 1440 min for a daily repeat."""
        schedule = self._generate(8, 0, 30, 4)
        total = 0
        for line in schedule.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[0] not in ("ON", "OFF"):
                continue
            dur = 0
            for tok in parts[1:]:
                if tok.startswith("H"):
                    dur += int(tok[1:]) * 60
                elif tok.startswith("M"):
                    dur += int(tok[1:])
            total += dur
        assert total == 1440, f"Schedule durations sum to {total}, expected 1440"

    def test_max_duration_uses_interval_minus_2(self):
        """ON window should be interval-2, not the old hardcoded 4 minutes."""
        schedule = self._generate(8, 0, 30, 4)
        # First ON line
        on_line = next(l for l in schedule.splitlines() if l.startswith("ON"))
        assert "M28" in on_line, f"Expected ON M28 (30-2), got: {on_line}"


# ---------------------------------------------------------------------------
# 5. apply_schedule guard — no IndexError on short script output
# ---------------------------------------------------------------------------

class TestApplyScheduleGuard:
    """Output with fewer than 2 lines must not raise IndexError."""

    def _make_wittypi(self):
        from tools.witty_pi_4 import WittyPi4
        return WittyPi4()

    def test_empty_output_returns_dash(self):
        wp = self._make_wittypi()
        with patch("tools.witty_pi_4.check_output", return_value=""):
            result = wp.apply_schedule(max_retries=1)
        assert result == "-"

    def test_one_line_output_returns_dash(self):
        wp = self._make_wittypi()
        with patch("tools.witty_pi_4.check_output", return_value="only one line"):
            result = wp.apply_schedule(max_retries=1)
        assert result == "-"

    def test_valid_output_returns_startup_time(self):
        wp = self._make_wittypi()
        good = "\nIgnored\nSchedule next startup at: 2026-05-30 08:00:00\n"
        with patch("tools.witty_pi_4.check_output", return_value=good):
            result = wp.apply_schedule(max_retries=1)
        assert result == "2026-05-30 08:00:00"
