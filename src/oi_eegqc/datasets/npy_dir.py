from __future__ import annotations

from pathlib import Path
from typing import Iterator

from ..io.array import load_npy
from ..types import RecordingInput
from .base import DatasetAdapter, DatasetSpec


class NpyDirAdapter(DatasetAdapter):
    """Directory of 2D ``.npy`` clips, one recording per file."""

    spec = DatasetSpec(
        name="npy",
        kind="files",
        description="Directory of (n_channels, n_times) .npy clips",
        default_unit="uV",
    )

    def __init__(
        self,
        root: str | Path,
        sfreq: float,
        *,
        pattern: str = "*.npy",
        ch_names_path: str | Path | None = None,
        unit: str = "uV",
        adc_to_uv: float | None = None,
        expected_n_channels: int | None = None,
        subject_id: str | None = None,
        session_id: str | None = None,
        channels_first: bool | None = None,
    ) -> None:
        self.root = Path(root)
        self.sfreq = float(sfreq)
        self.pattern = pattern
        self.ch_names_path = ch_names_path
        self.unit = unit
        self.adc_to_uv = adc_to_uv
        self.expected_n_channels = expected_n_channels
        self.subject_id = subject_id
        self.session_id = session_id
        self.channels_first = channels_first

    def iter_recordings(self) -> Iterator[RecordingInput]:
        files = sorted(self.root.glob(self.pattern))
        if not files:
            raise FileNotFoundError(f"No files matched {self.root}/{self.pattern}")
        for path in files:
            rec = load_npy(
                path,
                self.sfreq,
                ch_names_path=self.ch_names_path,
                channels_first=self.channels_first,
                unit=self.unit,
                adc_to_uv=self.adc_to_uv,
                expected_n_channels=self.expected_n_channels,
                subject_id=self.subject_id,
                session_id=self.session_id or self.root.name,
                clip_id=path.stem,
                dataset=self.spec.name,
            )
            rec.meta["dataset"] = self.spec.name
            yield rec
