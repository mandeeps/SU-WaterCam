"""Tests for the LoRa daemon + IPC redesign (docs/LORA_HANDLER_MULTIPROCESS_ISSUE.md).

Covers:
1. get_lora_handler() — returns None when the daemon socket is absent, a
   LoRaHandlerClient when it exists.
2. LoRaHandlerClient <-> tools.lora_daemon.serve() — a real client/server
   round trip for each RPC action, over a throwaway /tmp socket (mirroring
   segformer_daemon.py's own --socket /tmp/... testing convention).
3. LoRaRuntimeManager(lora_handler=...) — daemon-mode wiring (wires
   set_runtime_callback/start_listening) vs. client-mode (does not).
4. set_runtime_manager()/get_runtime_manager() — publishing a manager as the
   process singleton so module-level get_parameter/set_parameter reach it.

Hardware is mocked by the autouse _mock_serial_port fixture in conftest.py.
"""
import os
import threading
import time

import pytest
from unittest.mock import MagicMock, patch

import tools.lora_daemon as daemon_mod
import tools.lora_handler_concurrent as lhc
import tools.lora_runtime_integration as lri


@pytest.fixture(autouse=True)
def _reset_singletons():
    lhc._lora_handler = None
    lri._runtime_manager = None
    yield
    lhc._lora_handler = None
    lri._runtime_manager = None


# ---------------------------------------------------------------------------
# get_lora_handler() socket-existence contract
# ---------------------------------------------------------------------------

class TestGetLoraHandlerSocketContract:

    def test_returns_none_when_socket_absent(self, tmp_path):
        missing = str(tmp_path / "does_not_exist.sock")
        assert lhc.get_lora_handler(missing) is None

    def test_returns_client_when_socket_present(self, tmp_path):
        sock_path = tmp_path / "present.sock"
        import socket as _socket
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.bind(str(sock_path))
        try:
            result = lhc.get_lora_handler(str(sock_path))
            assert isinstance(result, lhc.LoRaHandlerClient)
        finally:
            s.close()

    def test_returns_none_when_path_is_not_a_socket(self, tmp_path):
        not_a_socket = tmp_path / "plain_file"
        not_a_socket.write_text("not a socket")
        assert lhc.get_lora_handler(str(not_a_socket)) is None


# ---------------------------------------------------------------------------
# Real client/server round trip
# ---------------------------------------------------------------------------

@pytest.fixture
def running_daemon(tmp_path):
    """A real tools.lora_daemon.serve() loop around a mocked-serial
    LoRaHandler, bound to a throwaway /tmp socket, in a background thread."""
    socket_path = str(tmp_path / "lora_test.sock")

    with patch.object(lhc.LoRaHandler, "refresh_size_limit", autospec=True, return_value=True), \
         patch.object(lhc.LoRaHandler, "start_listening", autospec=True):
        handler = lhc.create_lora_handler_with_retry()
    assert handler is not None

    thread = threading.Thread(
        target=daemon_mod.serve, args=(handler, socket_path), daemon=True
    )
    thread.start()

    deadline = time.time() + 5
    while time.time() < deadline and not os.path.exists(socket_path):
        time.sleep(0.02)
    assert os.path.exists(socket_path), "daemon socket never appeared"

    yield socket_path, handler


class TestLoRaHandlerClientRoundTrip:

    def test_is_joined_round_trip(self, running_daemon):
        socket_path, handler = running_daemon
        handler.is_joined = lambda: True
        client = lhc.LoRaHandlerClient(socket_path)
        assert client.is_joined() is True

    def test_queue_transmit_round_trip(self, running_daemon):
        socket_path, handler = running_daemon
        client = lhc.LoRaHandlerClient(socket_path)
        assert client.queue_transmit({'timestamp': 123, 'battery_percent': 50}) is True
        assert handler.get_queue_depth() == 1

    def test_queue_binary_transmit_round_trip(self, running_daemon):
        socket_path, handler = running_daemon
        client = lhc.LoRaHandlerClient(socket_path)
        assert client.queue_binary_transmit("deadbeef") is True
        assert handler.get_queue_depth() == 1

    def test_get_queue_depth_round_trip(self, running_daemon):
        socket_path, handler = running_daemon
        handler.queue_transmit({'timestamp': 1})
        handler.queue_transmit({'timestamp': 2})
        client = lhc.LoRaHandlerClient(socket_path)
        assert client.get_queue_depth() == 2

    def test_get_size_limit_round_trip(self, running_daemon):
        socket_path, handler = running_daemon
        handler.current_size_limit = 133
        client = lhc.LoRaHandlerClient(socket_path)
        assert client.get_size_limit() == 133

    def test_transmit_round_trip_with_bytes_content(self, running_daemon):
        socket_path, handler = running_daemon
        handler.transmit = lambda content, max_retries=2: content == b"hello"
        client = lhc.LoRaHandlerClient(socket_path)
        assert client.transmit(b"hello") is True
        assert client.transmit(b"nope") is False

    def test_transmit_round_trip_with_hex_string_content(self, running_daemon):
        socket_path, handler = running_daemon
        received = []
        handler.transmit = lambda content, max_retries=2: received.append(content) or True
        client = lhc.LoRaHandlerClient(socket_path)
        assert client.transmit("deadbeef", max_retries=1) is True
        assert received == ["deadbeef"]

    def test_process_transmit_queue_round_trip(self, running_daemon):
        socket_path, handler = running_daemon
        called = []
        handler.process_transmit_queue = lambda: called.append(True)
        client = lhc.LoRaHandlerClient(socket_path)
        client.process_transmit_queue()
        assert called == [True]

    def test_unknown_action_raises(self, running_daemon):
        socket_path, handler = running_daemon
        client = lhc.LoRaHandlerClient(socket_path)
        with pytest.raises(RuntimeError):
            client._call("not_a_real_action", 5)

    def test_client_unreachable_socket_degrades_gracefully(self, tmp_path):
        client = lhc.LoRaHandlerClient(str(tmp_path / "nope.sock"))
        assert client.is_joined() is False
        assert client.queue_transmit({'timestamp': 1}) is False
        assert client.get_queue_depth() == 0
        assert client.get_size_limit() == 242
        client.process_transmit_queue()  # must not raise


