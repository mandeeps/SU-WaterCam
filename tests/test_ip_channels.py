"""ip_uplink_transmit's channel list: 01 04 always, a bad field drops only itself,
the bitmap as base64."""
import base64
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import test_ip_transport_order as t


def _sent_channels(tx):
    return {c["code"]: c for c in tx.send_uplink.call_args.args[0]}


def _run_with(tx, *extra_patches, **run_kw):
    """t._run, with extra patches applied after the hardware ones (so they win)."""
    orig = t._hardware

    def hardware(stack):
        orig(stack)
        for p in extra_patches:
            stack.enter_context(p)
    t._hardware = hardware
    try:
        return t._run(tx, wifi=True, **run_kw)
    finally:
        t._hardware = orig


def test_emergency_status_is_always_sent():
    tx = t._tx(reachable=True)
    t._run(tx, wifi=True)
    assert _sent_channels(tx)["01 04"]["payload_hex"] == "00000000"


def test_emergency_status_follows_the_runtime_flag():
    tx = t._tx(reachable=True)
    _run_with(tx, patch("tools.lora_runtime_integration.get_parameter",
                        side_effect=lambda k, d=None: True if k == "emergency_mode" else d))
    assert _sent_channels(tx)["01 04"]["payload_hex"] == "00000001"


def test_a_nan_humidity_drops_only_the_humidity_channel():
    tx = t._tx(reachable=True)
    m = _run_with(tx, patch("tools.aht20_temperature.get_aht20",
                            return_value={"temperature_celsius": 20.0, "relative_humidity": float("nan")}))
    ch = _sent_channels(tx)
    assert m["result"]["success"] and "06 01" not in ch
    assert {"00 01", "01 04", "02 01", "05 01", "09 19"} <= set(ch)


def test_the_bitmap_goes_as_base64():
    import ticktalk_main
    tx = t._tx(reachable=True)
    with ExitStack() as stack:
        t._hardware(stack)
        stack.enter_context(patch("tools.transmit_ip.IPTransmitter", return_value=tx))
        stack.enter_context(patch("tools.lora_handler_concurrent.get_lora_handler", return_value=MagicMock()))
        stack.enter_context(patch("tools.transmit_ip.cellular_status", return_value=dict(t.ON_DEMAND_IDLE)))
        stack.enter_context(patch("tools.transmit_ip.wifi_connected", return_value=True))
        stack.enter_context(patch("tools.lora_health.assess", return_value={"healthy": False, "reason": "t"}))
        stack.enter_context(patch("tools.lora_store_forward.remove_delivered", return_value=0))
        ticktalk_main.ip_uplink_transmit.__wrapped__(bitmap=b"\x01\x02\x03", _sensor_tracker=None, dirname=None)
    c = _sent_channels(tx)["08 18"]
    assert "payload_hex" not in c and base64.b64decode(c["payload_b64"]) == b"\x01\x02\x03"
