<!-- Parent: ../AGENTS.md -->
# Training and evaluation

Updated 2026-10-13; development is paused. Current state: [project closeout](../../docs/project_closeout.md). Architecture: [reference](../../docs/architecture.md).

| File | Responsibility |
|---|---|
| `train.py` | `CfCTrainingConfig`, Dense/AutoNCP regressors, stateful batches, losses, normalization and per-target metrics |
| `run_db2_paper_cfc_finetune.py` | Main pretraining, head-only fine-tuning and alternating GAN-style ATL CLI |
| `run_db2_template_atl.py` | Source-derived public templates and weak-template adaptation experiment |
| `atl_chain_r2_ab.py`, `atl_ft30_rerun.py`, `atl_mislabel_ab.py` | Preserved experiment-specific reproduction CLIs |
| `profile_training.py` | Independent training-performance diagnostic |

## Contracts to preserve

- Both `dense_cfc_linear` and `autoncp_cfc_linear` are implemented. A checkpoint's family, wiring, input feature order, state width and target mapping must match the requested run.
- J10 order is one-based glove channels 2, 3, 5, 6, 8, 9, 12, 13, 16, 17. Legacy mappings remain explicit compatibility APIs.
- Stateful execution carries the complete recursive state. AutoNCP motor features have target width; they do not replace the full carried state.
- Fit normalization from the protocol's supervised training rows only. Preserve chronological context and provenance; do not supervise query labels. Masked labels are unavailable evidence, not zero-valued targets.
- ATL uses alternating discriminator and target-network optimizer updates, not a GRL. Preserve fixed-epoch/final-epoch behavior where required by the experiment protocol.
- Report per-target MAE/RMSE/R² with split sizes and scoring masks. Template fit, quantization parity and true glove prediction are different measurements.
- Preserve non-overlap and causal reset boundaries. Do not replace recording or repetition splits with shuffled overlapping windows.
- Model wall-clock updates and CfC `ts` are separate contracts. Do not change a checkpoint's time step to 0.05 merely because features update every 50 ms.

## Working rules

Reuse `train.py` and dataflow utilities. Do not copy the training engine into experiment scripts. Use existing tiny synthetic tests for changes; never launch training merely to perform cleanup. Keep experiment configurations, checkpoint identities and original results in `log/`.

Python 3.13 is the verified checkpoint environment. Actual package versions are recorded in the closeout evidence; a different installed environment must be checked rather than assumed equivalent. Hardware budgets belong to the target's owner document: ESP32 Flash/PSRAM and UNO Q Linux are different environments, so parameter count alone is not an SRAM allocation proof.

## Verification

From the repository root, run the existing Python 3.13 interpreter with `-m unittest discover -s tests -v`. Construction, state carry, label isolation, cache behavior, weighted losses and continuation tests use synthetic data. CUDA-specific behavior remains explicitly opt-in and must not be claimed verified by CPU checks.
