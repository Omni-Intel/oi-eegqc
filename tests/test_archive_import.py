import json
from pathlib import Path
from zipfile import ZipFile

from oi_eegqc.archive_import import expand_inputs, read_sessions, preferred_recordings, attach_sessions, sync_database, database_pending


def test_archive_reuses_extraction_and_prefers_lossless(tmp_path):
    path=tmp_path/'sub-13467638_ses-one.zip'
    base='sub-13467638/ses-one/'
    with ZipFile(path,'w') as z:
        z.writestr(base+'session.json',json.dumps(dict(participant_id='sub-13467638',session_id='ses-one')))
        z.writestr(base+'eeg/sub-13467638_task-things_eeg.float32',b'1234')
        z.writestr(base+'eeg/sub-13467638_task-things_eeg.float32.json',json.dumps(dict(shape=[1,1],sampling_rate_hz=1)))
        z.writestr(base+'eeg/sub-13467638_task-things_eeg.edf',b'1234')
    roots=expand_inputs([path],tmp_path/'cache')
    assert expand_inputs([path],tmp_path/'cache',progress=lambda *a: (_ for _ in ()).throw(AssertionError('extracted twice')))==roots
    row=read_sessions(roots[0])[0]
    assert row['participant_number']=='13467638' and row['paradigm_code']=='RSVP-20260924'
    assert row['recorded_duration_s']==1 and row['usable_duration_s'] is None
    files=list((Path(row['session_directory'])/'eeg').iterdir())
    assert len(preferred_recordings([p for p in files if p.suffix in ('.edf','.float32')]))==1


def test_partial_database_failure_preserves_receipts_and_retries_only_pending():
    from oi_eegqc.upload_http import ServiceError
    import pytest
    batch=dict(status='completed',upload_id='u',round_id='r',database_status='pending',imports=[{'source_session_id':'one'},{'source_session_id':'two'}])
    class Store:
        def save(self,b): pass
    class Client:
        seen=[]
        fail=True
        def commit_import(self,p):
            self.seen.append(p['source_session_id'])
            if self.fail and p['source_session_id']=='two':raise ServiceError(503)
            return {'session_id':p['source_session_id']}
    client=Client()
    for r in batch['imports']:r.update(participant_number='13467638',started_at='2026-09-30T09:00:00+08:00',ended_at='2026-09-30T09:01:00+08:00',recorded_duration_s=60)
    with pytest.raises(ServiceError):sync_database(Store(),batch,client,lambda *a:None)
    assert batch['status']=='completed' and database_pending(batch)
    client.fail=False
    sync_database(Store(),batch,client,lambda *a:None)
    assert client.seen==['one','two','two'] and not database_pending(batch)


def test_cached_edf_score_supplies_duration_and_reports_progress(tmp_path):
    from oi_eegqc.archive_import import grade_sessions
    from oi_eegqc.scoring_version import SCORING_ALGORITHM_VERSION
    path=tmp_path/'eeg.edf';path.write_bytes(b'raw')
    stat=path.stat()
    (tmp_path/'eegqc-report.json').write_text(json.dumps({'source':[stat.st_size,stat.st_mtime_ns,50,SCORING_ALGORITHM_VERSION],
        'report':{'gqi':90,'usable_ratio':.8,'duration_s':100}}))
    row={'recording':str(path),'session_directory':str(tmp_path),'source_session_id':'one','recorded_duration_s':None}
    progress=[]
    counts = grade_sessions([row],lambda:False,progress.append)
    assert counts == dict(scored=0, reused=1, failed=0)
    assert row['recorded_duration_s']==100 and row['usable_duration_s']==80
    assert progress[0].startswith('复用评分 1/1')


def test_main_score_is_reused_only_for_unchanged_recording(tmp_path, monkeypatch):
    from oi_eegqc.archive_import import grade_sessions
    from oi_eegqc.application.scoring_process import ScoringProcess
    path = tmp_path/'eeg.edf'; path.write_bytes(b'raw')
    stat = path.stat()
    report = {'gqi':90, 'usable_ratio':.8, 'duration_s':100}
    row = dict(recording=str(path), session_directory=str(tmp_path), source_session_id='one',
               recorded_duration_s=None, signal_qc=report, score_stat=(stat.st_size, stat.st_mtime_ns))
    calls = []
    monkeypatch.setattr(ScoringProcess, 'score', lambda *args: (calls.append(True) or ('result', dict(report, gqi=80))))
    assert grade_sessions([row], lambda:False, lambda value:None) == dict(scored=0, reused=1, failed=0)
    assert calls == [] and row['final_score'] == 90
    assert json.loads((tmp_path/'eegqc-report.json').read_text())['report'] == report
    path.write_bytes(b'changed')
    assert grade_sessions([row], lambda:False, lambda value:None) == dict(scored=1, reused=0, failed=0)
    assert calls == [True] and row['final_score'] == 80


