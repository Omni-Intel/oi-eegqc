from oi_eegqc.desktop_upload import BatchStore
from oi_eegqc.upload_http import validate_put_url, _connection
from urllib.parse import urlsplit
from oi_eegqc.upload_signer.service import UploadCoordinator

RSVP = {'code': 'RSVP-20260924', 'name': 'RSVP', 'destination':
        {'provider': 'cos', 'bucket': 'jianyu-1442740494', 'region': 'ap-beijing', 'prefix': 'rsvp'}}


def test_single_file_queue_keeps_paradigm_on_resume(tmp_path):
    source = tmp_path / 'recording.edf'
    source.write_bytes(b'raw-eeg')
    store = BatchStore(tmp_path / 'state', RSVP)
    first = store.prepare([source], None)
    assert first['destination'] == RSVP['destination']
    assert first['entries'][0]['relative'] == source.name
    resumed = store.prepare([source], None)
    assert resumed['local_id'] == first['local_id']
    assert resumed['round_request_id'] == first['round_request_id']


def test_rsvp_signing_uses_database_destination(tmp_path):
    class Storage:
        prefix = 'rsvp'
        def begin_round(self, *args): pass
        def sign_put(self, key): return f'https://jianyu-1442740494.cos.ap-beijing.myqcloud.com/{key}?signed=1'
    service = UploadCoordinator(tmp_path/'state.sqlite3', Storage())
    ids = service.start_round(None, 'request-rsvp-01')
    upload, round_id = ids['upload_id'], ids['round_id']
    service.register_files(upload, round_id, [{'path':'eeg.edf','size':1,'sha256':'a'*64,'directory':False}])
    service.seal_round(upload, round_id, 1)
    signed = service.sign_single(upload, round_id, ['eeg.edf'])[0]
    assert signed['objectKey'] == f'rsvp/{upload}/eeg.edf'
    validate_put_url(signed['putUrl'], signed['uploadKey'], RSVP['destination'])
    connection = _connection(urlsplit(signed['putUrl']), 1, RSVP['destination'])
    assert connection.host == 'jianyu-1442740494.cos.ap-beijing.myqcloud.com'
    connection.close()
