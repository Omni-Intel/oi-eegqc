import pytest

from oi_eegqc.upload_signer import Conflict, UploadCoordinator

HASH = "a" * 64
EMPTY_HASH = __import__("hashlib").sha256(b"").hexdigest()


class Storage:
    def __init__(self):
        self.objects = {}
        self.uploads = {}
        self.begun = []

    def begin_round(self, upload_id, round_id, created_at):
        prefix = f"eeg/inbox/{upload_id}/"
        self.objects.pop(prefix + "_COMPLETE", None)
        self.objects[prefix + "_UPDATING"] = round_id
        self.begun.append((upload_id, round_id))

    def finish_round(self, upload_id, round_id, completed_at):
        prefix = f"eeg/inbox/{upload_id}/"
        self.objects[prefix + "_COMPLETE"] = round_id
        self.objects.pop(prefix + "_UPDATING", None)

    def sign_put(self, key):
        return "https://xiekp.tos-cn-beijing.volces.com/" + key + "?signed=1"

    def promote(self, source, destination, etag):
        self.objects[destination] = self.objects.get(source, b"test-content")

    def discard_staged(self, key):
        self.objects.pop(key, None)

    def create_multipart(self, key):
        upload_id = "tos-upload-" + str(len(self.uploads) + 1)
        self.uploads[upload_id] = {"key": key, "parts": {}}
        return upload_id

    def sign_part(self, key, upload_id, number):
        return self.sign_put(key) + f"&uploadId={upload_id}&partNumber={number}"

    def list_parts(self, key, upload_id):
        return [
            {"partNumber": number, "size": size, "etag": f"etag-{number}"}
            for number, size in sorted(self.uploads[upload_id]["parts"].items())
        ]

    def complete_multipart(self, key, upload_id, parts):
        self.objects[key] = sum(part["size"] for part in parts)
        return "combined-etag"


@pytest.fixture
def coordinator(tmp_path):
    storage = Storage()
    return UploadCoordinator(tmp_path / "state.sqlite3", storage), storage


def start(coordinator, upload_id=None, request_id="request-0001"):
    ids = coordinator.start_round(upload_id, request_id)
    return ids["upload_id"], ids["round_id"]


def test_round_request_is_idempotent_and_invalidates_old_complete(coordinator):
    service, storage = coordinator
    upload_id, round_id = start(service)
    storage.objects[f"eeg/inbox/{upload_id}/_COMPLETE"] = "old"
    service.db.execute("UPDATE collections SET current_round_id = NULL WHERE upload_id = ?", (upload_id,))
    service.db.execute("UPDATE rounds SET state = 'complete' WHERE round_id = ?", (round_id,))
    service.db.commit()

    second = service.start_round(upload_id, "request-0002")
    same = service.start_round(upload_id, "request-0002")
    assert second == same
    assert f"eeg/inbox/{upload_id}/_COMPLETE" not in storage.objects
    assert storage.objects[f"eeg/inbox/{upload_id}/_UPDATING"] == second["round_id"]


def test_sealed_manifest_retry_and_round_completion_are_idempotent(coordinator):
    service, storage = coordinator
    upload_id, round_id = start(service)
    manifest = [{"path": "session/a.npy", "size": 3, "directory": False, "sha256": HASH}]
    service.register_files(upload_id, round_id, manifest)
    service.seal_round(upload_id, round_id, 1)
    service.register_files(upload_id, round_id, manifest)
    signed = service.sign_single(upload_id, round_id, ["session/a.npy"])
    assert signed[0]["objectKey"] == f"eeg/inbox/{upload_id}/session/a.npy"
    service.mark_single_complete(
        upload_id, round_id, [{"path": "session/a.npy", "size": 3, "etag": "etag"}]
    )
    first = service.complete_round(upload_id, round_id)
    second = service.complete_round(upload_id, round_id)
    assert first["state"] == second["state"] == "complete"
    assert storage.objects[f"eeg/inbox/{upload_id}/_COMPLETE"] == round_id

    _, next_round = start(service, upload_id, "request-0002")
    service.register_files(upload_id, next_round, manifest)
    service.seal_round(upload_id, next_round, 1)
    assert service.round_status(upload_id, next_round)["files"][0]["state"] == "complete"
    service.complete_round(upload_id, next_round)


