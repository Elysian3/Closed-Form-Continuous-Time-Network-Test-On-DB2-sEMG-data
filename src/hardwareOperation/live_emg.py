"""Causal 8-channel device-uV features, shared by recorded replay and streaming.

This module does not convert to DB2 units or apply source normalization. Those
operations remain blocked until the source-unit provenance is established.
Unlike source preprocessing, this path uses no record-wide mean or filtfilt.
"""

from __future__ import annotations

import numpy as np
from scipy import signal

from src.dataflow.datapreprocess import (
    BP_HIGH,
    BP_LOW,
    BP_ORDER,
    FS,
    NOTCH_FREQ,
    NOTCH_Q,
)
from src.dataflow.feature_extraction import (
    _compute_rest_thresholds,
    extract_emg_features,
)
from src.dataflow.SwRectify import STRIDE_MS, WIN_MS, sliding_window


def _samples(values: np.ndarray) -> np.ndarray:
    samples = np.asarray(values, dtype=np.float64)
    if samples.ndim != 2 or samples.shape[1] != 8 or not np.isfinite(samples).all():
        raise ValueError("samples must have shape (n, 8) and finite device-uV values")
    return samples


def rest_thresholds_uv(
    filtered_emg_uv: np.ndarray, rest_mask: np.ndarray
) -> np.ndarray:
    """Measure RMS on explicitly marked, already causally filtered rest samples.

    The caller excludes startup and gap warmup. Freeze the resulting thresholds
    for the entire training replay/live run; never recalibrate on active windows.
    """
    samples = _samples(filtered_emg_uv)
    mask = np.asarray(rest_mask)
    if mask.dtype != np.bool_ or mask.shape != (len(samples),) or not mask.any():
        raise ValueError("rest_mask must explicitly select at least one rest sample")
    with np.errstate(over="raise", invalid="raise"):
        return _compute_rest_thresholds(samples, stimulus=np.where(mask, 0, 1))


class CausalRMSZC:
    """50 Hz notch + 20–450 Hz bandpass, 200 ms windows every 50 ms at 2 kHz.

    ``push`` returns (features, window_end_indices), where each end is exclusive
    in the host received-sample domain. Features are channel-major RMS(uV), ZC
    (count) pairs. Initial state is zero; no features precede 400 real samples.
    Call ``reset`` at a known gap; never stitch across segments or pad a window.
    Filter startup settling and physical latency still need device verification.
    """

    window_size = round(FS * WIN_MS / 1000)
    stride = round(FS * STRIDE_MS / 1000)

    def __init__(self, thresholds_uv: np.ndarray):
        thresholds = np.asarray(thresholds_uv, dtype=np.float32)
        if (
            thresholds.shape != (8,)
            or not np.isfinite(thresholds).all()
            or (thresholds < 0).any()
        ):
            raise ValueError(
                "eight finite nonnegative frozen thresholds in uV are required"
            )
        self._thresholds = thresholds.copy()
        self._thresholds.setflags(write=False)
        notch = signal.tf2sos(*signal.iirnotch(NOTCH_FREQ, NOTCH_Q, fs=FS))
        bandpass = signal.butter(
            BP_ORDER, [BP_LOW, BP_HIGH], btype="bandpass", fs=FS, output="sos"
        )
        self._sos = np.concatenate((notch, bandpass))
        self.reset()

    def reset(self, received_sample_index: int = 0) -> None:
        if type(received_sample_index) is not int or received_sample_index < 0:
            raise ValueError("received_sample_index must be a nonnegative integer")
        self._state = np.zeros((len(self._sos), 2, 8), dtype=np.float64)
        self._pending = np.empty((0, 8), dtype=np.float32)
        self._next_start = received_sample_index

    def contract(self) -> dict:
        return {
            "sample_rate_hz": FS,
            "channels": list(range(1, 9)),
            "input_units": "uV",
            "rms_units": "uV",
            "zc_units": "count",
            "feature_order": [f"ch{c}_{f}" for c in range(1, 9) for f in ("rms", "zc")],
            "notch_hz": NOTCH_FREQ,
            "notch_q": NOTCH_Q,
            "bandpass_hz": [BP_LOW, BP_HIGH],
            "bandpass_order": BP_ORDER,
            "causal": True,
            "initial_state": "zero",
            "window_samples": self.window_size,
            "stride_samples": self.stride,
            "zc_thresholds_uv": self._thresholds.tolist(),
            "source_scaling": "not_applied",
            "source_normalization": "not_applied",
        }

    def push(self, samples_uv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        samples = _samples(samples_uv)
        if not len(samples):
            return np.empty((0, 16), np.float32), np.empty(0, np.int64)
        # A bad batch never advances filter state. Caller closes the affected
        # segment on rejection, as it would for a parser or transport failure.
        with np.errstate(over="raise", invalid="raise"):
            filtered, next_state = signal.sosfilt(
                self._sos, samples, axis=0, zi=self._state
            )
            pending = np.concatenate((self._pending, filtered.astype(np.float32)))
            if len(pending) < self.window_size:
                self._state, self._pending = next_state, pending
                return np.empty((0, 16), np.float32), np.empty(0, np.int64)
            windows = sliding_window(
                pending, fs=FS, window_ms=WIN_MS, stride_ms=STRIDE_MS
            )
            features = extract_emg_features(
                windows, feature_order=("rms", "zc"), zc_threshold=self._thresholds
            )["feature_matrix"]
        if not np.isfinite(features).all():
            raise ValueError("nonfinite feature output; close the segment")
        ends = windows["window_end_indices"].astype(np.int64) + self._next_start
        consumed = len(features) * self.stride
        self._state = next_state
        self._pending = pending[consumed:].copy()
        self._next_start += consumed
        return features, ends
