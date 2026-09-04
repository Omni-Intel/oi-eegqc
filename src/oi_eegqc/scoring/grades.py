from __future__ import annotations

from dataclasses import dataclass

from ..config import BenchConfig, DurationProfile, LetterCutoffs, MontageProfile
from ..types import (
    AvailabilityFlag,
    LetterGrade,
    PenaltyBreakdown,
    RecordingInput,
    WindowQASummary,
)

_ORDER = [LetterGrade.D, LetterGrade.C, LetterGrade.B, LetterGrade.A]

DIMENSIONS = ("contact", "cleanliness", "usable_time", "integrity", "stimulus_sync")


@dataclass
class DimensionScore:
    """One GQI dimension.

    ``assessed`` is False when the inputs for the dimension were never
    supplied. Unassessed dimensions are excluded from GQI and their weight is
    redistributed, so a caller who passes no sync or integrity metadata cannot
    collect those points for free.
    """

    quality: float
    assessed: bool


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _ramp(value: float, good: float, bad: float) -> float:
    """Continuous 1 -> 0 ramp: 1 at or below ``good``, 0 at or above ``bad``."""
    if bad <= good:
        return 1.0
    return _clip01((bad - value) / (bad - good))


def letter_from_odq(
    odq: float,
    cutoffs: LetterCutoffs,
    duration_profile: DurationProfile | None = None,
) -> LetterGrade:
    """Letter grade from the WeBrain-style ODQ (usable-window percentage).

    ODQ is the share of windows surviving the bad-channel budget, so these
    cutoffs act on recording time, matching the quantity WeBrain's A/B/C/D
    boundaries were defined against. Duration profiles tighten them for short
    clips, where one lost window removes a large slice of the clip.
    """
    a = duration_profile.odq_for_a if duration_profile else cutoffs.a
    b = duration_profile.odq_for_b if duration_profile else cutoffs.b
    c = duration_profile.odq_for_c if duration_profile else cutoffs.c
    if odq >= a:
        return LetterGrade.A
    if odq >= b:
        return LetterGrade.B
    if odq >= c:
        return LetterGrade.C
    return LetterGrade.D


def apply_bad_channel_ceiling(
    grade: LetterGrade,
    bad_channel_pct: float,
    montage_profile: MontageProfile,
) -> LetterGrade:
    """Cap the letter grade when too much of the montage is unusable.

    Independent of ODQ: a recording can keep every window inside the
    bad-channel budget while a fixed subset of electrodes is dead throughout,
    which ODQ alone would never notice.
    """
    if bad_channel_pct >= montage_profile.bad_ch_hard_pct:
        max_allowed = LetterGrade.D
    elif bad_channel_pct >= montage_profile.bad_ch_soft_pct:
        max_allowed = LetterGrade.C
    elif bad_channel_pct >= montage_profile.bad_ch_caution_pct:
        max_allowed = LetterGrade.B
    else:
        max_allowed = LetterGrade.A
    return _ORDER[min(_ORDER.index(grade), _ORDER.index(max_allowed))]


def collect_hard_fails(
    rec: RecordingInput,
    window_qa: WindowQASummary,
    n_channels_used: int,
    n_dropped: int,
    cfg: BenchConfig,
) -> list[str]:
    """Non-negotiable rejection gates.

    Deliberately narrow. Every gate here makes GQI discontinuous, so only
    conditions with no meaningful gradation qualify: the markers are unusable,
    the amplifier is railed, or the montage is largely absent. Ordinary
    noise and bad channels degrade the score continuously instead.
    """
    fails: list[str] = []
    if not rec.event_ok:
        fails.append("event marker integrity failed")
    if n_channels_used and (
        len(window_qa.clipped_channels) >= cfg.hard_fail_clipped_frac * n_channels_used
    ):
        fails.append(
            f"{len(window_qa.clipped_channels)}/{n_channels_used} channels rail-clipped"
        )
    expected = rec.expected_n_channels
    if expected:
        present = n_channels_used + n_dropped
        if present < cfg.hard_fail_present_channel_frac * expected:
            fails.append(f"only {present} of {expected} expected channels present")
    return fails


