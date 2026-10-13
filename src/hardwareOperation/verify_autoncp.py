"""Compare the exact exported C model with ncps on a synthetic carried-state stream."""

import argparse
import ctypes
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import torch
from export_autoncp import ROOT, load_model, quantized_model


def verify(checkpoint_path, model_dir):
    checkpoint_path, model_dir = Path(checkpoint_path), Path(model_dir).resolve()
    manifest = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
    if (
        hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
        != manifest["checkpoint_sha256"]
    ):
        raise ValueError("export identity does not match this checkpoint")
    reference = quantized_model(
        load_model(torch.load(checkpoint_path, map_location="cpu", weights_only=False)),
        manifest["dimensions"]["WEIGHT_BITS"],
        fuse_gate=manifest.get("optimizations", {}).get("fuse_gate", False),
    )
    source = ROOT / "firmware/model/autoncp.c"
    inputs = (
        np.random.default_rng(20260920).uniform(-1, 1, (128, 16)).astype(np.float32)
    )
    inputs[:8] = 0
    with tempfile.TemporaryDirectory(prefix="autoncp-parity-") as temporary:
        library_path = Path(temporary) / "autoncp.dll"
        subprocess.run(
            [
                "gcc",
                "-std=c99",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-shared",
                "-I",
                str(model_dir),
                "-I",
                str(source.parent),
                str(source),
                "-lm",
                "-o",
                str(library_path),
            ],
            check=True,
        )
        library = ctypes.CDLL(str(library_path))
        pointer = ctypes.POINTER(ctypes.c_float)
        library.autoncp_step.argtypes = [pointer, pointer]
        library.autoncp_step.restype = ctypes.c_bool
        library.autoncp_state.restype = pointer
        library.autoncp_reset()
        errors, state_errors, state = [], [], None
        try:
            with torch.inference_mode():
                for features in inputs:
                    expected, state = reference.forward_with_state(
                        torch.from_numpy(features[None, None]), state
                    )
                    actual = np.empty(10, dtype=np.float32)
                    if not library.autoncp_step(
                        features.ctypes.data_as(pointer), actual.ctypes.data_as(pointer)
                    ):
                        raise ValueError(
                            "C model rejected a valid synthetic feature vector"
                        )
                    errors.append(np.abs(actual - expected.numpy()[0]))
                    c_state = np.ctypeslib.as_array(
                        library.autoncp_state(), shape=(reference.cfc.state_size,)
                    )
                    state_errors.append(
                        float(np.max(np.abs(c_state - state.numpy()[0])))
                    )
        finally:
            # Release the DLL before removing the temporary directory on Windows.
            free = ctypes.windll.kernel32.FreeLibrary
            free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_bool
            if not free(library._handle):
                raise OSError("could not release verification library")
        per_target = np.asarray(errors).max(axis=0)
        state_error = max(state_errors)
        report = {
            "steps": len(inputs),
            "checkpoint_sha256": manifest["checkpoint_sha256"],
            "runtime_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "exported_weights_sha256": hashlib.sha256(
                (model_dir / "autoncp_weights.h").read_bytes()
            ).hexdigest(),
            "per_target_c_vs_ncps_max_abs": per_target.tolist(),
            "max_state_abs_error": state_error,
            "tolerance": 0.0002,
            "passed": bool(np.max(per_target) <= 0.0002 and state_error <= 0.0002),
            "synthetic_only": True,
            "physical_latency_verified": False,
        }
        (model_dir / "host_c_parity.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        if not report["passed"]:
            raise ValueError(f"C/ncps numerical mismatch: {report}")
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    print(json.dumps(verify(args.checkpoint, args.model_dir), indent=2))
