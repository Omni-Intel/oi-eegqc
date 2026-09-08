"""Desktop imports the shared intake entry so the GUI does not grow a second scorer."""
from .intake import (
    EDF_SUFFIXES,
    NPY_REQUIRED,
    inspect_edf_header,
    inspect_file,
    merge_npy_kwargs,
    npy_metadata,
    npy_ready,
    score_file,
)

__all__ = [
    "EDF_SUFFIXES",
    "NPY_REQUIRED",
    "inspect_edf_header",
    "inspect_file",
    "merge_npy_kwargs",
    "npy_metadata",
    "npy_ready",
    "score_file",
]
