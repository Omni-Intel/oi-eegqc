"""Machine-readable contract for CLI --json/--ndjson and the stdio sidecar.

Two version strings are kept distinct on purpose:

* ``REPORT_SCHEMA_VERSION`` is frozen into every ``QualityReport.to_dict()``.
  Persist this with the recording; bump it when report fields change.
* ``PROTOCOL_SCHEMA_VERSION`` wraps CLI and sidecar envelopes (ok/event/kind).
  Electron should reject unknown protocol versions rather than guess.

Stdout in machine mode is JSON only. Human text belongs on stderr.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Mapping, TextIO

from .types import REPORT_SCHEMA_VERSION

PROTOCOL_SCHEMA_VERSION = "oi-eegqc-protocol-v1"

__all__ = [
    "PROTOCOL_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "ProtocolError",
    "dumps",
    "envelope",
    "map_exception",
    "write_json",
    "write_ndjson",
]


class ProtocolError(Exception):
    """Structured failure the frontend can switch on via ``code``."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        status: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status = status

    def to_dict(self, request_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "schema_version": PROTOCOL_SCHEMA_VERSION,
            "event": "error",
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
        if request_id is not None:
            payload["id"] = request_id
        return payload


def envelope(
    kind: str,
    *,
    ok: bool = True,
    request_id: str | None = None,
    event: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "kind": kind,
    }
    if event:
        payload["event"] = event
    if request_id is not None:
        payload["id"] = request_id
    payload.update(fields)
    return payload


def dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_json_default)


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return str(value)


def write_json(payload: Mapping[str, Any], stream: TextIO | None = None) -> None:
    out = stream or sys.stdout
    out.write(dumps(payload) + "\n")
    out.flush()


def write_ndjson(payload: Mapping[str, Any], stream: TextIO | None = None) -> None:
    write_json(payload, stream)


def map_exception(exc: BaseException) -> ProtocolError:
    """Collapse loader/adapter exceptions into a stable error code."""
    if isinstance(exc, ProtocolError):
        return exc
    from .datasets.base import AdapterError
    from .io.edf import MneRequiredError

    if isinstance(exc, MneRequiredError):
        return ProtocolError("mne_required", str(exc))
    if isinstance(exc, AdapterError):
        return ProtocolError("adapter_error", str(exc), details={"adapter": exc.name})
    if isinstance(exc, FileNotFoundError):
        return ProtocolError("file_not_found", str(exc))
    if isinstance(exc, KeyError):
        return ProtocolError("unknown_dataset", str(exc).strip("'\""))
    if isinstance(exc, ValueError):
        text = str(exc)
        code = "unknown_unit" if "Unknown unit" in text or "unit=" in text else "invalid_request"
        return ProtocolError(code, text)
    return ProtocolError("eval_failed", f"{type(exc).__name__}: {exc}")
