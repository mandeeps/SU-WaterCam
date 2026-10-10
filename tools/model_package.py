"""Segmentation model update packages (.wcmu): build, sign, verify, apply, fingerprint.

The canonical copy lives here, with the node; the release builder in photo_processing
imports it by path. Design: documentation/MODEL_UPDATE_SYSTEM_PLAN.md.

Runs on the node's 5band env (Python 3.9, numpy 1.24, onnx 1.19) and newer: standard
library lzma, numpy, onnx; onnxruntime only for fingerprint(); `cryptography` optional.

Package:  b"WCMU1\\n" | u32 len | manifest (canonical JSON) | u32 len | signature | payload
Payload:  xz.  kind "full": the model file.  kind "delta" (tensor-delta-v1):
          u32 len | index JSON | blob, one index entry per changed ONNX initializer:
          {"name", "kind": "qdelta"|"raw", "dtype", "shape", "offset", "nbytes"[, "bits", "scale_bits"]}

The signature covers the manifest bytes, and the manifest carries the payload and result
hashes, so a valid signature authenticates the whole package.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import math
import os
import struct
from collections import namedtuple

import numpy as np

import ed25519_pure

MAGIC = b"WCMU1\n"
FORMAT = 1
SCHEME = "tensor-delta-v1"
_RESERVED = ("format", "kind", "parent", "result", "payload", "encoding", "signer_key_id")
_FLOAT_TYPES = {"tensor(float)": np.float32, "tensor(float16)": np.float16, "tensor(double)": np.float64}

Package = namedtuple("Package", "manifest manifest_bytes signature payload")


class PackageError(ValueError):
    """The package is malformed, doesn't match its manifest, or doesn't fit this parent."""


class SignatureError(PackageError):
    """The manifest signature is missing, from an unknown key, or invalid."""


# --- the one reconstruction rule -----------------------------------------------------------
def reconstruct(old: np.ndarray, q: np.ndarray, scale: np.float32) -> np.ndarray:
    """w_new = float32(old + float32(float32(q) * scale)).

    Separate elementwise ufuncs, all operands float32: IEEE single, round-to-nearest, no
    FMA. `scale` must be np.float32 -- a float64 scalar promotes differently under numpy 1.x
    (value-based casting) and 2.x (NEP 50), which would silently change the result.
    """
    if old.dtype != np.float32 or not isinstance(scale, np.float32):
        raise TypeError("reconstruct needs float32 weights and an np.float32 scale")
    w = q.astype(np.float32) * scale
    return old + w


def quantize_delta(old: np.ndarray, new: np.ndarray, bits: int):
    """Per-tensor symmetric quantization of new - old. Returns (q int8, scale np.float32)."""
    d = new - old
    if not np.all(np.isfinite(d)):
        raise PackageError("non-finite weight delta")
    qmax = 2 ** (bits - 1) - 1
    m = np.float32(np.abs(d).max())
    scale = np.float32(m / np.float32(qmax)) if m > 0 else np.float32(1.0)
    q = np.clip(np.rint(d / scale), -qmax, qmax).astype(np.int8)
    return q, scale


def pack_int4(q: np.ndarray) -> bytes:
    u = (q.ravel().astype(np.int16) + 8).astype(np.uint8)
    if u.size % 2:
        u = np.append(u, np.uint8(8))
    return (u[0::2] | (u[1::2] << 4)).tobytes()


def unpack_int4(b: bytes, n: int) -> np.ndarray:
    u = np.frombuffer(b, np.uint8)
    out = np.empty(u.size * 2, np.int16)
    out[0::2] = u & 0x0F
    out[1::2] = u >> 4
    return (out[:n] - 8).astype(np.int8)


# --- identities ----------------------------------------------------------------------------
def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def weights_digest(model) -> str:
    """Hash of initializer contents only, independent of protobuf serialization."""
    from onnx import numpy_helper
    h = hashlib.sha256()
    for t in sorted(model.graph.initializer, key=lambda t: t.name):
        a = numpy_helper.to_array(t)
        h.update(t.name.encode() + b"\0" + str(a.dtype).encode() + b"\0" +
                 repr(tuple(a.shape)).encode() + b"\0")
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def canonical(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


# --- container -----------------------------------------------------------------------------
def pack(manifest: dict, signature: bytes, payload: bytes) -> bytes:
    m = canonical(manifest)
    return MAGIC + struct.pack("<I", len(m)) + m + struct.pack("<I", len(signature)) + signature + payload


def read_package(pkg: bytes) -> Package:
    """Split a package and check the manifest's shape. Checks no hashes or signatures."""
    if pkg[:len(MAGIC)] != MAGIC:
        raise PackageError("not a WCMU1 package")
    try:
        i = len(MAGIC)
        (n,) = struct.unpack_from("<I", pkg, i); i += 4
        mbytes = bytes(pkg[i:i + n]); i += n
        (s,) = struct.unpack_from("<I", pkg, i); i += 4
        sig = bytes(pkg[i:i + s]); i += s
    except struct.error:
        raise PackageError("truncated package header") from None
    if len(mbytes) != n or len(sig) != s:
        raise PackageError("truncated package header")
    try:
        manifest = json.loads(mbytes)
    except ValueError:
        raise PackageError("manifest is not JSON") from None
    if not isinstance(manifest, dict) or canonical(manifest) != mbytes:
        raise PackageError("manifest is not canonical JSON")
    _check_manifest(manifest)
    return Package(manifest, mbytes, sig, bytes(pkg[i:]))


