#!/usr/bin/env python3
"""Score NOD-EEG (and optionally THINGS-EEG2) through the dataset adapters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from oi_eegqc.config import load_config
from oi_eegqc.datasets import DEFAULT_NOD_CHANNELS_TSV, DEFAULT_ROOTS, open_dataset, score_adapter
from oi_eegqc.io import summarize_reports, write_bench_json


def _log(rec, report) -> None:
    print(
        f"[{rec.meta.get('dataset')}] {rec.subject_id} {rec.meta.get('plan')}: "
        f"grade={report.letter_grade.value} GQI={report.gqi:5.1f} "
        f"ODQ={report.odq:5.1f} clean={report.clean_ratio:.2f} "
        f"badch={report.window_qa.bad_channel_pct:4.1f}%"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        default="/vePFS-0x0e/xkp/oi-eegqc/bench_runs/things_nod_smoke.json",
    )
    parser.add_argument("--nod-root", default=DEFAULT_ROOTS["nod"])
    parser.add_argument("--things-root", default=DEFAULT_ROOTS["things"])
    parser.add_argument("--include-things", action="store_true")
    parser.add_argument("--seeds-per-subject", type=int, default=2)
    parser.add_argument("--nod-subjects", nargs="+", default=["sub-01", "sub-02", "sub-03"])
    parser.add_argument("--things-subjects", nargs="+", default=["sub-01", "sub-02", "sub-03"])
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    all_rows: list[dict] = []

    nod = open_dataset(
        "nod",
        args.nod_root,
        subjects=args.nod_subjects,
        seeds_per_subject=args.seeds_per_subject,
        channels_tsv=DEFAULT_NOD_CHANNELS_TSV,
    )
    rows, _ = score_adapter(nod, cfg, on_recording=_log)
    all_rows.extend(rows)

    if args.include_things:
        print(
            "\n[warning] THINGS-EEG2 is noise-normalised and unitless. "
            "Do not compare against microvolt datasets.\n"
        )
        things = open_dataset(
            "things",
            args.things_root,
            subjects=args.things_subjects,
            seeds_per_subject=args.seeds_per_subject,
        )
        rows, _ = score_adapter(things, cfg, on_recording=_log)
        all_rows.extend(rows)

    summary = summarize_reports(all_rows, group_key="dataset")
    out = Path(args.output)
    write_bench_json(out, threshold_version=cfg.threshold_version, reports=all_rows, summary=summary)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
