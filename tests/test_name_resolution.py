"""Regression tests guarding against NameError in @SQify-decorated functions.

TTPython compiles ticktalk_main.py into a pickle (output/ticktalk_main.pickle).
When the runtime executes a function from that pickle, it runs the function's
bytecode in a namespace that was captured at compile time.  Any name that is
only imported at the module level — rather than inside the function body with a
local `import` statement — will not be present and raises NameError at runtime.

These tests call each @SQify function via __wrapped__ (which bypasses the
SQify machinery and invokes the raw Python function) with all hardware and
network dependencies patched.  A NameError from any test means a module-level
name leaked into a function body that TTPython will execute in isolation.

Behavioral tests for call_shutdown() and wittypi_emergency_control() are also
included here because those functions were the source of recent NameError bugs.
"""
import contextlib
import sys
from unittest.mock import MagicMock, patch, call as mock_call


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _mock_runtime_manager(count=1, auto_shutdown=True, limit=3, emergency=False):
    mgr = MagicMock()
    mgr.atomic_increment_iteration_count.return_value = {
        'iteration_count': count,
        'auto_shutdown_enabled': auto_shutdown,
        'shutdown_iteration_limit': limit,
        'emergency_mode': emergency,
    }
    mgr.parameters = {}
    return mgr


def _lora_runtime_patches(mgr=None, get_param_return=False):
    """Patches for tools.lora_runtime_integration used inside function bodies."""
    if mgr is None:
        mgr = _mock_runtime_manager()
    return [
        patch("tools.lora_runtime_integration.get_runtime_manager", return_value=mgr),
        patch("tools.lora_runtime_integration.get_parameter", return_value=get_param_return),
        patch("tools.lora_runtime_integration.integrate_with_ticktalk"),
    ]


# ---------------------------------------------------------------------------
# call_shutdown — behavioral + NameError regression
# ---------------------------------------------------------------------------

class TestCallShutdown:
    """call_shutdown() imports get_runtime_manager locally; verify correct
    behaviour and that no module-global NameError regresses."""

    def _run(self, mgr):
        import ticktalk_main
        with contextlib.ExitStack() as stack:
            for p in _lora_runtime_patches(mgr):
                stack.enter_context(p)
            return ticktalk_main.call_shutdown.__wrapped__("trigger")

    def test_no_name_error(self):
        """Smoke: function reaches get_runtime_manager without NameError."""
        mgr = _mock_runtime_manager(count=1, limit=3)
        try:
            self._run(mgr)
        except NameError as exc:
            raise AssertionError(f"NameError in call_shutdown: {exc}") from exc

    def test_below_limit_continues(self):
        """count < limit → function returns without calling sys.exit."""
        mgr = _mock_runtime_manager(count=1, limit=3)
        result = self._run(mgr)
        assert result is None  # SQify wrapper returns None for normal exit

    def test_emergency_mode_skips_shutdown(self):
        """emergency_mode=True → returns 'emergency_mode_active', no sys.exit."""
        mgr = _mock_runtime_manager(count=5, limit=3, emergency=True)
        result = self._run(mgr)
        assert result == "emergency_mode_active"

    def test_at_limit_triggers_shutdown(self):
        """count >= limit → sys.exit('shutdown') called."""
        import ticktalk_main
        mgr = _mock_runtime_manager(count=3, limit=3, auto_shutdown=True)
        with contextlib.ExitStack() as stack:
            for p in _lora_runtime_patches(mgr):
                stack.enter_context(p)
            stack.enter_context(patch("subprocess.call"))  # suppress doas
            try:
                ticktalk_main.call_shutdown.__wrapped__("trigger")
                raise AssertionError("Expected SystemExit not raised")
            except SystemExit as exc:
                assert exc.code == "shutdown"

    def test_shutdown_disabled_skips_exit(self):
        """auto_shutdown_enabled=False → no sys.exit even if count >= limit."""
        mgr = _mock_runtime_manager(count=10, limit=3, auto_shutdown=False)
        try:
            result = self._run(mgr)
        except SystemExit:
            raise AssertionError("sys.exit called when auto_shutdown_enabled=False")

    def test_runtime_manager_failure_uses_defaults(self):
        """get_runtime_manager() raises → defaults keep the process running."""
        import ticktalk_main
        with patch("tools.lora_runtime_integration.get_runtime_manager",
                   side_effect=RuntimeError("unavailable")):
            try:
                ticktalk_main.call_shutdown.__wrapped__("trigger")
            except SystemExit:
                raise AssertionError("sys.exit fired despite manager failure")
            except NameError as exc:
                raise AssertionError(f"NameError after manager failure: {exc}") from exc


# ---------------------------------------------------------------------------
# wittypi_emergency_control — NameError regression (get_wittypi_schedule_config)
# ---------------------------------------------------------------------------

