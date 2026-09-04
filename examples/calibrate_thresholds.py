#!/usr/bin/env python3
"""Noise-injection calibration for oi-eegqc thresholds.

Thresholds must not be tuned by lowering them until a convenient dataset
passes: that is circular, and it silently destroys sensitivity to the failure
modes the bench exists to catch. This script instead injects known,
monotonically increasing degradations into synthetic recordings that have
ground-truth severity, then checks three properties per failure mode:

``monotonic``   the score never rises as severity rises (Spearman rho <= -0.9)
``range``       the score actually spans a usable interval instead of sitting
                pinned at 100 and then collapsing to 0 in one step
``detection``   the mildest severity that is meant to fail actually fails

Run this after every threshold change and before publishing a threshold
version. WeBrain reports its QA indices decreasing almost linearly with noise
level; that is the behaviour being verified here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from oi_eegqc import RecordingInput, evaluate_recording
from oi_eegqc.config import load_config
from oi_eegqc.datasets import synth_clean

SFREQ = 250.0
DURATION_S = 20.0
N_CHANNELS = 32
N_SEEDS = 5


# --- Degradation models -------------------------------------------------------
# Each takes (clean_data, severity, rng) and returns degraded microvolt data.
# Severity 0 must always be a no-op so the clean reference is shared.


def deg_broadband_noise(data: np.ndarray, sev: float, rng: np.random.Generator) -> np.ndarray:
    """Sensor / EMI noise added to every channel, in microvolts."""
    return data + sev * rng.standard_normal(data.shape)


def deg_dead_channels(data: np.ndarray, sev: float, rng: np.random.Generator) -> np.ndarray:
    """Fraction of the montage detached (flat)."""
    out = data.copy()
    n_dead = int(round(sev * data.shape[0]))
    if n_dead:
        out[:n_dead] = 0.0
    return out


def deg_detached_channels(data: np.ndarray, sev: float, rng: np.random.Generator) -> np.ndarray:
    """Fraction of electrodes replaced by independent noise at normal amplitude.

    This is the failure mode only the spatial-coupling detector can catch: the
    amplitude is ordinary, so the flat and saturation gates stay silent, but the
    channel no longer shares any signal with the rest of the montage.
    """
    out = data.copy()
    n_bad = int(round(sev * data.shape[0]))
    if n_bad:
        amplitude = float(np.median(np.std(data, axis=1)))
        out[:n_bad] = amplitude * rng.standard_normal((n_bad, data.shape[1]))
    return out


def deg_saturation(data: np.ndarray, sev: float, rng: np.random.Generator) -> np.ndarray:
    """Amplifier gain driving the signal into a +/-400 uV rail."""
    if sev <= 1.0:
        return data.copy()
    return np.clip(data * sev, -400.0, 400.0)


def deg_movement_bursts(data: np.ndarray, sev: float, rng: np.random.Generator) -> np.ndarray:
    """Fraction of recording time lost to whole-head high-amplitude bursts."""
    out = data.copy()
    n_times = data.shape[1]
    n_bad = int(round(sev * n_times))
    if n_bad:
        start = (n_times - n_bad) // 2
        out[:, start : start + n_bad] += 250.0 * rng.standard_normal((data.shape[0], n_bad))
    return out


def deg_line_noise(data: np.ndarray, sev: float, rng: np.random.Generator) -> np.ndarray:
    """50 Hz mains interference at increasing amplitude in microvolts."""
    n_times = data.shape[1]
    t = np.arange(n_times) / SFREQ
    return data + sev * np.sin(2 * np.pi * 50.0 * t)[None, :]


def deg_drift(data: np.ndarray, sev: float, rng: np.random.Generator) -> np.ndarray:
    """Slow sweat / electrode drift, which a 1 Hz high-pass should absorb."""
    n_times = data.shape[1]
    t = np.arange(n_times) / SFREQ
    return data + sev * np.sin(2 * np.pi * 0.05 * t)[None, :]


#: ``kind`` selects which properties are required of the score:
#:
#: ``graded``      severity varies continuously in the real world, so the score
#:                 must decrease monotonically and span several distinct levels
#: ``binary``      the underlying fault is all-or-nothing hardware behaviour
#:                 (an amplifier either rails or it does not), so only clean
#:                 pass plus hard rejection is required, not a smooth ramp
#: ``robustness``  the degradation should be absorbed by preprocessing and must
#:                 not move the score at all
#:
#: ``fail_from`` is the first severity that must no longer grade A.
SCENARIOS = [
    {
        "name": "broadband_noise_uv",
        "fn": deg_broadband_noise,
        "levels": [0.0, 2.0, 4.0, 6.0, 8.0, 12.0, 20.0, 40.0],
        "fail_from": 8.0,
        "kind": "graded",
    },
    {
        "name": "dead_channel_fraction",
        "fn": deg_dead_channels,
        "levels": [0.0, 0.05, 0.10, 0.20, 0.30, 0.50, 0.75],
        "fail_from": 0.10,
        "kind": "graded",
    },
    {
        "name": "detached_channel_fraction",
        "fn": deg_detached_channels,
        "levels": [0.0, 0.05, 0.10, 0.20, 0.30, 0.50],
        "fail_from": 0.10,
        "kind": "graded",
    },
    {
        "name": "saturation_gain",
        "fn": deg_saturation,
        "levels": [1.0, 2.0, 5.0, 10.0, 20.0, 40.0],
        "fail_from": 10.0,
        "kind": "binary",
    },
    {
        "name": "movement_burst_time_fraction",
        "fn": deg_movement_bursts,
        "levels": [0.0, 0.02, 0.05, 0.10, 0.20, 0.40, 0.60],
        "fail_from": 0.10,
        "kind": "graded",
    },
    {
        "name": "line_noise_uv",
        "fn": deg_line_noise,
        "levels": [0.0, 2.0, 5.0, 7.0, 10.0, 20.0, 50.0],
        # Mains below roughly 5 uV is routinely notched out downstream and does
        # not make a recording unusable, so it is allowed to keep grade A.
        "fail_from": 10.0,
        "kind": "graded",
    },
    {
        "name": "drift_uv",
        "fn": deg_drift,
        # A 1 Hz high-pass must absorb 0.05 Hz drift, so this should stay clean
        # across the whole grid. It guards against the filter regressing.
        "levels": [0.0, 20.0, 50.0, 100.0, 200.0],
        "fail_from": None,
        "kind": "robustness",
    },
]


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rho without a scipy.stats dependency."""

    def rank(v: np.ndarray) -> np.ndarray:
        order = np.argsort(v, kind="mergesort")
        ranks = np.empty(len(v), dtype=float)
        ranks[order] = np.arange(len(v), dtype=float)
        # average ties
        _, inv, counts = np.unique(v, return_inverse=True, return_counts=True)
        for group in np.where(counts > 1)[0]:
            mask = inv == group
            ranks[mask] = ranks[mask].mean()
        return ranks

    rx, ry = rank(x), rank(y)
    if rx.std() == 0 or ry.std() == 0:
        return 0.0
    return float(np.corrcoef(rx, ry)[0, 1])


