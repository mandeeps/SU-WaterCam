"""Tests for SF-adaptive bitmap compression and raw/tokenized mode selection.

All tests call the real production functions via __wrapped__ (SQify uses
functools.wraps so __wrapped__ points to the original) with hardware
dependencies patched.  This ensures regressions in compress_bitmap() and
lora_token() are caught rather than just testing re-implemented logic.
"""
import contextlib
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _fake_compress_image(path, max_bytes=228, min_size=32, **_):
    """Stub: succeed for max_bytes ≥ 32, return payload exactly max_bytes long."""
    if max_bytes < 32:
        return {"success": False}
    data = bytes([0, 0, 32, 0, 32]) + b"\x00" * (max_bytes - 5)
    return {
        "success": True,
        "compressed_data": data,
        "total_size": max_bytes,
        "width": 32,
        "height": 32,
        "method": 0,
    }


def _make_lora_handler(size_limit: int) -> MagicMock:
    h = MagicMock()
    h.get_size_limit.return_value = size_limit
    h.current_size_limit = size_limit
    h.queue_binary_transmit.return_value = True
    h.process_transmit_queue.return_value = None
    h.queue_transmit.return_value = True
    return h


# ---------------------------------------------------------------------------
# compress_bitmap tests — call the real __wrapped__ function
# ---------------------------------------------------------------------------

class TestCompressBitmapBudget:
    """compress_bitmap().__wrapped__ must pass the right max_bytes to compress_image."""

    def _run(self, lora_limit):
        import ticktalk_main
        captured = {}

        def capturing_compress(path, max_bytes=228, **kw):
            captured["max_bytes"] = max_bytes
            return _fake_compress_image(path, max_bytes=max_bytes)

        with patch("tools.lora_handler_concurrent.get_size_limit", return_value=lora_limit), \
             patch("tools.compress_segmented.compress_image", side_effect=capturing_compress):
            data = ticktalk_main.compress_bitmap.__wrapped__("fake.png")

        return data, captured["max_bytes"]

    def test_sf7_500khz_tokenized(self):
        data, max_bytes = self._run(242)
        assert max_bytes == 228
        assert len(data) == 228

    def test_sf7_125khz_tokenized(self):
        data, max_bytes = self._run(133)
        assert max_bytes == 119
        assert len(data) == 119

    def test_sf8_500khz_raw(self):
        """SF8/500kHz (125B ≤ 128) → raw mode → full 125B budget."""
        data, max_bytes = self._run(125)
        assert max_bytes == 125
        assert len(data) == 125

    def test_sf9_raw(self):
        data, max_bytes = self._run(53)
        assert max_bytes == 53

    def test_at_threshold_is_raw(self):
        from ticktalk_main import _BITMAP_RAW_MODE_THRESHOLD as THR
        data, max_bytes = self._run(THR)
        assert max_bytes == THR

    def test_one_above_threshold_is_tokenized(self):
        from ticktalk_main import _BITMAP_RAW_MODE_THRESHOLD as THR
        data, max_bytes = self._run(THR + 1)
        assert max_bytes == THR + 1 - 14


class TestCompressBitmapEdgeCases:

    def test_budget_below_min_returns_empty_without_calling_compress_image(self):
        """Budget < 32B → immediate b'', compress_image never called."""
        import ticktalk_main
        with patch("tools.lora_handler_concurrent.get_size_limit", return_value=20), \
             patch("tools.compress_segmented.compress_image") as mock_ci:
            result = ticktalk_main.compress_bitmap.__wrapped__("fake.png")
        assert result == b""
        mock_ci.assert_not_called()

    def test_compress_failure_returns_empty(self):
        import ticktalk_main
        with patch("tools.lora_handler_concurrent.get_size_limit", return_value=242), \
             patch("tools.compress_segmented.compress_image",
                   return_value={"success": False}):
            result = ticktalk_main.compress_bitmap.__wrapped__("fake.png")
        assert result == b""

    def test_handler_unavailable_falls_back_to_228(self):
        """get_size_limit() raises → fall back to 228B default (tokenized)."""
        import ticktalk_main
        captured = {}

        def capturing_compress(path, max_bytes=228, **kw):
            captured["max_bytes"] = max_bytes
            return _fake_compress_image(path, max_bytes=max_bytes)

        with patch("tools.lora_handler_concurrent.get_size_limit",
                   side_effect=RuntimeError("no mDot")), \
             patch("tools.compress_segmented.compress_image",
                   side_effect=capturing_compress):
            result = ticktalk_main.compress_bitmap.__wrapped__("fake.png")

        assert captured["max_bytes"] == 228
        assert len(result) == 228


