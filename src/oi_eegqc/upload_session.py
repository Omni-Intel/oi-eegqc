import math
import re
import secrets
import threading
import time

from .upload_http import (
    MAX_SINGLE_PUT,
    PART_SIZE,
    PREFIX,
    ServiceError,
    SignClient,
    validate_put_url,
)


ROUND_PATTERN = r"r_\d{8}T\d{6}Z_[0-9a-f]{8}"


class UploadSession:
    def __init__(self, store, batch, data_directory, progress, client_factory=SignClient):
        self.store, self.batch, self.data_directory = store, batch, data_directory
        self.progress, self.client_factory = progress, client_factory
        self.stopped = threading.Event()
        self.commit_lock = threading.Lock()
        self.committing = False
        self.child = None
        self.client = None

    def cancel(self):
        with self.commit_lock:
            if self.child and not self.child.cancel():
                return False
            if self.committing:
                return False
            self.stopped.set()
            if self.client is not None and hasattr(self.client, "interrupt"):
                self.client.interrupt()
            return True

    def check_cancel(self):
        from .desktop_upload import UploadCancelled

        if self.stopped.is_set():
            raise UploadCancelled()

    def _ensure_round(self, client, entries):
        from .desktop_upload import ID_PATTERN, UploadError

        batch = self.batch
        request_id = batch.setdefault("round_request_id", secrets.token_hex(16))
        self.check_cancel()
        self.store.save(batch)
        if not batch.get("round_id"):
            if batch.get("allocation_pending") and not batch.get("upload_id"):
                raise UploadError("旧版首次申请结果未确认，请先恢复采集编号")
            predecessor = None
            for prior_request in batch.get("superseded_requests", []):
                self.check_cancel()
                recovered = client.start_round(batch.get("upload_id"), prior_request, predecessor)
                recovered_id = recovered.get("uploadId")
                if not isinstance(recovered_id, str) or not re.fullmatch(ID_PATTERN, recovered_id):
                    raise UploadError("上传服务返回了无效采集编号")
                if batch.get("upload_id") and batch["upload_id"] != recovered_id:
                    raise UploadError("上传服务返回了不一致的采集编号")
                batch["upload_id"] = recovered_id
                self.store.save(batch)
                predecessor = prior_request
            response = (client.start_round(batch.get("upload_id"), request_id, predecessor)
                        if predecessor else client.start_round(batch.get("upload_id"), request_id))
            upload_id, round_id = response.get("uploadId"), response.get("roundId")
            if not isinstance(upload_id, str) or not re.fullmatch(ID_PATTERN, upload_id):
                raise UploadError("上传服务返回了无效采集编号")
            if not isinstance(round_id, str) or not re.fullmatch(ROUND_PATTERN, round_id):
                raise UploadError("上传服务返回了无效轮次编号")
            if batch.get("upload_id") and batch["upload_id"] != upload_id:
                raise UploadError("上传服务返回了不一致的采集编号")
            batch.update(upload_id=upload_id, round_id=round_id, allocation_pending=False,
                         superseded_requests=[])
            self.store.save(batch)

        upload_id, round_id = batch["upload_id"], batch["round_id"]
        if not batch.get("round_sealed"):
            for offset in range(0, len(entries), 5000):
                self.check_cancel()
                group = entries[offset : offset + 5000]
                client.register_files(
                    upload_id,
                    round_id,
                    [
                        {
                            "path": entry["relative"],
                            "size": entry["size"],
                            "directory": entry["directory"],
                            "sha256": entry["sha256"],
                        }
                        for entry in group
                    ],
                )
            self.check_cancel()
            client.seal_round(upload_id, round_id, len(entries))
            batch["round_sealed"] = True
            self.store.save(batch)
        return upload_id, round_id

    def _reconcile(self, client, upload_id, round_id, entries):
        from .desktop_upload import UploadError

        status = client.round_status(upload_id, round_id)
        if status.get("uploadId") != upload_id or status.get("roundId") != round_id:
            raise UploadError("上传服务返回了不一致的轮次状态")
        remote = {item.get("path"): item for item in status.get("files", [])}
        if set(remote) != {entry["relative"] for entry in entries}:
            raise UploadError("上传服务中的文件清单与本地不一致")
        for entry in entries:
            item = remote[entry["relative"]]
            if item.get("size") != entry["size"] or item.get("sha256") != entry["sha256"]:
                raise UploadError("上传服务中的文件内容标识与本地不一致")
            entry["done"] = item.get("state") == "complete"
            if item.get("multipartUploadId"):
                entry["multipart_upload_id"] = item["multipartUploadId"]
                entry["part_size"] = item.get("partSize")
        self.store.save(self.batch)
        return status.get("state")

    def _signed_single(self, client, upload_id, round_id, entries):
        from .desktop_upload import UploadError

        response = client.sign_round(upload_id, round_id, [entry["relative"] for entry in entries])
        if response.get("uploadId") != upload_id or response.get("roundId") != round_id:
            raise UploadError("上传服务返回了不一致的签名目标")
        if response.get("prefix") != PREFIX + upload_id + "/" or not response.get("expiresAt"):
            raise UploadError("上传服务返回了无效签名信息")
        objects = response.get("objects")
        if not isinstance(objects, list) or len(objects) != len(entries):
            raise UploadError("签名文件清单不完整")
        expected, result = {entry["relative"] for entry in entries}, {}
        for item in objects:
            if not isinstance(item, dict) or item.get("path") not in expected or item["path"] in result:
                raise UploadError("签名文件路径不一致")
            key = PREFIX + upload_id + "/" + item["path"]
            if item.get("objectKey") != key:
                raise UploadError("签名目标不在本轮上传内")
            upload_key = item.get("uploadKey", key)
            if upload_key not in (key, PREFIX + ".staging/" + upload_id + "/" + round_id + "/" + item["path"]):
                raise UploadError("签名暂存目标不在本轮上传内")
            validate_put_url(item.get("putUrl", ""), upload_key)
            result[item["path"]] = (item["putUrl"], upload_key)
        return result

    def _put_single(self, client, upload_id, round_id, entry, url, done, total, started, transferred):
        from .desktop_upload import UploadError, fingerprint

        self.check_cancel()
        if not entry["directory"] and fingerprint(entry["source"], self.stopped.is_set) != {
            key: entry[key] for key in ("size", "mtime")
        }:
            raise UploadError("源文件已变化，已停止上传")
        url, key = url
        last = 0

        def report(sent):
            nonlocal last
            transferred[0] += max(0, sent - last)
            last = sent
            self.progress(
                {
                    "current": entry["relative"],
                    "done": done + sent,
                    "total": total,
                    "speed": transferred[0] / max(0.1, time.monotonic() - started),
                }
            )

        for attempt in range(2):
            try:
                etag = client.put(
                    url,
                    key,
                    None if entry["directory"] else entry["source"],
                    entry["size"],
                    self.stopped.is_set,
                    report,
                )
                break
            except ServiceError as error:
                if error.status_code not in (401, 403) or attempt:
                    raise
                url, key = self._signed_single(client, upload_id, round_id, [entry])[entry["relative"]]
                last = 0
        self.check_cancel()
        if not entry["directory"] and fingerprint(entry["source"], self.stopped.is_set) != {
            key: entry[key] for key in ("size", "mtime")
        }:
            raise UploadError("上传期间文件发生变化，不会确认文件完成")
        client.complete_files(
            upload_id,
            round_id,
            [{"path": entry["relative"], "size": entry["size"], "etag": etag or ""}],
        )

    @staticmethod
    def _part_size(size):
        minimum = math.ceil(size / 10000)
        block = 5 * 1024**2
        return max(PART_SIZE, math.ceil(minimum / block) * block)

    def _put_multipart(self, client, upload_id, round_id, entry, done, total, started, transferred):
        from .desktop_upload import UploadError, fingerprint

        self.check_cancel()
        if fingerprint(entry["source"], self.stopped.is_set) != {
            key: entry[key] for key in ("size", "mtime")
        }:
            raise UploadError("源文件已变化，已停止上传")
        info = client.start_multipart(
            upload_id, round_id, entry["relative"], self._part_size(entry["size"])
        )
        if info.get("path") != entry["relative"] or not info.get("multipartUploadId"):
            raise UploadError("上传服务返回了无效分片任务")
        part_size, part_count = info.get("partSize"), info.get("partCount")
        if not isinstance(part_size, int) or not isinstance(part_count, int):
            raise UploadError("上传服务返回了无效分片参数")
        entry.update(multipart_upload_id=info["multipartUploadId"], part_size=part_size)
        self.store.save(self.batch)
        listed = client.list_parts(upload_id, round_id, entry["relative"]).get("parts", [])
        uploaded = {part["partNumber"]: part for part in listed}
        existing = sum(part.get("size", 0) for part in uploaded.values())
        key = PREFIX + upload_id + "/" + entry["relative"]
        for offset in range(0, part_count, 1000):
            numbers = [
                number
                for number in range(offset + 1, min(offset + 1000, part_count) + 1)
                if number not in uploaded
            ]
            if not numbers:
                continue
            signed = client.sign_parts(upload_id, round_id, entry["relative"], numbers)
            urls = {part["partNumber"]: part["putUrl"] for part in signed.get("parts", [])}
            if set(urls) != set(numbers):
                raise UploadError("分片签名清单不完整")
            for number in numbers:
                self.check_cancel()
                part_offset = (number - 1) * part_size
                length = min(part_size, entry["size"] - part_offset)
                last = 0

                def report(sent, base=existing, number=number):
                    nonlocal last
                    transferred[0] += max(0, sent - last)
                    last = sent
                    self.progress(
                        {
                            "current": entry["relative"] + f"（分片 {number}/{part_count}）",
                            "done": done + base + sent,
                            "total": total,
                            "speed": transferred[0] / max(0.1, time.monotonic() - started),
                        }
                    )

                for attempt in range(2):
                    try:
                        client.put_part(
                            urls[number],
                            key,
                            entry["source"],
                            part_offset,
                            length,
                            self.stopped.is_set,
                            report,
                        )
                        break
                    except ServiceError as error:
                        if error.status_code not in (401, 403) or attempt:
                            raise
                        refreshed = client.sign_parts(
                            upload_id, round_id, entry["relative"], [number]
                        )
                        urls[number] = refreshed["parts"][0]["putUrl"]
                        last = 0
                existing += length
        self.check_cancel()
        if fingerprint(entry["source"], self.stopped.is_set) != {
            key: entry[key] for key in ("size", "mtime")
        }:
            raise UploadError("上传期间文件发生变化，不会合并分片")
        client.complete_multipart(upload_id, round_id, entry["relative"])

    def run(self):
        if self.batch.get("children"):
            return self._run_children()
        from .desktop_upload import UploadCancelled, UploadError, fingerprint, friendly_error, scan_sources

        batch, client = self.batch, None
        try:
            batch["status"], batch["error"] = "uploading", ""
            self.store.save(batch)
            from .score_cache import ScoreCache

            with ScoreCache(self.data_directory / "score-cache.sqlite3") as hash_cache:
                current = scan_sources(
                    batch["roots"], self.stopped.is_set, labels=batch.get("labels"),
                    hash_cache=hash_cache,
                )
            identity = lambda entry: (
                entry["source"],
                entry["relative"],
                entry["size"],
                entry["mtime"],
                entry["directory"],
                entry.get("sha256"),
            )
            legacy_identity = lambda entry: identity(entry)[:-1]
            if [legacy_identity(entry) for entry in current] != [
                legacy_identity(entry) for entry in batch["entries"]
            ]:
                raise UploadError("源文件夹已变化，请重新准备上传")
            for saved, observed in zip(batch["entries"], current):
                if not re.fullmatch(r"[0-9a-f]{64}", str(saved.get("sha256", ""))):
                    saved["sha256"] = observed.get("sha256")
            if [identity(entry) for entry in current] != [identity(entry) for entry in batch["entries"]]:
                raise UploadError("源文件夹已变化，请重新准备上传")
            for entry in current:
                if any(part in ("", ".", "..") for part in entry["relative"].rstrip("/").split("/")) or "\\" in entry["relative"]:
                    raise UploadError("文件相对路径无效")
            client = self.client_factory(self.data_directory)
            self.client = client
            self.check_cancel()
            client.health()
            upload_id, round_id = self._ensure_round(client, current)
            remote_state = self._reconcile(client, upload_id, round_id, batch["entries"])
            total = sum(entry["size"] for entry in current)
            done = sum(entry["size"] for entry in batch["entries"] if entry["done"])
            if remote_state == "complete":
                batch["status"], batch["current"], batch["error"] = "completed", "", ""
                self.progress({"current": "", "done": total, "total": total, "speed": 0})
                return batch
            started, transferred = time.monotonic(), [0]
            pending_single = [
                entry
                for entry in batch["entries"]
                if not entry["done"] and (entry["directory"] or entry["size"] <= MAX_SINGLE_PUT)
            ]
            for offset in range(0, len(pending_single), 5000):
                group = pending_single[offset : offset + 5000]
                urls = self._signed_single(client, upload_id, round_id, group)
                for entry in group:
                    batch["current"] = entry["relative"]
                    self.store.save(batch)
                    self._put_single(
                        client,
                        upload_id,
                        round_id,
                        entry,
                        urls[entry["relative"]],
                        done,
                        total,
                        started,
                        transferred,
                    )
                    self.check_cancel()
                    if not entry["directory"] and fingerprint(entry["source"], self.stopped.is_set) != {
                        key: entry[key] for key in ("size", "mtime")
                    }:
                        raise UploadError("上传期间文件发生变化，不会完成本轮上传")
                    entry["done"] = True
                    done += entry["size"]
                    self.store.save(batch)
            for entry in [
                item
                for item in batch["entries"]
                if not item["done"] and not item["directory"] and item["size"] > MAX_SINGLE_PUT
            ]:
                batch["current"] = entry["relative"]
                self.store.save(batch)
                self._put_multipart(
                    client, upload_id, round_id, entry, done, total, started, transferred
                )
                self.check_cancel()
                if fingerprint(entry["source"], self.stopped.is_set) != {
                    key: entry[key] for key in ("size", "mtime")
                }:
                    raise UploadError("上传期间文件发生变化，不会完成本轮上传")
                entry["done"] = True
                done += entry["size"]
                self.store.save(batch)
            self.check_cancel()
            if not all(entry["done"] for entry in batch["entries"]):
                raise UploadError("文件尚未全部成功，不能完成本轮上传")
            with ScoreCache(self.data_directory / "score-cache.sqlite3") as hash_cache:
                after = scan_sources(
                    batch["roots"], self.stopped.is_set, labels=batch.get("labels"),
                    hash_cache=hash_cache,
                )
            if [identity(entry) for entry in after] != [identity(entry) for entry in current]:
                raise UploadError("源文件夹已变化，不会完成本轮上传")
            with self.commit_lock:
                self.check_cancel()
                self.committing = True
            self.progress(
                {"current": "正在确认完成，请稍候", "done": done, "total": total, "speed": 0}
            )
            result = client.complete_round(upload_id, round_id)
            if result.get("state") != "complete" or result.get("roundId") != round_id:
                raise UploadError("上传服务未确认本轮完成")
            batch["status"], batch["current"] = "completed", ""
            self.progress({"current": "", "done": total, "total": total, "speed": 0})
        except Exception as error:
            batch["status"] = (
                "paused"
                if self.stopped.is_set() or isinstance(error, UploadCancelled)
                else "failed"
            )
            batch["error"] = "" if batch["status"] == "paused" else friendly_error(error)
        finally:
            self.client = None
            try:
                if client:
                    client.close()
            finally:
                self.store.save(batch)
        return batch

    def _run_children(self):
        from .desktop_upload import UploadCancelled, friendly_error

        batch, outer = self.batch, self
        total = sum(entry["size"] for entry in batch["entries"])
        completed = 0
        batch.update(status="uploading", error="")
        groups = {}
        for index, part in enumerate(batch["children"]):
            groups.setdefault(part.get("upload_id") or part["local_id"], []).append(index)
        try:
            for indices in groups.values():
                self.check_cancel()
                parts = [batch["children"][index] for index in indices]
                child = dict(
                    parts[0],
                    roots=[root for part in parts for root in part["roots"]],
                    entries=[entry for part in parts for entry in part["entries"]],
                    labels={
                        root: label
                        for part in parts
                        for root, label in part.get("labels", {}).items()
                    },
                )
                size = sum(entry["size"] for entry in child["entries"])
                if all(part["status"] == "completed" for part in parts):
                    completed += size
                    continue

                class ChildStore:
                    def save(self, value):
                        from pathlib import Path

                        for index in indices:
                            part = batch["children"][index]
                            root = Path(part["roots"][0])
                            for key in (
                                "upload_id",
                                "allocation_pending",
                                "round_id",
                                "round_request_id",
                                "superseded_requests",
                                "round_sealed",
                                "status",
                                "error",
                                "current",
                            ):
                                if key in value:
                                    part[key] = value[key]
                            part["entries"] = [
                                entry
                                for entry in value["entries"]
                                if root == Path(entry["source"])
                                or root in Path(entry["source"]).parents
                            ]
                        batch["entries"] = [
                            entry for part in batch["children"] for entry in part["entries"]
                        ]
                        outer.store.save(batch)

                def progress(value):
                    self.committing = self.child.committing
                    self.progress(dict(value, done=completed + value["done"], total=total))

                self.child = UploadSession(
                    ChildStore(), child, self.data_directory, progress, self.client_factory
                )
                if self.stopped.is_set():
                    self.child.cancel()
                result = self.child.run()
                self.child, self.committing = None, False
                if result["status"] != "completed":
                    batch.update(status=result["status"], error=result.get("error", ""))
                    return batch
                completed += size
            batch.update(status="completed", error="", current="")
            self.progress({"current": "", "done": total, "total": total, "speed": 0})
        except Exception as error:
            batch.update(
                status="paused" if isinstance(error, UploadCancelled) else "failed",
                error="" if isinstance(error, UploadCancelled) else friendly_error(error),
            )
        finally:
            self.child, self.committing = None, False
            self.store.save(batch)
        return batch
