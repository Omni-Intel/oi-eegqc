from __future__ import annotations

import argparse
import json
import sys
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
from .io.reports import batch_envelope
from .pipeline import evaluate_recording
from .protocol import ProtocolError, envelope, map_exception, write_json, write_ndjson


def _machine(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False) or getattr(args, "ndjson", False))


def _emit_error(args: argparse.Namespace | None, exc: ProtocolError) -> None:
    if args is not None and _machine(args):
        write_json(exc.to_dict())
    else:
        print(exc.message, file=sys.stderr)


def _print_one(report, stream=None) -> None:
    out = stream or sys.stdout
    print(
        f"{report.clip_id or '-':24s} grade={report.letter_grade.value} "
        f"GQI={report.gqi:5.1f} ODQ={report.odq:5.1f} clean={report.clean_ratio:.2f} "
        f"{report.availability.value}",
        file=out,
    )
    for reason in report.reasons[:3]:
        print(f"    - {reason}", file=out)


def _write_json_file(path: str | Path, payload: dict) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_init_config(args: argparse.Namespace) -> int:
    dump_default_config(args.output)
    if _machine(args):
        write_json(envelope("config", event="result", path=str(Path(args.output).resolve())))
    else:
        print(f"Wrote default config: {args.output}")
    return 0


def cmd_datasets(args: argparse.Namespace) -> int:
    specs = [spec.to_dict() for spec in list_datasets()]
    if args.json or args.ndjson:
        write_json(envelope("datasets", event="result", datasets=specs))
        return 0
    print(f"{'name':<12} {'kind':<10} mne  nominal  description")
    for spec in list_datasets():
        mne = "yes" if spec.requires_mne else "no"
        nom = "yes" if spec.unit_is_nominal else "no"
        print(f"{spec.name:<12} {spec.kind:<10} {mne:<4} {nom:<8} {spec.description}")
    return 0


def _emit_single_report(args: argparse.Namespace, report, *, source_path: str | None = None) -> int:
    body = report.to_dict()
    if source_path:
        extras = dict(body.get("extras") or {})
        extras.setdefault("source_path", source_path)
        body["extras"] = extras
    wrapped = envelope("report", event="result", report=body)
    if args.output:
        _write_json_file(args.output, wrapped if _machine(args) else body)
    if args.ndjson:
        write_ndjson(wrapped)
        write_ndjson(envelope("report", event="done", cancelled=False))
    elif args.json:
        write_json(wrapped)
    elif args.output:
        print(f"Wrote {args.output}", file=sys.stderr if _machine(args) else sys.stdout)
    else:
        # Legacy: raw report body on stdout so existing scripts keep parsing.
        print(json.dumps(body, indent=2, ensure_ascii=False))
    return 0


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
    return _emit_single_report(args, report, source_path=str(Path(args.input).resolve()))


def _batch_progress(args: argparse.Namespace, *, adapter_name: str):
    """Human progress on stderr in machine mode; NDJSON progress on stdout."""

    def on_progress(done, total, rec, report) -> None:
        if args.ndjson:
            write_ndjson(
                envelope(
                    "batch",
                    event="progress",
                    done=done,
                    total=total,
                    clip_id=rec.clip_id,
                    letter_grade=report.letter_grade.value,
                    gqi=round(report.gqi, 2),
                )
            )
            return
        if args.quiet or args.json:
            return
        extra = rec.meta.get("clip") or rec.meta.get("plan") or ""
        stream = sys.stderr if _machine(args) else sys.stdout
        print(
            f"[{adapter_name}] {rec.subject_id or ''} {rec.session_id or ''} {extra} "
            f"grade={report.letter_grade.value} GQI={report.gqi:5.1f} "
            f"ODQ={report.odq:5.1f} clean={report.clean_ratio:.2f} "
            f"{report.availability.value}",
            file=stream,
        )
        if rec.meta.get("integrity_problems"):
            for problem in rec.meta["integrity_problems"]:
                print(f"   integrity: {problem}", file=stream)

    return on_progress


