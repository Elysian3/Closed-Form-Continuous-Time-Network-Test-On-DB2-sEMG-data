"""Synthetic host parity checks for the generated wired AutoNCP runtime."""

import ctypes
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "firmware" / "model"
EXPORTER_DIR = ROOT / "src" / "hardwareOperation"
if str(EXPORTER_DIR) not in sys.path:
    sys.path.insert(0, str(EXPORTER_DIR))

import export_autoncp


class AutoNCPFirmwareTests(unittest.TestCase):
    """Compile the exact C source against a small generated test-only export."""

    @staticmethod
    def _checkpoint() -> dict:
        torch.manual_seed(20260920)
        model = export_autoncp.build_cfc_regressor(
            input_dim=16,
            output_dim=10,
            hidden_units=32,
            model_family="autoncp_cfc_linear",
            cfc_dropout=0.0,
        ).eval()
        return {
            "config": {
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
                "hidden_units": 32,
            },
            "target_contract": {
                "mapping_name": "joint_angles10",
                "output_dim": 10,
                "source_channels_one_based": [2, 3, 5, 6, 8, 9, 12, 13, 16, 17],
                "target_names": [f"joint_{index}" for index in range(10)],
            },
            "model_state_dict": model.state_dict(),
            "feature_normalization_stats": {
                "method": "mu_law",
                "center": np.linspace(-0.4, 0.4, 16, dtype=np.float32),
                "scale": np.linspace(0.5, 1.3, 16, dtype=np.float32),
                "mu": 255.0,
            },
            "target_normalization_stats": {
                "method": "mu_law",
                "center": np.linspace(-1.0, 1.0, 10, dtype=np.float32),
                "scale": np.linspace(0.25, 0.75, 10, dtype=np.float32),
                "mu": 255.0,
            },
        }

    def _library(self, output_dir: Path, profile=False, integer=False, blocked=False):
        library = output_dir / "autoncp_test.dll"
        command = [
            "gcc",
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-shared",
            "-fPIC",
            "-O2",
            "-I",
            str(output_dir),
            "-I",
            str(MODEL_DIR),
            str(MODEL_DIR / "autoncp.c"),
            "-lm",
            "-o",
            str(library),
        ]
        if profile:
            clock = output_dir / "profile_clock.c"
            clock.write_text("#include <stdint.h>\nint64_t autoncp_profile_now_us(void) { static int64_t t; return t++; }\n")
            command.extend(["-DAUTONCP_PROFILE=1", str(clock)])
        if integer:
            command.append("-DAUTONCP_INTEGER_FC=1")
        if blocked:
            command.append("-DAUTONCP_BLOCKED_FC=1")
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        lib = ctypes.CDLL(str(library))
        float_pointer = ctypes.POINTER(ctypes.c_float)
        lib.autoncp_reset.argtypes = []
        lib.autoncp_step.argtypes = [float_pointer, float_pointer]
        lib.autoncp_step.restype = ctypes.c_bool
        lib.autoncp_state.argtypes = []
        lib.autoncp_state.restype = float_pointer
        lib.autoncp_normalize_features.argtypes = [float_pointer, float_pointer]
        lib.autoncp_normalize_features.restype = ctypes.c_bool
        lib.autoncp_inverse_targets.argtypes = [float_pointer, float_pointer]
        lib.autoncp_inverse_targets.restype = ctypes.c_bool
        return lib

    @unittest.skipUnless(shutil.which("gcc"), "gcc required")
    def test_optimized_layout_gate_and_row_blocks_preserve_reference_and_state(self):
        payload = self._checkpoint()
        inputs = np.random.default_rng(49).uniform(-2, 2, (64, 16)).astype(np.float32)
        pointer = ctypes.POINTER(ctypes.c_float)
        for bits, padded, fused, blocked in (
            (8, True, False, False), (16, True, False, True),
            (8, False, False, True), (8, False, True, False),
            (8, True, True, True), (8, False, True, True),
        ):
            with self.subTest(bits=bits, padded=padded, fused=fused, blocked=blocked), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                manifest = export_autoncp.export_model(payload, folder, weight_bits=bits, pad_rows=padded, fuse_gate=fused)
                reference = export_autoncp.quantized_model(export_autoncp.load_model(payload), bits, fuse_gate=fused)
                library = self._library(folder, blocked=blocked, profile=True)
                library.autoncp_profile_enable.argtypes = [ctypes.c_bool]
                library.autoncp_profile_last.restype = ctypes.POINTER(ctypes.c_int64 * 12)
                library.autoncp_profile_enable(True)
                state = None
                try:
                    for row in inputs:
                        expected, state = reference.forward_with_state(torch.from_numpy(row[None, None]), state)
                        actual = np.empty(10, np.float32)
                        self.assertTrue(library.autoncp_step(row.ctypes.data_as(pointer), actual.ctypes.data_as(pointer)))
                        np.testing.assert_allclose(actual, expected.detach().numpy()[0], rtol=0, atol=2e-4)
                        actual_state = np.ctypeslib.as_array(library.autoncp_state(), shape=(manifest["dimensions"]["STATE_SIZE"],)).copy()
                        np.testing.assert_allclose(actual_state, state.detach().numpy()[0], rtol=0, atol=2e-4)
                    self.assertEqual(list(library.autoncp_profile_last().contents), [1, 1, 1, 0] * 3 if fused else [1] * 12)
                    for value in (np.nan, np.finfo(np.float32).max):
                        invalid = np.full(16, value, np.float32)
                        self.assertFalse(library.autoncp_step(invalid.ctypes.data_as(pointer), actual.ctypes.data_as(pointer)))
                        np.testing.assert_array_equal(np.ctypeslib.as_array(library.autoncp_state(), shape=actual_state.shape), actual_state)
                    library.autoncp_reset()
                    np.testing.assert_array_equal(np.ctypeslib.as_array(library.autoncp_state(), shape=actual_state.shape), 0)
                finally:
                    self._unload(library)

    @unittest.skipUnless(shutil.which("gcc"), "gcc required")
    def test_integer_fc_rejects_fused_int16_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            export_autoncp.export_model(self._checkpoint(), folder, weight_bits=8, fuse_gate=True)
            result = subprocess.run(["gcc", "-fsyntax-only", "-DAUTONCP_INTEGER_FC=1",
                                     "-I", str(folder), "-I", str(MODEL_DIR), str(MODEL_DIR / "autoncp.c")],
                                    capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("The fused gate requires FP32 activations", result.stderr)

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for compiled C parity")
    def test_stage_probes_preserve_outputs_state_and_reject_invalid_steps(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = export_autoncp.export_model(self._checkpoint(), directory, weight_bits=8)
            library = self._library(directory, profile=True)
            library.autoncp_profile_enable.argtypes = [ctypes.c_bool]
            class Profile(ctypes.Structure):
                _fields_ = [("fc", ctypes.c_int64 * 12), ("activation", ctypes.c_int64 * 3),
                            ("head", ctypes.c_int64), ("total", ctypes.c_int64),
                            ("quantize", ctypes.c_int64 * 4)]
            library.autoncp_profile_last.restype = ctypes.POINTER(Profile)
            inputs = np.random.default_rng(42).uniform(-1, 1, (7, 16)).astype(np.float32)
            pointer = ctypes.POINTER(ctypes.c_float)
            expected = []
            try:
                for enabled in (False, True):
                    library.autoncp_reset()
                    library.autoncp_profile_enable(enabled)
                    for index, values in enumerate(inputs):
                        output = np.empty(10, np.float32)
                        self.assertTrue(library.autoncp_step(values.ctypes.data_as(pointer), output.ctypes.data_as(pointer)))
                        state = np.ctypeslib.as_array(library.autoncp_state(), shape=(manifest["dimensions"]["STATE_SIZE"],)).copy()
                        if not enabled:
                            expected.append((output.copy(), state))
                        else:
                            np.testing.assert_array_equal(output, expected[index][0])
                            np.testing.assert_array_equal(state, expected[index][1])
                            measured = library.autoncp_profile_last().contents
                            # Each bracketed stage consumes two successive clock ticks.
                            self.assertEqual(list(measured.fc), [1] * 12)
                            self.assertEqual(list(measured.activation), [1] * 3)
                            self.assertEqual(measured.head, 1)
                            self.assertEqual(list(measured.quantize), [0] * 4)
                            self.assertGreaterEqual(measured.total, 16)
                invalid = np.full(16, np.nan, np.float32)
                self.assertFalse(library.autoncp_step(invalid.ctypes.data_as(pointer), output.ctypes.data_as(pointer)))
                np.testing.assert_array_equal(np.ctypeslib.as_array(library.autoncp_state(), shape=state.shape), state)
                self.assertEqual(library.autoncp_profile_last().contents.total, 0)
            finally:
                self._unload(library)

    def test_export_rejects_wrong_feature_contract_and_invalid_normalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            for failure in ("features", "normalization", "target_order"):
                with self.subTest(failure=failure):
                    checkpoint = self._checkpoint()
                    if failure == "features":
                        checkpoint["config"]["feature_order"] = ["zc", "rms"]
                    elif failure == "normalization":
                        checkpoint["target_normalization_stats"]["scale"][0] = 0
                    else:
                        checkpoint["target_contract"][
                            "source_channels_one_based"
                        ].reverse()
                    with self.assertRaises(ValueError):
                        export_autoncp.export_model(checkpoint, temporary)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    @staticmethod
    def _unload(library) -> None:
        """Release the Windows DLL lock before TemporaryDirectory cleanup."""
        if sys.platform == "win32" and library._handle is not None:
            free_library = ctypes.windll.kernel32.FreeLibrary
            free_library.argtypes = [ctypes.c_void_p]
            free_library.restype = ctypes.c_bool
            if not free_library(ctypes.c_void_p(library._handle)):
                raise OSError("FreeLibrary failed")
            library._handle = None

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for compiled C parity")
    def test_compiled_runtime_matches_dequantized_ncps_and_normalization(self):
        checkpoint = self._checkpoint()
        rng = np.random.default_rng(20260920)
        normalized_inputs = rng.uniform(-1.0, 1.0, (7, 16)).astype(np.float32)
        raw_features = rng.normal(size=16).astype(np.float32)

        for weight_bits in (8, 16):
            with (
                self.subTest(weight_bits=weight_bits),
                tempfile.TemporaryDirectory() as temporary,
            ):
                directory = Path(temporary)
                model = export_autoncp.load_model(checkpoint)
                reference = export_autoncp.quantized_model(
                    model, weight_bits=weight_bits
                )
                manifest = export_autoncp.export_model(
                    checkpoint, directory, weight_bits=weight_bits
                )
                self.assertEqual(manifest["dimensions"]["INPUT_SIZE"], 16)
                # Legacy INT8/INT16 exports predate both optimization macros.
                dimensions = directory / "autoncp_dims.h"
                dimensions.write_text("\n".join(
                    line for line in dimensions.read_text().splitlines()
                    if not line.startswith(("#define AUTONCP_PADDED_ROWS ", "#define AUTONCP_FUSED_GATE_BITS "))
                ) + "\n")
                library = self._library(directory)
                library.autoncp_reset()

                state = None
                for features in normalized_inputs:
                    expected, state = reference.forward_with_state(
                        torch.from_numpy(features[None, None]), state
                    )
                    actual = np.empty(10, dtype=np.float32)
                    self.assertTrue(
                        library.autoncp_step(
                            features.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                            actual.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                        )
                    )
                    np.testing.assert_allclose(
                        actual, expected.detach().numpy()[0], rtol=2e-5, atol=2e-5
                    )
                    actual_state = np.ctypeslib.as_array(
                        library.autoncp_state(),
                        shape=(manifest["dimensions"]["STATE_SIZE"],),
                    ).copy()
                    np.testing.assert_allclose(
                        actual_state, state.detach().numpy()[0], rtol=2e-5, atol=2e-5
                    )

                normalized = np.empty(16, dtype=np.float32)
                self.assertTrue(
                    library.autoncp_normalize_features(
                        raw_features.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                        normalized.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                    )
                )
                stats = checkpoint["feature_normalization_stats"]
                scaled = (raw_features - stats["center"]) / stats["scale"]
                expected_normalized = (
                    np.sign(scaled)
                    * np.log1p(stats["mu"] * np.abs(scaled))
                    / math.log1p(stats["mu"])
                )
                np.testing.assert_allclose(
                    normalized,
                    expected_normalized.astype(np.float32),
                    rtol=2e-6,
                    atol=2e-6,
                )

                restored = np.empty(10, dtype=np.float32)
                self.assertTrue(
                    library.autoncp_inverse_targets(
                        actual.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                        restored.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                    )
                )
                target = checkpoint["target_normalization_stats"]
                expected_restored = (
                    np.sign(actual)
                    * np.expm1(np.abs(actual) * math.log1p(target["mu"]))
                    / target["mu"]
                    * target["scale"]
                    + target["center"]
                )
                np.testing.assert_allclose(
                    restored, expected_restored.astype(np.float32), rtol=2e-6, atol=2e-6
                )
                self._unload(library)

    @unittest.skipUnless(shutil.which("gcc"), "gcc is required for compiled C parity")
    def test_reset_and_nonfinite_input_do_not_advance_state(self):
        checkpoint = self._checkpoint()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = export_autoncp.export_model(
                checkpoint, directory, weight_bits=16
            )
            library = self._library(directory)
            first = np.full(16, 0.25, dtype=np.float32)
            output = np.empty(10, dtype=np.float32)
            self.assertTrue(
                library.autoncp_step(
                    first.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                    output.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                )
            )
            before = np.ctypeslib.as_array(
                library.autoncp_state(), shape=(manifest["dimensions"]["STATE_SIZE"],)
            ).copy()
            invalid = first.copy()
            invalid[3] = np.nan
            self.assertFalse(
                library.autoncp_step(
                    invalid.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                    output.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                )
            )
            np.testing.assert_array_equal(
                np.ctypeslib.as_array(library.autoncp_state(), shape=before.shape),
                before,
            )
            # Finite but overflowing arithmetic must not commit partial state.
            invalid.fill(np.finfo(np.float32).max)
            self.assertFalse(
                library.autoncp_step(
                    invalid.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                    output.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                )
            )
            np.testing.assert_array_equal(
                np.ctypeslib.as_array(library.autoncp_state(), shape=before.shape),
                before,
            )
            excessive_targets = np.full(10, 1000, dtype=np.float32)
            self.assertFalse(
                library.autoncp_inverse_targets(
                    excessive_targets.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                    output.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                )
            )
            library.autoncp_reset()
            np.testing.assert_array_equal(
                np.ctypeslib.as_array(library.autoncp_state(), shape=before.shape), 0.0
            )
            self._unload(library)
