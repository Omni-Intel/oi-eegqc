from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from ..io.edf import find_bdf, infer_unit, load_edf_bdf
from ..io.segment import DEFAULT_CLIP_SECONDS, centered_clips
from ..types import RecordingInput
from .base import AdapterError, DatasetAdapter, DatasetSpec

TD10_ADC_TO_UV = 0.036
OK_STATUSES = {"completed", "complete", "ok"}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _wall_clock_s(session: dict[str, Any]) -> float | None:
    start, end = session.get("started_at"), session.get("ended_at")
    if not start or not end:
        return None
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except ValueError:
        return None


def session_event_ok(session: dict[str, Any]) -> tuple[bool, list[str]]:
    """Marker / timeline integrity as reported by the acquisition software."""
    problems: list[str] = []
    status = session.get("status")
    if status not in OK_STATUSES:
        problems.append(f"session status={status}")
    if session.get("stop_reason") not in (None, "completed", "complete"):
        problems.append(f"stop_reason={session.get('stop_reason')}")
    if session.get("error"):
        problems.append(f"acquisition error: {session['error']}")
    if not int(session.get("events_written") or 0):
        problems.append("no event markers written")
    if int(session.get("nonfinite_samples") or 0):
        problems.append(f"{session['nonfinite_samples']} non-finite samples")
    return (not problems), problems


def session_sync_error_ms(session: dict[str, Any]) -> float | None:
    """Hardware-vs-LSL offset, or None when it was never calibrated."""
    sync = session.get("synchronization_summary") or {}
    med = (sync.get("hardware_lsl_delta_ms") or {}).get("median")
    if med is None:
        return None
    try:
        return float(med)
    except (TypeError, ValueError):
        return None


class HuaweiSessionAdapter(DatasetAdapter):
    """Neuracle / TD10 session folders: ``session.json`` + ``eeg.bdf``.

    Integrity metadata is passed through as recorded. An acquisition error
    (for example a non-monotonic TD10 timeline) keeps ``event_ok=False`` even
    when the waveforms themselves look clean.
    """

    spec = DatasetSpec(
        name="hw",
        kind="session",
        description="Huawei Neuracle/TD10 session directories (session.json + BDF)",
        requires_mne=True,
        default_unit="V",
    )

    def __init__(
        self,
        root: str | Path,
        *,
        clip_seconds: tuple[float, ...] = DEFAULT_CLIP_SECONDS,
        adc_to_uv: float = TD10_ADC_TO_UV,
    ) -> None:
        self.root = Path(root)
        self.clip_seconds = clip_seconds
        self.adc_to_uv = adc_to_uv

    def iter_sessions(self) -> Iterator[Path]:
        if not self.root.is_dir():
            raise FileNotFoundError(f"session root does not exist: {self.root}")
        found = False
        for path in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if find_bdf(path) is not None:
                found = True
                yield path
        if not found:
            raise FileNotFoundError(f"No session directories with a BDF under {self.root}")

    def iter_recordings(self) -> Iterator[RecordingInput]:
        for session_dir in self.iter_sessions():
            yield from self._session_clips(session_dir)

    def _session_clips(self, session_dir: Path) -> Iterator[RecordingInput]:
        bdf = find_bdf(session_dir)
        if bdf is None:
            raise AdapterError(self.spec.name, f"no readable BDF in {session_dir}")
        session_path = session_dir / "session.json"
        session = _load_json(session_path) if session_path.exists() else {}

        units = session.get("channel_units") or []
        unit, inferred_adc = infer_unit(units)
        adc_to_uv = self.adc_to_uv if unit == "adc" else inferred_adc

        rec = load_edf_bdf(bdf, unit=unit, adc_to_uv=adc_to_uv)
        meta_names = session.get("channel_names") or session.get("bdf_channel_labels")
        if meta_names and len(meta_names) == rec.data.shape[0]:
            rec.ch_names = [str(x) for x in meta_names]

        expected = len(meta_names) if meta_names else rec.data.shape[0]
        event_ok, problems = session_event_ok(session)
        device = "neuracle" if "neuracle" in str(session.get("source_type", "")).lower() else "td10"

        extra = {
            "dataset": self.spec.name,
            "session_dir": session_dir.name,
            "bdf": bdf.name,
            "source_type": session.get("source_type"),
            "paradigm": session.get("paradigm"),
            "status": session.get("status"),
            "stop_reason": session.get("stop_reason"),
            "error": session.get("error"),
            "valid_samples": session.get("valid_samples"),
            "events_written": session.get("events_written"),
            "nonfinite_samples": session.get("nonfinite_samples"),
            "clipped_samples_reported": session.get("clipped_samples"),
            "timing_status": session.get("timing_status"),
            "device": device,
            "integrity_problems": problems,
            "unit": unit,
            "adc_to_uv": adc_to_uv,
        }
        wall = _wall_clock_s(session)
        valid = session.get("valid_samples")
        if wall and valid:
            sample_s = float(valid) / rec.sfreq
            extra["wall_clock_s"] = round(wall, 2)
            extra["sample_duration_s"] = round(sample_s, 2)
            extra["duration_mismatch_pct"] = round(100.0 * abs(sample_s - wall) / wall, 2)

        clips = centered_clips(
            rec.data,
            rec.sfreq,
            targets_s=self.clip_seconds,
            valid_samples=session.get("valid_samples"),
        )
        for clip_name, clip in clips:
            yield RecordingInput(
                data=clip,
                sfreq=rec.sfreq,
                ch_names=rec.ch_names,
                unit=unit,
                adc_to_uv=adc_to_uv,
                subject_id=str(session.get("participant_id", "hw")),
                session_id=session_dir.name,
                clip_id=f"{session_dir.name}_{clip_name}",
                expected_n_channels=expected,
                event_ok=event_ok,
                sync_error_ms=session_sync_error_ms(session),
                meta={**extra, "clip": clip_name},
            )
