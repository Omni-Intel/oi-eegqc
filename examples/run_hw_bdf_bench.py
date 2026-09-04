#!/usr/bin/env python3
"""Score Huawei Neuracle / TD10 session folders through the ``hw`` adapter."""

from __future__ import annotations

import argparse
import json

from oi_eegqc.config import load_config
from oi_eegqc.datasets import DEFAULT_ROOTS, open_dataset, score_adapter
from oi_eegqc.io import write_bench_json


def _log(rec, report) -> None:
    clip = rec.meta.get("clip", "")
    print(
        f"{rec.session_id} {clip:>6s}: grade={report.letter_grade.value} "
        f"GQI={report.gqi:5.1f} ODQ={report.odq:5.1f} "
        f"clean={report.clean_ratio:.2f} {report.availability.value}"
    )
    for problem in rec.meta.get("integrity_problems") or []:
        print(f"   integrity: {problem}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=DEFAULT_ROOTS["hw"])
    parser.add_argument(
        "-o",
        "--output",
        default="/vePFS-0x0e/xkp/oi-eegqc/bench_runs/hw_smoke.json",
    )
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    adapter = open_dataset("hw", args.root)
    rows, summary = score_adapter(adapter, cfg, on_recording=_log)
    write_bench_json(
        args.output,
        threshold_version=cfg.threshold_version,
        reports=rows,
        summary=summary,
    )
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
