"""tools/node_inventory.py turns a node's packages, release and firmware into CycloneDX."""
import datetime
import json

from tools import node_inventory as inv

DPKG = ("ii \topenssl\t3.5.7-1~deb13u3\tarm64\n"
        "ii \ttailscale\t1.102.4\tarm64\n"
        "rc \told-removed\t1.0\tarm64\n"          # removed, config left: not installed
        "garbage line\n")
PIP = json.dumps([{"name": "onnxruntime", "version": "1.31.0"}, {"name": "Pillow", "version": "11.0"},
                  {"name": "broken"}])


def test_deb_packages():
    debs = inv.deb_packages(DPKG, "debian-13")
    assert [d["name"] for d in debs] == ["openssl", "tailscale"]
    assert debs[0]["purl"] == "pkg:deb/debian/openssl@3.5.7-1~deb13u3?arch=arm64&distro=debian-13"


def test_pip_packages():
    pips = inv.pip_packages(PIP)
    assert [p["purl"] for p in pips] == ["pkg:pypi/onnxruntime@1.31.0", "pkg:pypi/pillow@11.0"]
    assert inv.pip_packages("not json") == [] and inv.pip_packages(None) == []


def test_wittypi_firmware():
    assert inv.wittypi_firmware("0x07\n") == "0x07"
    assert inv.wittypi_firmware("Error: Read failed") is None
    assert inv.wittypi_firmware(None) is None


def test_bom():
    bom = inv.build_bom(
        inv.deb_packages(DPKG, "debian-13"), inv.pip_packages(PIP),
        {"describe": "v2026.10.1-dirty", "commit": "abc12345"},
        {"name": "type_pool_b0_dyn_meta.onnx", "path": "/x", "sha256": "4ca62324" + "0" * 56},
        "0x07", None, "ufo-01-01-006", now=datetime.datetime(2026, 10, 12, tzinfo=datetime.timezone.utc))
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.5"
    assert bom["metadata"]["component"] == {"type": "device", "name": "ufo-01-01-006",
                                            "version": "v2026.10.1-dirty"}
    by_name = {c["name"]: c for c in bom["components"]}
    assert by_name["SU-WaterCam"]["version"] == "v2026.10.1-dirty"
    assert by_name["type_pool_b0_dyn_meta.onnx"]["type"] == "machine-learning-model"
    assert by_name["type_pool_b0_dyn_meta.onnx"]["hashes"][0]["content"].startswith("4ca62324")
    assert by_name["Witty Pi 4 firmware"]["version"] == "0x07"
    assert by_name["MultiTech mDot firmware"]["version"] == "unknown"
    assert len(bom["components"]) == 4 + 2 + 2
    json.dumps(bom)                                 # serialisable as is


def test_bom_without_software_or_model():
    bom = inv.build_bom([], [], None, None, None, "4.1.21", "h")
    names = [c["name"] for c in bom["components"]]
    assert names == ["SU-WaterCam", "Witty Pi 4 firmware", "MultiTech mDot firmware"]
    assert bom["components"][2]["version"] == "4.1.21"


def test_main_writes_the_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(inv, "collect", lambda mdot: inv.build_bom(
        inv.deb_packages(DPKG, "debian-13"), [], None, None, None, mdot, "h"))
    out = tmp_path / "inv.cdx.json"
    assert inv.main(["--out", str(out), "--mdot-version", "4.1.21"]) == 0
    assert json.loads(out.read_text())["components"][2]["version"] == "4.1.21"
    assert "2 library" in capsys.readouterr().out
