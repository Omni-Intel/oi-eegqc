"""Qt Quick presentation layer; scoring stays in the shared process service."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from PySide6.QtCore import (QObject, Property, Signal, Slot, QAbstractListModel,
                           QModelIndex, Qt, QThread, QTimer, QUrl, QSettings,
                           QCoreApplication, QEvent, QStandardPaths)
from PySide6.QtGui import QGuiApplication, QIcon, QFont, QDesktopServices
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from .desktop import BatchWorker, UpdateWorker, configure_logging
from .desktop_import import discover_files
from .desktop_service import inspect_channel_names, inspect_file, npy_ready, time_weighted_usable
from . import __version__
from .desktop_update import downloadable, download_installer, DownloadCancelled, launch_installer


class DownloadWorker(QThread):
    progress = Signal(int)

    def __init__(self, info, cache, parent=None):
        super().__init__(parent)
        self.info, self.cache = info, cache
        self.path, self.error = None, ""

    def run(self):
        try:
            self.path = download_installer(self.info, self.cache, self.progress.emit,
                                           self.isInterruptionRequested)
        except DownloadCancelled:
            self.error = "已取消下载"
        except Exception:
            self.error = "下载或校验失败，请重试"


class InstallWorker(QThread):
    def __init__(self, path, info, parent=None):
        super().__init__(parent)
        self.path, self.info, self.error = path, info, False

    def run(self):
        try:
            launch_installer(self.path, self.info)
        except Exception:
            self.error = True


def _as_str_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


class ChannelModel(QAbstractListModel):
    fields = ("name", "row", "chosen")
    roles = {Qt.ItemDataRole.UserRole + i + 1: name.encode() for i, name in enumerate(fields)}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def roleNames(self):
        return self.roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        key = self.roles.get(role)
        return self.rows[index.row()].get(key.decode()) if key else None

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def patch(self, index, **values):
        self.rows[index].update(values)
        self.dataChanged.emit(self.index(index), self.index(index), [])


class FileModel(QAbstractListModel):
    fields = ("label", "score", "state", "detail", "chosen", "ready")
    roles = {Qt.ItemDataRole.UserRole + i + 1: name.encode() for i, name in enumerate(fields)}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def roleNames(self):
        return self.roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        key = self.roles.get(role)
        return self.rows[index.row()].get(key.decode()) if key else None

    def append(self, path, metadata):
        n = len(self.rows)
        self.beginInsertRows(QModelIndex(), n, n)
        self.rows.append(dict(path=path, metadata=metadata, label=Path(path).name.lower(),
                              score="—", state="待评分", detail=path.lower(), chosen=False,
                              ready=False, report=None, error=""))
        self.endInsertRows()

    def patch(self, index, **values):
        self.rows[index].update(values)
        self.dataChanged.emit(self.index(index), self.index(index), [])

    def names(self):
        from collections import defaultdict
        groups = defaultdict(list)
        for row in self.rows:
            groups[Path(row["path"]).name.lower()].append(row["path"])
        roots = {}
        for name, paths in groups.items():
            if len(paths) > 1:
                try:
                    roots[name] = os.path.commonpath(paths)
                except ValueError:
                    roots[name] = ""
        for i, row in enumerate(self.rows):
            path = Path(row["path"])
            label = path.name.lower()
            if label in roots:
                try:
                    label = str(path.relative_to(roots[label])).lower()
                except ValueError:
                    label = str(path).lower()
            if row["label"] != label:
                self.patch(i, label=label)


class IntakeWorker(QThread):
    def __init__(self, paths, known):
        super().__init__()
        self.paths, self.known = paths, known
        self.items, self.errors = [], 0

    def run(self):
        try:
            paths, self.errors = discover_files(self.paths, self.isInterruptionRequested)
            for path in paths:
                if self.isInterruptionRequested():
                    break
                path = str(Path(path).resolve())
                key = os.path.normcase(path)
                if key in self.known:
                    continue
                self.known.add(key)
                try:
                    self.items.append((path, inspect_file(path)))
                except (ValueError, OSError):
                    self.errors += 1
        except Exception:
            self.errors += 1


class Controller(QObject):
    changed = Signal()
    parametersChanged = Signal()
    imported = Signal()
    updateCompleted = Signal(object)
    closeReady = Signal()

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.files = FileModel(self)
        self.channels = ChannelModel(self)
        self.worker = self.updater = None
        self.downloader = None
        self.installer_worker = None
        self._update_info = self._installer = None
        self._download_progress = 0
        self._busy = self._importing = self._stopping = self._closing = False
        self._notice = self._update_text = self._update_url = ""
        self._pending = []
        self._cursor = 0
        self._shared = None
        self._parameters = {}
        self._active = -1
        self._anchor = 0
        self._done = self._total = 0
        self._started = 0
        self._phase = ""
        self._errors = 0
        self._channels_open = False
        self._score_after_channels = False
        self._report_open = False
        self._report = {}
        self.store = settings if settings is not None else QSettings("Omni-Intelligence", "EEGQC")
        order = str(self.store.value("channels_first", "")).lower()
        self._order = 1 if order in ("true", "1") else 2 if order in ("false", "0") else 0
        self._mains = 60 if str(self.store.value("line_hz", 50)) in ("60", "60.0") else 50
        self._all_channels = str(self.store.value("all_channels", "true")).lower() not in ("false", "0")
        self._selected_channels = _as_str_list(self.store.value("selected_channels", []))
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.changed)

    model = Property(QObject, lambda self: self.files, constant=True)
    channelModel = Property(QObject, lambda self: self.channels, constant=True)
    version = Property(str, lambda self: __version__, constant=True)
    busy = Property(bool, lambda self: self._busy, notify=changed)
    stopping = Property(bool, lambda self: self._stopping, notify=changed)
    count = Property(int, lambda self: len(self.files.rows), notify=changed)
    selectedCount = Property(int, lambda self: sum(r["chosen"] for r in self.files.rows), notify=changed)
    canScore = Property(bool, lambda self: any(r["report"] is None for r in self.files.rows), notify=changed)
    orderIndex = Property(int, lambda self: self._order, notify=changed)
    mains = Property(int, lambda self: self._mains, notify=changed)
    allChannels = Property(bool, lambda self: self._all_channels, notify=changed)
    channelsOpen = Property(bool, lambda self: self._channels_open, notify=changed)
    reportOpen = Property(bool, lambda self: self._report_open, notify=changed)
    reportCard = Property("QVariantMap", lambda self: self._report, notify=changed)
    channelCount = Property(int, lambda self: len(self.channels.rows), notify=changed)
    selectedChannelCount = Property(int, lambda self: sum(r["chosen"] for r in self.channels.rows), notify=changed)
    parameters = Property("QVariantMap", lambda self: self._parameters, notify=parametersChanged)
    checking = Property(bool, lambda self: self.updater is not None, notify=changed)
    updateText = Property(str, lambda self: self._update_text, notify=changed)
    updateAvailable = Property(bool, lambda self: bool(self._update_url), notify=changed)
    downloading = Property(bool, lambda self: self.downloader is not None, notify=changed)
    downloadProgress = Property(int, lambda self: self._download_progress, notify=changed)
    updateReady = Property(bool, lambda self: self._installer is not None, notify=changed)
    canDownload = Property(bool, lambda self: self._update_info is not None and downloadable(self._update_info), notify=changed)
    portable = Property(bool, lambda self: not (Path(sys.executable).parent / "unins000.exe").is_file(), constant=True)

    @Property(str, notify=changed)
    def summary(self):
        if self._stopping:
            return "正在取消…"
        if self._importing:
            return "等待采样参数" if self._parameters else "正在导入…"
        if self._busy:
            elapsed = max(0, int(time.monotonic() - self._started)) if self._active >= 0 else 0
            return f"{self._phase or '准备'} · {self._done}/{self._total} · {elapsed//60:02}:{elapsed%60:02}"
        if self._notice:
            return self._notice
        scores = [r["report"].gqi for r in self.files.rows if r["report"] is not None]
        if scores:
            text = f"平均 {sum(scores)/len(scores):.0f}"
            share = time_weighted_usable(r["report"] for r in self.files.rows if r["report"] is not None)
            if share is not None:
                text += f" · 可用 {share:.0%}"
            return f"{text} · {len(scores)}/{len(self.files.rows)}"
        return f"{len(self.files.rows)} 个文件" if self.files.rows else ""

    @Slot("QVariantList")
    def addUrls(self, urls):
        paths = []
        for value in urls:
            url = QUrl(value)
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        self.add_paths(paths)

    def add_paths(self, paths):
        if self._busy or not paths:
            return
        self._busy = self._importing = True
        self._notice = ""
        self._shared = None
        known = {os.path.normcase(r["path"]) for r in self.files.rows}
        self.worker = IntakeWorker(paths, known)
        self.worker.finished.connect(self._scanned)
        self.worker.start()
        self.changed.emit()

    @Slot()
    def _scanned(self):
        worker = self.worker
        self.worker = None
        self._pending, self._errors = worker.items, worker.errors
        self._cursor = 0
        cancelled = worker.isInterruptionRequested()
        worker.deleteLater()
        if cancelled:
            self._pending = []
        self._consume()

    def _consume(self):
        # Yield between chunks so large folders never monopolize the scene thread.
        budget = 50
        while self._cursor < len(self._pending) and not self._stopping:
            path, declared = self._pending[self._cursor]
            params = dict(self._shared or {})
            params.update(declared)
            if Path(path).suffix.lower() == ".npy" and not npy_ready(params):
                self._parameters = {"name": Path(path).name.lower(), "sfreq": params.get("sfreq", ""),
                                    "unit": {"uV": 0, "mV": 1, "V": 2}.get(params.get("unit"), 0)}
                self.parametersChanged.emit()
                self.changed.emit()
                return
            self.files.append(path, params)
            self._cursor += 1
            budget -= 1
            if not budget:
                self.changed.emit()
                QTimer.singleShot(0, self._consume)
                return
        self.files.names()
        self._pending = []
        self._busy = self._importing = False
        if self._stopping:
            self._notice = "已取消导入"
        elif self._errors:
            self._notice = f"{len(self.files.rows)} 个文件 · {self._errors} 项无法读取"
        elif not self.files.rows:
            self._notice = "未找到支持的文件"
        self._stopping = False
        self.changed.emit()
        if self._closing:
            self.closeReady.emit()
        else:
            self.imported.emit()
            if not self._all_channels and self.canScore and not self._busy:
                QTimer.singleShot(0, self.openChannelSheet)

    @Slot(str, int, bool, result=bool)
    def acceptParameters(self, rate, unit, reuse):
        if not self._parameters:
            return False
        try:
            value = float(rate)
            if not 0 < value <= 1_000_000 or unit not in (0, 1, 2):
                return False
        except ValueError:
            return False
        path, declared = self._pending[self._cursor]
        params = dict(declared)
        params.update(sfreq=value, unit=("uV", "mV", "V")[unit])
        if reuse:
            self._shared = {"sfreq": value, "unit": params["unit"]}
        self.files.append(path, params)
        self._cursor += 1
        self._parameters = {}
        self.parametersChanged.emit()
        QTimer.singleShot(0, self._consume)
        return True

    @Slot()
    def skipParameters(self):
        if self._parameters:
            self._cursor += 1
            self._parameters = {}
            self.parametersChanged.emit()
            QTimer.singleShot(0, self._consume)

    @Slot(int, int)
    def select(self, index, modifiers=0):
        if self._busy or not 0 <= index < len(self.files.rows):
            return
        control = bool(modifiers & Qt.KeyboardModifier.ControlModifier.value)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier.value)
        for i, row in enumerate(self.files.rows):
            chosen = (min(self._anchor, index) <= i <= max(self._anchor, index)) if shift else (
                not row["chosen"] if control and i == index else row["chosen"] if control else i == index)
            if chosen != row["chosen"]:
                self.files.patch(i, chosen=chosen)
        if not shift:
            self._anchor = index
        self.changed.emit()

    @Slot()
    def selectAll(self):
        if not self._busy:
            for i in range(len(self.files.rows)):
                self.files.patch(i, chosen=True)
            self.changed.emit()

    @Slot(bool)
    def remove(self, all_rows=False):
        if self._busy:
            return
        for i in range(len(self.files.rows)-1, -1, -1):
            if all_rows or self.files.rows[i]["chosen"]:
                self.files.beginRemoveRows(QModelIndex(), i, i)
                self.files.rows.pop(i)
                self.files.endRemoveRows()
        self._anchor = 0
        self._notice = ""
        self.files.names()
        self.changed.emit()

    @Slot(int, int)
    def saveSettings(self, order, mains):
        if self._busy or order not in (0, 1, 2) or mains not in (50, 60):
            return
        self._order, self._mains = order, mains
        self.store.setValue("channels_first", ("", "true", "false")[order])
        self.store.setValue("line_hz", mains)
        self.changed.emit()

    def _save_channel_prefs(self):
        self.store.setValue("all_channels", "true" if self._all_channels else "false")
        self.store.setValue("selected_channels", self._selected_channels)

    def _channel_catalog(self):
        ordered, seen = [], set()
        for row in self.files.rows:
            if row["report"] is not None:
                continue
            try:
                names = inspect_channel_names(row["path"], row["metadata"])
            except Exception:
                continue
            for name in names:
                if name not in seen:
                    seen.add(name)
                    ordered.append(name)
        return ordered

    def _needs_channel_pick(self):
        if self._all_channels or not self.canScore:
            return False
        catalog = self._channel_catalog()
        if not catalog:
            return self.canScore
        chosen = set(self._selected_channels)
        return not chosen or not chosen.intersection(catalog)

    def _fill_channel_model(self):
        catalog = self._channel_catalog()
        previous = set(self._selected_channels)
        if previous and any(name in previous for name in catalog):
            marks = [name in previous for name in catalog]
        else:
            marks = [True] * len(catalog)
        self.channels.replace([
            dict(name=name, row=index, chosen=mark) for index, (name, mark) in enumerate(zip(catalog, marks))
        ])

    def _open_channel_sheet(self, score_after=False):
        if self._busy or not self.canScore:
            return
        self._score_after_channels = score_after
        self._fill_channel_model()
        self._channels_open = True
        self.changed.emit()

    @Slot(bool)
    def setAllChannels(self, enabled):
        if self._busy:
            return
        if enabled:
            self._all_channels = True
            self._channels_open = False
            self._score_after_channels = False
            self._save_channel_prefs()
            self.changed.emit()
            return
        self._all_channels = False
        self._save_channel_prefs()
        if self.canScore:
            self._open_channel_sheet(False)
        else:
            self.changed.emit()

    @Slot()
    def openChannelSheet(self):
        if not self._busy and self.canScore:
            self._all_channels = False
            self._open_channel_sheet(False)

    @Slot(int)
    def toggleChannel(self, index):
        if 0 <= index < len(self.channels.rows):
            self.channels.patch(index, chosen=not self.channels.rows[index]["chosen"])
            self.changed.emit()

    @Slot()
    def selectAllChannels(self):
        for i in range(len(self.channels.rows)):
            self.channels.patch(i, chosen=True)
        self.changed.emit()

    @Slot()
    def clearChannels(self):
        for i in range(len(self.channels.rows)):
            self.channels.patch(i, chosen=False)
        self.changed.emit()

    @Slot()
    def acceptChannels(self):
        names = [row["name"] for row in self.channels.rows if row["chosen"]]
        if not names:
            return
        self._selected_channels = names
        self._all_channels = False
        self._channels_open = False
        self._save_channel_prefs()
        self.changed.emit()
        if self._score_after_channels:
            self._score_after_channels = False
            QTimer.singleShot(0, self.scoreOrStop)

    @Slot()
    def cancelChannels(self):
        if not self._channels_open:
            return
        self._channels_open = False
        self._score_after_channels = False
        if not self._selected_channels:
            self._all_channels = True
        self._save_channel_prefs()
        self.changed.emit()

    def _report_card(self, row):
        label = row.get("label") or ""
        report = row.get("report")
        if report is None:
            return {
                "name": label,
                "score": "",
                "headline": row.get("error") or "还没评分",
                "layout": "",
                "dimensions": [],
                "notes": [{"text": row.get("detail") or "还没评分"}],
            }
        extras = getattr(report, "extras", None) or {}
        card = dict(extras.get("operator") or {})
        card["name"] = label
        card["score"] = f"{float(report.gqi):.0f}"
        card.setdefault("headline", "")
        card.setdefault("layout", "")
        card.setdefault("dimensions", [])
        card.setdefault("notes", [])
        return card

    @Slot(int)
    def openReport(self, index):
        if not 0 <= index < len(self.files.rows):
            return
        row = self.files.rows[index]
        if not row.get("ready"):
            return
        self._report = self._report_card(row)
        self._report_open = True
        self.changed.emit()

    @Slot()
    def openSelectedReport(self):
        chosen = [i for i, row in enumerate(self.files.rows) if row["chosen"] and row.get("ready")]
        if len(chosen) == 1:
            self.openReport(chosen[0])

    @Slot()
    def closeReport(self):
        if not self._report_open:
            return
        self._report_open = False
        self.changed.emit()

    @Slot()
    def scoreOrStop(self):
        if self.installer_worker:
            return
        if self._busy:
            self._stopping = True
            if self.worker:
                self.worker.requestInterruption()
            elif self._parameters:
                self._parameters = {}
                self.parametersChanged.emit()
                QTimer.singleShot(0, self._consume)
            self.changed.emit()
            return
        if not self._all_channels and self._needs_channel_pick():
            self._open_channel_sheet(True)
            return
        jobs = []
        keep = None if self._all_channels else list(self._selected_channels)
        for i, row in enumerate(self.files.rows):
            if row["report"] is not None:
                continue
            meta = dict(row["metadata"], line_hz=self._mains)
            if "channels_first" not in meta and self._order:
                meta["channels_first"] = self._order == 1
            if keep:
                meta["keep_channels"] = keep
            jobs.append((i, row["path"], meta))
            self.files.patch(i, error="", state="待评分", detail=row["path"].lower())
        if not jobs:
            return
        self._busy = True
        self._notice = self._phase = ""
        self._total, self._done = len(jobs), 0
        self.worker = BatchWorker(jobs)
        self.worker.started_file.connect(self._started_file)
        self.worker.phase_changed.connect(self._phase_changed)
        self.worker.scored.connect(self._scored)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled_file.connect(self._cancelled)
        self.worker.finished.connect(self._finished)
        self.worker.start()
        self.timer.start()
        self.changed.emit()

    @Slot(int)
    def _started_file(self, index):
        self._active, self._started = index, time.monotonic()
        self._phase_changed(index, "准备")

    @Slot(int, str)
    def _phase_changed(self, index, phase):
        self._phase = phase
        self.files.patch(index, state=phase + "…")
        self.changed.emit()

    @Slot(int, object)
    def _scored(self, index, report):
        extras = getattr(report, "extras", None) or {}
        detail = f"{extras['sfreq_hz']:g} 赫兹 · {report.duration_s:g} 秒"
        coverage = extras.get("frequency_coverage") or {}
        if coverage and not all(coverage.values()):
            detail += "\n采样率限制了部分频段检测"
        headline = (extras.get("operator") or {}).get("headline")
        if headline:
            detail += "\n" + headline
        self.files.patch(index, report=report, score=f"{report.gqi:.1f}", state="完成",
                         detail=detail, ready=True)
        self._done += 1
        self.changed.emit()

    @Slot(int, str)
    def _failed(self, index, message):
        self.files.patch(index, error=message, state="超时" if "超时" in message else "失败",
                         detail=message.lower(), ready=True)
        self._done += 1
        self.changed.emit()

    @Slot(int)
    def _cancelled(self, index):
        self.files.patch(index, state="已取消", detail="可重新评分")

    @Slot()
    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self._busy = self._stopping = False
        self._active = -1
        self.timer.stop()
        self.changed.emit()
        if self._closing:
            self.closeReady.emit()

    @Slot()
    def checkUpdate(self):
        if self.updater or self.downloader or self.installer_worker:
            return
        self._installer = None
        self._update_info = None
        self._update_text, self._update_url = "正在检查…", ""
        self.updater = UpdateWorker(self)
        self.updater.finished.connect(self._updated)
        self.updater.start()
        self.changed.emit()

    @Slot()
    def _updated(self):
        worker = self.updater
        info = worker.result
        self._update_info = info
        self.updater = None
        worker.deleteLater()
        self._update_text = {"current": "已是最新版本", "error": "暂时无法检查更新"}.get(
            info.status, f"发现新版本 {info.latest.lower()}")
        self._update_url = (info.asset_url or info.release_url) if info.status == "available" else ""
        self.changed.emit()
        self.updateCompleted.emit(info)

    @Slot()
    def openUpdate(self):
        if self._update_url:
            QDesktopServices.openUrl(QUrl(self._update_url))

    @Slot()
    def downloadUpdate(self):
        if self.downloader or self.updater or self.installer_worker or not self.canDownload or self._closing:
            return
        self._installer = None
        self._download_progress = 0
        self._update_text = "正在下载…"
        cache = Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation)) / "updates"
        self.downloader = DownloadWorker(self._update_info, cache, self)
        self.downloader.progress.connect(self._downloaded_progress)
        self.downloader.finished.connect(self._download_finished)
        self.downloader.start()
        self.changed.emit()

    @Slot(int)
    def _downloaded_progress(self, value):
        self._download_progress = value
        self.changed.emit()

    @Slot()
    def cancelDownload(self):
        if self.downloader:
            self.downloader.requestInterruption()
            self._update_text = "正在取消下载…"
            self.changed.emit()

    @Slot()
    def _download_finished(self):
        worker = self.downloader
        self._installer = worker.path
        self._update_text = worker.error or "已下载并校验"
        self.downloader = None
        worker.deleteLater()
        self.changed.emit()
        if self._closing and not self._busy:
            self.closeReady.emit()

    @Slot()
    def installUpdate(self):
        if self._busy or self.downloader or self.updater or self.installer_worker or not self._installer:
            return
        self._busy = True
        self._phase = "准备更新"
        self.installer_worker = InstallWorker(self._installer, self._update_info, self)
        self.installer_worker.finished.connect(self._install_started)
        self.installer_worker.start()
        self.changed.emit()

    @Slot()
    def _install_started(self):
        worker = self.installer_worker
        self.installer_worker = None
        self._busy = False
        worker.deleteLater()
        if worker.error:
            self._update_text = "无法启动安装，请重新下载或打开下载页"
            self._installer = None
            self.changed.emit()
            return
        self._closing = True
        self.closeReady.emit()

    @Slot(result=bool)
    def requestClose(self):
        if self.installer_worker:
            return False
        self._closing = True
        if self.downloader:
            self.cancelDownload()
            if self._busy:
                self.scoreOrStop()
            return False
        if self._busy:
            self.scoreOrStop()
            return False
        return True

    def shutdown(self):
        self._closing = True
        self.timer.stop()
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait()
        if self.updater:
            self.updater.wait()
        if self.downloader:
            self.downloader.requestInterruption()
            self.downloader.wait()
        if self.installer_worker:
            self.installer_worker.wait()


def create_engine(controller):
    engine = QQmlApplicationEngine()
    engine.setInitialProperties({"backend": controller})
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    return engine


def main():
    from multiprocessing import freeze_support
    freeze_support()
    app = QGuiApplication(sys.argv[:1])
    app.setOrganizationName("Omni-Intelligence")
    app.setApplicationName("EEGQC")
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    icon = QIcon(str(root / "assets" / "omni-intelli logo" / "OMNI_LOGO_100x100.ico"))
    app.setWindowIcon(icon)
    app.setFont(QFont("Microsoft YaHei", 10))
    QQuickStyle.setStyle("Basic")
    configure_logging()
    controller = Controller()
    engine = create_engine(controller)
    if not engine.rootObjects():
        return 1
    window = engine.rootObjects()[0]
    app.aboutToQuit.connect(controller.shutdown)
    args = sys.argv[1:]
    if args:
        import json
        from dataclasses import asdict
        output = Path(args[-1])
        def finish(payload, success=True):
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            window.grabWindow().save(str(output.with_suffix(".png")))
            app.exit(0 if success else 1)
        if args[0] == "--startup-check":
            def ready():
                heavy = [n for n in ("numpy", "scipy", "mne", "matplotlib") if n in sys.modules]
                finish(dict(title=window.title(), heavy_modules=heavy, icon_loaded=not icon.isNull(), ui="qml"), not heavy)
            QTimer.singleShot(400, ready)
        elif args[0] == "--update-check":
            controller.updateCompleted.connect(lambda info: QTimer.singleShot(100, lambda: finish(asdict(info), info.status != "error")))
            QTimer.singleShot(250, lambda: window.setProperty("settingsOpen", True))
            QTimer.singleShot(400, controller.checkUpdate)
        elif args[0] == "--verify":
            controller.imported.connect(controller.scoreOrStop)
            controller.add_paths(args[1:-1])
            timer = QTimer()
            def poll():
                if controller._busy:
                    return
                timer.stop()
                rows = controller.files.rows
                finish([r["report"].to_dict() if r["report"] else {"error": r["error"] or "未评分"} for r in rows],
                       bool(rows) and all(r["report"] for r in rows))
            timer.timeout.connect(poll)
            timer.start(150)
    result = app.exec()
    controller.shutdown()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
