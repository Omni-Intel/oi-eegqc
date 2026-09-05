from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ..protocol import envelope
from ..types import QualityReport


def report_payload(report: QualityReport) -> dict[str, Any]:
    """Canonical report body. Dataset provenance lives in ``extras`` only."""
    return report.to_dict()


def _lookup(row: dict[str, Any], key: str) -> Any:
    extras = row.get("extras") or {}
    if extras.get(key) is not None:
        return extras[key]
    return row.get(key)


def summarize_reports(rows: Iterable[dict[str, Any]], group_key: str = "dataset") -> dict[str, Any]:
    rows = list(rows)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(_lookup(row, group_key) or "ungrouped"), []).append(row)

    def _group_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(items),
            "mean_gqi": float(np.mean([r["gqi"] for r in items])) if items else 0.0,
            "mean_odq": float(np.mean([r["odq"] for r in items])) if items else 0.0,
            "mean_clean_ratio": float(np.mean([r.get("clean_ratio", 0.0) for r in items]))
            if items
            else 0.0,
            "letter_counts": dict(Counter(r["letter_grade"] for r in items)),
            "availability_counts": dict(Counter(r["availability"] for r in items)),
            "hard_failed": sum(1 for r in items if r.get("hard_fail_reasons")),
        }

    out: dict[str, Any] = {"n_total": len(rows), "groups": {}}
    for key, items in grouped.items():
        out["groups"][key] = _group_stats(items)
    if rows:
        out["overall"] = _group_stats(rows)
    else:
        out["overall"] = _group_stats([])
    return out


def batch_envelope(
    *,
    threshold_version: str,
    reports: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    cancelled: bool = False,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = envelope(
        "batch",
        event="done",
        request_id=request_id,
        threshold_version=threshold_version,
        summary=summary if summary is not None else summarize_reports(reports),
        reports=reports,
        cancelled=cancelled,
    )
    if extra:
        payload.update(extra)
    return payload


def write_bench_json(
    path: str | Path,
    *,
    threshold_version: str,
    reports: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    cancelled: bool = False,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = batch_envelope(
        threshold_version=threshold_version,
        reports=reports,
        summary=summary,
        cancelled=cancelled,
        extra=extra,
    )
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
