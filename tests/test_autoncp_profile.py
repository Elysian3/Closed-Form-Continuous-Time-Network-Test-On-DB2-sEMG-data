"""A timing report must reject incomplete sequences and invalid numerical evidence."""
import importlib.util
import struct
import subprocess
import sys
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "firmware/ads1298_autoncp/profile_autoncp.py"
spec = importlib.util.spec_from_file_location("autoncp_profile", path)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class ProfileEvidenceTests(unittest.TestCase):
    def test_tool_directory_does_not_shadow_standard_library_profiler(self):
        result = subprocess.run(
            [sys.executable, "-c", "import cProfile; cProfile.run('pass')"],
            cwd=path.parent, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fresh_capture_requires_matching_explicit_variant(self):
        vectors = {"optimizations": {"pad_rows": True, "fuse_gate": True}}
        result = {"variant": {"padded": 1, "fused_gate_bits": 16, "blocked": 1}}
        profile.check_variant(result, vectors, blocked=True)
        for invalid in ({}, {"variant": {"padded": 1, "fused_gate_bits": 16, "blocked": 0}}):
            with self.assertRaises(ValueError):
                profile.check_variant(invalid, vectors, blocked=True)
        with self.assertRaises(ValueError):
            profile.check_variant({}, {})

    def test_transport_rejects_zero_scans_and_identifies_host_batching(self):
        with self.assertRaises(ValueError):
            profile.transport_metrics([], [], 10)
        result = profile.transport_metrics([100, 100], [7, 7], 10)
        self.assertIsNone(result["scan_rate_between_first_last"])
        self.assertEqual(result["host_packet_interval"]["max_us"], 0)
        self.assertIn("same read", result["arrival_clock"])
        self.assertEqual(result["scans"], 14)

    def test_waveform_split_packet_and_invalid_payload(self):
        payload = struct.pack("<8f", *range(8))
        frame = bytes((0xa5, 34, 0x11, 34 ^ 0x11)) + payload + b"\x5a"
        pending = bytearray(frame[:10])
        self.assertEqual(profile.waveform_packets(pending), ([], 0))
        pending.extend(frame[10:])
        self.assertEqual(profile.waveform_packets(pending), ([(0x11, payload)], 0))
        self.assertEqual(pending, b"")
        invalid = bytearray(frame)
        invalid[4:8] = struct.pack("<f", float("nan"))
        self.assertEqual(profile.waveform_packets(invalid), ([], 1))
        pending.extend(b"x" + frame[:-1] + b"\x00" + frame)
        packets, errors = profile.waveform_packets(pending)
        self.assertEqual(packets, [(0x11, payload)])
        self.assertGreater(errors, 0)

    def test_complete_capture_and_fail_closed_evidence_checks(self):
        lines = [f"AutoNCP sha256={'a' * 64} weight_bits=8 states=768 outputs=10",
                 f"PROFILE_META steps=128 warmup=16 instrumented=1 corpus={'b' * 64}",
                 "PROFILE_MEMORY weight=0x3c010000 physical=0x10000 target=FLASH psram=0",
                 "PROFILE_CLOCK n=256 sum=128 max=1"]
        for mode in ("control", "stages"):
            for i in range(128):
                fc = ",".join(["1" if mode == "stages" else "0"] * 12)
                act = "1,1,1" if mode == "stages" else "0,0,0"
                head = int(mode == "stages")
                lines.append(f"PROFILE_STEP mode={mode} i={i} total=20 fc={fc} act={act} head={head}")
            lines.append(f"PROFILE_NUMERIC mode={mode} state=0 targets=" + ",".join(["0"] * 10))
        lines.extend(f"PROFILE_AUX i={i} us=1,2,3,4,5" for i in range(128))
        lines.extend(["PROFILE_AUX_NUMERIC normalize=0 inverse_relative=0 rms_zc=pass packet=pass", "PROFILE_DONE"])
        raw = "\n".join(lines).encode()
        report = profile.parse_capture(raw)
        # Twelve FCs + three activations + head = 16 of a 20-us whole interval.
        self.assertEqual(report["modes"]["stages"][16]["unattributed_measured"], 4)
        self.assertEqual(len(report["modes"]["stages"]), 128)
        extended = raw.replace(b"PROFILE_META", b"PROFILE_KERNEL kind=int8_simd\n"
                               b"SIMD_DOT_TEST lengths=1,10,15,16,17,31,32,313,471,758 patterns=3 result=pass\nPROFILE_META", 1)
        extended = extended.replace(b"head=1\n", b"head=1 quant=1,1,0,0\n")
        extended_report = profile.parse_capture(extended)
        self.assertEqual(extended_report["kernel_kind"], "int8_simd")
        self.assertEqual(extended_report["modes"]["stages"][16]["quantize_sum"], 2)
        self.assertEqual(extended_report["modes"]["stages"][16]["unattributed_measured"], 2)
        identified = raw.replace(b"PROFILE_META", b"PROFILE_VARIANT padded=1 fused_gate_bits=16 blocked=1\nPROFILE_META", 1)
        self.assertEqual(profile.parse_capture(identified)["variant"], {"padded": 1, "fused_gate_bits": 16, "blocked": 1})
        # Four hand-ordered measurements; nearest-rank median is the second value.
        self.assertEqual(profile.stats([4, 1, 3, 2])["p50_us"], 2)
        self.assertEqual(profile.stats([4, 1, 3, 2])["p95_us"], 4)
        for invalid in (raw.replace(b"PROFILE_DONE", b""),
                        identified.replace(b"fused_gate_bits=16", b"fused_gate_bits=8"),
                        extended.replace(b"patterns=3 result=pass", b"patterns=3 result=fail"),
                        raw.replace(b"mode=stages i=17 ", b"mode=stages i=16 "),
                        raw.replace(b"state=0 targets=", b"state=nan targets="),
                        raw.replace(b"total=20", b"total=1")):
            with self.subTest(invalid=invalid[:60]), self.assertRaises(ValueError):
                profile.parse_capture(invalid)


if __name__ == "__main__":
    unittest.main()
