"""Identify external acquisitions from session manifests, BIDS and array metadata."""
import csv
import json
import re
from pathlib import Path

FORMATS = {'.edf', '.edf+', '.bdf', '.npy', '.float32'}
TASK_CODES = {'things': 'RSVP-20260924', 'rsvp': 'RSVP-20260924', 'av': 'AVEEG-20260923'}


def recording_duration(path):
    from .intake import inspect_file
    try:
        if path.suffix.lower() in {'.edf', '.edf+', '.bdf'}:
            with path.open('rb') as stream:
                header = stream.read(256)
            records, seconds = int(header[236:244]), float(header[244:252])
            return records * seconds if records > 0 and seconds > 0 else None
        if path.suffix == '.float32':
            meta = read_json(path.with_suffix('.float32.json'))
            return meta['shape'][0] / meta['sampling_rate_hz']
        if path.suffix == '.npy':
            import numpy as np
            meta = inspect_file(path)
            if not meta.get('sfreq'):
                return None
            from .io.array import orient_channels_first
            raw = np.load(path, mmap_mode='r')
            return orient_channels_first(raw, len(meta['channel_names']) if meta.get('channel_names') else None,
                                         channels_first=meta.get('channels_first')).shape[1] / meta['sfreq']
    except (ValueError, OSError, KeyError, ZeroDivisionError):
        return None
    return None


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}


def entity(path, name):
    match = re.search(r'(?:^|[/\\_])' + name + r'-([A-Za-z0-9]+)(?=[/\\_.]|$)', str(path))
    return match[1] if match else None


def primary_recordings(paths):
    """Prefer a lossless acquisition over its same-stem EDF export."""
    paths = sorted(paths)
    lossless = {p.with_suffix('') for p in paths if p.suffix == '.float32' and p.with_suffix('.float32.json').is_file()}
    return [p for p in paths if not (p.suffix.lower() in {'.edf', '.edf+'} and p.with_suffix('') in lossless)]


def scan_time(directory, recording):
    for scans in directory.glob('*_scans.tsv'):
        with scans.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream, delimiter='\t'):
                if row.get('filename') == recording.relative_to(directory).as_posix():
                    value = row.get('acq_time')
                    return value if value and value != 'n/a' else None
    return None


def discover_sessions(root):
    root = Path(root)
    rows, handled = [], set()
    manifests = sorted(root.rglob('session.json'))
    manifests += [p for p in sorted(root.rglob('metadata.json')) if not (p.parent/'session.json').exists()]
    for manifest in manifests:
        data = read_json(manifest)
        participant = str(data.get('participant_id') or data.get('participant_number') or data.get('subject_id') or '')
        match = re.fullmatch(r'(?:sub-)?(\d{8})', participant)
        if not match:
            continue
        directory = manifest.parent
        named = entity(directory, 'sub')
        if named and named != match[1]:
            raise ValueError('目录与采集元数据中的被试编号不一致')
        eeg = directory/'eeg' if (directory/'eeg').is_dir() else directory
        recordings = primary_recordings([p for p in eeg.iterdir() if p.is_file() and p.suffix.lower() in FORMATS])
        if not recordings:
            continue
        sid = str(data.get('session_id') or entity(directory, 'ses') or directory.name)
        row = session_row(directory, recordings, match[1], sid, data)
        rows.append(row)
        handled.update(p.resolve() for p in recordings)
    # Standard BIDS datasets need no company session.json. Group all runs of a Session.
    eeg_dirs = sorted({p.parent for p in root.rglob('*_eeg.*') if p.suffix.lower() in FORMATS})
    for eeg in eeg_dirs:
        recordings = primary_recordings([p for p in eeg.iterdir() if p.suffix.lower() in FORMATS and p.is_file()])
        recordings = [p for p in recordings if p.resolve() not in handled]
        if not recordings:
            continue
        participant = entity(eeg, 'sub')
        if not participant or not re.fullmatch(r'\d{8}', participant):
            continue
        directory = eeg.parent
        sid = 'bids-sub-' + participant + '-ses-' + (entity(directory, 'ses') or '01')
        rows.append(session_row(directory, recordings, participant, sid, {}))
    return rows


def session_row(directory, recordings, participant, sid, data):
    codes = {str(data['paradigm_code'])} if data.get('paradigm_code') else set()
    details = []
    for raw in recordings:
        from .bids_metadata import eeg_metadata
        metadata = read_json(raw.with_suffix('.float32.json')) if raw.suffix == '.float32' else eeg_metadata(raw)
        if metadata.get('paradigm_code'):
            codes.add(str(metadata['paradigm_code']))
        elif not data.get('paradigm_code'):
            code = TASK_CODES.get(entity(raw.name, 'task'))
            if code:
                codes.add(code)
        duration = None
        if raw.suffix == '.float32':
            duration = metadata['shape'][0] / metadata['sampling_rate_hz']
        details.append(dict(recording=str(raw), recorded_duration_s=duration,
                            started_at=metadata.get('started_at') or scan_time(directory, raw)))
    if len(codes) > 1:
        raise ValueError('同一 Session 包含不同范式，请按范式分别导出采集目录')
    row = dict(participant_number=participant, source_session_id=sid,
               paradigm_code=next(iter(codes), None),
               started_at=data.get('started_at'), ended_at=data.get('ended_at'),
               recorded_duration_s=None, recording=str(recordings[0]), session_directory=str(directory),
               presentation=read_json(directory/'qc_summary.json'), final_score=None, usable_duration_s=None)
    if len(details) == 1:
        row['recorded_duration_s'] = details[0]['recorded_duration_s']
        row['started_at'] = row['started_at'] or details[0]['started_at']
    else:
        row['recordings'] = details
    row['presentation'].update(recording_count=len(details),
                               recording_status=data.get('recording_status'), stop_reason=data.get('stop_reason'))
    return row
