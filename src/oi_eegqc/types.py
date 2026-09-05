from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class LetterGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class AvailabilityFlag(str, Enum):
    AVAILABLE = "Available"
    CAUTION = "Caution"
    UNAVAILABLE = "Unavailable"


#: Frozen into every ``QualityReport.to_dict()``. Bump when report fields change.
REPORT_SCHEMA_VERSION = "oi-eegqc-report-v1"


#: Multipliers converting a declared input unit into microvolts.
UNIT_TO_UV: dict[str, float] = {
    "uv": 1.0,
    "µv": 1.0,
    "microvolt": 1.0,
    "mv": 1e3,
    "v": 1e6,
    "volt": 1e6,
}


@dataclass
class RecordingInput:
    """One continuous EEG segment aligned to a stimulus / task clip.

    ``unit`` declares the physical unit of ``data`` and is mandatory in spirit:
    absolute amplitude gates (saturation, flat/dead channels) are meaningless
    without it. Use ``unit="adc"`` together with ``adc_to_uv`` for raw counts.
    """

    data: Any  # np.ndarray, shape (n_channels, n_times)
    sfreq: float
    ch_names: list[str]
    unit: str = "uV"
    adc_to_uv: float | None = None
    duration_s: float | None = None
    subject_id: str | None = None
    session_id: str | None = None
    clip_id: str | None = None
    stimulus_duration_s: float | None = None
    expected_n_channels: int | None = None
    impedance_kohm: dict[str, float] | None = None
    event_ok: bool = True
    sync_error_ms: float | None = None
    aux_ch_names: list[str] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def resolved_duration_s(self) -> float:
        if self.duration_s is not None:
            return float(self.duration_s)
        n_times = int(self.data.shape[-1])
        return n_times / float(self.sfreq)

    def to_uv_scale(self) -> float:
        """Return the multiplier that converts ``data`` into microvolts."""
        key = str(self.unit).strip().lower()
        if key in {"adc", "count", "counts"}:
            if self.adc_to_uv is None or self.adc_to_uv <= 0:
                raise ValueError(
                    "unit='adc' requires a positive adc_to_uv conversion factor; "
                    "absolute amplitude gates cannot run on raw counts."
                )
            return float(self.adc_to_uv)
        if key not in UNIT_TO_UV:
            raise ValueError(
                f"Unknown unit {self.unit!r}. Expected one of "
                f"{sorted(set(UNIT_TO_UV))} or 'adc' with adc_to_uv."
            )
        return UNIT_TO_UV[key]


@dataclass
class WindowQASummary:
    """Window QA output.

    ``clean_ratio`` and ``usable_window_ratio`` are deliberately different
    quantities and must not be conflated:

    * ``clean_ratio`` is a *density* over channel x window cells. It answers
      "how much of the recorded surface is contaminated".
    * ``usable_window_ratio`` is a *time* measure. A window counts as usable
      when at most ``max_bad_ch_frac_per_window`` of its channels are bad, so
      this answers "how many seconds survive". This is the WeBrain-style ODQ.

    Ten percent bad channels in every window gives clean_ratio 0.90 with
    usable_window_ratio 1.00; ten percent of windows destroyed outright gives
    clean_ratio 0.90 with usable_window_ratio 0.90.
    """

    n_windows: int
    n_channels: int
    clean_ratio: float
    bad_cell_ratio: float
    usable_window_ratio: float
    odq: float
    constant_ratio: float
    flat_ratio: float
    extreme_amp_ratio: float
    temporal_outlier_ratio: float
    spatial_outlier_ratio: float
    high_nsr_ratio: float
    line_noise_flag_ratio: float
    low_corr_ratio: float
    # Continuous spectral measures kept alongside the binary flags: a score
    # built only from thresholded flags degenerates into a step function.
    nsr_median: float
    nsr_p90: float
    bad_channel_pct: float
    bad_channels: list[str]
    clipped_channels: list[str]
    dead_channels: list[str]
    mean_abs_uv: float
    p99_abs_uv: float
    max_abs_uv: float
    line_noise_ratio: float
    muscle_band_ratio: float


@dataclass
class PenaltyBreakdown:
    contact: float = 0.0
    cleanliness: float = 0.0
    usable_time: float = 0.0
    integrity: float = 0.0
    stimulus_sync: float = 0.0

    def total(self) -> float:
        return (
            self.contact
            + self.cleanliness
            + self.usable_time
            + self.integrity
            + self.stimulus_sync
        )

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class QualityReport:
    letter_grade: LetterGrade
    availability: AvailabilityFlag
    gqi: float
    odq: float
    usable_ratio: float
    clean_ratio: float
    duration_profile: str
    montage_profile: str
    n_channels_used: int
    duration_s: float
    window_qa: WindowQASummary
    penalties: PenaltyBreakdown
    threshold_version: str
    hard_fail_reasons: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    subject_id: str | None = None
    session_id: str | None = None
    clip_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def hard_failed(self) -> bool:
        return bool(self.hard_fail_reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "subject_id": self.subject_id,
            "session_id": self.session_id,
            "clip_id": self.clip_id,
            "letter_grade": self.letter_grade.value,
            "availability": self.availability.value,
            "gqi": round(self.gqi, 2),
            "odq": round(self.odq, 2),
            "usable_ratio": round(self.usable_ratio, 4),
            "clean_ratio": round(self.clean_ratio, 4),
            "duration_profile": self.duration_profile,
            "montage_profile": self.montage_profile,
            "n_channels_used": self.n_channels_used,
            "duration_s": round(self.duration_s, 3),
            "window_qa": asdict(self.window_qa),
            "penalties": self.penalties.as_dict(),
            "threshold_version": self.threshold_version,
            "hard_fail_reasons": self.hard_fail_reasons,
            "reasons": self.reasons,
            "extras": self.extras,
        }
