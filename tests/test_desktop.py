import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from oi_eegqc.desktop import STYLE, Window
except ImportError as exc:
    pytest.skip(f"Qt unavailable: {exc}", allow_module_level=True)

from oi_eegqc.datasets.synthetic import synth_clean
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


def write_edf(path, sfreq=250):
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
                         ([sfreq]*n, 8), ([""]*n, 32)]:
        header += b"".join(field(v, size) for v in values)
    data = np.clip(synth_clean(n, sfreq, 5) / 200 * rail, -rail-1, rail).astype("<i4" if bdf else "<i2")
    if bdf:
        header = b"\xffBIOSEMI" + header[8:]
    records = []
    for i in range(5):
        chunk = data[:, i*sfreq:(i+1)*sfreq].copy()
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
    assert window.table.item(1, 1).text()
    assert report.extras["adaptive"] is True
    assert report.duration_profile == "ultra_short"
    assert report.montage_profile == "low_density"
    assert "1/2" in window.status.text()
    assert window.score_button.isEnabled()
    np.save(bad, synth_clean(4, 250, 5))
    window.start_score()
    wait_for_batch(app, window)
    assert window.entries[0].report is not None
    assert window.entries[1].report is report
    assert not window.score_button.isEnabled()
    assert "2/2" in window.status.text()
    window.table.selectAll()
    window.remove_selected()
    assert not window.entries
    assert window.stack.currentIndex() == 0
    window.close()


def test_stop_preserves_pending_and_resume(tmp_path, monkeypatch):
    import oi_eegqc.desktop as desktop
    from oi_eegqc.desktop_process import ScoringProcess
    from process_workers import blocked_scores
    app = QApplication.instance() or QApplication([])
    window = Window()
    paths = [tmp_path / f"{i}.npy" for i in range(3)]
    for path in paths:
        np.save(path, synth_clean(4, 250, 5))
    real_worker = desktop.BatchWorker
    monkeypatch.setattr(desktop, "BatchWorker", lambda jobs: real_worker(
        jobs, process_factory=lambda: ScoringProcess(target=blocked_scores)))
    window.add_files(paths, {"sfreq": 250})
    window.start_score()
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(20)
    deadline = time.monotonic() + 10
    while window.active_phase != "评分" and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window.active_phase == "评分"
    assert ticks  # Main event loop stays alive while a child is blocked.
    timer.stop()
    start = time.monotonic()
    window.score_or_stop()
    wait_for_batch(app, window)
    assert time.monotonic() - start < 3
    assert not any(e.report for e in window.entries)
    assert window.table.item(0, 2).text() == "已取消"
    monkeypatch.setattr(desktop, "BatchWorker", real_worker)
    window.start_score()
    wait_for_batch(app, window)
    assert all(e.report for e in window.entries)
    window.close()


def test_timeout_and_process_crash_continue_queue():
    from oi_eegqc.desktop import BatchWorker
    from oi_eegqc.desktop_process import ScoringProcess
    from process_workers import fault_scores
    import multiprocessing
    app = QApplication.instance() or QApplication([])
    before = {p.pid for p in multiprocessing.active_children()}
    worker = BatchWorker([(0, "timeout", {}), (1, "crash", {}), (2, "ok", {})],
                         timeout_s=2, process_factory=lambda: ScoringProcess(target=fault_scores))
    errors, results = [], []
    worker.failed.connect(lambda i, message: errors.append((i, message)))
    worker.scored.connect(lambda i, report: results.append((i, report.gqi)))
    worker.start()
    deadline = time.monotonic() + 15
    while worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    worker.wait(1000)
    app.processEvents()
    assert not worker.isRunning()
    assert len(errors) == 2 and "超时" in errors[0][1] and "异常退出" in errors[1][1]
    assert results == [(2, 90)]
    assert {p.pid for p in multiprocessing.active_children()} <= before


