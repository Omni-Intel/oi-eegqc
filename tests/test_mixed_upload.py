import json
from pathlib import Path

import pytest

from oi_eegqc.archive_import import attach_sessions, database_pending, sync_database
from oi_eegqc.desktop_upload import BatchStore, UploadError
from oi_eegqc.mixed_upload import MixedBatchStore, paradigm_directory
from oi_eegqc.upload_http import SignClient, validate_put_url
from oi_eegqc.upload_session import UploadSession
from test_desktop_upload import FakeClient

SINGLE = dict(code='RSVP-20260924', name='单图 RSVP',
              destination=dict(bucket='jianyu-1442740494', region='ap-beijing', prefix='rsvp'))
DUAL = dict(code='DoRSVP-20261011', name='双图 RSVP',
            destination=dict(bucket='jianyu-1442740494', region='ap-beijing', prefix='DoRSVP'))
PARADIGMS = [SINGLE, DUAL]


def fixture_sessions(root):
    rows = []
    for name, paradigm in (('single', SINGLE), ('dual', DUAL)):
        directory = root/name
        directory.mkdir(parents=True)
        recording = directory/'eeg.edf'
        recording.write_bytes(('raw-'+name).encode())
        (directory/'session.json').write_text(json.dumps(dict(
            participant_id='sub-13467638', session_id='ses-'+name,
            paradigm_code=paradigm['code'],
            started_at='2026-10-20T01:00:00+00:00', ended_at='2026-10-20T01:01:00+00:00',
        )))
        rows.append(dict(session_directory=str(directory), recording=str(recording),
                         participant_number='13467638', source_session_id='ses-'+name,
                         paradigm_code=paradigm['code'], recorded_duration_s=60,
                         started_at='2026-10-20T01:00:00+00:00', ended_at='2026-10-20T01:01:00+00:00',
                         final_score=90, usable_duration_s=60,
                         signal_qc=dict(gqi=90, usable_ratio=1, duration_s=60)))
    return rows


class RoutedClient(SignClient):
    def __init__(self, backends):
        super().__init__()
        self.backends = backends

    @property
    def backend(self):
        return self.backends[self.paradigm]

    def sign_round(self, upload_id, round_id, paths):
        prefix = self.destination['prefix'].rstrip('/')+'/'+upload_id+'/'
        return dict(uploadId=upload_id, roundId=round_id, prefix=prefix, expiresAt='later',
                    objects=[dict(path=p, objectKey=prefix+p, putUrl=self.backend.url(prefix+p)) for p in paths])

    def put(self, url, key, source, size, cancelled, progress):
        validate_put_url(url, key, self.destination)
        self.backend.calls.append(key)
        if self.backend.fail and key.endswith(self.backend.fail):
            raise OSError('test failure')
        data = Path(source).read_bytes() if source else b''
        assert len(data) == size
        progress(size)
        self.backend.objects[key] = data
        return 'etag'


def delegate(name):
    def call(self, *args, **kwargs):
        return getattr(self.backend, name)(*args, **kwargs)
    return call


for method in ('health', 'resolve_collection', 'start_round', 'register_files',
               'seal_round', 'round_status', 'complete_files', 'complete_round'):
    setattr(RoutedClient, method, delegate(method))


