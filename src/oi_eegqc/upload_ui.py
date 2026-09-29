"""Qt background lifecycle for the folder upload service."""
from copy import deepcopy
from pathlib import Path
import time
import re

from PySide6.QtCore import QObject, QThread, Signal, Slot, Property, QLockFile, QTimer, QUrl
from .desktop_upload import BatchStore, UploadSession, UploadCancelled, friendly_error
from .archive_import import database_pending


class ParadigmWorker(QThread):
    def run(self):
        from .upload_http import SignClient
        self.rows, self.error = [], ''
        try:
            self.rows = SignClient().paradigms()
        except Exception as exc:
            self.error = '无法读取范式列表，请连接内网后刷新'
            if self.parent().telemetry:
                self.parent().telemetry.report('paradigm_fetch_failed', exc=exc)


class UploadWorker(QThread):
    progress = Signal(object)

    def __init__(self, store, data_directory, batch=None, roots=None, scored=None, parent=None, new_acquisition=False, sessions=None):
        super().__init__(parent)
        self.store, self.data_directory = store, data_directory
        self.batch, self.roots, self.scored = batch, roots, scored
        self.session, self.error = None, ""
        self.new_acquisition = new_acquisition
        self.imports = [dict(s) for s in sessions or []]
        self.sessions_provided = sessions is not None
        self.database_error = ''
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
                if self.parent().telemetry:self.parent().telemetry.report('upload_prepare_started',count=len(self.roots))
                from .archive_import import grade_sessions, attach_sessions, read_sessions
                self.progress.emit({'stage':'identifying','current':'正在识别被试和采集 Session'})
                if not self.sessions_provided:
                    self.imports=[s for root in self.roots if Path(root).is_dir() for s in read_sessions(root)]
                if any(s['paradigm_code'] and s['paradigm_code']!=self.store.paradigm['code'] for s in self.imports):
                    raise ValueError('图片采集包请选择 RSVP 图片 EEG 范式')
                if self.imports:
                    self.progress.emit({'stage':'scoring','current':f'正在评分 0/{len(self.imports)}'})
                    if self.parent().telemetry:self.parent().telemetry.report('archive_scoring_started',count=len(self.imports))
                    grade_sessions(self.imports, self.isInterruptionRequested,
                                   lambda name: self.report({'current': name}), getattr(self.parent().parent(), '_mains', 50))
                    if self.parent().telemetry:self.parent().telemetry.report('archive_scoring_completed',count=len(self.imports))
                self.progress.emit({'stage':'scanning','current':'正在扫描并核验上传文件'})
                self.batch = self.store.prepare(self.roots, self.scored, self.isInterruptionRequested,
                                                lambda name: self.report({"current": name}), self.new_acquisition)
                if self.imports:
                    attach_sessions(self.batch, self.imports)
                    self.store.save(self.batch)
                if self.parent().telemetry:self.parent().telemetry.report('upload_prepare_completed',count=len(self.batch['entries']))
            else:
                self.batch = self.store.load()
                from .archive_import import score_summary
                for part in self.batch.get('children') or [self.batch]:
                    for row in part.get('imports', []):
                        if 'signal_qc' in row:
                            row['signal_qc'] = score_summary(row['signal_qc'])
                if self.batch['status'] != 'completed':
                    self.session = UploadSession(self.store, self.batch, self.data_directory, self.report)
                    if self.isInterruptionRequested():
                        self.session.cancel()
                    self.batch = self.session.run()
                from .archive_import import sync_database
                from .upload_http import SignClient
                client = SignClient()
                try:
                    sync_database(self.store, self.batch, client, lambda name:self.report({'current':name}), self.isInterruptionRequested)
                except Exception as error:
                    self.database_error = ('文件已上传，平台未找到对应被试。请核对账号后重试入库'
                                           if getattr(error, 'status_code', None) == 404 else '文件已上传，统计入库失败。请点击“重试入库”')
                    if self.parent().telemetry:
                        self.parent().telemetry.report('database_import_failed', exc=error)
                finally:
                    client.close()
        except UploadCancelled:
            self.error = "已取消准备"
        except Exception as error:
            self.error = friendly_error(error)
            if self.parent().telemetry:
                self.parent().telemetry.report('upload_prepare_failed' if self.roots is not None else 'upload_failed', exc=error)


