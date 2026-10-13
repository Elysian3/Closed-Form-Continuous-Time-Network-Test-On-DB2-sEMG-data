"""Prepare golden vectors and capture timings without shadowing stdlib profile."""

import argparse
import hashlib
import json
import math
import re
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEPS, WARMUP = 128, 16
KINDS = ("ff1", "ff2", "time_a", "time_b")


def stats(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty timing series")
    return {"n": len(ordered), "mean_us": sum(ordered) / len(ordered),
            "min_us": ordered[0], "p50_us": ordered[math.ceil(len(ordered) * .5) - 1],
            "p95_us": ordered[math.ceil(len(ordered) * .95) - 1],
            "p99_us": ordered[math.ceil(len(ordered) * .99) - 1], "max_us": ordered[-1]}


def prepare(checkpoint, model_dir, folder, integer_fc=False):
    import numpy as np
    import torch
    sys.path.insert(0, str(ROOT / "src/hardwareOperation"))
    from export_autoncp import load_model, quantized_model

    manifest = json.loads((model_dir / "manifest.json").read_text())
    checkpoint_hash = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if checkpoint_hash != manifest["checkpoint_sha256"]:
        raise ValueError("checkpoint differs from exported weights")
    torch.set_num_threads(1)
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    optimizations = manifest.get("optimizations", {})
    fused = optimizations.get("fuse_gate", False)
    model = quantized_model(load_model(payload), manifest["dimensions"]["WEIGHT_BITS"], fuse_gate=fused)
    float_reference = model
    if integer_fc:
        if fused:
            raise ValueError("integer FC does not support the fused INT16 gate")
        if manifest["dimensions"]["WEIGHT_BITS"] != 8:
            raise ValueError("integer FC requires INT8 weights")
        from integer_reference import integer_model
        model = integer_model(payload)
    inputs = np.random.default_rng(20260920).uniform(-1, 1, (STEPS, 16)).astype(np.float32)
    inputs[:8] = 0
    outputs, state = [], None
    with torch.inference_mode():
        for values in inputs:
            output, state = model.forward_with_state(torch.from_numpy(values[None, None]), state)
            outputs.append(output.numpy()[0])
    outputs = np.asarray(outputs, dtype=np.float32)

    def inverse(values, normalization):
        mu = normalization["mu"]
        expanded = np.sign(values) * np.expm1(np.abs(values) * np.log1p(mu)) / mu
        return (expanded * np.asarray(normalization["scale"]) + normalization["center"]).astype(np.float32)

    arrays = {"profile_inputs": inputs, "profile_outputs": outputs,
              "profile_features": inverse(inputs, payload["feature_normalization_stats"]),
              "profile_targets": inverse(outputs, payload["target_normalization_stats"]),
              "profile_final_state": state.numpy()[0]}
    corpus = hashlib.sha256(b"".join(a.astype("<f4").tobytes() for a in arrays.values())).hexdigest()
    header = f'#pragma once\n#define PROFILE_STEPS {STEPS}\n#define PROFILE_WARMUP {WARMUP}\n#define PROFILE_CORPUS_SHA256 "{corpus}"\n'
    for name, array in arrays.items():
        if not np.isfinite(array).all():
            raise ValueError("nonfinite golden fixture")
        dimensions = "".join(f"[{size}]" for size in array.shape)
        rows = array if array.ndim == 2 else [array]
        literals = [", ".join(f"{float(v):.9e}f" for v in row) for row in rows]
        body = ",\n".join("{" + row + "}" for row in literals) if array.ndim == 2 else literals[0]
        header += f"static const float {name}{dimensions} = {{\n{body}\n}};\n"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile_vectors.h").write_text(header, encoding="utf-8")
    identity = {"checkpoint_sha256": checkpoint_hash, "weight_bits": manifest["dimensions"]["WEIGHT_BITS"],
                "weights_sha256": hashlib.sha256((model_dir / "autoncp_weights.h").read_bytes()).hexdigest(),
                "header_sha256": hashlib.sha256((folder / "profile_vectors.h").read_bytes()).hexdigest(), "corpus_sha256": corpus,
                "seed": 20260920, "distribution": "uniform[-1,1], first 8 rows zero",
                "steps": STEPS, "warmup": WARMUP,
                "kernel_kind": "int8_scalar" if integer_fc else "fp32",
                "reference": "independent int32 dot plus FP32 CfC" if integer_fc else "same quantized ncps model as exporter",
                "target_names": manifest["target_contract"]["target_names"],
                "numpy": np.__version__, "torch": torch.__version__}
    identity["optimizations"] = optimizations
    if integer_fc:
        from integer_reference import predictions, difference
        reference_outputs, reference_states = predictions(float_reference, inputs)
        identity["integer_vs_float_per_target"] = difference(outputs, reference_outputs)
        identity["integer_vs_float_final_state_error"] = float(np.max(np.abs(state.numpy()[0] - reference_states[-1])))
        boot = "#pragma once\n#define AUTONCP_SELF_TEST_STEPS 12\n"
        for name, values in (("model_self_test_inputs", inputs[:12]), ("model_self_test_outputs", outputs[:12])):
            body = ", ".join(f"{float(v):.9e}f" for v in values.ravel())
            boot += f"static const float {name}[{values.size}] = {{{body}}};\n"
        (folder / "integer_self_test.h").write_text(boot, encoding="utf-8")
        identity["self_test_header_sha256"] = hashlib.sha256((folder / "integer_self_test.h").read_bytes()).hexdigest()
    (folder / "vectors.json").write_text(json.dumps(identity, indent=2) + "\n")
    return identity


def parse_capture(raw):
    text = raw.decode("utf-8", errors="replace")
    if "PROFILE_DONE" not in text:
        raise ValueError("benchmark incomplete; raw log retained")
    text = text.split("PROFILE_DONE", 1)[0]
    meta = re.search(r"PROFILE_META steps=(\d+) warmup=(\d+) instrumented=(\d+) corpus=([a-f0-9]{64})", text)
    memory = re.search(r"PROFILE_MEMORY weight=(\S+) physical=(\S+) target=(FLASH|PSRAM) psram=(\d+)", text)
    model = re.search(r"AutoNCP sha256=([a-f0-9]{64}) weight_bits=(\d+) states=(\d+) outputs=(\d+)", text)
    kernel = re.search(r"PROFILE_KERNEL kind=(fp32|int8_scalar|int8_simd)", text)
    variant = re.search(r"PROFILE_VARIANT padded=([01]) fused_gate_bits=(0|16) blocked=([01])", text)
    if "PROFILE_VARIANT" in text and not variant:
        raise ValueError("invalid compute variant identity")
    if not (meta and memory and model):
        raise ValueError("missing benchmark identity")
    if kernel and kernel[1] == "int8_simd" and not re.search(
        r"SIMD_DOT_TEST lengths=1,10,15,16,17,31,32,313,471,758 patterns=3 result=pass", text
    ):
        raise ValueError("missing native SIMD dot self-test")
    steps, warmup, instrumented = map(int, meta.groups()[:3])
    if (steps, warmup) != (STEPS, WARMUP):
        raise ValueError("unexpected corpus dimensions")
    modes = {}
    for mode, index, total, fc, act, head, quant in re.findall(
        r"PROFILE_STEP mode=(control|stages) i=(\d+) total=(\d+) fc=([\d,]+) act=([\d,]+) head=(\d+)(?: quant=([\d,]+))?", text
    ):
        fc, act = list(map(int, fc.split(","))), list(map(int, act.split(",")))
        has_quant = bool(quant)
        quant = list(map(int, quant.split(","))) if quant else [0] * 4
        if len(fc) != 12 or len(act) != 3 or len(quant) != 4:
            raise ValueError("wrong stage count")
        row = {"i": int(index), "total": int(total), "head": int(head),
               **{f"l{layer}_{kind}": fc[layer * 4 + k] for layer in range(3) for k, kind in enumerate(KINDS)},
               **{f"l{layer}_activation": value for layer, value in enumerate(act)}}
        if has_quant:
            row.update({f"quantize_{layer}": value for layer, value in enumerate(quant)})
            row["quantize_sum"] = sum(quant)
        row["fc_sum"] = sum(fc) + int(head)
        row["activation_sum"] = sum(act)
        row["unattributed_measured"] = int(total) - sum(fc) - sum(act) - int(head) - sum(quant)
        if row["unattributed_measured"] < 0:
            raise ValueError("stage intervals exceed whole-step time")
        modes.setdefault(mode, []).append(row)
    if set(modes) != ({"control", "stages"} if instrumented else {"control"}):
        raise ValueError("missing or unexpected timing mode")
    numeric = {}
    for mode, state, targets in re.findall(r"PROFILE_NUMERIC mode=(control|stages) state=(\S+) targets=(\S+)", text):
        targets = list(map(float, targets.split(",")))
        state = float(state)
        if len(targets) != 10 or any(not math.isfinite(v) or v > .0002 or v < 0 for v in targets + [state]):
            raise ValueError("numerical parity failed")
        numeric[mode] = {"per_target_max_abs_error": targets, "final_state_max_abs_error": state}
    for mode, rows in modes.items():
        if [row["i"] for row in rows] != list(range(steps)) or mode not in numeric:
            raise ValueError("incomplete or duplicated sequence")
    aux = [{"i": int(index), **dict(zip(("normalize", "inverse", "rms_reference", "zc_reference", "pack7"), map(int, values.split(","))))}
           for index, values in re.findall(r"PROFILE_AUX i=(\d+) us=([\d,]+)", text)]
    auxiliary_ok = re.search(r"PROFILE_AUX_NUMERIC normalize=(\S+) inverse_relative=(\S+) rms_zc=pass packet=pass", text)
    if [row["i"] for row in aux] != list(range(steps)) or any(len(row) != 6 for row in aux) or not auxiliary_ok:
        raise ValueError("auxiliary benchmark incomplete")
    if any(not math.isfinite(float(v)) or float(v) > .0002 or float(v) < 0 for v in auxiliary_ok.groups()):
        raise ValueError("auxiliary numerical check failed")
    clock = re.search(r"PROFILE_CLOCK n=256 sum=(\d+) max=(\d+)", text)
    if not clock:
        raise ValueError("missing clock calibration")
    return {"steps": steps, "warmup": warmup, "instrumented": bool(instrumented), "corpus_sha256": meta[4],
            **({"kernel_kind": kernel[1]} if kernel else {}),
            **({"variant": dict(zip(("padded", "fused_gate_bits", "blocked"), map(int, variant.groups())))} if variant else {}),
            "checkpoint_sha256": model[1], "weight_bits": int(model[2]), "state_size": int(model[3]),
            "weight_address": memory[1], "weight_physical_address": memory[2], "weight_target": memory[3],
            "initialized_psram_bytes": int(memory[4]), "clock_pair_mean_us": int(clock[1]) / 256,
            "clock_pair_max_us": int(clock[2]), "numeric": numeric, "modes": modes, "auxiliary": aux}


def check_variant(result, vectors, blocked=False):
    """Fresh board captures require explicit identity; archived parsers remain compatible."""
    optimizations = vectors.get("optimizations", {})
    expected = {"padded": int(optimizations.get("pad_rows", False)),
                "fused_gate_bits": 16 if optimizations.get("fuse_gate", False) else 0,
                "blocked": int(blocked)}
    if result.get("variant") != expected:
        raise ValueError("missing or mismatched board compute variant")


def capture(args):
    import serial
    from esptool.reset import HardReset

    vectors = json.loads((args.vectors / "vectors.json").read_text())
    expected_kernel = args.kernel or vectors.get("kernel_kind", "fp32")
    args.output.mkdir(parents=True, exist_ok=True)
    if list(args.output.glob("boot_*.log")) or list(args.output.glob("boot_*.json")):
        raise ValueError("choose a fresh capture directory; existing boots must not be pooled with a new run")
    for run in range(1, args.runs + 1):
        raw, completed = bytearray(), None
        with serial.Serial(port=None, baudrate=115200, timeout=.2) as connection:
            connection.dtr = connection.rts = False
            connection.port = args.port
            connection.open()
            connection.reset_input_buffer()
            HardReset(connection, uses_usb=True)()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and len(raw) < 262144:
                raw.extend(connection.read(max(1, connection.in_waiting)))
                if b"PROFILE_FAILED" in raw:
                    break
                if b"PROFILE_DONE" in raw and completed is None:
                    completed = time.monotonic()
                if completed is not None and time.monotonic() - completed > 1:
                    break
        path = args.output / f"boot_{run:02}"
        path.with_suffix(".log").write_bytes(raw)
        result = parse_capture(raw)
        if result["corpus_sha256"] != vectors["corpus_sha256"] or result["checkpoint_sha256"] != vectors["checkpoint_sha256"]:
            raise ValueError("board fixture/model identity mismatch")
        if result["weight_bits"] != vectors["weight_bits"]:
            raise ValueError("board weight precision differs from golden reference")
        check_variant(result, vectors, args.blocked_fc)
        if result["weight_target"].lower() != args.storage:
            raise ValueError("MMU weight backing differs from requested storage")
        if result.get("kernel_kind", "fp32") != expected_kernel:
            raise ValueError("board kernel differs from requested arithmetic")
        if (expected_kernel != "fp32") != (vectors.get("kernel_kind", "fp32") != "fp32"):
            raise ValueError("golden reference arithmetic differs from requested kernel")
        result.update(captured_at=datetime.now(timezone.utc).isoformat(), raw_sha256=hashlib.sha256(raw).hexdigest())
        path.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"run": run, "storage": args.storage, "instrumented": result["instrumented"],
                          "control": stats([r["total"] for r in result["modes"]["control"][WARMUP:]])}), flush=True)
    return summarize(args.output)


