import json
from pathlib import Path
import pytest
from oi_eegqc.desktop_upload import BatchStore, UploadSession, UploadError, scan_sources
from oi_eegqc.upload_http import PREFIX, ServiceError, validate_put_url

SERVER_ID = "20260911T132500Z-a1b2c3d4"


class FakeClient:
    def __init__(self):
        self.objects, self.signs, self.calls = {}, [], []
        self.fail = None
        self.expire = False
        self.cancel = None
        self.complete_calls = 0
        self.unknown = False
        self.allocations = 0

    def health(self):
        pass

    def sign(self, paths, upload_id=None):
        self.signs.append((list(paths), upload_id))
        if self.unknown:
            raise TimeoutError()
        if not upload_id:
            self.allocations += 1
            upload_id = SERVER_ID if self.allocations == 1 else SERVER_ID + "-" + str(self.allocations)
        return dict(uploadId=upload_id or SERVER_ID, prefix=PREFIX + (upload_id or SERVER_ID) + "/",
                    expiresAt="2026-09-11T14:25:00Z", files=[
                        dict(path=p, objectKey=PREFIX + (upload_id or SERVER_ID) + "/" + p,
                             putUrl=self.url(PREFIX + (upload_id or SERVER_ID) + "/" + p)) for p in paths])

    def url(self, key):
        from urllib.parse import quote
        return "https://xiekp.tos-cn-beijing.volces.com/" + quote(key) + "?signature=do-not-persist"

    def put(self, url, key, source, size, cancelled, progress):
        validate_put_url(url, key)
        self.calls.append(key)
        if self.cancel:
            self.cancel()
        if cancelled():
            raise InterruptedError()
        if self.expire:
            self.expire = False
            raise ServiceError(403)
        if self.fail and key.endswith(self.fail):
            raise OSError("secret-response-must-not-leak")
        content = Path(source).read_bytes() if source else b""
        assert len(content) == size
        progress(size)
        self.objects[key] = content

    def complete(self, upload_id):
        self.complete_calls += 1
        return {"putUrl": self.url(PREFIX + upload_id + "/_COMPLETE")}

    def close(self):
        pass


