import re
import threading
import time
from .upload_http import SignClient, ServiceError, MAX_FILE, PREFIX, validate_put_url


class UploadSession:
    def __init__(self, store, batch, data_directory, progress, client_factory=SignClient):
        self.store, self.batch, self.data_directory = store, batch, data_directory
        self.progress, self.client_factory = progress, client_factory
        self.stopped = threading.Event()
        self.commit_lock = threading.Lock()
        self.committing = False
        self.child = None

    def cancel(self):
        with self.commit_lock:
            if self.child and not self.child.cancel():
                return False
            if self.committing:
                return False
            self.stopped.set()
            return True

    def check_cancel(self):
        from .desktop_upload import UploadCancelled
        if self.stopped.is_set():
            raise UploadCancelled()

    def signed(self, client, entries):
        from .desktop_upload import UploadError, ID_PATTERN
        batch = self.batch
        self.check_cancel()
        first = not batch["upload_id"]
        if first:
            if batch.get("allocation_pending"):
                raise UploadError("首次申请结果未确认，请联系管理员恢复批次编号，不能自动新建")
            batch["allocation_pending"] = True
            self.store.save(batch)
        try:
            response = client.sign([e["relative"] for e in entries], batch["upload_id"])
        except ServiceError as error:
            if first and 400 <= error.status_code < 500:
                batch["allocation_pending"] = False
                self.store.save(batch)
            raise
        received = response.get("uploadId")
        if not isinstance(received, str) or not re.fullmatch(ID_PATTERN, received):
            raise UploadError("签名服务返回了无效批次编号")
        if first:
            batch["upload_id"] = received
            batch["allocation_pending"] = False
            self.store.save(batch)
        if received != batch["upload_id"] or response.get("prefix") != PREFIX + received + "/":
            raise UploadError("签名服务返回了不一致的上传目标")
        if not response.get("expiresAt"):
            raise UploadError("签名服务未返回有效期")
        files = response.get("objects") if "objects" in response else response.get("files")
        if not isinstance(files, list) or len(files) != len(entries):
            raise UploadError("签名文件清单不完整")
        expected = {e["relative"] for e in entries}
        signed = {}
        for item in files:
            if not isinstance(item, dict):
                raise UploadError("签名文件清单格式无效")
            path = item.get("path")
            if path not in expected or path in signed:
                raise UploadError("签名文件路径不一致")
            key = PREFIX + received + "/" + path
            if item.get("objectKey") != key:
                raise UploadError("签名目标不在本批次内")
            validate_put_url(item["putUrl"], key)
            signed[path] = item["putUrl"]
        return signed

    def run(self):
        if self.batch.get("children"):
            return self._run_children()
        from .desktop_upload import scan_sources, fingerprint, UploadError, UploadCancelled, friendly_error
        batch, client = self.batch, None
        try:
            batch["status"], batch["error"] = "uploading", ""
            self.store.save(batch)
            current = scan_sources(batch["roots"], self.stopped.is_set, labels=batch.get("labels"))
            identity = lambda e: (e["source"], e["relative"], e["size"], e["mtime"], e["directory"])
            if [identity(e) for e in current] != [identity(e) for e in batch["entries"]]:
                raise UploadError("源文件夹已变化，请恢复原文件后继续此批次")
            if any(e["size"] > MAX_FILE for e in current):
                raise UploadError("单个文件超过 5 吉字节，暂不支持，请联系管理员启用分片上传")
            for entry in current:
                if any(p in ("", ".", "..") for p in entry["relative"].rstrip("/").split("/")) or "\\" in entry["relative"]:
                    raise UploadError("文件相对路径无效")
            self.check_cancel()
            client = self.client_factory(self.data_directory)
            pending = [e for e in batch["entries"] if not e["done"]]
            total = sum(e["size"] for e in current)
            done = sum(e["size"] for e in batch["entries"] if e["done"])
            started, transferred = time.monotonic(), 0
            for offset in range(0, len(pending), 5000):
                group = pending[offset:offset + 5000]
                urls = self.signed(client, group)
                for entry in group:
                    self.check_cancel()
                    batch["current"] = entry["relative"]
                    self.store.save(batch)
                    if not entry["directory"] and fingerprint(entry["source"], self.stopped.is_set) != {k: entry[k] for k in ("size", "mtime")}:
                        raise UploadError("源文件已变化，已停止上传")
                    key = PREFIX + batch["upload_id"] + "/" + entry["relative"]
                    last = 0
                    def progress(sent):
                        nonlocal transferred, last
                        transferred += max(0, sent - last)
                        last = sent
                        self.progress(dict(current=entry["relative"], done=done + sent, total=total,
                                           speed=transferred / max(.1, time.monotonic() - started)))
                    for attempt in range(2):
                        try:
                            client.put(urls[entry["relative"]], key, None if entry["directory"] else entry["source"],
                                       entry["size"], self.stopped.is_set, progress)
                            break
                        except ServiceError as error:
                            if error.status_code not in (401, 403) or attempt:
                                raise
                            urls.update(self.signed(client, [e for e in group if not e["done"]]))
                            last = 0
                    self.check_cancel()
                    if not entry["directory"] and fingerprint(entry["source"], self.stopped.is_set) != {k: entry[k] for k in ("size", "mtime")}:
                        raise UploadError("上传期间文件发生变化，不会创建完成标记")
                    entry["done"] = True
                    done += entry["size"]
                    self.store.save(batch)
            self.check_cancel()
            if not all(e["done"] for e in batch["entries"]) or not batch["upload_id"]:
                raise UploadError("文件尚未全部成功，不能完成批次")
            after = scan_sources(batch["roots"], self.stopped.is_set, labels=batch.get("labels"))
            if [identity(e) for e in after] != [identity(e) for e in current]:
                raise UploadError("源文件夹已变化，不会创建完成标记")
            marker = client.complete(batch["upload_id"])
            key = PREFIX + batch["upload_id"] + "/_COMPLETE"
            validate_put_url(marker["putUrl"], key)
            with self.commit_lock:
                self.check_cancel()
                self.committing = True
            self.progress(dict(current="正在确认完成，请稍候", done=done, total=total, speed=0))
            client.put(marker["putUrl"], key, None, 0, lambda: False, lambda n: None)
            batch["status"], batch["current"] = "completed", ""
            self.progress(dict(current="", done=total, total=total, speed=0))
        except Exception as error:
            batch["status"] = "paused" if self.stopped.is_set() or isinstance(error, UploadCancelled) else "failed"
            batch["error"] = "" if batch["status"] == "paused" else friendly_error(error)
        finally:
            try:
                if client:
                    client.close()
            finally:
                self.store.save(batch)
        return batch

    def _run_children(self):
        """Reuse the existing per-batch uploader for each persistent folder."""
        from .desktop_upload import UploadCancelled, friendly_error
        batch, outer = self.batch, self
        total = sum(e["size"] for e in batch["entries"])
        completed = 0
        batch.update(status="uploading", error="")
        groups = {}
        for index, part in enumerate(batch["children"]):
            groups.setdefault(part.get("upload_id") or part["local_id"], []).append(index)
        try:
            for indices in groups.values():
                self.check_cancel()
                parts = [batch["children"][i] for i in indices]
                child = dict(parts[0], roots=[r for p in parts for r in p["roots"]],
                             entries=[e for p in parts for e in p["entries"]],
                             labels={r: label for p in parts for r, label in p.get("labels", {}).items()})
                size = sum(e["size"] for e in child["entries"])
                if all(p["status"] == "completed" for p in parts):
                    completed += size
                    continue
                class ChildStore:
                    def save(self, value):
                        from pathlib import Path
                        for index in indices:
                            part = batch["children"][index]
                            root = Path(part["roots"][0])
                            for key in ("upload_id", "allocation_pending", "status", "error", "current"):
                                if key in value:
                                    part[key] = value[key]
                            part["entries"] = [e for e in value["entries"] if root == Path(e["source"]) or root in Path(e["source"]).parents]
                        batch["entries"] = [e for part in batch["children"] for e in part["entries"]]
                        outer.store.save(batch)
                def progress(value):
                    self.committing = self.child.committing
                    self.progress(dict(value, done=completed + value["done"], total=total))
                self.child = UploadSession(ChildStore(), child, self.data_directory, progress, self.client_factory)
                if self.stopped.is_set():
                    self.child.cancel()
                result = self.child.run()
                self.child, self.committing = None, False
                if result["status"] != "completed":
                    batch.update(status=result["status"], error=result.get("error", ""))
                    return batch
                completed += size
            batch.update(status="completed", error="", current="")
            self.progress(dict(current="", done=total, total=total, speed=0))
        except Exception as error:
            batch.update(status="paused" if isinstance(error, UploadCancelled) else "failed",
                         error="" if isinstance(error, UploadCancelled) else friendly_error(error))
        finally:
            self.child, self.committing = None, False
            self.store.save(batch)
        return batch