def summarize(folder):
    runs = [json.loads(p.read_text()) for p in sorted(folder.glob("boot_*.json"))]
    if not runs:
        raise ValueError("no completed runs")
    for path, run in zip(sorted(folder.glob("boot_*.json")), runs):
        raw = path.with_suffix(".log").read_bytes()
        if hashlib.sha256(raw).hexdigest() != run["raw_sha256"]:
            raise ValueError("raw capture hash changed")
        if any(run[key] != value for key, value in parse_capture(raw).items()):
            raise ValueError("parsed measurements differ from raw capture")
    identity = {(r["corpus_sha256"], r["checkpoint_sha256"], r["weight_target"], r["instrumented"], r.get("kernel_kind", "fp32"),
                 tuple(sorted(r.get("variant", {}).items()))) for r in runs}
    if len(identity) != 1:
        raise ValueError("cannot pool different configurations")
    result = {"runs": len(runs), "quantile_rule": "nearest rank ceil(p*n)", "units": "microseconds",
              "observations_per_run": STEPS - WARMUP, "synthetic_only": True,
              "kernel_kind": runs[0].get("kernel_kind", "fp32"),
              "variant": runs[0].get("variant", {}),
              "physical_end_to_end_verified": False, "modes": {}, "auxiliary": {}, "per_boot": []}
    for mode in runs[0]["modes"]:
        rows = [row for run in runs for row in run["modes"][mode][WARMUP:]]
        result["modes"][mode] = {key: stats([row[key] for row in rows]) for key in rows[0] if key != "i"}
    rows = [row for run in runs for row in run["auxiliary"][WARMUP:]]
    result["auxiliary"] = {key: stats([row[key] for row in rows]) for key in rows[0] if key != "i"}
    for run in runs:
        result["per_boot"].append({mode: stats([row["total"] for row in data[WARMUP:]]) for mode, data in run["modes"].items()})
    result["per_target_max_abs_error"] = [max(r["numeric"][mode]["per_target_max_abs_error"][i] for r in runs for mode in r["numeric"]) for i in range(10)]
    result["final_state_max_abs_error"] = max(v["final_state_max_abs_error"] for r in runs for v in r["numeric"].values())
    (folder / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def waveform_packets(buffer):
    """Benchmark-only decoder; same framing and finite checks as ui/acquisition.js."""
    packets, errors = [], 0
    while buffer:
        if buffer[0] != 0xa5:
            del buffer[0]
            errors += 1
            continue
        if len(buffer) < 4:
            break
        length, kind = buffer[1:3]
        valid = length == 4 if kind == 0x10 else kind == 0x11 and 34 <= length <= 226 and (length - 2) % 32 == 0
        if not valid or buffer[3] != length ^ kind:
            del buffer[0]
            errors += 1
            continue
        if len(buffer) < length + 3:
            break
        if buffer[length + 2] != 0x5a:
            del buffer[0]
            errors += 1
            continue
        payload = bytes(buffer[4:length + 2])
        del buffer[:length + 3]
        if kind == 0x11 and not all(math.isfinite(v) for v in struct.unpack(f"<{len(payload) // 4}f", payload)):
            errors += 1
            continue
        if kind == 0x10 and (payload[0] > 2 or payload[1] > 6):
            errors += 1
            continue
        packets.append((kind, payload))
    return packets, errors


def transport_metrics(arrivals, counts, elapsed):
    if not counts or sum(counts) == 0:
        raise ValueError("START sent but no complete waveform scan received")
    result = {"elapsed_seconds": elapsed, "packets": len(counts), "scans": sum(counts),
              "first_packet_us": arrivals[0], "packet_arrivals_us": arrivals,
              "packet_scan_counts": counts, "scans_per_elapsed_second": sum(counts) / elapsed,
              "arrival_clock": "host read completion; packets in the same read share one timestamp"}
    if len(arrivals) > 1:
        result["host_packet_interval"] = stats([b - a for a, b in zip(arrivals, arrivals[1:])])
        span = (arrivals[-1] - arrivals[0]) / 1e6
        result["scan_rate_between_first_last"] = sum(counts[1:]) / span if span > 0 else None
    return result


def transport(args):
    import serial

    if not math.isfinite(args.seconds) or args.seconds <= 0:
        raise ValueError("capture duration must be finite and positive")

    def command(address, value):
        return bytes((0xaa, 4, 0x80, address, value, 4 ^ 0x80 ^ address ^ value, 0xbb))

    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise ValueError("choose a fresh transport directory")
    raw, pending, arrivals, counts = bytearray(), bytearray(), [], []
    result = {"scope": "host USB arrival; no device timestamps or sample counter", "port": args.port,
              "requested_seconds": args.seconds, "framing_errors": 0, "started": False}
    with serial.Serial(port=None, baudrate=1000000, timeout=.01, write_timeout=1) as connection:
        connection.dtr = connection.rts = False
        connection.port = args.port
        connection.open()
        try:
            connection.reset_input_buffer()
            connection.write(command(0x11, 0))
            connection.write(command(0x09, 1))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and "parameters" not in result:
                pending.extend(connection.read(max(1, connection.in_waiting)))
                for kind, payload in waveform_packets(pending)[0]:
                    if kind == 0x10:
                        result["parameters"] = list(payload)
            if result.get("parameters", [None])[0] != 2:
                raise ValueError("device did not acknowledge 2000 Hz; waveform test not started")
            pending.clear()
            connection.reset_input_buffer()
            started = time.perf_counter_ns()
            connection.write(command(0x11, 1))
            result["started"] = True
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                chunk = connection.read(max(1, connection.in_waiting))
                arrived = time.perf_counter_ns()
                raw.extend(chunk)
                pending.extend(chunk)
                packets, errors = waveform_packets(pending)
                result["framing_errors"] += errors
                for kind, payload in packets:
                    if kind == 0x11:
                        arrivals.append((arrived - started) / 1000)
                        counts.append(len(payload) // 32)
            elapsed = (time.perf_counter_ns() - started) / 1e9
            result.update(bytes=len(raw), trailing_bytes=len(pending), elapsed_seconds=elapsed,
                          packets=len(counts), scans=sum(counts))
            result.update(transport_metrics(arrivals, counts, elapsed))
            if result["framing_errors"]:
                raise ValueError("waveform framing or finite-value validation failed")
        except Exception as error:
            result["error"] = str(error)
        finally:
            try:
                connection.write(command(0x11, 0))
                result["stop_sent"] = True
            except Exception as error:
                result["stop_error"] = str(error)
    (args.output / "waveform.bin").write_bytes(raw)
    result["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    (args.output / "transport.json").write_text(json.dumps(result, indent=2) + "\n")
    return {key: value for key, value in result.items() if key not in ("packet_arrivals_us", "packet_scan_counts")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("checkpoint", type=Path)
    prep.add_argument("--model-dir", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--integer-fc", action="store_true")
    cap = commands.add_parser("capture")
    cap.add_argument("--port", default="COM3")
    cap.add_argument("--vectors", type=Path, required=True)
    cap.add_argument("--output", type=Path, required=True)
    cap.add_argument("--storage", choices=("flash", "psram"), required=True)
    cap.add_argument("--runs", type=int, default=3)
    cap.add_argument("--blocked-fc", action="store_true", help="Expect the four-row FP32 build")
    cap.add_argument("--kernel", choices=("fp32", "int8_scalar", "int8_simd"), help="Defaults to the vector reference kind")
    summary = commands.add_parser("summarize")
    summary.add_argument("folder", type=Path)
    usb = commands.add_parser("transport")
    usb.add_argument("--port", default="COM3")
    usb.add_argument("--output", type=Path, required=True)
    usb.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.checkpoint, args.model_dir, args.output, args.integer_fc)
    elif args.command == "capture":
        result = capture(args)
    elif args.command == "transport":
        result = transport(args)
    else:
        result = summarize(args.folder)
    print(json.dumps(result, indent=2))
    if args.command == "transport" and ("error" in result or "stop_error" in result):
        sys.exit(1)
