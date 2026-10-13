"""Synthetic contracts for opt-in AutoNCP export optimizations."""

import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import test_autoncp_firmware as firmware_test
import torch

export_autoncp = firmware_test.export_autoncp


class AutoNCPExportOptimizationTests(unittest.TestCase):
    def test_default_and_int16_exports_remain_unfused(self):
        checkpoint = firmware_test.AutoNCPFirmwareTests._checkpoint()
        with tempfile.TemporaryDirectory() as temporary:
            manifest = export_autoncp.export_model(checkpoint, temporary)
            header = (Path(temporary) / "autoncp_weights.h").read_text()
            dims = (Path(temporary) / "autoncp_dims.h").read_text()
            self.assertEqual(manifest["dimensions"]["WEIGHT_BITS"], 16)
            self.assertIn("#define AUTONCP_PADDED_ROWS 0", dims)
            self.assertIn("#define AUTONCP_FUSED_GATE_BITS 0", dims)
            self.assertIn("model_l0_time_b_weight", header)
            self.assertNotIn("__attribute__((aligned(16)))", header)

    def test_padding_zeroes_every_weight_row_and_counts_storage(self):
        checkpoint = firmware_test.AutoNCPFirmwareTests._checkpoint()
        with tempfile.TemporaryDirectory() as temporary:
            manifest = export_autoncp.export_model(
                checkpoint, temporary, weight_bits=8, pad_rows=True
            )
            header = (Path(temporary) / "autoncp_weights.h").read_text()
            self.assertIn(
                "#define AUTONCP_PADDED_ROWS 1",
                (Path(temporary) / "autoncp_dims.h").read_text(),
            )
            self.assertEqual(manifest["optimizations"]["pad_rows"], True)
            expected_bytes = 0
            model = export_autoncp.load_model(checkpoint)
            for index, cell in enumerate(model.cfc.rnn_cell._layers):
                inputs = cell.input_size + cell.hidden_size
                stride = (inputs + 15) & ~15
                for kind in export_autoncp.KINDS:
                    match = re.search(
                        rf"static const int8_t model_l{index}_{kind}_weight\[(\d+)\]"
                        rf" __attribute__\(\(aligned\(16\)\)\) =\s*\{{([^}}]+)\}};",
                        header,
                    )
                    self.assertIsNotNone(match)
                    values = np.fromstring(
                        match[2].rstrip().rstrip(","), sep=",", dtype=np.int16
                    )
                    self.assertEqual(int(match[1]), cell.hidden_size * stride)
                    self.assertEqual(values.size, cell.hidden_size * stride)
                    np.testing.assert_array_equal(
                        values.reshape(-1, stride)[:, inputs:], 0
                    )
                    expected_bytes += values.size
            head_stride = (model.head.in_features + 15) & ~15
            head = re.search(
                r"static const int8_t model_head_weight\[(\d+)\]"
                r" __attribute__\(\(aligned\(16\)\)\) =\s*\{([^}]+)\};",
                header,
            )
            self.assertIsNotNone(head)
            head_values = np.fromstring(
                head[2].rstrip().rstrip(","), sep=",", dtype=np.int16
            )
            np.testing.assert_array_equal(
                head_values.reshape(-1, head_stride)[:, model.head.in_features :], 0
            )
            expected_bytes += model.head.out_features * head_stride
            self.assertEqual(manifest["weight_bytes"], expected_bytes)
            self.assertEqual(
                manifest["optimizations"]["row_strides"]["head"], head_stride
            )

    def test_fused_gate_derives_from_baseline_int8_and_has_finite_reference(self):
        checkpoint = firmware_test.AutoNCPFirmwareTests._checkpoint()
        model = export_autoncp.load_model(checkpoint)
        baseline = export_autoncp.quantized_model(model, weight_bits=8)
        fused = export_autoncp.quantized_model(model, weight_bits=8, fuse_gate=True)
        for base_cell, fused_cell in zip(
            baseline.cfc.rnn_cell._layers, fused.cfc.rnn_cell._layers
        ):
            combined = base_cell.time_a.weight + base_cell.time_b.weight
            q, scales = export_autoncp.quantize_weight(combined, 16)
            np.testing.assert_array_equal(
                fused_cell.time_a.weight.detach().numpy(),
                (q.float() * torch.from_numpy(scales[:, None])).numpy(),
            )
            np.testing.assert_array_equal(
                fused_cell.time_a.bias.detach().numpy(),
                (base_cell.time_a.bias + base_cell.time_b.bias).detach().numpy(),
            )
            np.testing.assert_array_equal(fused_cell.time_b.weight.detach().numpy(), 0)
            np.testing.assert_array_equal(fused_cell.time_b.bias.detach().numpy(), 0)
        with tempfile.TemporaryDirectory() as temporary:
            manifest = export_autoncp.export_model(
                checkpoint, temporary, weight_bits=8, fuse_gate=True
            )
            header = (Path(temporary) / "autoncp_weights.h").read_text()
            self.assertIn(
                "#define AUTONCP_FUSED_GATE_BITS 16",
                (Path(temporary) / "autoncp_dims.h").read_text(),
            )
            self.assertIn("static const int16_t model_l0_time_a_weight", header)
            self.assertNotIn("model_l0_time_b_weight", header)
            self.assertRegex(
                header,
                r"\{model_l0_ff1_weight, model_l0_ff2_weight, model_l0_time_a_weight, 0\}",
            )
            self.assertEqual(
                manifest["weight_bytes"],
                sum(
                    4 * cell.hidden_size * (cell.input_size + cell.hidden_size)
                    for cell in model.cfc.rnn_cell._layers
                )
                + model.head.weight.numel(),
            )
            self.assertTrue(
                np.isfinite(
                    manifest["synthetic_quantization_check"][
                        "per_target_max_abs_normalized_error"
                    ]
                ).all()
            )
            self.assertTrue(
                np.isfinite(
                    manifest["synthetic_quantization_check"][
                        "per_target_fused_vs_baseline_max_abs_normalized_error"
                    ]
                ).all()
            )

    def test_fusion_rejects_int16_base_weights(self):
        checkpoint = firmware_test.AutoNCPFirmwareTests._checkpoint()
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(ValueError, "INT8"),
        ):
            export_autoncp.export_model(checkpoint, temporary, fuse_gate=True)