def run_scenario(scenario: dict, cfg, n_seeds: int) -> dict:
    rows = []
    for level in scenario["levels"]:
        gqis, odqs, letters = [], [], []
        for seed in range(n_seeds):
            clean = synth_clean(N_CHANNELS, SFREQ, DURATION_S, seed)
            rng = np.random.default_rng(1000 + seed)
            data = scenario["fn"](clean, level, rng)
            rec = RecordingInput(
                data=data,
                sfreq=SFREQ,
                ch_names=[f"EEG{i:02d}" for i in range(N_CHANNELS)],
                unit="uV",
                clip_id=f"{scenario['name']}_{level}_{seed}",
                expected_n_channels=N_CHANNELS,
            )
            report = evaluate_recording(rec, cfg)
            gqis.append(report.gqi)
            odqs.append(report.odq)
            letters.append(report.letter_grade.value)
        rows.append(
            {
                "severity": level,
                "gqi_mean": float(np.mean(gqis)),
                "gqi_std": float(np.std(gqis)),
                "odq_mean": float(np.mean(odqs)),
                "letters": "".join(sorted(letters)),
                "worst_letter": max(letters),
            }
        )

    sev = np.array([r["severity"] for r in rows], dtype=float)
    gqi = np.array([r["gqi_mean"] for r in rows], dtype=float)
    kind = scenario["kind"]

    checks: dict[str, object] = {}
    if kind == "graded":
        rho = _spearman(sev, gqi)
        checks["spearman_rho"] = round(rho, 3)
        checks["monotonic"] = bool(rho <= -0.9)
        # Count distinct plateaus: a step function has few, a graded score many.
        distinct = len({round(g / 5.0) for g in gqi})
        checks["distinct_gqi_levels"] = distinct
        checks["graded_response"] = bool(distinct >= max(3, len(gqi) // 2))
    elif kind == "robustness":
        checks["gqi_min"] = round(float(gqi.min()), 1)
        checks["stays_clean"] = bool(gqi.min() >= 85.0)

    fail_from = scenario["fail_from"]
    if fail_from is not None:
        failing = [r for r in rows if r["severity"] >= fail_from]
        checks["detects_from_fail_threshold"] = bool(
            failing and all(r["worst_letter"] != "A" for r in failing)
        )
        checks["clean_reference_is_A"] = bool(rows[0]["worst_letter"] == "A")
        if kind == "binary":
            # An all-or-nothing fault must be rejected outright, not merely
            # downgraded, once it is present.
            checks["binary_fault_rejected"] = bool(
                all(r["worst_letter"] == "D" for r in failing)
            )

    return {"name": scenario["name"], "rows": rows, "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--seeds", type=int, default=N_SEEDS)
    parser.add_argument(
        "-o",
        "--output",
        default="/vePFS-0x0e/xkp/oi-eegqc/bench_runs/calibration.json",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    results = [run_scenario(s, cfg, args.seeds) for s in SCENARIOS]

    all_ok = True
    for res in results:
        print(f"\n=== {res['name']}")
        for row in res["rows"]:
            bar = "#" * int(round(row["gqi_mean"] / 4.0))
            print(
                f"   sev={row['severity']:<7g} GQI={row['gqi_mean']:6.1f}"
                f" +/-{row['gqi_std']:4.1f}  ODQ={row['odq_mean']:6.1f}"
                f"  letters={row['letters']:<6s} {bar}"
            )
        for key, val in res["checks"].items():
            if isinstance(val, bool):
                mark = "PASS" if val else "FAIL"
                all_ok = all_ok and val
                print(f"   [{mark}] {key}")
            else:
                print(f"          {key} = {val}")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "threshold_version": cfg.threshold_version,
                "all_checks_passed": all_ok,
                "scenarios": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nall_checks_passed={all_ok}")
    print(f"Wrote {out}")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
