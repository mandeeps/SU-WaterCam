"""Model update packages (tools/model_package.py): round trips, tamper rejection, and a
committed fixture whose result hash must reproduce on every platform (x86 CI and the Pi).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

onnx = pytest.importorskip("onnx")
from onnx import numpy_helper  # noqa: E402

import ed25519_pure  # noqa: E402
import model_package as mp  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "model_package"
EXPECTED = json.loads((FIX / "expected.json").read_text())
SECRET = bytes.fromhex(ed25519_pure.RFC_TEST1["secret"])
KEYS = {EXPECTED["test_key_id"]: bytes.fromhex(EXPECTED["test_public_key"])}


@pytest.fixture(scope="module")
def models():
    return (FIX / "base.onnx").read_bytes(), (FIX / "trained.onnx").read_bytes()


def _weights(b):
    return {t.name: numpy_helper.to_array(t) for t in onnx.load_from_string(b).graph.initializer}


def _signed_delta(models, bits=4):
    pkg, result = mp.build_delta(*models, bits)
    return mp.sign_package(pkg, SECRET, EXPECTED["test_key_id"]), result


# --- the cross-platform fixture ---------------------------------------------------------

def test_fixture_reproduces_recorded_result():
    base = (FIX / "base.onnx").read_bytes()
    pkg = (FIX / "delta_int4.wcmu").read_bytes()
    assert mp.sha256(base) == EXPECTED["base_sha256"]
    m = mp.verify_package(pkg, KEYS)
    result = mp.apply_bytes(pkg, base)
    assert mp.sha256(result) == EXPECTED["result_sha256"] == m["result"]["sha256"]
    assert mp.weights_digest(onnx.load_from_string(result)) == EXPECTED["result_weights"]


def test_fixture_rebuild_is_deterministic(models):
    pkg, result = mp.build_delta(*models, 4)
    assert mp.sha256(result) == EXPECTED["result_sha256"]


# --- round trips --------------------------------------------------------------------------

@pytest.mark.parametrize("bits", [4, 8])
def test_delta_round_trip(models, bits):
    base, trained = models
    pkg, result = _signed_delta(models, bits)
    assert mp.apply_bytes(pkg, base) == result
    m = mp.read_manifest(pkg)
    assert m["kind"] == "delta" and m["encoding"]["bits"] == bits
    assert m["parent"]["sha256"] == mp.sha256(base)
    got, want, old = _weights(result), _weights(trained), _weights(base)
    for name in want:
        step = np.abs(want[name] - old[name]).max() / (2 ** (bits - 1) - 1) if want[name].dtype == np.float32 else 0
        assert np.abs(got[name].astype(np.float64) - want[name]).max() <= step / 2 + 1e-6, name
    assert np.array_equal(got["shape"], old["shape"])


def test_unchanged_tensors_are_not_sent(models):
    pkg, _ = mp.build_delta(*models, 8)
    p = mp.read_package(pkg)
    raw = mp._unxz(p.payload, 1 << 20)
    names = {e["name"] for e in json.loads(raw[4:4 + int.from_bytes(raw[:4], "little")])}
    assert names == {"conv1.weight", "gain", "head.weight", "head.bias"}


def test_identical_models_give_an_empty_delta(models):
    base = models[0]
    pkg, result = mp.build_delta(base, base, 4)
    assert mp.apply_bytes(pkg, base) == result
    assert _weights(result).keys() == _weights(base).keys()


def test_full_round_trip(models):
    pkg = mp.sign_package(mp.build_full(models[1], {"release_id": "seg-g0001"}), SECRET, EXPECTED["test_key_id"])
    m = mp.verify_package(pkg, KEYS)
    assert m["kind"] == "full" and m["release_id"] == "seg-g0001" and "parent" not in m
    assert mp.apply_bytes(pkg) == models[1]


def test_apply_writes_file_atomically(models, tmp_path):
    (tmp_path / "parent.onnx").write_bytes(models[0])
    pkg, result = _signed_delta(models)
    (tmp_path / "u.wcmu").write_bytes(pkg)
    sha = mp.apply(tmp_path / "u.wcmu", tmp_path / "parent.onnx", tmp_path / "out.onnx")
    assert sha == mp.sha256(result) == mp.sha256_file(tmp_path / "out.onnx")
    assert not (tmp_path / "out.onnx.tmp").exists()


# --- rejection ----------------------------------------------------------------------------

def test_wrong_parent_rejected(models, tmp_path):
    pkg, _ = _signed_delta(models)
    with pytest.raises(mp.PackageError, match="parent"):
        mp.apply_bytes(pkg, models[1])
    (tmp_path / "wrong.onnx").write_bytes(models[1])
    with pytest.raises(mp.PackageError):
        mp.apply(pkg, tmp_path / "wrong.onnx", tmp_path / "out.onnx")
    assert not (tmp_path / "out.onnx").exists()


def test_tampered_payload_rejected(models):
    pkg, _ = _signed_delta(models)
    bad = pkg[:-10] + bytes([pkg[-10] ^ 1]) + pkg[-9:]
    with pytest.raises(mp.PackageError, match="payload"):
        mp.verify_package(bad, KEYS)
    with pytest.raises(mp.PackageError, match="payload"):
        mp.apply_bytes(bad, models[0])


def test_tampered_manifest_rejected(models):
    pkg, _ = _signed_delta(models)
    p = mp.read_package(pkg)
    m = dict(p.manifest, release_id="seg-g9999")
    with pytest.raises(mp.SignatureError, match="bad signature"):
        mp.verify_package(mp.pack(m, p.signature, p.payload), KEYS)


def test_unsigned_and_unknown_key_rejected(models):
    pkg, _ = mp.build_delta(*models, 4)
    with pytest.raises(mp.SignatureError, match="unsigned"):
        mp.verify_package(pkg, KEYS)
    other = mp.sign_package(pkg, b"\x01" * 32, "someone-else")
    with pytest.raises(mp.SignatureError, match="unknown"):
        mp.verify_package(other, KEYS)
    forged = mp.sign_package(pkg, b"\x01" * 32, EXPECTED["test_key_id"])
    with pytest.raises(mp.SignatureError, match="bad signature"):
        mp.verify_package(forged, KEYS)


def test_result_mismatch_rejected(models):
    pkg, _ = _signed_delta(models)
    p = mp.read_package(pkg)
    m = json.loads(p.manifest_bytes)
    m["result"]["sha256"] = "0" * 64
    with pytest.raises(mp.PackageError, match="result sha"):
        mp.apply_bytes(mp.pack(m, b"", p.payload), models[0])


@pytest.mark.parametrize("blob", [b"", b"WCMU1\n", b"WCMU1\n\xff\xff\x00\x00{}", b"PK\x03\x04junk"])
def test_garbage_rejected(blob):
    with pytest.raises(mp.PackageError):
        mp.read_package(blob)


def test_non_canonical_manifest_rejected(models):
    pkg, _ = mp.build_delta(*models, 4)
    p = mp.read_package(pkg)
    m = json.dumps(p.manifest, indent=1).encode()
    loose = mp.MAGIC + len(m).to_bytes(4, "little") + m + b"\0\0\0\0" + p.payload
    with pytest.raises(mp.PackageError, match="canonical"):
        mp.read_package(loose)


def test_graph_change_needs_a_full_package(models):
    base = onnx.load_from_string(models[0])
    base.graph.initializer[0].name = "renamed"
    with pytest.raises(mp.PackageError, match="not a delta-compatible"):
        mp.build_delta(models[0], base.SerializeToString(), 4)


def test_reserved_meta_keys_rejected(models):
    with pytest.raises(ValueError):
        mp.build_full(models[0], {"result": {}})


# --- primitives ---------------------------------------------------------------------------

def test_reconstruct_requires_float32_scale():
    old = np.ones(3, np.float32)
    q = np.array([1, -1, 0], np.int8)
    with pytest.raises(TypeError):
        mp.reconstruct(old, q, 0.5)
    assert mp.reconstruct(old, q, np.float32(0.5)).tolist() == [1.5, 0.5, 1.0]


@pytest.mark.parametrize("n", [1, 2, 7, 64])
def test_int4_pack_round_trip(n):
    q = np.random.default_rng(n).integers(-7, 8, n).astype(np.int8)
    assert np.array_equal(mp.unpack_int4(mp.pack_int4(q), n), q)


def test_ed25519_rfc_vector():
    t = ed25519_pure.RFC_TEST1
    assert ed25519_pure.public_key(bytes.fromhex(t["secret"])).hex() == t["public"]
    assert ed25519_pure.verify(bytes.fromhex(t["public"]), b"", bytes.fromhex(t["sig"]))
    assert not ed25519_pure.verify(bytes.fromhex(t["public"]), b"x", bytes.fromhex(t["sig"]))


def test_load_pubkeys(tmp_path):
    (tmp_path / "k.json").write_text(json.dumps({"keys": {"a": EXPECTED["test_public_key"]}}))
    assert mp.load_pubkeys(tmp_path / "k.json") == {"a": KEYS[EXPECTED["test_key_id"]]}


# --- fingerprint --------------------------------------------------------------------------

def test_fingerprint_is_deterministic(tmp_path):
    pytest.importorskip("onnxruntime")
    path = FIX / "base.onnx"
    a = mp.fingerprint(path, seed=1234)
    assert a == mp.fingerprint(path, seed=1234)
    assert a["input_shape"] == [1, 5, 16, 16]
    assert a["mask_sha256"] != mp.fingerprint(FIX / "trained.onnx", seed=1234)["mask_sha256"] \
        or a["logits_sha256"] != mp.fingerprint(FIX / "trained.onnx", seed=1234)["logits_sha256"]