def test_multiselect_dialog_and_npy_parameters(tmp_path, monkeypatch):
    import oi_eegqc.desktop as desktop
    app = QApplication.instance() or QApplication([])
    window = Window()
    paths = [tmp_path / f"{i}.npy" for i in range(2)]
    for path in paths:
        np.save(path, synth_clean(4, 250, 5))
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileNames", lambda *a: ([str(p) for p in paths], ""))
    calls = []
    def parameters(path, multiple, defaults=None):
        calls.append((path, multiple))
        return {"sfreq": 500, "unit": "mV", "channels_first": False}, True
    monkeypatch.setattr(window, "npy_parameters", parameters)
    window.choose_files()
    assert len(calls) == 1 and calls[0][1]
    assert all(e.metadata["sfreq"] == 500 for e in window.entries)
    window.close()


def test_prefs_dialog_and_job_metadata(tmp_path):
    from PySide6.QtWidgets import QComboBox, QPushButton
    app = QApplication.instance() or QApplication([])
    window = Window()
    assert window.prefs == {"channels_first": None, "line_hz": 50.0}
    seen = []
    def inspect():
        dialog = app.activeModalWidget()
        combos = dialog.findChildren(QComboBox)
        seen.append((combos[0].itemText(0), combos[0].currentIndex(), combos[1].currentData()))
        seen.append([b.text() for b in dialog.findChildren(QPushButton)])
        combos[0].setCurrentIndex(2)
        combos[1].setCurrentIndex(1)
        dialog.accept()
    QTimer.singleShot(0, inspect)
    window.edit_prefs()
    assert seen[0] == ("默认", 0, 50.0)
    assert "检查更新" in seen[1]
    assert window.prefs["channels_first"] is False
    assert window.prefs["line_hz"] == 60.0
    path = tmp_path / "sample.npy"
    np.save(path, synth_clean(4, 250, 5))
    window.add_files([path], {"sfreq": 250, "unit": "uV"})
    meta = window.job_metadata(window.entries[0])
    assert meta["line_hz"] == 60.0
    assert meta["channels_first"] is False
    assert "expected_n_channels" not in meta
    window.entries[0].metadata["channels_first"] = True
    meta = window.job_metadata(window.entries[0])
    assert meta["channels_first"] is True
    window.close()


def test_close_settings_during_update(monkeypatch):
    import oi_eegqc.desktop_update as updates
    from PySide6.QtWidgets import QPushButton
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.show()
    shown = []
    def slow_check(version):
        time.sleep(0.2)
        return updates.UpdateInfo(status="current", current=version)
    monkeypatch.setattr(updates, "check_update", slow_check)
    monkeypatch.setattr(window, "show_update_result", lambda *args: shown.append(args))
    def click_and_close():
        dialog = app.activeModalWidget()
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "检查更新").click()
        dialog.reject()
    QTimer.singleShot(0, click_and_close)
    window.edit_prefs()
    assert window.update_workers
    window.shutdown()
    app.processEvents()
    assert not shown
    assert not window.update_workers
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
    assert window.windowTitle() == "Omni-Intelligence EEG Quality Control App"
    texts = []
    texts += [w.text() for cls in (QLabel, QPushButton) for w in window.findChildren(cls)]
    texts += [window.table.item(0, c).text() for c in range(3)]
    assert not any(re.search("[A-Z]", text) for text in texts)
    def inspect_settings():
        dialog = app.activeModalWidget()
        texts.extend(w.text() for cls in (QLabel, QPushButton) for w in dialog.findChildren(cls))
        dialog.reject()
    QTimer.singleShot(0, inspect_settings)
    window.edit_prefs()
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


