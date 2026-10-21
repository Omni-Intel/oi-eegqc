import os
from pathlib import Path
from test_quick import quick,recording,wait
from oi_eegqc.record_link import RecordHub,record_id
from oi_eegqc.record_bridge import SharedRecordView,notify_existing


def test_handoff_score_remove_restore_and_keep_upload_history(quick,tmp_path):
    app,c,engine,window=quick
    root=tmp_path/'capture';root.mkdir();source=root/'signal.npy';recording(source)
    hub=RecordHub(tmp_path/'hub.sqlite');key=hub.publish('capture:1',root,'capture',{'participant':'P001'})
    bridge=SharedRecordView(c,window,hub);c.record_bridge=bridge;bridge.timer.stop()
    bridge.refresh();wait(app,lambda:bridge.worker is None and not bridge.jobs and not c._busy)
    assert len(c.files.rows)==1 and c._folder_roots==[str(root)]
    bridge.refresh();wait(app,lambda:bridge.worker is None and not bridge.jobs and not c._busy)
    assert len(c.files.rows)==1
    c.scoreOrStop();wait(app,lambda:not c._busy,timeout=45)
    assert c.files.rows[0]['report'] is not None
    bridge.refresh();wait(app,lambda:bridge.worker is None and hub.get(key)['qc'].get('state')=='scored')
    c.prepareUpload();wait(app,lambda:not c._upload.active,timeout=30)
    assert c._upload.batch is not None
    original=c._upload.store.directory/c._upload.batch['local_id']/'state.json'
    hub.remove(key);bridge.refresh();wait(app,lambda:bridge.worker is None and not c.files.rows)
    assert not c.files.rows and not c._folder_roots and c._upload.batch is None
    assert original.exists() and source.exists() and hub.get(key)['removed']==1
    assert not bridge.upload_allowed([str(tmp_path)])
    # Owner refresh never resurrects a tombstone.
    hub.publish('capture:1',root,'capture',{'participant':'P001','status':'completed'})
    assert hub.get(key)['removed']
    hub.restore(key);bridge.refresh();wait(app,lambda:bridge.worker is None and not bridge.jobs and not c._busy)
    assert len(c.files.rows)==1 and c.files.rows[0]['report'] is not None
    c.files.rows[0]['chosen']=True;c.remove()
    assert hub.get(key)['removed'] and not c.files.rows and source.exists()


def test_manual_import_and_running_instance_activation(quick,tmp_path):
    app,c,engine,window=quick
    hub=RecordHub(tmp_path/'hub.sqlite');bridge=SharedRecordView(c,window,hub);c.record_bridge=bridge;bridge.timer.stop()
    import subprocess,sys
    bridge.listen();window.showMinimized()
    process=subprocess.Popen([sys.executable,'-c','from oi_eegqc.window_activation import notify_existing; import sys; assert notify_existing(sys.argv[1])',str(hub.path)])
    wait(app,lambda:process.poll() is not None)
    assert process.returncode==0
    from PySide6.QtCore import Qt
    assert window.isVisible() and window.windowState()!=Qt.WindowMinimized
    root=tmp_path/'external';root.mkdir();recording(root/'signal.npy')
    c.add_paths([str(root)]);wait(app,lambda:not c._busy)
    wait(app,lambda:bridge.worker is None and len(hub.rows())==1)
    assert len(hub.rows())==1 and hub.rows()[0]['producer']=='eegqc'
    bridge.server.close()


def test_removal_waits_for_scoring_and_blocks_stale_upload_selection(quick,tmp_path):
    app,c,engine,window=quick
    root=tmp_path/'capture';root.mkdir();recording(root/'signal.npy')
    hub=RecordHub(tmp_path/'hub.sqlite');hub.publish('capture:busy',root,'capture',{})
    bridge=SharedRecordView(c,window,hub);c.record_bridge=bridge;bridge.timer.stop()
    bridge.refresh();wait(app,lambda:bridge.worker is None and not bridge.jobs and not c._busy)
    c._busy=True;hub.remove('capture:busy');bridge.refresh();wait(app,lambda:bridge.worker is None)
    assert c.files.rows and hub.get('capture:busy')['removed']==1
    c._upload.prepare([str(root)],{})
    assert not c._upload.active and '已移除' in c._upload._error
    c._busy=False;bridge.refresh();wait(app,lambda:bridge.worker is None and not c.files.rows)
    assert not c.files.rows and hub.get('capture:busy')['removed']


