"""Format loaders. Dataset-specific layout lives in ``oi_eegqc.datasets``."""

from .edf import MneRequiredError, find_bdf, infer_unit, load_edf_bdf, require_mne
from .array import load_channel_names, load_npy, orient_channels_first, split_recording_kwargs
from .reports import report_payload, summarize_reports, write_bench_json
from .segment import (
    DEFAULT_CLIP_SECONDS,
    centered_clips,
    concat_epochs,
    epoch_duration_plan,
)

__all__ = [
    "DEFAULT_CLIP_SECONDS",
    "MneRequiredError",
    "centered_clips",
    "concat_epochs",
    "epoch_duration_plan",
    "find_bdf",
    "infer_unit",
    "load_channel_names",
    "load_edf_bdf",
    "load_npy",
    "orient_channels_first",
    "report_payload",
    "require_mne",
    "summarize_reports",
    "write_bench_json",
]
