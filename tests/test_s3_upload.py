"""RustFS transport contract, including direct HTTP PUT and multipart recovery."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from oi_eegqc.desktop_upload import BatchStore, UploadSession
from oi_eegqc.upload_destination import (
    S3_ENDPOINT, destination_id, destination_text, normalize_destination, queue_directory,
)
from oi_eegqc.upload_http import SignClient, _connection, validate_put_url
from oi_eegqc.upload_signer.app import UploadServer
from oi_eegqc.upload_signer.paradigms import ParadigmRegistry
from oi_eegqc.upload_signer.s3_backend import S3Storage
from oi_eegqc.upload_signer.service import UploadCoordinator


def target(endpoint=S3_ENDPOINT, prefix="test-task"):
    return dict(provider="s3", endpoint=endpoint, bucket="oi-data", region="us-east-1", prefix=prefix)


def paradigm(destination=None):
    return dict(code="NAS-TEST", name="NAS test", destination=destination or target())


def test_s3_urls_use_exact_endpoint_bucket_key_and_prefix():
    destination = target()
    key = "test-task/recording/脑电.edf"
    url = f"{S3_ENDPOINT}/oi-data/{key}?X-Amz-Signature=test"
    validate_put_url(url, key, destination)
    connection = _connection(urlsplit(url), 1, destination)
    assert (connection.host, connection.port) == ("172.16.4.2", 9000)
    connection.close()
    for bad in (
        url.replace("172.16.4.2", "172.16.4.3"), url.replace(":9000", ":9001"),
        url.replace("http:", "https:"), url.replace("/oi-data/", "/oi-task-assets/"),
        url.replace("172.16.4.2", "user@172.16.4.2"), url + "#fragment",
        url.split("?")[0], url.replace("脑电.edf", "other.edf"),
    ):
        with pytest.raises(ValueError):
            validate_put_url(bad, key, destination)
    for key in ("other/a", "test-task/../a", "test-task//a", "test-task/a\\b"):
        with pytest.raises(ValueError):
            validate_put_url(f"{S3_ENDPOINT}/oi-data/{key}?signed=1", key, destination)
    with pytest.raises(ValueError):
        _connection(urlsplit("http://172.16.4.2:9001/"), 1, destination)


@pytest.mark.parametrize("endpoint", [
    "http://user:password@host:9000", "http://host:9000/oi-data", "ftp://host",
    "http://host:9000?secret=value", "http://host:99999", "http://host/#fragment",
])
def test_endpoint_is_an_origin_without_embedded_credentials(endpoint):
    with pytest.raises(ValueError):
        normalize_destination(target(endpoint))


def test_endpoint_aliases_share_an_identity_but_different_targets_do_not():
    original = target("http://NAS:80/")
    assert destination_id(original) == destination_id(target("http://nas"))
    assert destination_id(original) != destination_id(target("http://nas:9000"))
    assert destination_id(original) != destination_id(target("http://nas", "other-task"))
    assert destination_text(target()) == f"s3://oi-data/test-task/ · {S3_ENDPOINT}"


def test_nas_queues_do_not_adopt_cos_completion_records(tmp_path):
    source = tmp_path / "recording.txt"
    source.write_bytes(b"source")
    old_row = dict(code="AVEEG-20260923", destination=dict(
        provider="cos", bucket="neurolm-1442740494", region="ap-beijing", prefix="neuro-lm/data/inbox"))
    old = BatchStore(queue_directory(tmp_path / "app", old_row), old_row)
    batch = old.prepare([source], None)
    batch.update(status="completed", upload_id="20261102T000000Z-12345678")
    for entry in batch["entries"]:
        entry["done"] = True
    old.save(batch)
    new_row = dict(old_row, destination=target())
    assert queue_directory(tmp_path / "app", new_row) != old.directory
    # Even if a caller accidentally selects the old directory, never reuse its receipt.
    new = BatchStore(old.directory, new_row)
    assert new.load() is None
    fresh = new.prepare([source], None)
    assert fresh["upload_id"] is None and not any(e["done"] for e in fresh["entries"])
    assert (old.directory / batch["local_id"] / "state.json").exists()


def test_changed_server_destination_is_rejected_before_allocating(tmp_path):
    source = tmp_path / "a.txt"
    source.write_bytes(b"a")
    store = BatchStore(tmp_path / "queue", paradigm())
    batch = store.prepare([source], None)

    class Client:
        def health(self):
            return {"storageTarget": target(prefix="changed-task")}
        def start_round(self, *args):
            pytest.fail("A stale target must not allocate an upload")
        def close(self):
            pass

    result = UploadSession(store, batch, tmp_path, lambda p: None, lambda p: Client()).run()
    assert result["status"] == "failed" and "存储目标已变化" in result["error"]
    assert result["upload_id"] is None


@pytest.fixture
def s3(monkeypatch):
    pytest.importorskip("boto3")
    pytest.importorskip("moto.server")
    from moto.server import ThreadedMotoServer
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test-secret-key")
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    destination = target(f"http://{host}:{port}")
    storage = S3Storage.from_env(**{k: v for k, v in destination.items() if k != "provider"})
    storage.client.create_bucket(Bucket="oi-data")
    try:
        yield storage
    finally:
        storage.client.close()
        server.stop()


@contextmanager
def signer(tmp_path, storage, monkeypatch, rows=None):
    config = tmp_path / "paradigms.json"
    config.write_text(json.dumps({"paradigms": rows or [paradigm(storage.destination)]}), encoding="utf-8")
    state = tmp_path / "signer.sqlite3"
    coordinator = UploadCoordinator(state, storage)
    registry = ParadigmRegistry(None, state, storage, config_file=config)
    server = UploadServer(("127.0.0.1", 0), coordinator, storage, registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("oi_eegqc.upload_http.ORIGIN", f"http://127.0.0.1:{server.server_port}")
    try:
        yield registry
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        coordinator.db.close()
        for _, routed, routed_storage in registry.routes.values():
            routed.db.close()
            routed_storage.client.close()


def test_direct_s3_upload_publishes_bytes_and_completion_receipt(tmp_path, s3, monkeypatch):
    source = tmp_path / "collection"
    source.mkdir()
    (source / "数据.txt").write_bytes(b"raw recording")
    (source / "empty").mkdir()
    with signer(tmp_path, s3, monkeypatch):
        client = SignClient()
        rows = client.paradigms()
        assert rows == [paradigm(s3.destination)]
        assert not any("key" in k for k in rows[0]["destination"])
        store = BatchStore(queue_directory(tmp_path / "app", rows[0]), rows[0])
        batch = store.prepare([source], None)
        session = UploadSession(store, batch, tmp_path / "app", lambda p: None)
        result = session.run()
        assert result["status"] == "completed", result["error"]
        key = f"test-task/{result['upload_id']}/collection/数据.txt"
        assert s3.client.get_object(Bucket="oi-data", Key=key)["Body"].read() == b"raw recording"
        prefix = f"test-task/{result['upload_id']}/"
        marker = s3.client.get_object(Bucket="oi-data", Key=prefix + "_COMPLETE")["Body"].read()
        assert json.loads(marker)["roundId"] == result["round_id"]
        keys = [x["Key"] for x in s3.client.list_objects_v2(Bucket="oi-data")["Contents"]]
        assert prefix + "_UPDATING" not in keys
        assert not any("/.staging/" in k for k in keys)
        retried = UploadSession(store, store.load(), tmp_path / "app", lambda p: None)
        assert retried.run()["status"] == "completed"
        assert retried.transferred_files == 0 and retried.reused_files == 1


def test_mixed_nas_upload_isolates_cos_receipts_and_reuses_canonical_ids(tmp_path, s3, monkeypatch):
    from oi_eegqc.mixed_upload import MixedBatchStore
    rows, sessions = [], []
    for code, prefix in (("TASK-A", "task-a"), ("TASK-B", "task-b")):
        rows.append(dict(code=code, name=code, destination=dict(s3.destination, prefix=prefix)))
        source = tmp_path / code
        source.mkdir()
        (source / "eeg.txt").write_bytes(code.encode())
        sessions.append(dict(session_directory=str(source), paradigm_code=code))
    app = tmp_path / "app"
    roots = [s["session_directory"] for s in sessions]
    cos_rows = [dict(p, destination=dict(provider="cos", bucket="jianyu-1442740494",
                                        region="ap-beijing", prefix=p["code"])) for p in rows]
    cos = MixedBatchStore(app, cos_rows)
    old = cos.prepare(cos.configure(roots, sessions), None)
    for child in old["children"]:
        child.update(status="completed", upload_id="20261102T000000Z-12345678")
        for entry in child["entries"]:
            entry["done"] = True
    old["status"] = "completed"
    cos.save(old)
    original = (cos.directory / old["local_id"] / "state.json").read_bytes()

    # Normal and mixed views of the same NAS task share its upload identity.
    normal = BatchStore(queue_directory(app, rows[0]), rows[0])
    canonical = normal.prepare([roots[0]], None)
    mixed = MixedBatchStore(app, rows)
    assert mixed.directory != cos.directory and mixed.load() is None
    batch = mixed.prepare(mixed.configure(roots, sessions), None)
    assert batch["children"][0]["local_id"] == canonical["local_id"]
    assert all(p["upload_id"] is None and not any(e["done"] for e in p["entries"])
               for p in batch["children"])
    with signer(tmp_path, s3, monkeypatch, rows):
        result = UploadSession(mixed, batch, app, lambda _: None).run()
        assert result["status"] == "completed", result["error"]
        for child in result["children"]:
            prefix = child["destination"]["prefix"] + "/" + child["upload_id"] + "/"
            key = prefix + child["paradigm_code"] + "/eeg.txt"
            assert s3.client.get_object(Bucket="oi-data", Key=key)["Body"].read() == child["paradigm_code"].encode()
            s3.client.head_object(Bucket="oi-data", Key=prefix + "_COMPLETE")
        resumed = MixedBatchStore(app, list(reversed(rows)))
        retry = UploadSession(resumed, resumed.load(), app, lambda _: None)
        assert retry.run()["status"] == "completed" and retry.transferred_files == 0
    assert normal.load()["upload_id"] == result["children"][0]["upload_id"]
    assert (cos.directory / old["local_id"] / "state.json").read_bytes() == original
    changed = [dict(rows[0], destination=dict(rows[0]["destination"], prefix="new-task")), rows[1]]
    assert MixedBatchStore(app, changed).load() is None


def test_multipart_resumes_after_signer_restart(tmp_path, s3, monkeypatch):
    import oi_eegqc.upload_session as module
    block = 5 * 1024**2
    monkeypatch.setattr(S3Storage, "single_put_limit", block)
    monkeypatch.setattr(module, "single_put_limit", lambda target: block)
    monkeypatch.setattr(module, "PART_SIZE", block)
    source = tmp_path / "large.bin"
    contents = b"a" * block + b"final-part"
    source.write_bytes(contents)
    row = paradigm(s3.destination)
    store = BatchStore(queue_directory(tmp_path / "app", row), row)
    batch = store.prepare([source], None)
    entry = batch["entries"][0]
    with signer(tmp_path, s3, monkeypatch):
        client = SignClient()
        client.paradigm, client.destination = row["code"], row["destination"]
        ids = client.start_round(None, batch["round_request_id"])
        uid, rid = ids["uploadId"], ids["roundId"]
        client.register_files(uid, rid, [dict(path=entry["relative"], size=entry["size"], sha256=entry["sha256"])])
        client.seal_round(uid, rid, 1)
        info = client.start_multipart(uid, rid, entry["relative"], block)
        s3.client.upload_part(Bucket="oi-data", Key=info["objectKey"],
                             UploadId=info["multipartUploadId"], PartNumber=1, Body=contents[:block])
        batch.update(upload_id=uid, round_id=rid, round_sealed=True, status="paused")
        store.save(batch)
    transmitted = []
    original = SignClient.put_part

    def put_part(self, url, *args):
        transmitted.append(parse_qs(urlsplit(url).query)["partNumber"])
        return original(self, url, *args)

    monkeypatch.setattr(SignClient, "put_part", put_part)
    with signer(tmp_path, s3, monkeypatch):
        result = UploadSession(store, store.load(), tmp_path / "app", lambda p: None).run()
        assert result["status"] == "completed", result["error"]
        assert transmitted == [["2"]]
        actual = s3.client.get_object(Bucket="oi-data", Key=info["objectKey"])["Body"].read()
        assert hashlib.sha256(actual).hexdigest() == entry["sha256"]


def test_registry_uses_different_ledgers_for_changed_nas_targets(tmp_path, s3):
    config = tmp_path / "paradigms.json"
    state = tmp_path / "signer.sqlite3"
    config.write_text(json.dumps({"paradigms": [paradigm(s3.destination)]}), encoding="utf-8")
    first = ParadigmRegistry(None, state, s3, config_file=config)
    coordinator, _ = first.route("NAS-TEST")
    original_database = coordinator.db.execute("PRAGMA database_list").fetchone()[2]
    coordinator.start_round(None, "test-request-001")
    changed = dict(s3.destination, endpoint=s3.endpoint.replace("127.0.0.1", "localhost"))
    config.write_text(json.dumps({"paradigms": [paradigm(changed)]}), encoding="utf-8")
    first.loaded_at = 0
    with pytest.raises(ValueError, match="destination changed"):
        first.route("NAS-TEST")
    second = ParadigmRegistry(None, state, s3, config_file=config)
    new, _ = second.route("NAS-TEST")
    assert new.db.execute("PRAGMA database_list").fetchone()[2] != original_database
    assert new.db.execute("SELECT COUNT(*) FROM collections").fetchone()[0] == 0
    for registry in (first, second):
        for _, coordinator, storage in registry.routes.values():
            coordinator.db.close()
            storage.client.close()


def test_default_s3_server_startup_selects_isolated_ledger(tmp_path, s3, monkeypatch):
    from oi_eegqc.upload_signer import app
    monkeypatch.setenv("UPLOAD_BACKEND", "s3")
    monkeypatch.setenv("UPLOAD_STATE", str(tmp_path / "signer.sqlite3"))
    monkeypatch.setenv("LISTEN_HOST", "127.0.0.1")
    monkeypatch.delenv("TLS_CERT", raising=False)
    monkeypatch.setattr(S3Storage, "from_env", classmethod(lambda cls: s3))
    captured = {}

    class Server:
        def __init__(self, address, coordinator, storage, registry):
            self.server_address = address
            captured.update(coordinator=coordinator, storage=storage, registry=registry)
        def serve_forever(self):
            pass

    monkeypatch.setattr(app, "UploadServer", Server)
    app.main()
    coordinator = captured["coordinator"]
    database = coordinator.db.execute("PRAGMA database_list").fetchone()[2]
    assert Path(database).name == f"signer-s3-{destination_id(s3.destination)}.sqlite3"
    assert captured["registry"] is not None and captured["storage"] is s3
    coordinator.db.close()


def test_s3_registration_switches_to_multipart_at_configured_cutoff(tmp_path, s3):
    coordinator = UploadCoordinator(tmp_path / "threshold.sqlite3", s3)
    ids = coordinator.start_round(None, "threshold-request")
    uid, rid = ids["upload_id"], ids["round_id"]
    coordinator.register_files(uid, rid, [dict(path="large.bin", size=128 * 1024**2 + 1, sha256="a" * 64)])
    assert coordinator.round_status(uid, rid)["files"][0]["mode"] == "multipart"
    coordinator.db.close()
