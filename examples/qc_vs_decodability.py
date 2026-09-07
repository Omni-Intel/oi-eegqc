#!/usr/bin/env python3
"""Does intake QC grade predict stimulus-specific EEG (split-half 1-NN)?

session_01 is a *visual video watching* task: unique clips, no category labels,
one presentation each. Video-identity classification with a train/test split
is therefore impossible. The test that remains:

  Take the first 5 s of each video EEG, split into two 2.5 s halves, extract
  log-bandpower, and ask whether half-A identifies the matching half-B among
  all videos (cosine 1-NN). Correlate that self-match with GQI / letter.

A muscle-band (55–95 Hz) twin run checks whether D-grade clips are
"identifiable" only because their artifacts are unique.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.signal import welch
from scipy.stats import spearmanr

from oi_eegqc.datasets import open_dataset
from oi_eegqc.pipeline import evaluate_recording

NEURAL = ((1.0, 4.0), (4.0, 8.0), (8.0, 13.0), (13.0, 30.0), (30.0, 45.0))
MUSCLE = ((55.0, 95.0),)
LETTER_N = {"A": 4, "B": 3, "C": 2, "D": 1}


def bandpower(data: np.ndarray, sfreq: float, bands: tuple[tuple[float, float], ...]) -> np.ndarray:
    nperseg = int(min(sfreq, data.shape[-1]))
    freqs, psd = welch(data, fs=sfreq, nperseg=nperseg, axis=-1)
    feats = []
    for lo, hi in bands:
        mask = (freqs >= lo) & (freqs < hi)
        if not mask.any():
            feats.append(np.zeros(data.shape[0]))
            continue
        feats.append(np.log10(np.maximum(psd[:, mask].mean(axis=-1), 1e-20)))
    return np.concatenate(feats, axis=0)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / np.clip(np.linalg.norm(a, axis=1, keepdims=True), 1e-12, None)
    b = b / np.clip(np.linalg.norm(b, axis=1, keepdims=True), 1e-12, None)
    return a @ b.T


def identify(a: np.ndarray, b: np.ndarray) -> dict:
    sim = cosine_sim(a, b)
    n = sim.shape[0]
    pred = sim.argmax(axis=1)
    hits = pred == np.arange(n)
    self_r = np.diag(sim)
    order = np.argsort(-sim, axis=1)
    ranks = np.array([int(np.where(order[i] == i)[0][0]) + 1 for i in range(n)])
    return {
        "hit_rate": float(hits.mean()),
        "chance": 1.0 / n,
        "self_r": self_r,
        "hits": hits,
        "ranks": ranks,
        "median_rank": float(np.median(ranks)),
        "n": n,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default="/vePFS-0x0e/xkp/oi-eegqc/bench_runs/session_01_extract/session_01",
    )
    parser.add_argument(
        "--qc-json",
        default="/vePFS-0x0e/xkp/oi-eegqc/bench_runs/session_01_qc.json",
    )
    parser.add_argument(
        "--plot",
        default="/vePFS-0x0e/xkp/oi-eegqc/assets/qc-vs-decodability.png",
    )
    parser.add_argument(
        "--output",
        default="/vePFS-0x0e/xkp/oi-eegqc/bench_runs/qc_vs_decodability.json",
    )
    parser.add_argument("--clip-s", type=float, default=5.0)
    parser.add_argument("--session-stamp", default="20260905_144322")
    args = parser.parse_args()

    qc_path = Path(args.qc_json)
    qc_by_id: dict[str, dict] = {}
    if qc_path.exists():
        payload = json.loads(qc_path.read_text(encoding="utf-8"))
        for row in payload.get("reports") or []:
            qc_by_id[row["clip_id"]] = row

    adapter = open_dataset("avsession", args.root)
    half = int(round(0.5 * args.clip_s * 1000))  # placeholder; use rec.sfreq below
    neural_a, neural_b, muscle_a, muscle_b = [], [], [], []
    gqis, odqs, letters, clip_ids, durs = [], [], [], [], []

    for rec in adapter.iter_recordings():
        if rec.meta.get("kind") != "video":
            continue
        if args.session_stamp and rec.session_id != args.session_stamp:
            continue
        n_need = int(round(args.clip_s * rec.sfreq))
        if rec.data.shape[-1] < n_need:
            continue
        half = n_need // 2
        left = np.asarray(rec.data[:, :half], dtype=np.float64)
        right = np.asarray(rec.data[:, half : 2 * half], dtype=np.float64)
        neural_a.append(bandpower(left, rec.sfreq, NEURAL))
        neural_b.append(bandpower(right, rec.sfreq, NEURAL))
        muscle_a.append(bandpower(left, rec.sfreq, MUSCLE))
        muscle_b.append(bandpower(right, rec.sfreq, MUSCLE))
        row = qc_by_id.get(rec.clip_id)
        if row is None:
            report = evaluate_recording(rec)
            row = report.to_dict()
        gqis.append(float(row["gqi"]))
        odqs.append(float(row["odq"]))
        letters.append(str(row["letter_grade"]))
        clip_ids.append(rec.clip_id)
        durs.append(float(rec.resolved_duration_s()))

    neural_a = np.stack(neural_a)
    neural_b = np.stack(neural_b)
    muscle_a = np.stack(muscle_a)
    muscle_b = np.stack(muscle_b)
    gqis = np.asarray(gqis)
    letters = np.asarray(letters)
    letter_n = np.array([LETTER_N[x] for x in letters])

    neural = identify(neural_a, neural_b)
    muscle = identify(muscle_a, muscle_b)

    rho_gqi, p_gqi = spearmanr(gqis, neural["self_r"])
    rho_letter, p_letter = spearmanr(letter_n, neural["self_r"])
    rho_hit, p_hit = spearmanr(gqis, neural["hits"].astype(float))
    rho_m, p_m = spearmanr(gqis, muscle["self_r"])

    by_letter = {}
    for grade in ("A", "B", "C", "D"):
        idx = np.where(letters == grade)[0]
        if idx.size < 2:
            continue
        sub = identify(neural_a[idx], neural_b[idx])
        mus = identify(muscle_a[idx], muscle_b[idx])
        by_letter[grade] = {
            "n": int(idx.size),
            "mean_gqi": float(gqis[idx].mean()),
            "neural_hit": sub["hit_rate"],
            "neural_chance": sub["chance"],
            "neural_hit_over_chance": sub["hit_rate"] / sub["chance"],
            "neural_self_r": float(sub["self_r"].mean()),
            "muscle_hit": mus["hit_rate"],
            "muscle_hit_over_chance": mus["hit_rate"] / mus["chance"],
        }

    rng = np.random.default_rng(0)
    shuffle_hits = []
    perm = np.arange(neural_a.shape[0])
    for _ in range(200):
        rng.shuffle(perm)
        shuffle_hits.append(identify(neural_a, neural_b[perm])["hit_rate"])

    summary = {
        "task": "visual video watching (unique clips, no category, one shot)",
        "n_videos": int(neural["n"]),
        "clip_s": args.clip_s,
        "neural_bands_hz": [list(b) for b in NEURAL],
        "neural": {
            "hit_rate": neural["hit_rate"],
            "chance": neural["chance"],
            "hit_over_chance": neural["hit_rate"] / neural["chance"],
            "median_rank": neural["median_rank"],
            "mean_self_r": float(neural["self_r"].mean()),
        },
        "muscle": {
            "hit_rate": muscle["hit_rate"],
            "chance": muscle["chance"],
            "hit_over_chance": muscle["hit_rate"] / muscle["chance"],
            "mean_self_r": float(muscle["self_r"].mean()),
        },
        "spearman": {
            "gqi_vs_neural_self_r": {"rho": float(rho_gqi), "p": float(p_gqi)},
            "letter_vs_neural_self_r": {"rho": float(rho_letter), "p": float(p_letter)},
            "gqi_vs_neural_hit": {"rho": float(rho_hit), "p": float(p_hit)},
            "gqi_vs_muscle_self_r": {"rho": float(rho_m), "p": float(p_m)},
        },
        "label_shuffle_hit_mean": float(np.mean(shuffle_hits)),
        "label_shuffle_hit_95": float(np.quantile(shuffle_hits, 0.95)),
        "by_letter": by_letter,
        "letter_counts": dict(Counter(letters.tolist())),
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    _plot(
        Path(args.plot),
        gqis,
        neural["self_r"],
        letters,
        by_letter,
        rho_gqi,
        p_gqi,
        neural["hit_rate"],
        neural["chance"],
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.output}")
    print(f"Wrote {args.plot}")


def _plot(path, gqis, self_r, letters, by_letter, rho, p, hit, chance):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"A": "#2a9d4a", "B": "#4a4a4a", "C": "#c47b16", "D": "#c0392b"}
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.2), dpi=140)
    ax = axes[0]
    for grade, color in colors.items():
        m = letters == grade
        if not m.any():
            continue
        ax.scatter(gqis[m], self_r[m], s=18, alpha=0.75, c=color, label=f"{grade} (n={int(m.sum())})", edgecolors="none")
    ax.set_xlabel("Intake GQI")
    ax.set_ylabel("Split-half cosine (neural 1–45 Hz)")
    ax.set_title(f"QC vs self-match  Spearman ρ={rho:.2f}, p={p:.3g}")
    ax.legend(frameon=False, fontsize=8)
    ax.set_xlim(0, 100)

    ax = axes[1]
    grades = [g for g in ("A", "B", "C", "D") if g in by_letter]
    x = np.arange(len(grades))
    hoc = [by_letter[g]["neural_hit_over_chance"] for g in grades]
    m_hoc = [by_letter[g]["muscle_hit_over_chance"] for g in grades]
    ax.bar(x - 0.18, hoc, 0.36, color=[colors[g] for g in grades], label="Neural 1–45 Hz")
    ax.bar(x + 0.18, m_hoc, 0.36, color="#bbbbbb", label="Muscle 55–95 Hz")
    ax.axhline(1.0, color="#888888", lw=1, ls="--", label="chance")
    ax.set_xticks(x, grades)
    ax.set_ylabel("1-NN hit / chance (within grade)")
    ax.set_title(f"Global 1-NN hit {100*hit:.1f}%  (chance {100*chance:.2f}%)")
    ax.legend(frameon=False, fontsize=8)
    fig.suptitle("session_01 · video watching · first 5 s split-half", fontsize=11)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
