from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DurationProfile:
    name: str
    min_s: float
    max_s: float
    window_s: float
    hop_s: float
    # Duration-adaptive ODQ cutoffs for letter grades A/B/C (else D).
    # Short clips have few windows, so a single lost window costs more; the
    # cutoffs relax slightly as clips get longer.
    odq_for_a: float
    odq_for_b: float
    odq_for_c: float
    # Reference usable-window ratio for the GQI usable-time term.
    usable_target: float


@dataclass
class MontageProfile:
    name: str
    min_channels: int
    max_channels: int
    # PREP/HAPPILEE-inspired: denser arrays tolerate stricter neighbor
    # correlation. Calibrated from the measured separation between detached
    # electrodes (top-3 |corr| around 0.09-0.17) and intact recordings
    # (NOD-EEG 5th percentile 0.69, Neuracle 0.71).
    corr_threshold: float
    # Spatial coupling is only diagnostic on montages dense enough for
    # electrodes to share signal. On a 4-channel headset the intact channels
    # score 0.13-0.31, overlapping the detached range, so the detector cannot
    # separate the two and is switched off rather than given an arbitrary
    # threshold. Sparse montages rely on the amplitude gates and NSR instead.
    corr_detector_enabled: bool
    # Fraction of windows a channel may be bad before marked globally bad.
    bad_channel_broken_frac: float
    # Bad-channel percent ceilings capping the letter grade at B / C / D.
    bad_ch_caution_pct: float
    bad_ch_soft_pct: float
    bad_ch_hard_pct: float
    # Robust-z threshold for amplitude outlier detection (temporal + spatial).
    amp_z: float
    # Noise-to-signal ratio threshold (HF / signal band).
    nsr_threshold: float
    # Mains-band power as a fraction of signal-band power before flagging.
    # Calibrated so a cell is flagged only once mains power rivals the entire
    # 1-45 Hz band. Milder interference is narrowband and notch-removable, so
    # it lowers the cleanliness score without disqualifying the recording.
    line_ratio_threshold: float
    # Absolute amplitude gates in microvolts, applied to high-passed windows.
    # ptp_max_uv catches saturation / rail clipping and gross movement;
    # ptp_min_uv catches dead amplifiers, detached leads and shorted pairs.
    ptp_max_uv: float
    ptp_min_uv: float
    # A window counts as usable while at most this fraction of channels is bad.
    max_bad_ch_frac_per_window: float


@dataclass
class LetterCutoffs:
    """Global ODQ letter cutoffs used when a duration profile has no override."""

    a: float = 90.0
    b: float = 80.0
    c: float = 60.0


@dataclass
class GQIWeights:
    """GQI dimension weights.

    ``cleanliness`` scores contamination density (channel x window cells) and
    ``usable_time`` scores surviving recording time (window pass rate). They are
    computed from different quantities on purpose; see ``WindowQASummary``.
    """

    contact: float = 0.20
    cleanliness: float = 0.30
    usable_time: float = 0.30
    integrity: float = 0.10
    stimulus_sync: float = 0.10


@dataclass
class BenchConfig:
    threshold_version: str = "oi-eegqc-v0.2.0"
    letter: LetterCutoffs = field(default_factory=LetterCutoffs)
    gqi_weights: GQIWeights = field(default_factory=GQIWeights)
    highpass_hz: float = 1.0
    # The signal and HF-noise bands leave a guard gap around the mains
    # frequency. Overlapping them, as (1, 50) and (50, 100) did, counts mains
    # power as both signal and noise and conflates interference with
    # broadband/muscle noise.
    signal_band_hz: tuple[float, float] = (1.0, 45.0)
    noise_band_hz: tuple[float, float] = (55.0, 95.0)
    line_hz: float = 50.0
    line_halfwidth_hz: float = 2.0
    muscle_band_hz: tuple[float, float] = (20.0, 45.0)
    default_aux_names: tuple[str, ...] = (
        "ECG",
        "EKG",
        "HEOR",
        "HEOL",
        "VEOU",
        "VEOL",
        "EOG",
        "EMG",
        "TRIG",
        "STI 014",
    )
    impedance_good_kohm: float = 5.0
    impedance_ok_kohm: float = 10.0
    sync_warn_ms: float = 40.0
    sync_fail_ms: float = 100.0
    # Fraction of samples sitting on a channel's own extreme rail before the
    # channel is declared clipped. Clean EEG stays far below 1%.
    clip_frac_threshold: float = 0.01
    # Share of the montage that must be rail-clipped or missing before the
    # recording is rejected outright rather than merely scored low. Kept
    # narrow on purpose: broad hard-fail gates make GQI discontinuous, so
    # graded degradation is preferred wherever it is meaningful.
    hard_fail_clipped_frac: float = 0.5
    hard_fail_present_channel_frac: float = 0.75
    # Cleanliness blends flag density with a continuous spectral score. This is
    # the weight on the continuous part; without it the score is a step
    # function, since every cell flips at the same NSR threshold.
    spectral_blend: float = 0.4
    duration_profiles: list[DurationProfile] = field(default_factory=list)
    montage_profiles: list[MontageProfile] = field(default_factory=list)

    def select_duration(self, duration_s: float) -> DurationProfile:
        for profile in self.duration_profiles:
            if profile.min_s <= duration_s < profile.max_s:
                return profile
        # Fallback: nearest by center.
        return min(
            self.duration_profiles,
            key=lambda p: abs(duration_s - 0.5 * (p.min_s + p.max_s)),
        )

    def select_montage(self, n_channels: int) -> MontageProfile:
        for profile in self.montage_profiles:
            if profile.min_channels <= n_channels <= profile.max_channels:
                return profile
        return min(
            self.montage_profiles,
            key=lambda p: abs(n_channels - 0.5 * (p.min_channels + p.max_channels)),
        )

    def resolved_noise_band(self, sfreq: float) -> tuple[float, float] | None:
        """Clamp the HF noise band to Nyquist, or None when unusable."""
        lo, hi = self.noise_band_hz
        nyq = 0.5 * float(sfreq)
        hi = min(hi, nyq - 1.0)
        if hi <= lo + 2.0:
            return None
        return (lo, hi)