def read_manifest(pkg: bytes) -> dict:
    return read_package(pkg).manifest


def _check_manifest(m: dict) -> None:
    def need(cond, what):
        if not cond:
            raise PackageError(f"manifest: {what}")
    need(m.get("format") == FORMAT, f"format must be {FORMAT}")
    need(m.get("kind") in ("delta", "full"), "kind must be delta or full")
    for key in ("result", "payload"):
        need(isinstance(m.get(key), dict) and isinstance(m[key].get("sha256"), str)
             and isinstance(m[key].get("size"), int), f"{key}.sha256 and {key}.size required")
    need(m["payload"].get("compression") == "xz", "payload.compression must be xz")
    if m["kind"] == "delta":
        need(isinstance(m.get("parent"), dict) and isinstance(m["parent"].get("sha256"), str),
             "delta needs parent.sha256")
        need(isinstance(m.get("encoding"), dict) and m["encoding"].get("scheme") == SCHEME,
             f"delta needs encoding.scheme {SCHEME}")


# --- signatures ----------------------------------------------------------------------------
def load_pubkeys(path) -> dict:
    """{"keys": {key_id: hex public key}} -> {key_id: bytes}."""
    with open(path) as f:
        return {k: bytes.fromhex(v) for k, v in json.load(f)["keys"].items()}


def sign_package(pkg: bytes, secret: bytes, key_id: str) -> bytes:
    """Return pkg re-packed with signer_key_id set and an Ed25519 signature."""
    p = read_package(pkg)
    manifest = dict(p.manifest, signer_key_id=key_id)
    msg = canonical(manifest)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        sig = Ed25519PrivateKey.from_private_bytes(secret).sign(msg)
    except ImportError:
        sig = ed25519_pure.sign(secret, msg)
    return pack(manifest, sig, p.payload)


def verify_signature(manifest_bytes: bytes, signature: bytes, pubkeys: dict) -> str:
    """Check the signature over the exact manifest bytes; return the signer's key id."""
    try:
        key_id = json.loads(manifest_bytes).get("signer_key_id")
    except ValueError:
        raise SignatureError("manifest is not JSON") from None
    if not signature:
        raise SignatureError("package is unsigned")
    if key_id not in pubkeys:
        raise SignatureError(f"unknown signing key {key_id!r}")
    if not ed25519_pure.verify(pubkeys[key_id], manifest_bytes, signature):
        raise SignatureError("bad signature")
    return key_id


def verify_package(pkg: bytes, pubkeys: dict) -> dict:
    """Signature and payload hash. Return the manifest; raise PackageError otherwise."""
    p = read_package(pkg)
    verify_signature(p.manifest_bytes, p.signature, pubkeys)
    _check_payload(p)
    return p.manifest


def _check_payload(p: Package) -> None:
    if len(p.payload) != p.manifest["payload"]["size"] or sha256(p.payload) != p.manifest["payload"]["sha256"]:
        raise PackageError("payload sha mismatch")


# --- build ---------------------------------------------------------------------------------
def _xz(raw: bytes) -> bytes:
    return lzma.compress(raw, preset=9 | lzma.PRESET_EXTREME)


def _unxz(payload: bytes, limit: int) -> bytes:
    d = lzma.LZMADecompressor()
    try:
        raw = d.decompress(payload, max_length=limit)
    except lzma.LZMAError:
        raise PackageError("payload is not valid xz") from None
    if not d.eof:
        raise PackageError("payload larger than its manifest allows")
    return raw


