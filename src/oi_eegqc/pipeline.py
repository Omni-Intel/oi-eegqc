from __future__ import annotations

from pathlib import Path
from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np

from .adapters import highpass_channels, pick_eeg_channels
from .config import BenchConfig, load_config
from .io.array import load_npy
from .qa.windows import assess_windows
from .scoring.grades import (
    collect_hard_fails,
    compute_dimension_scores,
    gqi_from_scores,
)
from .scoring.explain import build_operator
from .types import QualityReport, RecordingInput


@dataclass
class PreparedEEG:
    raw_uv: np.ndarray
    filtered_uv: np.ndarray
    names: list[str]
    dropped: list[str]

    def crop(self, first: int, last: int) -> PreparedEEG:
        return PreparedEEG(self.raw_uv[:, first:last], self.filtered_uv[:, first:last],
                           self.names, self.dropped)


def prepare_eeg(recording: RecordingInput, cfg: BenchConfig) -> PreparedEEG:
    data = np.asarray(recording.data, dtype=float)
    if data.ndim != 2:
        raise ValueError(f"data must be 2D (n_channels, n_times), got {data.shape}")
    aux = recording.aux_ch_names or list(cfg.default_aux_names)
    eeg, names, dropped = pick_eeg_channels(data, list(recording.ch_names), aux)
    raw_uv = eeg * recording.to_uv_scale()
    return PreparedEEG(raw_uv, highpass_channels(raw_uv, recording.sfreq, cfg.highpass_hz),
                       names, dropped)


def evaluate_recording(
    recording: RecordingInput,
    config: BenchConfig | None = None,
    *,
    prepared: PreparedEEG | None = None,
) -> QualityReport:
    """Run adaptive QA/QC on one continuous EEG clip."""
    cfg = config or load_config()
    signal = prepared if prepared is not None else prepare_eeg(recording, cfg)
    raw_eeg, eeg = signal.raw_uv, signal.filtered_uv
    names, dropped = signal.names, signal.dropped
    n_missing = int((~np.isfinite(raw_eeg).any(axis=1)).sum())

    duration_s = recording.resolved_duration_s()
    dur_prof = cfg.select_duration(duration_s)
    mon_prof = cfg.select_montage(eeg.shape[0])

    window_qa, channel_issues = assess_windows(
        eeg,
        names,
        recording.sfreq,
        dur_prof,
        mon_prof,
        cfg,
        raw_data_uv=raw_eeg,
    )

    hard_fails = collect_hard_fails(
        recording,
        window_qa,
        n_channels_used=eeg.shape[0],
        n_dropped=len(dropped),
        cfg=cfg,
        n_missing=n_missing,
    )

    scores, reasons = compute_dimension_scores(
        recording,
        window_qa,
        dur_prof,
        mon_prof,
        cfg,
        n_dropped=len(dropped),
        n_missing=n_missing,
    )
    gqi, penalties, effective_weights = gqi_from_scores(scores, cfg)

    if hard_fails:
        gqi = 0.0
        reasons = hard_fails + reasons

    report = QualityReport(
        # Intake no longer settles on letter / availability tracks.
        letter_grade=None,
        availability=None,
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
            "frequency_coverage": {
                "signal_band_complete": recording.sfreq / 2 - 1 >= cfg.signal_band_hz[1],
                "noise_band_complete": recording.sfreq / 2 - 1 >= cfg.noise_band_hz[1],
                "line_measurable": cfg.line_hz + cfg.line_halfwidth_hz < recording.sfreq / 2,
            },
            "stimulus_duration_s": recording.stimulus_duration_s,
            "sync_error_ms": recording.sync_error_ms,
            "decision_tracks": {"letter": False, "availability": False},
            # Which dimensions actually had inputs, and the weights after
            # redistributing the unassessed ones.
            "assessed_dimensions": sorted(n for n, s in scores.items() if s.assessed),
            "effective_weights": {k: round(v, 4) for k, v in effective_weights.items()},
            "dimension_quality": {
                k: round(v.quality, 4) for k, v in scores.items() if v.assessed
            },
            "channel_issues": channel_issues,
            "channel_names": names,
            "scoring_config": asdict(cfg),
            "usable_window_rule": {
                "window_s": dur_prof.window_s, "hop_s": dur_prof.hop_s,
                "max_bad_channel_fraction": mon_prof.max_bad_ch_frac_per_window,
                "max_bad_channels": int(np.floor(mon_prof.max_bad_ch_frac_per_window * len(names))),
                "usable_target": dur_prof.usable_target,
            },
            "clipping_method": "window-extrema-plateaus-v2",
            "filter_context": "continuous_recording" if prepared is not None else "input_recording",
        },
    )
    from .scoring_version import SCORING_ALGORITHM_VERSION
    report.extras["algorithm_version"] = SCORING_ALGORITHM_VERSION
    if recording.meta.get("channel_layout"):
        report.extras["channel_layout"] = recording.meta["channel_layout"]
    report.extras["operator"] = build_operator(report)
    return report


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
