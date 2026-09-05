from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Optional

from ..config import BenchConfig, load_config
from ..io.reports import report_payload, summarize_reports
from ..pipeline import evaluate_recording
from ..types import QualityReport, RecordingInput
from .base import DatasetAdapter

ProgressFn = Callable[[int, Optional[int], RecordingInput, QualityReport], None]
CancelFn = Callable[[], bool]


def _merge_extras(report: QualityReport, rec: RecordingInput, adapter: DatasetAdapter) -> dict[str, Any]:
    payload = report_payload(report)
    extras = dict(payload.get("extras") or {})
    for key, value in rec.meta.items():
        extras.setdefault(key, value)
    extras.setdefault("dataset", adapter.spec.name)
    extras.setdefault("unit_is_nominal", adapter.spec.unit_is_nominal)
    payload["extras"] = extras
    return payload


def iter_scored(
    adapter: DatasetAdapter,
    config: BenchConfig | None = None,
    *,
    on_recording: Callable[[RecordingInput, QualityReport], None] | None = None,
    on_progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> Iterator[tuple[RecordingInput, QualityReport]]:
    cfg = config or load_config()
    total = adapter.estimate_count()
    done = 0
    for rec in adapter.iter_recordings():
        if cancel is not None and cancel():
            return
        report = evaluate_recording(rec, cfg)
        done += 1
        if on_recording is not None:
            on_recording(rec, report)
        if on_progress is not None:
            on_progress(done, total, rec, report)
        yield rec, report


def score_adapter(
    adapter: DatasetAdapter,
    config: BenchConfig | None = None,
    *,
    on_recording: Callable[[RecordingInput, QualityReport], None] | None = None,
    on_progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
    group_key: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Score every recording from an adapter and return JSON-ready rows.

    Dataset-specific fields stay in ``extras``. ``summary['cancelled']`` is set
    when ``cancel`` trips mid-batch; already-scored rows are kept.
    """
    cfg = config or load_config()
    rows: list[dict[str, Any]] = []
    cancelled = False
    if cancel is not None and cancel():
        cancelled = True
    else:

        def gated() -> bool:
            nonlocal cancelled
            if cancel is not None and cancel():
                cancelled = True
                return True
            return False

        for rec, report in iter_scored(
            adapter,
            cfg,
            on_recording=on_recording,
            on_progress=on_progress,
            cancel=gated,
        ):
            rows.append(_merge_extras(report, rec, adapter))

    key = group_key or ("device" if adapter.spec.name == "hw" else "dataset")
    summary = summarize_reports(rows, group_key=key)
    summary["cancelled"] = cancelled
    return rows, summary
