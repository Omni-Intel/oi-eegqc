"""Re-export scoring symbols; letter enums live in oi_eegqc.types."""

from ..types import AvailabilityFlag, LetterGrade
from .grades import (
    DIMENSIONS,
    DimensionScore,
    apply_bad_channel_ceiling,
    availability_from_report,
    collect_hard_fails,
    compute_dimension_scores,
    gqi_from_scores,
    letter_from_odq,
)

__all__ = [
    "AvailabilityFlag",
    "LetterGrade",
    "DIMENSIONS",
    "DimensionScore",
    "apply_bad_channel_ceiling",
    "availability_from_report",
    "collect_hard_fails",
    "compute_dimension_scores",
    "gqi_from_scores",
    "letter_from_odq",
]
