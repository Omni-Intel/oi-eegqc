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
    from PySide6.QtGui import QFontDatabase
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.exists() and not app.property("testFontLoaded"):
        QFontDatabase.addApplicationFont(str(font_path))
        app.setProperty("testFontLoaded", True)
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


def test_reimport_preserves_unchanged_scores_and_invalidates_changed_files(quick, tmp_path):
    app, controller, _, _ = quick
    first, second = tmp_path / "a.npy", tmp_path / "b.npy"
    recording(first)
    recording(second)
    controller.add_paths([tmp_path])
    wait(app, lambda: not controller.busy)
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    reports = {row["path"]: row["report"] for row in controller.files.rows}
    assert all(reports.values())
    recording(second, channels=5)
    controller.add_paths([tmp_path])
    wait(app, lambda: not controller.busy)
    assert controller.count == 2
    rows = {row["path"]: row for row in controller.files.rows}
    assert rows[str(first)]["report"] is reports[str(first)]
    assert rows[str(second)]["report"] is None
    assert not controller.canUpload


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


def test_multiple_folders_are_grouped_and_all_files_listed(quick, tmp_path):
    app, controller, engine, window = quick
    roots = [tmp_path / "first" / "session", tmp_path / "second" / "session"]
    for root in roots:
        root.mkdir(parents=True)
        recording(root / "data.npy")
        (root / "impedance.png").write_bytes(b"image")
        (root / "position.mp4").write_bytes(b"video")
        (root / "notes").mkdir()
        (root / "notes" / "readme.txt").write_text("notes")
    controller.add_paths(roots)
    wait(app, lambda: not controller.busy)
    assert controller.folderCount == 2
    assert {r["folder"] for r in controller.files.rows} == {str(p).lower() for p in roots}
    assert [r["label"] for r in controller.files.rows] == ["data.npy", "data.npy"]
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    QTest.qWait(150)
    assert window.grabWindow().save(str(tmp_path / "folders-main.png"))
    controller.prepareUpload()
    upload = controller._upload
    wait(app, lambda: not upload.active)
    assert upload.info["folderCount"] == 2 and upload.info["count"] == 10
    assert {f["path"] for f in upload.info["folders"]} == {str(p) for p in roots}
    assert all(f["count"] == 5 for f in upload.info["folders"])
    assert window.findChild(QObject, "uploadFolderList").property("count") == 2
    QTest.qWait(150)
    assert window.grabWindow().save(str(tmp_path / "folders-upload.png"))


def test_pending_other_folder_is_not_silently_selected(quick, tmp_path):
    app, controller, engine, window = quick
    upload = controller._upload
    original = tmp_path / "original"
    original.mkdir()
    recording(original / "data.npy")
    stat = (original / "data.npy").stat()
    old = upload.store.prepare([original], {str(original / "data.npy"): (stat.st_size, stat.st_mtime_ns)})
    other = tmp_path / "other"
    other.mkdir()
    recording(other / "data.npy")
    other_stat = (other / "data.npy").stat()
    upload.prepare([other], {str(other / "data.npy"): (other_stat.st_size, other_stat.st_mtime_ns)})
    wait(app, lambda: not upload.active)
    assert not upload.info["error"] and upload.canStart
    assert upload.batch["roots"] == [str(other)]
    assert upload.batch["local_id"] != old["local_id"]
    upload.prepare([original], {str(original / "data.npy"): (stat.st_size, stat.st_mtime_ns)})
    wait(app, lambda: not upload.active)
    assert upload.batch["local_id"] == old["local_id"]


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


def test_new_acquisition_requires_confirmation(quick, tmp_path):
    app, controller, engine, window = quick
    root = tmp_path / "collection"
    root.mkdir()
    recording(root / "data.npy")
    controller.add_paths([root])
    wait(app, lambda: not controller.busy)
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    controller.prepareUpload()
    upload = controller._upload
    wait(app, lambda: not upload.active)
    upload.batch["upload_id"] = "existing-acquisition"
    upload.store.save(upload.batch)
    upload.changed.emit()
    app.processEvents()
    button = window.findChild(QObject, "newAcquisition")
    assert not button.property("visible")
    more = window.findChild(QObject, "uploadMaintenance")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     more.mapToScene(more.boundingRect().center()).toPoint())
    wait(app, lambda: button.property("visible"))
    QTest.qWait(100)  # Allow the popup to relayout after expanding maintenance actions.
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     button.mapToScene(button.boundingRect().center()).toPoint())
    wait(app, lambda: window.findChild(QObject, "newAcquisitionConfirm").property("visible"))
    original = upload.store.load()["local_id"]
    assert upload.store.load()["upload_id"] == "existing-acquisition"
    confirm = window.findChild(QObject, "confirmNewAcquisition")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     confirm.mapToScene(confirm.boundingRect().center()).toPoint())
    wait(app, lambda: not upload.active and upload.batch["local_id"] != original)
    assert upload.batch["upload_id"] is None


