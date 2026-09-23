"""Versioned, headless segment scoring API for independent acquisition apps."""
import json
import math
from pathlib import Path
import uuid

SCHEMA='oi-eegqc-segments-v1'


def score_request(request):
    from .io.edf import require_mne
    from .pipeline import evaluate_recording
    from .types import RecordingInput
    from .scoring_version import SCORING_ALGORITHM_VERSION
    if request.get('schema')!=SCHEMA:raise ValueError('Unsupported segment request schema')
    threshold=float(request.get('threshold',60))
    if not math.isfinite(threshold) or not 0<=threshold<=100:raise ValueError('Invalid threshold')
    segments=request['segments']
    ids=[s['trial_id'] for s in segments]
    if not ids or len(ids)!=len(set(ids)):raise ValueError('Segment IDs must be nonempty and unique')
    path=Path(request['recording'])
    stat=path.stat();fingerprint=(stat.st_size,stat.st_mtime_ns)
    mne=require_mne()
    reader=mne.io.read_raw_bdf if path.suffix.lower()=='.bdf' else mne.io.read_raw_edf
    rows=[]
    with reader(path,preload=False,verbose='ERROR') as raw:
        fs=float(raw.info['sfreq']);duration=raw.n_times/fs
        for segment in segments:
            row={'trial_id':segment['trial_id'],'stimulus_id':segment['stimulus_id'],'score':None,'state':'needs_review'}
            try:
                start=float(segment['onset']);stop=float(segment['offset'])
                if not segment.get('event_valid',True):raise ValueError('片段事件标记不完整，需检查时间边界')
                if not all(map(math.isfinite,(start,stop))) or start<0 or stop>duration or stop<=start:
                    raise ValueError('片段时间超出脑电文件范围')
                first=math.ceil(start*fs);last=math.ceil(stop*fs)
                if last-first<max(16,math.ceil(fs)):raise ValueError('片段不足 1 秒，无法可靠评分')
                unit_labels=getattr(raw,'_orig_units',{})
                if any(str(u).lower() not in ('µv','μv','uv','mv','v') for u in unit_labels.values()):
                    raise ValueError('脑电物理单位未确认，无法按电压评分')
                recording=RecordingInput(data=raw.get_data(start=first,stop=last),sfreq=fs,
                    ch_names=list(raw.ch_names),unit='V',clip_id=segment['trial_id'],
                    subject_id=request.get('participant'),session_id=request.get('session_id'),
                    stimulus_duration_s=segment.get('stimulus_duration_s'))
                report=evaluate_recording(recording)
                score=float(report.gqi)
                if not math.isfinite(score):raise ValueError('评分结果无效')
                row.update(score=score,state='retry' if score<threshold else 'passed',
                    onset=start,offset=stop,sample_start=first,sample_stop=last,report=report.to_dict())
            except Exception as exc:row['reason']=str(exc)
            rows.append(row)
    stat=path.stat()
    if fingerprint!=(stat.st_size,stat.st_mtime_ns):raise ValueError('评分期间脑电文件发生变化')
    scores=[r['score'] for r in rows if r['score'] is not None]
    return {'schema':SCHEMA,'request_id':request['request_id'],'session_id':request['session_id'],
        'platform_session_id':request.get('platform_session_id'),
        'algorithm_version':SCORING_ALGORITHM_VERSION,'threshold':threshold,'segments':rows,
        'recording':{'size':fingerprint[0],'mtime_ns':fingerprint[1]},
        'timing_status':request.get('timing_status','unknown'),
        'summary':{'passed':sum(r['state']=='passed' for r in rows),'retry':sum(r['state']=='retry' for r in rows),
            'needs_review':sum(r['state']=='needs_review' for r in rows),
            'minimum':min(scores) if scores else None,'mean':sum(scores)/len(scores) if scores else None}}


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--quality-request',required=True);parser.add_argument('--quality-output',required=True)
    args=parser.parse_args(argv)
    output=Path(args.quality_output)
    request=json.loads(Path(args.quality_request).read_text(encoding='utf-8'))
    try:result=score_request(request);code=0
    except Exception as exc:
        result={'schema':SCHEMA,'request_id':request.get('request_id'),'error':str(exc)};code=1
    temp=output.with_name(output.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');temp.replace(output)
    return code


if __name__=='__main__':raise SystemExit(main())
