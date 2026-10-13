<!-- Parent: ../AGENTS.md -->
# Dataflow

Updated 2026-10-13; see [project closeout](../../docs/project_closeout.md).

| Module | Contract |
|---|---|
| `datapreprocess.py` | Load selected MATLAB fields; offline DC removal, 50 Hz notch and 20–450 Hz bandpass |
| `SwRectify.py` | 200 ms windows / 50 ms stride; explicit target alignment and sample offset |
| `feature_extraction.py` | Requested EMG features, training-statistics normalization and regression preparation |
| `doa_mapping.py` | Ordered DB2 J10 selection and explicit legacy mappings |

## Boundaries

- Offline zero-phase filtering uses future samples. Live causal filtering is owned by `src/hardwareOperation/live_emg.py`; no filter-equivalence or physical-latency claim follows from offline tests.
- Window length, stride, target offset, feature ordering and source recording indices are numerical contracts. Trace downstream consumers before changing them.
- The reusable feature API supports MAV, MAVS, WL, ZC, SSC and RMS. The paper CLI defaults to RMS; the preserved eight-channel NCP export uses interleaved RMS/ZC. Do not confuse those configurations.
- ZC/SSC thresholds require a valid calibration/unit contract. Compute only requested features and their required dependencies.
- J10 selects one-based channels 2, 3, 5, 6, 8, 9, 12, 13, 16, 17. DB2 sensor values must not be relabeled as calibrated anatomical angles without evidence.
- Data splits must prevent shared supervised signal/label provenance under the stated protocol. Keep causal context separate from supervised labels and retain boundary/gap checks.
- Do not guess a conversion between DB2 storage units and device microvolts. This remains an unresolved physical deployment boundary.

## Verification

Use synthetic arrays and hand-computable expectations in `tests/test_regression_pipeline.py`. Preserve window indices, target timing, mapping order, on-demand feature behavior and normalization round trips. Use existing NumPy/SciPy dependencies; changes to filters or window geometry need explicit evidence and regression checks.
