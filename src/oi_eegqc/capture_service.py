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


from .local_files import atomic_json


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
    if meta.get("status") in (None, "preflight", "recording"):
        raise ValueError("采集尚未结束，不能上传仍在写入的数据")
    if plan.get("mode") != "bcigo" or not plan.get("platform_session_id"):
        raise ValueError("仅已授权的真实采集可一站式上传")
    if meta.get("session_id") != plan.get("session_id"):
        raise ValueError("采集记录与实验计划不匹配")
    quality_path = folder / "quality-response.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
    if quality and not quality.get("error") and (quality.get("session_id") != plan["session_id"] or quality.get("platform_session_id") != plan["platform_session_id"]):
        raise ValueError("逐视频质检结果与实验不匹配")
    data_directory = Path(data_directory) if data_directory else app_data()
    store = BatchStore(data_directory / "uploads-cos-beijing")
    store.directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(store.directory / "upload.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise ValueError("EEGQC 正在处理另一批上传，请稍后重试")
    try:
        recordings = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in EEG_SUFFIXES)
        report_path = folder / "file-quality.json"
        report = {}
        file_report = {"schema": SCHEMA, "quality_state": "unavailable", "report": report,
                       "segment_quality": meta.get("quality_summary", {}), "bids_error": meta.get("bids_error")}
        if len(recordings) == 1:
            recording = recordings[0]
            progress({"phase": "scoring", "message": "正在评测整轮脑电"})
            with ScoreCache(data_directory / "score-cache.sqlite3") as cache:
                digest, identity = cache.digest(recording)
                file_report.update(recording=str(recording.relative_to(folder)), sha256=digest)
                report = cache.lookup(digest, {})
                if report is None:
                    try:
                        report = score_file(recording, progress=lambda phase: progress({"phase": "scoring", "message": "整轮评测：" + phase})).to_dict()
                    except Exception as exc:
                        report = {}
                        file_report.update(quality_state="failed", reason=str(exc))
                        progress({"phase": "preparing", "message": "评分未完成，继续上传原始数据"})
                    current = recording.stat()
                    if (current.st_size, current.st_mtime_ns) != identity:
                        raise ValueError("评分期间脑电文件发生变化")
                    if report:
                        cache.store(digest, {}, report)
                file_report["report"] = report
                if report:
                    file_report["quality_state"] = "scored"
        else:
            file_report["reason"] = "未找到唯一的脑电文件，保留本轮全部文件"
        try:
            previous_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous_report = None
        if previous_report != file_report:
            atomic_json(report_path, file_report)
        progress({"phase": "preparing", "message": "正在核验文件与准备上传"})
        batch = store.completed_for(folder, validate_bids=False)
        if batch is None:
            batch = store.prepare([folder], None, progress=lambda name: progress({"phase": "preparing", "message": "正在核验 " + Path(name).name}))
            progress({"phase": "uploading", "message": "正在上传原始数据"})
            batch = UploadSession(store, batch, data_directory, lambda info: progress({"phase": "uploading", **info})).run()
        result = {"schema": SCHEMA, "request_id": request["request_id"], "session_id": plan["session_id"],
                  "state": batch["status"], "upload_id": batch.get("upload_id"), "score": report.get("gqi"),
                  "file_quality": str(report_path), "quality_state": file_report["quality_state"]}
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
                  "error": friendly_error(exc) if not isinstance(exc, ValueError) else str(exc),
                  "error_info": {"type":type(exc).__name__,"errno":getattr(exc,'errno',None),
                                 "winerror":getattr(exc,'winerror',None),"filename":getattr(exc,'filename',None)}}
        code = 1
    atomic_json(args.capture_complete_output, result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
