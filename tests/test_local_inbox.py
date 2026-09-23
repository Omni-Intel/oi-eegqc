from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from oi_eegqc.upload_signer.local_backend import LocalInboxStorage
from oi_eegqc.upload_signer.service import INBOX_PREFIX


def storage(tmp_path):
    return LocalInboxStorage(tmp_path / "inbox", "http://172.16.1.249:8443", b"secret-bytes")


def test_signed_put_stays_on_intranet_and_round_trip(tmp_path):
    backend = storage(tmp_path)
    key = f"{INBOX_PREFIX}/20260918T150000Z-abcd1234/session_01/a.npy"
    url = backend.sign_put(key)
    parsed = urlsplit(url)
    assert parsed.hostname == "172.16.1.249"
    assert parsed.path == "/objects/" + key
    backend.verify_put(key, parse_qs(parsed.query))
    etag = backend.write_object(key, BytesIO(b"eeg-bytes"), 9)
    assert backend.resolve(key).read_bytes() == b"eeg-bytes"
    assert etag == '"9"'


def test_key_cannot_escape_inbox(tmp_path):
    backend = storage(tmp_path)
    try:
        backend.resolve(f"{INBOX_PREFIX}/../secret")
    except ValueError:
        return
    raise AssertionError("escaped inbox")
