"""Loopback capture server; microvolt input saved as volts, no model activation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import secrets
import sys
import threading
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import numpy as np
except ImportError:
    np = None

MAX_BODY = 256 * 1024
MAX_ROWS = 512
TEMPLATE_DIR = Path(
    "log/db2_paper_cfc_finetune/source33_template_noquery_atl60_lr3e5_20260914_095931"
)
TEMPLATE_HASHES = {
    "npz": "0d7e2be8c264b8e57fe64003cca78c5642eab3d5a092079af31178a3179df92f",
    "json": "b6e543d6e93379ec701ba872569cdd25eedd12317163e18d3030b6b3c29898b0",
}
PUBLIC_TEMPLATE_HASH = (
    "b2b16a327b16ba536daca7b7573d9289fb779252bed1011b29329e4ff5d98fce"
)
J10 = [
    "thumb_mcp",
    "thumb_ip",
    "index_mcp",
    "index_pip",
    "middle_mcp",
    "middle_pip",
    "ring_mcp",
    "ring_pip",
    "little_mcp",
    "little_pip",
]
PGA_GAINS = (1, 2, 3, 4, 6, 8, 12)
STATIC_SUFFIXES = {".css", ".glb", ".html", ".js", ".png", ".svg", ".webp"}
NCP512_CHECKPOINT = Path(
    "log/db2_paper_cfc_finetune/new_stateful_test_11_lr1e-4_ncpwiring/checkpoints/S1_autoncp_cfc_pretrain.pt"
)
NCP512_CHECKPOINT_SHA256 = (
    "8c214094387222409aa7a038bfcd313e2c6156904a39c038ccb141bf1cc27769"
)


def _capture_plan(profile):
    if profile == "full":
        actions, groups, names = list(range(1, 41)), 1, {}
    elif profile == "two-actions":
        # DB2 exercise B/global IDs; preserve the pinned template's row mapping.
        actions, groups = [6, 5], 3
        names = {
            "6": {"zh": "握拳", "en": "Make a fist"},
            "5": {"zh": "张开五指", "en": "Spread all fingers"},
        }
    else:
        raise ValueError("unknown capture profile")
    return {
        "profile": profile,
        "actions": actions,
        "groups": groups,
        "repetitions_per_group": 6,
        "repetitions": groups * 6,
        "total": len(actions) * groups * 6,
        "action_names": names,
    }


class ApiError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite_number(value):
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


def _clock_number(value):
    return _finite_number(value) and value >= 0


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON numbers must be finite")
    return number


class Template:
    def __init__(
        self,
        actions,
        time_s,
        poses,
        channels,
        hashes,
        display_low=None,
        display_high=None,
    ):
        self.actions, self.time_s, self.poses, self.channels, self.hashes = (
            actions,
            time_s,
            poses,
            channels,
            hashes,
        )
        self.display_low, self.display_high = display_low, display_high

    @classmethod
    def production(cls, root):
        if np is None:
            raise ValueError("numpy missing")
        directory = root / TEMPLATE_DIR
        npz_path, json_path = directory / "templates.npz", directory / "templates.json"
        return cls.from_files(
            npz_path, json_path, TEMPLATE_HASHES, PUBLIC_TEMPLATE_HASH
        )

    @classmethod
    def from_files(cls, npz_path, json_path, expected_hashes, public_template_hash):
        """Load a fixed template; test fixtures may pass their own expected pins."""
        if np is None:
            raise ValueError("numpy missing")
        if not npz_path.is_file() or not json_path.is_file():
            raise ValueError("pinned template files are missing")
        if (
            _sha256(npz_path) != expected_hashes["npz"]
            or _sha256(json_path) != expected_hashes["json"]
        ):
            raise ValueError("pinned template hash mismatch")
        metadata = json.loads(json_path.read_text(encoding="utf-8"))
        with np.load(npz_path, allow_pickle=False) as data:
            actions, time_s, poses, raw = (
                data["actions"].tolist(),
                data["playback_time_s"].astype(float).tolist(),
                data["display_sensor_unit"].astype(float).tolist(),
                data["raw_j10"],
            )
            low, high = (
                data["display_low"].astype(float).tolist(),
                data["display_high"].astype(float).tolist(),
            )
            channels, sources = J10, data["source_subjects"].tolist()
        contract, playback = (
            metadata.get("target_contract", {}),
            metadata.get("playback", {}),
        )
        expected_names = [
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
        if metadata.get("template_sha256") != public_template_hash:
            raise ValueError("public template digest mismatch")
        if (
            contract.get("mapping_name") != "joint_angles10"
            or contract.get("output_dim") != 10
            or contract.get("target_names") != expected_names
        ):
            raise ValueError("template target contract mismatch")
        if (
            playback.get("duration_s") != 9
            or playback.get("cue_on_s") != 2
            or playback.get("cue_off_s") != 7
        ):
            raise ValueError("template cue boundary mismatch")
        if (
            actions != list(range(1, 41))
            or len(time_s) != 181
            or not np.isfinite(time_s).all()
            or any(abs(t - i * 0.05) > 1e-7 for i, t in enumerate(time_s))
        ):
            raise ValueError("template action or timeline mismatch")
        if (
            np.asarray(poses).shape != (40, 181, 10)
            or raw.shape != (40, 181, 10)
            or not np.isfinite(poses).all()
            or not np.isfinite(raw).all()
        ):
            raise ValueError("template pose shape mismatch")
        if (
            len(low) != 10
            or len(high) != 10
            or not np.isfinite(low + high).all()
            or any(a >= b for a, b in zip(low, high))
        ):
            raise ValueError("template display range mismatch")
        if (
            list(channels) != J10
            or len(sources) != 33
            or not all(isinstance(subject, str) for subject in sources)
            or "S1" in sources
            or metadata.get("excluded_subject") != "S1"
        ):
            raise ValueError("template source/channel mismatch")
        return cls(
            actions, time_s, poses, list(channels), dict(expected_hashes), low, high
        )

    def public(self):
        return {
            "actions": self.actions,
            "repetitions": 6,
            "duration_s": 9,
            "cue_on_s": 2,
            "cue_off_s": 7,
            "time_s": self.time_s,
            "poses": self.poses,
            "target_channels": self.channels,
            "display_low": self.display_low,
            "display_high": self.display_high,
            "hashes": self.hashes,
        }


class CaptureService:
    def __init__(
        self,
        root,
        template=None,
        template_error=None,
        *,
        output_root=None,
        capture_profile="full",
    ):
        self.capture_plan = _capture_plan(capture_profile)
        self.root, self.output_root = (
            Path(root).resolve(),
            Path(output_root).resolve()
            if output_root is not None
            else Path(root).resolve() / "log" / "live_sessions",
        )
        self.template_error, self.template, self.session, self.lock = (
            template_error,
            template,
            None,
            threading.RLock(),
        )
        if template is None and template_error is None:
            try:
                self.template = Template.production(self.root)
            except (OSError, ValueError, KeyError) as exc:
                self.template_error = str(exc)
        self._recover_latest()

    def _recover_latest(self):
        """Restore one unfinished local session after a server restart."""
        if not self.output_root.is_dir():
            return
        for directory in sorted(self.output_root.iterdir(), reverse=True):
            manifest_path = directory / "manifest.json"
            raw_path = directory / "emg.f32"
            if not manifest_path.is_file() or not raw_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("schema_version") != 1 or manifest.get(
                    "status"
                ) not in {"recording", "paused"}:
                    continue
                plan = manifest.get("capture_plan", _capture_plan("full"))
                if plan != self.capture_plan:
                    continue
                manifest.setdefault("stored_units", "uV")
                rows, remainder = divmod(raw_path.stat().st_size, 8 * 4)
                if remainder:
                    continue
                active = manifest.get("active_segment")
                audited_rows = 0
                with (directory / "events.jsonl").open(encoding="utf-8") as events:
                    for line in events:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue  # An interrupted event cannot certify raw rows.
                        if event.get("event") in {
                            "batch",
                            "recovery_unacknowledged_raw",
                        }:
                            audited_rows = max(audited_rows, event["stored_row_end"])
                            if (
                                active
                                and event.get("event") == "batch"
                                and event["segment_id"] == active["segment_id"]
                            ):
                                active["next_index"] = max(
                                    active["next_index"], event["received_sample_end"]
                                )
                        elif event.get("event") == "received_gap":
                            gap = {
                                key: event[key]
                                for key in (
                                    "received_sample_start",
                                    "received_sample_end",
                                    "reason",
                                    "stored",
                                    "clock_domain",
                                )
                            }
                            gaps = manifest.setdefault("gaps", [])
                            if gap not in gaps:
                                gaps.append(gap)
                            manifest["received_sample_end"] = max(
                                manifest.get("received_sample_end", 0),
                                gap["received_sample_end"],
                            )
                segment_ids = [
                    item.get("segment_id", 0) for item in manifest.get("segments", [])
                ]
                if active:
                    segment_ids.append(active.get("segment_id", 0))
                self.session = {
                    "dir": directory,
                    "manifest": manifest,
                    "rows": rows,
                    "next_segment": max(segment_ids, default=0) + 1,
                    "active": active,
                }
                if active:
                    self._invalidate("server_restart", active["next_index"])
                self.session["manifest"]["status"] = "paused"
                if rows > audited_rows:
                    interval = {
                        "stored_row_start": audited_rows,
                        "stored_row_end": rows,
                        "received_sample_index": None,
                        "valid": False,
                    }
                    manifest.setdefault("recovery_unacknowledged_raw", []).append(
                        interval
                    )
                    self._event("recovery_unacknowledged_raw", **interval)
                elif rows < audited_rows:
                    manifest["status"] = "failed"
                    manifest["failure"] = "raw_file_shorter_than_batch_audit"
                self._event("server_restart", stored_rows=rows)
                self._atomic_manifest()
                return
            except (OSError, ValueError, TypeError):
                continue

    @property
    def blockers(self):
        out = ["blocked_unit_evidence"]
        if self.template is None:
            out.append("blocked_template")
        if np is None or self.model_runtime_missing:
            out.append("blocked_runtime")
        return out

    @property
    def model_runtime_missing(self):
        return [
            name
            for name in ("torch", "ncps", "scipy")
            if importlib.util.find_spec(name) is None
        ]

    def _atomic_manifest(self):
        self.session["manifest"]["active_segment"] = self.session["active"]
        dest = self.session["dir"] / "manifest.json"
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.session["manifest"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, dest)

    def _event(self, kind, **values):
        event = {
            "schema_version": 1,
            "event": kind,
            "at": datetime.now(UTC).isoformat(),
            **values,
        }
        with (self.session["dir"] / "events.jsonl").open("a", encoding="utf-8") as h:
            h.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            h.flush()
            os.fsync(h.fileno())

    def _summary(self):
        if not self.session:
            return None
        m = self.session["manifest"]
        completed = [
            {
                "action": r["action"],
                "repetition": r["repetition"],
                **self._group_metadata(r["repetition"]),
            }
            for r in m["repetitions"]
            if r["valid"]
        ]
        return {
            "session_id": m["session_id"],
            "status": m["status"],
            "valid_repetitions": len(completed),
            "completed": completed,
            "segments": len(m["segments"]),
            "next_segment_id": self.session["next_segment"],
            "received_sample_end": max(
                [m.get("received_sample_end", 0)]
                + [segment.get("next_index", -1) for segment in m["segments"]]
                + (
                    [self.session["active"]["next_index"]]
                    if self.session["active"]
                    else []
                )
            ),
            "stored_rows": self.session["rows"],
            "preferences": m["preferences"],
            "parameters": m["parameters"],
            "capture_plan": self._session_plan(),
        }

    def _session_plan(self):
        return self.session["manifest"].get("capture_plan", _capture_plan("full"))

    def template_public(self):
        return {**self.template.public(), "capture_plan": self.capture_plan}

    def _group_metadata(self, repetition):
        per_group = self._session_plan()["repetitions_per_group"]
        return {
            "group": (repetition - 1) // per_group + 1,
            "repetition_in_group": (repetition - 1) % per_group + 1,
        }

    def status(self):
        with self.lock:
            training = "blocked_unit_evidence"
            if self.model_runtime_missing:
                training = "blocked_runtime"
            return {
                "status": self.session["manifest"]["status"]
                if self.session
                else "closed",
                "blockers": self.blockers,
                "session": self._summary(),
                "training": {"status": training, "missing": self.model_runtime_missing},
                "templates_available": self.template is not None,
            }

    def create(self, body):
        with self.lock:
            if self.session and self.session["manifest"]["status"] not in {
                "closed",
                "capture_finished_unverified",
                "failed",
            }:
                raise ApiError(
                    "active_session", "pause or stop the active session first", 409
                )
            if (
                body.get("rate_index") != 2
                or type(body.get("pga_index")) is not int
                or not 0 <= body["pga_index"] <= 6
            ):
                raise ApiError(
                    "parameters", "only rate_index 2 and PGA indices 0..6 are accepted"
                )
            prefs = body.get("preferences")
            if (
                not isinstance(prefs, dict)
                or set(prefs) != {"hand", "language"}
                or type(prefs["hand"]) is not str
                or type(prefs["language"]) is not str
                or prefs["hand"] not in {"left", "right"}
                or prefs["language"] not in {"zh", "en"}
            ):
                raise ApiError(
                    "preferences", "hand is left/right and language is zh/en"
                )
            if not _clock_number(body.get("ack_at_ms")):
                raise ApiError("parameters", "ack_at_ms must be finite")
            origin = body.get("browser_time_origin_ms")
            if origin is not None and not _clock_number(origin):
                raise ApiError("parameters", "browser_time_origin_ms must be finite")
            checkpoint = None
            if self.capture_plan["profile"] == "two-actions":
                path = self.root / NCP512_CHECKPOINT
                if not path.is_file() or _sha256(path) != NCP512_CHECKPOINT_SHA256:
                    raise ApiError(
                        "blocked_checkpoint",
                        "pinned NCP512 checkpoint is missing or mismatched",
                        409,
                    )
                checkpoint = {
                    "path": NCP512_CHECKPOINT.as_posix(),
                    "checkpoint_sha256": NCP512_CHECKPOINT_SHA256,
                    "model_family": "autoncp_cfc_linear",
                    "input_size": 16,
                    "state_size": 512,
                    "output_size": 10,
                }
            sid = (
                datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                + "-"
                + secrets.token_hex(4)
            )
            directory = self.output_root / sid
            directory.mkdir(parents=True, exist_ok=False)
            params = {
                "rate_index": 2,
                "pga_index": body["pga_index"],
                "gain": PGA_GAINS[body["pga_index"]],
                "ack_at_ms": body["ack_at_ms"],
            }
            if origin is not None:
                params["browser_time_origin_ms"] = origin
            manifest = {
                "schema_version": 1,
                "session_id": sid,
                "status": "recording",
                "created_at": datetime.now(UTC).isoformat(),
                "declared_rate_hz": 2000,
                "channel_order": list(range(1, 9)),
                "wire_units": "uV",
                "stored_units": "V",
                "dtype": "<f4",
                "row_width": 8,
                "parameters": params,
                "preferences": prefs,
                "template_hashes": self.template.hashes
                if self.template
                else TEMPLATE_HASHES,
                "source_checkpoint": "log/db2_paper_cfc_finetune/new_stateful_test_12_weighted_ch2ch3_x2_lr3e-5_ep30_20260909_194121/checkpoints/S1_autoncp_cfc_pretrain.pt",
                "segments": [],
                "repetitions": [],
                "blockers": self.blockers,
                "dsp_status": "not_applied",
                "scale_status": "blocked_unit_evidence",
                "device_continuity": "unverified_legacy_wire",
                "capture_plan": self.capture_plan,
            }
            if checkpoint:
                manifest["source_checkpoint"] = checkpoint["path"]
                manifest["model_provenance"] = checkpoint
            (directory / "emg.f32").touch()
            (directory / "events.jsonl").touch()
            self.session = {
                "dir": directory,
                "manifest": manifest,
                "rows": 0,
                "next_segment": 1,
                "active": None,
            }
            self._atomic_manifest()
            self._event("parameter_ack", **params)
            return {"session_id": sid, **self._summary(), "blockers": self.blockers}

    def _require(self, body, allow_paused=False):
        if (
            not self.session
            or body.get("session_id") != self.session["manifest"]["session_id"]
        ):
            raise ApiError("session", "unknown session", 404)
        if self.session["manifest"]["status"] != "recording" and not (
            allow_paused and self.session["manifest"]["status"] == "paused"
        ):
            raise ApiError("session_state", "session is not recording", 409)

    def _invalidate(self, reason, index=None, *, valid=False):
        active = self.session["active"]
        if not active:
            return
        active["valid"], active["reason"] = valid, reason
        if not valid:
            self.session["manifest"]["repetitions"].append(
                {
                    "action": active["action"],
                    "repetition": active["repetition"],
                    **self._group_metadata(active["repetition"]),
                    "segment_id": active["segment_id"],
                    "valid": False,
                    "reason": reason,
                    "start_index": active["start_index"],
                    "end_index": active["next_index"],
                    "cues": active["cues"],
                }
            )
        self._event(
            "segment_end",
            segment_id=active["segment_id"],
            reason=reason,
            received_sample_index=index if _nonnegative_int(index) else None,
        )
        self.session["manifest"]["segments"].append(
            {
                k: active.get(k)
                for k in (
                    "segment_id",
                    "action",
                    "repetition",
                    "group",
                    "repetition_in_group",
                    "valid",
                    "reason",
                    "gap_reason",
                    "start_index",
                    "next_index",
                )
            }
        )
        self.session["active"] = None
        self._atomic_manifest()

    def _record_gap(self, end, reason):
        """Record host-received rows intentionally outside an accepted segment."""
        start = self._summary()["received_sample_end"]
        if _nonnegative_int(end) and end > start:
            gap = {
                "received_sample_start": start,
                "received_sample_end": end,
                "reason": reason,
                "stored": False,
                "clock_domain": "host_received_index",
            }
            self.session["manifest"].setdefault("gaps", []).append(gap)
            self.session["manifest"]["received_sample_end"] = end
            self._event("received_gap", **gap)

    def samples(self, body):
        with self.lock:
            self._require(body)
            active = self.session["active"]
            rows = body.get("samples")
            if not active:
                raise ApiError("segment", "a start cue is required before samples", 409)
            if (
                not _nonnegative_int(body.get("segment_id"))
                or body.get("segment_id") != active["segment_id"]
                or not isinstance(rows, list)
                or not 1 <= len(rows) <= MAX_ROWS
                or not _nonnegative_int(body.get("batch_id"))
                or not _nonnegative_int(body.get("received_sample_index"))
                or not _clock_number(body.get("received_at_ms"))
            ):
                self._invalidate("malformed_samples", body.get("received_sample_index"))
                raise ApiError(
                    "samples", "samples require finite metadata and 1..512 rows"
                )
            if (
                body["batch_id"] != active["next_batch"]
                or body["received_sample_index"] != active["next_index"]
            ):
                self._invalidate("ordering", body.get("received_sample_index"))
                raise ApiError(
                    "ordering", "batch or received index is not consecutive", 409
                )
            if any(
                not isinstance(row, list)
                or len(row) != 8
                or any(not _finite_number(value) for value in row)
                for row in rows
            ):
                self._invalidate("malformed_samples", body["received_sample_index"])
                raise ApiError(
                    "samples", "samples must be finite numeric 8-channel rows"
                )
            try:
                matrix = np.asarray(rows, dtype="<f4") if np is not None else None
            except (TypeError, ValueError):
                matrix = None
            if (
                matrix is None
                or matrix.shape != (len(rows), 8)
                or not np.isfinite(matrix).all()
            ):
                self._invalidate("malformed_samples", body.get("received_sample_index"))
                raise ApiError("samples", "samples must be finite 8-channel numbers")
            if self.session["manifest"]["stored_units"] == "V":
                matrix *= 1e-6
            start, stored = active["next_index"], self.session["rows"]
            with (self.session["dir"] / "emg.f32").open("ab") as h:
                h.write(matrix.tobytes(order="C"))
                h.flush()
                os.fsync(h.fileno())
            active["next_index"] += len(rows)
            active["next_batch"] += 1
            self.session["rows"] += len(rows)
            self._event(
                "batch",
                batch_id=body["batch_id"],
                segment_id=active["segment_id"],
                received_sample_start=start,
                received_sample_end=start + len(rows),
                stored_row_start=stored,
                stored_row_end=stored + len(rows),
                first_receipt_ms=body.get("received_at_ms"),
                last_receipt_ms=body.get("received_at_ms"),
                clock_domain="browser_performance_ms",
            )
            self._atomic_manifest()
            return {
                "count": len(rows),
                "received_sample_start": start,
                "received_sample_end": start + len(rows),
                "stored_row_start": stored,
                "stored_row_end": stored + len(rows),
                "status": "recording",
            }

    def cue(self, body):
        with self.lock:
            resuming = bool(
                self.session
                and self.session["manifest"]["status"] == "paused"
                and body.get("event") == "start"
            )
            if resuming:
                if body.get("session_id") != self.session["manifest"]["session_id"]:
                    raise ApiError("session", "unknown session", 404)
            else:
                self._require(body)
            event, action, repetition = (
                body.get("event"),
                body.get("action"),
                body.get("repetition"),
            )
            plan = self._session_plan()
            if (
                type(event) is not str
                or event not in {"start", "cue_on", "cue_off", "complete"}
                or type(action) is not int
                or action not in plan["actions"]
                or type(repetition) is not int
                or not 1 <= repetition <= plan["repetitions"]
            ):
                if self.session["active"]:
                    self._invalidate("invalid_cue", body.get("received_sample_index"))
                raise ApiError("cue", "invalid cue")
            if (
                not _nonnegative_int(body.get("segment_id"))
                or not _nonnegative_int(body.get("received_sample_index"))
                or not _clock_number(body.get("browser_time_ms"))
            ):
                if self.session["active"]:
                    self._invalidate("malformed_cue", body.get("received_sample_index"))
                raise ApiError(
                    "cue", "cue requires finite clock and nonnegative segment/index"
                )
            for field in ("ack_at_ms", "browser_time_origin_ms"):
                if field in body and not _clock_number(body[field]):
                    if self.session["active"]:
                        self._invalidate(
                            "malformed_cue", body.get("received_sample_index")
                        )
                    raise ApiError("cue", f"{field} must be finite")
            active = self.session["active"]
            if event == "start":
                if active:
                    raise ApiError(
                        "segment", "current repetition must be paused or completed", 409
                    )
                if body["segment_id"] != self.session["next_segment"]:
                    raise ApiError(
                        "segment", "segment id must be next allocated value", 409
                    )
                if any(
                    r["valid"]
                    and r["action"] == action
                    and r["repetition"] == repetition
                    for r in self.session["manifest"]["repetitions"]
                ):
                    raise ApiError("repetition", "repetition already complete", 409)
                last = self._summary()["received_sample_end"]
                if body["received_sample_index"] < last:
                    raise ApiError(
                        "segment", "new segment index cannot precede prior segment", 409
                    )
                reason = body.get("gap_reason", "reconnect")
                if type(reason) is not str or reason not in {
                    "startup_preview",
                    "reconnected",
                    "repetition_transition",
                    "reconnect",
                }:
                    raise ApiError("gap_reason", "unknown capture gap reason")
                self._record_gap(body["received_sample_index"], reason)
                active = {
                    "segment_id": body["segment_id"],
                    "action": action,
                    "repetition": repetition,
                    **self._group_metadata(repetition),
                    "start_index": body["received_sample_index"],
                    "next_index": body["received_sample_index"],
                    "next_batch": 0,
                    "valid": True,
                    "cues": {"start": body["browser_time_ms"]},
                    "gap_reason": reason
                    if body["received_sample_index"] > last
                    else None,
                }
                self.session["manifest"]["status"] = "recording"
                self.session["active"] = active
                self.session["next_segment"] += 1
                self._event(
                    "segment_begin",
                    segment_id=active["segment_id"],
                    action=action,
                    repetition=repetition,
                    **self._group_metadata(repetition),
                    received_sample_index=active["start_index"],
                    gap_reason=active["gap_reason"],
                    ack_at_ms=body.get("ack_at_ms"),
                    browser_time_origin_ms=body.get("browser_time_origin_ms"),
                )
            else:
                if (
                    not active
                    or body["segment_id"] != active["segment_id"]
                    or (action, repetition) != (active["action"], active["repetition"])
                    or body["received_sample_index"] != active["next_index"]
                ):
                    if active:
                        self._invalidate(
                            "cue_mismatch", body.get("received_sample_index")
                        )
                    raise ApiError(
                        "cue_order", "cue does not match active segment/index", 409
                    )
                required = {
                    "cue_on": "start",
                    "cue_off": "cue_on",
                    "complete": "cue_off",
                }[event]
                if required not in active["cues"] or event in active["cues"]:
                    self._invalidate("cue_order", body["received_sample_index"])
                    raise ApiError("cue_order", "cue order is invalid", 409)
                target = {"cue_on": 2000, "cue_off": 7000, "complete": 9000}[event]
                if (
                    abs(body["browser_time_ms"] - active["cues"]["start"] - target)
                    > 250
                ):
                    self._invalidate("cue_timing", body["received_sample_index"])
                    raise ApiError(
                        "cue_timing", "cue timing is outside 250 ms host tolerance", 409
                    )
                expected_span = {"cue_on": 4000, "cue_off": 14000, "complete": 18000}[
                    event
                ]
                actual_span = body["received_sample_index"] - active["start_index"]
                if abs(actual_span - expected_span) > 500:
                    self._invalidate("cue_sample_span", body["received_sample_index"])
                    raise ApiError(
                        "cue_sample_span",
                        "cue sample index is outside 2000 Hz tolerance",
                        409,
                    )
                active["cues"][event] = body["browser_time_ms"]
            self._event(
                "cue_" + event,
                action=action,
                repetition=repetition,
                **self._group_metadata(repetition),
                segment_id=body["segment_id"],
                received_sample_index=body["received_sample_index"],
                browser_time_ms=body["browser_time_ms"],
            )
            if event == "complete":
                span = active["next_index"] - active["start_index"]
                if not 17500 <= span <= 18500:
                    self._invalidate("sample_duration", active["next_index"])
                    raise ApiError(
                        "sample_duration",
                        "a completed repetition needs approximately 9 seconds of 2000 Hz samples",
                        409,
                    )
                self.session["manifest"]["repetitions"].append(
                    {
                        "action": action,
                        "repetition": repetition,
                        **self._group_metadata(repetition),
                        "segment_id": active["segment_id"],
                        "valid": True,
                        "start_index": active["start_index"],
                        "end_index": active["next_index"],
                        "cues": active["cues"],
                    }
                )
                self._invalidate("complete", active["next_index"], valid=True)
                self._event(
                    "capture_transition",
                    received_sample_start=active["next_index"],
                    reason="awaiting_next_repetition",
                    stored=False,
                )
            self._atomic_manifest()
            return {
                "status": "recording",
                "event": event,
                "action": action,
                "repetition": repetition,
                **self._group_metadata(repetition),
                "valid_repetitions": sum(
                    r["valid"] for r in self.session["manifest"]["repetitions"]
                ),
            }

    def pause(self, body):
        with self.lock:
            self._require(body, allow_paused=True)
            active = self.session["active"]
            if self.session["manifest"]["status"] == "paused":
                return {"status": "paused", "invalidated": None}
            if not _clock_number(body.get("browser_time_ms")) or not _nonnegative_int(
                body.get("received_sample_index")
            ):
                if active:
                    self._invalidate(
                        "malformed_pause", body.get("received_sample_index")
                    )
                raise ApiError(
                    "pause",
                    "pause requires finite clock and nonnegative received index",
                )
            if active:
                self._invalidate(
                    body.get("reason", "pause"), body.get("received_sample_index")
                )
            self._record_gap(body["received_sample_index"], body.get("reason", "pause"))
            self.session["manifest"]["status"] = "paused"
            self._event(
                "pause",
                reason=body.get("reason"),
                browser_time_ms=body.get("browser_time_ms"),
                received_sample_index=body.get("received_sample_index"),
            )
            self._atomic_manifest()
            return {
                "status": "paused",
                "invalidated": {
                    "action": active["action"],
                    "repetition": active["repetition"],
                }
                if active
                else None,
            }

    def finish(self, body):
        with self.lock:
            self._require(body, allow_paused=True)
            if self.session["active"]:
                raise ApiError(
                    "active_repetition", "pause or complete the active repetition", 409
                )
            valid = [r for r in self.session["manifest"]["repetitions"] if r["valid"]]
            done = len(valid)
            plan = self._session_plan()
            expected = {
                (a, r)
                for a in plan["actions"]
                for r in range(1, plan["repetitions"] + 1)
            }
            if (
                done != plan["total"]
                or {(r["action"], r["repetition"]) for r in valid} != expected
            ):
                remaining = expected - {(r["action"], r["repetition"]) for r in valid}
                raise ApiError("incomplete", f"{len(remaining)} repetitions remain", 409)
            self._record_gap(body.get("received_sample_index"), "recording_finished")
            self.session["manifest"]["status"] = "capture_finished_unverified"
            self._event("finalization", valid_repetitions=done)
            self._atomic_manifest()
            return {
                "status": "capture_finished_unverified",
                "remaining": 0,
                "blockers": self.blockers,
            }

    def stop(self, body):
        with self.lock:
            if (
                not self.session
                or body.get("session_id") != self.session["manifest"]["session_id"]
            ):
                raise ApiError("session", "unknown session", 404)
            if self.session["manifest"]["status"] in {
                "closed",
                "capture_finished_unverified",
                "failed",
            }:
                return {"status": self.session["manifest"]["status"]}
            if self.session["manifest"]["status"] not in {"recording", "paused"}:
                raise ApiError("session_state", "session cannot be stopped", 409)
            if self.session["active"]:
                self._invalidate("stop", body.get("received_sample_index"))
            self._record_gap(body.get("received_sample_index"), "stop")
            self.session["manifest"]["status"] = "closed"
            self._event("stop")
            self._atomic_manifest()
            return {"status": "closed"}

    def activation(self, body):
        raise ApiError(
            "blocked_unit_evidence",
            "DB2-to-device unit provenance is required before ATL or live inference",
            409,
        )

    def fail(self, message):
        """Best-effort persistent failure state for storage errors."""
        with self.lock:
            if not self.session:
                return
            self.session["manifest"]["status"] = "failed"
            self.session["manifest"]["failure"] = message
            try:
                self._atomic_manifest()
            except OSError:
                pass


class Handler(SimpleHTTPRequestHandler):
    service = None
    server_version = "AntikytheraLoopback/1"

    def __init__(self, *args, **kwargs):
        kwargs["directory"] = str(self.service.root / "ui")
        super().__init__(*args, **kwargs)

    def log_message(self, *_):
        pass

    def _same_origin(self):
        allowed = {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }
        return self.headers.get("Host", "") in allowed and (
            not self.headers.get("Origin")
            or self.headers["Origin"] in {f"http://{x}" for x in allowed}
        )

    def _json(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        if (
            self.headers.get("Content-Type", "").split(";", 1)[0].lower()
            != "application/json"
        ):
            raise ApiError("content_type", "Content-Type must be application/json", 415)
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            size = -1
        if size < 0 or size > MAX_BODY:
            raise ApiError("body_size", "JSON body exceeds 256 KiB", 413)
        try:
            value = json.loads(
                self.rfile.read(size),
                parse_float=_json_float,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ApiError("json", "malformed JSON")
        if not isinstance(value, dict):
            raise ApiError("json", "JSON body must be object")
        return value

    def _static_path(self):
        decoded = unquote(urlsplit(self.path).path)
        relative = decoded.lstrip("/") or "index.html"
        candidate = (self.service.root / "ui" / relative).resolve()
        static_root = (self.service.root / "ui").resolve()
        if (
            not candidate.is_file()
            or static_root not in candidate.parents
            or candidate.suffix.lower() not in STATIC_SUFFIXES
        ):
            return None
        return candidate

    def _serve_static(self, send_body):
        path = self._static_path()
        if path is None:
            return self.send_error(404)
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if send_body:
            self.wfile.write(content)

    def do_GET(self):
        if self.path == "/api/status":
            return (
                self._json(200, self.service.status())
                if self._same_origin()
                else self._json(
                    403, {"error": "origin", "message": "same-origin required"}
                )
            )
        if self.path == "/api/templates":
            if not self._same_origin():
                return self._json(
                    403, {"error": "origin", "message": "same-origin required"}
                )
            if not self.service.template:
                return self._json(
                    503,
                    {
                        "error": "blocked_template",
                        "message": self.service.template_error
                        or "template unavailable",
                    },
                )
            return self._json(200, self.service.template_public())
        if self.path.startswith("/api/"):
            return self._json(404, {"error": "endpoint", "message": "unknown endpoint"})
        if not self._same_origin():
            return self.send_error(404)
        return self._serve_static(True)

    def do_HEAD(self):
        if not self._same_origin():
            return self.send_error(404)
        return self._serve_static(False)

    def do_POST(self):
        if not self._same_origin():
            return self._json(
                403, {"error": "origin", "message": "same-origin required"}
            )
        method = {
            "/api/session": "create",
            "/api/samples": "samples",
            "/api/cue": "cue",
            "/api/pause": "pause",
            "/api/finish": "finish",
            "/api/stop": "stop",
            "/api/train": "activation",
            "/api/live": "activation",
        }.get(self.path)
        if not method:
            return self._json(404, {"error": "endpoint", "message": "unknown endpoint"})
        try:
            result = getattr(self.service, method)(self._body())
        except ApiError as exc:
            return self._json(exc.status, {"error": exc.code, "message": exc.message})
        except OSError:
            self.service.fail("disk_io")
            return self._json(
                507,
                {
                    "error": "storage",
                    "message": "session storage failed; recording is closed",
                },
            )
        return self._json(200, result)


def serve(root, port, capture_profile="full"):
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Python 3.13 is required")
    Handler.service = CaptureService(root, capture_profile=capture_profile)
    Handler.directory = str(Path(root) / "ui")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Antikythera UI: http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--capture-profile", choices=("full", "two-actions"), default="full"
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    serve(args.root, args.port, args.capture_profile)