@pytest.fixture
def batch(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    p = root / "a.npy"
    p.write_bytes(b"signal")
    (root / "z.txt").write_text("notes")
    store = BatchStore(tmp_path / "state")
    s = p.stat()
    return store, store.prepare([root], {str(p): (s.st_size, s.st_mtime_ns)}), root


def run(store, state, client):
    return UploadSession(store, state, store.directory.parent, lambda v: None, lambda p: client).run()


@pytest.mark.parametrize("stage", ["health", "sign"])
def test_offline_first_upload_can_retry_without_recovery(batch, stage):
    from oi_eegqc.upload_http import NetworkUnavailable
    store, state, root = batch
    client = FakeClient()
    original = getattr(client, stage)
    def offline(*args):
        raise NetworkUnavailable()
    setattr(client, stage, offline)
    assert run(store, state, client)["error"] == "网络不可用，请联网后重试"
    saved = store.load()
    assert not saved["allocation_pending"] and saved["upload_id"] is None
    setattr(client, stage, original)
    assert run(store, saved, client)["status"] == "completed"
    assert client.allocations == 1


def test_connect_failure_is_known_but_response_loss_is_uncertain(monkeypatch):
    import oi_eegqc.upload_http as module
    sent = []
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            raise OSError("offline")
        def request(self, *args, **kwargs):
            sent.append(True)
        def getresponse(self):
            raise TimeoutError()
        def close(self):
            pass
    monkeypatch.setattr(module.http.client, "HTTPSConnection", Connection)
    client = module.SignClient()
    with pytest.raises(module.NetworkUnavailable):
        client.sign(["a"])
    assert not sent
    monkeypatch.setattr(Connection, "connect", lambda self: None)
    with pytest.raises(TimeoutError):
        client.sign(["a"])
    assert sent


def test_reset_removes_history_only_for_selected_folder(batch, tmp_path):
    store, state, root = batch
    client = FakeClient()
    run(store, state, client)
    first_id = state["local_id"]
    newer = store.prepare([root], scored_files(root), new_acquisition=True)
    run(store, newer, client)
    other = tmp_path / "other"
    other.mkdir()
    (other / "b.npy").write_bytes(b"other")
    group = store.prepare([root, other], {**scored_files(root), **scored_files(other)})
    run(store, group, client)
    other_id = next(p["upload_id"] for p in group["children"] if str(other) in p["roots"])
    objects = dict(client.objects)
    store.reset_folder(root)
    assert not (store.directory / first_id / "state.json").exists()
    restarted = BatchStore(store.directory)
    assert restarted.prepare([root], scored_files(root))["upload_id"] is None
    assert restarted.prepare([other], scored_files(other))["upload_id"] == other_id
    assert client.objects == objects and (root / "a.npy").read_bytes() == b"signal"
    assert all(str(root) not in json.loads(p.read_text(encoding="utf-8")).get("roots", [])
               for p in store.directory.glob("*/state.json") if p.parent.name not in
               [restarted._folder_record(root)[1]["local_id"]])


def test_id_from_service_persisted_and_marker_last(batch):
    store, state, root = batch
    assert state["upload_id"] is None
    client = FakeClient()
    assert run(store, state, client)["status"] == "completed"
    assert client.signs[0][1] is None
    assert store.load()["upload_id"] == SERVER_ID
    assert client.calls[-1].endswith("/_COMPLETE")
    raw = (store.directory / state["local_id"] / "state.json").read_text()
    assert "signature=" not in raw and "do-not-persist" not in raw and "putUrl" not in raw


@pytest.mark.parametrize("field", ["objects", "files"])
def test_sign_response_list_formats(batch, field):
    store, state, root = batch
    client = FakeClient()
    original = client.sign
    def sign(paths, upload_id=None):
        response = original(paths, upload_id)
        response[field] = response.pop("files")
        return response
    client.sign = sign
    assert run(store, state, client)["status"] == "completed"
    assert client.complete_calls == 1


@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_target", "malformed"])
def test_objects_list_still_requires_exact_manifest(batch, fault):
    store, state, root = batch
    client = FakeClient()
    original = client.sign
    def sign(paths, upload_id=None):
        response = original(paths, upload_id)
        objects = response.pop("files")
        if fault == "missing":
            objects.pop()
        elif fault == "duplicate":
            objects[-1] = objects[0]
        elif fault == "wrong_target":
            objects[0]["objectKey"] = "outside/prefix"
        else:
            objects[0] = None
        response["objects"] = objects
        return response
    client.sign = sign
    assert run(store, state, client)["status"] == "failed"
    assert not client.calls and client.complete_calls == 0
    assert store.load()["upload_id"] == SERVER_ID


def test_failure_never_completes_and_retry_same_id(batch):
    store, state, root = batch
    client = FakeClient()
    client.fail = "z.txt"
    assert run(store, state, client)["status"] == "failed"
    assert client.complete_calls == 0 and not any(k.endswith("_COMPLETE") for k in client.objects)
    assert "secret-response" not in json.dumps(state)
    client.fail = None
    assert run(store, store.load(), client)["status"] == "completed"
    assert client.signs[1] == (["source/z.txt"], SERVER_ID)
    assert sum(k.endswith("a.npy") for k in client.calls) == 1


def test_cancel_no_marker_retains_id(batch):
    store, state, root = batch
    client = FakeClient()
    task = UploadSession(store, state, store.directory.parent, lambda v: None, lambda p: client)
    client.cancel = task.cancel
    assert task.run()["status"] == "paused"
    assert client.complete_calls == 0
    assert store.load()["upload_id"] == SERVER_ID


def test_expired_url_renews_with_same_id(batch):
    store, state, root = batch
    client = FakeClient()
    client.expire = True
    assert run(store, state, client)["status"] == "completed"
    assert len(client.signs) == 2 and client.signs[1][1] == SERVER_ID


def test_lost_initial_response_blocks_new_allocation(batch):
    store, state, root = batch
    client = FakeClient()
    client.unknown = True
    assert run(store, state, client)["status"] == "failed"
    assert run(store, store.load(), client)["status"] == "failed"
    assert len(client.signs) == 1


def test_sign_chunking_reuses_id(batch):
    store, state, root = batch
    client = FakeClient()
    task = UploadSession(store, state, store.directory.parent, lambda v: None, lambda p: client)
    first = [{"relative": str(n)} for n in range(5000)]
    task.signed(client, first)
    task.signed(client, [{"relative": "5000"}])
    assert client.signs[0][1] is None and client.signs[1][1] == SERVER_ID


@pytest.mark.parametrize("url", [
    "http://xiekp.tos-cn-beijing.volces.com/eeg/inbox/x/a?q=1",
    "https://evil.test/eeg/inbox/x/a?q=1",
    "https://xiekp.tos-cn-beijing.volces.com/eeg-external-2026-09-11/a?q=1",
])
def test_wrong_destination_rejected(url):
    with pytest.raises(ValueError):
        validate_put_url(url, "eeg/inbox/x/a")


def test_source_changes_block_completion(batch):
    store, state, root = batch
    (root / "new.txt").write_text("new")
    client = FakeClient()
    assert run(store, state, client)["status"] == "failed"
    assert not client.signs and not client.calls


def test_preparation_reads_metadata_not_file_contents(batch, monkeypatch):
    from oi_eegqc.desktop_upload import fingerprint
    store, state, root = batch
    def no_read(*args, **kwargs):
        raise AssertionError("preparation must not read file contents")
    monkeypatch.setattr(Path, "open", no_read)
    assert set(fingerprint(root / "a.npy")) == {"size", "mtime"}
    assert all("sha256" not in entry for entry in scan_sources([root]))


def test_old_hash_batches_resume_without_new_id(batch):
    store, state, root = batch
    state["upload_id"] = SERVER_ID
    for entry in state["entries"]:
        entry["sha256"] = "old-hash-is-not-required"
    store.save(state)
    client = FakeClient()
    assert run(store, store.load(), client)["status"] == "completed"
    assert client.signs[0][1] == SERVER_ID


def test_all_folder_files_including_media_are_uploaded(batch):
    store, state, root = batch
    media = root / "media"
    media.mkdir()
    (media / "photo.jpg").write_bytes(b"photo")
    (media / "video.mp4").write_bytes(b"video")
    (media / "impedance.png").write_bytes(b"impedance")
    s = (root / "a.npy").stat()
    state = store.prepare([root, media], {str(root / "a.npy"): (s.st_size, s.st_mtime_ns)})
    assert len(state["roots"]) == 1
    client = FakeClient()
    assert run(store, state, client)["status"] == "completed"
    for name in ("photo.jpg", "video.mp4", "impedance.png"):
        assert client.objects[PREFIX + SERVER_ID + "/source/media/" + name] == (media / name).read_bytes()
    assert len(client.objects) == 6  # five files and the completion marker


def test_limit_before_signing(batch, monkeypatch):
    import oi_eegqc.desktop_upload as module
    store, state, root = batch
    entry = dict(state["entries"][0], size=5 * 1024**3 + 1)
    monkeypatch.setattr(module, "scan_sources", lambda *a, **k: [entry])
    with pytest.raises(UploadError, match="暂不支持"):
        store.prepare([root], {})


def test_cancel_after_complete_url_does_not_put_marker(batch):
    store, state, root = batch
    client = FakeClient()
    task = UploadSession(store, state, store.directory.parent, lambda v: None, lambda p: client)
    original = client.complete
    def complete(uid):
        result = original(uid)
        task.cancel()
        return result
    client.complete = complete
    assert task.run()["status"] == "paused"
    assert not any(k.endswith("/_COMPLETE") for k in client.objects)


def test_server_id_saved_before_first_put(batch):
    store, state, root = batch
    client = FakeClient()
    original = client.put
    def put(*args):
        assert store.load()["upload_id"] == SERVER_ID
        return original(*args)
    client.put = put
    assert run(store, state, client)["status"] == "completed"


def test_anonymous_signing_and_put_is_streamed(tmp_path, monkeypatch):
    import io
    import oi_eegqc.upload_http as module
    connections = []
    class Connection:
        def __init__(self, host, **kwargs):
            self.host, self.headers, self.blocks = host, {}, []
            assert kwargs["context"].check_hostname
            connections.append(self)
        def connect(self):
            pass
        def request(self, method, route, body, headers):
            self.headers = headers
            assert self.host == "eeg-upload.kunpeng.blog"
            assert not any(k.lower() == "authorization" for k in headers)
        def putrequest(self, method, route):
            assert method == "PUT"
        def putheader(self, key, value):
            self.headers[key] = value
        def endheaders(self):
            pass
        def send(self, block):
            self.blocks.append(block)
            assert len(block) <= 256 * 1024
        def getresponse(self):
            class Response(io.BytesIO):
                status = 200
            return Response(b"{}")
        def close(self):
            pass
    monkeypatch.setattr(module.http.client, "HTTPSConnection", Connection)
    client = module.SignClient()
    client.sign(["a"])
    source = tmp_path / "a"
    source.write_bytes(b"x" * 600000)
    key = PREFIX + SERVER_ID + "/a"
    client.put(FakeClient().url(key), key, source, 600000, lambda: False, lambda n: None)
    assert "Authorization" not in connections[1].headers
    assert b"".join(connections[1].blocks) == source.read_bytes()
    client.complete(SERVER_ID)
    assert connections[-1].headers["Content-Length"] == "0"


def scored_files(*roots):
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
            for root in roots for p in root.rglob("*.npy")}


