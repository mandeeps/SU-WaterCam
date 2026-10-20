"""config/serial-getty-noise.conf: console-login messages rate-limited, no restart delay."""
import configparser
import os

PATH = os.path.join(os.path.dirname(__file__), "..", "config", "serial-getty-noise.conf")


def test_dropin_settings():
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    with open(PATH) as f:
        cp.read_string(f.read())
    assert "RestartSec" not in cp["Service"]          # the prompt must come back at once
    assert cp["Service"]["LogRateLimitBurst"] and cp["Service"]["LogRateLimitIntervalSec"]


def test_guide_installs_it_for_the_console_port():
    guide = open(os.path.join(os.path.dirname(PATH), "..", "docs", "FIELD_DEPLOYMENT_GUIDE.md")).read()
    assert "serial-getty@ttyAMA0.service.d/watercam-noise.conf" in guide
