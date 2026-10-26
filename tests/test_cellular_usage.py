"""tools/cellular_usage.py: the cellular byte counter and its once-a-day report."""
import datetime
import json
from unittest.mock import patch

import pytest

from tools import cellular_usage as cu

DAY = datetime.date(2026, 11, 3)


@pytest.fixture
def net(tmp_path):
    """A fake /sys/class/net; set(iface, ifindex, rx, tx) creates or updates one."""
    root = tmp_path / "net"
    root.mkdir()

    def set_(iface, ifindex, rx, tx):
        d = root / iface / "statistics"
        d.mkdir(parents=True, exist_ok=True)
        (root / iface / "ifindex").write_text(f"{ifindex}\n")
        (d / "rx_bytes").write_text(f"{rx}\n")
        (d / "tx_bytes").write_text(f"{tx}\n")
    set_.root = str(root)
    return set_


@pytest.fixture
def state(tmp_path):
    return str(tmp_path / "cellular_usage.json")


def _sample(state, net, boot="boot-1"):
    return cu.sample(state, net.root, boot)


def test_first_sample_counts_everything_so_far(net, state):
    net("wwan0", 5, 1000, 400)
    assert _sample(state, net) == 1400


def test_repeated_sample_adds_nothing(net, state):
    net("wwan0", 5, 1000, 400)
    _sample(state, net)
    assert _sample(state, net) == 1400


def test_growth_adds_the_difference(net, state):
    net("wwan0", 5, 1000, 400)
    _sample(state, net)
    net("wwan0", 5, 1500, 600)
    assert _sample(state, net) == 2100


def test_new_boot_counts_the_new_counters_in_full(net, state):
    net("wwan0", 5, 1000, 400)
    _sample(state, net, "boot-1")
    net("wwan0", 5, 300, 100)                 # the next wake: counters from zero again
    assert _sample(state, net, "boot-2") == 1800


def test_recreated_interface_counts_in_full_even_with_higher_counters(net, state):
    net("wwan0", 5, 1000, 400)
    _sample(state, net)
    net("wwan0", 6, 2000, 900)                # USB power cycled within the boot
    assert _sample(state, net) == 1400 + 2900


def test_counter_reset_is_never_subtracted(net, state):
    net("wwan0", 5, 1000, 400)
    _sample(state, net)
    net("wwan0", 5, 10, 5)
    assert _sample(state, net) == 1415


def test_interface_gone_keeps_the_total(net, state, tmp_path):
    net("wwan0", 5, 1000, 400)
    _sample(state, net)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cu.sample(state, str(empty), "boot-1") == 1400


def test_unit_without_a_modem_reports_nothing(state, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cu.sample(state, str(empty), "boot-1") is None
    assert cu.daily_report_kib(state, str(empty), "boot-1", DAY) is None
    assert not (tmp_path / "cellular_usage.json").exists()


def test_only_modem_interfaces_count(net, state):
    net("wlan0", 3, 10 ** 9, 10 ** 9)
    net("tailscale0", 4, 10 ** 9, 10 ** 9)
    net("wwan0", 5, 1000, 400)
    assert _sample(state, net) == 1400


def test_damaged_state_file_never_raises(net, state):
    with open(state, "w") as f:
        f.write("{not json")
    net("wwan0", 5, 1000, 400)
    assert _sample(state, net) == 1400        # starts again rather than failing the caller


def test_daily_report_first_wake_of_the_day_only(net, state):
    net("wwan0", 5, 4096, 1024)
    assert cu.daily_report_kib(state, net.root, "boot-1", DAY) == 5
    # the same wake's other uplink (IP after LoRa) carries it too
    assert cu.daily_report_kib(state, net.root, "boot-1", DAY) == 5
    # a later wake the same day does not (fresh counters: a new boot)
    net("wwan0", 5, 0, 0)
    assert cu.daily_report_kib(state, net.root, "boot-2", DAY) is None
    # the next day's first wake does
    net("wwan0", 7, 2048, 0)
    assert cu.daily_report_kib(state, net.root, "boot-3", DAY + datetime.timedelta(days=1)) == 7


def test_state_file_records_since_and_the_report(net, state):
    net("wwan0", 5, 1000, 400)
    cu.daily_report_kib(state, net.root, "boot-1", DAY)
    data = json.load(open(state))
    assert data["total_bytes"] == 1400 and "since" in data
    assert data["reported"] == {"day": DAY.isoformat(), "boot_id": "boot-1"}


def test_usb_power_off_samples_first(monkeypatch, tmp_path):
    from tools import usb_power
    order = []
    monkeypatch.setattr(usb_power, "HOLDERS_DIR", str(tmp_path / "holders"))
    monkeypatch.setattr(usb_power, "_switch", lambda action: order.append(action) or True)
    with patch("tools.cellular_usage.sample", side_effect=lambda *a, **k: order.append("sample")):
        usb_power.release("cellular")
    assert order == ["sample", "off"]


def test_lora_field_and_codec_case():
    from tools.lora_handler_concurrent import _encode_compressed_packet
    assert _encode_compressed_packet({"cellular_kib_total": 70000}) == bytes(
        [0x01, 0x11, 0x00, 0x01, 0x11, 0x70])
    from pathlib import Path
    codec = (Path(__file__).resolve().parent.parent / "config" / "chirpstack_device_profile_codec.js").read_text()
    assert "case '1-11':" in codec and "cellular_kib_total" in codec


def test_any_change_qualifies_for_lora():
    from tools import sensor_changes
    assert "cellular_kib_total" in sensor_changes.EXACT_FIELDS