def test_same_folder_next_day_overwrites_and_appends_after_restart(batch):
    store, state, root = batch
    client = FakeClient()
    assert run(store, state, client)["status"] == "completed"
    upload_id = state["upload_id"]
    (root / "a.npy").write_bytes(b"second-day-more-eeg")
    (root / "photo.jpg").write_bytes(b"photo")
    (root / "session.json").write_bytes(b"{}")
    (root / "z.txt").unlink()
    restarted = BatchStore(store.directory)
    second = restarted.prepare([root], scored_files(root))
    assert second["upload_id"] == upload_id
    assert not any(e["done"] for e in second["entries"])
    assert run(restarted, second, client)["status"] == "completed"
    prefix = PREFIX + upload_id + "/source/"
    assert client.objects[prefix + "a.npy"] == b"second-day-more-eeg"
    assert client.objects[prefix + "photo.jpg"] == b"photo"
    assert client.objects[prefix + "session.json"] == b"{}"
    assert client.objects[prefix + "z.txt"] == b"notes"  # never delete cloud-only objects
    assert client.calls.count(prefix + "a.npy") == 2
    assert client.allocations == 1
    third = BatchStore(store.directory).prepare([root], scored_files(root))
    assert third["upload_id"] == upload_id
    assert not any(e["done"] for e in third["entries"])