class TestWittypiEmergencyControl:
    """wittypi_emergency_control() must not call the module-global
    get_wittypi_schedule_config() — it now reads parameters via a local
    get_parameter import.  Tests verify both modes and no NameError."""

    def _run(self, emergency_mode, get_param_side=None):
        import ticktalk_main
        patches = [
            patch("tools.wittypi_control.clear_shutdown_time"),
            patch("tools.wittypi_control.set_schedule",
                  return_value="2026-01-01 08:00:00"),
        ]
        if get_param_side is not None:
            patches.append(
                patch("tools.lora_runtime_integration.get_parameter",
                      side_effect=get_param_side)
            )
        else:
            patches.append(
                patch("tools.lora_runtime_integration.get_parameter",
                      return_value=8)
            )
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return ticktalk_main.wittypi_emergency_control.__wrapped__(emergency_mode)

    def test_emergency_on_no_name_error(self):
        """Emergency ON: must not raise NameError."""
        try:
            result = self._run(True)
        except NameError as exc:
            raise AssertionError(
                f"NameError in wittypi_emergency_control(True): {exc}"
            ) from exc
        assert result['status'] == 'wittypi_emergency_activated'

    def test_emergency_off_no_name_error(self):
        """Emergency OFF: must not raise NameError (previously called module-global
        get_wittypi_schedule_config() which would NameError in pickle execution)."""
        try:
            result = self._run(False)
        except NameError as exc:
            raise AssertionError(
                f"NameError in wittypi_emergency_control(False): {exc}"
            ) from exc
        assert result['status'] == 'wittypi_normal_schedule_restored'

    def test_emergency_on_clears_schedule(self):
        """Emergency ON calls clear_shutdown_time exactly once."""
        import ticktalk_main
        mock_clear = MagicMock()
        with patch("tools.wittypi_control.clear_shutdown_time", mock_clear), \
             patch("tools.wittypi_control.set_schedule", return_value=""), \
             patch("tools.lora_runtime_integration.get_parameter", return_value=8):
            ticktalk_main.wittypi_emergency_control.__wrapped__(True)
        mock_clear.assert_called_once()

    def test_emergency_off_reads_params_from_get_parameter(self):
        """Emergency OFF reads schedule config via get_parameter, not a global fn."""
        import ticktalk_main
        param_calls = []

        def recording_get_param(key, default=None):
            param_calls.append(key)
            return default

        with patch("tools.wittypi_control.clear_shutdown_time"), \
             patch("tools.wittypi_control.set_schedule", return_value="next"), \
             patch("tools.lora_runtime_integration.get_parameter",
                   side_effect=recording_get_param):
            ticktalk_main.wittypi_emergency_control.__wrapped__(False)

        assert 'wittypi_start_hour' in param_calls
        assert 'wittypi_interval_minutes' in param_calls

    def test_wittypi_unavailable_returns_error_dict(self):
        """ImportError from wittypi_control → status 'wittypi_unavailable', no crash."""
        import ticktalk_main
        with patch("tools.wittypi_control.clear_shutdown_time",
                   side_effect=ImportError("no WittyPi")):
            result = ticktalk_main.wittypi_emergency_control.__wrapped__(True)
        assert result['status'] == 'wittypi_unavailable'


# ---------------------------------------------------------------------------
# lora_listener — NameError smoke test
# ---------------------------------------------------------------------------

class TestLoraListenerNameResolution:

    def test_no_name_error(self):
        """lora_listener() must not raise NameError for any name it uses."""
        import ticktalk_main
        mock_handler = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.parameters = {}

        with patch("tools.lora_handler_concurrent.get_lora_handler",
                   return_value=mock_handler), \
             patch("tools.lora_runtime_integration.get_parameter",
                   return_value=False), \
             patch("tools.lora_runtime_integration.get_runtime_manager",
                   return_value=mock_mgr):
            try:
                ticktalk_main.lora_listener.__wrapped__()
            except NameError as exc:
                raise AssertionError(
                    f"NameError in lora_listener: {exc}"
                ) from exc


# ---------------------------------------------------------------------------
# initialize_lora_integration — NameError smoke test
# ---------------------------------------------------------------------------

class TestInitializeLoraIntegration:

    def test_no_name_error(self):
        """initialize_lora_integration() must resolve all names via local imports."""
        import ticktalk_main
        with contextlib.ExitStack() as stack:
            for p in _lora_runtime_patches():
                stack.enter_context(p)
            try:
                result = ticktalk_main.initialize_lora_integration.__wrapped__("trigger")
            except NameError as exc:
                raise AssertionError(
                    f"NameError in initialize_lora_integration: {exc}"
                ) from exc
        assert result['status'] == 'success'

    def test_import_error_returns_failed_status(self):
        """ImportError → status 'failed', no crash."""
        import ticktalk_main
        with patch("tools.lora_runtime_integration.integrate_with_ticktalk",
                   side_effect=ImportError("no module")), \
             patch("tools.lora_runtime_integration.get_runtime_manager",
                   return_value=MagicMock()):
            result = ticktalk_main.initialize_lora_integration.__wrapped__("trigger")
        assert result['status'] == 'failed'
        assert 'Import error' in result['error']
