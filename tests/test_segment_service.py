import numpy as np
import pytest
from oi_eegqc.segment_service import score_request,SCHEMA


def test_one_bad_video_does_not_reject_the_good_video(tmp_path):
    import pyedflib
    path=tmp_path/'signal.edf';fs=250
    t=np.arange(2500)/fs
    rng=np.random.default_rng(7)
    data=np.array([20*np.sin(2*np.pi*10*t+i*.08)+rng.normal(0,.8,t.size) for i in range(8)])
    data[:,1250:]=0
    with pyedflib.EdfWriter(str(path),8,file_type=pyedflib.FILETYPE_EDFPLUS) as writer:
        writer.setSignalHeaders([dict(label=f'C{i}',dimension='uV',sample_frequency=fs,physical_min=-1000,physical_max=1000,digital_min=-32768,digital_max=32767) for i in range(8)])
        writer.writeSamples(data)
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


def test_duplicate_trial_ids_rejected_before_reading():
    with pytest.raises(ValueError,match='unique'):
        score_request({'schema':SCHEMA,'segments':[{'trial_id':'x'},{'trial_id':'x'}]})
