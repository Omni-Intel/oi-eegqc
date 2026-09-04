from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import dump_default_config, load_config
from .datasets import (
    DEFAULT_NOD_CHANNELS_TSV,
    DEFAULT_ROOTS,
    SyntheticAdapter,
    list_datasets,
    open_dataset,
    score_adapter,
)
from .io import load_edf_bdf, load_npy, write_bench_json
from .pipeline import evaluate_recording
from .types import RecordingInput


def cmd_init_config(args: argparse.Namespace) -> int:
    dump_default_config(args.output)
    print(f"Wrote default config: {args.output}")
    return 0


def cmd_datasets(_args: argparse.Namespace) -> int:
    print(f"{'name':<12} {'kind':<10} mne  nominal  description")
    for spec in list_datasets():
        mne = "yes" if spec.requires_mne else "no"
        nom = "yes" if spec.unit_is_nominal else "no"
        print(f"{spec.name:<12} {spec.kind:<10} {mne:<4} {nom:<8} {spec.description}")
    return 0


def _print_one(report) -> None:
    print(
        f"{report.clip_id or '-':24s} grade={report.letter_grade.value} "
        f"GQI={report.gqi:5.1f} ODQ={report.odq:5.1f} clean={report.clean_ratio:.2f} "
        f"{report.availability.value}"
    )
    for reason in report.reasons[:3]:
        print(f"    - {reason}")


