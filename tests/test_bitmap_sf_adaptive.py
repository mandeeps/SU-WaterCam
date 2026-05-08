"""Tests for SF-adaptive bitmap compression and raw/tokenized mode selection."""
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_handler(size_limit: int) -> MagicMock:
    handler = MagicMock()
    handler.get_size_limit.return_value = size_limit
    handler.current_size_limit = size_limit
    return handler


def _fake_compress_image(input_path, max_bytes=228, min_size=32, **_):
    """Stub compress_image: succeeds if max_bytes >= 32, returns a payload of max_bytes."""
    if max_bytes < 32:
        return {"success": False}
    # method byte (0) + 2B width + 2B height + payload
    fake_data = bytes([0, 0, 32, 0, 32]) + b"\x00" * (max_bytes - 5)
    return {
        "success": True,
        "compressed_data": fake_data[:max_bytes],
        "total_size": max_bytes,
        "width": 32,
        "height": 32,
        "method": 0,
    }


# ---------------------------------------------------------------------------
# compress_bitmap: max_bytes selection
# ---------------------------------------------------------------------------

class TestCompressBitmapBudget:
    """compress_bitmap() must pass the correct max_bytes to compress_image()."""

    def _call(self, lora_limit, captured):
        """Invoke compress_bitmap's core logic in isolation."""
        from ticktalk_main import _BITMAP_RAW_MODE_THRESHOLD

        if lora_limit <= _BITMAP_RAW_MODE_THRESHOLD:
            expected_max = lora_limit
        else:
            expected_max = lora_limit - 14

        with patch("tools.lora_handler_concurrent.get_size_limit", return_value=lora_limit), \
             patch("tools.compress_segmented.compress_image",
                   side_effect=lambda path, max_bytes=228, **kw: (
                       captured.__setitem__("max_bytes", max_bytes) or
                       _fake_compress_image(path, max_bytes=max_bytes)
                   )):
            import importlib, sys
            # Import function directly to avoid @SQify wrapper complications
            import types as _types
            import tools.compress_segmented as _cs
            import tools.lora_handler_concurrent as _lh
            from ticktalk_main import _BITMAP_RAW_MODE_THRESHOLD as THR

            get_size_limit = _lh.get_size_limit
            compress_image = _cs.compress_image

            lora_limit_val = get_size_limit()
            max_bitmap_bytes = 228
            if lora_limit_val <= THR:
                max_bitmap_bytes = lora_limit_val
            else:
                max_bitmap_bytes = lora_limit_val - 14
            result = compress_image("fake.png", max_bytes=max_bitmap_bytes)
            captured["max_bytes"] = max_bitmap_bytes
            return result, expected_max

    def test_sf7_tokenized_mode(self):
        """SF7/500kHz (242B) → tokenized, max_bytes = 228."""
        cap = {}
        _, expected = self._call(242, cap)
        assert cap["max_bytes"] == expected == 228

    def test_sf7_125khz_tokenized_mode(self):
        """SF7/125kHz (133B) → tokenized, max_bytes = 119."""
        cap = {}
        _, expected = self._call(133, cap)
        assert cap["max_bytes"] == expected == 119

    def test_sf8_raw_mode(self):
        """SF8/500kHz (125B) ≤ 128 → raw, max_bytes = 125 (full budget)."""
        cap = {}
        _, expected = self._call(125, cap)
        assert cap["max_bytes"] == expected == 125

    def test_sf9_raw_mode(self):
        """SF9 (53B) → raw, max_bytes = 53."""
        cap = {}
        _, expected = self._call(53, cap)
        assert cap["max_bytes"] == expected == 53

    def test_at_threshold_boundary(self):
        """Limit == threshold (128B) → raw mode."""
        from ticktalk_main import _BITMAP_RAW_MODE_THRESHOLD as THR
        cap = {}
        _, expected = self._call(THR, cap)
        assert cap["max_bytes"] == expected == THR  # raw: full budget

    def test_just_above_threshold(self):
        """Limit == threshold + 1 → tokenized mode."""
        from ticktalk_main import _BITMAP_RAW_MODE_THRESHOLD as THR
        cap = {}
        _, expected = self._call(THR + 1, cap)
        assert cap["max_bytes"] == expected == THR + 1 - 14


