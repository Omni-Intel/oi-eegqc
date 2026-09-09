"""Named channel layouts for known acquisition SDKs.

Scoring stays generic: it only sees ``ch_names``. A layout is an optional name
overlay, applied when the file or session declares it, or when a device alias
matches both the vendor tag and the channel count.

Add a new SDK by dropping another ``*.yaml`` next to this package. Do not put
vendor row order into ``evaluate_recording``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

LAYOUT_SCHEMA = "oi-eegqc-layout-v1"
_FALLBACK_PREFIX = "EEG"


@dataclass(frozen=True)
class ChannelSpec:
    index: int
    name: str
    edf_label: str | None = None
    kind: str = "eeg"


@dataclass(frozen=True)
class ChannelLayout:
    id: str
    sdk: str
    sdk_version: str
    n_channels: int
    device_aliases: tuple[str, ...]
    channels: tuple[ChannelSpec, ...]
    source: str = ""
    notes: str = ""

    @property
    def names(self) -> list[str]:
        return [ch.name for ch in self.channels]


def _layout_dir() -> Path:
    return Path(__file__).resolve().parent


def _parse_layout(path: Path, raw: Mapping[str, Any]) -> ChannelLayout:
    schema = str(raw.get("layout_schema") or "")
    if schema != LAYOUT_SCHEMA:
        raise ValueError(f"{path.name}: unsupported layout_schema {schema!r}")
    layout_id = str(raw["id"])
    if path.stem != layout_id:
        raise ValueError(f"{path.name}: file stem must match id {layout_id!r}")
    rows = list(raw.get("channels") or [])
    n_channels = int(raw["n_channels"])
    if len(rows) != n_channels:
        raise ValueError(f"{layout_id}: expected {n_channels} channels, got {len(rows)}")
    channels = []
    seen: set[int] = set()
    for row in rows:
        index = int(row["index"])
        if index in seen or not (0 <= index < n_channels):
            raise ValueError(f"{layout_id}: bad channel index {index}")
        seen.add(index)
        channels.append(
            ChannelSpec(
                index=index,
                name=str(row["name"]),
                edf_label=str(row["edf_label"]) if row.get("edf_label") else None,
                kind=str(row.get("kind") or "eeg"),
            )
        )
    channels.sort(key=lambda ch: ch.index)
    if [ch.index for ch in channels] != list(range(n_channels)):
        raise ValueError(f"{layout_id}: channel indices must be 0..{n_channels - 1}")
    aliases = tuple(str(x).strip().lower() for x in (raw.get("device_aliases") or ()) if str(x).strip())
    return ChannelLayout(
        id=layout_id,
        sdk=str(raw.get("sdk") or ""),
        sdk_version=str(raw.get("sdk_version") or ""),
        n_channels=n_channels,
        device_aliases=aliases,
        channels=tuple(channels),
        source=str(raw.get("source") or "").strip(),
        notes=str(raw.get("notes") or "").strip(),
    )


@lru_cache(maxsize=1)
def load_layouts() -> dict[str, ChannelLayout]:
    layouts: dict[str, ChannelLayout] = {}
    for path in sorted(_layout_dir().glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: layout file must be a mapping")
        layout = _parse_layout(path, raw)
        layouts[layout.id] = layout
    return layouts


def get_layout(layout_id: str) -> ChannelLayout:
    layouts = load_layouts()
    if layout_id not in layouts:
        known = ", ".join(sorted(layouts)) or "(none)"
        raise ValueError(f"unknown channel_layout {layout_id!r}; known: {known}")
    return layouts[layout_id]


def layout_for_device(device_type: str | None, n_channels: int) -> ChannelLayout | None:
    if not device_type:
        return None
    alias = str(device_type).strip().lower()
    if not alias:
        return None
    matches = [
        layout
        for layout in load_layouts().values()
        if alias in layout.device_aliases and layout.n_channels == n_channels
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def fallback_names(n_channels: int) -> list[str]:
    return [f"{_FALLBACK_PREFIX}{i:02d}" for i in range(int(n_channels))]


def resolve_channel_names(
    n_channels: int,
    *,
    names: list[str] | None = None,
    layout_id: str | None = None,
    device_type: str | None = None,
) -> tuple[list[str], str | None]:
    """Pick channel names without guessing from array length.

    Precedence: explicit ``names`` > ``layout_id`` > device alias + matching
    count > positional fallback (``EEG00`` …). A declared layout whose count
    does not match ``n_channels`` is an error. A device alias on the wrong
    montage count is ignored so other headsets from the same vendor stay generic.
    """
    n_channels = int(n_channels)
    if names is not None:
        if len(names) != n_channels:
            raise ValueError(
                f"channel_names length {len(names)} does not match {n_channels} channels"
            )
        return [str(x) for x in names], None
    if layout_id:
        layout = get_layout(str(layout_id).strip())
        if layout.n_channels != n_channels:
            raise ValueError(
                f"channel_layout {layout.id} has {layout.n_channels} channels, "
                f"recording has {n_channels}"
            )
        return layout.names, layout.id
    matched = layout_for_device(device_type, n_channels)
    if matched is not None:
        return matched.names, matched.id
    return fallback_names(n_channels), None
