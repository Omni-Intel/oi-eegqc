import json
import pytest
from oi_eegqc.desktop_upload import scan_sources, UploadError


def test_bids_must_be_complete_and_unchanged(tmp_path):
    signal=tmp_path/'record.edf';signal.write_bytes(b'eeg')
    (tmp_path/'session.json').write_text(json.dumps({'storage_format':'eeg-bids-1'}))
    with pytest.raises(UploadError,match='BIDS'):scan_sources([tmp_path])
    stat=signal.stat();marker=tmp_path/'bids-export.json'
    manifest={'schema':'oi-bids-export-v1','state':'ready','files':[{'path':'record.edf','bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns,'sha256':'fake'}]}
    marker.write_text(json.dumps(manifest));assert len(scan_sources([tmp_path]))==3
    signal.write_bytes(b'changed')
    with pytest.raises(UploadError,match='BIDS'):scan_sources([tmp_path])


def test_legacy_upload_unaffected(tmp_path):
    (tmp_path/'record.edf').write_bytes(b'eeg');assert len(scan_sources([tmp_path]))==1
