"""Qt background lifecycle for the folder upload service."""
from copy import deepcopy
from pathlib import Path
import time
import re

from PySide6.QtCore import QObject, QThread, Signal, Slot, Property, QLockFile
from .desktop_upload import BatchStore, UploadSession, UploadCancelled, friendly_error


class UploadWorker(QThread):
    progress = Signal(object)

    def __init__(self, store, data_directory, batch=None, roots=None, scored=None, parent=None, new_acquisition=False):
        super().__init__(parent)
        self.store, self.data_directory = store, data_directory
        self.batch, self.roots, self.scored = deepcopy(batch), roots, scored
        self.session, self.error = None, ""
        self.new_acquisition = new_acquisition
        self._last_progress = 0

    def report(self, value):
        now = time.monotonic()
        if now - self._last_progress >= 0.1:
            self._last_progress = now
            self.progress.emit(value)

    def cancel(self):
        if self.session:
            if not self.session.cancel():
                return
        self.requestInterruption()

    def run(self):
        try:
            if self.roots is not None:
                self.batch = self.store.prepare(self.roots, self.scored, self.isInterruptionRequested,
                                                lambda name: self.report({"current": name}), self.new_acquisition)
            else:
                self.session = UploadSession(self.store, self.batch, self.data_directory, self.report)
                if self.isInterruptionRequested():
                    self.session.cancel()
                self.batch = self.session.run()
        except UploadCancelled:
            self.error = "已取消准备"
        except Exception as error:
            self.error = friendly_error(error)


