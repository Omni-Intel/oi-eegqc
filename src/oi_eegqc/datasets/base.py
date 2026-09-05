from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

from ..types import RecordingInput


@dataclass(frozen=True)
class DatasetSpec:
    """Public description of a registered dataset adapter."""

    name: str
    kind: str
    description: str
    unit_is_nominal: bool = False
    requires_mne: bool = False
    default_unit: str = "uV"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DatasetAdapter(ABC):
    """Turn one on-disk layout into a stream of :class:`RecordingInput`.

    Adapters never score. They only declare units, expected channel counts and
    which integrity fields were actually measured, so GQI does not award points
    for metadata the source never provided.
    """

    spec: DatasetSpec

    @abstractmethod
    def iter_recordings(self) -> Iterator[RecordingInput]:
        raise NotImplementedError

    def recordings(self) -> list[RecordingInput]:
        return list(self.iter_recordings())

    def estimate_count(self) -> int | None:
        """Best-effort clip count for progress bars. ``None`` means unknown."""
        return None


@dataclass
class AdapterError(Exception):
    name: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.name}: {self.message}"
