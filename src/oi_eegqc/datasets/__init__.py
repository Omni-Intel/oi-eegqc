"""Named dataset adapters. Each one yields :class:`RecordingInput` only."""

from __future__ import annotations

from typing import Any

from .base import AdapterError, DatasetAdapter, DatasetSpec
from .bench import iter_scored, score_adapter
from .epoched import NodEegAdapter, ThingsEeg2Adapter
from .hw import HuaweiSessionAdapter
from .npy_dir import NpyDirAdapter
from .synthetic import SyntheticAdapter, synth_clean

ADAPTERS: dict[str, type[DatasetAdapter]] = {
    "npy": NpyDirAdapter,
    "hw": HuaweiSessionAdapter,
    "nod": NodEegAdapter,
    "things": ThingsEeg2Adapter,
    "synthetic": SyntheticAdapter,
}

# Common local defaults on this workstation; callers should still pass ``root``.
DEFAULT_ROOTS: dict[str, str] = {
    "hw": "/vePFS-0x0e/xkp/oi-eegqc/bench_runs/hw_extract/hw",
    "nod": "/vePFS-0x0e/xkp/dense-global-caption/data/nod_eeg/epochs_uV",
    "things": "/vePFS-0x0e/xkp/datasets/Things-EEG2/Preprocessed_data_250Hz",
}

DEFAULT_NOD_CHANNELS_TSV = (
    "/vePFS-0x0e/xkp/ds005811/sub-01/ses-ImageNet01/eeg/"
    "sub-01_ses-ImageNet01_task-ImageNet_run-01_channels.tsv"
)


def list_datasets() -> list[DatasetSpec]:
    return [cls.spec for cls in ADAPTERS.values()]


def get_adapter_class(name: str) -> type[DatasetAdapter]:
    key = name.strip().lower()
    if key not in ADAPTERS:
        known = ", ".join(sorted(ADAPTERS))
        raise KeyError(f"Unknown dataset {name!r}. Registered: {known}")
    return ADAPTERS[key]


def open_dataset(name: str, *args: Any, **kwargs: Any) -> DatasetAdapter:
    """Construct a registered adapter. ``root`` is required except for synthetic."""
    cls = get_adapter_class(name)
    if cls is NodEegAdapter and "channels_tsv" not in kwargs:
        kwargs["channels_tsv"] = DEFAULT_NOD_CHANNELS_TSV
    return cls(*args, **kwargs)


__all__ = [
    "ADAPTERS",
    "DEFAULT_NOD_CHANNELS_TSV",
    "DEFAULT_ROOTS",
    "AdapterError",
    "DatasetAdapter",
    "DatasetSpec",
    "HuaweiSessionAdapter",
    "NodEegAdapter",
    "NpyDirAdapter",
    "SyntheticAdapter",
    "ThingsEeg2Adapter",
    "get_adapter_class",
    "iter_scored",
    "list_datasets",
    "open_dataset",
    "score_adapter",
    "synth_clean",
]
