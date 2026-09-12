import os
import json
import re
import time
from pathlib import Path

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


def recording(path, complete=True, channels=4):
    import numpy as np
    from oi_eegqc.datasets import synth_clean
    np.save(path, synth_clean(channels, 250, 5))
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
    settings = window.findChild(QObject, "settingsButton")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     settings.mapToScene(settings.boundingRect().center()).toPoint())
    wait(app, lambda: window.property("settingsOpen"))
    checkbox = window.findChild(QObject, "allChannels")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     checkbox.mapToScene(checkbox.boundingRect().center()).toPoint())
    wait(app, lambda: controller.channelsOpen and controller.channelCount == 4)
    sheet = window.findChild(QObject, "channelSheet")
    wait(app, lambda: sheet.property("visible"))
    assert not window.property("settingsOpen")
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
    assert not controller.canScore
    button = window.findChild(QObject, "pickChannels")
    assert not button.property("enabled")
    controller.openChannelSheet()
    assert not controller.channelsOpen
    # Completed data must not contribute channels to a new pending batch.
    second = tmp_path / "two.npy"
    recording(second, channels=2)
    controller.add_paths([second])
    wait(app, lambda: not controller.busy and controller.channelsOpen)
    assert controller.channelCount == 2
    assert sheet.property("visible")


def test_qml_channel_picker_empty_then_import(quick, tmp_path):
    app, controller, engine, window = quick
    settings = window.findChild(QObject, "settingsButton")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     settings.mapToScene(settings.boundingRect().center()).toPoint())
    wait(app, lambda: window.property("settingsOpen"))
    checkbox = window.findChild(QObject, "allChannels")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     checkbox.mapToScene(checkbox.boundingRect().center()).toPoint())
    wait(app, lambda: not controller.allChannels)
    assert not controller.channelsOpen
    button = window.findChild(QObject, "pickChannels")
    assert button.property("visible") and not button.property("enabled")
    controller.openChannelSheet()
    assert not controller.channelsOpen
    path = tmp_path / "new.npy"
    recording(path, channels=6)
    controller.add_paths([path])
    sheet = window.findChild(QObject, "channelSheet")
    wait(app, lambda: not controller.busy and sheet.property("visible"))
    assert controller.channelCount == 6
    assert not window.property("settingsOpen")


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
    sheet = window.findChild(QObject, "reportSheet")
    # ListView delegates have visual rather than QObject ownership.
    def visual_child(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = visual_child(child, name)
            if found is not None:
                return found
    wait(app, lambda: visual_child(window.contentItem(), "fileRow0") is not None)
    row = visual_child(window.contentItem(), "fileRow0")
    for fraction in (0.1, 0.5, 0.95):
        point = row.mapToScene(QPointF(row.width() * fraction, row.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        wait(app, lambda: controller.reportOpen and sheet.property("visible"))
        controller.closeReport()
        wait(app, lambda: not sheet.property("visible"))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, point)
    app.processEvents()
    assert not controller.reportOpen and not sheet.property("visible")
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


def test_qml_download_and_confirm_install(quick, tmp_path, monkeypatch):
    import oi_eegqc.quick as module
    from test_desktop_update import installer_info
    app, controller, engine, window = quick
    path = tmp_path / "setup.exe"
    path.write_bytes(b"installer")
    def download(info, cache, progress, cancelled):
        progress(50)
        progress(100)
        return path
    monkeypatch.setattr(module, "download_installer", download)
    calls = []
    monkeypatch.setattr(module, "launch_installer", lambda *args: calls.append(args))
    controller._update_info = installer_info()
    controller._update_url = controller._update_info.asset_url
    controller.changed.emit()
    controller.downloadUpdate()
    wait(app, lambda: controller.updateReady and not controller.downloading)
    assert controller.downloadProgress == 100
    assert not calls
    controller._busy = True
    controller.installUpdate()
    assert not calls and controller.installer_worker is None
    controller._busy = False
    controller.changed.emit()
    window.resize(760, 760)
    window.setProperty("settingsOpen", True)
    app.processEvents()
    button = window.findChild(QObject, "installUpdate")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     button.mapToScene(button.boundingRect().center()).toPoint())
    popup = window.findChild(QObject, "installConfirmation")
    wait(app, lambda: popup.property("visible"))
    assert not calls
    confirm = window.findChild(QObject, "confirmInstall")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     confirm.mapToScene(confirm.boundingRect().center()).toPoint())
    wait(app, lambda: bool(calls))
    wait(app, lambda: controller.installer_worker is None)
    assert len(calls) == 1