def test_shared_record_read_does_not_block_ui(quick,tmp_path,monkeypatch):
    import threading
    from PySide6.QtCore import QTimer
    app,c,_,_=quick
    hub=RecordHub(tmp_path/'hub.sqlite')
    entered,release=threading.Event(),threading.Event()
    original=hub.rows
    def rows():
        entered.set();release.wait(5)
        return original()
    monkeypatch.setattr(hub,'rows',rows)
    bridge=SharedRecordView(c,hub=hub);c.record_bridge=bridge;bridge.timer.stop()
    bridge.refresh();wait(app,entered.is_set)
    heartbeat=[]
    QTimer.singleShot(0,lambda:heartbeat.append(True))
    wait(app,lambda:bool(heartbeat))
    assert bridge.worker is not None
    release.set();wait(app,lambda:bridge.worker is None)


def test_unchanged_record_poll_does_not_read_reports_again(quick,tmp_path,monkeypatch):
    from unittest.mock import Mock
    app,c,_,_=quick
    hub=RecordHub(tmp_path/'hub.sqlite')
    rows=Mock(wraps=hub.rows);monkeypatch.setattr(hub,'rows',rows)
    bridge=SharedRecordView(c,hub=hub);c.record_bridge=bridge;bridge.timer.stop()
    bridge.refresh();wait(app,lambda:bridge.worker is None)
    initial=rows.call_count
    bridge.refresh();wait(app,lambda:bridge.worker is None)
    assert rows.call_count==initial


def test_clear_then_drag_file_or_restore_button_recovers_record(quick,tmp_path):
    app,c,engine,window=quick
    root=tmp_path/'capture';root.mkdir();source=root/'signal.npy';recording(source)
    (root/'session.json').write_text('{"participant_id":"12345678","session_id":"s1","paradigm_code":"AVEEG-20260923"}',encoding='utf-8')
    hub=RecordHub(tmp_path/'hub.sqlite');key=hub.publish('capture:clear',root,'capture',{'participant':'12345678'})
    bridge=SharedRecordView(c,window,hub);c.record_bridge=bridge;bridge.timer.stop()
    bridge.refresh();wait(app,lambda:bridge.worker is None and not bridge.jobs and not c._busy)
    assert len(c.files.rows)==1
    # User clicks "清空" before uploading
    c.remove(True)
    wait(app,lambda:bridge.worker is None and not bridge.jobs)
    assert not c.files.rows and hub.get(key)['removed']==1 and c.removedCount==1
    # User drags the recording file itself back into EEGQC
    c.add_paths([str(source)])
    wait(app,lambda:not c._busy and bridge.worker is None and not bridge.jobs)
    bridge.refresh();wait(app,lambda:bridge.worker is None and not bridge.jobs and not c._busy)
    assert len(c.files.rows)==1
    assert hub.get(key)['removed']==0 and c.removedCount==0
    assert c.canUpload and bridge.upload_allowed(c._folder_roots)
    assert '已从公共名单移除' not in c.summary
    # Clear again and use the one-click restore button
    c.remove(True)
    wait(app,lambda:bridge.worker is None and not bridge.jobs)
    assert not c.files.rows and c.removedCount==1
    c.restoreRemoved()
    wait(app,lambda:bridge.worker is None and not bridge.jobs and not c._busy and len(c.files.rows)==1)
    assert hub.get(key)['removed']==0 and c.removedCount==0


def test_bridge_reuses_capture_handoff_report_and_upload_receipt_without_rescoring(quick,tmp_path):
    import json
    from oi_eegqc.scoring_version import SCORING_ALGORITHM_VERSION
    app,c,engine,window=quick
    root=tmp_path/'round';root.mkdir();source=root/'signal.npy';recording(source)
    stat=source.stat()
    (root/'file-quality.json').write_text(json.dumps({
        'schema':'oi-eegqc-capture-complete-v1',
        'quality_state':'scored',
        'recording':'signal.npy',
        'source':[stat.st_size,stat.st_mtime_ns,50,SCORING_ALGORITHM_VERSION],
        'report':{'gqi':88.5,'duration_s':4.0,'usable_ratio':1.0,'extras':{'sfreq_hz':250,'algorithm_version':SCORING_ALGORITHM_VERSION}}
    }),encoding='utf-8')
    (root/'upload-result.json').write_text(json.dumps({
        'schema':'oi-eegqc-capture-complete-v1',
        'state':'completed',
        'upload_id':'u-123',
        'score':88.5
    }),encoding='utf-8')
    hub=RecordHub(tmp_path/'hub.sqlite')
    key=hub.publish('capture:done',root,'capture',{'participant':'12345678'})
    bridge=SharedRecordView(c,window,hub);c.record_bridge=bridge;bridge.timer.stop()
    bridge.refresh();wait(app,lambda:bridge.worker is None and not bridge.jobs and not c._busy)
    assert len(c.files.rows)==1
    assert c.files.rows[0]['report'] is not None and c.files.rows[0]['score']=='88.5'
    assert c.files.rows[0]['uploadState']=='已上传'
    assert not c.canScore and c.canUpload