# ---------------------------------------------------------------------------
# LoRaRuntimeManager(lora_handler=...) daemon-mode vs. client-mode wiring
# ---------------------------------------------------------------------------

class TestLoRaRuntimeManagerDaemonMode:

    def test_daemon_mode_wires_callback_and_starts_listening(self):
        mock_handler = MagicMock()
        manager = lri.LoRaRuntimeManager(lora_handler=mock_handler)

        assert manager.lora_handler is mock_handler
        mock_handler.set_runtime_callback.assert_called_once()
        mock_handler.start_listening.assert_called_once()
        assert manager.listening is True

    def test_client_mode_does_not_wire_listening(self, tmp_path):
        with patch("tools.lora_runtime_integration.get_lora_handler", return_value=None):
            manager = lri.LoRaRuntimeManager()

        assert manager.lora_handler is None
        assert manager.listening is False

    def test_client_mode_with_reachable_client_does_not_start_listening(self):
        """Even when the daemon IS reachable, client-mode must not attempt to
        wire set_runtime_callback()/start_listening() -- that wiring is
        exclusively the daemon's own responsibility (see
        _adopt_daemon_owned_handler), since decode() needs the real handler's
        own state and must run in exactly one process.

        spec=LoRaHandlerClient means these attributes don't exist on the
        mock at all (matching the real class, which has no such methods) --
        so if _init_lora_handler() still tried to call them, this would raise
        AttributeError immediately rather than silently no-op.
        """
        mock_client = MagicMock(spec=lhc.LoRaHandlerClient)
        with patch("tools.lora_runtime_integration.get_lora_handler", return_value=mock_client):
            manager = lri.LoRaRuntimeManager()

        assert manager.lora_handler is mock_client
        assert manager.listening is False


# ---------------------------------------------------------------------------
# set_runtime_manager() / get_runtime_manager() publishing
# ---------------------------------------------------------------------------

class TestSetRuntimeManager:

    def test_set_runtime_manager_is_returned_by_get_runtime_manager(self):
        mock_handler = MagicMock()
        manager = lri.LoRaRuntimeManager(lora_handler=mock_handler)
        lri.set_runtime_manager(manager)

        assert lri.get_runtime_manager() is manager

    def test_module_level_get_parameter_reaches_published_manager(self):
        mock_handler = MagicMock()
        manager = lri.LoRaRuntimeManager(lora_handler=mock_handler)
        manager.parameters['area_threshold'] = 77
        lri.set_runtime_manager(manager)

        assert lri.get_parameter('area_threshold') == 77


# ---------------------------------------------------------------------------
# get_parameter() change-detection reload (Step 5 of the daemon plan)
# ---------------------------------------------------------------------------

class TestParameterReloadOnChange:
    """A second LoRaRuntimeManager instance simulates the daemon process
    writing runtime_config.json independently of the instance under test --
    the scenario this reload exists for: a change applied by the daemon's
    own manager must still be observed, and fire registered callbacks, on
    every OTHER process's manager instance."""

    @pytest.fixture
    def manager_pair(self, tmp_path):
        config_path = str(tmp_path / "runtime_config.json")
        mgr_a = lri.LoRaRuntimeManager(config_file=config_path, lora_handler=MagicMock())
        mgr_b = lri.LoRaRuntimeManager(config_file=config_path, lora_handler=MagicMock())
        return mgr_a, mgr_b

    def test_external_change_is_observed_via_get_parameter(self, manager_pair):
        mgr_a, mgr_b = manager_pair
        mgr_a.set_parameter('area_threshold', 42)

        assert mgr_b.get_parameter('area_threshold') == 42

    def test_external_change_fires_registered_callback(self, manager_pair):
        mgr_a, mgr_b = manager_pair
        seen = []
        mgr_b.register_update_callback('area_threshold', lambda new, old: seen.append((old, new)))

        mgr_a.set_parameter('area_threshold', 55)
        mgr_b.get_parameter('area_threshold')  # triggers the mtime-gated reload

        assert seen == [(10, 55)]  # 10 is the default

    def test_unchanged_file_does_not_reread_or_refire(self, manager_pair, monkeypatch):
        mgr_a, mgr_b = manager_pair
        calls = []
        real_open = open

        def counting_open(path, *a, **kw):
            if path == mgr_b.config_file:
                calls.append(path)
            return real_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", counting_open)

        mgr_b.get_parameter('area_threshold')
        mgr_b.get_parameter('area_threshold')
        mgr_b.get_parameter('area_threshold')

        assert calls == [], "unchanged file should not be re-opened/re-parsed"

    def test_own_write_does_not_self_trigger_a_duplicate_callback(self, manager_pair):
        """A manager must not treat its own set_parameter() write as an
        external change on the next get_parameter() call -- that would fire
        the same callback a second time for a change it already applied and
        already dispatched synchronously inside set_parameter() itself."""
        mgr_a, _mgr_b = manager_pair
        seen = []
        mgr_a.register_update_callback('area_threshold', lambda new, old: seen.append((old, new)))

        mgr_a.set_parameter('area_threshold', 33)
        mgr_a.get_parameter('area_threshold')
        mgr_a.get_parameter('area_threshold')

        assert seen == [(10, 33)]  # exactly once, not twice
