"""Synthetic checks of the device-uV path; no source-unit assumption or hardware."""

import unittest

import numpy as np
from scipy import signal

from src.dataflow.feature_extraction import extract_emg_features
from src.dataflow.SwRectify import sliding_window
from src.hardwareOperation.live_emg import CausalRMSZC, rest_thresholds_uv


class LiveEMGTests(unittest.TestCase):
    def setUp(self):
        self.raw = np.random.default_rng(7).normal(0, 25, (2173, 8))
        self.thresholds = np.arange(1, 9, dtype=np.float32)

    def test_chunks_equal_independent_causal_replay_and_exact_window_ends(self):
        stream = CausalRMSZC(self.thresholds)
        # Independent scipy replay, then existing window/feature implementation.
        b, a = signal.iirnotch(50, 30, fs=2000)
        filtered = signal.lfilter(b, a, self.raw, axis=0)
        sos = signal.butter(4, [20, 450], btype="bandpass", fs=2000, output="sos")
        filtered = signal.sosfilt(sos, filtered, axis=0)
        expected = extract_emg_features(
            sliding_window(filtered),
            feature_order=("rms", "zc"),
            zc_threshold=self.thresholds,
        )["feature_matrix"]
        chunks, ends = [], []
        start = 0
        for stop in [1, 31, 400, 401, 507, 1000, 2173]:
            x, e = stream.push(self.raw[start:stop])
            chunks.append(x)
            ends.append(e)
            start = stop
        np.testing.assert_allclose(
            np.concatenate(chunks), expected, rtol=1e-6, atol=1e-5
        )
        # 400-sample window, then one endpoint per 100 new samples, no padding.
        np.testing.assert_array_equal(np.concatenate(ends), np.arange(400, 2174, 100))
        self.assertLess(len(stream._pending), 400)  # Retained tail is bounded.

    def test_prefix_independent_of_future_and_thresholds_frozen(self):
        whole = CausalRMSZC(self.thresholds)
        prefix = CausalRMSZC(self.thresholds)
        self.thresholds[:] = 1e8  # Caller mutation cannot change an active contract.
        early, e1 = prefix.push(self.raw[:1000])
        changed_future = self.raw.copy()
        changed_future[1000:] *= 100
        all_features, e2 = whole.push(changed_future)
        np.testing.assert_array_equal(early, all_features[e2 <= 1000])
        np.testing.assert_array_equal(e1, np.arange(400, 1001, 100))
        self.assertEqual(prefix.contract()["zc_thresholds_uv"], list(range(1, 9)))

    def test_reset_requires_new_real_window_and_preserves_host_index(self):
        stream = CausalRMSZC(self.thresholds)
        stream.push(self.raw[:900])
        stream.reset(2000)
        features, ends = stream.push(self.raw[:399])
        self.assertEqual(features.shape, (0, 16))  # Gap discards the old filter/window.
        self.assertEqual(len(ends), 0)
        features, ends = stream.push(self.raw[399:500])
        expected, _ = CausalRMSZC(self.thresholds).push(self.raw[:500])
        np.testing.assert_array_equal(features, expected)
        np.testing.assert_array_equal(ends, [2400, 2500])

    def test_known_rms_zc_and_channel_major_order(self):
        # Alternating +/- channel amplitude gives RMS == amplitude, 399 flips.
        values = np.tile(np.array([1.0, -1.0]), 200)[:, None] * np.arange(1, 9)[None, :]
        features = extract_emg_features(
            sliding_window(values),
            feature_order=("rms", "zc"),
            zc_threshold=np.arange(1, 9),
        )["feature_matrix"]
        np.testing.assert_array_equal(features[0, ::2], np.arange(1, 9))
        np.testing.assert_array_equal(features[0, 1::2], np.full(8, 399))
        zero, ends = CausalRMSZC(np.zeros(8)).push(np.zeros((400, 8)))
        np.testing.assert_array_equal(zero, np.zeros((1, 16)))
        np.testing.assert_array_equal(ends, [400])

    def test_rest_uses_only_explicit_rest_and_rejects_implicit_fallback(self):
        filtered = np.array([[3.0] * 8, [-3.0] * 8, [100.0] * 8])
        np.testing.assert_array_equal(
            rest_thresholds_uv(filtered, np.array([True, True, False])), np.full(8, 3)
        )  # sqrt((9+9)/2), active row excluded.
        for mask in [np.zeros(3, bool), np.ones(2, bool), np.ones(3, int)]:
            with self.assertRaises(ValueError):
                rest_thresholds_uv(filtered, mask)

    def test_invalid_input_does_not_advance_state(self):
        for threshold in [None, np.zeros(7), np.full(8, -1), np.full(8, np.nan)]:
            with self.assertRaises(ValueError):
                CausalRMSZC(threshold)
        stream = CausalRMSZC(self.thresholds)
        for batch in [np.zeros((4, 7)), np.full((4, 8), np.inf), np.zeros(8)]:
            with self.assertRaises(ValueError):
                stream.push(batch)
        a, ends = stream.push(self.raw[:400])
        b, _ = CausalRMSZC(self.thresholds).push(self.raw[:400])
        np.testing.assert_array_equal(a, b)
        np.testing.assert_array_equal(ends, [400])
        self.assertEqual(stream.contract()["source_scaling"], "not_applied")


if __name__ == "__main__":
    unittest.main()
