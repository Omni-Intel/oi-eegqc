from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

import numpy as np

from ..io.segment import concat_epochs, epoch_duration_plan
from ..types import RecordingInput
from .base import DatasetAdapter, DatasetSpec


def load_bids_eeg_channel_names(tsv_path: str | Path, n_channels: int) -> list[str]:
    names: list[str] = []
    path = Path(tsv_path)
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                if row.get("type", "").upper() == "EEG":
                    names.append(row["name"])
    if len(names) < n_channels:
        names.extend(f"E{i:02d}" for i in range(len(names), n_channels))
    return names[:n_channels]


class _EpochedAdapter(DatasetAdapter):
    """Shared machinery: concat short published epochs into duration profiles."""

    def __init__(
        self,
        root: str | Path,
        *,
        subjects: list[str] | None = None,
        seeds_per_subject: int = 2,
        seed: int = 20260904,
        sfreq: float = 250.0,
    ) -> None:
        self.root = Path(root)
        self.subjects = subjects
        self.seeds_per_subject = seeds_per_subject
        self.seed = seed
        self.sfreq = sfreq

    def _subject_ids(self) -> list[str]:
        if self.subjects:
            return list(self.subjects)
        return self.discover_subjects()

    def discover_subjects(self) -> list[str]:
        raise NotImplementedError

    def load_subject_epochs(self, subject: str) -> tuple[np.ndarray, list[str], float]:
        raise NotImplementedError

    def iter_recordings(self) -> Iterator[RecordingInput]:
        rng = np.random.default_rng(self.seed)
        for subject in self._subject_ids():
            epochs, ch_names, sfreq = self.load_subject_epochs(subject)
            n_ep, n_ch, n_times = epochs.shape
            for plan_name, n_need in epoch_duration_plan(n_times, sfreq):
                if n_need > n_ep:
                    continue
                for k in range(self.seeds_per_subject):
                    start = int(rng.integers(0, n_ep - n_need + 1))
                    data = concat_epochs(epochs, start, n_need)
                    yield RecordingInput(
                        data=data,
                        sfreq=sfreq,
                        ch_names=ch_names,
                        unit="uV",
                        subject_id=subject,
                        session_id=self.spec.name,
                        clip_id=f"{subject}_{plan_name}_s{start}_k{k}",
                        expected_n_channels=n_ch,
                        event_ok=True,
                        stimulus_duration_s=None,
                        sync_error_ms=None,
                        meta={
                            "dataset": self.spec.name,
                            "plan": plan_name,
                            "n_epochs_concat": n_need,
                            "epoch_start": start,
                            "n_channels": n_ch,
                            "unit_is_nominal": self.spec.unit_is_nominal,
                        },
                    )


class NodEegAdapter(_EpochedAdapter):
    """NOD-EEG epoch store: ``{subject}_epochs_uV.npy`` in physical microvolts."""

    spec = DatasetSpec(
        name="nod",
        kind="epoched",
        description="NOD-EEG concatenated epochs in microvolts",
        default_unit="uV",
    )

    def __init__(
        self,
        root: str | Path,
        *,
        channels_tsv: str | Path | None = None,
        **kwargs,
    ) -> None:
        super().__init__(root, **kwargs)
        self.channels_tsv = Path(channels_tsv) if channels_tsv else None

    def discover_subjects(self) -> list[str]:
        files = sorted(self.root.glob("*_epochs_uV.npy"))
        if not files:
            raise FileNotFoundError(f"No *_epochs_uV.npy under {self.root}")
        return [p.name.replace("_epochs_uV.npy", "") for p in files]

    def load_subject_epochs(self, subject: str) -> tuple[np.ndarray, list[str], float]:
        path = self.root / f"{subject}_epochs_uV.npy"
        arr = np.asarray(np.load(path), dtype=np.float64)
        names = (
            load_bids_eeg_channel_names(self.channels_tsv, arr.shape[1])
            if self.channels_tsv
            else [f"E{i:02d}" for i in range(arr.shape[1])]
        )
        return arr, names, self.sfreq


class ThingsEeg2Adapter(_EpochedAdapter):
    """THINGS-EEG2 preprocessed arrays. Unitless / noise-normalised.

    Scores are not comparable with microvolt datasets. The adapter still
    declares ``unit='uV'`` so the pipeline can run, and marks
    ``unit_is_nominal`` on every recording.
    """

    spec = DatasetSpec(
        name="things",
        kind="epoched",
        description="THINGS-EEG2 noise-normalised epochs (unitless; not a QC reference)",
        unit_is_nominal=True,
        default_unit="uV",
    )

    def discover_subjects(self) -> list[str]:
        subjects = sorted(p.name for p in self.root.iterdir() if p.is_dir() and p.name.startswith("sub-"))
        if not subjects:
            raise FileNotFoundError(f"No sub-* directories under {self.root}")
        return subjects

    def load_subject_epochs(self, subject: str) -> tuple[np.ndarray, list[str], float]:
        test_path = self.root / subject / "preprocessed_eeg_test.npy"
        train_path = self.root / subject / "preprocessed_eeg_training.npy"
        path = test_path if test_path.exists() else train_path
        if not path.exists():
            raise FileNotFoundError(f"No preprocessed THINGS array for {subject} under {self.root}")
        raw = np.load(path, allow_pickle=True)
        data = raw if isinstance(raw, dict) else raw.item()
        arr = np.asarray(data["preprocessed_eeg_data"], dtype=np.float64)
        n_img, n_rep, n_ch, n_times = arr.shape
        flat = arr.reshape(n_img * n_rep, n_ch, n_times)
        ch_names = [str(x) for x in data["ch_names"]]
        return flat, ch_names, self.sfreq
