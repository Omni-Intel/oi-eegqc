from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..types import RecordingInput
from .array import split_recording_kwargs

BDF_CANDIDATES = ("eeg.bdf", "eeg.inprogress.bdf")


class MneRequiredError(ImportError):
    """Raised when an EDF/BDF loader is used without the optional ``mne`` extra."""


def require_mne():
    try:
        import mne
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise MneRequiredError(
            "Reading EDF/BDF requires the optional extra: pip install 'oi-eegqc[mne]'"
        ) from exc
    return mne


def infer_unit(channel_units: list[Any] | None) -> tuple[str, float | None]:
    """Map vendor / BIDS unit strings onto the RecordingInput unit contract.

    MNE rescales a microvolt EDF/BDF physical dimension into SI volts, so a
    session that declares ``uV`` still needs ``unit='V'`` after ``raw.get_data()``.
    ADC-count streams are left unscaled and need an explicit conversion factor.
    """
    if not channel_units:
        return "V", None
    labels = [str(u).strip().lower() for u in channel_units]
    if all("adc" in u or "count" in u for u in labels):
        return "adc", None
    if all(u in {"v", "volt", "volts"} for u in labels):
        return "V", None
    if all(u in {"uv", "µv", "microvolt", "microvolts"} for u in labels):
        return "V", None
    if all(u in {"mv", "millivolt", "millivolts"} for u in labels):
        return "V", None
    return "V", None


def find_bdf(session_dir: str | Path) -> Path | None:
    root = Path(session_dir)
    for name in BDF_CANDIDATES:
        candidate = root / name
        if candidate.exists() and candidate.stat().st_size > 10_000:
            return candidate
    matches = sorted(root.glob("*.bdf"))
    for candidate in matches:
        if candidate.stat().st_size > 10_000:
            return candidate
    return None


def load_edf_bdf(
    path: str | Path,
    *,
    unit: str | None = None,
    adc_to_uv: float | None = None,
    eeg_only: bool = True,
    **meta,
) -> RecordingInput:
    """Load one EDF or BDF file as a :class:`RecordingInput`.

    Default ``unit`` is SI volts, matching ``mne.io.read_raw_*.get_data()``.
    Override with ``unit='adc'`` and ``adc_to_uv`` for unscaled headset counts.
    """
    mne = require_mne()
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".bdf":
        raw = mne.io.read_raw_bdf(path, preload=True, verbose="ERROR")
    elif suffix in {".edf", ".edf+"}:
        raw = mne.io.read_raw_edf(path, preload=True, verbose="ERROR")
    else:
        raise ValueError(f"Unsupported EEG file suffix {suffix!r} (expected .edf or .bdf)")

    picks = mne.pick_types(raw.info, eeg=True, exclude="bads") if eeg_only else np.arange(len(raw.ch_names))
    if eeg_only and len(picks) == 0:
        data = raw.get_data()
        ch_names = list(raw.ch_names)
    else:
        data = raw.get_data(picks=picks)
        ch_names = [raw.ch_names[i] for i in picks]

    fields, extras = split_recording_kwargs(meta)
    fields.pop("adc_to_uv", None)
    fields.setdefault("clip_id", path.stem)
    fields.setdefault("expected_n_channels", data.shape[0])
    extras.setdefault("source_path", str(path))
    return RecordingInput(
        data=np.asarray(data, dtype=np.float64),
        sfreq=float(raw.info["sfreq"]),
        ch_names=ch_names,
        unit=unit or "V",
        adc_to_uv=adc_to_uv,
        meta=extras,
        **fields,
    )
