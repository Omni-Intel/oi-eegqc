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
    for r in batch['imports']:r['participant_number']='13467638'
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
    grade_sessions([row],lambda:False,progress.append)
    assert row['recorded_duration_s']==100 and row['usable_duration_s']==80
    assert progress and '1/1' in progress[0]


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
    parent=QObject();parent.telemetry=None
    store=Mock();store.paradigm={'code':'RSVP-20260924'};store.prepare.return_value={'entries':[]}
    row={'paradigm_code':'RSVP-20260924','session_directory':str(tmp_path)}
    monkeypatch.setattr(archive_import,'read_sessions',lambda root:(_ for _ in ()).throw(AssertionError('metadata read twice')))
    monkeypatch.setattr(archive_import,'grade_sessions',lambda *args:None)
    worker=UploadWorker(store,tmp_path,roots=[tmp_path],parent=parent,sessions=[row])
    progress=[];worker.progress.connect(progress.append);worker.run()
    assert worker.error==''
    assert [p['stage'] for p in progress if 'stage' in p]==['identifying','scoring','scanning']
