import json
from pathlib import Path

import pytest

from oi_eegqc.capture_service import SCHEMA, complete


def test_portable_manifest_advertises_headless_handoff():
    manifest = Path(__file__).resolve().parents[1] / 'packaging' / 'quality-api.json'
    assert json.loads(manifest.read_text(encoding='utf-8'))['capture_complete'] == SCHEMA


def fixture_round(tmp_path):
    folder = tmp_path / 'round'
    folder.mkdir()
    (folder / 'session.json').write_text(json.dumps({'session_id': 'local-1', 'status': 'completed',
                                                     'storage_format': 'eeg-bids-1'}), encoding='utf-8')
    (folder / 'plan.json').write_text(json.dumps({'session_id': 'local-1', 'mode': 'bcigo',
                                                  'platform_session_id': 'claim-1'}), encoding='utf-8')
    (folder / 'quality-response.json').write_text(json.dumps({'session_id': 'local-1',
        'platform_session_id': 'claim-1', 'segments': [{'trial_id': 'trial-1', 'score': 80}]}), encoding='utf-8')
    recording = folder / 'recording.edf'
    recording.write_bytes(b'fixture')
    return folder, recording


def test_headless_handoff_scores_then_uses_existing_upload_engine(tmp_path, monkeypatch):
    from oi_eegqc import desktop_upload, intake, score_cache
    folder, recording = fixture_round(tmp_path)
    called = []

    class Cache:
        def __init__(self, path): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def digest(self, path):
            stat = path.stat()
            return 'a' * 64, (stat.st_size, stat.st_mtime_ns)
        def lookup(self, digest, metadata): return None
        def store(self, digest, metadata, report): called.append('cache')

    class Report:
        def to_dict(self): return {'gqi': 84.25}

    class Store:
        def __init__(self, path): self.directory = Path(path)
        def prepare(self, roots, scored, progress):
            assert roots == [folder]
            assert recording in scored
            assert (folder / 'file-quality.json').exists()
            called.append('prepare')
            return {'status': 'ready'}

    class Session:
        def __init__(self, store, batch, data, progress): pass
        def run(self):
            called.append('upload')
            return {'status': 'completed', 'upload_id': 'cloud-1'}

    monkeypatch.setattr(score_cache, 'ScoreCache', Cache)
    monkeypatch.setattr(intake, 'score_file', lambda path, progress: Report())
    monkeypatch.setattr(desktop_upload, 'BatchStore', Store)
    monkeypatch.setattr(desktop_upload, 'UploadSession', Session)
    result = complete({'schema': SCHEMA, 'request_id': 'req-1', 'folder': str(folder)},
                      data_directory=tmp_path / 'app')
    assert called == ['cache', 'prepare', 'upload']
    assert result['score'] == 84.25 and result['upload_id'] == 'cloud-1'


def test_handoff_rejects_unmatched_quality_before_upload(tmp_path):
    folder, _ = fixture_round(tmp_path)
    quality = folder / 'quality-response.json'
    value = json.loads(quality.read_text(encoding='utf-8'))
    value['platform_session_id'] = 'another-claim'
    quality.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='不匹配'):
        complete({'schema': SCHEMA, 'request_id': 'req-1', 'folder': str(folder)},
                 data_directory=tmp_path / 'app')