@pytest.mark.parametrize("sfreq", [64, 128, 250, 256, 500, 1000, 2000])
@pytest.mark.parametrize("suffix", [".npy", ".edf", ".bdf"])
def test_dynamic_sample_rates(tmp_path, sfreq, suffix):
    path = tmp_path / ("recording" + suffix)
    if suffix == ".npy":
        np.save(path, synth_clean(4, sfreq, 5))
        report = score_file(path, sfreq=sfreq)
    else:
        write_edf(path, sfreq)
        # Reader must use the header, regardless of an unrelated array default.
        report = score_file(path, sfreq=250)
    assert report.extras["sfreq_hz"] == sfreq
    assert report.duration_s == 5
    assert report.window_qa.n_windows == 9
    assert np.isfinite(report.gqi)
    assert report.extras["frequency_coverage"]["noise_band_complete"] == (sfreq >= 192)


def test_inspect_then_prompt_only_when_required(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from oi_eegqc.desktop_service import inspect_edf_header, inspect_file
    app = QApplication.instance() or QApplication([])
    window = Window()
    prompted = []

    def parameters(path, multiple, defaults=None):
        prompted.append((Path(path).name, defaults))
        return {"sfreq": 512, "unit": "mV", "channels_first": True}, False

    monkeypatch.setattr(window, "npy_parameters", parameters)

    complete = tmp_path / "complete.npy"
    np.save(complete, synth_clean(4, 500, 5))
    complete.with_suffix(".json").write_text(json.dumps({"sfreq": 500, "unit": "uV"}))
    partial = tmp_path / "partial.npy"
    np.save(partial, synth_clean(4, 250, 5))
    partial.with_suffix(".json").write_text(json.dumps({"sfreq": 250}))
    edf = tmp_path / "header.edf"
    write_edf(edf, 256)

    window.add_files([complete, partial, edf])
    assert [e.metadata["sfreq"] for e in window.entries] == [500, 512, 256]
    assert window.entries[0].metadata["unit"] == "uV"
    assert "channels_first" not in window.entries[0].metadata
    assert window.entries[1].metadata["unit"] == "mV"
    assert prompted == [("partial.npy", {"sfreq": 250.0})]
    assert inspect_file(edf) == inspect_edf_header(edf) == {"sfreq": 256.0}
    window.close()


def test_mixed_rate_sidecars_override_batch_parameters(tmp_path):
    import json
    app = QApplication.instance() or QApplication([])
    window = Window()
    paths = []
    for sfreq in (128, 256, 500, 1000):
        path = tmp_path / f"{sfreq}.npy"
        np.save(path, synth_clean(4, sfreq, 5))
        path.with_suffix(".json").write_text(json.dumps({"sfreq": sfreq, "unit": "uV", "channels_first": True}))
        paths.append(path)
    window.add_files(paths, {"sfreq": 250})
    window.start_score()
    wait_for_batch(app, window)
    assert [e.report.extras["sfreq_hz"] for e in window.entries] == [128, 256, 500, 1000]
    assert all(e.report.duration_s == 5 for e in window.entries)
    assert "4/4" in window.status.text()
    window.close()


def test_missing_rate_has_no_250_default(tmp_path):
    from PySide6.QtWidgets import QDoubleSpinBox, QDialogButtonBox
    app = QApplication.instance() or QApplication([])
    window = Window()
    seen = []
    def inspect():
        dialog = app.activeModalWidget()
        rate = dialog.findChild(QDoubleSpinBox)
        button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        seen.append((rate.value(), button.isEnabled()))
        rate.setValue(512)
        seen.append((rate.value(), button.isEnabled()))
        dialog.accept()
    QTimer.singleShot(0, inspect)
    result = window.npy_parameters(tmp_path / "sample.npy", False)
    assert seen == [(0, False), (512, True)]
    assert result[0]["sfreq"] == 512
    window.close()


def test_summary_includes_pending_failed_and_caution():
    from types import SimpleNamespace
    from oi_eegqc.desktop import Entry
    app = QApplication.instance() or QApplication([])
    window = Window()
    def entry(gqi):
        return Entry("sample", report=SimpleNamespace(gqi=gqi))
    window.entries = [entry(80), entry(60), entry(40), Entry("pending"), Entry("failed", error="error")]
    window.update_summary()
    assert window.status.text() == "平均 60 · 3/5"
    window.entries = []
    window.close()


def test_sidecar_rejects_conflicting_or_invalid_rates(tmp_path):
    import json
    from oi_eegqc.desktop_service import npy_metadata
    path = tmp_path / "sample.npy"
    for metadata in ({"sfreq": 0}, {"sfreq": True}, {"sfreq": 250, "SamplingFrequency": 500}):
        path.with_suffix(".json").write_text(json.dumps(metadata))
        with pytest.raises(ValueError):
            npy_metadata(path)
    path.with_suffix(".json").write_text(json.dumps({
        "sfreq": 250, "unit": "uV", "impedance_kohm": {"Fp1": 4.2}, "sync_error_ms": 12
    }))
    parsed = npy_metadata(path)
    assert parsed["impedance_kohm"] == {"Fp1": 4.2}
    assert parsed["sync_error_ms"] == 12


def test_startup_does_not_import_scientific_stack():
    import subprocess
    import sys
    subprocess.run([sys.executable, "-c",
                    "import sys; import oi_eegqc.desktop; "
                    "assert not any(n in sys.modules for n in ('numpy','scipy','mne','matplotlib'))"], check=True)


def test_lazy_public_api_keeps_existing_exports():
    import oi_eegqc
    for name in oi_eegqc.__all__:
        assert getattr(oi_eegqc, name) is not None
    with pytest.raises(AttributeError):
        getattr(oi_eegqc, "nonexistent_attribute")


def test_import_crash_restores_controls(tmp_path, monkeypatch):
    import oi_eegqc.desktop as desktop
    app = QApplication.instance() or QApplication([])
    window = Window()
    def broken(*args):
        raise RuntimeError("scan failure")
    monkeypatch.setattr(desktop, "discover_files", broken)
    window.add_files([tmp_path])
    wait_for_batch(app, window)
    assert window.status.text() == "导入失败，可重试"
    assert window.choose_button.isEnabled() and window.folder_button.isEnabled()
    assert window.prefs_button.isEnabled()
    assert window.worker is None
    monkeypatch.setattr(desktop, "discover_files", lambda *args: ([], 0))
    window.add_files([tmp_path])
    wait_for_batch(app, window)
    assert window.status.text() == "未找到支持的文件"
    window.close()


def test_oversized_sidecar_rejected(tmp_path):
    from oi_eegqc.desktop_service import npy_metadata
    path = tmp_path / "array.npy"
    path.with_suffix(".json").write_text(" " * 65537)
    with pytest.raises(ValueError, match="过大"):
        npy_metadata(path)


def test_shutdown_waits_for_worker(tmp_path, monkeypatch):
    import oi_eegqc.desktop as desktop
    import threading
    app = QApplication.instance() or QApplication([])
    window = Window()
    entered = threading.Event()
    def scanning(paths, cancelled):
        entered.set()
        while not cancelled():
            time.sleep(0.005)
        return [], 0
    monkeypatch.setattr(desktop, "discover_files", scanning)
    window.add_files([tmp_path])
    assert entered.wait(5)
    window.shutdown()
    assert not window.worker.isRunning()
    wait_for_batch(app, window)
    window.close()


def test_batch_worker_reuses_cached_score(tmp_path):
    from oi_eegqc.desktop import BatchWorker
    from oi_eegqc.types import REPORT_SCHEMA_VERSION

    source = tmp_path / "signal.npy"
    source.write_bytes(b"content")
    calls = []

    class Client:
        def score(self, *args):
            calls.append(args[0])
            return "result", {"schema_version": REPORT_SCHEMA_VERSION, "gqi": 88}

        def close(self):
            pass

    job = (0, str(source), {"sfreq": 250}, tmp_path / "score-cache.sqlite3")
    first = BatchWorker([job], process_factory=Client)
    first.run()
    second = BatchWorker([job], process_factory=Client)
    results = []
    second.scored.connect(lambda index, report: results.append(report.gqi))
    second.run()
    assert calls == [str(source)]
    assert results == [88]