def _new_manifest(kind: str, meta, result: bytes, payload: bytes) -> dict:
    import onnx
    meta = dict(meta or {})
    clash = [k for k in _RESERVED if k in meta]
    if clash:
        raise ValueError(f"meta may not set {clash}")
    meta.update(format=FORMAT, kind=kind,
                result={"sha256": sha256(result), "size": len(result),
                        "weights": weights_digest(onnx.load_from_string(result))},
                payload={"sha256": sha256(payload), "size": len(payload), "compression": "xz"})
    return meta


def build_full(onnx_bytes: bytes, meta: dict = None) -> bytes:
    """An unsigned full-model package (first release, or a graph change)."""
    payload = _xz(onnx_bytes)
    return pack(_new_manifest("full", meta, onnx_bytes, payload), b"", payload)


def build_delta(parent_bytes: bytes, trained_bytes: bytes, bits: int, meta: dict = None):
    """Return (unsigned package, result model bytes): the result is what every node reproduces.

    The trained model must have the parent's graph: same initializer names, shapes, dtypes.
    meta: extra manifest fields; "parent_release" ({"release_id": ...}) goes into parent.
    """
    import onnx
    from onnx import numpy_helper
    if bits not in (4, 8):
        raise ValueError("bits must be 4 or 8")
    parent = onnx.load_from_string(parent_bytes)
    trained = onnx.load_from_string(trained_bytes)
    old = {t.name: numpy_helper.to_array(t) for t in parent.graph.initializer}
    new = {t.name: numpy_helper.to_array(t) for t in trained.graph.initializer}
    if old.keys() != new.keys():
        raise PackageError("initializer names differ: not a delta-compatible update")
    index, blob = [], bytearray()
    for name in sorted(new):
        o, n = old[name], new[name]
        if o.shape != n.shape or o.dtype != n.dtype:
            raise PackageError(f"graph mismatch at {name}: not a delta-compatible update")
        if o.tobytes() == n.tobytes():
            continue
        e = {"name": name, "dtype": str(n.dtype), "shape": list(n.shape), "offset": len(blob)}
        if n.dtype == np.float32:
            q, scale = quantize_delta(o, n, bits)
            if not q.any():
                continue                    # change below one quantization step
            data = pack_int4(q) if bits == 4 else q.tobytes()
            e.update(kind="qdelta", bits=bits, scale_bits=int(np.array(scale, np.float32).view(np.uint32)))
        else:
            data = np.ascontiguousarray(n).tobytes()
            e.update(kind="raw")
        e["nbytes"] = len(data)
        blob += data
        index.append(e)
    idx = canonical(index)
    payload = _xz(struct.pack("<I", len(idx)) + idx + bytes(blob))
    result = _apply_delta(parent_bytes, payload, limit=2 * len(parent_bytes) + (1 << 20))
    meta = dict(meta or {})
    parent_info = meta.pop("parent_release", {})    # e.g. {"release_id": "seg-g0006"}
    manifest = _new_manifest("delta", meta, result, payload)
    manifest.update(parent=dict(parent_info, sha256=sha256(parent_bytes), weights=weights_digest(parent)),
                    encoding={"scheme": SCHEME, "bits": bits, "per": "tensor"})
    return pack(manifest, b"", payload), result


# --- apply ---------------------------------------------------------------------------------
def _apply_delta(parent_bytes: bytes, payload: bytes, limit: int) -> bytes:
    import onnx
    from onnx import numpy_helper
    raw = _unxz(payload, limit)
    try:
        (n,) = struct.unpack_from("<I", raw, 0)
        index = json.loads(raw[4:4 + n])
    except (struct.error, ValueError):
        raise PackageError("bad delta index") from None
    blob = memoryview(raw)[4 + n:]
    model = onnx.load_from_string(parent_bytes)
    by_name = {t.name: t for t in model.graph.initializer}
    for e in index:
        t = by_name.get(e.get("name"))
        if t is None:
            raise PackageError(f"delta names unknown initializer {e.get('name')!r}")
        if list(t.dims) != e["shape"]:
            raise PackageError(f"shape mismatch at {e['name']}")
        off, nb = e["offset"], e["nbytes"]
        if off < 0 or nb < 0 or off + nb > len(blob):
            raise PackageError(f"delta entry {e['name']} outside the payload")
        b = bytes(blob[off:off + nb])
        count = int(np.prod(e["shape"])) if e["shape"] else 1
        if e["kind"] == "raw":
            dt = np.dtype(e["dtype"])
            if nb != count * dt.itemsize or dt != numpy_helper.to_array(t).dtype:
                raise PackageError(f"raw entry {e['name']} has the wrong size or dtype")
            arr = np.frombuffer(b, dt).reshape(e["shape"])
        elif e["kind"] == "qdelta":
            old = numpy_helper.to_array(t)
            bits = e["bits"]
            expected = (count + 1) // 2 if bits == 4 else count
            if old.dtype != np.float32 or bits not in (4, 8) or nb != expected:
                raise PackageError(f"qdelta entry {e['name']} has the wrong size or dtype")
            q = unpack_int4(b, count) if bits == 4 else np.frombuffer(b, np.int8)
            scale = np.array(e["scale_bits"], np.uint32).view(np.float32)[()]
            arr = reconstruct(old, q.reshape(old.shape), np.float32(scale))
        else:
            raise PackageError(f"unknown entry kind {e['kind']!r}")
        _set_initializer(t, arr)
    return model.SerializeToString()


