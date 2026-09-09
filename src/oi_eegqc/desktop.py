"""Quiet native batch scoring interface."""
from __future__ import annotations

import os
import sys
import logging
import time
from types import SimpleNamespace
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize, QStandardPaths, QSettings, QUrl, QCoreApplication, QEvent
from PySide6.QtGui import QFont, QIcon, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMenu, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QStyle, QStyleFactory, QMessageBox, QProgressBar,
)
from .desktop_service import inspect_file, npy_metadata, npy_ready
from .desktop_import import SUPPORTED, discover_files
from .desktop_process import ScoringProcess, DEFAULT_FILE_TIMEOUT_S


def configure_logging():
    """Bounded local diagnostics; never record signal arrays."""
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger("oi_eegqc.desktop")
    if logger.handlers:
        return
    try:
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / "desktop.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
    except OSError:
        logger.addHandler(logging.NullHandler())

STYLE = """
QWidget { color: #292929; font-size: 13px; }
QMainWindow, QDialog { background: #fafafa; }
QLabel { background: transparent; }
QLabel#muted { color: #888888; }
QProgressBar { border: none; background: #eeeeee; max-height: 3px; }
QProgressBar::chunk { background: #777777; }
QTableWidget { background: #fafafa; border: none; outline: none;
               selection-background-color: #eeeeee; selection-color: #292929; }
QTableWidget::item { padding: 0px 8px; border-bottom: 1px solid #eeeeee; }
QHeaderView::section { background: #fafafa; color: #888888; border: none;
                       border-bottom: 1px solid #dddddd; padding: 10px 8px; }
QDoubleSpinBox, QComboBox { background: white; border: 1px solid #dedede; border-radius: 5px; padding: 7px; }
QCheckBox { spacing: 8px; padding: 8px 0; }
QMenu { background: white; border: 1px solid #dedede; }
QMenu::item { padding: 8px 24px; }
QMenu::item:selected { background: #eeeeee; }
"""

DEFAULT_PREFS = {"channels_first": None, "line_hz": 50.0}


def default_prefs():
    return dict(DEFAULT_PREFS)


class ImportWorker(QThread):
    def __init__(self, paths):
        super().__init__()
        self.paths = paths
        self.result = ([], 0)
        self.cancelled = False
        self.error = ""

    def run(self):
        try:
            self.result = discover_files(self.paths, self.isInterruptionRequested)
        except Exception as exc:
            self.error = str(exc).lower()
            logging.getLogger("oi_eegqc.desktop").exception("Folder import failed")
        finally:
            self.cancelled = self.isInterruptionRequested()


class UpdateWorker(QThread):
    def run(self):
        from . import __version__
        from .desktop_update import check_update, UpdateInfo
        try:
            self.result = check_update(__version__)
        except Exception:
            logging.getLogger("oi_eegqc.desktop").exception("Update check failed")
            self.result = UpdateInfo(status="error", current=__version__)


@dataclass
class Entry:
    path: str
    metadata: dict = field(default_factory=dict)
    report: object = None
    error: str = ""


