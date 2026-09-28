import io
import json
from zipfile import ZipFile
from dataclasses import replace
from urllib.error import URLError
import pytest

from oi_eegqc.desktop_update import (INTRANET_CHANNEL, ZIP_ASSET, downloadable,
                                      stage_portable_update)
from oi_eegqc.telemetry import Telemetry


def test_portable_channel_and_package_layout(tmp_path):
    from test_desktop_update import installer_info
    info = replace(installer_info(), asset_name=ZIP_ASSET,
                   asset_url=INTRANET_CHANNEL + 'eegqc-0.3.5.zip')
    assert downloadable(info)
    assert downloadable(replace(installer_info(), asset_url=INTRANET_CHANNEL + 'eegqc-0.3.5-setup.exe'))
    archive = tmp_path / ZIP_ASSET
    with ZipFile(archive, 'w') as package:
        package.writestr('OI-EEGQC/OI-EEGQC.exe', b'program')
        package.writestr('OI-EEGQC/_internal/runtime.dll', b'runtime')
    incoming = stage_portable_update(archive)
    assert (incoming / 'OI-EEGQC.exe').read_bytes() == b'program'
    with ZipFile(archive, 'w') as package:
        package.writestr('OI-EEGQC/manual.txt', 'manual')
    with pytest.raises(ValueError, match='缺少程序'):
        stage_portable_update(archive)


def test_telemetry_retains_offline_events_until_ack(tmp_path):
    client = Telemetry(tmp_path, enabled=False, install_kind='installed')
    client.paradigm = 'RSVP-20260924'
    try:
        raise PermissionError(13, 'participant@example.com D:/private/subject.edf')
    except PermissionError as exc:
        client.report('upload_failed', exc=exc)
    def offline(*args, **kwargs):
        raise URLError('offline')
    with pytest.raises(URLError):
        client.flush(opener=offline)
    captured = []
    def acknowledge(request, **kwargs):
        events = json.loads(request.data)['events']
        captured.extend(events)
        assert 'participant@example.com' not in request.data.decode()
        assert 'subject.edf' not in request.data.decode()
        return io.BytesIO(json.dumps({'accepted': [e['id'] for e in events]}).encode())
    client.flush(opener=acknowledge)
    assert captured[0]['app'] == 'EEGQC'
    assert captured[0]['paradigm'] == 'RSVP-20260924'
    assert captured[0]['install_kind'] == 'installed'
    assert captured[0]['errno'] == 13
    with client.db() as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
