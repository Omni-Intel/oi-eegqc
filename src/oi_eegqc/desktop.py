"""Quiet native batch scoring interface."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMenu, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QStyle, QStyleFactory,
)
from .desktop_service import score_file
from .desktop_import import SUPPORTED, discover_files

STYLE = """
QWidget { color: #292929; font-size: 13px; }
QMainWindow, QDialog { background: #fafafa; }
QLabel { background: transparent; }
QLabel#muted { color: #888888; }
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

class ImportWorker(QThread):
    def __init__(self, paths):
        super().__init__()
        self.paths = paths
        self.result = ([], 0)
        self.cancelled = False

    def run(self):
        self.result = discover_files(self.paths, self.isInterruptionRequested)
        self.cancelled = self.isInterruptionRequested()


@dataclass
class Entry:
    path: str
    metadata: dict = field(default_factory=dict)
    report: object = None
    error: str = ""


class BatchWorker(QThread):
    started_file = Signal(int)
    scored = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, jobs):
        super().__init__()
        self.jobs = jobs

    def run(self):
        for index, path, metadata in self.jobs:
            if self.isInterruptionRequested():
                break
            self.started_file.emit(index)
            try:
                # Always use the core's default adaptive configuration.
                self.scored.emit(index, score_file(path, **metadata))
            except Exception as exc:
                self.failed.emit(index, str(exc).lower())


class Window(QMainWindow):
    imported = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("omni intelligence · 脑电质检")
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        self.setWindowIcon(QIcon(str(root / "assets" / "omni-intelli logo" / "OMNI_LOGO_100x100.ico")))
        self.resize(640, 440)
        self.setMinimumSize(460, 320)
        self.setAcceptDrops(True)
        self.entries = []
        self.worker = None
        self.busy = False
        self.importing = False
        self.close_pending = False
        self.total = self.done = 0
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
        self.table.setHorizontalHeaderLabels(["文件", "分数", "结果"])
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
        self.score_button = QPushButton("评分")
        self.score_button.setDefault(True)
        self.score_button.setEnabled(False)
        self.score_button.clicked.connect(self.score_or_stop)
        actions.addWidget(self.score_button)
        for button, width in ((self.choose_button, 106), (self.folder_button, 120), (self.score_button, 72)):
            button.setMinimumSize(width, 34)
            button.setIconSize(QSize(16, 16))
        layout.addLayout(actions)
        QShortcut(QKeySequence.StandardKey.Delete, self.table, activated=self.remove_selected)
        QShortcut(QKeySequence.StandardKey.Open, self, activated=self.choose_files)

    def npy_parameters(self, path, multiple):
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
        rate.setRange(1, 1000000)
        rate.setValue(250)
        rate.setSuffix(" 赫兹")
        layout.addWidget(rate)
        layout.addWidget(QLabel("单位"))
        unit = QComboBox()
        for label, value in [("微伏", "uV"), ("毫伏", "mV"), ("伏", "V")]:
            unit.addItem(label, value)
        layout.addWidget(unit)
        layout.addWidget(QLabel("排列"))
        order = QComboBox()
        order.addItems(["通道 × 采样点", "采样点 × 通道"])
        layout.addWidget(order)
        reuse = QCheckBox("应用于其余数组文件")
        reuse.setVisible(multiple)
        layout.addWidget(reuse)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if not dialog.exec():
            return None
        return dict(sfreq=rate.value(), unit=unit.currentData(), channels_first=order.currentIndex() == 0), reuse.isChecked()

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
        self.worker.deleteLater()
        self.worker = None
        self.busy = self.importing = False
        self.choose_button.setEnabled(True)
        self.folder_button.setEnabled(True)
        self.score_button.setText("评分")
        if self.close_pending:
            self.close()
            return
        if cancelled:
            self.update_controls()
            self.status.setText("已取消导入")
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
        for path in pending:
            params = {}
            if Path(path).suffix.lower() == ".npy":
                if shared is None:
                    answer = self.npy_parameters(path, sum(Path(p).suffix.lower() == ".npy" for p in pending) > 1)
                    if answer is None:
                        continue
                    params, reuse = answer
                    if reuse:
                        shared = params
                else:
                    params = shared.copy()
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
        self.status.setText(f"{len(self.entries)} 个文件" if self.entries else "")

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
            self.status.setText("正在取消…" if self.importing else "当前文件完成后停止")
        else:
            self.start_score()

    def start_score(self):
        if self.busy:
            return
        jobs = [(i, e.path, e.metadata.copy()) for i, e in enumerate(self.entries) if e.report is None]
        if not jobs:
            return
        self.busy = True
        self.done, self.total = 0, len(jobs)
        self.choose_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self.score_button.setText("停止")
        self.score_button.setEnabled(True)
        for index, _, _ in jobs:
            self.entries[index].error = ""
            self.table.item(index, 2).setText("待评分")
            self.table.item(index, 2).setToolTip("")
        self.worker = BatchWorker(jobs)
        self.worker.started_file.connect(self.started_file)
        self.worker.scored.connect(self.show_report)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def started_file(self, index):
        self.table.item(index, 2).setText("评分中")
        self.status.setText(f"{self.done} / {self.total}")

    def show_report(self, index, report):
        self.entries[index].report = report
        self.table.item(index, 1).setText(f"{report.gqi:.1f}")
        label = {"Available": "可用", "Caution": "需留意", "Unavailable": "不可用"}[report.availability.value]
        self.table.item(index, 2).setText(label)
        self.done += 1

    def show_error(self, index, message):
        self.entries[index].error = message
        self.table.item(index, 2).setText("失败")
        self.table.item(index, 2).setToolTip(message.lower())
        self.done += 1

    def finished(self):
        self.busy = False
        self.worker.deleteLater()
        self.worker = None
        self.choose_button.setEnabled(True)
        self.folder_button.setEnabled(True)
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


def main():
    app = QApplication(sys.argv[:1])
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
    window.show()
    # Frozen integration check: one or more sources, then output.
    if len(sys.argv) >= 4 and sys.argv[1] == "--verify":
        import json
        output = Path(sys.argv[-1])
        window.imported.connect(window.start_score)
        window.add_files(sys.argv[2:-1], {"sfreq": 250})
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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
