import io
import json
from pathlib import Path

import pytest

from oi_eegqc.archive_import import read_sessions, grade_sessions, attach_sessions


def make_session(tmp_path, code='AVEEG-20260923', manifest=True):
    directory = tmp_path/'sub-00000001'/'ses-abc123'
    (directory/'eeg').mkdir(parents=True)
    if manifest:
        (directory/'session.json').write_text(json.dumps(dict(participant_id='00000001', session_id='abc123',
             paradigm_code=code, started_at='2026-09-30T09:00:00+08:00', ended_at='2026-09-30T09:01:00+08:00')))
    return directory


def test_explicit_paradigm_and_all_runs_are_identified(tmp_path):
    directory = make_session(tmp_path, code='CUSTOM-20260930')
    for run in (1,2):
        (directory/'eeg'/f'sub-00000001_ses-abc123_task-custom_run-{run}_eeg.edf').write_bytes(b'raw')
    row = read_sessions(tmp_path)[0]
    assert row['paradigm_code'] == 'CUSTOM-20260930'
    assert row['participant_number'] == '00000001'
    assert len(row['recordings']) == 2


def test_bids_without_manifest_uses_scans_time_and_stable_identity(tmp_path):
    directory = make_session(tmp_path, manifest=False)
    name='sub-00000001_ses-abc123_task-things_run-01_eeg.bdf'
    (directory/'eeg'/name).write_bytes(b'raw')
    (directory/'sub-00000001_ses-abc123_scans.tsv').write_text('filename\tacq_time\neeg/'+name+'\t2026-09-30T09:00:00+08:00\n')
    row=read_sessions(tmp_path)[0]
    assert row['source_session_id']=='bids-sub-00000001-ses-abc123'
    assert row['started_at']=='2026-09-30T09:00:00+08:00'
    assert row['paradigm_code']=='RSVP-20260924'


def test_multi_run_grade_is_weighted_and_payload_has_no_local_paths(tmp_path,monkeypatch):
    from oi_eegqc.application.scoring_process import ScoringProcess
    directory=make_session(tmp_path)
    for run in (1,2):
        (directory/'eeg'/f'run{run}.edf').write_bytes(b'raw')
    calls=[]
    def score(self,path,*args):
        calls.append(path)
        return 'result',dict(gqi=90 if 'run1' in path else 50, usable_ratio=.8, duration_s=10 if 'run1' in path else 30)
    monkeypatch.setattr(ScoringProcess,'score',score)
    row=read_sessions(tmp_path)[0]
    grade_sessions([row],lambda:False,lambda message:None)
    assert len(calls)==2
    assert row['recorded_duration_s']==40
    assert row['final_score']==60
    assert row['usable_duration_s']==8
    batch=dict(paradigm_code='AVEEG-20260923',entries=[dict(source=r['recording'],relative='package/eeg/'+Path(r['recording']).name) for r in row['recordings']])
    attach_sessions(batch,[row])
    payload=batch['imports'][0]
    assert len(batch['imports'])==1 and 'recordings' not in payload
    assert payload['signal_qc']['recording_count']==2
    assert str(tmp_path) not in json.dumps(payload)
    grade_sessions([row],lambda:False,lambda message:None)
    assert len(calls)==2


def test_npy_bids_inherits_sampling_and_channel_names(tmp_path):
    from oi_eegqc.intake import npy_metadata
    directory=make_session(tmp_path)
    (tmp_path/'dataset_description.json').write_text('{}')
    (tmp_path/'task-av_eeg.json').write_text(json.dumps(dict(SamplingFrequency=250, channels_first=False)))
    path=directory/'eeg'/'sub-00000001_ses-abc123_task-av_eeg.npy'
    path.with_name(path.stem[:-4]+'_channels.tsv').write_text('name\ttype\tunits\nFz\tEEG\tuV\nCz\tEEG\tuV\n')
    params=npy_metadata(path)
    assert params['sfreq']==250 and params['unit']=='uV'
    assert params['channel_names']==['Fz','Cz'] and params['channels_first'] is False


def test_mixed_paradigms_in_same_session_do_not_silently_mislabel(tmp_path):
    directory=make_session(tmp_path,manifest=False)
    for task in ('av','things'):
        (directory/'eeg'/f'sub-00000001_task-{task}_eeg.edf').write_bytes(b'raw')
    with pytest.raises(ValueError,match='不同范式'):
        read_sessions(tmp_path)


def test_company_update_falls_back_to_legacy_on_bad_manifest():
    from oi_eegqc.desktop_update import check_update,INTRANET_RELEASE_URL,LEGACY_CHANNEL,ZIP_ASSET
    seen=[]
    def opener(request,**kwargs):
        seen.append(request.full_url)
        data={} if len(seen)==1 else dict(tag_name='v0.6.15',assets=[dict(name=ZIP_ASSET,browser_download_url=LEGACY_CHANNEL+'eegqc-0.6.15.zip')])
        return io.BytesIO(json.dumps(data).encode())
    assert check_update('0.6.14',opener=opener).status=='available'
    assert seen==[INTRANET_RELEASE_URL,LEGACY_CHANNEL+'eegqc-stable.json']


def test_both_company_asset_paths_remain_downloadable():
    from oi_eegqc.desktop_update import UpdateInfo,downloadable,INTRANET_CHANNEL,LEGACY_CHANNEL,ZIP_ASSET
    for channel in (INTRANET_CHANNEL,LEGACY_CHANNEL):
        info=UpdateInfo('available','0.6.14','0.6.15',asset_url=channel+'eegqc-0.6.15.zip',asset_name=ZIP_ASSET,digest='sha256:'+'a'*64,size=1024)
        assert downloadable(info)
