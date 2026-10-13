"""Export wired AutoNCP CfC checkpoints for the independent ESP32 runtime.

Weights stay in read-only flash. This export does not establish device input
units, calibrate ZC thresholds, or authorize inference from electrode data.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import torch
from export_weights import _array_literal, quantize_per_channel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "deep learning"))
from train import build_cfc_regressor

KINDS = ("ff1", "ff2", "time_a", "time_b")
J10_CHANNELS = [2, 3, 5, 6, 8, 9, 12, 13, 16, 17]


def _stats(checkpoint, key, width):
    stats = checkpoint.get(key, {})
    center = np.asarray(stats.get("center"), dtype=np.float32)
    scale = np.asarray(stats.get("scale"), dtype=np.float32)
    mu = float(stats.get("mu", 0))
    if (
        stats.get("method") != "mu_law"
        or center.shape != (width,)
        or scale.shape != (width,)
        or not np.isfinite(center).all()
        or not np.isfinite(scale).all()
        or (scale <= 0).any()
        or not np.isfinite(mu)
        or mu <= 0
    ):
        raise ValueError(f"invalid {key}; require finite mu-law statistics")
    return center, scale, mu


def load_model(checkpoint):
    """Strictly reconstruct the checkpoint; reject incompatible signal contracts."""
    config = checkpoint["config"]
    expected = {
        "model_family": "autoncp_cfc_linear",
        "emg_channels": list(range(1, 9)),
        "feature_order": ["rms", "zc"],
        "window_ms": 200.0,
        "stride_ms": 50.0,
        "target_offset_samples": 0,
        "target_mapping": "joint_angles10",
        "feature_normalization": "mu_law",
        "target_normalization": "mu_law",
        "stateful": True,
    }
    for key, value in expected.items():
        actual = config.get(key)
        if isinstance(actual, tuple):
            actual = list(actual)
        if actual != value:
            raise ValueError(f"unsupported {key}: {actual!r}; expected {value!r}")
    contract = checkpoint["target_contract"]
    if (
        contract.get("mapping_name") != "joint_angles10"
        or contract.get("output_dim") != 10
        or contract.get("source_channels_one_based") != J10_CHANNELS
        or len(contract.get("target_names", [])) != 10
    ):
        raise ValueError("checkpoint must carry the ordered J10 target contract")
    hidden = config["hidden_units"]
    if type(hidden) is not int or not 12 <= hidden <= 768:
        raise ValueError("unsupported hidden_units; maximum reviewed size is 768")
    model = build_cfc_regressor(
        input_dim=16,
        output_dim=10,
        hidden_units=hidden,
        model_family="autoncp_cfc_linear",
        cfc_dropout=0.0,
    )
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    if any(not torch.isfinite(value).all() for value in model.state_dict().values()):
        raise ValueError("checkpoint contains nonfinite parameters")
    for cell in model.cfc.rnn_cell._layers:
        if cell.mode != "default" or cell.backbone_layers != 0:
            raise ValueError(
                "only wired default-mode CfC without a backbone is supported"
            )
        if not torch.all((cell.sparsity_mask == 0) | (cell.sparsity_mask == 1)):
            raise ValueError("expected binary absolute wiring masks")
    _stats(checkpoint, "feature_normalization_stats", 16)
    _stats(checkpoint, "target_normalization_stats", 10)
    return model.eval()


def quantize_weight(weight, weight_bits=16):
    if weight_bits == 8:
        return quantize_per_channel(weight)
    if weight_bits != 16:
        raise ValueError("weight_bits must be 8 or 16")
    weight = weight.detach().float()
    maximum = weight.abs().amax(dim=1)
    maximum = torch.where(maximum < 1e-10, torch.ones_like(maximum), maximum)
    scales = (maximum / 32767.0).numpy().astype(np.float32)
    q = (
        torch.round(weight / torch.from_numpy(scales[:, None]))
        .clamp(-32767, 32767)
        .to(torch.int16)
    )
    return q, scales


def quantized_model(model, weight_bits=16, fuse_gate=False):
    """Independent ncps reference using exported dequantized effective weights."""
    if fuse_gate and weight_bits != 8:
        raise ValueError("fused gate requires INT8 base weights")
    reference = copy.deepcopy(model).eval()
    with torch.no_grad():
        for cell in reference.cfc.rnn_cell._layers:
            for kind in KINDS:
                layer = getattr(cell, kind)
                effective = (
                    layer.weight * cell.sparsity_mask
                    if kind in ("ff1", "ff2")
                    else layer.weight
                )
                q, scales = quantize_weight(effective, weight_bits)
                layer.weight.copy_(q.float() * torch.from_numpy(scales[:, None]))
            if fuse_gate:
                gate = cell.time_a.weight + cell.time_b.weight
                q, scales = quantize_weight(gate, 16)
                cell.time_a.weight.copy_(q.float() * torch.from_numpy(scales[:, None]))
                cell.time_a.bias.add_(cell.time_b.bias)
                cell.time_b.weight.zero_()
                cell.time_b.bias.zero_()
        q, scales = quantize_weight(reference.head.weight, weight_bits)
        reference.head.weight.copy_(q.float() * torch.from_numpy(scales[:, None]))
    return reference


def _c_array(name, values, *, integer=False, weight_bits=16, aligned=False):
    values = np.asarray(values)
    dtype, fmt = (f"int{weight_bits}_t", "{:d}") if integer else ("float", "{:.9e}f")
    align = " __attribute__((aligned(16)))" if aligned else ""
    return f"static const {dtype} {name}[{values.size}]{align} =\n{_array_literal(values, fmt)};\n"


def _predictions(model, inputs):
    state, outputs = None, []
    with torch.inference_mode():
        for row in inputs:
            prediction, state = model.forward_with_state(
                torch.from_numpy(row[None, None]), state
            )
            outputs.append(prediction.numpy()[0])
    return np.asarray(outputs)


def export_model(
    checkpoint,
    output_dir,
    checkpoint_sha256="synthetic-test",
    weight_bits=16,
    pad_rows=False,
    fuse_gate=False,
):
    """Write a numerical-validation bundle; synthetic fixtures use this entry point."""
    if not checkpoint_sha256 or any(
        c not in "0123456789abcdefghijklmnopqrstuvwxyz-" for c in checkpoint_sha256
    ):
        raise ValueError("checkpoint identity must be a safe digest or test label")
    if fuse_gate and weight_bits != 8:
        raise ValueError("fused gate requires INT8 base weights")
    model = load_model(checkpoint)
    baseline = quantized_model(model, weight_bits)
    reference = (
        quantized_model(model, weight_bits, fuse_gate=fuse_gate)
        if fuse_gate
        else baseline
    )
    cells = model.cfc.rnn_cell._layers
    sizes = [cell.hidden_size for cell in cells]
    concats = [cell.input_size + cell.hidden_size for cell in cells]
    dims = {
        "INPUT_SIZE": 16,
        "OUTPUT_SIZE": 10,
        "STATE_SIZE": sum(sizes),
        "MAX_CONCAT": max(concats),
        "LAYER_COUNT": len(cells),
        "MAX_UNITS": max(sizes),
        "WEIGHT_BITS": weight_bits,
    }
    flags = {"PADDED_ROWS": int(pad_rows), "FUSED_GATE_BITS": 16 if fuse_gate else 0}
    dims_text = (
        "// Generated; weights are in flash, state/scratch in RAM.\n#pragma once\n"
    )
    dims_text += "\n".join(
        f"#define AUTONCP_{key} {value}" for key, value in dims.items()
    )
    dims_text += "\n" + "\n".join(
        f"#define AUTONCP_{key} {value}" for key, value in flags.items()
    )
    dims_text += f'\n#define AUTONCP_CHECKPOINT_SHA256 "{checkpoint_sha256}"\n'
    lines = ["// Generated wired CfC; masks applied only to ff1/ff2.\n#pragma once\n"]
    descriptors, offset, weight_bytes, float_bytes = [], 0, 0, 0
    row_strides = []
    for index, cell in enumerate(cells):
        names = []
        inputs = cell.input_size + cell.hidden_size
        stride = (inputs + 15) & ~15 if pad_rows else inputs
        row_strides.append(stride)
        for kind in KINDS:
            if fuse_gate and kind == "time_b":
                names.append("0")
                continue
            layer = getattr(cell, kind)
            effective = (
                layer.weight * cell.sparsity_mask
                if kind in ("ff1", "ff2")
                else layer.weight
            )
            if fuse_gate and kind == "time_a":
                base_cell = baseline.cfc.rnn_cell._layers[index]
                q, scales = quantize_weight(
                    base_cell.time_a.weight + base_cell.time_b.weight, 16
                )
                bias = (layer.bias + cell.time_b.bias).detach().numpy()
                bits = 16
            else:
                q, scales = quantize_weight(effective, weight_bits)
                bias = layer.bias.detach().numpy()
                bits = weight_bits
            if pad_rows:
                q = torch.nn.functional.pad(q, (0, stride - inputs))
            name = f"model_l{index}_{kind}"
            names.append(name)
            lines += [
                _c_array(
                    name + "_weight",
                    q.numpy(),
                    integer=True,
                    weight_bits=bits,
                    aligned=pad_rows,
                ),
                _c_array(name + "_scale", scales),
                _c_array(name + "_bias", bias),
            ]
            weight_bytes += q.numel() * (bits // 8)
            float_bytes += 8 * cell.hidden_size
        pointers = [
            "{"
            + ", ".join(name + suffix if name != "0" else "0" for name in names)
            + "}"
            for suffix in ("_weight", "_scale", "_bias")
        ]
        descriptors.append(
            "{"
            + f"{cell.input_size}, {cell.hidden_size}, {offset}, "
            + ", ".join(pointers)
            + "}"
        )
        offset += cell.hidden_size
    lines.append(
        "static const autoncp_layer_t model_layers[AUTONCP_LAYER_COUNT] = {\n"
        + ",\n".join(descriptors)
        + "\n};\n"
    )
    q, scales = quantize_weight(model.head.weight, weight_bits)
    head_stride = (
        (model.head.in_features + 15) & ~15 if pad_rows else model.head.in_features
    )
    if pad_rows:
        q = torch.nn.functional.pad(q, (0, head_stride - model.head.in_features))
    lines += [
        _c_array(
            "model_head_weight",
            q.numpy(),
            integer=True,
            weight_bits=weight_bits,
            aligned=pad_rows,
        ),
        _c_array("model_head_scale", scales),
        _c_array("model_head_bias", model.head.bias.detach().numpy()),
    ]
    weight_bytes += q.numel() * (weight_bits // 8)
    float_bytes += 8 * model.head.out_features
    for group, width in (("feature", 16), ("target", 10)):
        center, scale, mu = _stats(checkpoint, f"{group}_normalization_stats", width)
        lines += [
            _c_array(f"model_{group}_center", center),
            _c_array(f"model_{group}_scale", scale),
            f"static const float model_{group}_mu = {mu:.9e}f;\n",
        ]
        float_bytes += 8 * width + 4

    # A fixed normalized-input stream exercises state carry without real data.
    inputs = (
        np.random.default_rng(20260920).uniform(-1, 1, (128, 16)).astype(np.float32)
    )
    inputs[:8] = 0
    fp32 = _predictions(model, inputs)
    baseline_outputs = _predictions(baseline, inputs)
    selected_outputs = (
        _predictions(reference, inputs) if fuse_gate else baseline_outputs
    )
    error = np.abs(fp32 - selected_outputs)
    boot_inputs = inputs[:12]
    boot_outputs = selected_outputs[:12]
    boot = (
        "// Synthetic normalized inputs only; no electrode-readiness claim.\n#pragma once\n"
        + f"#define AUTONCP_SELF_TEST_STEPS {len(boot_inputs)}\n"
        + _c_array("model_self_test_inputs", boot_inputs)
        + _c_array("model_self_test_outputs", boot_outputs)
    )
    provenance = checkpoint.get("template_adaptation_provenance", {})
    manifest = {
        "schema_version": 1,
        "checkpoint_sha256": checkpoint_sha256,
        "model_family": "autoncp_cfc_linear",
        "dimensions": dims,
        "optimizations": {
            "pad_rows": bool(pad_rows),
            "fuse_gate": bool(fuse_gate),
            "row_strides": {"layers": row_strides, "head": head_stride},
        },
        "layer_sizes": sizes,
        "time_step": 1.0,
        "time_step_note": "ncps trained default; not 0.05 wall-clock seconds",
        "quantization": (
            f"symmetric INT{weight_bits} per output row; "
            + ("fused gate INT16; " if fuse_gate else "")
            + "float32 activations/biases"
        ),
        "weight_bytes": weight_bytes,
        "float_parameter_bytes": float_bytes,
        "parameter_bytes": weight_bytes + float_bytes,
        "parameter_storage": "const flash rodata; do not copy all weights into SRAM",
        "target_contract": checkpoint["target_contract"],
        "adaptation_arm": provenance.get("arm", "source_pretrain"),
        "adaptation_subject": provenance.get("protocol", {}).get("target_subject"),
        "device_input_status": "blocked_unit_evidence_and_target_zc_thresholds",
        "synthetic_quantization_check": {
            "steps": len(inputs),
            "input": "seed20260920 uniform[-1,1], first8 rows zero",
            "per_target_max_abs_normalized_error": error.max(axis=0).tolist(),
            "per_target_mean_abs_normalized_error": error.mean(axis=0).tolist(),
            "limitation": "numerical quantization comparison; not prediction accuracy or physical latency",
        },
    }
    if fuse_gate:
        baseline_error = np.abs(fp32 - baseline_outputs)
        fusion_error = np.abs(baseline_outputs - selected_outputs)
        manifest["synthetic_quantization_check"].update(
            {
                "per_target_baseline_int8_max_abs_normalized_error": baseline_error.max(
                    axis=0
                ).tolist(),
                "per_target_baseline_int8_mean_abs_normalized_error": baseline_error.mean(
                    axis=0
                ).tolist(),
                "per_target_fused_vs_baseline_max_abs_normalized_error": fusion_error.max(
                    axis=0
                ).tolist(),
                "per_target_fused_vs_baseline_mean_abs_normalized_error": fusion_error.mean(
                    axis=0
                ).tolist(),
            }
        )
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in (
        ("autoncp_dims.h", dims_text),
        ("autoncp_weights.h", "\n".join(lines)),
        ("autoncp_self_test.h", boot),
        ("manifest.json", json.dumps(manifest, indent=2)),
    ):
        (out / name).write_text(text + "\n", encoding="utf-8")
    return manifest


def export_checkpoint(
    checkpoint_path, output_dir, weight_bits=16, pad_rows=False, fuse_gate=False
):
    path = Path(checkpoint_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    return export_model(
        checkpoint,
        output_dir,
        checkpoint_sha256=digest,
        weight_bits=weight_bits,
        pad_rows=pad_rows,
        fuse_gate=fuse_gate,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--weight-bits", type=int, choices=(8, 16), default=16)
    parser.add_argument("--pad-rows", action="store_true")
    parser.add_argument("--fuse-gate", action="store_true")
    args = parser.parse_args()
    torch.set_num_threads(1)
    print(
        json.dumps(
            export_checkpoint(
                args.checkpoint,
                args.output_dir,
                args.weight_bits,
                pad_rows=args.pad_rows,
                fuse_gate=args.fuse_gate,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
