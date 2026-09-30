"""Expand acquisition ZIPs and read their participant and Session metadata."""
import json
from pathlib import Path
import re
import sqlite3
import uuid
from zipfile import ZipFile

RSVP = 'RSVP-20260924'


def read_sessions(root):
    from .recording_manifest import discover_sessions
    return discover_sessions(root)


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
        for index,row in enumerate(rows,1):
            if cancelled():
                raise UploadCancelled()
            if row.get('recordings'):
                grade_recordings(row, cancelled, progress, line_hz)
                continue
            path = Path(row['recording'])
            stat = path.stat()
            existing_report = row.get('signal_qc') if row.get('score_stat') == (stat.st_size, stat.st_mtime_ns) else None
            if existing_report and existing_report.get('extras', {}).get('line_hz', line_hz) != line_hz:
                existing_report = None
            row['score_stat'] = (stat.st_size, stat.st_mtime_ns)
            identity = [stat.st_size, stat.st_mtime_ns, line_hz, SCORING_ALGORITHM_VERSION]
            sidecar = Path(row.get('report_path') or Path(row['session_directory'])/'eegqc-report.json')
            progress(f"评分 {index}/{len(rows)} · {row['source_session_id']}")
            try:saved = json.loads(sidecar.read_text(encoding='utf-8')) if sidecar.exists() else {}
            except (ValueError,OSError):saved={}
            report = existing_report or (saved.get('report') if saved.get('source') == identity else None)
            if report is None:
                try:
                    kind, payload = client.score(str(path), dict(inspect_file(path), line_hz=line_hz), cancelled,
                                                 lambda phase: progress(f"评分 {index}/{len(rows)} · {phase}"), DEFAULT_FILE_TIMEOUT_S)
                except (ValueError, OSError) as error:
                    kind, payload = 'error', str(error)
                if kind == 'cancelled':
                    raise UploadCancelled()
                if kind == 'result':
                    report = payload
                    try:atomic_json(sidecar, dict(source=identity, report=report))
                    except OSError as error:row['grading_error']=str(error)
                else:
                    row['grading_error'] = str(payload or kind)
            elif existing_report is not None and (saved.get('source') != identity or saved.get('report') != report):
                try:atomic_json(sidecar, dict(source=identity, report=report))
                except OSError as error:row['grading_error']=str(error)
            row['signal_qc'] = report
            row['final_score'] = row['usable_duration_s'] = None
            if report is not None:
                if row['recorded_duration_s'] is None:row['recorded_duration_s']=report['duration_s']
                row['final_score'] = float(report['gqi'])
                row['usable_duration_s'] = (row['recorded_duration_s'] * float(report['usable_ratio'])
                                             if row['final_score'] >= 60 else 0.)
            complete_recording_metadata(row)
    finally:
        client.close()


def complete_recording_metadata(row):
    """Recover real duration even when scoring fails; never infer wall time from mtime."""
    from datetime import datetime, timedelta
    if row.get('recorded_duration_s') is None:
        from .recording_manifest import recording_duration
        row['recorded_duration_s'] = recording_duration(Path(row['recording']))
    if row.get('started_at') and not row.get('ended_at') and row.get('recorded_duration_s'):
        try:
            start = datetime.fromisoformat(row['started_at'].replace('Z', '+00:00'))
        except ValueError:
            return
        if start.tzinfo is not None:
            row['ended_at'] = (start + timedelta(seconds=row['recorded_duration_s'])).isoformat()


def grade_recordings(row, cancelled, progress, line_hz):
    """Score every run and aggregate duration once for the source Session."""
    from .local_files import atomic_json
    from .scoring_version import SCORING_ALGORITHM_VERSION
    from datetime import datetime
    children = row['recordings']
    for child in children:
        child.update(session_directory=row['session_directory'], source_session_id=row['source_session_id'],
                     report_path=str(Path(child['recording']).with_name(Path(child['recording']).stem+'.eegqc-report.json')))
    grade_sessions(children, cancelled, progress, line_hz)
    durations = [r.get('recorded_duration_s') for r in children]
    total = sum(durations) if all(v is not None for v in durations) else None
    scored = all(r.get('final_score') is not None for r in children)
    row['recorded_duration_s'] = total
    row['final_score'] = sum(r['final_score']*r['recorded_duration_s'] for r in children)/total if scored and total else None
    row['usable_duration_s'] = sum(r['usable_duration_s'] for r in children) if scored and total else None
    # A Session span may include gaps. Only actual samples contribute to recorded time.
    if not row.get('started_at') and all(r.get('started_at') for r in children):
        row['started_at'] = min((r['started_at'] for r in children), key=lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')))
    if not row.get('ended_at') and all(r.get('ended_at') for r in children):
        row['ended_at'] = max((r['ended_at'] for r in children), key=lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')))
    row['signal_qc'] = dict(aggregation='duration_weighted_runs', scoring_algorithm_version=SCORING_ALGORITHM_VERSION,
                            recording_count=len(children), duration_s=total, gqi=row['final_score'],
                            usable_duration_s=row['usable_duration_s'],
                            recordings=[dict(file=Path(r['recording']).relative_to(row['session_directory']).as_posix(),
                                             duration_s=r.get('recorded_duration_s'), final_score=r.get('final_score'),
                                             usable_duration_s=r.get('usable_duration_s'),
                                             report=score_summary(r.get('signal_qc')), error=r.get('grading_error')) for r in children])
    try:
        atomic_json(Path(row['session_directory'])/'eegqc-session-report.json', row['signal_qc'])
    except OSError as error:
        row['grading_error'] = str(error)


def score_summary(report):
    """Keep aggregate results in the queue; detailed evidence stays in the report file."""
    if report is None:
        return None
    result = dict(report)
    if 'window_qa' in result:
        result['window_qa'] = {k:v for k,v in result['window_qa'].items() if k != 'window_evidence'}
    if 'extras' in result:
        result['extras'] = dict(result['extras'])
        if 'operator' in result['extras']:
            result['extras']['operator'] = {k:v for k,v in result['extras']['operator'].items() if k != 'timeline'}
    return result


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
            data = {k:v for k,v in row.items() if k in ('participant_number','source_session_id','paradigm_code','started_at','ended_at','recorded_duration_s','presentation','final_score','usable_duration_s')}
            data['signal_qc'] = score_summary(row.get('signal_qc'))
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
            from datetime import datetime
            for field in ('started_at', 'ended_at'):
                try:
                    stamp = datetime.fromisoformat(str(row.get(field) or '').replace('Z', '+00:00'))
                    if stamp.tzinfo is None:
                        raise ValueError()
                except ValueError:
                    raise ValueError(f'采集元数据缺少带时区的 {field}，请补齐后重新导入') from None
            if not row.get('recorded_duration_s') or row['recorded_duration_s'] <= 0:
                raise ValueError('无法确定实际采集时长，请核对原始文件后重新导入')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', row['source_session_id']):
                raise ValueError('采集编号需为 1–128 位字母、数字、下划线或连字符')
            progress(f"正在入库 {row['participant_number']} · {row['source_session_id']}")
            payload = {k:v for k,v in row.items() if k!='receipt'}
            if 'signal_qc' in payload:
                payload['signal_qc'] = score_summary(payload['signal_qc'])
            payload.update(upload_id=part['upload_id'], round_id=part['round_id'])
            row['receipt'] = client.commit_import(payload)
            store.save(batch)
        part['database_status'] = 'complete' if part.get('imports') else 'not_required'
        store.save(batch)


def database_pending(batch):
    return any(p.get('database_status')=='pending' for p in ((batch or {}).get('children') or [batch or {}]))
