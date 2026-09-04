from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np

from .adapters import detect_clipped_channels, highpass_channels, pick_eeg_channels
from .config import BenchConfig, load_config
from .io.array import load_npy
from .qa.windows import assess_windows
from .scoring.grades import (
    apply_bad_channel_ceiling,
    availability_from_report,
    collect_hard_fails,
    compute_dimension_scores,
    gqi_from_scores,
    letter_from_odq,
)
from .types import LetterGrade, QualityReport, RecordingInput


def evaluate_recording(
    recording: RecordingInput,
    config: BenchConfig | None = None,
) -> QualityReport:
    """Run adaptive QA/QC on one continuous EEG clip."""
    cfg = config or load_config()
    data = np.asarray(recording.data, dtype=float)
    if data.ndim != 2:
        raise ValueError(f"data must be 2D (n_channels, n_times), got {data.shape}")

    aux = recording.aux_ch_names or list(cfg.default_aux_names)
    eeg, names, dropped = pick_eeg_channels(data, list(recording.ch_names), aux)

    # Everything downstream assumes microvolts: the flat and saturation gates
    # compare against physical thresholds, so the unit cannot be left implicit.
    eeg = eeg * recording.to_uv_scale()

    # Clipping is detected before filtering, which would smear the rail.
    clipped_idx = detect_clipped_channels(eeg, cfg.clip_frac_threshold)

    eeg = highpass_channels(eeg, recording.sfreq, cfg.highpass_hz)

    duration_s = recording.resolved_duration_s()
    dur_prof = cfg.select_duration(duration_s)
    mon_prof = cfg.select_montage(eeg.shape[0])

    window_qa = assess_windows(
        eeg,
        names,
        recording.sfreq,
        dur_prof,
        mon_prof,
        cfg,
        clipped_idx=clipped_idx,
    )

    hard_fails = collect_hard_fails(
        recording,
        window_qa,
        n_channels_used=eeg.shape[0],
        n_dropped=len(dropped),
        cfg=cfg,
    )

    scores, reasons = compute_dimension_scores(
        recording,
        window_qa,
        dur_prof,
        mon_prof,
        cfg,
        n_dropped=len(dropped),
    )
    gqi, penalties, effective_weights = gqi_from_scores(scores, cfg)

    if hard_fails:
        letter = LetterGrade.D
        gqi = 0.0
        reasons = hard_fails + reasons
    else:
        letter = letter_from_odq(window_qa.odq, cfg.letter, dur_prof)
        letter = apply_bad_channel_ceiling(letter, window_qa.bad_channel_pct, mon_prof)

    availability = availability_from_report(letter, gqi, bool(hard_fails))

    return QualityReport(
        letter_grade=letter,
        availability=availability,
        gqi=gqi,
        odq=window_qa.odq,
        usable_ratio=window_qa.usable_window_ratio,
        clean_ratio=window_qa.clean_ratio,
        duration_profile=dur_prof.name,
        montage_profile=mon_prof.name,
        n_channels_used=eeg.shape[0],
        duration_s=duration_s,
        window_qa=window_qa,
        penalties=penalties,
        threshold_version=cfg.threshold_version,
        hard_fail_reasons=hard_fails,
        reasons=reasons,
        subject_id=recording.subject_id,
        session_id=recording.session_id,
        clip_id=recording.clip_id,
        extras={
            "bad_channels": window_qa.bad_channels,
            "dead_channels": window_qa.dead_channels,
            "clipped_channels": window_qa.clipped_channels,
            "dropped_channels": dropped,
            "input_unit": recording.unit,
            "stimulus_duration_s": recording.stimulus_duration_s,
            "sync_error_ms": recording.sync_error_ms,
            # Which dimensions actually had inputs, and the weights after
            # redistributing the unassessed ones.
            "assessed_dimensions": sorted(n for n, s in scores.items() if s.assessed),
            "effective_weights": {k: round(v, 4) for k, v in effective_weights.items()},
            "dimension_quality": {
                k: round(v.quality, 4) for k, v in scores.items() if v.assessed
            },
        },
    )


def evaluate_batch(
    recordings: Iterable[RecordingInput],
    config: BenchConfig | None = None,
) -> list[QualityReport]:
    cfg = config or load_config()
    return [evaluate_recording(rec, cfg) for rec in recordings]


def load_npy_recording(
    path: str | Path,
    sfreq: float,
    ch_names: list[str] | None = None,
    **meta,
) -> RecordingInput:
    """Load a (n_channels, n_times) or (n_times, n_channels) npy array."""
    return load_npy(path, sfreq, ch_names=ch_names, **meta)
