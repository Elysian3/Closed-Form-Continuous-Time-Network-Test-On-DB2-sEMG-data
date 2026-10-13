"""Independent NumPy INT32 dot reference; ncps retains FP32 CfC dynamics."""

import argparse
import ctypes
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/hardwareOperation"))
import export_autoncp


def quantize_input(values):
    values = np.asarray(values, dtype=np.float32)
    if not np.isfinite(values).all():
        raise ValueError("nonfinite FC input")
    maximum = np.max(np.abs(values))
    scale = np.float32(1) if maximum == 0 else np.maximum(np.float32(maximum / np.float32(127)), np.finfo(np.float32).tiny)
    inverse = np.float32(np.float32(1) / scale)
    scaled = (values * inverse).astype(np.float32)
    rounded = np.trunc((scaled + np.copysign(np.float32(.5), scaled)).astype(np.float32))
    return np.clip(rounded, -127, 127).astype(np.int8), scale


def integer_model(payload):
    model = export_autoncp.load_model(payload)

    def install(layer, effective):
        q, scales = export_autoncp.quantize_weight(effective, 8)
        weights = q.numpy().astype(np.int32)
        bias = layer.bias.detach().numpy().copy()

        def forward(values):
            source = values.detach().numpy()
            if source.shape[0] != 1:
                raise ValueError("reference requires batch size 1")
            quantized, input_scale = quantize_input(source[0])
            dot = weights @ quantized.astype(np.int32)
            restored = (dot.astype(np.float32) * input_scale).astype(np.float32)
            result = (bias + (restored * scales).astype(np.float32)).astype(np.float32)
            return torch.from_numpy(result[None])

        layer.forward = forward

    for cell in model.cfc.rnn_cell._layers:
        for kind in export_autoncp.KINDS:
            layer = getattr(cell, kind)
            effective = layer.weight * cell.sparsity_mask if kind in ("ff1", "ff2") else layer.weight
            install(layer, effective)
        # Effective weights already include the mask. Force ncps through Linear.forward.
        cell.sparsity_mask = None
    install(model.head, model.head.weight)
    return model.eval()


def predictions(model, inputs):
    outputs, states, state = [], [], None
    with torch.inference_mode():
        for row in inputs:
            output, state = model.forward_with_state(torch.from_numpy(row[None, None]), state)
            outputs.append(output.numpy()[0].copy())
            states.append(state.numpy()[0].copy())
    return np.asarray(outputs), np.asarray(states)


def difference(actual, reference):
    delta = actual.astype(np.float64) - reference.astype(np.float64)
    return {"max_abs": np.max(np.abs(delta), axis=0).tolist(),
            "mean_abs": np.mean(np.abs(delta), axis=0).tolist(),
            "rmse": np.sqrt(np.mean(delta * delta, axis=0)).tolist()}