def _emit_batch(
    args: argparse.Namespace,
    *,
    threshold_version: str,
    rows: list,
    summary: dict,
    extra: dict | None = None,
    output: str | Path | None = None,
) -> int:
    payload = batch_envelope(
        threshold_version=threshold_version,
        reports=rows,
        summary=summary,
        cancelled=bool(summary.get("cancelled")),
        extra=extra,
    )
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.ndjson or args.json:
        write_json(payload)
        return 0 if not summary.get("cancelled") else 0
    overall = summary.get("overall") or {}
    if output:
        print(f"Evaluated {len(rows)} clips → {output} (mean GQI={overall.get('mean_gqi', float('nan')):.1f})")
    if not args.quiet:
        print("\n=== SUMMARY ===")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        if output:
            print(f"\nWrote {output}")
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
    rows, summary = score_adapter(
        adapter,
        cfg,
        on_progress=_batch_progress(args, adapter_name="npy"),
    )
    return _emit_batch(
        args,
        threshold_version=cfg.threshold_version,
        rows=rows,
        summary=summary,
        extra={"dataset": "npy"},
        output=args.output,
    )


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
    return _emit_single_report(args, report, source_path=str(Path(args.input).resolve()))


def cmd_demo(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    adapter = SyntheticAdapter(n_channels=args.channels, duration_s=args.duration)
    if args.json or args.ndjson:
        rows, summary = score_adapter(
            adapter,
            cfg,
            on_progress=_batch_progress(args, adapter_name="synthetic"),
        )
        return _emit_batch(
            args,
            threshold_version=cfg.threshold_version,
            rows=rows,
            summary=summary,
            extra={"dataset": "synthetic"},
        )
    _, _ = score_adapter(
        adapter,
        cfg,
        on_recording=lambda rec, report: _print_one(report),
    )
    return 0


def _resolve_root(args: argparse.Namespace, name: str) -> str:
    if args.root:
        return args.root
    default = DEFAULT_ROOTS.get(name)
    if not default:
        raise ProtocolError("missing_root", f"--root is required for dataset {name!r}")
    if _machine(args):
        raise ProtocolError(
            "missing_root",
            f"dataset {name!r} requires an explicit --root in --json/--ndjson mode",
            details={"hint": default},
        )
    print(f"[warning] using workstation default --root {default}", file=sys.stderr)
    return default


def cmd_bench(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    name = args.dataset
    kwargs: dict = {}
    if name != "synthetic":
        kwargs["root"] = _resolve_root(args, name)
    if name == "npy":
        if args.sfreq is None:
            raise ProtocolError("missing_sfreq", "dataset npy requires --sfreq")
        kwargs["sfreq"] = args.sfreq
        kwargs["pattern"] = args.glob
        kwargs["unit"] = args.unit
        kwargs["adc_to_uv"] = args.adc_to_uv
    if name == "nod":
        kwargs["subjects"] = args.subjects
        kwargs["seeds_per_subject"] = args.seeds_per_subject
        kwargs["channels_tsv"] = args.channels_tsv or DEFAULT_NOD_CHANNELS_TSV
    if name == "things":
        kwargs["subjects"] = args.subjects
        kwargs["seeds_per_subject"] = args.seeds_per_subject
        if not args.quiet:
            print(
                "[warning] THINGS-EEG2 is noise-normalised and unitless. "
                "Absolute amplitude gates do not apply; do not compare against "
                "microvolt datasets.",
                file=sys.stderr,
            )
    if name == "synthetic":
        kwargs["n_channels"] = args.channels
        kwargs["duration_s"] = args.duration

    adapter = open_dataset(name, **kwargs)
    rows, summary = score_adapter(
        adapter,
        cfg,
        on_progress=_batch_progress(args, adapter_name=adapter.spec.name),
    )
    out = Path(args.output) if args.output else (None if _machine(args) else Path(f"{name}_bench.json"))
    return _emit_batch(
        args,
        threshold_version=cfg.threshold_version,
        rows=rows,
        summary=summary,
        extra={"dataset": adapter.spec.name, "unit_is_nominal": adapter.spec.unit_is_nominal},
        output=out,
    )


def cmd_serve(_args: argparse.Namespace) -> int:
    from .serve import run_stdio

    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stdin.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    return run_stdio()


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


def _add_machine_flags(p: argparse.ArgumentParser, *, suppress_default: bool = False) -> None:
    extra = {"default": argparse.SUPPRESS} if suppress_default else {}
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--json",
        action="store_true",
        help="Stdout a single protocol envelope (human text on stderr)",
        **extra,
    )
    g.add_argument(
        "--ndjson",
        action="store_true",
        help="Stdout protocol events as NDJSON (progress + done)",
        **extra,
    )
    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress human progress",
        **extra,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oi-eegqc",
        description="OI-EEGQC — adaptive multi-duration / multi-montage EEG quality bench",
    )
    _add_machine_flags(p)
    p.set_defaults(json=False, ndjson=False, quiet=False, func=None)
    machine = argparse.ArgumentParser(add_help=False)
    _add_machine_flags(machine, suppress_default=True)
    sub = p.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-config", parents=[machine], help="Write default YAML config")
    p_init.add_argument("-o", "--output", default="oi_eegqc_config.yaml")
    p_init.set_defaults(func=cmd_init_config)

    p_list = sub.add_parser("datasets", parents=[machine], help="List registered dataset adapters")
    p_list.set_defaults(func=cmd_datasets)

    p_eval = sub.add_parser("eval-npy", parents=[machine], help="Evaluate one .npy recording")
    p_eval.add_argument("-i", "--input", required=True)
    p_eval.add_argument("--sfreq", type=float, required=True)
    p_eval.add_argument("--ch-names", default=None, help=".npy or text list of channel names")
    p_eval.add_argument("--channels-first", action="store_true")
    p_eval.add_argument("--times-first", action="store_true")
    _add_eval_common(p_eval)
    p_eval.set_defaults(func=cmd_eval_npy)

    p_dir = sub.add_parser(
        "eval-dir",
        parents=[machine],
        help="Evaluate a directory of .npy clips",
    )
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

    p_bdf = sub.add_parser(
        "eval-bdf",
        parents=[machine],
        help="Evaluate one EDF/BDF file (requires mne extra)",
    )
    p_bdf.add_argument("-i", "--input", required=True)
    _add_eval_common(p_bdf, default_unit="V")
    p_bdf.set_defaults(func=cmd_eval_bdf)

    p_demo = sub.add_parser(
        "demo",
        parents=[machine],
        help="Run synthetic clean vs noisy smoke demo",
    )
    p_demo.add_argument("--channels", type=int, default=32)
    p_demo.add_argument("--duration", type=float, default=12.0)
    p_demo.add_argument("--config", default=None)
    p_demo.set_defaults(func=cmd_demo)

    p_bench = sub.add_parser("bench", parents=[machine], help="Score a registered dataset adapter")
    p_bench.add_argument(
        "dataset",
        choices=sorted(["npy", "hw", "nod", "things", "synthetic"]),
        help="Registered adapter name (see `oi-eegqc datasets`)",
    )
    p_bench.add_argument("--root", default=None, help="Dataset root (human CLI has workstation defaults)")
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

    p_serve = sub.add_parser(
        "serve",
        parents=[machine],
        help="NDJSON sidecar on stdin/stdout for Electron",
    )
    p_serve.add_argument(
        "--stdio",
        action="store_true",
        default=True,
        help="Read requests from stdin, write events to stdout (default)",
    )
    p_serve.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        raise
    try:
        status = args.func(args)
    except ProtocolError as exc:
        _emit_error(args, exc)
        raise SystemExit(exc.status) from exc
    except Exception as exc:
        if _machine(args):
            mapped = map_exception(exc)
            _emit_error(args, mapped)
            raise SystemExit(mapped.status) from exc
        raise
    raise SystemExit(status)


if __name__ == "__main__":
    main()
