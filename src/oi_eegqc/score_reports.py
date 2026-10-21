"""Recording-side scoring reports shared by import, manual scoring and upload."""
import json
from pathlib import Path
from .local_files import atomic_json
from .scoring_version import SCORING_ALGORITHM_VERSION


def report_path(path):
    path = Path(path)
    return path.with_name(path.stem + '.eegqc-report.json')


def read_report(path, metadata, source_stat=None):
    path = Path(path)
    stat = path.stat()
    identity = source_stat or (stat.st_size, stat.st_mtime_ns)
    source = [*identity, metadata.get('line_hz', 50), SCORING_ALGORITHM_VERSION]
    def load(candidate):
        if not candidate.is_file():return None
        try:
            saved=json.loads(candidate.read_text(encoding='utf-8'))
        except (ValueError,OSError):return None
        if saved.get('source')!=source:return None
        parameters=saved.get('parameters')
        if parameters is not None and parameters!=metadata:return None
        if parameters is None and metadata.get('keep_channels'):return None
        return saved.get('report')

    report=load(report_path(path))
    if report is not None:return report
    candidates=[path.parent/'eegqc-report.json']
    if path.parent.name=='eeg':candidates.append(path.parent.parent/'eegqc-report.json')
    candidates=[p for p in candidates if p.is_file()]
    if candidates:
        from .recording_manifest import FORMATS,primary_recordings
        if len(primary_recordings([p for p in path.parent.iterdir() if p.suffix.lower() in FORMATS and p.is_file()]))==1:
            for candidate in candidates:
                report=load(candidate)
                if report is not None:return report
    if metadata.get('line_hz', 50) == 50 and not metadata.get('keep_channels'):
        resolved = path.resolve()
        for parent in (path.parent, *list(path.parents)[:3]):
            fq = parent / 'file-quality.json'
            if not fq.is_file():
                continue
            try:
                saved = json.loads(fq.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                continue
            if saved.get('quality_state') != 'scored' or not isinstance(saved.get('report'), dict):
                continue
            rec = saved.get('recording')
            if not rec:
                continue
            try:
                if (parent / rec).resolve() != resolved:
                    continue
                fq_mtime = fq.stat().st_mtime_ns
            except OSError:
                continue
            rep = saved['report']
            if rep.get('extras', {}).get('algorithm_version', SCORING_ALGORITHM_VERSION) != SCORING_ALGORITHM_VERSION:
                continue
            if saved.get('source') == source or ('source' not in saved and identity[1] <= fq_mtime):
                return rep
    return None


def write_report(path, metadata, report, source_stat):
    stat = Path(path).stat()
    if source_stat != (stat.st_size, stat.st_mtime_ns):
        return
    source = [*source_stat, metadata.get('line_hz', 50), SCORING_ALGORITHM_VERSION]
    atomic_json(report_path(path), dict(source=source, parameters=metadata, report=report))