def cmd_eval_npy(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    rec = load_npy(
        args.input,
        args.sfreq,
        ch_names_path=args.ch_names,
        channels_first=True if args.channels_first else False if args.times_first else None,
        unit=args.unit,
        adc_to_uv=args.adc_to_uv,
        subject_id=args.subject,
        session_id=args.session,
        clip_id=args.clip,
        stimulus_duration_s=args.stimulus_duration,
        expected_n_channels=args.expected_channels,
        event_ok=not args.events_bad,
        sync_error_ms=args.sync_error_ms,
    )
    report = evaluate_recording(rec, cfg)
    payload = report.to_dict()
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(json.dumps(payload, indent=2))
    return 0


def cmd_eval_dir(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    adapter = open_dataset(
        "npy",
        args.input,
        args.sfreq,
        pattern=args.glob,
        ch_names_path=args.ch_names,
        unit=args.unit,
        adc_to_uv=args.adc_to_uv,
        expected_n_channels=args.expected_channels,
        subject_id=args.subject,
        session_id=args.session,
    )

    def _log(rec: RecordingInput, report) -> None:
        print(
            f"{rec.clip_id}: grade={report.letter_grade.value} "
            f"GQI={report.gqi:.1f} ODQ={report.odq:.1f}"
        )

    rows, summary = score_adapter(adapter, cfg, on_recording=_log)
    write_bench_json(
        args.output,
        threshold_version=cfg.threshold_version,
        reports=rows,
        summary=summary,
    )
    overall = summary.get("overall") or {}
    print(
        f"Evaluated {len(rows)} clips → {args.output} "
        f"(mean GQI={overall.get('mean_gqi', float('nan')):.1f})"
    )
    return 0


def cmd_eval_bdf(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    rec = load_edf_bdf(
        args.input,
        unit=args.unit,
        adc_to_uv=args.adc_to_uv,
        subject_id=args.subject,
        session_id=args.session,
        clip_id=args.clip,
        expected_n_channels=args.expected_channels,
        event_ok=not args.events_bad,
        sync_error_ms=args.sync_error_ms,
        stimulus_duration_s=args.stimulus_duration,
    )
    report = evaluate_recording(rec, cfg)
    payload = report.to_dict()
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(json.dumps(payload, indent=2))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    adapter = SyntheticAdapter(
        n_channels=args.channels,
        duration_s=args.duration,
    )
    _, _ = score_adapter(adapter, cfg, on_recording=lambda rec, report: _print_one(report))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    name = args.dataset
    kwargs: dict = {}
    if name != "synthetic":
        root = args.root or DEFAULT_ROOTS.get(name)
        if not root:
            raise SystemExit(f"--root is required for dataset {name!r}")
        kwargs["root"] = root
    if name == "npy":
        if args.sfreq is None:
            raise SystemExit("dataset npy requires --sfreq")
        kwargs["sfreq"] = args.sfreq
        kwargs["pattern"] = args.glob
        kwargs["unit"] = args.unit
        kwargs["adc_to_uv"] = args.adc_to_uv
    if name == "nod":
        kwargs["subjects"] = args.subjects
        kwargs["seeds_per_subject"] = args.seeds_per_subject
        if args.channels_tsv:
            kwargs["channels_tsv"] = args.channels_tsv
        else:
            kwargs["channels_tsv"] = DEFAULT_NOD_CHANNELS_TSV
    if name == "things":
        kwargs["subjects"] = args.subjects
        kwargs["seeds_per_subject"] = args.seeds_per_subject
        print(
            "[warning] THINGS-EEG2 is noise-normalised and unitless. "
            "Absolute amplitude gates do not apply; do not compare against "
            "microvolt datasets.\n"
        )
    if name == "synthetic":
        kwargs["n_channels"] = args.channels
        kwargs["duration_s"] = args.duration

    adapter = open_dataset(name, **kwargs)

    def _log(rec: RecordingInput, report) -> None:
        extra = rec.meta.get("clip") or rec.meta.get("plan") or ""
        print(
            f"[{adapter.spec.name}] {rec.subject_id or ''} {rec.session_id or ''} {extra} "
            f"grade={report.letter_grade.value} GQI={report.gqi:5.1f} "
            f"ODQ={report.odq:5.1f} clean={report.clean_ratio:.2f} "
            f"{report.availability.value}"
        )
        if rec.meta.get("integrity_problems"):
            for problem in rec.meta["integrity_problems"]:
                print(f"   integrity: {problem}")

    rows, summary = score_adapter(adapter, cfg, on_recording=_log)
    out = Path(args.output) if args.output else Path(f"{name}_bench.json")
    write_bench_json(
        out,
        threshold_version=cfg.threshold_version,
        reports=rows,
        summary=summary,
        extra={"dataset": adapter.spec.name, "unit_is_nominal": adapter.spec.unit_is_nominal},
    )
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {out}")
    return 0


def _add_eval_common(p: argparse.ArgumentParser, *, default_unit: str = "uV") -> None:
    p.add_argument("--config", default=None)
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--subject", default=None)
    p.add_argument("--session", default=None)
    p.add_argument("--clip", default=None)
    p.add_argument("--stimulus-duration", type=float, default=None)
    p.add_argument("--sync-error-ms", type=float, default=None)
    p.add_argument("--unit", default=default_unit)
    p.add_argument("--adc-to-uv", type=float, default=None)
    p.add_argument("--expected-channels", type=int, default=None)
    p.add_argument("--events-bad", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oi-eegqc",
        description="OI-EEGQC — adaptive multi-duration / multi-montage EEG quality bench",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-config", help="Write default YAML config")
    p_init.add_argument("-o", "--output", default="oi_eegqc_config.yaml")
    p_init.set_defaults(func=cmd_init_config)

    p_list = sub.add_parser("datasets", help="List registered dataset adapters")
    p_list.set_defaults(func=cmd_datasets)

    p_eval = sub.add_parser("eval-npy", help="Evaluate one .npy recording")
    p_eval.add_argument("-i", "--input", required=True)
    p_eval.add_argument("--sfreq", type=float, required=True)
    p_eval.add_argument("--ch-names", default=None, help=".npy or text list of channel names")
    p_eval.add_argument("--channels-first", action="store_true")
    p_eval.add_argument("--times-first", action="store_true")
    _add_eval_common(p_eval)
    p_eval.set_defaults(func=cmd_eval_npy)

    p_dir = sub.add_parser("eval-dir", help="Evaluate a directory of .npy clips")
    p_dir.add_argument("-i", "--input", required=True)
    p_dir.add_argument("--glob", default="*.npy")
    p_dir.add_argument("--sfreq", type=float, required=True)
    p_dir.add_argument("--ch-names", default=None)
    p_dir.add_argument("--config", default=None)
    p_dir.add_argument("-o", "--output", required=True)
    p_dir.add_argument("--subject", default=None)
    p_dir.add_argument("--session", default=None)
    p_dir.add_argument("--unit", default="uV")
    p_dir.add_argument("--adc-to-uv", type=float, default=None)
    p_dir.add_argument("--expected-channels", type=int, default=None)
    p_dir.set_defaults(func=cmd_eval_dir)

    p_bdf = sub.add_parser("eval-bdf", help="Evaluate one EDF/BDF file (requires mne extra)")
    p_bdf.add_argument("-i", "--input", required=True)
    _add_eval_common(p_bdf, default_unit="V")
    p_bdf.set_defaults(func=cmd_eval_bdf)

    p_demo = sub.add_parser("demo", help="Run synthetic clean vs noisy smoke demo")
    p_demo.add_argument("--channels", type=int, default=32)
    p_demo.add_argument("--duration", type=float, default=12.0)
    p_demo.add_argument("--config", default=None)
    p_demo.set_defaults(func=cmd_demo)

    p_bench = sub.add_parser("bench", help="Score a registered dataset adapter")
    p_bench.add_argument(
        "dataset",
        choices=sorted(["npy", "hw", "nod", "things", "synthetic"]),
        help="Registered adapter name (see `oi-eegqc datasets`)",
    )
    p_bench.add_argument("--root", default=None, help="Dataset root (defaults exist for hw/nod/things)")
    p_bench.add_argument("-o", "--output", default=None)
    p_bench.add_argument("--config", default=None)
    p_bench.add_argument("--subjects", nargs="+", default=None)
    p_bench.add_argument("--seeds-per-subject", type=int, default=2)
    p_bench.add_argument("--channels-tsv", default=None)
    p_bench.add_argument("--sfreq", type=float, default=None)
    p_bench.add_argument("--glob", default="*.npy")
    p_bench.add_argument("--unit", default="uV")
    p_bench.add_argument("--adc-to-uv", type=float, default=None)
    p_bench.add_argument("--channels", type=int, default=32)
    p_bench.add_argument("--duration", type=float, default=20.0)
    p_bench.set_defaults(func=cmd_bench)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
