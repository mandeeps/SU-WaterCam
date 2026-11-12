"""The node's input firewall (config/nftables/watercam-host.nft): drop by default,
let Tailscale in, and never touch Tailscale's or the cellular filter's tables."""
import os
import re
import shutil
import subprocess

import pytest

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")
NFT = os.path.join(CONFIG, "nftables", "watercam-host.nft")
UNIT = os.path.join(CONFIG, "watercam-firewall.service")


def _rules():
    with open(NFT) as f:
        return [line.split("#")[0].strip() for line in f if line.split("#")[0].strip()]


def test_input_drops_by_default():
    assert "type filter hook input priority filter; policy drop;" in _rules()


def test_tailscale_and_replies_get_in():
    rules = _rules()
    for rule in ('iif "lo" accept', 'iifname "tailscale0" accept',
                 "ct state established,related accept", "udp dport 41641 accept",
                 "udp sport 67 udp dport 68 accept"):
        assert rule in rules, rule
    # IPv6 keeps its address: router adverts and neighbour discovery
    assert any("nd-router-advert" in r and "nd-neighbor-solicit" in r for r in rules)


def test_no_port_is_opened_beyond_tailscale_and_dhcp():
    opened = {int(p) for r in _rules() for p in re.findall(r"\bdport (\d+) accept", r)}
    assert opened <= {41641, 68, 546}, opened


def test_it_only_touches_its_own_table():
    rules = _rules()
    assert not any(r.startswith("flush") for r in rules)
    tables = {m.group(1) for r in rules if (m := re.match(r"(?:delete )?table (\S+ \S+)", r))}
    assert tables == {"inet watercam_host"}, tables


def test_unit_loads_it_before_the_network():
    with open(UNIT) as f:
        unit = f.read()
    assert "ExecStart=/usr/sbin/nft -f /etc/nftables.d/watercam-host.nft" in unit
    assert "Before=network-pre.target" in unit and "DefaultDependencies=no" in unit
    assert "ExecStop=-/usr/sbin/nft delete table inet watercam_host" in unit


def test_nft_accepts_the_file():
    """Syntax check with nft itself, in a throwaway network namespace."""
    if not (shutil.which("nft") and shutil.which("unshare")):
        pytest.skip("nft or unshare not installed")
    r = subprocess.run(["unshare", "-rn", "nft", "-c", "-f", NFT], capture_output=True, text=True)
    if r.returncode and ("Operation not permitted" in r.stderr or "unshare" in r.stderr):
        pytest.skip("no user namespaces here: " + r.stderr.strip())
    assert r.returncode == 0, r.stderr