def host_check(checkpoint, model_dir, output, steps=1024):
    if steps < 1:
        raise ValueError("steps must be positive")
    sys.path.insert(0, str(ROOT / "tests"))
    from test_autoncp_firmware import AutoNCPFirmwareTests

    torch.set_num_threads(1)
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    manifest = json.loads((model_dir / "manifest.json").read_text())
    checkpoint_hash = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if checkpoint_hash != manifest["checkpoint_sha256"] or manifest["dimensions"]["WEIGHT_BITS"] != 8:
        raise ValueError("require matching INT8 export")
    rng = np.random.default_rng(20260927)
    time = np.arange(steps, dtype=np.float32)[:, None]
    sequences = {"uniform": rng.uniform(-1, 1, (steps, 16)).astype(np.float32),
                 "zero": np.zeros((steps, 16), np.float32),
                 "smooth": np.sin(time * np.linspace(.01, .09, 16, dtype=np.float32)[None]).astype(np.float32),
                 "large_amplitude": rng.uniform(-8, 8, (steps, 16)).astype(np.float32)}
    result = {"synthetic_only": True, "not_prediction_accuracy": True, "steps_per_sequence": steps,
              "checkpoint_sha256": checkpoint_hash, "target_names": manifest["target_contract"]["target_names"],
              "weights_sha256": hashlib.sha256((model_dir / "autoncp_weights.h").read_bytes()).hexdigest(),
              "model_source_sha256": hashlib.sha256((ROOT / "firmware/model/autoncp.c").read_bytes()).hexdigest(),
              "reference": "independent NumPy int32 dot; ncps FP32 CfC", "sequences": {}}
    helper = AutoNCPFirmwareTests()
    pointer = ctypes.POINTER(ctypes.c_float)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        libraries = []
        try:
            for integer in (False, True):
                folder = Path(temporary) / str(integer)
                folder.mkdir()
                for name in ("autoncp_dims.h", "autoncp_weights.h"):
                    shutil.copyfile(model_dir / name, folder / name)
                libraries.append(helper._library(folder, integer=integer))
            for name, inputs in sequences.items():
                compiled = []
                for library in libraries:
                    library.autoncp_reset()
                    outputs, states = [], []
                    for row in inputs:
                        prediction = np.empty(10, np.float32)
                        if not library.autoncp_step(row.ctypes.data_as(pointer), prediction.ctypes.data_as(pointer)):
                            raise ValueError(f"C rejected {name}")
                        outputs.append(prediction.copy())
                        states.append(np.ctypeslib.as_array(library.autoncp_state(), shape=(manifest["dimensions"]["STATE_SIZE"],)).copy())
                    compiled.append((np.asarray(outputs), np.asarray(states)))
                reference, reference_states = predictions(integer_model(payload), inputs)
                float_outputs, float_states = compiled[0]
                integer_outputs, integer_states = compiled[1]
                parity_output = float(np.max(np.abs(integer_outputs - reference)))
                parity_state = float(np.max(np.abs(integer_states - reference_states)))
                output_errors = np.max(np.abs(integer_outputs - reference), axis=1)
                state_errors = np.max(np.abs(integer_states - reference_states), axis=1)
                failed = np.flatnonzero((output_errors > .0002) | (state_errors > .0002))
                evidence = output.parent / f"{output.stem}_{name}.npz"
                np.savez_compressed(evidence, inputs=inputs, float_outputs=float_outputs,
                                    float_states=float_states, integer_outputs=integer_outputs,
                                    integer_states=integer_states, reference_outputs=reference,
                                    reference_states=reference_states)
                result["sequences"][name] = {
                    "input_sha256": hashlib.sha256(inputs.astype("<f4").tobytes()).hexdigest(),
                    "integer_vs_float_per_target": difference(integer_outputs, float_outputs),
                    "integer_vs_float_max_state_error": float(np.max(np.abs(integer_states - float_states))),
                    "integer_vs_float_final_state_error": float(np.max(np.abs(integer_states[-1] - float_states[-1]))),
                    "c_vs_reference_per_target": difference(integer_outputs, reference),
                    "c_vs_reference_max_state_error": parity_state,
                    "c_vs_reference_final_state_error": float(np.max(np.abs(integer_states[-1] - reference_states[-1]))),
                    "first_parity_failure_step_zero_based": int(failed[0]) if failed.size else None,
                    "raw_arrays_file": evidence.name,
                    "raw_arrays_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    "parity_at_2e_4": parity_output <= .0002 and parity_state <= .0002}
                if name == "zero":
                    # Test-only state injection isolates one-step arithmetic from recursive drift.
                    library = libraries[1]
                    library.autoncp_reset()
                    controlled_outputs, controlled_states = [], []
                    for index, row in enumerate(inputs):
                        if index:
                            ctypes.memmove(library.autoncp_state(), reference_states[index - 1].ctypes.data,
                                           reference_states[index - 1].nbytes)
                        prediction = np.empty(10, np.float32)
                        if not library.autoncp_step(row.ctypes.data_as(pointer), prediction.ctypes.data_as(pointer)):
                            raise ValueError("controlled zero step rejected")
                        controlled_outputs.append(prediction.copy())
                        controlled_states.append(np.ctypeslib.as_array(library.autoncp_state(), shape=reference_states.shape[1:]).copy())
                    controlled_outputs, controlled_states = np.asarray(controlled_outputs), np.asarray(controlled_states)
                    controlled_output_error = float(np.max(np.abs(controlled_outputs - reference)))
                    controlled_state_error = float(np.max(np.abs(controlled_states - reference_states)))
                    result["sequences"][name]["one_step_reference_state_injection"] = {
                        "not_free_running": True, "max_output_error": controlled_output_error,
                        "max_state_error": controlled_state_error,
                        "parity_at_2e_4": max(controlled_output_error, controlled_state_error) <= .0002}
                    controlled_path = output.parent / f"{output.stem}_zero_controlled.npz"
                    np.savez_compressed(controlled_path, outputs=controlled_outputs, states=controlled_states)
                    result["sequences"][name]["one_step_reference_state_injection"].update(
                        raw_arrays_file=controlled_path.name,
                        raw_arrays_sha256=hashlib.sha256(controlled_path.read_bytes()).hexdigest())
                print(name, "C/reference max output", parity_output, "max state", parity_state, flush=True)
        finally:
            for library in libraries:
                helper._unload(library)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=1024)
    args = parser.parse_args()
    result = host_check(args.checkpoint, args.model_dir, args.output, args.steps)
    if any(not item["parity_at_2e_4"] for item in result["sequences"].values()):
        sys.exit(1)
