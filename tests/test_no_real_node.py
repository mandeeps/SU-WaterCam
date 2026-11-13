"""The suite never reaches a node's real hardware, state or server (conftest
_no_real_node): run on 005 on 2026-11-12, call_shutdown's emergency path armed and
cleared the real Witty Pi alarms."""
import os

import tools.gps_window as gps_window
import tools.node_version as node_version
import tools.transmit_ip as transmit_ip
import tools.witty_pi_4 as witty_pi_4
from tests.conftest import _NODE_PATHS


def test_node_paths_point_into_the_tests_own_dir(tmp_path):
    for mod, attr in ((witty_pi_4, "WITTYPI_DIRECTORY"), (transmit_ip, "_DEFAULT_CONFIG_PATH"),
                      (node_version, "CACHE_PATH"), (gps_window, "BOOT_MARK"),
                      (gps_window, "ATTEMPT_FILE")):
        path = getattr(mod, attr)
        assert path.startswith(str(tmp_path / "no-node")), (mod.__name__, attr, path)
    # no Witty Pi and no config; state files can be written
    assert not os.path.exists(witty_pi_4.WITTYPI_DIRECTORY)
    assert not os.path.exists(transmit_ip._DEFAULT_CONFIG_PATH)
    assert os.path.isdir(os.path.dirname(gps_window.ATTEMPT_FILE))


def test_every_listed_path_exists_in_its_module():
    """A renamed constant would silently stop being redirected."""
    import importlib
    for name, attrs in _NODE_PATHS.items():
        mod = importlib.import_module("tools." + name)
        for attr in attrs:
            assert hasattr(mod, attr), f"tools.{name}.{attr} is gone: update conftest._NODE_PATHS"


def test_an_ip_transmitter_reads_no_real_config():
    tx = transmit_ip.IPTransmitter()
    assert not tx.enabled and not tx.api_key and not tx.configured


def test_witty_pi_calls_fail_as_on_a_workstation():
    from tools import wittypi_control
    try:
        armed = wittypi_control.set_startup_at(2_000_000_000)
    except Exception:          # noqa: BLE001 -- what a machine without a Witty Pi does
        armed = False
    assert not armed
