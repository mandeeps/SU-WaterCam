"""Tests for LoRaWAN MAC command queue flush logic.

Covers:
- _flush_mac_commands() returns True/False on OK/ERROR
- _attempt_transmission promotes 'Tx buffer filled' to error_message
- transmit() calls _flush_mac_commands() on MAC error, then retries
"""

import pytest
from unittest.mock import patch, MagicMock, call


# ── helpers ──────────────────────────────────────────────────────────────────

def _serial_lines(*lines):
    """Build a read_until side_effect that yields each line then empty bytes."""
    encoded = [line.encode() + b'\r\n' for line in lines] + [b''] * 20
    return encoded


def _make_handler():
    import tools.lora_handler_concurrent as lhc
    return lhc.LoRaHandler()


# ── _flush_mac_commands ───────────────────────────────────────────────────────

class TestFlushMacCommands:
    def test_returns_true_on_ok(self, _mock_serial_port):
        handler = _make_handler()
        mock_ser = _mock_serial_port
        mock_ser.in_waiting = 1
        mock_ser.read_until.side_effect = _serial_lines(
            'AT+SENDB=00',   # echo
            'OK',
        )
        assert handler._flush_mac_commands() is True

    def test_returns_false_on_error(self, _mock_serial_port):
        handler = _make_handler()
        mock_ser = _mock_serial_port
        mock_ser.in_waiting = 1
        mock_ser.read_until.side_effect = _serial_lines(
            'AT+SENDB=00',
            'ERROR',
        )
        assert handler._flush_mac_commands() is False

    def test_returns_false_on_exception(self, _mock_serial_port):
        handler = _make_handler()
        _mock_serial_port.write.side_effect = OSError("serial gone")
        assert handler._flush_mac_commands() is False

    def test_sends_one_byte_probe(self, _mock_serial_port):
        handler = _make_handler()
        mock_ser = _mock_serial_port
        mock_ser.in_waiting = 1
        mock_ser.read_until.side_effect = _serial_lines('OK')
        handler._flush_mac_commands()
        mock_ser.write.assert_called_with(b'AT+SENDB=00\r\n')


# ── MAC error promotion in _attempt_transmission ─────────────────────────────

class TestMacErrorPromotion:
    def test_mac_buffer_error_in_responses_sets_specific_error_message(self, _mock_serial_port):
        """When final_responses contains 'Tx buffer filled', error_message must reflect it."""
        handler = _make_handler()
        mock_ser = _mock_serial_port
        mock_ser.in_waiting = 1
        mock_ser.read_until.side_effect = _serial_lines(
            'AT+SENDB=ab',
            'Tx buffer filled by MAC Commands, send data again',
            'ERROR',
        )
        success, error_message = handler._attempt_transmission(b'\xab', size_limit=242)
        assert not success
        assert 'Tx buffer filled by MAC Commands' in error_message

    def test_normal_error_unaffected(self, _mock_serial_port):
        """A plain ERROR response must not be confused with the MAC buffer error."""
        handler = _make_handler()
        mock_ser = _mock_serial_port
        mock_ser.in_waiting = 1
        mock_ser.read_until.side_effect = _serial_lines(
            'AT+SENDB=ab',
            'ERROR',
        )
        success, error_message = handler._attempt_transmission(b'\xab', size_limit=242)
        assert not success
        assert 'Tx buffer filled' not in error_message


# ── transmit() calls flush on MAC error ──────────────────────────────────────

