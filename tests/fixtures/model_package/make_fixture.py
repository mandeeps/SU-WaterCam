"""Regenerate the model_package fixture: a tiny 5-band segmentation-shaped ONNX model, a
"trained" variant, and a signed int4 delta between them.

    python tests/fixtures/model_package/make_fixture.py

expected.json records the result sha256 that test_model_package.py requires on every
platform (x86 CI and the Pi). Only regenerate it on purpose: the committed bytes are the
cross-platform check.
"""
import json
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "tools"))
import ed25519_pure  # noqa: E402
import model_package as mp  # noqa: E402

TEST_KEY_ID = "test-rfc8032-1"     # RFC 8032 TEST 1 key: public, for tests only


def make_model(w):
    init = [numpy_helper.from_array(v, k) for k, v in sorted(w.items())]
    nodes = [helper.make_node("Conv", ["input", "conv1.weight", "conv1.bias"], ["h1"], pads=[1, 1, 1, 1]),
             helper.make_node("Relu", ["h1"], ["h2"]),
             helper.make_node("Mul", ["h2", "gain"], ["h3"]),
             helper.make_node("Conv", ["h3", "head.weight", "head.bias"], ["h4"]),
             helper.make_node("Reshape", ["h4", "shape"], ["logits"])]
    g = helper.make_graph(nodes, "tiny_seg", [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 5, 16, 16])],
                          [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, 2, 16, 16])], init)
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)], producer_name="model_package_fixture")
    m.ir_version = 8
    onnx.checker.check_model(m)
    return m.SerializeToString()


def main():
    rng = np.random.default_rng(7)
    base = {"conv1.weight": rng.normal(0, 0.1, (8, 5, 3, 3)).astype(np.float32),
            "conv1.bias": rng.normal(0, 0.1, 8).astype(np.float32),
            "gain": np.array(1.5, np.float32),                       # 0-d tensor
            "head.weight": rng.normal(0, 0.1, (2, 8, 1, 1)).astype(np.float32),
            "head.bias": np.zeros(2, np.float32),
            "shape": np.array([1, 2, 16, 16], np.int64)}             # never changes
    trained = dict(base)
    trained["conv1.weight"] = base["conv1.weight"] + rng.normal(0, 0.01, (8, 5, 3, 3)).astype(np.float32)
    trained["gain"] = np.array(1.4, np.float32)
    trained["head.weight"] = base["head.weight"] + rng.normal(0, 0.02, (2, 8, 1, 1)).astype(np.float32)
    trained["head.bias"] = np.array([0.05, -0.05], np.float32)
    base_b, trained_b = make_model(base), make_model(trained)
    pkg, result = mp.build_delta(base_b, trained_b, 4, meta={
        "release_id": "seg-g0002", "parent_release": {"release_id": "seg-g0001"},
        "lineage": {"track": "global", "global_base": {"release_id": "seg-g0002"}}})
    secret = bytes.fromhex(ed25519_pure.RFC_TEST1["secret"])
    pkg = mp.sign_package(pkg, secret, TEST_KEY_ID)
    (HERE / "base.onnx").write_bytes(base_b)
    (HERE / "trained.onnx").write_bytes(trained_b)
    (HERE / "delta_int4.wcmu").write_bytes(pkg)
    expected = {"test_key_id": TEST_KEY_ID, "test_public_key": ed25519_pure.RFC_TEST1["public"],
                "base_sha256": mp.sha256(base_b), "result_sha256": mp.sha256(result),
                "result_weights": mp.weights_digest(onnx.load_from_string(result)),
                "generated_with": {"numpy": np.__version__, "onnx": onnx.__version__}}
    (HERE / "expected.json").write_text(json.dumps(expected, indent=1) + "\n")
    print(json.dumps(expected, indent=1))


if __name__ == "__main__":
    main()