def _set_initializer(t, arr: np.ndarray) -> None:
    """Replace a tensor's contents in place, keeping every other field."""
    for f in ("float_data", "int32_data", "int64_data", "double_data", "uint64_data"):
        t.ClearField(f)
    t.raw_data = np.ascontiguousarray(arr).tobytes()


def apply_bytes(pkg: bytes, parent_bytes: bytes = None) -> bytes:
    """Apply a package in memory. Checks parent, payload and result hashes, not the signature."""
    p = read_package(pkg)
    m = p.manifest
    _check_payload(p)
    limit = m["result"]["size"]
    if m["kind"] == "full":
        result = _unxz(p.payload, limit)
    else:
        if parent_bytes is None or sha256(parent_bytes) != m["parent"]["sha256"]:
            raise PackageError("parent sha mismatch")
        result = _apply_delta(parent_bytes, p.payload, limit=2 * len(parent_bytes) + (1 << 20))
    if len(result) != m["result"]["size"] or sha256(result) != m["result"]["sha256"]:
        raise PackageError("result sha mismatch: reconstruction differs from the workstation build")
    return result


def apply(pkg, parent_path, out_path) -> str:
    """Apply a package (bytes or path) to the model at parent_path; write out_path atomically.

    Checks the parent hash before and the result hash after; out_path is only created when
    the result matches. Verify the signature first with verify_package(). Returns the
    result's sha256.
    """
    if not isinstance(pkg, (bytes, bytearray)):
        with open(pkg, "rb") as f:
            pkg = f.read()
    parent = None
    if parent_path is not None:
        with open(parent_path, "rb") as f:
            parent = f.read()
    result = apply_bytes(pkg, parent)
    tmp = f"{out_path}.tmp"
    with open(tmp, "wb") as f:
        f.write(result)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
    return sha256(result)


# --- fingerprint ---------------------------------------------------------------------------
def fingerprint(onnx_path, seed: int, input_shape=None, threads: int = 4) -> dict:
    """Run the model on a seeded random input; return hashes of the logits and the mask.

    Bit-identical across Pi 4B nodes with the same onnxruntime (Phase 0), so record it on
    the bench Pi and compare on the node. Not portable across architectures: x86 and aarch64
    give the same mask but logits that differ in the last bits. numpy's RNG stream is only stable within a numpy
    version, so the manifest's runtime field pins numpy too.
    """
    import onnxruntime as ort
    o = ort.SessionOptions()
    o.intra_op_num_threads = threads
    o.inter_op_num_threads = 1
    o.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    o.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    s = ort.InferenceSession(str(onnx_path), o, providers=["CPUExecutionProvider"])
    inp = s.get_inputs()[0]
    shape = list(input_shape or inp.shape)
    if not all(isinstance(d, int) and d > 0 for d in shape):
        raise ValueError(f"input shape {inp.shape} has dynamic dims: pass input_shape")
    dtype = _FLOAT_TYPES.get(inp.type)
    if dtype is None:
        raise ValueError(f"unsupported input type {inp.type}")
    x = np.random.default_rng(seed).integers(0, 256, shape, dtype=np.uint8).astype(dtype)
    lg = s.run(None, {inp.name: x})[0]
    return {"seed": seed, "input_shape": shape,
            "mask_sha256": sha256(lg.argmax(1).astype(np.uint8).tobytes()),
            "logits_sha256": sha256(np.ascontiguousarray(lg).tobytes()),
            "logits_sum": math.fsum(lg.astype(np.float64).ravel().tolist())}
