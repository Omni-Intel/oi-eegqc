from __future__ import annotations

from ..config import BenchConfig, DurationProfile, LetterCutoffs, MontageProfile
from ..types import (
    AvailabilityFlag,
    LetterGrade,
    PenaltyBreakdown,
    RecordingInput,
    WindowQASummary,
)


def letter_from_odq(odq: float, cutoffs: LetterCutoffs) -> LetterGrade:
    if odq >= cutoffs.a:
        return LetterGrade.A
    if odq >= cutoffs.b:
        return LetterGrade.B
    if odq >= cutoffs.c:
        return LetterGrade.C
    return LetterGrade.D


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def compute_usable_ratio(window_qa: WindowQASummary) -> float:
    """Usable time proxy: 1 - bad channel-window fraction."""
    return _clip01(1.0 - window_qa.bad_window_ratio)


def apply_usable_floor(
    grade: LetterGrade,
    usable: float,
    duration_profile: DurationProfile,
) -> LetterGrade:
    """Duration-adaptive usable-time floors can demote letter grade."""
    order = [LetterGrade.D, LetterGrade.C, LetterGrade.B, LetterGrade.A]
    max_allowed = LetterGrade.D
    if usable >= duration_profile.usable_for_a:
        max_allowed = LetterGrade.A
    elif usable >= duration_profile.usable_for_b:
        max_allowed = LetterGrade.B
    elif usable >= duration_profile.usable_for_c:
        max_allowed = LetterGrade.C
    return order[min(order.index(grade), order.index(max_allowed))]


def compute_penalties(
    rec: RecordingInput,
    window_qa: WindowQASummary,
    usable: float,
    duration_profile: DurationProfile,
    montage_profile: MontageProfile,
    cfg: BenchConfig,
) -> tuple[PenaltyBreakdown, list[str]]:
    reasons: list[str] = []
    w = cfg.gqi_weights
    pen = PenaltyBreakdown()

    # Contact: impedance + bad channels
    contact_score = 1.0
    if rec.impedance_kohm:
        vals = list(rec.impedance_kohm.values())
        median_z = float(sorted(vals)[len(vals) // 2])
        if median_z > cfg.impedance_ok_kohm:
            contact_score -= 0.6
            reasons.append(f"median impedance {median_z:.1f} kΩ > {cfg.impedance_ok_kohm}")
        elif median_z > cfg.impedance_good_kohm:
            contact_score -= 0.25
            reasons.append(f"median impedance {median_z:.1f} kΩ above ideal {cfg.impedance_good_kohm}")
    if window_qa.bad_channel_pct >= montage_profile.bad_ch_hard_pct:
        contact_score -= 0.7
        reasons.append(
            f"bad channels {window_qa.bad_channel_pct:.1f}% >= hard {montage_profile.bad_ch_hard_pct}%"
        )
    elif window_qa.bad_channel_pct >= montage_profile.bad_ch_soft_pct:
        contact_score -= 0.35
        reasons.append(
            f"bad channels {window_qa.bad_channel_pct:.1f}% >= soft {montage_profile.bad_ch_soft_pct}%"
        )
    pen.contact = w.contact * 100.0 * (1.0 - _clip01(contact_score))

    # Cleanliness from ODQ / spectral proxies
    clean = window_qa.odq / 100.0
    if window_qa.line_noise_ratio > 0.35:
        clean -= 0.15
        reasons.append(f"elevated line noise ratio {window_qa.line_noise_ratio:.2f}")
    if window_qa.muscle_band_ratio > 0.45:
        clean -= 0.15
        reasons.append(f"elevated muscle-band ratio {window_qa.muscle_band_ratio:.2f}")
    pen.cleanliness = w.cleanliness * 100.0 * (1.0 - _clip01(clean))

    # Usable time vs duration-adaptive target
    u = usable / max(duration_profile.usable_target, 1e-6)
    pen.usable_time = w.usable_time * 100.0 * (1.0 - _clip01(u))
    if usable < duration_profile.usable_for_c:
        reasons.append(
            f"usable_ratio {usable:.2f} below {duration_profile.name} floor {duration_profile.usable_for_c}"
        )

    # Integrity: events / duration match / channels present
    integrity = 1.0
    if not rec.event_ok:
        integrity -= 0.8
        reasons.append("event markers failed integrity check")
    stim = rec.stimulus_duration_s
    if stim is not None and stim > 0:
        rel = abs(rec.resolved_duration_s() - stim) / stim
        if rel > 0.15:
            integrity -= 0.5
            reasons.append(f"recording vs stimulus duration mismatch {rel:.1%}")
        elif rel > 0.05:
            integrity -= 0.2
    pen.integrity = w.integrity * 100.0 * (1.0 - _clip01(integrity))

    # Stimulus sync only (AV alignment). Not cognitive / paradigm performance.
    sync_score = 1.0
    if rec.sync_error_ms is not None:
        if rec.sync_error_ms >= cfg.sync_fail_ms:
            sync_score -= 0.8
            reasons.append(f"sync error {rec.sync_error_ms:.0f} ms >= fail {cfg.sync_fail_ms}")
        elif rec.sync_error_ms >= cfg.sync_warn_ms:
            sync_score -= 0.35
            reasons.append(f"sync error {rec.sync_error_ms:.0f} ms >= warn {cfg.sync_warn_ms}")
    pen.stimulus_sync = w.stimulus_sync * 100.0 * (1.0 - _clip01(sync_score))

    return pen, reasons


def gqi_from_penalties(penalties: PenaltyBreakdown) -> float:
    return float(max(0.0, min(100.0, 100.0 - penalties.total())))


def availability_from_report(
    letter: LetterGrade,
    gqi: float,
    event_ok: bool,
    usable: float,
    duration_profile: DurationProfile,
) -> AvailabilityFlag:
    """HBN-inspired availability flag (orthogonal to letter grade)."""
    if not event_ok or usable < 0.5 * duration_profile.usable_for_c or letter == LetterGrade.D and gqi < 40:
        return AvailabilityFlag.UNAVAILABLE
    if letter in {LetterGrade.C, LetterGrade.D} or gqi < 70:
        return AvailabilityFlag.CAUTION
    return AvailabilityFlag.AVAILABLE