class UploadController(QObject):
    changed = Signal()
    idle = Signal()

    def __init__(self, data_directory, parent=None):
        super().__init__(parent)
        self.data_directory = Path(data_directory)
        self.store = BatchStore(self.data_directory / "uploads")
        self.worker = None
        self.batch = None
        self._opened, self._error = False, ""
        self._show_batch_error = False
        self._progress = {}
        self._folder_cache_key, self._folder_cache = None, []
        self._selection = None
        self.lock = None
        try:
            self.batch = self.store.load()
        except Exception as error:
            self._error = friendly_error(error)

    opened = Property(bool, lambda self: self._opened, notify=changed)
    active = Property(bool, lambda self: self.worker is not None, notify=changed)
    canCancel = Property(bool, lambda self: self.active and not (self.worker.session and self.worker.session.committing), notify=changed)
    hasBatch = Property(bool, lambda self: self.batch is not None and self.batch["status"] != "completed", notify=changed)
    hasIssue = Property(bool, lambda self: bool(self._error), notify=changed)
    canStart = Property(bool, lambda self: not self.active and self.hasBatch and not self._error, notify=changed)

    @Property("QVariantMap", notify=changed)
    def info(self):
        batch = self.batch or {}
        entries = batch.get("entries", [])
        total = sum(e["size"] for e in entries)
        done = self._progress.get("done", sum(e["size"] for e in entries if e["done"]))
        status = batch.get("status", "")
        if status == "completed":
            done = total
        cache_key = (id(batch), tuple(batch.get("roots", [])), len(entries))
        if cache_key != self._folder_cache_key:
            self._folder_cache = []
            for root in batch.get("roots", []):
                members = [e for e in entries if not e["directory"] and Path(root) in Path(e.get("source", "")).parents]
                size = sum(e["size"] for e in members)
                self._folder_cache.append(dict(name=Path(root).name, path=root, count=len(members),
                                               size=f"{size / 1024 / 1024:.1f} 兆字节"))
            self._folder_cache_key = cache_key
        folders = self._folder_cache
        parts = batch.get("children") or [batch]
        for folder in folders:
            part = next((p for p in parts if folder["path"] in p.get("roots", [])), {})
            folder["uploadId"] = part.get("upload_id") or ""
        return dict(roots="\n".join(batch.get("roots", [])), uploadId=batch.get("upload_id") or "", uncertain=any(p.get("allocation_pending") for p in parts),
                    folders=folders, folderCount=len(folders),
                    preparing=bool(self.active and self.worker.roots is not None), completed=status == "completed",
                    started=any(p.get("upload_id") for p in parts), failed=self._show_batch_error and status == "failed",
                    count=sum(not e["directory"] for e in entries), size=f"{total / 1024 / 1024:.1f} 兆字节",
                    progress=min(1., done / total) if total else (1. if status == "completed" else 0.),
                    speed=f"{self._progress.get('speed', 0) / 1024 / 1024:.1f} 兆字节/秒",
                    current="" if status == "completed" else str(self._progress.get("current", batch.get("current", ""))).lower(),
                    error=self._error or (batch.get("error", "") if self._show_batch_error else ""),
                    status=("正在准备…" if self.active and self.worker.roots is not None else "正在上传…") if self.active else
                    {"ready": "待上传", "paused": "已暂停，可继续", "failed": "上传失败，可重试" if self._show_batch_error else "待继续上传", "completed": "上传完成"}.get(status, ""))

    def _acquire(self):
        try:
            self.store.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._error = "无法写入本地上传状态，请检查应用数据目录权限"
            self.changed.emit()
            return False
        lock = QLockFile(str(self.store.directory / "upload.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            self._error = "另一个窗口正在处理上传，请稍后重试"
            self.changed.emit()
            return False
        self.lock = lock
        return True

    def prepare(self, roots, scored, new_acquisition=False):
        self._opened = True
        if self.active:
            self.changed.emit()
            return
        self._show_batch_error = False
        self._error = ""
        if not self._acquire():
            return
        self._selection = (list(roots), dict(scored))
        self.worker = UploadWorker(self.store, self.data_directory, roots=roots, scored=scored, parent=self, new_acquisition=new_acquisition)
        self._launch()

    @Slot()
    def newAcquisition(self):
        if not self.active and self._selection:
            self.prepare(*self._selection, new_acquisition=True)

    @Slot(str)
    def resetUploadRecord(self, root):
        if self.active or not self.batch or not self._selection or root not in self.batch.get("roots", []):
            return
        if not self._acquire():
            return
        try:
            self.store.reset_folder(root)
        except Exception as error:
            self._error = friendly_error(error)
            self.changed.emit()
            return
        finally:
            self.lock.unlock()
            self.lock = None
        self.prepare(*self._selection)

    def _launch(self):
        self._progress = {}
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._finished)
        self.worker.start()
        self.changed.emit()

    @Slot(object)
    def _on_progress(self, value):
        self._progress = value
        self.changed.emit()

    @Slot()
    def _finished(self):
        worker = self.worker
        self._show_batch_error = worker.roots is None
        self.worker = None
        if worker.batch:
            self.batch = worker.batch
        self._error = worker.error
        worker.deleteLater()
        self.lock.unlock()
        self.lock = None
        self.changed.emit()
        self.idle.emit()

    @Slot()
    def start(self):
        if self.active or not self.hasBatch:
            return
        self._show_batch_error = False
        self._error = ""
        if not self._acquire():
            return
        try:
            self.batch = self.store.load()
            if not self.batch or self.batch["status"] == "completed":
                self.lock.unlock()
                self.lock = None
                self.changed.emit()
                return
        except Exception as error:
            self._error = friendly_error(error)
            self.lock.unlock()
            self.lock = None
            self.changed.emit()
            return
        self.worker = UploadWorker(self.store, self.data_directory, batch=self.batch, parent=self)
        self._launch()

    @Slot()
    def cancel(self):
        if self.worker:
            self.worker.cancel()

    @Slot()
    def close(self):
        self._opened = False
        self.changed.emit()

    @Slot(str, result=bool)
    def restoreUploadId(self, value):
        from .desktop_upload import ID_PATTERN
        value = value.strip()
        if self.active or not self.batch or not self.info["uncertain"] or not re.fullmatch(ID_PATTERN, value):
            return False
        if not self._acquire():
            return False
        try:
            batch = self.store.load()
            if batch["local_id"] != self.batch["local_id"]:
                return False
            pending = [p for p in (batch.get("children") or [batch]) if p.get("allocation_pending")]
            if len(pending) != 1:
                self._error = "请单独添加需要恢复编号的文件夹"
                self.changed.emit()
                return False
            pending[0].update(upload_id=value, allocation_pending=False, error="", status="paused")
            batch.update(error="", status="paused")
            self.store.save(batch)
            self.batch, self._error = batch, ""
            self.changed.emit()
            return True
        finally:
            self.lock.unlock()
            self.lock = None

    def shutdown(self):
        if self.worker:
            self.worker.cancel()
            self.worker.wait()
        if self.lock:
            self.lock.unlock()
