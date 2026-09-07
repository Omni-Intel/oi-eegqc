"""Audiovisual-watching session folders: ``continuous_eeg.npy`` + ``events.json``.

One folder per recording stamp (``YYYYMMDD_HHMMSS``), optionally nested under a
session root that also has ``session_summary.json``. Clips are cut from
``video_on`` / ``video_off`` (and optionally rest) using software sample
indices. Hardware markers may be a no-op; that is recorded in ``extras``, not
used as a hard fail, because the local timeline is still what the task used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from ..types import RecordingInput
from .base import AdapterError, DatasetAdapter, DatasetSpec


def pair_events(events: list[dict[str, Any]], on: str, off: str) -> list[tuple[int, int, dict, dict]]:
    out: list[tuple[int, int, dict, dict]] = []
    start = None
    for event in events:
        if event.get("name") == on:
            start = event
        elif event.get("name") == off and start is not None:
            a, b = int(start["sample_index"]), int(event["sample_index"])
            if b > a:
                out.append((a, b, start, event))
            start = None
    return out


def looks_like_avsession(root: str | Path) -> bool:
    path = Path(root)
    if not path.is_dir():
        return False
    if (path / "session_summary.json").exists():
        return True
    if (path / "continuous_eeg.npy").exists() and (path / "metadata.json").exists():
        return True
    for child in path.iterdir():
        if (
            child.is_dir()
            and (child / "continuous_eeg.npy").exists()
            and (child / "metadata.json").exists()
        ):
            return True
    return False


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class AvSessionAdapter(DatasetAdapter):
    """BrainCo-style local continuous EEG from the visual-video task."""

    spec = DatasetSpec(
        name="avsession",
        kind="session",
        description="AV-watching session folders (continuous_eeg.npy + video events)",
        default_unit="uV",
    )

    def __init__(
        self,
        root: str | Path,
        *,
        unit: str = "uV",
        include_rest: bool = False,
        kinds: tuple[str, ...] = ("video",),
    ) -> None:
        self.root = Path(root)
        self.unit = unit
        self.include_rest = include_rest
        self.kinds = kinds if not include_rest else tuple(dict.fromkeys((*kinds, "rest")))

    def iter_stamps(self) -> Iterator[Path]:
        if not self.root.is_dir():
            raise FileNotFoundError(f"session root does not exist: {self.root}")
        if (self.root / "continuous_eeg.npy").exists() and (self.root / "metadata.json").exists():
            yield self.root
            return
        found = False
        for path in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if (path / "continuous_eeg.npy").exists() and (path / "metadata.json").exists():
                found = True
                yield path
        if not found:
            raise FileNotFoundError(
                f"No stamp folders with continuous_eeg.npy + metadata.json under {self.root}"
            )

    def estimate_count(self) -> int | None:
        n = 0
        try:
            stamps = list(self.iter_stamps())
        except FileNotFoundError:
            return 0
        for stamp in stamps:
            events_path = stamp / "events.json"
            if not events_path.exists():
                continue
            events = _load_json(events_path)
            if "video" in self.kinds:
                n += len(pair_events(events, "video_on", "video_off"))
            if "rest" in self.kinds:
                n += len(pair_events(events, "rest_start", "rest_end"))
        return n

    def iter_recordings(self) -> Iterator[RecordingInput]:
        for stamp in self.iter_stamps():
            yield from self._stamp_clips(stamp)

    def _stamp_clips(self, stamp: Path) -> Iterator[RecordingInput]:
        meta = _load_json(stamp / "metadata.json")
        events = _load_json(stamp / "events.json") if (stamp / "events.json").exists() else []
        segs = _load_json(stamp / "eeg_segments.json") if (stamp / "eeg_segments.json").exists() else {}
        termination = (segs.get("segments") or [{}])[0].get("termination_reason")
        npy_path = stamp / str(meta.get("eeg_file") or "continuous_eeg.npy")
        if not npy_path.exists():
            raise AdapterError(self.spec.name, f"missing {npy_path}")
        data = np.load(npy_path, mmap_mode="r")
        if data.ndim != 2:
            raise AdapterError(self.spec.name, f"{npy_path} is not 2D, got {data.shape}")
        n_ch = int(meta.get("n_channels") or min(data.shape))
        if data.shape[0] != n_ch and data.shape[1] == n_ch:
            # times-first; mmap transpose would copy — reject rather than hide it
            raise AdapterError(
                self.spec.name,
                f"{npy_path} looks times-first {tuple(data.shape)}; expected (n_channels, n_times)",
            )
        sfreq = float(meta.get("sfreq") or meta.get("expected_sampling_rate_hz") or 0.0)
        if sfreq <= 0:
            raise AdapterError(self.spec.name, f"{stamp} metadata is missing sfreq")
        ch_names = [f"EEG{i:02d}" for i in range(int(data.shape[0]))]
        problems = _integrity_problems(meta, events, termination)
        extra = {
            "dataset": self.spec.name,
            "device": meta.get("device_type"),
            "session_stamp": stamp.name,
            "source_path": str(npy_path),
            "marker_mode": meta.get("marker_mode"),
            "termination_reason": termination,
            "integrity_problems": problems,
            "task_mode": meta.get("task_mode"),
            "unit": self.unit,
        }
        subject = str(meta.get("subject_id", stamp.parent.name))
        if "video" in self.kinds:
            for i, (a, b, _, __) in enumerate(pair_events(events, "video_on", "video_off"), start=1):
                clip = np.asarray(data[:, a:b], dtype=np.float64)
                dur = (b - a) / sfreq
                yield RecordingInput(
                    data=clip,
                    sfreq=sfreq,
                    ch_names=ch_names,
                    unit=self.unit,
                    subject_id=subject,
                    session_id=stamp.name,
                    clip_id=f"{stamp.name}_video_{i:03d}",
                    expected_n_channels=n_ch,
                    stimulus_duration_s=dur,
                    event_ok=True,
                    sync_error_ms=None,
                    meta={**extra, "kind": "video", "video_index": i, "start_sample": a, "end_sample": b},
                )
        if "rest" in self.kinds:
            for i, (a, b, _, __) in enumerate(pair_events(events, "rest_start", "rest_end"), start=1):
                clip = np.asarray(data[:, a:b], dtype=np.float64)
                yield RecordingInput(
                    data=clip,
                    sfreq=sfreq,
                    ch_names=ch_names,
                    unit=self.unit,
                    subject_id=subject,
                    session_id=stamp.name,
                    clip_id=f"{stamp.name}_rest_{i:02d}",
                    expected_n_channels=n_ch,
                    event_ok=True,
                    sync_error_ms=None,
                    meta={**extra, "kind": "rest", "rest_index": i, "start_sample": a, "end_sample": b},
                )


def _integrity_problems(meta: dict, events: list, termination: str | None) -> list[str]:
    problems: list[str] = []
    if str(meta.get("marker_mode") or "").lower() == "noop":
        problems.append("marker_mode=noop")
    sent = sum(1 for e in events if (e.get("payload") or {}).get("external_marker_sent"))
    if events and sent == 0:
        problems.append("no external hardware markers sent")
    if meta.get("completed") is False:
        problems.append(f"recording completed=false ({termination or 'unknown'})")
    return problems
