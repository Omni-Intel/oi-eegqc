"""Desktop imports the shared intake entry so the GUI does not grow a second scorer."""
from .intake import (
    EDF_SUFFIXES,
    NPY_REQUIRED,
    inspect_channel_names,
    inspect_edf_header,
    inspect_file,
    merge_npy_kwargs,
    npy_metadata,
    npy_ready,
    score_file,
)


def time_weighted_usable(reports):
    """Usable-window time over recorded time. Longer sessions weigh more."""
    total = usable = 0.0
    for report in reports:
        duration = float(getattr(report, "duration_s", 0) or 0)
        ratio = getattr(report, "usable_ratio", None)
        if duration <= 0 or ratio is None:
            continue
        total += duration
        usable += duration * float(ratio)
    if total <= 0:
        return None
    return usable / total


__all__ = [
    "EDF_SUFFIXES",
    "NPY_REQUIRED",
    "inspect_channel_names",
    "inspect_edf_header",
    "inspect_file",
    "merge_npy_kwargs",
    "npy_metadata",
    "npy_ready",
    "score_file",
    "time_weighted_usable",
]