def compute_dimension_scores(
    rec: RecordingInput,
    window_qa: WindowQASummary,
    duration_profile: DurationProfile,
    montage_profile: MontageProfile,
    cfg: BenchConfig,
    n_dropped: int = 0,
) -> tuple[dict[str, DimensionScore], list[str]]:
    reasons: list[str] = []
    scores: dict[str, DimensionScore] = {}

    # --- Contact: impedance, unusable channels, rail clipping ---------------
    contact = 1.0
    if rec.impedance_kohm:
        vals = sorted(rec.impedance_kohm.values())
        median_z = float(vals[len(vals) // 2])
        if median_z > cfg.impedance_ok_kohm:
            contact -= 0.6
            reasons.append(f"median impedance {median_z:.1f} kΩ > {cfg.impedance_ok_kohm}")
        elif median_z > cfg.impedance_good_kohm:
            contact -= 0.25
            reasons.append(
                f"median impedance {median_z:.1f} kΩ above ideal {cfg.impedance_good_kohm}"
            )
    if window_qa.bad_channel_pct >= montage_profile.bad_ch_hard_pct:
        contact -= 1.0
        reasons.append(
            f"bad channels {window_qa.bad_channel_pct:.1f}% >= hard "
            f"{montage_profile.bad_ch_hard_pct}%"
        )
    elif window_qa.bad_channel_pct >= montage_profile.bad_ch_soft_pct:
        contact -= 0.35
        reasons.append(
            f"bad channels {window_qa.bad_channel_pct:.1f}% >= soft "
            f"{montage_profile.bad_ch_soft_pct}%"
        )
    elif window_qa.bad_channel_pct >= montage_profile.bad_ch_caution_pct:
        contact -= 0.15
        reasons.append(
            f"bad channels {window_qa.bad_channel_pct:.1f}% >= caution "
            f"{montage_profile.bad_ch_caution_pct}%"
        )
    if window_qa.dead_channels:
        dead_frac = len(window_qa.dead_channels) / max(window_qa.n_channels, 1)
        contact -= min(1.0, 2.0 * dead_frac)
        reasons.append(
            f"{len(window_qa.dead_channels)} flat/dead channels ({dead_frac:.0%}): "
            f"{window_qa.dead_channels[:6]}"
        )
    if window_qa.clipped_channels:
        clip_frac = len(window_qa.clipped_channels) / max(window_qa.n_channels, 1)
        contact -= min(1.0, 2.0 * clip_frac)
        reasons.append(
            f"{len(window_qa.clipped_channels)} rail-clipped channels ({clip_frac:.0%}): "
            f"{window_qa.clipped_channels[:6]}"
        )
    scores["contact"] = DimensionScore(_clip01(contact), True)

    # --- Cleanliness: flag density blended with continuous spectral quality -
    # Thresholded flags alone make the score a step function, because every
    # cell crosses the NSR threshold at once under uniform noise.
    nsr_q = _ramp(
        window_qa.nsr_median,
        0.5 * montage_profile.nsr_threshold,
        2.0 * montage_profile.nsr_threshold,
    )
    line_q = _ramp(
        window_qa.line_noise_ratio,
        0.5 * montage_profile.line_ratio_threshold,
        2.0 * montage_profile.line_ratio_threshold,
    )
    spectral_q = min(nsr_q, line_q)
    blend = _clip01(cfg.spectral_blend)
    cleanliness = (1.0 - blend) * window_qa.clean_ratio + blend * spectral_q
    if nsr_q < 0.5:
        reasons.append(
            f"median HF noise-to-signal {window_qa.nsr_median:.2f} approaching "
            f"threshold {montage_profile.nsr_threshold}"
        )
    if line_q < 0.5:
        reasons.append(
            f"median mains ratio {window_qa.line_noise_ratio:.2f} approaching "
            f"threshold {montage_profile.line_ratio_threshold}"
        )
    if window_qa.muscle_band_ratio > 0.45:
        cleanliness -= 0.10
        reasons.append(f"elevated muscle-band ratio {window_qa.muscle_band_ratio:.2f}")
    scores["cleanliness"] = DimensionScore(_clip01(cleanliness), True)

    # --- Usable time: surviving windows against the duration-adaptive target
    usable = window_qa.usable_window_ratio
    scores["usable_time"] = DimensionScore(
        _clip01(usable / max(duration_profile.usable_target, 1e-6)), True
    )
    if usable < duration_profile.odq_for_c / 100.0:
        reasons.append(
            f"usable window ratio {usable:.2f} below {duration_profile.name} "
            f"floor {duration_profile.odq_for_c / 100.0:.2f}"
        )

    # --- Integrity: markers, duration match, channel count -----------------
    integrity = 1.0
    integrity_assessed = False
    if not rec.event_ok:
        integrity -= 1.0
        integrity_assessed = True
        reasons.append("event markers failed integrity check")
    stim = rec.stimulus_duration_s
    if stim is not None and stim > 0:
        integrity_assessed = True
        rel = abs(rec.resolved_duration_s() - stim) / stim
        if rel > 0.15:
            integrity -= 0.5
            reasons.append(f"recording vs stimulus duration mismatch {rel:.1%}")
        elif rel > 0.05:
            integrity -= 0.2
            reasons.append(f"recording vs stimulus duration drift {rel:.1%}")
    expected = rec.expected_n_channels
    if expected:
        integrity_assessed = True
        present = window_qa.n_channels + n_dropped
        if present < expected:
            integrity -= min(1.0, 2.0 * (expected - present) / expected)
            reasons.append(f"{expected - present} of {expected} channels missing")
    scores["integrity"] = DimensionScore(_clip01(integrity), integrity_assessed)

    # --- Stimulus sync: audio/video alignment only, never task performance -
    sync = 1.0
    sync_assessed = rec.sync_error_ms is not None
    if sync_assessed:
        if rec.sync_error_ms >= cfg.sync_fail_ms:
            sync -= 1.0
            reasons.append(f"sync error {rec.sync_error_ms:.0f} ms >= fail {cfg.sync_fail_ms}")
        elif rec.sync_error_ms >= cfg.sync_warn_ms:
            sync -= 0.35
            reasons.append(f"sync error {rec.sync_error_ms:.0f} ms >= warn {cfg.sync_warn_ms}")
    scores["stimulus_sync"] = DimensionScore(_clip01(sync), sync_assessed)

    return scores, reasons


def gqi_from_scores(
    scores: dict[str, DimensionScore],
    cfg: BenchConfig,
) -> tuple[float, PenaltyBreakdown, dict[str, float]]:
    """GQI as a weighted average over assessed dimensions only.

    Weights are renormalised across the assessed dimensions, so GQI spans the
    full 0-100 range: a recording failing every measured dimension scores 0
    rather than bottoming out at the sum of the untested weights.
    """
    w = cfg.gqi_weights
    base = {name: float(getattr(w, name)) for name in DIMENSIONS}
    total = sum(base[n] for n in DIMENSIONS if scores[n].assessed)
    effective = {
        n: (base[n] / total if scores[n].assessed and total > 0 else 0.0) for n in DIMENSIONS
    }

    pen = PenaltyBreakdown()
    quality = 0.0
    for name in DIMENSIONS:
        penalty = 100.0 * effective[name] * (1.0 - scores[name].quality)
        setattr(pen, name, penalty)
        quality += effective[name] * scores[name].quality
    gqi = float(max(0.0, min(100.0, 100.0 * quality)))
    return gqi, pen, effective


def availability_from_report(
    letter: LetterGrade,
    gqi: float,
    hard_failed: bool,
) -> AvailabilityFlag:
    """HBN-inspired availability flag, kept consistent with the letter grade.

    The letter grade is the contractual decision; this flag is the research-use
    hint derived from it. D never maps to Available and a hard fail is always
    Unavailable, so the two tracks cannot contradict each other.
    """
    if hard_failed or letter == LetterGrade.D:
        return AvailabilityFlag.UNAVAILABLE
    if letter == LetterGrade.C or gqi < 70.0:
        return AvailabilityFlag.CAUTION
    return AvailabilityFlag.AVAILABLE
