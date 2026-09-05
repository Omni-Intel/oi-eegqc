"""NDJSON stdio sidecar for an Electron (or any) frontend.

One JSON object per line on stdin; one or more JSON objects per line on stdout.
Requests are handled sequentially. A ``cancel`` line interrupts the in-flight
batch between recordings (in-progress ``evaluate_recording`` still finishes).

Recognised ops: ``ping``, ``list_datasets``, ``score_file``, ``score_dataset``,
``cancel``, ``shutdown``.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Mapping, TextIO

from . import __version__
from .config import load_config
from .datasets import DEFAULT_NOD_CHANNELS_TSV, list_datasets, open_dataset, score_adapter
from .io import load_edf_bdf, load_npy
from .io.reports import batch_envelope
from .pipeline import evaluate_recording
from .protocol import (
    PROTOCOL_SCHEMA_VERSION,
    ProtocolError,
    envelope,
    map_exception,
    write_ndjson,
)

_OPS = {"ping", "list_datasets", "score_file", "score_dataset"}
_CONTROL_OPS = {"cancel", "shutdown"}


class StdioServer:
    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._current_id: str | None = None
        self._shutdown = False

    def emit(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            write_ndjson(payload, self.stdout)

    def run(self) -> int:
        """Read stdin on a side thread so ``cancel`` can interrupt a batch."""
        incoming: queue.Queue = queue.Queue()

        def reader() -> None:
            while True:
                line = self.stdin.readline()
                if line == "":
                    incoming.put(None)
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except json.JSONDecodeError as exc:
                    self.emit(
                        ProtocolError("invalid_request", f"malformed JSON: {exc}").to_dict()
                    )
                    continue
                if not isinstance(req, dict):
                    self.emit(
                        ProtocolError("invalid_request", "request must be a JSON object").to_dict()
                    )
                    continue
                op = req.get("op")
                if op == "cancel":
                    target = req.get("target_id", self._current_id)
                    if target is not None and target == self._current_id:
                        self._cancel.set()
                    self.emit(
                        envelope(
                            "cancel",
                            request_id=req.get("id"),
                            event="ack",
                            target_id=target,
                        )
                    )
                    continue
                incoming.put(req)
                if op == "shutdown":
                    return

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        while True:
            req = incoming.get()
            if req is None:
                break
            if self._handle(req):
                break
        return 0

    def _handle(self, req: dict[str, Any]) -> bool:
        """Return True to stop the server."""
        request_id = req.get("id")
        op = req.get("op")
        if op == "shutdown":
            self._cancel.set()
            self.emit(envelope("pong", request_id=request_id, event="shutdown"))
            return True
        if op not in _OPS:
            self.emit(
                ProtocolError(
                    "unknown_op",
                    f"Unknown op {op!r}",
                    details={"known": sorted(_OPS | _CONTROL_OPS)},
                ).to_dict(request_id)
            )
            return False
        try:
            self._dispatch(op, req, request_id)
        except Exception as exc:  # noqa: BLE001 - sidecar must never die on one job
            self.emit(map_exception(exc).to_dict(request_id))
        return False

    def _dispatch(self, op: str, req: dict[str, Any], request_id: str | None) -> None:
        if op == "ping":
            self.emit(
                envelope(
                    "pong",
                    request_id=request_id,
                    event="pong",
                    version=__version__,
                    protocol=PROTOCOL_SCHEMA_VERSION,
                )
            )
            return
        if op == "list_datasets":
            self.emit(
                envelope(
                    "datasets",
                    request_id=request_id,
                    event="result",
                    datasets=[spec.to_dict() for spec in list_datasets()],
                )
            )
            return
        if op == "score_file":
            self._score_file(req, request_id)
            return
        if op == "score_dataset":
            self._score_dataset(req, request_id)
            return

    def _score_file(self, req: dict[str, Any], request_id: str | None) -> None:
        path = req.get("path")
        if not path:
            raise ProtocolError("invalid_request", "score_file requires 'path'")
        path = Path(path)
        cfg = load_config(req.get("config"))
        unit = req.get("unit")
        rec_kwargs = {
            "subject_id": req.get("subject_id"),
            "session_id": req.get("session_id"),
            "clip_id": req.get("clip_id"),
            "expected_n_channels": req.get("expected_n_channels"),
            "event_ok": req.get("event_ok", True),
            "sync_error_ms": req.get("sync_error_ms"),
            "stimulus_duration_s": req.get("stimulus_duration_s"),
            "adc_to_uv": req.get("adc_to_uv"),
        }
        rec_kwargs = {k: v for k, v in rec_kwargs.items() if v is not None or k == "event_ok"}

        if path.is_dir():
            dataset = "hw" if (path / "session.json").exists() else "npy"
            payload = dict(req)
            payload["dataset"] = dataset
            payload.setdefault("root", str(path))
            self._score_dataset(payload, request_id)
            return

        suffix = path.suffix.lower()
        if suffix == ".npy":
            sfreq = req.get("sfreq")
            if sfreq is None:
                raise ProtocolError("missing_sfreq", "score_file on .npy requires 'sfreq'")
            rec = load_npy(path, float(sfreq), unit=unit or "uV", **rec_kwargs)
        elif suffix in {".bdf", ".edf"}:
            rec = load_edf_bdf(path, unit=unit or "V", **rec_kwargs)
        else:
            raise ProtocolError(
                "invalid_request",
                f"Unsupported file type {suffix!r}",
                details={"path": str(path)},
            )
        report = evaluate_recording(rec, cfg)
        body = report.to_dict()
        extras = dict(body.get("extras") or {})
        extras.setdefault("source_path", str(path))
        body["extras"] = extras
        self.emit(
            envelope("report", request_id=request_id, event="result", report=body)
        )
        self.emit(
            envelope(
                "report",
                request_id=request_id,
                event="done",
                cancelled=False,
            )
        )

    def _score_dataset(self, req: dict[str, Any], request_id: str | None) -> None:
        name = req.get("dataset")
        if not name:
            raise ProtocolError("invalid_request", "score_dataset requires 'dataset'")
        kwargs = _adapter_kwargs(name, req)
        adapter = open_dataset(name, **kwargs)
        cfg = load_config(req.get("config"))
        self._cancel.clear()
        self._current_id = request_id
        total = adapter.estimate_count()

        def on_progress(done, tot, rec, report) -> None:
            self.emit(
                envelope(
                    "batch",
                    request_id=request_id,
                    event="progress",
                    done=done,
                    total=tot if tot is not None else total,
                    clip_id=rec.clip_id,
                    letter_grade=report.letter_grade.value,
                    gqi=round(report.gqi, 2),
                )
            )

        try:
            rows, summary = score_adapter(
                adapter,
                cfg,
                on_progress=on_progress,
                cancel=self._cancel.is_set,
            )
        finally:
            self._current_id = None

        self.emit(
            batch_envelope(
                threshold_version=cfg.threshold_version,
                reports=rows,
                summary=summary,
                cancelled=bool(summary.get("cancelled")),
                request_id=request_id,
            )
        )


def _adapter_kwargs(name: str, req: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if name == "synthetic":
        if req.get("n_channels") is not None:
            kwargs["n_channels"] = int(req["n_channels"])
        if req.get("duration_s") is not None:
            kwargs["duration_s"] = float(req["duration_s"])
        if req.get("sfreq") is not None:
            kwargs["sfreq"] = float(req["sfreq"])
        return kwargs
    root = req.get("root") or req.get("path")
    if not root:
        raise ProtocolError("missing_root", f"dataset {name!r} requires 'root'")
    kwargs["root"] = root
    if name == "npy":
        sfreq = req.get("sfreq")
        if sfreq is None:
            raise ProtocolError("missing_sfreq", "dataset npy requires 'sfreq'")
        kwargs["sfreq"] = float(sfreq)
        if req.get("pattern"):
            kwargs["pattern"] = req["pattern"]
        kwargs["unit"] = req.get("unit") or "uV"
        if req.get("adc_to_uv") is not None:
            kwargs["adc_to_uv"] = req["adc_to_uv"]
    if name == "nod":
        if req.get("subjects") is not None:
            kwargs["subjects"] = req["subjects"]
        if req.get("seeds_per_subject") is not None:
            kwargs["seeds_per_subject"] = int(req["seeds_per_subject"])
        kwargs["channels_tsv"] = req.get("channels_tsv") or DEFAULT_NOD_CHANNELS_TSV
    if name == "things":
        if req.get("subjects") is not None:
            kwargs["subjects"] = req["subjects"]
        if req.get("seeds_per_subject") is not None:
            kwargs["seeds_per_subject"] = int(req["seeds_per_subject"])
    return kwargs


def run_stdio(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    server = StdioServer(stdin or sys.stdin, stdout or sys.stdout)
    return server.run()
