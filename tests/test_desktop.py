import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from oi_eegqc.datasets.synthetic import synth_clean
from oi_eegqc.desktop import STYLE, Window
from oi_eegqc.desktop_service import score_file


def test_npy_scoring_units_axes_and_invalid_inputs(tmp_path):
    path = tmp_path / "测试.npy"
    data = synth_clean(4, 250, 5)
    np.save(path, data)
    report = score_file(path, sfreq=250)
    np.save(path, data.T / 1e6)
    converted = score_file(path, sfreq=250, unit="V", channels_first=False)
    assert converted.gqi == pytest.approx(report.gqi)
    assert converted.n_channels_used == 4
    with pytest.raises(ValueError, match="采样率"):
        score_file(path)
    np.save(path, np.empty((4, 0)))
    with pytest.raises(ValueError, match="有效"):
        score_file(path, sfreq=250)


def write_edf(path):
    """Small valid EDF fixture; avoids requiring an EDF export dependency."""
    def field(value, size):
        return str(value).encode("ascii").ljust(size)
    n = 4
    bdf = path.suffix.lower() == ".bdf"
    rail = 8388607 if bdf else 32767
    header = b"".join(field(v, s) for v, s in [
        ("0", 8), ("X", 80), ("X", 80), ("01.01.24", 8), ("00.00.00", 8),
        (256 + n * 256, 8), ("", 44), (5, 8), (1, 8), (n, 4)])
    for values, size in [(["Fp1", "Fp2", "C3", "C4"], 16), ([""]*n, 80),
                         (["uV"]*n, 8), ([-200]*n, 8), ([200]*n, 8),
                         ([-rail-1]*n, 8), ([rail]*n, 8), ([""]*n, 80),
                         ([250]*n, 8), ([""]*n, 32)]:
        header += b"".join(field(v, size) for v in values)
    data = np.clip(synth_clean(n, 250, 5) / 200 * rail, -rail-1, rail).astype("<i4" if bdf else "<i2")
    if bdf:
        header = b"\xffBIOSEMI" + header[8:]
    records = []
    for i in range(5):
        chunk = data[:, i*250:(i+1)*250].copy()
        records.append(chunk.view(np.uint8).reshape(-1, 4)[:, :3].tobytes() if bdf else chunk.tobytes())
    path.write_bytes(header + b"".join(records))


@pytest.mark.parametrize("suffix", [".edf", ".bdf"])
def test_edf_real_reader(tmp_path, suffix):
    path = tmp_path / ("测试" + suffix)
    write_edf(path)
    report = score_file(path)
    assert report.n_channels_used == 4
    assert report.duration_s == 5
    assert report.gqi > 50


def wait_for_batch(app, window):
    deadline = time.monotonic() + 30
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not window.busy