def test_upload_has_no_token_import(quick):
    from PySide6.QtCore import QObject
    app, controller, engine, window = quick
    assert not hasattr(controller._upload, "importDeviceToken")
    assert window.findChild(QObject, "importDeviceToken") is None
    assert window.findChild(QObject, "deviceTokenInput") is None


def test_changed_file_during_scoring_stays_rescorable(quick, tmp_path):
    app, controller, engine, window = quick
    path = tmp_path / "changing.npy"
    recording(path)
    controller.add_paths([path])
    wait(app, lambda: not controller.busy)
    row = controller.files.rows[0]
    stat = path.stat()
    row["score_input_stat"] = (stat.st_size, stat.st_mtime_ns)
    path.write_bytes(b"different-content")
    controller._scored(0, object())
    assert row["report"] is None and controller.canScore
    assert row["state"] == "需要重评"


def test_reset_requires_two_confirmations_and_offline_only_retries(quick, tmp_path):
    app, controller, engine, window = quick
    root = tmp_path / "collection"
    root.mkdir()
    recording(root / "data.npy")
    controller.add_paths([root])
    wait(app, lambda: not controller.busy)
    controller.scoreOrStop()
    wait(app, lambda: not controller.busy)
    controller.prepareUpload()
    upload = controller._upload
    wait(app, lambda: not upload.active)
    upload.batch.update(status="failed", allocation_pending=False, error="网络不可用，请联网后重试")
    upload._show_batch_error = True
    upload.changed.emit()
    app.processEvents()
    assert window.findChild(QObject, "startUpload").property("text") == "重试"
    assert not window.findChild(QObject, "restoreUploadId").property("visible")
    assert not window.findChild(QObject, "resetUploadRecord").property("visible")
    upload.batch["allocation_pending"] = True
    upload.store.save(upload.batch)
    upload.changed.emit()
    app.processEvents()
    assert not window.findChild(QObject, "startUpload").property("visible")
    more = window.findChild(QObject, "uploadMaintenance")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     more.mapToScene(more.boundingRect().center()).toPoint())
    app.processEvents()
    for name in ("restoreUploadId", "newAcquisition", "resetUploadRecord"):
        assert window.findChild(QObject, name).property("visible")
    QTest.qWait(100)
    def click(name):
        button = window.findChild(QObject, name)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         button.mapToScene(button.boundingRect().center()).toPoint())
    click("resetUploadRecord")
    wait(app, lambda: window.findChild(QObject, "resetUploadFirst").property("visible"))
    click("resetFirstConfirm")
    wait(app, lambda: window.findChild(QObject, "resetUploadSecond").property("visible"))
    assert upload.store.load()["allocation_pending"]
    click("resetSecondConfirm")
    wait(app, lambda: not upload.active and not upload.info["uncertain"])
    assert upload.batch["upload_id"] is None
    assert (root / "data.npy").exists()


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


@pytest.mark.parametrize("width,height", [(600, 380), (760, 520)])
@pytest.mark.parametrize("phase", ["ready", "uploading", "failed", "completed"])
def test_upload_sheet_layout(quick, tmp_path, width, height, phase):
    from types import SimpleNamespace
    app, controller, engine, window = quick
    upload = controller._upload
    upload.batch = dict(status=phase, roots=["c:/采集数据/第一批/" + "较长文件夹名称/" * 8],
                        upload_id="" if phase == "ready" else "20260912T010000Z-a1b2c3d4",
                        entries=[dict(directory=False, size=100, done=phase == "completed")],
                        error="网络连接失败，请稍后重试" if phase == "failed" else "")
    upload._show_batch_error = True
    upload._progress = dict(done=45, speed=1024 * 1024, current="采集数据/raw/eeg.npy")
    upload._opened = True
    if phase == "uploading":
        upload.worker = SimpleNamespace(roots=None, session=None)
    try:
        window.setWidth(width)
        window.setHeight(height)
        upload.changed.emit()
        QTest.qWait(180)
        app.processEvents()
        sheet = window.findChild(QObject, "uploadSheet")
        assert sheet.property("visible")
        assert 0 < sheet.property("height") <= height - 40
        error = window.findChild(QObject, "uploadError")
        if phase == "failed":
            top = error.mapToScene(error.boundingRect().topLeft())
            bottom = error.mapToScene(error.boundingRect().bottomRight())
            assert 0 <= top.y() < bottom.y() < height
        start = window.findChild(QObject, "startUpload")
        cancel = window.findChild(QObject, "cancelUpload")
        for button in (start, cancel):
            if button.isVisible():
                point = button.mapToScene(button.boundingRect().center())
                assert 0 < point.x() < width and 0 < point.y() < height
        assert window.grabWindow().save(str(tmp_path / "upload.png"))
    finally:
        upload.worker = None


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
