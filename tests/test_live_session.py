import hashlib
import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import numpy as np

SERVER_PATH = Path(__file__).resolve().parents[1] / "ui" / "server.py"
spec = importlib.util.spec_from_file_location("live_server", SERVER_PATH)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def fixture():
    return server.Template(
        list(range(1, 41)),
        [i * 0.05 for i in range(181)],
        np.zeros((40, 181, 10)).tolist(),
        server.J10,
        {"npz": "test", "json": "test"},
        [-1.0] * 10,
        [1.0] * 10,
    )


class LiveSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = server.CaptureService(Path(self.temp.name), template=fixture())
        self.session = self.service.create(
            {
                "rate_index": 2,
                "pga_index": 0,
                "ack_at_ms": 1,
                "preferences": {"hand": "right", "language": "zh"},
            }
        )
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.temp.cleanup()

    def cue(self, event, index, time, action=1, repetition=1, segment=1):
        return self.service.cue(
            {
                "session_id": self.sid,
                "event": event,
                "action": action,
                "repetition": repetition,
                "segment_id": segment,
                "received_sample_index": index,
                "browser_time_ms": time,
            }
        )

    def append(self, start, count, segment=1, first_batch=0):
        for offset in range(0, count, 512):
            n = min(512, count - offset)
            self.service.samples(
                {
                    "session_id": self.sid,
                    "segment_id": segment,
                    "batch_id": first_batch + offset // 512,
                    "received_sample_index": start + offset,
                    "received_at_ms": offset,
                    "samples": [[1.0] * 8 for _ in range(n)],
                }
            )

    def test_raw_rows_and_complete_repetition_are_audited(self):
        self.cue("start", 0, 0)
        self.append(0, 4000)
        self.cue("cue_on", 4000, 2000)
        self.append(4000, 10000, first_batch=8)
        self.cue("cue_off", 14000, 7000)
        self.append(14000, 4000, first_batch=28)
        result = self.cue("complete", 18000, 9000)
        self.assertEqual(result["valid_repetitions"], 1)
        root = self.service.session["dir"]
        self.assertEqual((root / "emg.f32").stat().st_size, 18000 * 8 * 4)
        manifest = json.loads((root / "manifest.json").read_text())
        self.assertEqual(manifest["dtype"], "<f4")
        self.assertEqual(manifest["repetitions"][0]["end_index"], 18000)
        self.assertEqual(manifest["device_continuity"], "unverified_legacy_wire")
        self.assertIn('"received_sample_start":0', (root / "events.jsonl").read_text())

    def test_samples_are_saved_in_volts_across_server_restart(self):
        self.cue("start", 0, 0)
        self.service.samples(
            {
                "session_id": self.sid,
                "segment_id": 1,
                "batch_id": 0,
                "received_sample_index": 0,
                "received_at_ms": 0,
                "samples": [[-1000, -100, -1, 0, 1, 100, 1000, 1000000]],
            }
        )
        root = self.service.session["dir"]
        self.service = server.CaptureService(Path(self.temp.name), template=fixture())
        self.cue("start", 1, 1, segment=2)
        self.append(1, 1, segment=2)
        manifest = json.loads((root / "manifest.json").read_text())
        self.assertEqual(manifest["wire_units"], "uV")
        self.assertEqual(manifest["stored_units"], "V")
        # Signed input microvolts become volts once, including after recovery.
        np.testing.assert_allclose(
            np.fromfile(root / "emg.f32", dtype="<f4").reshape(-1, 8),
            [
                [-0.001, -0.0001, -0.000001, 0, 0.000001, 0.0001, 0.001, 1],
                [0.000001] * 8,
            ],
            rtol=1e-7,
            atol=0,
        )

    def test_legacy_microvolt_session_keeps_its_units_when_resumed(self):
        self.service.session["manifest"].pop("stored_units", None)
        self.service._atomic_manifest()
        root = self.service.session["dir"]
        # Existing files without stored_units contain unscaled microvolts.
        (root / "emg.f32").write_bytes(np.full((1, 8), 100, dtype="<f4").tobytes())
        self.service = server.CaptureService(Path(self.temp.name), template=fixture())
        self.cue("start", 0, 0)
        self.append(0, 1)
        manifest = json.loads((root / "manifest.json").read_text())
        self.assertEqual(manifest["stored_units"], "uV")
        np.testing.assert_array_equal(
            np.fromfile(root / "emg.f32", dtype="<f4").reshape(-1, 8),
            [[100] * 8, [1] * 8],
        )

    def test_ordering_error_invalidates_then_replay_uses_new_segment(self):
        self.cue("start", 20, 0)
        with self.assertRaises(server.ApiError) as caught:
            self.service.samples(
                {
                    "session_id": self.sid,
                    "segment_id": 1,
                    "batch_id": 1,
                    "received_sample_index": 20,
                    "received_at_ms": 0,
                    "samples": [[0.0] * 8],
                }
            )
        self.assertEqual(caught.exception.code, "ordering")
        self.service.session["manifest"]["status"] = "recording"
        self.cue("start", 100, 0, segment=2)
        paused = self.service.pause(
            {
                "session_id": self.sid,
                "reason": "visibility_loss",
                "received_sample_index": 100,
                "browser_time_ms": 1,
            }
        )
        self.assertEqual(paused["invalidated"], {"action": 1, "repetition": 1})
        self.cue("start", 200, 0, segment=3)
        self.assertEqual(self.service.session["active"]["start_index"], 200)
        self.assertEqual(
            self.service.session["manifest"]["segments"][0]["reason"], "ordering"
        )

    def test_malformed_samples_and_client_flags_never_open_model(self):
        self.cue("start", 0, 0)
        with self.assertRaises(server.ApiError):
            self.service.samples(
                {
                    "session_id": self.sid,
                    "segment_id": 1,
                    "batch_id": 0,
                    "received_sample_index": 0,
                    "samples": [[float("nan")] * 8],
                }
            )
        with self.assertRaises(server.ApiError) as caught:
            self.service.activation({"verified": True, "unit_scale": 1})
        self.assertEqual(
            (caught.exception.code, caught.exception.status),
            ("blocked_unit_evidence", 409),
        )

    def test_forged_types_and_cue_mismatch_invalidate_active_segment(self):
        self.cue("start", 0, 0)
        with self.assertRaises(server.ApiError):
            self.service.samples(
                {
                    "session_id": self.sid,
                    "segment_id": 1,
                    "batch_id": False,
                    "received_sample_index": 0,
                    "received_at_ms": 0,
                    "samples": [["1"] * 8],
                }
            )
        self.assertIsNone(self.service.session["active"])
        self.cue("start", 10, 0, segment=2)
        with self.assertRaises(server.ApiError):
            self.cue("cue_on", 10, 2000, segment=99)
        self.assertIsNone(self.service.session["active"])

    def test_paused_stop_is_idempotent(self):
        self.cue("start", 0, 0)
        self.service.pause(
            {
                "session_id": self.sid,
                "reason": "manual",
                "received_sample_index": 0,
                "browser_time_ms": 1,
            }
        )
        self.assertEqual(
            self.service.stop({"session_id": self.sid})["status"], "closed"
        )
        self.assertEqual(
            self.service.stop({"session_id": self.sid})["status"], "closed"
        )

    def test_unfinished_active_segment_is_recovered_after_server_restart(self):
        self.cue("start", 25, 0)
        self.append(25, 1)
        restarted = server.CaptureService(Path(self.temp.name), template=fixture())
        self.assertEqual(restarted.session["manifest"]["session_id"], self.sid)
        self.assertIsNone(restarted.session["active"])
        self.assertEqual(restarted.session["manifest"]["status"], "paused")
        self.assertEqual(
            restarted.session["manifest"]["segments"][-1]["reason"], "server_restart"
        )
        self.assertFalse(restarted.session["manifest"]["repetitions"][-1]["valid"])
        self.assertEqual(restarted.status()["session"]["stored_rows"], 1)

    def test_private_template_loader_rejects_nonfinite_curve_after_hash_check(self):
        path = Path(self.temp.name)
        npz_path, json_path = path / "fixture.npz", path / "fixture.json"
        target_names = [
            "glove_c1_thumb_mcp",
            "glove_c2_thumb_ip",
            "glove_c4_index_mcp",
            "glove_c5_index_pip",
            "glove_c7_middle_mcp",
            "glove_c8_middle_pip",
            "glove_c11_ring_mcp",
            "glove_c12_ring_pip",
            "glove_c15_little_mcp",
            "glove_c16_little_pip",
        ]
        payload = {
            "template_sha256": "private-test-template",
            "excluded_subject": "S1",
            "target_contract": {
                "mapping_name": "joint_angles10",
                "output_dim": 10,
                "target_names": target_names,
            },
            "playback": {"duration_s": 9, "cue_on_s": 2, "cue_off_s": 7},
        }
        json_path.write_text(json.dumps(payload), encoding="utf-8")
        poses = np.zeros((40, 181, 10), dtype=np.float32)
        np.savez(
            npz_path,
            actions=np.arange(1, 41),
            playback_time_s=np.arange(181) * 0.05,
            display_sensor_unit=poses,
            raw_j10=poses,
            display_low=np.zeros(10),
            display_high=np.ones(10),
            source_subjects=np.array([f"S{i}" for i in range(2, 35)]),
        )
        hashes = {
            "npz": hashlib.sha256(npz_path.read_bytes()).hexdigest(),
            "json": hashlib.sha256(json_path.read_bytes()).hexdigest(),
        }
        self.assertEqual(
            server.Template.from_files(
                npz_path, json_path, hashes, "private-test-template"
            ).actions[0],
            1,
        )
        poses[0, 0, 0] = np.nan
        np.savez(
            npz_path,
            actions=np.arange(1, 41),
            playback_time_s=np.arange(181) * 0.05,
            display_sensor_unit=poses,
            raw_j10=poses,
            display_low=np.zeros(10),
            display_high=np.ones(10),
            source_subjects=np.array([f"S{i}" for i in range(2, 35)]),
        )
        hashes["npz"] = hashlib.sha256(npz_path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            server.Template.from_files(
                npz_path, json_path, hashes, "private-test-template"
            )

    def test_raw_flush_before_audit_crash_preserves_an_explicit_orphan_interval(self):
        self.cue("start", 25, 0)
        self.append(25, 1)
        # Two raw rows reached disk, but no batch event or acknowledgment exists.
        raw = self.service.session["dir"] / "emg.f32"
        with raw.open("ab") as handle:
            handle.write(np.zeros((2, 8), dtype="<f4").tobytes())
        restarted = server.CaptureService(Path(self.temp.name), template=fixture())
        summary = restarted.status()["session"]
        self.assertEqual(summary["stored_rows"], 3)
        self.assertEqual(
            summary["received_sample_end"], 26
        )  # Never invent host indices for orphan rows.
        orphan = restarted.session["manifest"]["recovery_unacknowledged_raw"][0]
        self.assertEqual((orphan["stored_row_start"], orphan["stored_row_end"]), (1, 3))
        self.assertIsNone(orphan["received_sample_index"])
        self.assertEqual(summary["valid_repetitions"], 0)
        again = server.CaptureService(Path(self.temp.name), template=fixture())
        self.assertEqual(
            len(again.session["manifest"]["recovery_unacknowledged_raw"]), 1
        )

    def test_pause_and_reconnect_gaps_have_disjoint_received_intervals(self):
        self.cue("start", 0, 0)
        self.append(0, 10)
        self.service.pause(
            {
                "session_id": self.sid,
                "reason": "pause",
                "browser_time_ms": 10,
                "received_sample_index": 15,
            }
        )
        self.cue("start", 20, 20, segment=2)
        gaps = self.service.session["manifest"]["gaps"]
        self.assertEqual(
            [(g["received_sample_start"], g["received_sample_end"]) for g in gaps],
            [(10, 15), (15, 20)],
        )
        self.assertTrue(all(not g["stored"] for g in gaps))

    def test_persisted_gap_survives_crash_before_manifest_replacement(self):
        self.cue("start", 0, 0)
        self.append(0, 10)
        original_write = self.service._atomic_manifest
        writes = 0

        def crash_on_pause_manifest():
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("simulated crash after gap event fsync")
            original_write()  # Segment invalidation persisted before the gap.

        with (
            patch.object(self.service, "_atomic_manifest", crash_on_pause_manifest),
            self.assertRaises(OSError),
        ):
            self.service.pause(
                {
                    "session_id": self.sid,
                    "reason": "manual",
                    "browser_time_ms": 10,
                    "received_sample_index": 15,
                }
            )
        self.service = server.CaptureService(Path(self.temp.name), template=fixture())
        self.assertEqual(self.service.status()["session"]["received_sample_end"], 15)
        self.cue("start", 20, 20, segment=2)
        self.service = server.CaptureService(Path(self.temp.name), template=fixture())
        gaps = self.service.session["manifest"]["gaps"]
        self.assertEqual(
            [(g["received_sample_start"], g["received_sample_end"]) for g in gaps],
            [(10, 15), (15, 20)],
        )
        self.assertTrue(all(not g["stored"] for g in gaps))
        self.assertEqual(self.service.status()["session"]["valid_repetitions"], 0)

    def test_finish_requires_all_240_and_never_uses_verified_complete(self):
        with self.assertRaises(server.ApiError) as caught:
            self.service.finish({"session_id": self.sid})
        self.assertEqual(caught.exception.code, "incomplete")
        self.service.session["manifest"]["repetitions"] = [
            {"action": a, "repetition": r, "valid": True}
            for a in range(1, 41)
            for r in range(1, 7)
        ]
        result = self.service.finish({"session_id": self.sid})
        self.assertEqual(result["status"], "capture_finished_unverified")
        self.assertNotIn(
            "verified_complete", json.dumps(self.service.session["manifest"])
        )


class TwoActionSessionTests(unittest.TestCase):
    cue = LiveSessionTests.cue
    append = LiveSessionTests.append

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        checkpoint = self.root / server.NCP512_CHECKPOINT
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"synthetic checkpoint identity")
        self.pin = patch.object(server, "NCP512_CHECKPOINT_SHA256", server._sha256(checkpoint))
        self.pin.start()
        self.service = server.CaptureService(
            self.root, template=fixture(), capture_profile="two-actions"
        )
        self.body = {
            "rate_index": 2, "pga_index": 0, "ack_at_ms": 1,
            "preferences": {"hand": "right", "language": "zh"},
        }
        self.session = self.service.create(self.body)
        self.sid = self.session["session_id"]

    def tearDown(self):
        self.pin.stop()
        self.temp.cleanup()

    def test_plan_preserves_full_template_and_binds_ncp512_identity(self):
        public = self.service.template_public()
        plan = public["capture_plan"]
        self.assertEqual(plan["actions"], [6, 5])  # DB2 global IDs: fist, finger abduction.
        self.assertEqual(plan["action_names"]["6"]["zh"], "握拳")
        self.assertEqual(plan["action_names"]["5"]["zh"], "张开五指")
        self.assertEqual(public["actions"], list(range(1, 41)))
        self.assertEqual(np.asarray(public["poses"]).shape, (40, 181, 10))
        self.assertEqual((plan["groups"], plan["repetitions_per_group"], plan["repetitions"], plan["total"]), (3, 6, 18, 36))
        self.assertEqual(self.session["capture_plan"], plan)
        self.assertEqual(self.service.status()["session"]["capture_plan"], plan)
        manifest = self.service.session["manifest"]
        self.assertEqual(manifest["source_checkpoint"], server.NCP512_CHECKPOINT.as_posix())
        provenance = manifest["model_provenance"]
        self.assertEqual(provenance["checkpoint_sha256"], server.NCP512_CHECKPOINT_SHA256)
        self.assertEqual((provenance["input_size"], provenance["state_size"], provenance["output_size"]), (16, 512, 10))

    def test_nonselected_action_and_repetition_outside_plan_are_rejected(self):
        selected = self.session["capture_plan"]["actions"]
        other = next(a for a in range(1, 41) if a not in selected)
        for action, repetition in ((other, 1), (selected[0], 0), (selected[0], 19)):
            with self.assertRaises(server.ApiError) as caught:
                self.cue("start", 0, 0, action=action, repetition=repetition)
            self.assertEqual(caught.exception.code, "cue")
        self.assertIsNone(self.service.session["active"])

    def test_repetition_group_boundaries_are_derived_and_audited(self):
        action = self.session["capture_plan"]["actions"][0]
        for segment, (repetition, group, within) in enumerate(((1, 1, 1), (6, 1, 6), (7, 2, 1), (13, 3, 1), (18, 3, 6)), 1):
            result = self.cue("start", 0, 0, action=action, repetition=repetition, segment=segment)
            self.assertEqual((result["group"], result["repetition_in_group"]), (group, within))
            self.service.pause({"session_id": self.sid, "reason": "manual", "received_sample_index": 0, "browser_time_ms": 1})
            row = self.service.session["manifest"]["repetitions"][-1]
            self.assertEqual((row["group"], row["repetition_in_group"]), (group, within))

    def test_group_three_complete_repetition_saves_raw_and_summary(self):
        action = self.session["capture_plan"]["actions"][0]
        self.cue("start", 0, 0, action=action, repetition=13)
        self.append(0, 4000)
        self.cue("cue_on", 4000, 2000, action=action, repetition=13)
        self.append(4000, 10000, first_batch=8)
        self.cue("cue_off", 14000, 7000, action=action, repetition=13)
        self.append(14000, 4000, first_batch=28)
        self.cue("complete", 18000, 9000, action=action, repetition=13)
        summary = self.service.status()["session"]
        self.assertEqual(summary["stored_rows"], 18000)
        self.assertEqual(summary["completed"], [{"action": action, "repetition": 13, "group": 3, "repetition_in_group": 1}])
        with self.assertRaises(server.ApiError):
            self.cue("start", 18000, 10000, action=action, repetition=13, segment=2)

    def test_finish_requires_all_36_unique_selected_repetitions(self):
        plan = self.session["capture_plan"]
        rows = [{"action": a, "repetition": r, "valid": True} for a in plan["actions"] for r in range(1, 19)]
        self.service.session["manifest"]["repetitions"] = rows[:-1] + [dict(rows[0])]
        with self.assertRaises(server.ApiError) as caught:
            self.service.finish({"session_id": self.sid})
        self.assertEqual(caught.exception.code, "incomplete")
        self.service.session["manifest"]["repetitions"] = rows
        self.assertEqual(self.service.finish({"session_id": self.sid})["status"], "capture_finished_unverified")

    def test_legacy_session_is_not_mutated_or_reinterpreted_by_two_action_recovery(self):
        self.service.stop({"session_id": self.sid})
        legacy = server.CaptureService(self.root, template=fixture())
        legacy.create(self.body)
        legacy.session["manifest"].pop("capture_plan")
        legacy._atomic_manifest()
        manifest_path = legacy.session["dir"] / "manifest.json"
        before = manifest_path.read_bytes()
        restarted = server.CaptureService(self.root, template=fixture(), capture_profile="two-actions")
        self.assertIsNone(restarted.session)
        self.assertEqual(manifest_path.read_bytes(), before)
        restored = server.CaptureService(self.root, template=fixture())
        self.assertEqual(restored.status()["session"]["capture_plan"]["total"], 240)
        self.assertNotIn("capture_plan", restored.session["manifest"])

    def test_two_action_recovery_retains_plan_and_full_service_skips_it(self):
        manifest_path = self.service.session["dir"] / "manifest.json"
        before = manifest_path.read_bytes()
        self.assertIsNone(server.CaptureService(self.root, template=fixture()).session)
        self.assertEqual(manifest_path.read_bytes(), before)
        restored = server.CaptureService(self.root, template=fixture(), capture_profile="two-actions")
        self.assertEqual(restored.status()["session"]["capture_plan"], self.session["capture_plan"])
        self.assertEqual(restored.status()["session"]["session_id"], self.sid)

    def test_missing_or_wrong_checkpoint_prevents_false_provenance(self):
        self.service.stop({"session_id": self.sid})
        checkpoint = self.root / server.NCP512_CHECKPOINT
        checkpoint.write_bytes(b"different checkpoint")
        with self.assertRaises(server.ApiError) as caught:
            self.service.create(self.body)
        self.assertEqual(caught.exception.code, "blocked_checkpoint")
        checkpoint.unlink()
        with self.assertRaises(server.ApiError):
            self.service.create(self.body)
        self.assertEqual(len(list(self.service.output_root.iterdir())), 1)


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        server.Handler.service = server.CaptureService(
            Path(self.temp.name), template=fixture()
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join()
        self.httpd.server_close()
        self.temp.cleanup()

    def request(self, method, path, host=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port)
        headers = {"Host": host or f"127.0.0.1:{self.httpd.server_port}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        c.request(
            method,
            path,
            body=json.dumps(body) if body is not None else None,
            headers=headers,
        )
        response = c.getresponse()
        value = response.read()
        c.close()
        return response.status, value

    def test_same_origin_and_private_source(self):
        self.assertEqual(self.request("GET", "/api/status", host="evil.test")[0], 403)
        self.assertEqual(self.request("GET", "/server.py")[0], 404)
        self.assertEqual(self.request("HEAD", "/%73erver%2epy")[0], 404)
        self.assertEqual(self.request("GET", "/tests/test_app.js")[0], 404)
        self.assertEqual(
            self.request("POST", "/api/train", body={"verified": True})[0], 409
        )


if __name__ == "__main__":
    unittest.main()