class TestTransmitMacFlush:
    def test_flush_called_on_mac_error_then_succeeds(self, _mock_serial_port):
        """transmit() must call _flush_mac_commands on MAC error, then retry and succeed."""
        import tools.lora_handler_concurrent as lhc
        handler = _make_handler()

        mac_error_msg = "mDot: Tx buffer filled by MAC Commands — Tx buffer filled by MAC Commands, send data again"
        attempt_results = [
            (False, mac_error_msg),
            (True, ""),
        ]

        flush_called = []

        def fake_flush():
            flush_called.append(True)
            return True

        with patch.object(handler, '_attempt_transmission', side_effect=attempt_results), \
             patch.object(handler, '_flush_mac_commands', side_effect=fake_flush), \
             patch.object(handler, '_clear_mdot_input', return_value=True), \
             patch('time.sleep'):
            result = handler.transmit(b'\x00', max_retries=2)

        assert result is True
        assert len(flush_called) == 1, "flush must be called exactly once"

    def test_flush_not_called_on_non_mac_error(self, _mock_serial_port):
        """transmit() must NOT call _flush_mac_commands for ordinary errors."""
        handler = _make_handler()

        attempt_results = [
            (False, "mDot reported error: ERROR"),
            (False, "mDot reported error: ERROR"),
            (False, "mDot reported error: ERROR"),
        ]

        flush_called = []

        with patch.object(handler, '_attempt_transmission', side_effect=attempt_results), \
             patch.object(handler, '_flush_mac_commands', side_effect=lambda: flush_called.append(True) or False), \
             patch.object(handler, '_clear_mdot_input', return_value=True), \
             patch('time.sleep'):
            result = handler.transmit(b'\x00', max_retries=2)

        assert result is False
        assert len(flush_called) == 0, "flush must not be called for non-MAC errors"

    def test_flush_loops_until_clean_drain_within_one_round_cap(self, _mock_serial_port):
        """A single flush may not fully drain the MAC backlog -- transmit()
        must retry _flush_mac_commands() (up to MAC_FLUSH_MAX_ROUNDS) within
        the same retry attempt until it confirms a clean drain, not give up
        after exactly one flush call. Regression for the bitmap transmission
        that kept failing "Tx buffer filled by MAC Commands" across all 3
        top-level attempts despite a flush being attempted between each."""
        import tools.lora_handler_concurrent as lhc
        handler = _make_handler()

        mac_error_msg = "mDot: Tx buffer filled by MAC Commands — Tx buffer filled by MAC Commands, send data again"
        attempt_results = [
            (False, mac_error_msg),
            (True, ""),
        ]

        flush_results = [False, False, True]  # dirty, dirty, clean
        flush_calls = []

        def fake_flush():
            flush_calls.append(True)
            return flush_results[len(flush_calls) - 1]

        with patch.object(handler, '_attempt_transmission', side_effect=attempt_results), \
             patch.object(handler, '_flush_mac_commands', side_effect=fake_flush), \
             patch.object(handler, '_clear_mdot_input', return_value=True), \
             patch('time.sleep'):
            result = handler.transmit(b'\x00', max_retries=2)

        assert result is True
        assert len(flush_calls) == 3 == lhc.MAC_FLUSH_MAX_ROUNDS

    def test_flush_gives_up_after_max_rounds_but_still_retries_payload(self, _mock_serial_port):
        """If the flush never confirms a clean drain within MAC_FLUSH_MAX_ROUNDS,
        transmit() must still proceed to retry the real payload afterward
        (rather than aborting) -- the retry may still succeed even without a
        confirmed-clean flush."""
        import tools.lora_handler_concurrent as lhc
        handler = _make_handler()

        mac_error_msg = "mDot: Tx buffer filled by MAC Commands — Tx buffer filled by MAC Commands, send data again"
        attempt_results = [
            (False, mac_error_msg),
            (True, ""),
        ]

        flush_calls = []

        def fake_flush():
            flush_calls.append(True)
            return False  # never confirms a clean drain

        with patch.object(handler, '_attempt_transmission', side_effect=attempt_results), \
             patch.object(handler, '_flush_mac_commands', side_effect=fake_flush), \
             patch.object(handler, '_clear_mdot_input', return_value=True), \
             patch('time.sleep'):
            result = handler.transmit(b'\x00', max_retries=2)

        assert result is True
        assert len(flush_calls) == lhc.MAC_FLUSH_MAX_ROUNDS
