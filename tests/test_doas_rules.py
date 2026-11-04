"""config/doas.conf: every passwordless rule names its exact arguments (#205).

A rule without args= lets pi run the command with anything (e.g. shutdown with
any time or option). The shutdown callers all use `shutdown -h now`.
"""
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _rules():
    with open(os.path.join(ROOT, "config", "doas.conf")) as f:
        return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]


def test_passwordless_rules_name_their_arguments():
    exempt = {"/usr/local/sbin/watercam-set-clock"}     # our own script; it checks its arguments
    for rule in _rules():
        if " nopass " not in f" {rule} ":
            continue
        cmd = re.search(r"\bcmd\s+(\S+)", rule).group(1)
        assert " args " in f" {rule} " or cmd in exempt, rule


def test_shutdown_is_only_halt_or_reboot_now():
    shutdown = [r for r in _rules() if "/usr/sbin/shutdown" in r]
    assert shutdown == ["permit nopass :pi as root cmd /usr/sbin/shutdown args -h now",
                        "permit nopass :pi as root cmd /usr/sbin/shutdown args -r now"]


def test_every_shutdown_caller_matches_the_rule():
    pattern = re.compile(r"doas[^\n]*?/usr/sbin/shutdown([^\n]*)")
    for dirpath, _, files in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT).split(os.sep)
        if any(part in rel for part in (".git", "venv", ".venv", "node_modules", "tests", "docs")):
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(dirpath, name), errors="replace") as f:
                for m in pattern.finditer(f.read()):
                    tail = m.group(1)
                    assert any(a in tail for a in ('"-h", "now"', "-h now", '"-r", "now"', "-r now")), (name, m.group(0))
