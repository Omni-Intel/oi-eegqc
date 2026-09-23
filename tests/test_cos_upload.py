import pytest
from oi_eegqc.upload_http import COS_HOST, PREFIX, validate_put_url
from oi_eegqc.upload_signer.cos_backend import CosStorage

def test_cos_destination_is_exact_and_https_only():
    key = PREFIX + 'sample/a.npy'
    validate_put_url(f'https://{COS_HOST}/{key}?q-signature=test', key)
    for url in [f'http://{COS_HOST}/{key}?x=1', f'https://{COS_HOST}.evil.test/{key}?x=1',
                f'https://{COS_HOST}:444/{key}?x=1', f'https://user@{COS_HOST}/{key}?x=1',
                f'https://{COS_HOST}/other/a.npy?x=1']:
        with pytest.raises(ValueError): validate_put_url(url, key)

def test_cos_parts_pagination_and_prefix():
    class Client:
        def list_parts(self, **kwargs):
            marker = kwargs['PartNumberMarker']
            return {'Part': [{'PartNumber': str(marker+1), 'Size': '42', 'ETag': 'etag'}],
                    'IsTruncated': 'true' if marker == 0 else 'false', 'NextPartNumberMarker': '1'}
    storage = CosStorage(Client())
    assert [p['partNumber'] for p in storage.list_parts(PREFIX+'sample/a', 'multipart')] == [1, 2]
    with pytest.raises(ValueError): storage._key('other/a')
    with pytest.raises(ValueError): storage._key(PREFIX+'../a')