def test_only_explicit_new_acquisition_allocates_new_id(batch):
    store, state, root = batch
    client = FakeClient()
    assert run(store, state, client)["status"] == "completed"
    previous_id = state["upload_id"]
    fresh = store.prepare([root], scored_files(root), new_acquisition=True)
    assert fresh["upload_id"] is None and fresh["local_id"] != state["local_id"]
    assert run(store, fresh, client)["status"] == "completed"
    assert fresh["upload_id"] != previous_id
    restored = BatchStore(store.directory).prepare([root], scored_files(root))
    assert restored["upload_id"] == fresh["upload_id"]
    assert PREFIX + previous_id + "/source/a.npy" in client.objects


def test_multiple_folders_keep_independent_ids_when_uploaded_separately(tmp_path):
    roots = [tmp_path / "first" / "collection", tmp_path / "second" / "collection"]
    for root in roots:
        root.mkdir(parents=True)
        (root / "data.npy").write_bytes(b"eeg")
    store = BatchStore(tmp_path / "state")
    state = store.prepare(roots, scored_files(*roots))
    client = FakeClient()
    progress = []
    session = UploadSession(store, state, store.directory.parent, progress.append, lambda p: client)
    assert session.run()["status"] == "completed"
    ids = [p["upload_id"] for p in state["children"]]
    assert len(set(ids)) == 2
    assert progress[-1]["done"] == progress[-1]["total"] == 6
    separate = BatchStore(store.directory).prepare([roots[1]], scored_files(roots[1]))
    assert separate["upload_id"] == ids[1]
    assert run(store, separate, client)["status"] == "completed"
    together = BatchStore(store.directory).prepare(roots, scored_files(*roots))
    assert [p["upload_id"] for p in together["children"]] == ids


