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


@dataclass
class RecordingInput:
    """One continuous EEG segment aligned to a stimulus / task clip."""

    data: Any  # np.ndarray, shape (n_channels, n_times)
    sfreq: float
    ch_names: list[str]
    duration_s: float | None = None
    subject_id: str | None = None
    session_id: str | None = None
    clip_id: str | None = None
    stimulus_duration_s: float | None = None
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


@dataclass
class WindowQASummary:
    n_windows: int
    n_channels: int
    bad_window_ratio: float
    constant_ratio: float
    high_amp_ratio: float
    high_nsr_ratio: float
    low_corr_ratio: float
    odq: float
    bad_channel_pct: float
    bad_channels: list[str]
    mean_abs_uv: float
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
    duration_profile: str
    montage_profile: str
    n_channels_used: int
    duration_s: float
    window_qa: WindowQASummary
    penalties: PenaltyBreakdown
    threshold_version: str
    reasons: list[str] = field(default_factory=list)
    subject_id: str | None = None
    session_id: str | None = None
    clip_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "session_id": self.session_id,
            "clip_id": self.clip_id,
            "letter_grade": self.letter_grade.value,
            "availability": self.availability.value,
            "gqi": round(self.gqi, 2),
            "odq": round(self.odq, 2),
            "usable_ratio": round(self.usable_ratio, 4),
            "duration_profile": self.duration_profile,
            "montage_profile": self.montage_profile,
            "n_channels_used": self.n_channels_used,
            "duration_s": round(self.duration_s, 3),
            "window_qa": asdict(self.window_qa),
            "penalties": self.penalties.as_dict(),
            "threshold_version": self.threshold_version,
            "reasons": self.reasons,
            "extras": self.extras,
        }
