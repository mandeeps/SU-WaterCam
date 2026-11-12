"""Every service that runs as pi is sandboxed (#207), and none that runs doas
blocks it: NoNewPrivileges is inherited by doas and would stop it working."""
import glob
import os

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")
# Services whose code calls doas (shutdown, systemctl, uhubctl, set-clock)
RUNS_DOAS = {"ticktalk.service", "max-awake.service", "remote-debug.service"}
# Bench and development only: not installed on deployed units
BENCH_ONLY = {"filebrowser.service", "button.service"}


def _units():
    for path in sorted(glob.glob(os.path.join(CONFIG, "*.service"))):
        with open(path) as f:
            yield os.path.basename(path), f.read()


def test_pi_services_are_sandboxed():
    for name, text in _units():
        if "\nUser=pi" in text and name not in BENCH_ONLY:
            assert "\nProtectSystem=" in text, name


def test_doas_services_keep_new_privileges():
    for name, text in _units():
        if name in RUNS_DOAS:
            assert "NoNewPrivileges=yes" not in text, name


def test_ticktalk_refuses_unlisted_pickles():
    """The runtime's UDP listener runs SafeUnpickle in enforce mode, not warn (#204)."""
    with open(os.path.join(CONFIG, "ticktalk.service")) as f:
        assert "\nEnvironment=TT_PICKLE_POLICY=enforce\n" in f.read()
