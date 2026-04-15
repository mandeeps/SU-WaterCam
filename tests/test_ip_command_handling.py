"""Unit tests for IP downlink command dispatch.

Tests ``tools.transmit_ip.apply_downlink_command`` in isolation — no server,
no runtime config file, no hardware.  A simple dict accumulator is passed as
``set_param_fn`` so every test can inspect exactly what parameters were written.

Run with:
    python tests/test_ip_command_handling.py
or:
    pytest tests/test_ip_command_handling.py -v
"""

from __future__ import annotations

import struct
import sys
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.transmit_ip import apply_downlink_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _accumulator():
    """Return (store dict, set_param callable) pair for capturing applied params."""
    store: dict = {}

    def _set(key, value):
        store[key] = value

    return store, _set


def _cmd(*parts: tuple[str, bytes]) -> dict:
    """Build a minimal command dict from (code, payload_bytes) tuples."""
    return {
        "queue_id": "test-qid-001",
        "hex_payload": "",
        "parts": [
            {"code": code, "payload_hex": payload.hex()}
            for code, payload in parts
        ],
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestAreaThreshold(unittest.TestCase):
    """10 90 — area_threshold_pct: direct u8 value."""

    def test_applies_value(self):
        store, fn = _accumulator()
        result = apply_downlink_command(_cmd(("10 90", struct.pack("B", 25))), fn)
        self.assertEqual(store["area_threshold"], 25)
        self.assertIn("area_threshold=25%", result["applied"])
        self.assertEqual(result["skipped"], [])

    def test_zero_value(self):
        store, fn = _accumulator()
        apply_downlink_command(_cmd(("10 90", struct.pack("B", 0))), fn)
        self.assertEqual(store["area_threshold"], 0)

    def test_max_value(self):
        store, fn = _accumulator()
        apply_downlink_command(_cmd(("10 90", struct.pack("B", 255))), fn)
        self.assertEqual(store["area_threshold"], 255)

    def test_wrong_payload_length_skipped(self):
        store, fn = _accumulator()
        result = apply_downlink_command(_cmd(("10 90", b"\x00\x00")), fn)  # 2 bytes, expected 1
        self.assertNotIn("area_threshold", store)
        self.assertIn("10 90", result["skipped"])


class TestStageThreshold(unittest.TestCase):
    """11 91 — stage_threshold_cm: u16 big-endian."""

    def test_applies_value(self):
        store, fn = _accumulator()
        result = apply_downlink_command(_cmd(("11 91", struct.pack(">H", 150))), fn)
        self.assertEqual(store["stage_threshold"], 150)
        self.assertIn("stage_threshold=150cm", result["applied"])

    def test_large_value(self):
        store, fn = _accumulator()
        apply_downlink_command(_cmd(("11 91", struct.pack(">H", 65535))), fn)
        self.assertEqual(store["stage_threshold"], 65535)

    def test_wrong_payload_length_skipped(self):
        store, fn = _accumulator()
        result = apply_downlink_command(_cmd(("11 91", b"\x00")), fn)  # 1 byte, expected 2
        self.assertNotIn("stage_threshold", store)
        self.assertIn("11 91", result["skipped"])


class TestMonitoringFrequency(unittest.TestCase):
    """12 92 — monitoring_freq_h: u8 index into [1, 3, 6, 24, 72]."""

    _MF_HOURS = [1, 3, 6, 24, 72]

    def test_each_valid_index(self):
        for idx, hours in enumerate(self._MF_HOURS):
            with self.subTest(idx=idx, hours=hours):
                store, fn = _accumulator()
                apply_downlink_command(_cmd(("12 92", struct.pack("B", idx))), fn)
                self.assertEqual(store["monitoring_frequency"], hours * 60)

    def test_out_of_range_index_skipped(self):
        store, fn = _accumulator()
        result = apply_downlink_command(_cmd(("12 92", struct.pack("B", 99))), fn)
        self.assertNotIn("monitoring_frequency", store)
        self.assertIn("12 92", result["skipped"])

    def test_applied_label_uses_hours(self):
        store, fn = _accumulator()
        result = apply_downlink_command(_cmd(("12 92", struct.pack("B", 2))), fn)  # 6h
        self.assertIn("monitoring_frequency=6h", result["applied"])


class TestEmergencyFrequency(unittest.TestCase):
    """13 93 — emergency_freq_min: u8 index into [2, 5, 10]."""

    _EF_MIN = [2, 5, 10]

    def test_each_valid_index(self):
        for idx, mins in enumerate(self._EF_MIN):
            with self.subTest(idx=idx, mins=mins):
                store, fn = _accumulator()
                apply_downlink_command(_cmd(("13 93", struct.pack("B", idx))), fn)
                self.assertEqual(store["emergency_frequency"], mins)

    def test_out_of_range_index_skipped(self):
        store, fn = _accumulator()
        result = apply_downlink_command(_cmd(("13 93", struct.pack("B", 5))), fn)
        self.assertNotIn("emergency_frequency", store)
        self.assertIn("13 93", result["skipped"])


class TestFloodCodeFrequency(unittest.TestCase):
    """14 94 — flood_code_freq_min: u8 index into [10, 20, 30, 40, 50, 60]."""

    _FF_MIN = [10, 20, 30, 40, 50, 60]

    def test_each_valid_index(self):
        for idx, mins in enumerate(self._FF_MIN):
            with self.subTest(idx=idx, mins=mins):
                store, fn = _accumulator()
                apply_downlink_command(_cmd(("14 94", struct.pack("B", idx))), fn)
                self.assertEqual(store["neighborhood_emergency_frequency"], mins)

    def test_out_of_range_index_skipped(self):
        store, fn = _accumulator()
        result = apply_downlink_command(_cmd(("14 94", struct.pack("B", 10))), fn)
        self.assertNotIn("neighborhood_emergency_frequency", store)
        self.assertIn("14 94", result["skipped"])


class TestMultiPartCommand(unittest.TestCase):
    """A single command dict may carry multiple parts."""

    def test_two_parts_both_applied(self):
        store, fn = _accumulator()
        cmd = _cmd(
            ("10 90", struct.pack("B", 30)),
            ("11 91", struct.pack(">H", 200)),
        )
        result = apply_downlink_command(cmd, fn)
        self.assertEqual(store["area_threshold"], 30)
        self.assertEqual(store["stage_threshold"], 200)
        self.assertEqual(len(result["applied"]), 2)
        self.assertEqual(result["skipped"], [])

    def test_mixed_valid_and_unknown(self):
        store, fn = _accumulator()
        cmd = {
            "queue_id": "q2",
            "parts": [
                {"code": "10 90", "payload_hex": struct.pack("B", 15).hex()},
                {"code": "FF FF", "payload_hex": "deadbeef"},
            ],
        }
        result = apply_downlink_command(cmd, fn)
        self.assertEqual(store["area_threshold"], 15)
        self.assertEqual(len(result["applied"]), 1)
        self.assertIn("FF FF", result["skipped"])


class TestMalformedInput(unittest.TestCase):
    """Edge cases: bad payloads, missing fields, wrong types."""

    def test_empty_parts_list(self):
        store, fn = _accumulator()
        result = apply_downlink_command({"queue_id": "q", "parts": []}, fn)
        self.assertEqual(store, {})
        self.assertEqual(result["applied"], [])
        self.assertEqual(result["skipped"], [])

    def test_missing_parts_field(self):
        store, fn = _accumulator()
        result = apply_downlink_command({"queue_id": "q"}, fn)
        self.assertEqual(result["applied"], [])

    def test_parts_not_a_list(self):
        store, fn = _accumulator()
        result = apply_downlink_command({"queue_id": "q", "parts": "bad"}, fn)
        self.assertEqual(result["applied"], [])

    def test_part_not_a_dict(self):
        store, fn = _accumulator()
        result = apply_downlink_command({"queue_id": "q", "parts": ["notadict"]}, fn)
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_invalid_hex_payload(self):
        store, fn = _accumulator()
        result = apply_downlink_command(
            {"queue_id": "q", "parts": [{"code": "10 90", "payload_hex": "ZZ"}]},
            fn,
        )
        self.assertNotIn("area_threshold", store)
        self.assertIn("10 90", result["skipped"])

    def test_queue_id_preserved_in_result(self):
        _, fn = _accumulator()
        result = apply_downlink_command({"queue_id": "abc-123", "parts": []}, fn)
        self.assertEqual(result["queue_id"], "abc-123")

    def test_queue_id_none_when_absent(self):
        _, fn = _accumulator()
        result = apply_downlink_command({"parts": []}, fn)
        self.assertIsNone(result["queue_id"])

    def test_set_param_called_once_per_valid_part(self):
        calls = []
        result = apply_downlink_command(
            _cmd(
                ("10 90", struct.pack("B", 5)),
                ("13 93", struct.pack("B", 1)),
            ),
            lambda k, v: calls.append((k, v)),
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], ("area_threshold", 5))
        self.assertEqual(calls[1], ("emergency_frequency", 5))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
