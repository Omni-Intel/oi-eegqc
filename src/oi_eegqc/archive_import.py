"""Expand acquisition ZIPs and read their participant and Session metadata."""
import json
from pathlib import Path
import re
import sqlite3
import uuid
from zipfile import ZipFile

RSVP = 'RSVP-20260924'


def read_sessions(root):
    rows = []
    for path in Path(root).rglob('session.json'):
        session = json.loads(path.read_text(encoding='utf-8-sig'))
        participant = re.fullmatch(r'(?:sub-)?(\d{8})', str(session.get('participant_id', '')))
        if not participant:
            continue
        named = re.search(r'sub-(\d{8})', str(path.parent))
        if named and named[1] != participant[1]:
            raise ValueError('目录与 Session 内的被试编号不一致')
        eeg = path.parent / 'eeg'
        recordings = sorted(eeg.glob('*.float32')) or sorted(eeg.glob('*.edf'))
        if not recordings:
            continue
        raw = recordings[0]
        metadata = json.loads(raw.with_suffix('.float32.json').read_text(encoding='utf-8-sig')) if raw.suffix == '.float32' else {}
        quality_path = path.parent/'qc_summary.json'
        quality = json.loads(quality_path.read_text(encoding='utf-8-sig')) if quality_path.exists() else {}
        rows.append(dict(participant_number=participant[1], source_session_id=session['session_id'],
                         paradigm_code=RSVP if '_task-things_' in raw.name else None,
                         started_at=session.get('started_at'), ended_at=session.get('ended_at'),
                         recorded_duration_s=metadata['shape'][0]/metadata['sampling_rate_hz'] if metadata else None,
                         recording=str(raw), session_directory=str(path.parent),
                         presentation=quality, final_score=None, usable_duration_s=None))
    return rows


def expand_inputs(paths, cache, cancelled=lambda: False, progress=lambda *args: None):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    expanded = []
    with sqlite3.connect(cache/'archives.sqlite') as db:
        db.execute('CREATE TABLE IF NOT EXISTS archives(source TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,target TEXT,complete INTEGER)')
        inputs = []
        for value in paths:
            path = Path(value).resolve()
            if path.is_dir():
                archives = sorted(path.rglob('*.zip'))
                if archives:
                    inputs.extend(archives)
                    if any(p.suffix.lower() in ('.edf', '.bdf', '.float32', '.npy') for p in path.rglob('*') if p.is_file()):
                        inputs.append(path)
                else:
                    inputs.append(path)
            else:
                inputs.append(path)
        for path in inputs:
            if cancelled():
                raise InterruptedError('已取消解压')
            if path.suffix.lower() != '.zip':
                expanded.append(str(path))
                continue
            stat = path.stat()
            saved = db.execute('SELECT size,mtime,target,complete FROM archives WHERE source=?', (str(path),)).fetchone()
            current = saved and saved[:2] == (stat.st_size,stat.st_mtime_ns)
            target = Path(saved[2]) if current else cache/(path.stem+'-'+uuid.uuid4().hex[:8])
            if not (current and saved[3] and target.is_dir()):
                target.mkdir(parents=True, exist_ok=True)
                db.execute('INSERT OR REPLACE INTO archives VALUES (?,?,?,?,0)',(str(path),stat.st_size,stat.st_mtime_ns,str(target)))
                db.commit()
                with ZipFile(path) as archive:
                    members = archive.infolist()
                    for member in members:
                        if not (target/member.filename).resolve().is_relative_to(target.resolve()):
                            raise ValueError('压缩包包含目录外路径')
                    total = sum(m.file_size for m in members)
                    done = 0
                    for member in members:
                        output = target/member.filename
                        if member.is_dir():
                            output.mkdir(parents=True,exist_ok=True)
                            continue
                        output.parent.mkdir(parents=True,exist_ok=True)
                        with archive.open(member) as source, output.open('wb') as dest:
                            while chunk := source.read(1024*1024):
                                if cancelled():
                                    raise InterruptedError('已取消解压')
                                dest.write(chunk)
                                done += len(chunk)
                                progress(path.name, done, total)
                db.execute('UPDATE archives SET complete=1 WHERE source=?',(str(path),))
                db.commit()
            children = list(target.iterdir())
            expanded.extend(str(p) for p in children if p.is_dir())
            if any(p.is_file() for p in children):
                expanded.append(str(target))
    return list(dict.fromkeys(expanded))