class UploadController(QObject):
    changed = Signal()
    idle = Signal()
    importRequested = Signal('QVariantList')
    graded = Signal(object)

    def __init__(self, data_directory, parent=None):
        super().__init__(parent)
        self.data_directory = Path(data_directory)
        self.telemetry = getattr(parent, 'telemetry', None)
        self.store = BatchStore(self.data_directory / "uploads-cos-beijing")
        self.worker = None
        self.batch = None
        self._opened, self._error = False, ""
        self._show_batch_error = False
        self._progress = {}
        self._folder_cache_key, self._folder_cache = None, []
        self._byte_cache_key, self._byte_totals = None, (0, 0)
        self._selection = None
        self._start_after_prepare = False
        self.lock = None
        self._paradigms, self._paradigm_index = [], -1
        self.registry_worker = None
        self._wanted_code = None
        self.subject_worker = None
        self._subject_text = ''
        try:
            self.batch = self.store.load()
        except Exception as error:
            self._error = friendly_error(error)
        QTimer.singleShot(0, self.refreshParadigms)

    paradigmNames = Property('QVariantList', lambda self: [f"{p['name']} · {p['code']}" for p in self._paradigms], notify=changed)
    paradigmIndex = Property(int, lambda self: self._paradigm_index, notify=changed)
    subjectText = Property(str, lambda self: self._subject_text, notify=changed)
    loadingParadigms = Property(bool, lambda self: self.registry_worker is not None, notify=changed)
    paradigmName = Property(str, lambda self: self._paradigms[self._paradigm_index]['name'] if self._paradigm_index >= 0 else '选择上传范式', notify=changed)

    @Property(str, notify=changed)
    def destinationText(self):
        if self._paradigm_index < 0:
            return '连接内网后选择上传范式'
        target = self._paradigms[self._paradigm_index]['destination']
        return f"cos://{target['bucket']}/{target['prefix'].strip('/')}/"

    @Slot()
    def refreshParadigms(self):
        if self.registry_worker is not None:
            return
        self.registry_worker = ParadigmWorker(self)
        self.registry_worker.finished.connect(self._registry_ready)
        self.registry_worker.start()
        self.changed.emit()

    @Slot()
    def _registry_ready(self):
        worker = self.registry_worker
        self.registry_worker = None
        if worker.error:
            self._error = worker.error
        else:
            settings = getattr(self.parent(), 'store', None)
            saved = settings.value('upload_paradigm', 'AVEEG-20260923') if settings is not None else 'AVEEG-20260923'
            old = self._paradigms[self._paradigm_index]['code'] if self._paradigm_index >= 0 else (self.batch or {}).get('paradigm_code', saved)
            self._paradigms = worker.rows
            index = next((i for i,p in enumerate(self._paradigms) if p['code'] == (self._wanted_code or old)), 0)
            if not self.active and self._paradigms:
                self.selectParadigm(index)
            elif not self._paradigms:
                self._paradigm_index = -1
                self._error = '数据库中没有启用的范式，请联系管理员配置'
        worker.deleteLater()
        self.changed.emit()

    @Slot(int)
    def selectParadigm(self, index):
        if self.active or not 0 <= index < len(self._paradigms):
            return
        self._paradigm_index = index
        row = self._paradigms[index]
        if self.telemetry:
            self.telemetry.paradigm = row['code']
        settings = getattr(self.parent(), 'store', None)
        if settings is not None:
            settings.setValue('upload_paradigm', row['code'])
        directory = self.data_directory / 'uploads-cos-beijing'
        if row['code'] != 'AVEEG-20260923':
            directory = self.data_directory / 'uploads' / row['code']
        self.store = BatchStore(directory, row)
        self._error = ''
        self._selection = None
        self._start_after_prepare = False
        self._progress = {}
        self._folder_cache_key = None
        try:
            self.batch = self.store.load()
        except Exception as error:
            self._error = friendly_error(error)
        self.changed.emit()

    @Slot('QVariantList')
    def addUrls(self, urls):
        self.importRequested.emit(urls)

    def selectCode(self, code):
        self._wanted_code = code
        index = next((i for i, p in enumerate(self._paradigms) if p['code'] == code), -1)
        if index >= 0:
            self.selectParadigm(index)

    def resolveSubjects(self, sessions):
        if self.subject_worker:
            return
        self.subject_worker = SubjectWorker(sorted({s['participant_number'] for s in sessions}), self)
        self.subject_worker.finished.connect(self._subjects_ready)
        self._subject_text = '正在匹配平台被试…'
        self.subject_worker.start()
        self.changed.emit()

    def _subjects_ready(self):
        worker = self.subject_worker
        self.subject_worker = None
        self._subject_text = worker.text
        worker.deleteLater()
        self.changed.emit()

    @Slot()
    def open(self):
        self._opened = True
        if not self.active:
            self.refreshParadigms()
        self.changed.emit()

    opened = Property(bool, lambda self: self._opened, notify=changed)
    active = Property(bool, lambda self: self.worker is not None, notify=changed)
    canCancel = Property(bool, lambda self: self.active and not (self.worker.session and self.worker.session.committing), notify=changed)
    hasBatch = Property(bool, lambda self: self.batch is not None and (self.batch["status"] != "completed" or database_pending(self.batch)), notify=changed)
    hasIssue = Property(bool, lambda self: bool(self._error), notify=changed)
    canStart = Property(bool, lambda self: self._paradigm_index >= 0 and not self.active and self.hasBatch and not self._error, notify=changed)

    @Property("QVariantMap", notify=changed)
    def progressInfo(self):
        batch = self.batch or {}
        entries = batch.get('entries', [])
        key = (id(batch), len(entries), batch.get('status'))
        if key != self._byte_cache_key:
            self._byte_cache_key = key
            self._byte_totals = (sum(e['size'] for e in entries), sum(e['size'] for e in entries if e['done']))
        total, initial_done = self._byte_totals
        done = self._progress.get('done', initial_done)
        status = batch.get('status', '')
        pending = database_pending(batch)
        preparing = bool(self.active and self.worker.roots is not None)
        if self.active:
            label = {'identifying':'正在识别 Session','scoring':'正在评分','scanning':'正在核验文件'}.get(self._progress.get('stage'), '正在准备') if preparing else '正在上传'
        else:
            completion = '上传和入库完成' if any(p.get('imports') for p in batch.get('children') or [batch]) else '上传完成'
            label = {'ready':'待上传', 'paused':'已暂停，可继续', 'failed':'上传失败，可重试', 'completed':'文件已上传，统计待入库' if pending else completion}.get(status, '')
        return dict(status=label, current=str(self._progress.get('current', '')) if self.active else '',
                    progress=1. if status == 'completed' else min(1., done / total) if total else 0.,
                    preparing=preparing, completed=status == 'completed' and not pending,
                    databasePending=pending, error=self._error or (batch.get('error', '') if self._show_batch_error else ''))

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
                members = [e for e in entries if not e["directory"] and (Path(root) == Path(e.get("source", "")) or Path(root) in Path(e.get("source", "")).parents)]
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
                    preparing=bool(self.active and self.worker.roots is not None), completed=status == "completed" and not database_pending(batch), databasePending=database_pending(batch),
                    started=any(p.get("upload_id") for p in parts), failed=self._show_batch_error and status == "failed",
                    count=sum(not e["directory"] for e in entries), size=f"{total / 1024 / 1024:.1f} 兆字节",
                    progress=min(1., done / total) if total else (1. if status == "completed" else 0.),
                    speed=f"{self._progress.get('speed', 0) / 1024 / 1024:.1f} 兆字节/秒",
                    current=str(self._progress.get("current", batch.get("current", ""))) if self.active or status != 'completed' else '',
                    error=self._error or (batch.get("error", "") if self._show_batch_error else ""),
                    status=({'identifying':'正在识别 Session…','scoring':'正在评分…','scanning':'正在核验文件…'}.get(self._progress.get('stage'),'正在准备…') if self.active and self.worker.roots is not None else "正在上传…") if self.active else
                    {"ready": "待上传", "paused": "已暂停，可继续", "failed": "上传失败，可重试" if self._show_batch_error else "待继续上传", "completed": "文件已上传，统计待入库" if database_pending(batch) else ("上传和入库完成" if any(p.get('imports') for p in parts) else "上传完成")}.get(status, ""))

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

    def prepare(self, roots, scored, new_acquisition=False, sessions=None, start_upload=False):
        if self._paradigm_index < 0:
            self._opened = True
            self._error = '请先连接内网并选择上传范式'
            self.changed.emit()
            return
        if getattr(self,'record_guard',None) and not self.record_guard(roots):
            self._error='该名单包含已移除记录，请重新选择';self.changed.emit();return
        if self.active:
            self.changed.emit()
            return
        self._show_batch_error = False
        self._error = ""
        if not self._acquire():
            return
        self._selection = (list(roots), dict(scored) if scored is not None else None)
        if any(s['paradigm_code'] and s['paradigm_code'] != self._paradigms[self._paradigm_index]['code'] for s in sessions or []):
            self._error = '图片采集包请选择 RSVP 图片 EEG 范式'
            self.lock.unlock(); self.lock = None; self.changed.emit(); return
        self.worker = UploadWorker(self.store, self.data_directory, roots=roots, scored=scored, parent=self, new_acquisition=new_acquisition, sessions=sessions)
        self._start_after_prepare = start_upload
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

    def detachLocalRecords(self, roots):
        """Detach the current queue, retaining historical upload identities on disk."""
        from .record_link import contains
        import secrets
        if self.active or not self._acquire():return False
        try:
            batch=self.store.load()
            if batch and any(contains(root,p) or contains(p,root) for root in roots for p in batch.get('roots',[])):
                # Never rewrite old children or their completed upload IDs.
                children=[p for p in batch.get('children',[]) if not any(contains(root,q) or contains(q,root) for root in roots for q in p.get('roots',[]))]
                if children:
                    batch=deepcopy(batch);batch.update(local_id=secrets.token_hex(16),children=children,
                        roots=[r for p in children for r in p['roots']],entries=[e for p in children for e in p['entries']])
                    self.store.save(batch)
                else:
                    (self.store.directory/'current.json').unlink(missing_ok=True);batch=None
                self.batch=batch;self._opened=False;self._error=''
            if self._selection:
                selected,scored=self._selection
                self._selection=([p for p in selected if not any(contains(r,p) or contains(p,r) for r in roots)],
                                 {p:v for p,v in scored.items() if not any(contains(r,p) for r in roots)} if scored is not None else None)
            self.changed.emit();return True
        except Exception as error:
            self._error=friendly_error(error);self.changed.emit();return False
        finally:
            self.lock.unlock();self.lock=None

    def _launch(self):
        self._progress = {}
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._finished)
        self.worker.start()
        self.changed.emit()

    @Slot(object)
    def _on_progress(self, value):
        self._progress.update(value)
        self.changed.emit()

    @Slot()
    def _finished(self):
        worker = self.worker
        self._show_batch_error = worker.roots is None
        self.worker = None
        if worker.batch:
            self.batch = worker.batch
        if worker.imports:
            self.graded.emit(worker.imports)
        self._error = worker.error
        if worker.database_error:
            self._subject_text = worker.database_error
        elif worker.roots is not None and worker.imports:
            missing = sum(s.get('signal_qc') is None for s in worker.imports)
            unmatched = self._subject_text if '平台未找到' in self._subject_text else ''
            self._subject_text = f"已识别 {len(worker.imports)} 个采集 Session，上传后自动入库" + (f"；{missing} 个评分暂缺，仍会上传原始文件" if missing else '') + (f'；{unmatched}' if unmatched else '')
            if missing and self.telemetry:
                self.telemetry.report('archive_score_failed', count=missing)
        elif (self.batch or {}).get('status') == 'completed' and not database_pending(self.batch):
            receipts = [r for p in self.batch.get('children') or [self.batch] for r in p.get('imports',[]) if r.get('receipt')]
            if receipts:
                self._subject_text = f"{len(receipts)} 个采集 Session 已入库，平台统计自动更新"
        if self.telemetry and not self._error and (self.batch or {}).get('status') == 'completed':
            self.telemetry.report('upload_completed', count=len(self.batch.get('entries', [])))
        worker.deleteLater()
        self.lock.unlock()
        self.lock = None
        self.changed.emit()
        if self._start_after_prepare and worker.roots is not None:
            self._start_after_prepare = False
            if not self._error and self.hasBatch:
                self.start()
                return
        self.idle.emit()

    @Slot()
    def start(self):
        if getattr(self,'record_guard',None) and self.batch and not self.record_guard(self.batch.get('roots',[])):
            self._error='该记录已从公共名单移除';self.changed.emit();return
        if self.active or not self.hasBatch:
            return
        self._show_batch_error = False
        self._error = ""
        if not self._acquire():
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
        if self.subject_worker:
            self.subject_worker.wait()
        if self.registry_worker:
            self.registry_worker.wait()
        if self.worker:
            self.worker.cancel()
            self.worker.wait()
        if self.lock:
            self.lock.unlock()


class SubjectWorker(QThread):
    def __init__(self, numbers, parent):
        super().__init__(parent)
        self.numbers, self.text = numbers, ''

    def run(self):
        from .upload_http import SignClient
        client = SignClient()
        try:
            rows = client.resolve_subjects(self.numbers)
            self.text = '；'.join(f"被试 {r['participant_number']} · {'平台已匹配' if r['matched'] else '平台未找到，请核对账号'}" for r in rows)
        except Exception as error:
            self.text = '平台被试匹配暂时不可用，入库时会重试'
            if self.parent().telemetry:
                self.parent().telemetry.report('subject_match_failed', exc=error)
        finally:
            client.close()
