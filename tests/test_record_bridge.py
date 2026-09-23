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
    bridge.refresh();wait(app,lambda:not c._busy)
    assert len(c.files.rows)==1 and c._folder_roots==[str(root)]
    bridge.refresh();wait(app,lambda:not c._busy)
    assert len(c.files.rows)==1
    c.scoreOrStop();wait(app,lambda:not c._busy,timeout=45)
    assert c.files.rows[0]['report'] is not None
    bridge.refresh();assert hub.get(key)['qc']['state']=='scored'
    c.prepareUpload();wait(app,lambda:not c._upload.active,timeout=30)
    assert c._upload.batch is not None
    original=c._upload.store.directory/c._upload.batch['local_id']/'state.json'
    hub.remove(key);bridge.refresh()
    assert not c.files.rows and not c._folder_roots and c._upload.batch is None
    assert original.exists() and source.exists() and hub.get(key)['removed']==1
    assert not bridge.upload_allowed([str(tmp_path)])
    # Owner refresh never resurrects a tombstone.
    hub.publish('capture:1',root,'capture',{'participant':'P001','status':'completed'})
    assert hub.get(key)['removed']
    hub.restore(key);bridge.refresh();wait(app,lambda:not c._busy)
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
    assert len(hub.rows())==1 and hub.rows()[0]['producer']=='eegqc'
    bridge.server.close()


def test_removal_waits_for_scoring_and_blocks_stale_upload_selection(quick,tmp_path):
    app,c,engine,window=quick
    root=tmp_path/'capture';root.mkdir();recording(root/'signal.npy')
    hub=RecordHub(tmp_path/'hub.sqlite');hub.publish('capture:busy',root,'capture',{})
    bridge=SharedRecordView(c,window,hub);c.record_bridge=bridge;bridge.timer.stop()
    bridge.refresh();wait(app,lambda:not c._busy)
    c._busy=True;hub.remove('capture:busy');bridge.refresh()
    assert c.files.rows and hub.get('capture:busy')['removed']==1
    c._upload.prepare([str(root)],{})
    assert not c._upload.active and '已移除' in c._upload._error
    c._busy=False;bridge.refresh()
    assert not c.files.rows and hub.get('capture:busy')['removed']
