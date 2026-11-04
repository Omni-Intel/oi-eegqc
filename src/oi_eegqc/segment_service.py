"""Versioned, headless segment scoring API for independent acquisition apps."""
import json
import math
from pathlib import Path
import uuid

SCHEMA='oi-eegqc-segments-v1'
# Two thresholds, no intermediate states: a clip is usable when most of its windows are,
# and it is void (record it again) when more than half of them are not.
PASS_RATIO=0.6
VOID_RATIO=0.5


def score_request(request):
    from .io.edf import require_mne
    from .pipeline import evaluate_recording, prepare_eeg
    from .config import load_config
    from .types import RecordingInput
    from .scoring_version import SCORING_ALGORITHM_VERSION
    from .evidence import boundary_check, file_identity, recording_identity, segment_evidence
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
        cfg=load_config()
        from .bids_metadata import channel_rows, eeg_metadata
        from .layouts import get_layout, layout_for_device

        sidecar=eeg_metadata(path)
        auxiliary_names=[]
        for channel in channel_rows(path):
            if channel.get('type','').strip().upper() not in ('EEG',''):
                auxiliary_names.append(channel['name'])
        layout_id=sidecar.get('channel_layout')
        if not layout_id:
            device=sidecar.get('Manufacturer') or sidecar.get('DeviceType')
            layout=layout_for_device(device,len(raw.ch_names))
            layout_id=layout.id if layout else None
        meta={}
        if layout_id:
            meta['channel_layout']=str(layout_id)
            layout=get_layout(str(layout_id))
            auxiliary_names.extend(channel.name for channel in layout.channels
                                   if channel.kind.lower()!='eeg')
        parent=RecordingInput(data=raw.get_data(),sfreq=fs,ch_names=list(raw.ch_names),
            unit='V',aux_ch_names=auxiliary_names,meta=meta)
        prepared=prepare_eeg(parent,cfg)
        identity=recording_identity(raw);identity.update(file_identity(path))
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
                recording=RecordingInput(data=parent.data[:,first:last],sfreq=fs,
                    ch_names=list(raw.ch_names),unit='V',clip_id=segment['trial_id'],
                    subject_id=request.get('participant'),session_id=request.get('session_id'),
                    stimulus_duration_s=segment.get('stimulus_duration_s'),
                    aux_ch_names=parent.aux_ch_names,meta=parent.meta)
                report=evaluate_recording(recording,cfg,prepared=prepared.crop(first,last))
                ratio=float(getattr(report,'usable_ratio',0.0) or 0.0)
                clip_duration=round(stop-start,3)
                # One verdict per segment: usable or not. Nothing in between, so the client,
                # the platform and the participant all see the same number.
                void=clip_duration<=0 or ratio<VOID_RATIO
                usable=(not void) and ratio>=PASS_RATIO
                evidence=segment_evidence(report,start,stop)
                evidence['boundary']=boundary_check(raw,start)
                evidence['void']=void
                row.update(score=round(ratio*100,2),state='usable' if usable else 'unusable',
                    onset=start,offset=stop,sample_start=first,sample_stop=last,
                    duration_s=clip_duration,usable=usable,void=void,
                    usable_duration_s=0.0 if void else round(clip_duration*ratio,3),
                    evidence=evidence,report=report.to_dict())
            except Exception as exc:
                # A segment that cannot be scored is unusable, never "pending": the upload
                # must not stall waiting for a second opinion.
                row.update(state='unusable',usable=False,void=True,score=0.0,
                    usable_duration_s=0.0,reason=str(exc))
            rows.append(row)
    stat=path.stat()
    if fingerprint!=(stat.st_size,stat.st_mtime_ns):raise ValueError('评分期间脑电文件发生变化')
    scored=[r for r in rows if r.get('score') is not None]
    scores=[r['score'] for r in scored]
    usable_rows=[r for r in rows if r.get('usable')]
    return {'schema':SCHEMA,'request_id':request['request_id'],'session_id':request['session_id'],
        'platform_session_id':request.get('platform_session_id'),
        'algorithm_version':SCORING_ALGORITHM_VERSION,'threshold':threshold,'segments':rows,
        'recording':{'size':fingerprint[0],'mtime_ns':fingerprint[1]},'recording_identity':identity,
        'timing_status':request.get('timing_status','unknown'),
        'rules':{'pass_ratio':PASS_RATIO,'void_ratio':VOID_RATIO},
        'summary':{'usable':len(usable_rows),'unusable':len(rows)-len(usable_rows),
            'void':sum(1 for r in rows if r.get('void')),
            'minimum':min(scores) if scores else 0.0,'mean':sum(scores)/len(scores) if scores else 0.0,
            'usable_duration_s':round(sum(r.get('usable_duration_s') or 0.0 for r in rows),3),
            'scored_duration_s':round(sum(r.get('duration_s') or 0.0 for r in scored),3)}}


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
