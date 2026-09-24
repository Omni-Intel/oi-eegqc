import json
import pytest
from oi_eegqc import local_files
from oi_eegqc.desktop_upload import friendly_error


def test_sharing_conflict_retries_without_overwriting_other_temporary_files(tmp_path,monkeypatch):
    path=tmp_path/'state.json';path.write_text('{"previous":true}')
    unrelated=tmp_path/'state.tmp';unrelated.write_text('another writer')
    replace=local_files.os.replace;calls=[]
    def busy(source,target):
        calls.append(source)
        if len(calls)<3:
            error=PermissionError(13,'busy',str(target));error.winerror=32;raise error
        return replace(source,target)
    monkeypatch.setattr(local_files.os,'replace',busy)
    monkeypatch.setattr(local_files.time,'sleep',lambda _:None)
    local_files.atomic_json(path,{'completed':234})
    assert json.loads(path.read_text())=={'completed':234}
    assert len(calls)==3 and calls[0]!=unrelated and unrelated.read_text()=='another writer'


def test_persistent_denial_keeps_previous_state_and_names_file(tmp_path,monkeypatch):
    path=tmp_path/'upload-state.json';path.write_text('original')
    def denied(*args):raise PermissionError(13,'denied',str(path))
    monkeypatch.setattr(local_files.os,'replace',denied)
    with pytest.raises(PermissionError) as exc:local_files.atomic_json(path,{})
    assert path.read_text()=='original'
    assert 'upload-state.json' in friendly_error(exc.value)
    error=PermissionError(13,'busy',str(path));error.winerror=32
    assert '暂被占用' in friendly_error(error)