def test_upload_summary_keeps_scores_without_window_evidence():
    from oi_eegqc.archive_import import score_summary
    report = dict(gqi=90, usable_ratio=.8, duration_s=100,
                  window_qa=dict(usable_windows=20, window_evidence=[{'samples':[1]*10000}]),
                  extras=dict(operator=dict(headline='良好', timeline=[{'samples':[1]*10000}])))
    summary = score_summary(report)
    assert summary['gqi'] == 90 and summary['window_qa'] == {'usable_windows':20}
    assert summary['extras']['operator'] == {'headline':'良好'}
    assert len(json.dumps(summary)) < 300
    assert report['window_qa']['window_evidence'] and report['extras']['operator']['timeline']


def test_resume_compacts_saved_reports_in_background(tmp_path, monkeypatch):
    from PySide6.QtCore import QObject
    from unittest.mock import Mock
    from oi_eegqc.upload_ui import UploadWorker
    from oi_eegqc import archive_import
    parent = QObject(); parent.telemetry = None
    batch = dict(status='completed', imports=[dict(signal_qc=dict(gqi=90, window_qa=dict(window_evidence=[1]*10000)))])
    store = Mock(); store.load.return_value = batch
    monkeypatch.setattr(archive_import, 'sync_database', lambda *args:None)
    worker = UploadWorker(store, tmp_path, batch=batch, parent=parent)
    store.load.assert_not_called()
    worker.run()
    store.load.assert_called_once()
    assert worker.error == '' and worker.batch['imports'][0]['signal_qc'] == dict(gqi=90, window_qa={})


def test_scoring_timeout_terminates_worker_before_next_file():
    from unittest.mock import Mock
    from oi_eegqc.application.scoring_process import ScoringProcess
    client=ScoringProcess();process=Mock();process.is_alive.return_value=True
    client.process=process;client.connection=Mock()
    assert client.score('slow',{},lambda:False,lambda phase:None,0)[0]=='timeout'
    process.terminate.assert_called_once()
    assert client.process is None and client.connection is None


def test_upload_preparation_reuses_identified_sessions_and_exposes_stages(tmp_path,monkeypatch):
    from PySide6.QtCore import QObject
    from unittest.mock import Mock
    from oi_eegqc import archive_import
    from oi_eegqc.upload_ui import UploadWorker
    parent=QObject();parent.telemetry=Mock()
    store=Mock();store.paradigm={'code':'RSVP-20260924'};store.prepare.return_value={'entries':[]};store.unchanged_completed.return_value=None
    row={'paradigm_code':'RSVP-20260924','session_directory':str(tmp_path)}
    monkeypatch.setattr(archive_import,'read_sessions',lambda root:(_ for _ in ()).throw(AssertionError('metadata read twice')))
    monkeypatch.setattr(archive_import,'grade_sessions',lambda *args:dict(scored=0, reused=2, failed=0))
    worker=UploadWorker(store,tmp_path,roots=[tmp_path],parent=parent,sessions=[row])
    progress=[];worker.progress.connect(progress.append);worker.run()
    assert worker.error==''
    assert [p['stage'] for p in progress if 'stage' in p]==['identifying','checking_scores','scanning']
    parent.telemetry.report.assert_any_call('archive_scores_reused', count=2)
    parent.telemetry.report.assert_any_call('archive_scoring_completed', count=0)


def test_failed_main_score_is_not_repeated_during_raw_upload(tmp_path, monkeypatch):
    from oi_eegqc.archive_import import grade_sessions
    from oi_eegqc.application.scoring_process import ScoringProcess
    path=tmp_path/'eeg.edf';path.write_bytes(b'raw')
    stat=path.stat()
    row=dict(recording=str(path), session_directory=str(tmp_path), source_session_id='one',
             recorded_duration_s=None, grading_error='invalid signal', failed_stat=(stat.st_size,stat.st_mtime_ns))
    monkeypatch.setattr(ScoringProcess,'score',lambda *args:(_ for _ in ()).throw(AssertionError('repeated failed score')))
    assert grade_sessions([row],lambda:False,lambda message:None)==dict(scored=0,reused=0,failed=1)
    assert row['final_score'] is None and row['usable_duration_s'] is None


