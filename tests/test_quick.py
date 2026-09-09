import os
import json
import re
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QSettings, Qt, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from oi_eegqc.quick import Controller, create_engine
except ImportError as exc:
    pytest.skip(f"Qt unavailable: {exc}", allow_module_level=True)


def wait(app, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


@pytest.fixture
def quick(tmp_path):
    app = QApplication.instance() or QApplication([])
    controller = Controller(settings=QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    engine = create_engine(controller)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    app.processEvents()
    yield app, controller, engine, window
    controller.shutdown()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def recording(path, complete=True):
    import numpy as np
    from oi_eegqc.datasets import synth_clean
    np.save(path, synth_clean(4, 250, 5))
    if complete:
        path.with_suffix(".json").write_text(json.dumps({"sfreq": 250, "unit": "uV"}))


def test_qml_load_text_selection_and_settings(quick, tmp_path):
    app, controller, engine, window = quick
    paths = [tmp_path / f"FILE{i}.npy" for i in range(3)]
    for path in paths:
        recording(path)
    controller.addUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    wait(app, lambda: not controller.busy)
    assert controller.count == 3
    assert window.title() == "Omni-Intelligence EEG Quality Control App"
    controller.select(0, 0)
    controller.select(2, Qt.KeyboardModifier.ShiftModifier.value)
    assert controller.selectedCount == 3
    controller.select(1, Qt.KeyboardModifier.ControlModifier.value)
    assert controller.selectedCount == 2
    # Exercise the actual QML settings button, not a hidden Widgets window.
    button = window.findChild(QObject, "settingsButton")
    point = button.mapToScene(button.boundingRect().center()).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    wait(app, lambda: window.property("settingsOpen"))
    texts = [o.property("text") for o in window.findChildren(QObject)]
    assert not any(re.search("[A-Z]", text) for text in texts if isinstance(text, str))
    controller.saveSettings(2, 60)
    assert controller.orderIndex == 2 and controller.mains == 60
    controller.remove(False)
    assert controller.count == 1
    assert all(p.exists() for p in paths)


def test_qml_nested_import_parameters_dedup_and_score(quick, tmp_path):
    app, controller, engine, window = quick
    nested = tmp_path / "nested"
    nested.mkdir()
    a, b = tmp_path / "same.npy", nested / "same.npy"
    recording(a, complete=False)
    recording(b, complete=False)
    (nested / "notes.txt").write_text("junk")
    controller.add_paths([tmp_path])
    wait(app, lambda: bool(controller.parameters))
    assert not controller.acceptParameters("0", 0, True)
    assert not controller.acceptParameters("nan", 0, True)
    assert controller.acceptParameters("250", 0, True)
    wait(app, lambda: not controller.busy)
    assert controller.count == 2
    assert controller.files.rows[0]["label"] != controller.files.rows[1]["label"]
    controller.add_paths([a, b])
    wait(app, lambda: not controller.busy)
    assert controller.count == 2
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    assert all(r["report"] and r["state"] == "完成" for r in controller.files.rows)
    assert "平均" in controller.summary
    assert "可用" in controller.summary


def test_qml_channel_subset_and_cancel_restores_all(quick, tmp_path):
    app, controller, engine, window = quick
    path = tmp_path / "clip.npy"
    recording(path)
    controller.add_paths([path])
    wait(app, lambda: not controller.busy)
    controller.setAllChannels(False)
    wait(app, lambda: controller.channelsOpen and controller.channelCount == 4)
    controller.cancelChannels()
    wait(app, lambda: not controller.channelsOpen)
    assert controller.allChannels
    controller.setAllChannels(False)
    wait(app, lambda: controller.channelsOpen)
    controller.clearChannels()
    controller.toggleChannel(0)
    controller.acceptChannels()
    wait(app, lambda: not controller.channelsOpen)
    assert not controller.allChannels
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    report = controller.files.rows[0]["report"]
    assert report is not None
    assert report.n_channels_used == 1


def test_qml_update_and_cancel_intake(quick, tmp_path, monkeypatch):
    from oi_eegqc.desktop_update import UpdateInfo
    import oi_eegqc.desktop_update as updates
    app, controller, engine, window = quick
    monkeypatch.setattr(updates, "check_update", lambda version: UpdateInfo("available", version, "0.4.0", asset_url="https://example.test/setup"))
    controller.checkUpdate()
    wait(app, lambda: not controller.checking)
    assert controller.updateAvailable
    assert controller.updateText == "发现新版本 0.4.0"
    path = tmp_path / "missing.npy"
    recording(path, complete=False)
    controller.add_paths([path])
    wait(app, lambda: bool(controller.parameters))
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    assert not controller.parameters
    assert controller.count == 0


def test_qml_native_drop_and_score_button(quick, tmp_path):
    from PySide6.QtCore import QMimeData, QPointF
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    app, controller, engine, window = quick
    path = tmp_path / "drop.npy"
    recording(path)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    enter = QDragEnterEvent(QPoint(200, 200), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window, enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(200, 200), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window, drop)
    assert drop.isAccepted()
    wait(app, lambda: not controller.busy)
    assert controller.count == 1
    button = window.findChild(QObject, "scoreButton")
    point = button.mapToScene(button.boundingRect().center()).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    wait(app, lambda: controller.files.rows[0]["report"] is not None and not controller.busy)
    controller.select(0, 0)
    controller.openReport(0)
    wait(app, lambda: controller.reportOpen)
    assert controller.reportCard["score"]
    assert any(item["name"] == "接触" for item in controller.reportCard["dimensions"])
    controller.closeReport()
    wait(app, lambda: not controller.reportOpen)


def test_qml_cancel_scoring_and_resume(quick, tmp_path, monkeypatch):
    import oi_eegqc.quick as module
    from oi_eegqc.desktop import BatchWorker
    from oi_eegqc.desktop_process import ScoringProcess
    from process_workers import blocked_scores
    app, controller, engine, window = quick
    path = tmp_path / "signal.npy"
    recording(path)
    controller.add_paths([path])
    wait(app, lambda: not controller.busy)
    monkeypatch.setattr(module, "BatchWorker", lambda jobs: BatchWorker(jobs, process_factory=lambda: ScoringProcess(target=blocked_scores)))
    controller.scoreOrStop()
    wait(app, lambda: controller._phase == "评分")
    started = time.monotonic()
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    assert time.monotonic() - started < 3
    assert controller.files.rows[0]["state"] == "已取消"
    monkeypatch.setattr(module, "BatchWorker", BatchWorker)
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    assert controller.files.rows[0]["report"] is not None
