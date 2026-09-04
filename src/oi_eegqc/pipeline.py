from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np

from .adapters import highpass_channels, pick_eeg_channels
from .config import BenchConfig, load_config
from .qa.windows import assess_windows
from .scoring.grades import (
    apply_usable_floor,
    availability_from_report,
    compute_penalties,
    compute_usable_ratio,
    gqi_from_penalties,
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
    eeg, names = pick_eeg_channels(data, list(recording.ch_names), aux)
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
    )
    usable = compute_usable_ratio(window_qa)
    letter = letter_from_odq(window_qa.odq, cfg.letter)
    letter = apply_usable_floor(letter, usable, dur_prof)

    penalties, reasons = compute_penalties(
        recording,
        window_qa,
        usable,
        dur_prof,
        mon_prof,
        cfg,
    )
    gqi = gqi_from_penalties(penalties)
    availability = availability_from_report(
        letter,
        gqi,
        recording.event_ok,
        usable,
        dur_prof,
    )

    # Hard demote if integrity critically fails
    if not recording.event_ok and letter != LetterGrade.D:
        letter = LetterGrade.D
        reasons.append("forced letter D due to failed event integrity")

    return QualityReport(
        letter_grade=letter,
        availability=availability,
        gqi=gqi,
        odq=window_qa.odq,
        usable_ratio=usable,
        duration_profile=dur_prof.name,
        montage_profile=mon_prof.name,
        n_channels_used=eeg.shape[0],
        duration_s=duration_s,
        window_qa=window_qa,
        penalties=penalties,
        threshold_version=cfg.threshold_version,
        reasons=reasons,
        subject_id=recording.subject_id,
        session_id=recording.session_id,
        clip_id=recording.clip_id,
        extras={
            "bad_channels": window_qa.bad_channels,
            "stimulus_duration_s": recording.stimulus_duration_s,
            "sync_error_ms": recording.sync_error_ms,
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
    arr = np.load(path)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D array in {path}, got {arr.shape}")
    # Heuristic: more time samples than channels is typical.
    if arr.shape[0] > arr.shape[1] and (ch_names is None or arr.shape[1] == len(ch_names)):
        # (n_times, n_channels) -> transpose
        if ch_names is not None and arr.shape[1] == len(ch_names):
            arr = arr.T
        elif ch_names is None and arr.shape[0] > arr.shape[1]:
            # ambiguous; prefer (channels, times) if first dim looks like channel count
            if arr.shape[1] > 1000 and arr.shape[0] <= 512:
                pass
            else:
                arr = arr.T
    if ch_names is None:
        ch_names = [f"ch{i:03d}" for i in range(arr.shape[0])]
    if len(ch_names) != arr.shape[0]:
        raise ValueError("ch_names length mismatch with array channels")
    return RecordingInput(data=arr, sfreq=sfreq, ch_names=list(ch_names), **meta)
