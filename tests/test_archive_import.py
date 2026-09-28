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