def test_changed_hash_requires_upload_but_does_not_delete_old_paths(coordinator):
    service, _ = coordinator
    upload_id, round_id = start(service)
    first = [{"path": "a.npy", "size": 3, "directory": False, "sha256": HASH}]
    service.register_files(upload_id, round_id, first)
    service.seal_round(upload_id, round_id, 1)
    service.mark_single_complete(upload_id, round_id, [{"path": "a.npy", "size": 3}])
    service.complete_round(upload_id, round_id)

    _, next_round = start(service, upload_id, "request-0002")
    changed = [
        {"path": "a.npy", "size": 3, "directory": False, "sha256": "b" * 64},
        {"path": "new.npy", "size": 4, "directory": False, "sha256": "c" * 64},
    ]
    service.register_files(upload_id, next_round, changed)
    service.seal_round(upload_id, next_round, 2)
    assert {item["path"]: item["state"] for item in service.round_status(upload_id, next_round)["files"]} == {
        "a.npy": "pending", "new.npy": "pending"
    }
    assert service.db.execute(
        "SELECT COUNT(*) FROM collection_files WHERE upload_id = ?", (upload_id,)
    ).fetchone()[0] == 1


def test_only_one_round_can_update_a_collection(coordinator):
    service, _ = coordinator
    upload_id, _ = start(service)
    with pytest.raises(Conflict, match="another upload round"):
        service.start_round(upload_id, "request-0002")


def test_empty_directory_keeps_its_object_key_slash(coordinator):
    service, _ = coordinator
    upload_id, round_id = start(service)
    manifest = [{"path": "session/empty/", "size": 0, "directory": True, "sha256": EMPTY_HASH}]
    service.register_files(upload_id, round_id, manifest)
    service.seal_round(upload_id, round_id, 1)
    signed = service.sign_single(upload_id, round_id, ["session/empty/"])[0]
    assert signed["path"] == "session/empty/"
    assert signed["objectKey"].endswith("/session/empty/")


def test_multipart_resume_lists_parts_and_requires_all_parts(coordinator):
    service, storage = coordinator
    upload_id, round_id = start(service)
    size = 5 * 1024**3 + 1
    service.register_files(
        upload_id, round_id, [{"path": "large.bin", "size": size, "directory": False, "sha256": HASH}]
    )
    service.seal_round(upload_id, round_id, 1)
    part_size = 1024**3
    info = service.start_multipart(upload_id, round_id, "large.bin", part_size)
    tos_id = info["multipartUploadId"]
    assert tos_id != round_id
    assert service.start_multipart(upload_id, round_id, "large.bin") == info
    storage.uploads[tos_id]["parts"] = {number: part_size for number in range(1, 6)}
    assert [part["partNumber"] for part in service.list_parts(upload_id, round_id, "large.bin")] == list(range(1, 6))
    with pytest.raises(Conflict, match="missing parts"):
        service.complete_multipart(upload_id, round_id, "large.bin")
    storage.uploads[tos_id]["parts"][6] = 1
    result = service.complete_multipart(upload_id, round_id, "large.bin")
    assert result["etag"] == "combined-etag"
    del storage.uploads[tos_id]
    assert service.complete_multipart(upload_id, round_id, "large.bin") == result
    assert service.round_status(upload_id, round_id)["files"][0]["state"] == "complete"


