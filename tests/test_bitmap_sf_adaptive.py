"""Tests for bitmap compression budget and TLV transmission path.

compress_bitmap() must pass max_bytes = lora_limit - 4 (TLV overhead) to
compress_image() regardless of SF.  lora_token() must queue the bitmap via
queue_transmit({'flood_bitmap_compressed': bitmap}) so compressed_encoding()
wraps it as 08 18 [2B len] [bytes] — the format the ChirpStack codec expects.

All tests call the real production functions via __wrapped__ (SQify uses
functools.wraps so __wrapped__ points to the original) with hardware
dependencies patched.
"""
import contextlib
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _fake_compress_image(path, max_bytes=238, min_size=32, **_):
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
    """compress_bitmap().__wrapped__ must pass max_bytes = lora_limit - 4."""

    def _run(self, lora_limit):
        import ticktalk_main
        captured = {}

        def capturing_compress(path, max_bytes=238, **kw):
            captured["max_bytes"] = max_bytes
            return _fake_compress_image(path, max_bytes=max_bytes)

        with patch("tools.lora_handler_concurrent.get_size_limit", return_value=lora_limit), \
             patch("tools.compress_segmented.compress_image", side_effect=capturing_compress):
            data = ticktalk_main.compress_bitmap.__wrapped__("fake.png")

        return data, captured["max_bytes"]

    def test_sf7_500khz(self):
        """SF7/500kHz (242B) → bitmap budget = 242 - 4 = 238."""
        data, max_bytes = self._run(242)
        assert max_bytes == 238
        assert len(data) == 238

    def test_sf7_125khz(self):
        """SF7/125kHz (133B) → bitmap budget = 133 - 4 = 129."""
        data, max_bytes = self._run(133)
        assert max_bytes == 129
        assert len(data) == 129

    def test_sf8_500khz(self):
        """SF8/500kHz (125B) → bitmap budget = 125 - 4 = 121."""
        data, max_bytes = self._run(125)
        assert max_bytes == 121
        assert len(data) == 121

    def test_sf9(self):
        """SF9 (53B) → bitmap budget = 53 - 4 = 49."""
        data, max_bytes = self._run(53)
        assert max_bytes == 49

    def test_budget_formula_is_always_limit_minus_4(self):
        """Formula is lora_limit - 4 at every limit, no threshold branching."""
        for limit in (40, 53, 100, 125, 128, 129, 133, 200, 242):
            _, max_bytes = self._run(limit)
            assert max_bytes == limit - 4, f"limit={limit}: expected {limit-4}, got {max_bytes}"


class TestCompressBitmapEdgeCases:

    def test_budget_below_min_returns_empty_without_calling_compress_image(self):
        """Budget < 32B → immediate b'', compress_image never called."""
        import ticktalk_main
        # lora_limit=35 → max_bitmap_bytes=31 < 32 → skip
        with patch("tools.lora_handler_concurrent.get_size_limit", return_value=35), \
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

    def test_handler_unavailable_falls_back_to_238(self):
        """get_size_limit() raises → fall back to 238B default (242 - 4)."""
        import ticktalk_main
        captured = {}

        def capturing_compress(path, max_bytes=238, **kw):
            captured["max_bytes"] = max_bytes
            return _fake_compress_image(path, max_bytes=max_bytes)

        with patch("tools.lora_handler_concurrent.get_size_limit",
                   side_effect=RuntimeError("no mDot")), \
             patch("tools.compress_segmented.compress_image",
                   side_effect=capturing_compress):
            result = ticktalk_main.compress_bitmap.__wrapped__("fake.png")

        assert captured["max_bytes"] == 238
        assert len(result) == 238


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

    def test_bitmap_queued_as_tlv_sf7(self):
        """SF7 (242B): bitmap sent via queue_transmit as flood_bitmap_compressed."""
        bitmap = b"\x01" * 100
        handler = self._invoke(bitmap, lora_limit=242)
        handler.queue_transmit.assert_called_with({'flood_bitmap_compressed': bitmap})

    def test_bitmap_queued_as_tlv_sf8(self):
        """Former 'raw mode' SF8 (125B): also sent via queue_transmit, not queue_binary_transmit."""
        bitmap = b"\x01" * 50
        handler = self._invoke(bitmap, lora_limit=125)
        handler.queue_transmit.assert_called_with({'flood_bitmap_compressed': bitmap})

    def test_no_raw_binary_transmit_for_bitmap(self):
        """Bitmap bytes must never be passed directly to queue_binary_transmit."""
        bitmap = b"\x01" * 100
        handler = self._invoke(bitmap, lora_limit=242)
        raw_calls = [c for c in handler.queue_binary_transmit.call_args_list
                     if c.args and c.args[0] == bitmap]
        assert raw_calls == []

    def test_empty_bitmap_skips_transmission(self):
        """Empty bitmap b'' → lora_token returns without any bitmap queue_transmit call."""
        handler = self._invoke(b"", lora_limit=242)
        bitmap_calls = [
            c for c in handler.queue_transmit.call_args_list
            if c.args and isinstance(c.args[0], dict)
            and 'flood_bitmap_compressed' in c.args[0]
        ]
        assert bitmap_calls == []