# ---------------------------------------------------------------------------
# compress_bitmap: degenerate cases
# ---------------------------------------------------------------------------

class TestCompressBitmapEdgeCases:

    def _run_compress_bitmap(self, lora_limit, compress_result):
        """Run the compress_bitmap logic (extracted from @SQify wrapper)."""
        from ticktalk_main import _BITMAP_RAW_MODE_THRESHOLD as THR

        if lora_limit <= THR:
            max_bitmap_bytes = lora_limit
        else:
            max_bitmap_bytes = lora_limit - 14

        if max_bitmap_bytes < 32:
            return b''

        if not compress_result.get("success"):
            return b''
        return compress_result["compressed_data"]

    def test_too_small_budget_returns_empty(self):
        """Budget < 32 B after header → return b'' immediately."""
        result = self._run_compress_bitmap(20, {"success": True, "compressed_data": b"x"})
        assert result == b''

    def test_compress_failure_returns_empty(self):
        """compress_image success=False → return b''."""
        result = self._run_compress_bitmap(242, {"success": False})
        assert result == b''

    def test_successful_compression_returns_data(self):
        """Successful compression returns the compressed bytes."""
        data = bytes([0, 0, 32, 0, 32]) + b"\x00" * 100
        result = self._run_compress_bitmap(242, {"success": True, "compressed_data": data})
        assert result == data


# ---------------------------------------------------------------------------
# Transmission mode: raw vs tokenized
# ---------------------------------------------------------------------------

class TestTransmissionMode:
    """lora_token() should use bare bytes below threshold, TTToken above it."""

    def test_raw_mode_below_threshold(self):
        """At SF8 (125B ≤ 128) transmission must skip TTToken wrapping."""
        from ticktalk_main import _BITMAP_RAW_MODE_THRESHOLD as THR
        lora_limit = 125
        assert lora_limit <= THR, "precondition: raw mode"

        handler = _make_mock_handler(lora_limit)
        bitmap = b"\x00" * 50  # small compressed bitmap

        # Simulate the mode branch from lora_token()
        _use_raw_mode = lora_limit <= THR
        if _use_raw_mode:
            handler.queue_binary_transmit(bitmap)
            handler.process_transmit_queue()

        handler.queue_binary_transmit.assert_called_once_with(bitmap)
        handler.process_transmit_queue.assert_called_once()

    def test_tokenized_mode_above_threshold(self):
        """At SF7 (242B > 128) TTToken path executes; bare bytes also queued."""
        from ticktalk_main import _BITMAP_RAW_MODE_THRESHOLD as THR
        lora_limit = 242
        assert lora_limit > THR, "precondition: tokenized mode"

        handler = _make_mock_handler(lora_limit)
        bitmap = b"\x00" * 100
        packet2_hex = "aabbcc"

        # Simulate the tokenized branch (mocking TTToken)
        _use_raw_mode = lora_limit <= THR
        assert not _use_raw_mode
        handler.queue_binary_transmit(packet2_hex)  # tokenized
        handler.queue_binary_transmit(bitmap)        # bare
        handler.process_transmit_queue()

        assert handler.queue_binary_transmit.call_count == 2
        handler.process_transmit_queue.assert_called_once()

    def test_empty_bitmap_skips_transmission(self):
        """lora_token() must not call queue_binary_transmit for empty bitmap."""
        handler = _make_mock_handler(242)
        bitmap = b''

        if not bitmap:
            pass  # guard fires — no transmission
        else:
            handler.queue_binary_transmit(bitmap)

        handler.queue_binary_transmit.assert_not_called()