# ---------------------------------------------------------------------------
# lora_token bitmap path — call __wrapped__ with all dependencies patched
# ---------------------------------------------------------------------------

def _lora_token_patches(handler):
    """Context-manager stack that patches every import inside lora_token."""
    mock_token = MagicMock()
    mock_lora_msg = MagicMock()
    mock_lora_msg.encode_token.return_value = b"\xde\xad\xbe\xef"

    return [
        patch("tools.lora_handler_concurrent.get_lora_handler", return_value=handler),
        patch("tools.lora_handler_concurrent.get_config_value", return_value=None),
        patch("tools.lora_handler_concurrent.transmit_data", return_value=True),
        patch("tools.lora_handler_concurrent.transmit_binary", return_value=True),
        patch("tools.lora_handler_concurrent.compressed_encoding", return_value=b"\x00" * 8),
        patch("tools.bno055_imu.get_orientation", return_value={}),
        patch("tools.aht20_temperature.get_aht20", return_value={}),
        patch("tools.get_gps.get_location_with_retry", return_value=({}, None)),
        patch("tools.wittypi_control.get_wittypi_status",
              return_value={"status": "unavailable"}),
        patch("tools.battery_manager.get_battery_status",
              return_value={"battery_pct": 80, "battery_source": "test"}),
        patch("tools.lora_runtime_integration.get_parameter", return_value=False),
        patch("ticktalkpython.TTToken.TTToken", return_value=mock_token),
        patch("ticktalkpython.NetworkInterfaceLoRa.TTLoRaMessage",
              return_value=mock_lora_msg),
        patch("pympler.asizeof.asizeof", return_value=100),
    ]


class TestLoraTokenBitmapMode:

    def _invoke(self, bitmap: bytes, lora_limit: int):
        import ticktalk_main
        handler = _make_lora_handler(lora_limit)
        with contextlib.ExitStack() as stack:
            for p in _lora_token_patches(handler):
                stack.enter_context(p)
            ticktalk_main.lora_token.__wrapped__(bitmap)
        return handler

    def test_raw_mode_queues_bare_bytes_only(self):
        """SF8 (125B ≤ threshold): only bare bitmap bytes queued, no TTToken."""
        bitmap = b"\x01" * 50
        handler = self._invoke(bitmap, lora_limit=125)

        queued = [c.args[0] for c in handler.queue_binary_transmit.call_args_list]
        assert bitmap in queued
        # TTToken-encoded would be b"\xde\xad\xbe\xef".hex() — must NOT be present
        assert b"\xde\xad\xbe\xef".hex() not in queued

    def test_tokenized_mode_queues_both(self):
        """SF7 (242B > threshold): TTToken hex AND bare bitmap both queued."""
        bitmap = b"\x01" * 100
        handler = self._invoke(bitmap, lora_limit=242)

        queued = [c.args[0] for c in handler.queue_binary_transmit.call_args_list]
        assert bitmap in queued
        assert b"\xde\xad\xbe\xef".hex() in queued

    def test_empty_bitmap_skips_all_transmission(self):
        """Empty bitmap b'' → lora_token returns without any queue_binary_transmit call for bitmap."""
        handler = self._invoke(b"", lora_limit=242)
        # Sensor data may be transmitted, but the bitmap-specific calls must not appear
        bitmap_calls = [
            c for c in handler.queue_binary_transmit.call_args_list
            if c.args[0] == b""
        ]
        assert bitmap_calls == []