def test_batch_failure_retry_dedup_and_default(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    window = Window()
    good = tmp_path / "TEST.npy"
    bad = tmp_path / "BAD.npy"
    np.save(good, synth_clean(4, 250, 5))
    bad.write_bytes(b"broken")
    window.add_files([bad, good, good], {"sfreq": 250})
    assert len(window.entries) == 2
    assert window.table.item(1, 0).text() == "test.npy"
    assert window.entries[1].path == str(good.resolve())
    window.start_score()
    assert not window.choose_button.isEnabled()
    wait_for_batch(app, window)
    assert window.entries[0].error
    report = window.entries[1].report
    assert report is not None
    assert report.extras["adaptive"] is True
    assert report.duration_profile == "ultra_short"
    assert report.montage_profile == "low_density"
    assert window.score_button.isEnabled()
    np.save(bad, synth_clean(4, 250, 5))
    window.start_score()
    wait_for_batch(app, window)
    assert window.entries[0].report is not None
    assert window.entries[1].report is report
    assert not window.score_button.isEnabled()
    window.table.selectAll()
    window.remove_selected()
    assert not window.entries
    assert window.stack.currentIndex() == 0
    window.close()


def test_stop_preserves_pending_and_resume(tmp_path, monkeypatch):
    import threading
    import oi_eegqc.desktop as desktop
    app = QApplication.instance() or QApplication([])
    window = Window()
    paths = [tmp_path / f"{i}.npy" for i in range(3)]
    for path in paths:
        np.save(path, synth_clean(4, 250, 5))
    entered, release = threading.Event(), threading.Event()
    real_score = desktop.score_file
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real_score(*args, **kwargs)
    monkeypatch.setattr(desktop, "score_file", slow)
    window.add_files(paths, {"sfreq": 250})
    window.start_score()
    assert entered.wait(5)
    window.score_or_stop()
    release.set()
    wait_for_batch(app, window)
    assert sum(e.report is not None for e in window.entries) == 1
    monkeypatch.setattr(desktop, "score_file", real_score)
    window.start_score()
    wait_for_batch(app, window)
    assert all(e.report for e in window.entries)
    window.close()


def test_multiselect_dialog_and_npy_parameters(tmp_path, monkeypatch):
    import oi_eegqc.desktop as desktop
    app = QApplication.instance() or QApplication([])
    window = Window()
    paths = [tmp_path / f"{i}.npy" for i in range(2)]
    for path in paths:
        np.save(path, synth_clean(4, 250, 5))
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileNames", lambda *a: ([str(p) for p in paths], ""))
    calls = []
    def parameters(path, multiple):
        calls.append((path, multiple))
        return {"sfreq": 500, "unit": "mV", "channels_first": False}, True
    monkeypatch.setattr(window, "npy_parameters", parameters)
    window.choose_files()
    assert len(calls) == 1 and calls[0][1]
    assert all(e.metadata["sfreq"] == 500 for e in window.entries)
    window.close()


def test_no_uppercase_in_app_text(tmp_path):
    import re
    from PySide6.QtWidgets import QLabel, QPushButton
    app = QApplication.instance() or QApplication([])
    window = Window()
    path = tmp_path / "TEST.npy"
    np.save(path, synth_clean(4, 250, 5))
    window.add_files([path], {"sfreq": 250})
    window.start_score()
    wait_for_batch(app, window)
    texts = [window.windowTitle()]
    texts += [w.text() for cls in (QLabel, QPushButton) for w in window.findChildren(cls)]
    texts += [window.table.item(0, c).text() for c in range(3)]
    assert not any(re.search("[A-Z]", text) for text in texts)
    assert not hasattr(window, "settings")
    window.close()


def test_folder_drop_recursive_and_duplicate_names(tmp_path):
    from PySide6.QtCore import QMimeData, QUrl, QPoint, QPointF
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication([])
    for folder in (tmp_path / "one", tmp_path / "two"):
        folder.mkdir()
        write_edf(folder / "EEG.edf")
    (tmp_path / "notes.txt").write_text("ignore")
    window = Window()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(drop)
    assert drop.isAccepted()
    wait_for_batch(app, window)
    assert len(window.entries) == 2
    assert window.table.item(0, 0).text() != window.table.item(1, 0).text()
    window.add_files([tmp_path, tmp_path / "one"])
    wait_for_batch(app, window)
    assert len(window.entries) == 2
    window.start_score()
    wait_for_batch(app, window)
    assert all(e.report for e in window.entries)
    window.clear_files()
    assert not window.entries
    assert (tmp_path / "one" / "EEG.edf").is_file()
    window.close()


def test_empty_folder_and_import_cancellation(tmp_path, monkeypatch):
    import threading
    import oi_eegqc.desktop as desktop
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.add_files([tmp_path])
    wait_for_batch(app, window)
    assert window.status.text() == "未找到支持的文件"
    assert not window.score_button.isEnabled()
    entered, release = threading.Event(), threading.Event()
    def slow(paths, cancelled):
        entered.set()
        assert release.wait(5)
        assert cancelled()
        return [], 0
    monkeypatch.setattr(desktop, "discover_files", slow)
    window.add_files([tmp_path])
    assert entered.wait(5)
    window.score_or_stop()
    release.set()
    wait_for_batch(app, window)
    assert window.status.text() == "已取消导入"
    assert window.choose_button.isEnabled()
    window.close()


def test_discovery_permission_error_and_filtering(tmp_path, monkeypatch):
    import oi_eegqc.desktop_import as discovery
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (tmp_path / "ok.EDF").write_bytes(b"fixture")
    (tmp_path / "ignored.txt").write_text("ignore")
    original = discovery.os.scandir
    def guarded(path):
        if Path(path) == blocked:
            raise PermissionError("denied")
        return original(path)
    from pathlib import Path
    monkeypatch.setattr(discovery.os, "scandir", guarded)
    paths, errors = discovery.discover_files([tmp_path, tmp_path / "ok.EDF"])
    assert len(paths) == 1 and errors == 1