def default_config() -> BenchConfig:
    return BenchConfig(
        duration_profiles=[
            DurationProfile(
                name="ultra_short",
                min_s=0.0,
                max_s=8.0,
                window_s=1.0,
                hop_s=0.5,
                odq_for_a=95.0,
                odq_for_b=85.0,
                odq_for_c=65.0,
                usable_target=0.90,
            ),
            DurationProfile(
                name="short",
                min_s=8.0,
                max_s=20.0,
                window_s=1.5,
                hop_s=0.75,
                odq_for_a=92.0,
                odq_for_b=82.0,
                odq_for_c=62.0,
                usable_target=0.88,
            ),
            DurationProfile(
                name="medium",
                min_s=20.0,
                max_s=45.0,
                window_s=2.0,
                hop_s=1.0,
                odq_for_a=90.0,
                odq_for_b=80.0,
                odq_for_c=60.0,
                usable_target=0.85,
            ),
            DurationProfile(
                name="long",
                min_s=45.0,
                max_s=1e9,
                window_s=2.5,
                hop_s=1.25,
                odq_for_a=88.0,
                odq_for_b=78.0,
                odq_for_c=58.0,
                usable_target=0.82,
            ),
        ],
        montage_profiles=[
            MontageProfile(
                name="low_density",
                min_channels=1,
                max_channels=16,
                corr_threshold=0.30,
                corr_detector_enabled=False,
                bad_channel_broken_frac=0.35,
                bad_ch_caution_pct=10.0,
                bad_ch_soft_pct=15.0,
                bad_ch_hard_pct=35.0,
                amp_z=5.5,
                nsr_threshold=0.8,
                line_ratio_threshold=1.2,
                ptp_max_uv=400.0,
                ptp_min_uv=1.0,
                max_bad_ch_frac_per_window=0.34,
            ),
            MontageProfile(
                name="mid_density",
                min_channels=17,
                max_channels=64,
                corr_threshold=0.40,
                corr_detector_enabled=True,
                bad_channel_broken_frac=0.40,
                bad_ch_caution_pct=5.0,
                bad_ch_soft_pct=12.0,
                bad_ch_hard_pct=30.0,
                amp_z=5.0,
                nsr_threshold=0.7,
                line_ratio_threshold=1.0,
                ptp_max_uv=350.0,
                ptp_min_uv=1.0,
                max_bad_ch_frac_per_window=0.25,
            ),
            MontageProfile(
                name="high_density",
                min_channels=65,
                max_channels=512,
                corr_threshold=0.40,
                corr_detector_enabled=True,
                bad_channel_broken_frac=0.40,
                bad_ch_caution_pct=5.0,
                bad_ch_soft_pct=10.0,
                bad_ch_hard_pct=25.0,
                amp_z=5.0,
                nsr_threshold=0.6,
                line_ratio_threshold=1.0,
                ptp_max_uv=350.0,
                ptp_min_uv=1.0,
                max_bad_ch_frac_per_window=0.25,
            ),
        ],
    )


def _duration_from_dict(d: dict[str, Any]) -> DurationProfile:
    return DurationProfile(**d)


def _montage_from_dict(d: dict[str, Any]) -> MontageProfile:
    return MontageProfile(**d)


_SCALAR_KEYS = (
    "highpass_hz",
    "line_hz",
    "line_halfwidth_hz",
    "impedance_good_kohm",
    "impedance_ok_kohm",
    "sync_warn_ms",
    "sync_fail_ms",
    "clip_frac_threshold",
    "hard_fail_clipped_frac",
    "hard_fail_present_channel_frac",
    "spectral_blend",
)


def load_config(path: str | Path | None = None) -> BenchConfig:
    cfg = default_config()
    if path is None:
        return cfg
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "threshold_version" in raw:
        cfg.threshold_version = str(raw["threshold_version"])
    for key in _SCALAR_KEYS:
        if key in raw:
            setattr(cfg, key, float(raw[key]))
    if "letter" in raw:
        cfg.letter = LetterCutoffs(**raw["letter"])
    if "gqi_weights" in raw:
        cfg.gqi_weights = GQIWeights(**raw["gqi_weights"])
    if "duration_profiles" in raw:
        cfg.duration_profiles = [_duration_from_dict(x) for x in raw["duration_profiles"]]
    if "montage_profiles" in raw:
        cfg.montage_profiles = [_montage_from_dict(x) for x in raw["montage_profiles"]]
    return cfg


def dump_default_config(path: str | Path) -> None:
    cfg = default_config()
    payload: dict[str, Any] = {"threshold_version": cfg.threshold_version}
    for key in _SCALAR_KEYS:
        payload[key] = getattr(cfg, key)
    payload["letter"] = {"a": cfg.letter.a, "b": cfg.letter.b, "c": cfg.letter.c}
    payload["gqi_weights"] = {
        "contact": cfg.gqi_weights.contact,
        "cleanliness": cfg.gqi_weights.cleanliness,
        "usable_time": cfg.gqi_weights.usable_time,
        "integrity": cfg.gqi_weights.integrity,
        "stimulus_sync": cfg.gqi_weights.stimulus_sync,
    }
    payload["duration_profiles"] = [p.__dict__ for p in cfg.duration_profiles]
    payload["montage_profiles"] = [p.__dict__ for p in cfg.montage_profiles]
    Path(path).write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