def preferred_recordings(paths):
    values = {str(Path(p).with_suffix('')) for p in paths if Path(p).suffix == '.float32' and Path(p).with_suffix('.float32.json').is_file()}
    return [p for p in paths if not (Path(p).suffix.lower() in ('.edf','.edf+') and str(Path(p).with_suffix('')) in values)]


def grade_sessions(rows, cancelled, progress, line_hz=50):
    from .application.scoring_process import ScoringProcess, DEFAULT_FILE_TIMEOUT_S
    from .local_files import atomic_json
    from .intake import inspect_file
    from .desktop_upload import UploadCancelled
    from .scoring_version import SCORING_ALGORITHM_VERSION
    client = ScoringProcess()
    try:
        for row in rows:
            if cancelled():
                raise UploadCancelled()
            path = Path(row['recording'])
            stat = path.stat()
            row['score_stat'] = (stat.st_size, stat.st_mtime_ns)
            identity = [stat.st_size, stat.st_mtime_ns, line_hz, SCORING_ALGORITHM_VERSION]
            sidecar = Path(row['session_directory'])/'eegqc-report.json'
            saved = json.loads(sidecar.read_text(encoding='utf-8')) if sidecar.exists() else {}
            report = saved.get('report') if saved.get('source') == identity else None
            if report is None:
                try:
                    kind, payload = client.score(str(path), dict(inspect_file(path), line_hz=line_hz), cancelled,
                                                 lambda phase: progress(f"评分 {row['source_session_id']} · {phase}"), DEFAULT_FILE_TIMEOUT_S)
                except (ValueError, OSError) as error:
                    kind, payload = 'error', str(error)
                if kind == 'cancelled':
                    raise UploadCancelled()
                if kind == 'result':
                    report = payload
                    atomic_json(sidecar, dict(source=identity, report=report))
                else:
                    row['grading_error'] = str(payload or kind)
            row['signal_qc'] = report
            if report is not None:
                row['final_score'] = float(report['gqi'])
                row['usable_duration_s'] = (row['recorded_duration_s'] * float(report['usable_ratio'])
                                             if row['final_score'] >= 60 else 0.)
    finally:
        client.close()


def attach_sessions(batch, rows):
    for part in batch.get('children') or [batch]:
        imports = []
        for row in rows:
            directory = Path(row['session_directory'])
            members = [e for e in part['entries'] if directory in Path(e['source']).parents]
            if not members:
                continue
            entry = members[0]
            suffix = Path(entry['source']).relative_to(directory).as_posix()
            relative = entry['relative'][:-len(suffix)].rstrip('/')
            data = {k:v for k,v in row.items() if k not in ('recording','session_directory','grading_error','score_stat')}
            data.update(directory=relative, paradigm_code=part['paradigm_code'])
            old = next((r for r in part.get('imports',[]) if {k:v for k,v in r.items() if k!='receipt'} == data), None)
            if old and old.get('receipt'):
                data['receipt'] = old['receipt']
            imports.append(data)
        part['imports'] = imports
        part['database_status'] = ('complete' if all(r.get('receipt') for r in imports) else 'pending') if imports else 'not_required'


def sync_database(store, batch, client, progress, cancelled=lambda:False):
    for part in batch.get('children') or [batch]:
        if part.get('status') != 'completed':
            continue
        for row in part.get('imports', []):
            if cancelled():
                return
            if row.get('receipt'):
                continue
            progress(f"正在入库 {row['participant_number']} · {row['source_session_id']}")
            payload = {k:v for k,v in row.items() if k!='receipt'}
            payload.update(upload_id=part['upload_id'], round_id=part['round_id'])
            row['receipt'] = client.commit_import(payload)
            store.save(batch)
        part['database_status'] = 'complete' if part.get('imports') else 'not_required'
        store.save(batch)


def database_pending(batch):
    return any(p.get('database_status')=='pending' for p in ((batch or {}).get('children') or [batch or {}]))