def test_mixed_queue_routes_resumes_and_keeps_canonical_receipts(tmp_path):
    rows = fixture_sessions(tmp_path/'originals')
    state = tmp_path/'state'
    # Adopt an existing normal-mode record rather than creating another ID.
    normal = BatchStore(paradigm_directory(state, SINGLE['code']), SINGLE)
    previous = normal.prepare([rows[0]['session_directory']], None)
    store = MixedBatchStore(state, PARADIGMS)
    roots = store.configure([tmp_path/'originals'], rows)
    batch = store.prepare(roots, None)
    attach_sessions(batch, rows)
    store.save(batch)
    single = next(p for p in batch['children'] if p['paradigm_code'] == SINGLE['code'])
    assert single['local_id'] == previous['local_id']
    assert {p['destination']['prefix'] for p in batch['children']} == {'rsvp', 'DoRSVP'}
    backends = {p['code']: FakeClient() for p in PARADIGMS}
    backends[DUAL['code']].fail = 'eeg.edf'
    run = UploadSession(store, batch, state, lambda _: None, lambda _: RoutedClient(backends))
    assert run.run()['status'] == 'failed'
    assert next(p for p in batch['children'] if p['paradigm_code'] == SINGLE['code'])['status'] == 'completed'
    successful_puts = len(backends[SINGLE['code']].calls)

    class Database:
        def __init__(self):
            self.calls = []

        def commit_import(self, payload):
            self.calls.append(payload['paradigm_code'])
            return dict(session_id=payload['source_session_id'])

    db = Database()
    sync_database(store, batch, db, lambda _: None)
    assert db.calls == [SINGLE['code']] and database_pending(batch)
    resumed_store = MixedBatchStore(state, PARADIGMS)
    resumed = resumed_store.load()
    ids = {(p['paradigm_code'], p['upload_id'], p['round_id']) for p in resumed['children']}
    # Fake backends deliberately allocate the same upload ID in distinct paradigms.
    assert len({p['upload_id'] for p in resumed['children']}) == 1
    backends[DUAL['code']].fail = None
    retry = UploadSession(resumed_store, resumed, state, lambda _: None, lambda _: RoutedClient(backends))
    assert retry.run()['status'] == 'completed'
    assert {(p['paradigm_code'], p['upload_id'], p['round_id']) for p in resumed['children']} == ids
    assert len(backends[SINGLE['code']].calls) == successful_puts
    assert retry.reused_files > 0
    sync_database(resumed_store, resumed, db, lambda _: None)
    assert db.calls == [SINGLE['code'], DUAL['code']]
    assert not database_pending(resumed)
    assert normal.load()['imports'][0]['receipt']['session_id'] == 'ses-single'
    before = sum(len(b.calls) for b in backends.values())
    assert UploadSession(resumed_store, resumed, state, lambda _: None,
                         lambda _: RoutedClient(backends)).run()['status'] == 'completed'
    assert sum(len(b.calls) for b in backends.values()) == before


def test_mixed_queue_rejects_unknown_or_unassociated_recordings(tmp_path):
    rows = fixture_sessions(tmp_path/'originals')
    store = MixedBatchStore(tmp_path/'state', PARADIGMS)
    with pytest.raises(UploadError, match='未识别或平台未启用'):
        store.configure([tmp_path/'originals'], [dict(rows[0], paradigm_code='UNKNOWN')])
    (tmp_path/'originals/orphan.edf').write_bytes(b'raw')
    with pytest.raises(UploadError, match='无法关联范式'):
        store.configure([tmp_path/'originals'], rows)


def test_mixed_canonical_checkpoint_survives_parent_save_interruption(tmp_path):
    rows = fixture_sessions(tmp_path/'originals')
    store = MixedBatchStore(tmp_path/'state', PARADIGMS)
    roots = store.configure([tmp_path/'originals'], rows)
    batch = store.prepare(roots, None)
    child = dict(batch['children'][0], upload_id='allocated-before-interruption', status='paused')
    store._write_record(child)
    loaded = MixedBatchStore(tmp_path/'state', PARADIGMS).load()
    assert loaded['children'][0]['upload_id'] == child['upload_id']


def test_worker_automatically_detects_mixed_sessions(tmp_path, monkeypatch):
    from PySide6.QtCore import QObject
    from oi_eegqc import archive_import
    from oi_eegqc.upload_ui import UploadWorker
    rows = fixture_sessions(tmp_path/'originals')
    monkeypatch.setattr(archive_import, 'grade_sessions', lambda *args: dict(scored=0, reused=2, failed=0))
    parent = QObject()
    parent.telemetry = None
    original_store = BatchStore(paradigm_directory(tmp_path/'state', SINGLE['code']), SINGLE)
    worker = UploadWorker(original_store, tmp_path/'state', roots=[tmp_path/'originals'],
                          parent=parent, paradigms=PARADIGMS)
    worker.run()
    assert worker.error == ''
    assert worker.batch['mixed_paradigms']
    assert len(worker.batch['children']) == 2
    assert all(p['imports'][0]['paradigm_code'] == p['paradigm_code'] for p in worker.batch['children'])


