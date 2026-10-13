<!-- Parent: ../AGENTS.md -->
# Regression tests

Updated 2026-10-13. The suite is retained for the paused project; [closeout](../docs/project_closeout.md) records the final checks and removed redundant assertions.

Use `unittest.TestCase` and synthetic inputs. The standard entry point from the project root is:

```powershell
& 'C:\Users\14148\AppData\Local\Programs\Python\Python313\python.exe' -m unittest discover -s tests -v
```

## Coverage that must remain

- Dataflow: window geometry, alignment, target ordering, numerical features and normalization.
- Training: Dense/AutoNCP state carry, supervised/query label isolation, weighted losses, continuation, evaluation and cache invalidation.
- Export and C kernels: metadata/normalization rejection, quantization, compiled numerical parity, full recursive state and failed-step state preservation.
- ADS1298: hardware configuration boundaries, binary protocol, invalid readbacks, settling frames and acquisition/control separation. Source checks at unexecutable hardware boundaries are not automatically redundant.
- Live sessions: units, durable raw data, restart recovery, disjoint gaps, capture plans and refusal to invent training/model readiness.
- Profiling: complete numerical/timing evidence and isolation from Python standard-library module names.

Remove tests only when their contract is obsolete or independently covered. Do not delete failing behavioral tests to obtain a green result. Absence of a retired filename, source indentation and duplicate function-object identity are not useful behavior locks.

Compile C harnesses with available GCC. Conditional skips for absent external tools or explicitly opt-in CUDA are reported, not counted as executed evidence. Do not impose a universal SRAM/parameter cap on both ESP32 and UNO Q; target-specific budgets belong to the corresponding firmware/deployment documents.

Browser behavior tests live in `ui/tests/`; follow `ui/README.md`. Test recordings use disposable storage and must never overwrite `log/live_sessions/`. Keep simulation injection confined to tests; production unit, model and training gates remain closed until supported by evidence.

Shared test fixtures should import from the discovery directory. Do not resolve `from tests ...` against a third-party installed package with the same name.
