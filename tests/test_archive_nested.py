"""zip-of-zips 必须被展开到底，否则上传界面会看到 0 个会话。"""
import json
import zipfile

from oi_eegqc.archive_import import expand_inputs


def build_session_zip(folder, session_id):
    session = folder / session_id
    session.mkdir(parents=True)
    (session / 'session.json').write_text(json.dumps({
        'participant_number': 'sub-13467638',
        'session_id': session_id,
        'paradigm_code': 'RSVP-20260924',
    }), encoding='utf-8')
    (session / 'recording_eeg.edf').write_bytes(b'0' * 2048)
    archive = folder.parent / f'{session_id}.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.write(session / 'session.json', f'{session_id}/session.json')
        zf.write(session / 'recording_eeg.edf', f'{session_id}/recording_eeg.edf')
    return archive


def test_nested_zip_of_zips_expands_to_session_dirs(tmp_path):
    inner = [build_session_zip(tmp_path / 'stage' / name, name) for name in ('ses-A', 'ses-B')]
    outer = tmp_path / '未上传.zip'
    with zipfile.ZipFile(outer, 'w') as zf:
        for archive in inner:
            zf.write(archive, f'未上传/{archive.name}')

    roots = expand_inputs([outer], tmp_path / 'cache')

    assert len(roots) == 2
    for root in roots:
        assert (tmp_path / 'cache' and __import__('pathlib').Path(root) / 'session.json').exists()
