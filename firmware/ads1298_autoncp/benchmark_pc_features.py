"""Measure the existing PC feature path; this is not an ESP32 latency estimate."""

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import platform
import sys
from time import perf_counter_ns
from unittest.mock import patch

import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.hardwareOperation import live_emg
from src.dataflow import feature_extraction as features


def check_block_formulas():
    """Check four 100-point blocks, including all three internal boundaries."""
    rng = np.random.default_rng(20260927)
    thresholds = np.arange(1, 9, dtype=np.float32)
    t = np.arange(1600)[:, None] / 2000
    boundary = np.tile(np.array([-0.5, 0.5, 0, -0.49, 0.49], np.float32), 320)
    cases = {
        "noise": rng.normal(0, 20, (1600, 8)).astype(np.float32),
        "zeros": np.zeros((1600, 8), np.float32),
        "sines": (30 * np.sin(2 * np.pi * t * np.arange(25, 65, 5))).astype(np.float32),
        "threshold_boundary": boundary[:, None] * thresholds[None, :],
    }
    checked = 0
    max_rms_error = 0.0
    for samples in cases.values():
        blocks = samples.reshape(-1, 100, 8)
        square_sums = np.square(blocks).sum(axis=1, dtype=np.float64)
        # Use the reference predicate: zero is not a sign change; equality counts.
        internal, _ = features.zero_crossings(blocks, thresholds)
        left, right = blocks[:-1, -1], blocks[1:, 0]
        boundaries = ((left * right < 0) & (np.abs(left - right) >= thresholds)).astype(np.int32)
        for start in range(len(blocks) - 3):
            window = samples[start * 100:(start + 4) * 100][None]
            reference_rms = features.root_mean_square(window)[0]
            reference_zc = features.zero_crossings(window, thresholds)[0][0]
            block_rms = np.sqrt(square_sums[start:start + 4].sum(axis=0) / 400).astype(np.float32)
            block_zc = internal[start:start + 4].sum(axis=0) + boundaries[start:start + 3].sum(axis=0)
            np.testing.assert_allclose(block_rms, reference_rms, rtol=1e-7, atol=1e-6)
            np.testing.assert_array_equal(block_zc, reference_zc)
            max_rms_error = max(max_rms_error, float(np.max(np.abs(block_rms - reference_rms))))
            checked += 1
    semantics = np.tile(np.array([-0.5, 0.5, 0, -0.49, 0.49], np.float32)[:, None], (1, 8))
    np.testing.assert_array_equal(features.zero_crossings(semantics[None], np.ones(8))[0], np.ones((1, 8)))
    return {
        "cases": list(cases), "windows_checked": checked,
        "pairs_per_window": 4 * 99 + 3, "zc_exact_match": True,
        "rms_max_abs_error_uv": max_rms_error,
        "rms_tolerance": {"rtol": 1e-7, "atol_uv": 1e-6},
        "arithmetic": "float32 squares and comparisons; float64 block sums",
        "scope": "Test formulas only; no optimized production implementation or speed comparison.",
    }


def distribution(ns):
    us = np.asarray(ns, dtype=np.float64) / 1000
    return {
        "count": len(ns), "mean_us": float(us.mean()),
        "p50_us": float(np.percentile(us, 50)),
        "p95_us": float(np.percentile(us, 95)),
        "p99_us": float(np.percentile(us, 99)),
        "max_us": float(us.max()), "sum_us": float(us.sum()),
    }


def benchmark():
    warmup, measured, batch_size = 16, 128, 100
    rng = np.random.default_rng(20260927)
    t = np.arange((warmup + measured) * batch_size)[:, None] / 2000
    samples = (30 * np.sin(2 * np.pi * t * np.arange(25, 65, 5))
               + rng.normal(0, 3, (len(t), 8))).astype(np.float64)
    # Frozen synthetic constants do not stand in for target rest calibration.
    extractor = live_emg.CausalRMSZC(np.arange(1, 9, dtype=np.float32))
    stages = {name: [] for name in ("sosfilt", "sliding_window", "rms", "zc")}
    totals, residuals = [], []
    active = None

    def timed(name, function):
        def wrapped(*args, **kwargs):
            start = perf_counter_ns()
            result = function(*args, **kwargs)
            elapsed = perf_counter_ns() - start
            if active is not None:
                active[name] += elapsed
                active[name + "_calls"] += 1
            return result
        return wrapped

    calls = {name: 0 for name in stages}
    with ExitStack() as stack:
        for module, attribute, name in (
            (live_emg.signal, "sosfilt", "sosfilt"),
            (live_emg, "sliding_window", "sliding_window"),
            (features, "root_mean_square", "rms"),
            (features, "zero_crossings", "zc"),
        ):
            stack.enter_context(patch.object(module, attribute, timed(name, getattr(module, attribute))))
        for batch in range(warmup + measured):
            active = None if batch < warmup else {
                **dict.fromkeys(stages, 0), **{name + "_calls": 0 for name in stages}
            }
            start = perf_counter_ns()
            output, ends = extractor.push(samples[batch * batch_size:(batch + 1) * batch_size])
            elapsed = perf_counter_ns() - start
            if active is not None:
                assert output.shape == (1, 16) and ends.tolist() == [(batch + 1) * batch_size]
                totals.append(elapsed)
                residuals.append(elapsed - sum(active[name] for name in stages))
                for name in stages:
                    stages[name].append(active[name])
                    calls[name] += active[name + "_calls"]
    assert all(count == measured for count in calls.values())
    stages.update(total_push=totals, residual_validation_copy_and_instrumentation=residuals)
    sources = [live_emg.__file__, features.__file__, str(ROOT / "src/dataflow/SwRectify.py")]
    return {
        "scope": "PC host only; not an ESP32 estimate, hardware or end-to-end measurement.",
        "environment": {"python": sys.version, "executable": sys.executable,
                        "numpy": np.__version__, "scipy": scipy.__version__,
                        "platform": platform.platform(), "machine": platform.machine(),
                        "processor": platform.processor(), "timer": "perf_counter_ns"},
        "protocol": {"seed": 20260927, "warmup_batches": warmup,
                     "measured_batches": measured, "batch_samples_per_channel": batch_size,
                     "state_carried": True, "realtime_pacing": False,
                     "thresholds_are_synthetic_not_target_calibration": True,
                     "synthetic_signal": "30 uV sine (25..60 Hz/channel) plus 3 uV Gaussian noise",
                     "instrumentation": "Temporary wrappers around exact existing stage functions; total includes wrapper overhead."},
        "feature_contract": extractor.contract(),
        "stage_calls": calls,
        "timings": {name: distribution(values) for name, values in stages.items()},
        "source_sha256": {str(Path(path).relative_to(ROOT)): hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in sources},
        "limitations": ["128 back-to-back host batches; OS scheduling and warm cache affect results.",
                        "Residual includes all uninstrumented work plus timer/wrapper overhead; it is not copy-only.",
                        "No source normalization, inverse targets, model inference, ADC, transport or rendering."],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checks = check_block_formulas()
    report = benchmark()
    report["block_formula_self_check"] = checks
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "timings": report["timings"], "self_check": checks}, indent=2))
