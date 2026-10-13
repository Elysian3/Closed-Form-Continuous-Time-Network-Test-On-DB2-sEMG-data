"""Compiled experimental INT8 activation contract and independent reference."""

import ctypes
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

import test_autoncp_firmware as firmware_test

export_autoncp, ROOT = firmware_test.export_autoncp, firmware_test.ROOT

sys.path.insert(0, str(ROOT / "firmware/ads1298_autoncp"))
from integer_reference import integer_model, predictions, quantize_input


class IntegerFCTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gcc"), "gcc required")
    def test_padded_rows_preserve_integer_reference(self):
        helper = firmware_test.AutoNCPFirmwareTests()
        payload = helper._checkpoint()
        inputs = np.random.default_rng(51).uniform(-2, 2, (64, 16)).astype(np.float32)
        expected, states = predictions(integer_model(payload), inputs)
        pointer = ctypes.POINTER(ctypes.c_float)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            manifest = export_autoncp.export_model(payload, folder, weight_bits=8, pad_rows=True)
            library = helper._library(folder, integer=True)
            try:
                for index, row in enumerate(inputs):
                    output = np.empty(10, np.float32)
                    self.assertTrue(library.autoncp_step(row.ctypes.data_as(pointer), output.ctypes.data_as(pointer)))
                    np.testing.assert_allclose(output, expected[index], rtol=0, atol=2e-4)
                    np.testing.assert_allclose(np.ctypeslib.as_array(library.autoncp_state(), shape=(manifest["dimensions"]["STATE_SIZE"],)), states[index], rtol=0, atol=2e-4)
            finally:
                helper._unload(library)

    def test_quantization_zero_and_half_away_from_zero(self):
        actual, scale = quantize_input(np.array([127, -127, .5, -.5, 1.5, -1.5, 0], np.float32))
        # Maximum 127 makes scale exactly one; ties round away from zero.
        np.testing.assert_array_equal(actual, [127, -127, 1, -1, 2, -2, 0])
        self.assertEqual(scale, 1)
        actual, scale = quantize_input(np.zeros(16, np.float32))
        np.testing.assert_array_equal(actual, 0)
        self.assertEqual(scale, 1)
        with self.assertRaises(ValueError):
            quantize_input([np.nan])

    @unittest.skipUnless(shutil.which("gcc"), "gcc required")
    def test_continuous_integer_parity_and_failed_step_state(self):
        helper = firmware_test.AutoNCPFirmwareTests()
        payload = helper._checkpoint()
        inputs = np.random.default_rng(37).uniform(-2, 2, (64, 16)).astype(np.float32)
        expected, expected_states = predictions(integer_model(payload), inputs)
        pointer = ctypes.POINTER(ctypes.c_float)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = export_autoncp.export_model(payload, directory, weight_bits=8)
            library = helper._library(directory, integer=True, profile=True)
            class Profile(ctypes.Structure):
                _fields_ = [("fc", ctypes.c_int64 * 12), ("activation", ctypes.c_int64 * 3),
                            ("head", ctypes.c_int64), ("total", ctypes.c_int64),
                            ("quantize", ctypes.c_int64 * 4)]
            library.autoncp_profile_enable.argtypes = [ctypes.c_bool]
            library.autoncp_profile_last.restype = ctypes.POINTER(Profile)
            try:
                library.autoncp_reset()
                library.autoncp_profile_enable(True)
                for index, row in enumerate(inputs):
                    actual = np.empty(10, np.float32)
                    self.assertTrue(library.autoncp_step(row.ctypes.data_as(pointer), actual.ctypes.data_as(pointer)))
                    np.testing.assert_allclose(actual, expected[index], rtol=0, atol=2e-4)
                    state = np.ctypeslib.as_array(library.autoncp_state(), shape=(manifest["dimensions"]["STATE_SIZE"],)).copy()
                    np.testing.assert_allclose(state, expected_states[index], rtol=0, atol=2e-4)
                    measured = library.autoncp_profile_last().contents
                    self.assertEqual(list(measured.quantize), [1] * 4)
                    self.assertEqual(list(measured.fc), [1] * 12)
                    self.assertEqual(list(measured.activation), [1] * 3)
                    self.assertEqual(measured.head, 1)
                for value in (np.nan, np.finfo(np.float32).max):
                    invalid = np.full(16, value, np.float32)
                    self.assertFalse(library.autoncp_step(invalid.ctypes.data_as(pointer), actual.ctypes.data_as(pointer)))
                    np.testing.assert_array_equal(np.ctypeslib.as_array(library.autoncp_state(), shape=state.shape), state)
                    self.assertEqual(library.autoncp_profile_last().contents.total, 0)
            finally:
                helper._unload(library)