def test_mixed_cancel_retains_route_and_round_for_retry(tmp_path):
    rows = fixture_sessions(tmp_path/'originals')
    state = tmp_path/'state'
    store = MixedBatchStore(state, PARADIGMS)
    batch = store.prepare(store.configure([tmp_path/'originals'], rows), None)
    backends = {p['code']: FakeClient() for p in PARADIGMS}
    cancelled = []
    def progress(value):
        if value.get('done', 0) and not cancelled:
            cancelled.append(True)
            session.cancel()
    session = UploadSession(store, batch, state, progress, lambda _: RoutedClient(backends))
    assert session.run()['status'] == 'paused'
    paused = MixedBatchStore(state, PARADIGMS).load()
    allocated = {(p['paradigm_code'], p['upload_id'], p['round_id'])
                 for p in paused['children'] if p['upload_id']}
    assert allocated
    retry = UploadSession(store, paused, state, lambda _: None, lambda _: RoutedClient(backends))
    assert retry.run()['status'] == 'completed'
    assert allocated <= {(p['paradigm_code'], p['upload_id'], p['round_id']) for p in paused['children']}


def test_mixed_save_updates_multi_folder_canonical_parent(tmp_path):
    rows = fixture_sessions(tmp_path/'originals')
    extra = dict(rows[0])
    directory = tmp_path/'originals/single-again'
    directory.mkdir()
    (directory/'eeg.edf').write_bytes(b'raw-extra')
    extra.update(session_directory=str(directory), recording=str(directory/'eeg.edf'), source_session_id='ses-extra')
    rows.append(extra)
    state = tmp_path/'state'
    store = MixedBatchStore(state, PARADIGMS)
    batch = store.prepare(store.configure([tmp_path/'originals'], rows), None)
    for part in batch['children']:
        part['status'] = 'completed'
        for entry in part['entries']:
            entry['done'] = True
    store.save(batch)
    restored = BatchStore(paradigm_directory(state, SINGLE['code']), SINGLE).load()
    assert restored['status'] == 'completed'
    assert len(restored['children']) == 2


def test_mixed_resume_rejects_changed_canonical_destination(tmp_path):
    rows = fixture_sessions(tmp_path/'originals')
    state = tmp_path/'state'
    store = MixedBatchStore(state, PARADIGMS)
    batch = store.prepare(store.configure([tmp_path/'originals'], rows), None)
    child = batch['children'][0]
    path = store.stores[child['paradigm_code']].directory/child['local_id']/'state.json'
    value = json.loads(path.read_text())
    value['destination'] = dict(value['destination'], prefix='unexpected')
    path.write_text(json.dumps(value))
    with pytest.raises(UploadError, match='范式或来源目录不一致'):
        store.load()


def test_mixed_checkpoint_does_not_rewrite_unchanged_child_states(tmp_path):
    rows = fixture_sessions(tmp_path/'originals')
    state = tmp_path/'state'
    store = MixedBatchStore(state, PARADIGMS)
    batch = store.prepare(store.configure([tmp_path/'originals'], rows), None)
    paths = [store.stores[p['paradigm_code']].directory/p['local_id']/'state.json'
             for p in batch['children']]
    before = [p.stat().st_mtime_ns for p in paths]
    store.save(batch)
    assert [p.stat().st_mtime_ns for p in paths] == before
    batch['children'][0]['upload_id'] = 'newly-allocated'
    store.save(batch)
    assert json.loads(paths[0].read_text())['upload_id'] == 'newly-allocated'
