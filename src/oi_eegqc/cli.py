from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .config import dump_default_config, load_config
from .pipeline import evaluate_batch, evaluate_recording
from .types import RecordingInput


def _load_ch_names(path: Path | None, n_channels: int) -> list[str]:
    if path is None:
        return [f"ch{i:03d}" for i in range(n_channels)]
    if path.suffix == ".npy":
        names = np.load(path, allow_pickle=True).astype(str).tolist()
        return [str(x) for x in names]
    text = path.read_text(encoding="utf-8").strip().splitlines()
    return [line.strip() for line in text if line.strip()]


def cmd_init_config(args: argparse.Namespace) -> int:
    dump_default_config(args.output)
    print(f"Wrote default config: {args.output}")
    return 0


def cmd_eval_npy(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    path = Path(args.input)
    arr = np.load(path)
    if arr.ndim != 2:
        raise SystemExit(f"Expected 2D npy, got {arr.shape}")
    # Prefer (n_channels, n_times)
    if args.channels_first:
        data = arr
    elif args.times_first:
        data = arr.T
    else:
        data = arr if arr.shape[0] <= arr.shape[1] else arr.T

    ch_names = _load_ch_names(Path(args.ch_names) if args.ch_names else None, data.shape[0])
    rec = RecordingInput(
        data=data,
        sfreq=args.sfreq,
        ch_names=ch_names,
        subject_id=args.subject,
        session_id=args.session,
        clip_id=args.clip or path.stem,
        stimulus_duration_s=args.stimulus_duration,
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
    root = Path(args.input)
    files = sorted(root.glob(args.glob))
    if not files:
        raise SystemExit(f"No files matched {root}/{args.glob}")

    reports = []
    for path in files:
        arr = np.load(path)
        if arr.ndim != 2:
            continue
        data = arr if arr.shape[0] <= arr.shape[1] else arr.T
        ch_names = _load_ch_names(Path(args.ch_names) if args.ch_names else None, data.shape[0])
        rec = RecordingInput(
            data=data,
            sfreq=args.sfreq,
            ch_names=ch_names,
            clip_id=path.stem,
            subject_id=args.subject,
            session_id=args.session,
        )
        reports.append(evaluate_recording(rec, cfg))

    payload = {
        "n_recordings": len(reports),
        "threshold_version": cfg.threshold_version,
        "summary": {
            "mean_gqi": float(np.mean([r.gqi for r in reports])) if reports else None,
            "mean_odq": float(np.mean([r.odq for r in reports])) if reports else None,
            "letter_counts": {
                g: sum(1 for r in reports if r.letter_grade.value == g)
                for g in ["A", "B", "C", "D"]
            },
        },
        "reports": [r.to_dict() for r in reports],
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"Evaluated {len(reports)} clips → {out} "
        f"(mean GQI={payload['summary']['mean_gqi']:.1f})"
    )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Synthetic clean vs noisy demo for smoke testing."""
    rng = np.random.default_rng(0)
    sfreq = 250.0
    n_ch = args.channels
    duration = args.duration
    n_times = int(sfreq * duration)
    t = np.arange(n_times) / sfreq
    shared = 0.8e-5 * np.sin(2 * np.pi * 10.0 * t)
    clean = np.stack(
        [shared + 0.05e-5 * np.sin(2 * np.pi * (0.3 * i + 1.0) * t) for i in range(n_ch)],
        axis=0,
    )
    clean += 0.03e-5 * rng.standard_normal(clean.shape)

    noisy = clean.copy()
    noisy[0] = 50e-5 * rng.standard_normal(n_times)  # broken channel
    noisy += 2e-5 * rng.standard_normal(noisy.shape)
    burst = int(0.5 * sfreq)
    noisy[:, n_times // 3 : n_times // 3 + burst] += 10e-5 * rng.standard_normal((n_ch, burst))

    cfg = load_config(args.config)
    names = [f"EEG{i:02d}" for i in range(n_ch)]
    reports = evaluate_batch(
        [
            RecordingInput(clean, sfreq, names, clip_id="synthetic_clean", subject_id="demo"),
            RecordingInput(noisy, sfreq, names, clip_id="synthetic_noisy", subject_id="demo"),
        ],
        cfg,
    )
    for r in reports:
        print(
            f"{r.clip_id}: grade={r.letter_grade.value} "
            f"availability={r.availability.value} "
            f"GQI={r.gqi:.1f} ODQ={r.odq:.1f} "
            f"profile={r.duration_profile}/{r.montage_profile}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oi-eegqc",
        description="OI-EEGQC — adaptive multi-duration / multi-montage EEG quality bench",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-config", help="Write default YAML config")
    p_init.add_argument("-o", "--output", default="oi_eegqc_config.yaml")
    p_init.set_defaults(func=cmd_init_config)

    p_eval = sub.add_parser("eval-npy", help="Evaluate one .npy recording")
    p_eval.add_argument("-i", "--input", required=True)
    p_eval.add_argument("--sfreq", type=float, required=True)
    p_eval.add_argument("--ch-names", default=None, help=".npy or text list of channel names")
    p_eval.add_argument("--config", default=None)
    p_eval.add_argument("-o", "--output", default=None)
    p_eval.add_argument("--subject", default=None)
    p_eval.add_argument("--session", default=None)
    p_eval.add_argument("--clip", default=None)
    p_eval.add_argument("--stimulus-duration", type=float, default=None)
    p_eval.add_argument("--sync-error-ms", type=float, default=None)
    p_eval.add_argument("--events-bad", action="store_true")
    p_eval.add_argument("--channels-first", action="store_true")
    p_eval.add_argument("--times-first", action="store_true")
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
    p_dir.set_defaults(func=cmd_eval_dir)

    p_demo = sub.add_parser("demo", help="Run synthetic clean vs noisy smoke demo")
    p_demo.add_argument("--channels", type=int, default=32)
    p_demo.add_argument("--duration", type=float, default=12.0)
    p_demo.add_argument("--config", default=None)
    p_demo.set_defaults(func=cmd_demo)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
