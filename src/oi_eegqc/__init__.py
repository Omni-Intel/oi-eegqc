"""OI-EEGQC: adaptive EEG quality validation bench."""

from .pipeline import evaluate_recording, evaluate_batch
from .types import AvailabilityFlag, LetterGrade, QualityReport, RecordingInput

__version__ = "0.1.0"
__all__ = [
    "evaluate_recording",
    "evaluate_batch",
    "LetterGrade",
    "AvailabilityFlag",
    "RecordingInput",
    "QualityReport",
    "__version__",
]
