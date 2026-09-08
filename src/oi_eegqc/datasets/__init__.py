"""Named dataset adapters. Each one yields :class:`RecordingInput` only."""

from __future__ import annotations

from typing import Any

from .avsession import AvSessionAdapter, looks_like_avsession
from .base import AdapterError, DatasetAdapter, DatasetSpec
from ..protocol import ProtocolError
from .bench import iter_scored, score_adapter
from .epoched import NodEegAdapter, ThingsEeg2Adapter
from .hw import HuaweiSessionAdapter
from .npy_dir import NpyDirAdapter
from .synthetic import SyntheticAdapter, synth_clean

ADAPTERS: dict[str, type[DatasetAdapter]] = {
    "npy": NpyDirAdapter,
    "hw": HuaweiSessionAdapter,
    "avsession": AvSessionAdapter,
    "nod": NodEegAdapter,
    "things": ThingsEeg2Adapter,
    "synthetic": SyntheticAdapter,
}


def list_datasets() -> list[DatasetSpec]:
    return [cls.spec for cls in ADAPTERS.values()]


def get_adapter_class(name: str) -> type[DatasetAdapter]:
    key = name.strip().lower()
    if key not in ADAPTERS:
        known = ", ".join(sorted(ADAPTERS))
        raise ProtocolError(
            "unknown_dataset",
            f"Unknown dataset {name!r}. Registered: {known}",
            details={"known": sorted(ADAPTERS)},
        )
    return ADAPTERS[key]


def open_dataset(name: str, *args: Any, **kwargs: Any) -> DatasetAdapter:
    """Construct a registered adapter. ``root`` is required except for synthetic."""
    cls = get_adapter_class(name)
    return cls(*args, **kwargs)


__all__ = [
    "ADAPTERS",
    "AdapterError",
    "AvSessionAdapter",
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
    "looks_like_avsession",
    "open_dataset",
    "score_adapter",
    "synth_clean",
]