def test_qml_download_failure_is_retryable(quick, monkeypatch):
    import oi_eegqc.quick as module
    from test_desktop_update import installer_info
    app, controller, engine, window = quick
    def fail(*args):
        raise OSError("offline")
    monkeypatch.setattr(module, "download_installer", fail)
    controller._update_info = installer_info()
    for _ in range(2):
        controller.downloadUpdate()
        wait(app, lambda: not controller.downloading)
        assert not controller.updateReady and controller.canDownload
        assert "重试" in controller.updateText


def test_qml_cancel_download_does_not_block_ui(quick, monkeypatch):
    import oi_eegqc.quick as module
    from test_desktop_update import installer_info
    app, controller, engine, window = quick
    def download(info, cache, progress, cancelled):
        while not cancelled():
            time.sleep(0.01)
        raise module.DownloadCancelled()
    monkeypatch.setattr(module, "download_installer", download)
    controller._update_info = installer_info()
    controller.downloadUpdate()
    assert controller.downloading and not controller.busy
    controller.cancelDownload()
    wait(app, lambda: not controller.downloading)
    assert not controller.updateReady and controller.updateText == "已取消下载"


def test_qml_install_failure_keeps_window(quick, tmp_path, monkeypatch):
    import oi_eegqc.quick as module
    from test_desktop_update import installer_info
    app, controller, engine, window = quick
    def fail(*args):
        raise OSError("launch denied")
    monkeypatch.setattr(module, "launch_installer", fail)
    controller._update_info = installer_info()
    controller._installer = tmp_path / "setup.exe"
    controller.installUpdate()
    wait(app, lambda: controller.installer_worker is None)
    assert not controller.busy and not controller.updateReady
    assert window.isVisible()
    assert "无法启动" in controller.updateText


def test_qml_folder_upload_gate_preview_and_resume(quick, tmp_path, monkeypatch):
    import oi_eegqc.desktop_upload as service
    from test_desktop_upload import FakeClient
    from oi_eegqc.upload_ui import UploadController
    app, controller, engine, window = quick
    button = window.findChild(QObject, "uploadFolder")
    assert not button.property("enabled")
    score = window.findChild(QObject, "scoreButton")
    upload_point = button.mapToScene(button.boundingRect().center())
    score_point = score.mapToScene(score.boundingRect().center())
    assert abs(upload_point.y() - score_point.y()) < 2
    assert upload_point.x() < score_point.x() and upload_point.y() > window.height() / 2
    folder = tmp_path / "collection"
    folder.mkdir()
    recording(folder / "data.npy")
    (folder / "notes.txt").write_text("keep me")
    controller.add_paths([folder])
    wait(app, lambda: not controller.busy)
    assert not controller.canUpload and not button.property("enabled")
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    assert controller.canUpload and button.property("enabled")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     button.mapToScene(button.boundingRect().center()).toPoint())
    upload = controller._upload
    wait(app, lambda: not upload.active and upload.hasBatch)
    assert window.findChild(QObject, "uploadSheet").property("visible")
    assert upload.info["count"] == 3
    assert upload.batch["status"] == "ready"
    batch_id = upload.batch["local_id"]
    # A fresh controller restores the exact pending batch without EEG rows.
    restored = UploadController(upload.data_directory)
    assert restored.hasBatch and restored.batch["local_id"] == batch_id
    client = FakeClient()
    original = service.UploadSession.__init__
    def init(self, *args, **kwargs):
        kwargs["client_factory"] = lambda data: client
        original(self, *args, **kwargs)
    monkeypatch.setattr(service.UploadSession, "__init__", init)
    start = window.findChild(QObject, "startUpload")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     start.mapToScene(start.boundingRect().center()).toPoint())
    wait(app, lambda: not upload.active and upload.batch["status"] == "completed")
    assert len(client.objects) == 4
    assert upload.batch["local_id"] == batch_id
    restored.shutdown()


