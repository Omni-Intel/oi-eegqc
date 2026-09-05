from __future__ import annotations

from typing import Iterator

import numpy as np

from ..types import RecordingInput
from .base import DatasetAdapter, DatasetSpec


def synth_clean(
    n_ch: int,
    sfreq: float,
    duration_s: float,
    seed: int = 0,
) -> np.ndarray:
    """Synthetic resting EEG in microvolts with realistic spatial coupling."""
    rng = np.random.default_rng(seed)
    n_times = int(sfreq * duration_s)
    t = np.arange(n_times) / sfreq
    shared = 8.0 * np.sin(2 * np.pi * 10.0 * t) + 4.0 * rng.standard_normal(n_times)
    data = np.stack([shared * (0.6 + 0.5 * rng.random()) for _ in range(n_ch)], axis=0)
    return data + 3.0 * rng.standard_normal(data.shape)


class SyntheticAdapter(DatasetAdapter):
    """In-memory clean / noisy / dead / saturated cases for smoke tests."""

    spec = DatasetSpec(
        name="synthetic",
        kind="synthetic",
        description="In-memory clean, noisy, dead-quarter and saturated clips",
        default_unit="uV",
    )

    def __init__(
        self,
        *,
        n_channels: int = 32,
        duration_s: float = 20.0,
        sfreq: float = 250.0,
        seed: int = 0,
    ) -> None:
        self.n_channels = n_channels
        self.duration_s = duration_s
        self.sfreq = sfreq
        self.seed = seed

    def estimate_count(self) -> int | None:
        return 4

    def iter_recordings(self) -> Iterator[RecordingInput]:
        rng = np.random.default_rng(self.seed)
        n_ch = self.n_channels
        n_times = int(self.sfreq * self.duration_s)
        names = [f"EEG{i:02d}" for i in range(n_ch)]
        clean = synth_clean(n_ch, self.sfreq, self.duration_s, self.seed)

        noisy = clean + 12.0 * rng.standard_normal(clean.shape)
        burst = int(0.5 * self.sfreq)
        noisy[:, n_times // 3 : n_times // 3 + burst] += 120.0 * rng.standard_normal(
            (n_ch, burst)
        )

        dead = clean.copy()
        dead[: max(1, n_ch // 4)] = 0.0

        saturated = np.clip(clean * 40.0, -400.0, 400.0)

        cases = [
            ("synthetic_clean", clean),
            ("synthetic_noisy", noisy),
            ("synthetic_dead_quarter", dead),
            ("synthetic_saturated", saturated),
        ]
        for clip_id, data in cases:
            yield RecordingInput(
                data=data,
                sfreq=self.sfreq,
                ch_names=names,
                unit="uV",
                clip_id=clip_id,
                subject_id="demo",
                session_id="synthetic",
                expected_n_channels=n_ch,
                meta={"dataset": self.spec.name},
            )
