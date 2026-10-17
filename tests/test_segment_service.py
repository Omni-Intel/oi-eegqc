import numpy as np
import pytest
from oi_eegqc.segment_service import score_request,SCHEMA


def test_one_bad_video_does_not_reject_the_good_video(tmp_path):
    from test_desktop import write_edf
    path=tmp_path/'signal.edf';fs=250
    t=np.arange(2500)/fs
    rng=np.random.default_rng(7)
    data=np.array([20*np.sin(2*np.pi*10*t+i*.08)+rng.normal(0,.8,t.size) for i in range(8)])
    data[:,1250:]=0
    write_edf(path, fs, data)
    request={'schema':SCHEMA,'request_id':'r1','session_id':'s1','recording':str(path),'segments':[
        dict(trial_id='good',stimulus_id='a',onset=0,offset=5),
        dict(trial_id='bad',stimulus_id='b',onset=5,offset=10),
        dict(trial_id='invalid',stimulus_id='c',onset=9,offset=12),
        dict(trial_id='short',stimulus_id='d',onset=1,offset=1.1)]}
    result=score_request(request)
    good,bad,invalid,short=result['segments']
    assert good['score']>=60 and good['state']=='passed'
    assert bad['score']<60 and bad['state']=='retry'
    assert invalid['state']==short['state']=='needs_review'
    assert good['sample_stop']==bad['sample_start']==1250
    assert result['summary']['retry']==1


def test_eog_io_marked_eeg_in_bids_is_not_scored_as_eeg(tmp_path):
    from test_desktop import write_edf

    path=tmp_path/'sub-01_task-test_eeg.edf';fs=250
    t=np.arange(2500)/fs
    rng=np.random.default_rng(9)
    names=[f'EEG {name}' for name in ('P8','P7','T8','T7','F8','F7','O2','O1')]+['EOG IO']
    data=np.array([20*np.sin(2*np.pi*10*t+i*.08)+rng.normal(0,.8,t.size) for i in range(8)])
    eog=np.full((1,t.size),4000.0)
    write_edf(path,fs,np.vstack([data,eog]),ch_names=names)
    path.with_name('sub-01_task-test_channels.tsv').write_text(
        'name\ttype\tunits\n'+''.join(f'{name}\tEEG\tuV\n' for name in names),
        encoding='utf-8',
    )
    request={'schema':SCHEMA,'request_id':'r-eog','session_id':'s-eog','recording':str(path),
        'segments':[dict(trial_id='trial-1',stimulus_id='test',onset=0,offset=5)]}
    result=score_request(request)
    report=result['segments'][0]['report']
    assert report['n_channels_used']==8
    assert report['extras']['dropped_channels']==['EOG IO']


def test_duplicate_trial_ids_rejected_before_reading():
    with pytest.raises(ValueError,match='unique'):
        score_request({'schema':SCHEMA,'segments':[{'trial_id':'x'},{'trial_id':'x'}]})
