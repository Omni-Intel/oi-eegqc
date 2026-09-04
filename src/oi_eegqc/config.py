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
    # Minimum usable-time ratio required for letter grades A/B/C (else D).
    usable_for_a: float
    usable_for_b: float
    usable_for_c: float
    # Soft penalty scale for GQI usable-time term.
    usable_target: float


@dataclass
class MontageProfile:
    name: str
    min_channels: int
    max_channels: int
    # PREP/HAPPILEE-inspired: denser arrays tolerate stricter neighbor correlation.
    corr_threshold: float
    # Fraction of windows a channel may be bad before marked globally bad.
    bad_channel_broken_frac: float
    # Bad-channel percent soft/hard ceilings for grading.
    bad_ch_soft_pct: float
    bad_ch_hard_pct: float
    # High-amplitude robust-z threshold (window).
    amp_z: float
    # Noise-to-signal ratio threshold (HF / signal band).
    nsr_threshold: float


@dataclass
class LetterCutoffs:
    """WeBrain-compatible ODQ letter cutoffs (overridable)."""

    a: float = 90.0
    b: float = 80.0
    c: float = 60.0


@dataclass
class GQIWeights:
    contact: float = 0.20
    cleanliness: float = 0.35
    usable_time: float = 0.25
    integrity: float = 0.10
    task_validity: float = 0.10


@dataclass
class BenchConfig:
    threshold_version: str = "oi-eegqc-v0.1"
    letter: LetterCutoffs = field(default_factory=LetterCutoffs)
    gqi_weights: GQIWeights = field(default_factory=GQIWeights)
    highpass_hz: float = 1.0
    signal_band_hz: tuple[float, float] = (1.0, 50.0)
    noise_band_hz: tuple[float, float] = (50.0, 100.0)
    line_hz: float = 50.0
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


def default_config() -> BenchConfig:
    return BenchConfig(
        duration_profiles=[
            DurationProfile(
                name="ultra_short",
                min_s=0.0,
                max_s=8.0,
                window_s=1.0,
                hop_s=0.5,
                usable_for_a=0.90,
                usable_for_b=0.75,
                usable_for_c=0.55,
                usable_target=0.85,
            ),
            DurationProfile(
                name="short",
                min_s=8.0,
                max_s=20.0,
                window_s=1.5,
                hop_s=0.75,
                usable_for_a=0.88,
                usable_for_b=0.72,
                usable_for_c=0.55,
                usable_target=0.82,
            ),
            DurationProfile(
                name="medium",
                min_s=20.0,
                max_s=45.0,
                window_s=2.0,
                hop_s=1.0,
                usable_for_a=0.85,
                usable_for_b=0.70,
                usable_for_c=0.55,
                usable_target=0.80,
            ),
            DurationProfile(
                name="long",
                min_s=45.0,
                max_s=1e9,
                window_s=2.5,
                hop_s=1.25,
                usable_for_a=0.82,
                usable_for_b=0.68,
                usable_for_c=0.50,
                usable_target=0.78,
            ),
        ],
        montage_profiles=[
            MontageProfile(
                name="low_density",
                min_channels=1,
                max_channels=16,
                corr_threshold=0.45,
                bad_channel_broken_frac=0.35,
                bad_ch_soft_pct=15.0,
                bad_ch_hard_pct=35.0,
                amp_z=5.5,
                nsr_threshold=0.8,
            ),
            MontageProfile(
                name="mid_density",
                min_channels=17,
                max_channels=64,
                corr_threshold=0.55,
                bad_channel_broken_frac=0.40,
                bad_ch_soft_pct=12.0,
                bad_ch_hard_pct=30.0,
                amp_z=5.0,
                nsr_threshold=0.7,
            ),
            MontageProfile(
                name="high_density",
                min_channels=65,
                max_channels=512,
                corr_threshold=0.75,
                bad_channel_broken_frac=0.40,
                bad_ch_soft_pct=10.0,
                bad_ch_hard_pct=25.0,
                amp_z=5.0,
                nsr_threshold=0.6,
            ),
        ],
    )


def _duration_from_dict(d: dict[str, Any]) -> DurationProfile:
    return DurationProfile(**d)


def _montage_from_dict(d: dict[str, Any]) -> MontageProfile:
    return MontageProfile(**d)


def load_config(path: str | Path | None = None) -> BenchConfig:
    cfg = default_config()
    if path is None:
        return cfg
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "threshold_version" in raw:
        cfg.threshold_version = str(raw["threshold_version"])
    if "highpass_hz" in raw:
        cfg.highpass_hz = float(raw["highpass_hz"])
    if "line_hz" in raw:
        cfg.line_hz = float(raw["line_hz"])
    if "letter" in raw:
        cfg.letter = LetterCutoffs(**raw["letter"])
    if "gqi_weights" in raw:
        cfg.gqi_weights = GQIWeights(**raw["gqi_weights"])
    if "duration_profiles" in raw:
        cfg.duration_profiles = [_duration_from_dict(x) for x in raw["duration_profiles"]]
    if "montage_profiles" in raw:
        cfg.montage_profiles = [_montage_from_dict(x) for x in raw["montage_profiles"]]
    if "impedance_good_kohm" in raw:
        cfg.impedance_good_kohm = float(raw["impedance_good_kohm"])
    if "impedance_ok_kohm" in raw:
        cfg.impedance_ok_kohm = float(raw["impedance_ok_kohm"])
    if "sync_warn_ms" in raw:
        cfg.sync_warn_ms = float(raw["sync_warn_ms"])
    if "sync_fail_ms" in raw:
        cfg.sync_fail_ms = float(raw["sync_fail_ms"])
    return cfg


def dump_default_config(path: str | Path) -> None:
    cfg = default_config()
    payload = {
        "threshold_version": cfg.threshold_version,
        "highpass_hz": cfg.highpass_hz,
        "line_hz": cfg.line_hz,
        "impedance_good_kohm": cfg.impedance_good_kohm,
        "impedance_ok_kohm": cfg.impedance_ok_kohm,
        "sync_warn_ms": cfg.sync_warn_ms,
        "sync_fail_ms": cfg.sync_fail_ms,
        "letter": {
            "a": cfg.letter.a,
            "b": cfg.letter.b,
            "c": cfg.letter.c,
        },
        "gqi_weights": {
            "contact": cfg.gqi_weights.contact,
            "cleanliness": cfg.gqi_weights.cleanliness,
            "usable_time": cfg.gqi_weights.usable_time,
            "integrity": cfg.gqi_weights.integrity,
            "task_validity": cfg.gqi_weights.task_validity,
        },
        "duration_profiles": [p.__dict__ for p in cfg.duration_profiles],
        "montage_profiles": [p.__dict__ for p in cfg.montage_profiles],
    }
    Path(path).write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
