"""OI-EEGQC: adaptive EEG quality validation bench."""

from importlib import import_module

# Preserve the public API without loading scientific libraries before a window
# can be displayed. Values are cached on first access.
_EXPORTS = {
    "config": ("BenchConfig", "dump_default_config", "load_config"),
    "datasets": ("list_datasets", "open_dataset", "score_adapter"),
    "io": ("load_edf_bdf", "load_npy"),
    "pipeline": ("evaluate_batch", "evaluate_recording", "load_npy_recording"),
    "intake": ("score_file",),
    "protocol": ("PROTOCOL_SCHEMA_VERSION", "ProtocolError", "envelope"),
    "types": ("REPORT_SCHEMA_VERSION", "AvailabilityFlag", "LetterGrade", "QualityReport", "RecordingInput"),
}
_MODULES = {name: module for module, names in _EXPORTS.items() for name in names}


def __getattr__(name):
    module = _MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))

__version__ = "0.3.3"
__all__ = [
    "AvailabilityFlag",
    "BenchConfig",
    "LetterGrade",
    "PROTOCOL_SCHEMA_VERSION",
    "ProtocolError",
    "QualityReport",
    "REPORT_SCHEMA_VERSION",
    "RecordingInput",
    "__version__",
    "dump_default_config",
    "envelope",
    "evaluate_batch",
    "evaluate_recording",
    "list_datasets",
    "load_config",
    "load_edf_bdf",
    "load_npy",
    "load_npy_recording",
    "open_dataset",
    "score_adapter",
    "score_file",
]