def test_upload_lock_prevents_parallel_batch_writes(quick, tmp_path):
    from oi_eegqc.upload_ui import UploadController
    app, controller, engine, window = quick
    first = controller._upload
    second = UploadController(first.data_directory)
    assert first._acquire()
    try:
        assert not second._acquire()
        assert "另一个窗口" in second.info["error"]
    finally:
        first.lock.unlock()
        first.lock = None
        second.shutdown()


def test_upload_has_no_token_import(quick):
    from PySide6.QtCore import QObject
    app, controller, engine, window = quick
    assert not hasattr(controller._upload, "importDeviceToken")
    assert window.findChild(QObject, "importDeviceToken") is None
    assert window.findChild(QObject, "deviceTokenInput") is None


def test_upload_preview_does_not_replay_previous_error(quick, tmp_path, monkeypatch):
    from oi_eegqc.desktop_upload import UploadSession
    app, controller, engine, window = quick
    upload = controller._upload
    root = tmp_path / "collection"
    root.mkdir()
    path = root / "data.npy"
    recording(path)
    stat = path.stat()
    scored = {str(path): (stat.st_size, stat.st_mtime_ns)}
    batch = upload.store.prepare([root], scored)
    batch.update(status="failed", error="签名文件清单不完整", upload_id="existing-batch")
    upload.store.save(batch)
    upload.batch = batch
    upload.prepare([root], scored)
    assert upload.info["error"] == ""
    wait(app, lambda: not upload.active)
    assert upload.info["error"] == ""
    assert upload.info["status"] == "待继续上传"
    assert upload.store.load()["error"] == "签名文件清单不完整"
    assert upload.batch["upload_id"] == "existing-batch"

    def fail(session):
        session.batch.update(status="failed", error="本次上传网络失败")
        session.store.save(session.batch)
        return session.batch
    monkeypatch.setattr(UploadSession, "run", fail)
    upload.start()
    assert upload.info["error"] == ""
    wait(app, lambda: not upload.active)
    assert upload.info["error"] == "本次上传网络失败"
    upload.close()
    upload.prepare([root], scored)
    wait(app, lambda: not upload.active)
    assert upload.info["error"] == ""
    assert upload.store.load()["upload_id"] == "existing-batch"


def test_pending_upload_cannot_bypass_scoring_gate(quick, tmp_path, monkeypatch):
    app, controller, engine, window = quick
    button = window.findChild(QObject, "uploadFolder")
    controller._upload.batch = {"status": "ready"}
    controller._upload._error = "历史上传错误"
    controller._upload.changed.emit()
    app.processEvents()
    calls = []
    monkeypatch.setattr(controller._upload, "prepare", lambda *args: calls.append(args))
    assert not button.property("enabled")
    controller.prepareUpload()
    assert not calls
    folder = tmp_path / "collection"
    folder.mkdir()
    recording(folder / "data.npy")
    controller.add_paths([folder])
    wait(app, lambda: not controller.busy)
    assert not button.property("enabled")
    controller.prepareUpload()
    assert not calls
    controller.scoreOrStop()
    assert not button.property("enabled")
    wait(app, lambda: not controller.busy)
    assert button.property("enabled")
    controller.prepareUpload()
    assert len(calls) == 1
    controller.files.rows[0]["scored_stat"] = None
    controller.changed.emit()
    app.processEvents()
    assert not button.property("enabled")
