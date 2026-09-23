"""Headless capture handoff: score a finished recording and resume its upload.

The acquisition app owns the experiment; EEGQC owns scoring and cloud transport.
This JSON-file protocol deliberately needs neither a QML window nor shared imports.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import uuid

SCHEMA = "oi-eegqc-capture-complete-v1"


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def app_data():
    from PySide6.QtCore import QCoreApplication, QStandardPaths
    app = QCoreApplication.instance() or QCoreApplication([])
    app.setOrganizationName("Omni-Intelligence")
    app.setApplicationName("EEGQC")
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))


def complete(request, progress=lambda value: None, data_directory=None):
    from PySide6.QtCore import QLockFile
    from .desktop_upload import BatchStore, EEG_SUFFIXES, UploadSession
    from .intake import score_file
    from .score_cache import ScoreCache

    if request.get("schema") != SCHEMA:
        raise ValueError("不支持的采集交接协议")
    folder = Path(request["folder"]).resolve(strict=True)
    meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    if meta.get("status") in ("preflight", "recording") or meta.get("storage_format") != "eeg-bids-1":
        raise ValueError("采集或 BIDS 整理尚未完成")
    if plan.get("mode") != "bcigo" or not plan.get("platform_session_id"):
        raise ValueError("仅已授权的真实采集可一站式上传")
    if meta.get("session_id") != plan.get("session_id"):
        raise ValueError("采集记录与实验计划不匹配")
    quality = json.loads((folder / "quality-response.json").read_text(encoding="utf-8"))
    if quality.get("session_id") != plan["session_id"] or quality.get("platform_session_id") != plan["platform_session_id"]:
        raise ValueError("逐视频质检结果与实验不匹配")
    if quality.get("error") or not quality.get("segments"):
        raise ValueError("请先完成逐视频质检")
    data_directory = Path(data_directory) if data_directory else app_data()
    store = BatchStore(data_directory / "uploads-cos-beijing")
    store.directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(store.directory / "upload.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise ValueError("EEGQC 正在处理另一批上传，请稍后重试")
    try:
        recordings = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in EEG_SUFFIXES)
        if len(recordings) != 1:
            raise ValueError("本轮必须有且仅有一份脑电文件")
        recording = recordings[0]
        progress({"phase": "scoring", "message": "正在评测整轮脑电"})
        with ScoreCache(data_directory / "score-cache.sqlite3") as cache:
            digest, identity = cache.digest(recording)
            report = cache.lookup(digest, {})
            if report is None:
                report = score_file(recording, progress=lambda phase: progress({"phase": "scoring", "message": "整轮评测：" + phase})).to_dict()
                current = recording.stat()
                if (current.st_size, current.st_mtime_ns) != identity:
                    raise ValueError("评分期间脑电文件发生变化")
                cache.store(digest, {}, report)
        report_path = folder / "file-quality.json"
        atomic_json(report_path, {"schema": SCHEMA, "recording": str(recording.relative_to(folder)), "sha256": digest, "report": report})
        progress({"phase": "preparing", "message": "正在核验文件与准备上传"})
        batch = store.prepare([folder], {recording: (*identity, digest)}, progress=lambda name: progress({"phase": "preparing", "message": "正在核验 " + Path(name).name}))
        if batch["status"] != "completed":
            progress({"phase": "uploading", "message": "正在上传原始数据"})
            batch = UploadSession(store, batch, data_directory, lambda info: progress({"phase": "uploading", **info})).run()
        result = {"schema": SCHEMA, "request_id": request["request_id"], "session_id": plan["session_id"],
                  "state": batch["status"], "upload_id": batch.get("upload_id"), "score": report.get("gqi"),
                  "file_quality": str(report_path)}
        progress({"phase": batch["status"], "message": "上传完成" if batch["status"] == "completed" else "上传未完成，可继续"})
        return result
    finally:
        lock.unlock()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-complete-request", required=True)
    parser.add_argument("--capture-complete-output", required=True)
    parser.add_argument("--capture-complete-progress", required=True)
    args = parser.parse_args(argv)
    request = json.loads(Path(args.capture_complete_request).read_text(encoding="utf-8"))
    progress = lambda value: atomic_json(args.capture_complete_progress, value)
    try:
        result = complete(request, progress)
        code = 0 if result["state"] == "completed" else 1
    except Exception as exc:
        from .desktop_upload import friendly_error
        result = {"schema": SCHEMA, "request_id": request.get("request_id"), "state": "failed",
                  "error": friendly_error(exc) if not isinstance(exc, ValueError) else str(exc)}
        code = 1
    atomic_json(args.capture_complete_output, result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