def test_dual_image_collection_and_incremental_archive_skip(tmp_path):
    from oi_eegqc.upload_http import ServiceError
    import pytest

    # 1. Dual-image RSVP folder with task-things filename resolves to DoRSVP-20261011
    dual_dir = tmp_path / 'dual' / 'ses-dual1'
    (dual_dir / 'eeg').mkdir(parents=True)
    (dual_dir / 'session.json').write_text(json.dumps(dict(participant_id='sub-13467638', session_id='ses-dual1')))
    (dual_dir / 'collection.json').write_text(json.dumps(dict(strategy='paired', paradigm='dual_image')))
    (dual_dir / 'eeg' / 'sub-13467638_ses-dual1_task-things_eeg.float32').write_bytes(b'1234')
    (dual_dir / 'eeg' / 'sub-13467638_ses-dual1_task-things_eeg.float32.json').write_text(
        json.dumps(dict(shape=[1, 1], sampling_rate_hz=1))
    )
    row = read_sessions(dual_dir)[0]
    assert row['paradigm_code'] == 'DoRSVP-20261011'

    # An empty failed export has no EEG to score. A committed session is still
    # scanned locally: the remote receipt alone cannot prove ZIP contents match.
    zips_dir = tmp_path / 'zips'
    zips_dir.mkdir()
    with ZipFile(zips_dir / 'empty_failed.zip', 'w') as z:
        z.writestr('sub-13467638/ses-empty/session.json', json.dumps(dict(participant_id='sub-13467638', session_id='ses-empty', status='integrity_failed')))
    with ZipFile(zips_dir / 'rehearsal.zip', 'w') as z:
        z.writestr('sub-00000001/ses-test/session.json', json.dumps(dict(participant_id='sub-00000001', session_id='ses-test')))
        z.writestr('sub-00000001/ses-test/eeg/sub-00000001_task-things_eeg.edf', b'1234')
    with ZipFile(zips_dir / 'already_committed.zip', 'w') as z:
        z.writestr('sub-13467638/ses-done/session.json', json.dumps(dict(participant_id='sub-13467638', session_id='ses-done')))
        z.writestr('sub-13467638/ses-done/eeg/sub-13467638_task-things_eeg.edf', b'1234')
    with ZipFile(zips_dir / 'new_pending.zip', 'w') as z:
        z.writestr('sub-13467638/ses-new/session.json', json.dumps(dict(participant_id='sub-13467638', session_id='ses-new')))
        z.writestr('sub-13467638/ses-new/eeg/sub-13467638_task-things_eeg.edf', b'1234')

    expanded = expand_inputs([zips_dir], tmp_path / 'cache')
    sessions = [s for r in expanded for s in read_sessions(r)]
    assert {s['source_session_id'] for s in sessions} == {'ses-done', 'ses-new'}
    assert expand_inputs([zips_dir], tmp_path / 'cache') == expanded

    # 3. sync_database commits subsequent sessions even if an earlier session raises 404
    batch = dict(
        status='completed',
        upload_id='u',
        round_id='r',
        database_status='pending',
        imports=[
            dict(participant_number='00000001', source_session_id='bad', started_at='2026-10-12T01:00:00+00:00', ended_at='2026-10-12T01:05:00+00:00', recorded_duration_s=300),
            dict(participant_number='13467638', source_session_id='good', started_at='2026-10-12T02:00:00+00:00', ended_at='2026-10-12T02:05:00+00:00', recorded_duration_s=300),
        ],
    )
    class Store:
        def save(self, b): pass
    class Client:
        def commit_import(self, p):
            if p['participant_number'] == '00000001':
                raise ServiceError(404)
            return {'session_id': p['source_session_id']}
    with pytest.raises(ServiceError):
        sync_database(Store(), batch, Client(), lambda *a: None)
    assert batch['imports'][1].get('receipt') == {'session_id': 'good'}


def test_changed_zip_at_new_path_does_not_reuse_extraction_by_size(tmp_path):
    first, second = tmp_path/'first', tmp_path/'second'
    first.mkdir(); second.mkdir()
    for parent, payload in ((first, b'aaaa'), (second, b'bbbb')):
        with ZipFile(parent/'same.zip', 'w') as z:
            z.writestr('sub-13467638/ses-one/session.json',
                       json.dumps({'participant_id': 'sub-13467638', 'session_id': 'ses-one'}))
            z.writestr('sub-13467638/ses-one/eeg/recording.edf', payload)
    assert (first/'same.zip').stat().st_size == (second/'same.zip').stat().st_size
    one = expand_inputs([first/'same.zip'], tmp_path/'cache')
    two = expand_inputs([second/'same.zip'], tmp_path/'cache')
    assert one != two
    assert (Path(one[0])/'ses-one/eeg/recording.edf').read_bytes() == b'aaaa'
    assert (Path(two[0])/'ses-one/eeg/recording.edf').read_bytes() == b'bbbb'


def test_invalid_session_metadata_does_not_block_other_imports():
    from pytest import raises
    rows = [
        dict(participant_number='13467638', source_session_id='broken',
             started_at=None, ended_at=None, recorded_duration_s=0),
        dict(participant_number='13467638', source_session_id='valid',
             started_at='2026-10-20T01:00:00+00:00',
             ended_at='2026-10-20T01:01:00+00:00', recorded_duration_s=60),
    ]
    batch = dict(status='completed', upload_id='u', round_id='r', imports=rows)

    class Store:
        def save(self, value): pass

    class Client:
        def commit_import(self, payload):
            return {'session_id': payload['source_session_id']}

    with raises(ValueError, match='started_at'):
        sync_database(Store(), batch, Client(), lambda _: None)
    assert batch['database_status'] == 'pending'
    assert rows[1]['receipt'] == {'session_id': 'valid'}
