"""OI-EEGQC: adaptive EEG quality validation bench."""

from .config import BenchConfig, dump_default_config, load_config
from .datasets import list_datasets, open_dataset, score_adapter
from .io import load_edf_bdf, load_npy
from .pipeline import evaluate_batch, evaluate_recording, load_npy_recording
from .protocol import PROTOCOL_SCHEMA_VERSION, ProtocolError, envelope
from .types import (
    REPORT_SCHEMA_VERSION,
    AvailabilityFlag,
    LetterGrade,
    QualityReport,
    RecordingInput,
)

__version__ = "0.3.0"
__all__ = [
    "AvailabilityFlag",
    "BenchConfig",
    "LetterGrade",
    "PROTOCOL_SCHEMA_VERSION",
    "ProtocolError",
    "QualityReport",
    "REPORT_SCHEMA_VERSION",
    "RecordingInput",
    "__version__",
    "dump_default_config",
    "envelope",
    "evaluate_batch",
    "evaluate_recording",
    "list_datasets",
    "load_config",
    "load_edf_bdf",
    "load_npy",
    "load_npy_recording",
    "open_dataset",
    "score_adapter",
]