def test_replacement_rejects_old_round_and_late_put_cannot_overwrite(coordinator):
    service, storage = coordinator
    uid, old = start(service)
    manifest = [{"path": "data.bin", "size": 3, "sha256": HASH}]
    service.register_files(uid, old, manifest)
    service.seal_round(uid, old, 1)
    old_url = service.sign_single(uid, old, ["data.bin"])[0]
    ids = service.start_round(uid, "request-0002", "request-0001")
    assert service.start_round(None, "request-0002", "request-0001") == ids
    new = ids["round_id"]
    service.register_files(uid, new, [dict(manifest[0], sha256="b" * 64)])
    service.seal_round(uid, new, 1)
    signed = service.sign_single(uid, new, ["data.bin"])[0]
    storage.objects[signed["uploadKey"]] = b"new"
    service.mark_single_complete(uid, new, [{"path": "data.bin", "size": 3}])
    assert signed["uploadKey"] not in storage.objects
    storage.objects[old_url["uploadKey"]] = b"old"  # A delayed, still-valid presigned PUT.
    for action in (
        lambda: service.mark_single_complete(uid, old, [{"path": "data.bin", "size": 3}]),
        lambda: service.sign_single(uid, old, ["data.bin"]),
        lambda: service.complete_round(uid, old),
        lambda: service.start_round(uid, "request-0003", "request-0001"),
    ):
        with pytest.raises(Conflict):
            action()
    assert storage.objects[signed["objectKey"]] == b"new"
    assert f"eeg/inbox/{uid}/_COMPLETE" not in storage.objects
    service.complete_round(uid, new)
    assert storage.objects[f"eeg/inbox/{uid}/_COMPLETE"] == new


def test_replacement_reuses_partial_multipart_across_unregistered_round(coordinator):
    service, storage = coordinator
    uid, first = start(service)
    manifest = [{"path": "large.bin", "size": 5 * 1024**3 + 1, "sha256": HASH}]
    service.register_files(uid, first, manifest)
    info = service.start_multipart(uid, first, "large.bin", 1024**3)
    storage.uploads[info["multipartUploadId"]]["parts"][1] = 1024**3
    service.start_round(uid, "request-0002", "request-0001")
    third = service.start_round(uid, "request-0003", "request-0002")["round_id"]
    service.register_files(uid, third, manifest)
    assert service.start_multipart(uid, third, "large.bin") == info
    assert len(service.list_parts(uid, third, "large.bin")) == 1
    with pytest.raises(Conflict):
        service.complete_multipart(uid, first, "large.bin")
    fourth = service.start_round(uid, "request-0004", "request-0003")["round_id"]
    service.register_files(uid, fourth, [dict(manifest[0], sha256="b" * 64)])
    assert service.start_multipart(uid, fourth, "large.bin")["multipartUploadId"] != info["multipartUploadId"]


def test_partial_round_completed_file_is_reused_but_changed_content_is_not(coordinator):
    service, _ = coordinator
    uid, first = start(service)
    manifest = [{"path": "a.bin", "size": 3, "sha256": HASH},
                {"path": "b.bin", "size": 3, "sha256": HASH}]
    service.register_files(uid, first, manifest)
    service.mark_single_complete(uid, first, [{"path": "a.bin", "size": 3}])
    second = service.start_round(uid, "request-0002", "request-0001")["round_id"]
    service.register_files(uid, second, manifest)
    rows = service.round_status(uid, second)["files"]
    assert [row["state"] for row in rows] == ["complete", "pending"]
    third = service.start_round(uid, "request-0003", "request-0002")["round_id"]
    service.register_files(uid, third, [dict(manifest[0], sha256="b" * 64)])
    assert service.round_status(uid, third)["files"][0]["state"] == "pending"


def test_staging_cleanup_failure_does_not_lose_completed_file(coordinator, monkeypatch):
    service, storage = coordinator
    uid, rid = start(service)
    service.register_files(uid, rid, [{"path": "a.bin", "size": 3, "sha256": HASH}])
    def offline(key):
        raise TimeoutError()
    monkeypatch.setattr(storage, "discard_staged", offline)
    service.mark_single_complete(uid, rid, [{"path": "a.bin", "size": 3}])
    assert service.round_status(uid, rid)["files"][0]["state"] == "complete"
    service.mark_single_complete(uid, rid, [{"path": "a.bin", "size": 3}])
