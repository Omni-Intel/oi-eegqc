from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ..types import QualityReport


def report_payload(report: QualityReport) -> dict[str, Any]:
    return report.to_dict()


def summarize_reports(rows: Iterable[dict[str, Any]], group_key: str = "dataset") -> dict[str, Any]:
    rows = list(rows)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(group_key) or "ungrouped"), []).append(row)

    def _group_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(items),
            "mean_gqi": float(np.mean([r["gqi"] for r in items])),
            "mean_odq": float(np.mean([r["odq"] for r in items])),
            "mean_clean_ratio": float(np.mean([r.get("clean_ratio", 0.0) for r in items])),
            "letter_counts": dict(Counter(r["letter_grade"] for r in items)),
            "availability_counts": dict(Counter(r["availability"] for r in items)),
            "hard_failed": sum(1 for r in items if r.get("hard_fail_reasons")),
        }

    out: dict[str, Any] = {"n_total": len(rows), "groups": {}}
    for key, items in grouped.items():
        out["groups"][key] = _group_stats(items)
    if rows:
        out["overall"] = _group_stats(rows)
    return out


def write_bench_json(
    path: str | Path,
    *,
    threshold_version: str,
    reports: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "threshold_version": threshold_version,
        "summary": summary if summary is not None else summarize_reports(reports),
        "reports": reports,
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