class ReportView:
    """Display plain report data without unpickling the scientific stack."""
    def __init__(self, payload):
        self.payload = payload
        value = payload.get("availability")
        self.availability = SimpleNamespace(value=value) if value is not None else None

    def __getattr__(self, name):
        try:
            return self.payload[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def to_dict(self):
        return self.payload


class BatchWorker(QThread):
    started_file = Signal(int)
    scored = Signal(int, object)
    failed = Signal(int, str)
    phase_changed = Signal(int, str)
    cancelled_file = Signal(int)

    def __init__(self, jobs, timeout_s=DEFAULT_FILE_TIMEOUT_S, process_factory=ScoringProcess):
        super().__init__()
        self.jobs = jobs
        self.timeout_s = timeout_s
        self.process_factory = process_factory

    def run(self):
        client = self.process_factory()
        try:
            for index, path, metadata in self.jobs:
                if self.isInterruptionRequested():
                    break
                self.started_file.emit(index)
                try:
                    kind, payload = client.score(path, metadata, self.isInterruptionRequested,
                                                 lambda phase: self.phase_changed.emit(index, phase), self.timeout_s)
                    if kind == "cancelled":
                        client.close()
                        self.cancelled_file.emit(index)
                        break
                    if kind == "result":
                        self.scored.emit(index, ReportView(payload))
                    else:
                        client.close()
                        logging.getLogger("oi_eegqc.desktop").warning("Scoring %s: %s", kind, payload)
                        self.failed.emit(index, payload)
                except Exception as exc:
                    client.close()
                    logging.getLogger("oi_eegqc.desktop").exception("Scoring failed")
                    self.failed.emit(index, str(exc).lower())
        finally:
            client.close()


class Window(QMainWindow):
    imported = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Omni-Intelligence EEG Quality Control App")
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        self.setWindowIcon(QIcon(str(root / "assets" / "omni-intelli logo" / "OMNI_LOGO_100x100.ico")))
        self.resize(640, 440)
        self.setMinimumSize(460, 320)
        self.setAcceptDrops(True)
        self.entries = []
        self.worker = None
        self.update_workers = set()
        self.busy = False
        self.importing = False
        self.close_pending = False
        self.total = self.done = 0
        self.active_index = None
        self.active_phase = "准备"
        self.active_started = 0
        self.prefs = self.load_prefs()
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(250)
        self.elapsed_timer.timeout.connect(self.refresh_activity)
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(18)
        self.stack = QStackedWidget()
        empty = QLabel("拖入文件或文件夹")
        empty.setObjectName("muted")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(empty)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["文件", "分数", "状态"])
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(50)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 78)
        self.table.setColumnWidth(2, 92)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.stack.addWidget(self.table)
        layout.addWidget(self.stack, 1)
        self.activity = QProgressBar()
        self.activity.setRange(0, 0)
        self.activity.setTextVisible(False)
        self.activity.setAccessibleName("正在处理")
        self.activity.hide()
        layout.addWidget(self.activity)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.choose_button = QPushButton("选择文件")
        self.choose_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.choose_button.clicked.connect(self.choose_files)
        actions.addWidget(self.choose_button)
        self.folder_button = QPushButton("选择文件夹")
        self.folder_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.folder_button.clicked.connect(self.choose_folder)
        actions.addWidget(self.folder_button)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        actions.addWidget(self.status, 1, Qt.AlignmentFlag.AlignCenter)
        self.prefs_button = QPushButton("设置")
        self.prefs_button.clicked.connect(self.edit_prefs)
        actions.addWidget(self.prefs_button)
        self.score_button = QPushButton("评分")
        self.score_button.setDefault(True)
        self.score_button.setEnabled(False)
        self.score_button.clicked.connect(self.score_or_stop)
        actions.addWidget(self.score_button)
        for button, width in ((self.choose_button, 106), (self.folder_button, 120), (self.prefs_button, 60), (self.score_button, 72)):
            button.setMinimumSize(width, 34)
            button.setIconSize(QSize(16, 16))
        layout.addLayout(actions)
        QShortcut(QKeySequence.StandardKey.Delete, self.table, activated=self.remove_selected)
        QShortcut(QKeySequence.StandardKey.Open, self, activated=self.choose_files)

    def npy_parameters(self, path, multiple, defaults=None):
        defaults = dict(defaults) if defaults is not None else npy_metadata(path)
        dialog = QDialog(self)
        dialog.setWindowTitle("采样参数")
        dialog.setMinimumWidth(300)
        layout = QVBoxLayout(dialog)
        name = QLabel(Path(path).name.lower())
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setWordWrap(True)
        layout.addWidget(name)
        layout.addWidget(QLabel("采样率"))
        rate = QDoubleSpinBox()
        rate.setDecimals(6)
        rate.setRange(0, 1000000)
        rate.setSpecialValueText("请输入")
        rate.setValue(defaults.get("sfreq", 0))
        rate.setSuffix(" 赫兹")
        layout.addWidget(rate)
        layout.addWidget(QLabel("单位"))
        unit = QComboBox()
        for label, value in [("微伏", "uV"), ("毫伏", "mV"), ("伏", "V")]:
            unit.addItem(label, value)
        unit.setCurrentIndex(max(0, unit.findData(defaults.get("unit", "uV"))))
        layout.addWidget(unit)
        reuse = QCheckBox("应用于其余数组文件")
        reuse.setVisible(multiple)
        layout.addWidget(reuse)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(rate.value() > 0)
        rate.valueChanged.connect(lambda value: ok.setEnabled(value > 0))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if not dialog.exec():
            return None
        return dict(sfreq=rate.value(), unit=unit.currentData()), reuse.isChecked()

    def load_prefs(self):
        prefs = default_prefs()
        app = QApplication.instance()
        if app is None or app.organizationName() != "Omni-Intelligence":
            return prefs
        store = QSettings("Omni-Intelligence", "EEGQC")
        try:
            prefs["line_hz"] = 60.0 if int(float(store.value("line_hz", 50))) == 60 else 50.0
        except (TypeError, ValueError):
            prefs["line_hz"] = 50.0
        order = store.value("channels_first", "")
        if order in (True, "true", "True", "1", 1):
            prefs["channels_first"] = True
        elif order in (False, "false", "False", "0", 0):
            prefs["channels_first"] = False
        else:
            prefs["channels_first"] = None
        return prefs

    def save_prefs(self):
        app = QApplication.instance()
        if app is None or app.organizationName() != "Omni-Intelligence":
            return
        store = QSettings("Omni-Intelligence", "EEGQC")
        store.setValue("line_hz", int(self.prefs["line_hz"]))
        order = self.prefs["channels_first"]
        store.setValue("channels_first", "" if order is None else ("true" if order else "false"))

    def edit_prefs(self):
        if self.busy:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("设置")
        dialog.setMinimumWidth(300)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("数组排列"))
        order = QComboBox()
        order.addItem("默认", "")
        order.addItem("通道 × 采样点", True)
        order.addItem("采样点 × 通道", False)
        current = self.prefs["channels_first"]
        order.setCurrentIndex(0 if current is None else (1 if current else 2))
        layout.addWidget(order)
        layout.addWidget(QLabel("电网频率"))
        mains = QComboBox()
        mains.addItem("50 赫兹", 50.0)
        mains.addItem("60 赫兹", 60.0)
        mains.setCurrentIndex(0 if self.prefs["line_hz"] != 60 else 1)
        layout.addWidget(mains)
        from . import __version__
        version = QLabel(f"当前版本 {__version__}")
        version.setObjectName("muted")
        layout.addWidget(version)
        check = QPushButton("检查更新")
        check.clicked.connect(lambda: self.start_update_check(dialog, check))
        layout.addWidget(check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if not dialog.exec():
            return
        order_value = order.currentData()
        self.prefs["channels_first"] = None if order_value in (None, "") else bool(order_value)
        self.prefs["line_hz"] = float(mains.currentData())
        self.save_prefs()

    def start_update_check(self, dialog, button):
        if getattr(dialog, "_update_worker", None) is not None:
            return
        button.setEnabled(False)
        button.setText("正在检查…")
        worker = UpdateWorker(self)
        self.update_workers.add(worker)
        dialog._update_worker = worker

        def done():
            self.update_workers.discard(worker)
            dialog._update_worker = None
            button.setEnabled(True)
            button.setText("检查更新")
            if dialog.isVisible() and self.isVisible():
                self.show_update_result(worker.result, dialog)

        worker.finished.connect(done)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def show_update_result(self, info, parent):
        if info.status == "available":
            notice = QMessageBox(parent)
            notice.setWindowTitle("检查更新")
            notice.setIcon(QMessageBox.Icon.Information)
            notice.setText(f"发现新版本 {info.latest}。")
            notice.setInformativeText("将打开下载页，用安装器覆盖当前版本。")
            open_btn = notice.addButton("打开", QMessageBox.ButtonRole.AcceptRole)
            notice.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            notice.exec()
            if notice.clickedButton() is open_btn:
                QDesktopServices.openUrl(QUrl(info.asset_url or info.release_url))
            return
        notice = QMessageBox(parent)
        notice.setWindowTitle("检查更新")
        if info.status == "current":
            notice.setIcon(QMessageBox.Icon.Information)
            notice.setText("已是最新版本。")
        else:
            notice.setIcon(QMessageBox.Icon.Warning)
            notice.setText("暂时无法检查更新。")
        notice.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
        notice.exec()

    def job_metadata(self, entry):
        meta = entry.metadata.copy()
        meta["line_hz"] = self.prefs["line_hz"]
        if "channels_first" not in meta and self.prefs["channels_first"] is not None:
            meta["channels_first"] = self.prefs["channels_first"]
        return meta

    def choose_files(self):
        if self.busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择文件", "", "脑电文件 (*.edf *.edf+ *.bdf *.npy)")
        self.add_files(paths)

    def choose_folder(self):
        if not self.busy:
            path = QFileDialog.getExistingDirectory(self, "选择文件夹")
            if path:
                self.add_files([path])

    def scan_finished(self, metadata):
        paths, errors = self.worker.result
        cancelled = self.worker.cancelled
        error = self.worker.error
        self.worker.deleteLater()
        self.worker = None
        self.busy = self.importing = False
        self.choose_button.setEnabled(True)
        self.folder_button.setEnabled(True)
        self.prefs_button.setEnabled(True)
        self.score_button.setText("评分")
        if self.close_pending:
            self.close()
            return
        if cancelled:
            self.update_controls()
            self.status.setText("已取消导入")
            return
        if error:
            self.update_controls()
            self.status.setText("导入失败，可重试")
            self.status.setToolTip(error)
            return
        self.add_files(paths, metadata)
        if errors:
            self.status.setText(f"{len(self.entries)} 个文件 · {errors} 项无法读取")
        elif not paths:
            self.status.setText("未找到支持的文件")

    def add_files(self, paths, metadata=None):
        if self.busy:
            return
        if any(Path(p).is_dir() for p in paths):
            self.busy = self.importing = True
            self.choose_button.setEnabled(False)
            self.folder_button.setEnabled(False)
            self.prefs_button.setEnabled(False)
            self.score_button.setText("取消")
            self.score_button.setEnabled(True)
            self.status.setText("正在查找文件…")
            self.worker = ImportWorker(paths)
            self.worker.finished.connect(lambda: self.scan_finished(metadata))
            self.worker.start()
            return
        known = {os.path.normcase(str(Path(e.path).resolve())) for e in self.entries}
        pending = []
        ignored = 0
        for path in paths:
            key = os.path.normcase(str(Path(path).resolve()))
            if key in known:
                continue
            if Path(path).suffix.lower() not in SUPPORTED or not Path(path).is_file():
                ignored += 1
                continue
            known.add(key)
            pending.append(str(Path(path).resolve()))
        shared = metadata
        remaining_npy = sum(Path(p).suffix.lower() == ".npy" for p in pending)
        for path in pending:
            params = {}
            try:
                declared = inspect_file(path)
            except (ValueError, OSError) as exc:
                notice = QMessageBox(QMessageBox.Icon.Warning, "参数无效",
                                     f"{Path(path).name.lower()}\n{str(exc).lower()}", parent=self)
                notice.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
                notice.exec()
                remaining_npy -= Path(path).suffix.lower() == ".npy"
                continue
            if Path(path).suffix.lower() == ".npy":
                remaining_npy -= 1
                if npy_ready(declared):
                    params = declared
                elif shared is None:
                    answer = self.npy_parameters(path, remaining_npy > 0, declared)
                    if answer is None:
                        continue
                    params, reuse = answer
                    if reuse:
                        shared = params
                else:
                    params = shared.copy()
                    params.update(declared)
            else:
                params = declared
            self.entries.append(Entry(path, params.copy()))
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, text in enumerate((Path(path).name.lower(), "—", "待评分")):
                item = QTableWidgetItem(text)
                item.setToolTip(path.lower() if col == 0 else "")
                self.table.setItem(row, col, item)
        self.update_controls()
        if ignored:
            self.status.setText(f"已忽略 {ignored} 项")
        self.imported.emit()

    def update_controls(self):
        # Show parent paths only when identical names need disambiguation.
        from collections import defaultdict
        groups = defaultdict(list)
        for entry in self.entries:
            groups[Path(entry.path).name.lower()].append(entry.path)
        roots = {}
        for name, peers in groups.items():
            if len(peers) > 1:
                try:
                    roots[name] = os.path.commonpath(peers)
                except ValueError:
                    roots[name] = ""
        for row, entry in enumerate(self.entries):
            path = Path(entry.path)
            label = path.name.lower()
            if label in roots:
                try:
                    label = str(path.relative_to(roots[label])).lower()
                except ValueError:
                    label = str(path).lower()
            self.table.item(row, 0).setText(label)
        self.stack.setCurrentIndex(1 if self.entries else 0)
        self.score_button.setEnabled(any(e.report is None for e in self.entries))
        self.update_summary()

    def update_summary(self):
        total = len(self.entries)
        scored = [e.report.gqi for e in self.entries if e.report is not None]
        completed = sum(e.report is not None or bool(e.error) for e in self.entries)
        failed = sum(bool(e.error) for e in self.entries)
        if scored:
            mean = sum(scored) / len(scored)
            self.status.setText(f"平均 {mean:.0f} · {len(scored)}/{total}")
        else:
            self.status.setText(f"{total} 个文件" if total else "")
        self.status.setToolTip(f"已完成 {completed} · 失败 {failed} · 待完成 {total-completed}" if total else "")
        if self.busy and not self.importing:
            self.status.setText(self.status.text() + f"\n评分 {self.done}/{self.total}")

    def context_menu(self, pos):
        if self.busy or not self.entries:
            return
        menu = QMenu(self)
        if self.table.selectionModel().selectedRows():
            menu.addAction("移除", self.remove_selected)
        menu.addAction("清空", self.clear_files)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def clear_files(self):
        if not self.busy:
            self.entries.clear()
            self.table.setRowCount(0)
            self.update_controls()

    def remove_selected(self):
        if self.busy:
            return
        for row in sorted((i.row() for i in self.table.selectionModel().selectedRows()), reverse=True):
            self.entries.pop(row)
            self.table.removeRow(row)
        self.update_controls()

    def score_or_stop(self):
        if self.busy:
            self.worker.requestInterruption()
            self.score_button.setEnabled(False)
            self.status.setText("正在取消…")
        else:
            self.start_score()

    def start_score(self):
        if self.busy:
            return
        jobs = [(i, e.path, self.job_metadata(e)) for i, e in enumerate(self.entries) if e.report is None]
        if not jobs:
            return
        self.busy = True
        self.activity.show()
        self.elapsed_timer.start()
        self.done, self.total = 0, len(jobs)
        self.choose_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self.prefs_button.setEnabled(False)
        self.score_button.setText("停止")
        self.score_button.setEnabled(True)
        for index, _, _ in jobs:
            self.entries[index].error = ""
            self.table.item(index, 1).setText("—")
            self.table.item(index, 2).setText("待评分")
            self.table.item(index, 2).setToolTip("")
        self.worker = BatchWorker(jobs)
        self.worker.started_file.connect(self.started_file)
        self.worker.scored.connect(self.show_report)
        self.worker.failed.connect(self.show_error)
        self.worker.phase_changed.connect(self.phase_changed)
        self.worker.cancelled_file.connect(self.cancelled_file)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def started_file(self, index):
        self.active_index = index
        self.active_phase = "准备"
        self.active_started = time.monotonic()
        self.refresh_activity()
        self.update_summary()

    def phase_changed(self, index, phase):
        if self.active_index == index:
            self.active_phase = phase
            self.refresh_activity()

    def refresh_activity(self):
        if self.active_index is None:
            return
        seconds = int(time.monotonic() - self.active_started)
        self.table.item(self.active_index, 2).setText(self.active_phase + "…")
        self.table.item(self.active_index, 2).setToolTip(f"已用时 {seconds} 秒 · 单文件最多 15 分钟")
        self.update_summary()
        self.status.setText(self.status.text() + f" · {seconds // 60:02}:{seconds % 60:02}")

    def cancelled_file(self, index):
        self.active_index = None
        self.table.item(index, 2).setText("已取消")
        self.table.item(index, 2).setToolTip("可重新评分")

    def show_report(self, index, report):
        self.active_index = None
        self.entries[index].report = report
        self.table.item(index, 1).setText(f"{report.gqi:.1f}")
        self.table.item(index, 2).setText("完成")
        rate = report.extras["sfreq_hz"]
        detail = f"{rate:g} 赫兹 · {report.duration_s:g} 秒"
        if not all(report.extras["frequency_coverage"].values()):
            detail += "\n采样率限制了部分频段检测"
        if report.reasons:
            detail += "\n" + "\n".join(report.reasons[:4])
        self.table.item(index, 1).setToolTip(detail)
        self.table.item(index, 2).setToolTip("")
        self.done += 1
        self.update_summary()

    def show_error(self, index, message):
        self.active_index = None
        self.entries[index].error = message
        self.table.item(index, 2).setText("超时" if "超时" in message else "失败")
        self.table.item(index, 2).setToolTip(message.lower())
        self.done += 1
        self.update_summary()

    def finished(self):
        self.busy = False
        self.active_index = None
        self.elapsed_timer.stop()
        self.activity.hide()
        self.worker.deleteLater()
        self.worker = None
        self.choose_button.setEnabled(True)
        self.folder_button.setEnabled(True)
        self.prefs_button.setEnabled(True)
        self.score_button.setText("评分")
        self.update_controls()
        if self.close_pending:
            self.close()

    def dragEnterEvent(self, event):
        if not self.busy and event.mimeData().hasUrls() and any(
            u.isLocalFile() and (Path(u.toLocalFile()).is_dir() or Path(u.toLocalFile()).suffix.lower() in SUPPORTED)
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.busy:
            self.add_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
            event.acceptProposedAction()

    def closeEvent(self, event):
        if self.busy:
            self.close_pending = True
            self.score_or_stop()
            event.ignore()
        else:
            event.accept()

    def shutdown(self):
        for update_worker in tuple(self.update_workers):
            update_worker.wait()
        # Also cover programmatic application quit, which can bypass closeEvent.
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait()


def widgets_main():
    # Required when launched through a console/gui entry point in a frozen app.
    from multiprocessing import freeze_support
    freeze_support()
    app = QApplication(sys.argv[:1])
    app.setOrganizationName("Omni-Intelligence")
    app.setApplicationName("EEGQC")
    configure_logging()
    styles = {name.lower(): name for name in QStyleFactory.keys()}
    if sys.platform == "win32":
        for name in ("windows11", "windowsvista", "windows"):
            if name in styles:
                app.setStyle(styles[name])
                break
    font = QFont()
    font.setFamilies(["Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC"])
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
    app.setStyleSheet(STYLE)
    window = Window()
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    if len(sys.argv) == 3 and sys.argv[1] == "--update-check":
        import json
        from dataclasses import asdict
        output = Path(sys.argv[2])
        def checked(info, dialog):
            output.write_text(json.dumps(asdict(info), ensure_ascii=False), encoding="utf-8")
            dialog.grab().save(str(output.with_suffix(".png")))
            dialog.accept()
            QTimer.singleShot(0, lambda: app.exit(0 if info.status != "error" else 1))
        window.show_update_result = checked
        def click_check():
            dialog = app.activeModalWidget()
            for button in dialog.findChildren(QPushButton):
                if button.text() == "检查更新":
                    button.click()
                    return
        def open_settings():
            QTimer.singleShot(0, click_check)
            window.edit_prefs()
        QTimer.singleShot(0, open_settings)
    if len(sys.argv) == 3 and sys.argv[1] == "--startup-check":
        import json
        output = Path(sys.argv[2])
        def ready():
            heavy = [name for name in ("numpy", "scipy", "mne", "matplotlib") if name in sys.modules]
            output.write_text(json.dumps({"title": window.windowTitle(), "heavy_modules": heavy,
                                          "icon_loaded": not window.windowIcon().isNull()}), encoding="utf-8")
            window.grab().save(str(output.with_suffix(".png")))
            app.exit(0 if not heavy else 1)
        QTimer.singleShot(0, ready)
    # Frozen integration check: one or more sources, then output.
    if len(sys.argv) >= 4 and sys.argv[1] == "--verify":
        import json
        output = Path(sys.argv[-1])
        window.imported.connect(window.start_score)
        window.add_files(sys.argv[2:-1])
        def finish():
            if window.busy:
                return
            timer.stop()
            results = [e.report.to_dict() if e.report else {"error": e.error or "未评分"} for e in window.entries]
            output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            window.grab().save(str(output.with_suffix(".png")))
            app.exit(0 if results and all(e.report for e in window.entries) else 1)
        timer = QTimer()
        timer.timeout.connect(finish)
        window.start_score()
        timer.start(100)
    result = app.exec()
    window.shutdown()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return result


def main():
    from .quick import main as quick_main
    return quick_main()


if __name__ == "__main__":
    raise SystemExit(main())
