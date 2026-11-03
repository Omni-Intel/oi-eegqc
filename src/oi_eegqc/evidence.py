"""Self-describing evidence attached to every QC result.

A QC verdict is only reproducible if the report states what was measured, on which file,
by which build. This module collects that context so a result can be audited without the
platform: the file hash, the EDF header facts, the window-level usable time, and how far the
claimed segment boundary sits from the nearest marker in the recording itself.
"""
import hashlib
from pathlib import Path

EVIDENCE_SCHEMA = 'oi-eegqc-evidence-v1'


def file_identity(path, block=4 * 1024 * 1024):
    """sha256 plus byte length of the file that was scored."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as handle:
        while True:
            chunk = handle.read(block)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return {'sha256': digest.hexdigest(), 'bytes': size}


def recording_identity(raw):
    """Header facts measured from the recording itself, never from the plan."""
    info = raw.info
    measured = float(getattr(raw, 'n_times', 0)) / float(info['sfreq'])
    identity = {
        'schema': EVIDENCE_SCHEMA,
        'sfreq': float(info['sfreq']),
        'n_channels': len(info['ch_names']),
        'n_times': int(getattr(raw, 'n_times', 0)),
        'measured_duration_s': round(measured, 3),
        'channel_names': list(info['ch_names'])[:64],
    }
    measurement = getattr(info, 'meas_date', None)
    if measurement is not None:
        identity['meas_date'] = measurement.isoformat()
    try:
        extras = raw._raw_extras[0] if getattr(raw, '_raw_extras', None) else {}
    except Exception:
        extras = {}
    for source, key in (('equipment', 'equipment'), ('device_serial', 'device_serial'),
                        ('recording_id', 'recording_id'), ('technician', 'technician')):
        value = extras.get(source) if isinstance(extras, dict) else None
        if value:
            identity[key] = str(value)[:120]
    annotations = getattr(raw, 'annotations', None)
    source = getattr(annotations, 'onset', None) if annotations is not None else None
    onsets = [float(item) for item in source] if source is not None and len(source) else []
    if onsets:
        identity['n_annotations'] = len(onsets)
        identity['first_annotation_s'] = round(min(onsets), 3)
        identity['last_annotation_s'] = round(max(onsets), 3)
    else:
        identity['n_annotations'] = 0
    return identity


def boundary_check(raw, start, tolerance_s=1.0):
    """How close the claimed segment start is to a marker actually present in the EDF."""
    annotations = getattr(raw, 'annotations', None)
    source = getattr(annotations, 'onset', None) if annotations is not None else None
    onsets = [float(item) for item in source] if source is not None and len(source) else []
    if not onsets:
        return {'checked': False, 'reason': 'no annotations in recording'}
    distance = min(abs(onset - start) for onset in onsets)
    return {'checked': True, 'nearest_offset_s': round(distance, 3),
            'tolerance_s': tolerance_s, 'within_tolerance': distance <= tolerance_s}


def segment_evidence(report, start, stop):
    """Window-level usable time for one scored segment, measured from the analysis."""
    duration = max(0.0, float(stop) - float(start))
    window_qa = getattr(report, 'window_qa', None)
    usable_ratio = float(getattr(report, 'usable_ratio', 0.0) or 0.0)
    evidence = {
        'schema': EVIDENCE_SCHEMA,
        'basis': 'measured_segment_window',
        'duration_s': round(duration, 3),
        'usable_duration_s': round(duration * usable_ratio, 3),
        'usable_ratio': round(usable_ratio, 4),
        'duration_profile': getattr(report, 'duration_profile', None),
        'letter_grade': getattr(getattr(report, 'letter_grade', None), 'value', None),
        'odq': round(float(getattr(report, 'odq', 0.0) or 0.0), 2),
    }
    if window_qa is not None:
        evidence['windows'] = {
            'total': int(getattr(window_qa, 'n_windows', 0) or 0),
            'usable': int(getattr(window_qa, 'usable_windows', 0) or 0),
        }
    return evidence
