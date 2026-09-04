from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from ..config import BenchConfig, load_config
from ..io.reports import report_payload, summarize_reports
from ..pipeline import evaluate_recording
from ..types import QualityReport, RecordingInput
from .base import DatasetAdapter


def iter_scored(
    adapter: DatasetAdapter,
    config: BenchConfig | None = None,
    *,
    on_recording: Callable[[RecordingInput, QualityReport], None] | None = None,
) -> Iterator[tuple[RecordingInput, QualityReport]]:
    cfg = config or load_config()
    for rec in adapter.iter_recordings():
        report = evaluate_recording(rec, cfg)
        if on_recording is not None:
            on_recording(rec, report)
        yield rec, report


def score_adapter(
    adapter: DatasetAdapter,
    config: BenchConfig | None = None,
    *,
    on_recording: Callable[[RecordingInput, QualityReport], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Score every recording from an adapter and return JSON-ready rows."""
    cfg = config or load_config()
    rows: list[dict[str, Any]] = []
    for rec, report in iter_scored(adapter, cfg, on_recording=on_recording):
        payload = report_payload(report)
        payload.setdefault("dataset", rec.meta.get("dataset", adapter.spec.name))
        payload.setdefault("unit_is_nominal", adapter.spec.unit_is_nominal)
        if rec.meta.get("device"):
            payload.setdefault("device", rec.meta["device"])
        if rec.meta.get("plan"):
            payload.setdefault("plan", rec.meta["plan"])
        if rec.meta.get("clip"):
            payload.setdefault("clip", rec.meta["clip"])
        if rec.meta.get("integrity_problems"):
            payload.setdefault("integrity_problems", rec.meta["integrity_problems"])
        if rec.meta.get("paradigm") is not None:
            payload.setdefault("paradigm", rec.meta["paradigm"])
        if rec.meta.get("status") is not None:
            payload.setdefault("session_status", rec.meta["status"])
        rows.append(payload)
    group_key = "device" if adapter.spec.name == "hw" else "dataset"
    return rows, summarize_reports(rows, group_key=group_key)