def test_failed_folder_resumes_after_restart_without_new_id(batch):
    store, state, root = batch
    client = FakeClient()
    client.fail = "z.txt"
    assert run(store, state, client)["status"] == "failed"
    restored = BatchStore(store.directory)
    again = restored.prepare([root], scored_files(root))
    assert again["upload_id"] == state["upload_id"]
    assert any(e["done"] for e in again["entries"])
    client.fail = None
    assert run(restored, again, client)["status"] == "completed"
    assert client.allocations == 1


def test_lost_first_id_is_not_forgotten_by_folder_reselection(batch):
    store, state, root = batch
    client = FakeClient()
    client.unknown = True
    assert run(store, state, client)["status"] == "failed"
    again = BatchStore(store.directory).prepare([root], scored_files(root))
    assert again["allocation_pending"]
    assert run(store, again, client)["status"] == "failed"
    assert len(client.signs) == 1


def test_legacy_multi_folder_id_and_object_paths_are_preserved(tmp_path):
    roots = [tmp_path / "first" / "collection", tmp_path / "second" / "collection"]
    for root in roots:
        root.mkdir(parents=True)
        (root / "data.npy").write_bytes(b"eeg")
    store = BatchStore(tmp_path / "state")
    entries = scan_sources(roots)
    old = dict(version=2, local_id="legacy", upload_id=SERVER_ID, allocation_pending=False,
               roots=[str(p) for p in roots], entries=entries, status="completed", error="")
    store.save(old)
    state = store.prepare(roots, scored_files(*roots))
    assert all(p["upload_id"] == SERVER_ID for p in state["children"])
    assert [e["relative"] for e in state["entries"]] == [e["relative"] for e in entries]
    client = FakeClient()
    assert run(store, state, client)["status"] == "completed"
    assert client.complete_calls == 1
    assert client.calls[-1].endswith("/_COMPLETE")


def test_folder_mapping_survives_parent_state_interruption(tmp_path):
    from copy import deepcopy
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
        (root / "data.npy").write_bytes(b"eeg")
    store = BatchStore(tmp_path / "state")
    state = store.prepare(roots, scored_files(*roots))
    stale = deepcopy(state)
    client = FakeClient()
    assert run(store, state, client)["status"] == "completed"
    ids = [part["upload_id"] for part in state["children"]]
    store._write_record(stale)  # simulate an old parent checkpoint after child records were saved
    restored = BatchStore(store.directory).load()
    assert [part["upload_id"] for part in restored["children"]] == ids
    for index, root in enumerate(roots):
        assert BatchStore(store.directory).prepare([root], scored_files(root))["upload_id"] == ids[index]


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path identity")
def test_windows_path_case_reuses_id_and_object_names(batch):
    store, state, root = batch
    client = FakeClient()
    assert run(store, state, client)["status"] == "completed"
    other_case = Path(str(root).upper())
    again = BatchStore(store.directory).prepare([other_case], scored_files(other_case))
    assert again["upload_id"] == state["upload_id"]
    assert run(store, again, client)["status"] == "completed"
    assert client.allocations == 1
    assert all("/SOURCE/" not in key for key in client.objects)
