from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import re
import secrets
import sqlite3
import threading
from pathlib import Path, PurePosixPath


INBOX_PREFIX = "eeg/inbox"
MAX_SINGLE_PUT = 5 * 1024**3
MAX_FILES_PER_REQUEST = 5000
MAX_PARTS_PER_REQUEST = 1000
MIN_PART_SIZE = 5 * 1024**2
DEFAULT_PART_SIZE = 64 * 1024**2
UPLOAD_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
ROUND_ID_RE = re.compile(r"^r_\d{8}T\d{6}Z_[0-9a-f]{8}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def make_upload_id():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def make_round_id():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"r_{stamp}_{secrets.token_hex(4)}"


def normalize_path(value):
    if not isinstance(value, str):
        raise ValueError("file path must be a string")
    value = value.replace("\\", "/")
    directory_marker = value.endswith("/")
    value = value.strip("/")
    if not value or len(value.encode("utf-8")) > 1024 or "\x00" in value:
        raise ValueError("invalid relative file path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("file path must be relative")
    normalized = str(path) + ("/" if directory_marker else "")
    if normalized in ("_COMPLETE", "_UPDATING") or normalized.endswith(("/_COMPLETE", "/_UPDATING")):
        raise ValueError("reserved file path")
    return normalized


class UploadCoordinator:
    """Own collection rounds while object bytes travel directly to TOS."""

    def __init__(self, database, storage):
        self.storage = storage
        self.lock = threading.RLock()
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS collections (
                upload_id TEXT PRIMARY KEY,
                current_round_id TEXT
            );
            CREATE TABLE IF NOT EXISTS rounds (
                round_id TEXT PRIMARY KEY,
                upload_id TEXT NOT NULL REFERENCES collections(upload_id),
                request_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                expected_files INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                size INTEGER NOT NULL,
                is_directory INTEGER NOT NULL,
                mode TEXT NOT NULL,
                state TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                etag TEXT,
                multipart_upload_id TEXT,
                part_size INTEGER,
                part_count INTEGER,
                PRIMARY KEY (round_id, path)
            );
            CREATE TABLE IF NOT EXISTS collection_files (
                upload_id TEXT NOT NULL REFERENCES collections(upload_id),
                path TEXT NOT NULL,
                size INTEGER NOT NULL,
                is_directory INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                PRIMARY KEY (upload_id, path)
            );
            CREATE TABLE IF NOT EXISTS round_replacements (
                round_id TEXT PRIMARY KEY,
                previous_round_id TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def _round(self, upload_id, round_id):
        if not UPLOAD_ID_RE.fullmatch(upload_id) or not ROUND_ID_RE.fullmatch(round_id):
            raise ValueError("invalid collection or round id")
        row = self.db.execute(
            "SELECT * FROM rounds WHERE upload_id = ? AND round_id = ?",
            (upload_id, round_id),
        ).fetchone()
        if not row:
            raise NotFound("upload round not found")
        return row

    def _active_round(self, upload_id, round_id):
        row = self._round(upload_id, round_id)
        current = self.db.execute(
            "SELECT current_round_id FROM collections WHERE upload_id = ?", (upload_id,)
        ).fetchone()
        if not current or current["current_round_id"] != round_id or row["state"] == "complete":
            raise Conflict("upload round is not active")
        return row

    def start_round(self, upload_id, request_id, replaces_request_id=None):
        if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError("invalid clientRequestId")
        if replaces_request_id is not None and (
            not isinstance(replaces_request_id, str) or not REQUEST_ID_RE.fullmatch(replaces_request_id)
        ):
            raise ValueError("invalid replacesRequestId")
        if upload_id is not None and (
            not isinstance(upload_id, str) or not UPLOAD_ID_RE.fullmatch(upload_id)
        ):
            raise ValueError("invalid uploadId")
        with self.lock, self.db:
            previous = self.db.execute(
                "SELECT upload_id, round_id FROM rounds WHERE request_id = ?", (request_id,)
            ).fetchone()
            if previous:
                if upload_id and previous["upload_id"] != upload_id:
                    raise Conflict("clientRequestId belongs to another collection")
                return dict(previous)
            replaced = None
            if replaces_request_id is not None:
                replaced = self.db.execute(
                    "SELECT * FROM rounds WHERE request_id = ?", (replaces_request_id,)
                ).fetchone()
                if not replaced:
                    raise Conflict("previous upload request is not known")
                if upload_id and replaced["upload_id"] != upload_id:
                    raise Conflict("previous request belongs to another collection")
                upload_id = replaced["upload_id"]
            upload_id = upload_id or make_upload_id()
            collection = self.db.execute(
                "SELECT current_round_id FROM collections WHERE upload_id = ?", (upload_id,)
            ).fetchone()
            current = collection["current_round_id"] if collection else None
            if replaced and (current != replaced["round_id"] and not (
                current is None and replaced["state"] == "complete"
            )):
                raise Conflict("previous upload round has already been replaced")
            if current and not replaced:
                raise Conflict("another upload round is active")
            round_id = make_round_id()
            now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            self.storage.begin_round(upload_id, round_id, now)
            self.db.execute(
                "INSERT OR IGNORE INTO collections(upload_id, current_round_id) VALUES (?, NULL)",
                (upload_id,),
            )
            self.db.execute(
                "INSERT INTO rounds(round_id, upload_id, request_id, state, created_at) VALUES (?, ?, ?, 'updating', ?)",
                (round_id, upload_id, request_id, now),
            )
            self.db.execute(
                "UPDATE collections SET current_round_id = ? WHERE upload_id = ?",
                (round_id, upload_id),
            )
            if replaced:
                self.db.execute("INSERT INTO round_replacements VALUES (?, ?)",
                                (round_id, replaced["round_id"]))
                if replaced["state"] != "complete":
                    self.db.execute("UPDATE rounds SET state = 'superseded' WHERE round_id = ?",
                                    (replaced["round_id"],))
            return {"upload_id": upload_id, "round_id": round_id}

    def register_files(self, upload_id, round_id, files):
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES_PER_REQUEST:
            raise ValueError(f"files must contain 1 to {MAX_FILES_PER_REQUEST} entries")
        normalized = []
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("file entry must be an object")
            path = normalize_path(item.get("path"))
            size = item.get("size")
            directory = bool(item.get("directory", False))
            content_hash = item.get("sha256")
            if not isinstance(size, int) or size < 0 or (directory and size != 0):
                raise ValueError("invalid file size")
            if directory != path.endswith("/"):
                raise ValueError("directory paths must end with a slash")
            if not isinstance(content_hash, str) or not SHA256_RE.fullmatch(content_hash):
                raise ValueError("invalid sha256")
            if directory and content_hash != EMPTY_SHA256:
                raise ValueError("invalid directory sha256")
            mode = "single" if directory or size <= MAX_SINGLE_PUT else "multipart"
            normalized.append((path, size, int(directory), mode, content_hash))
        if len({item[0] for item in normalized}) != len(normalized):
            raise ValueError("duplicate file path")
        with self.lock, self.db:
            row = self._active_round(upload_id, round_id)
            for path, size, directory, mode, content_hash in normalized:
                existing = self.db.execute(
                    "SELECT size, is_directory, mode, content_hash FROM files WHERE round_id = ? AND path = ?",
                    (round_id, path),
                ).fetchone()
                if existing:
                    if tuple(existing) != (size, directory, mode, content_hash):
                        raise Conflict("file metadata changed within the upload round")
                    continue
                if row["state"] != "updating":
                    raise Conflict("manifest is already sealed")
                known = self.db.execute(
                    "SELECT size, is_directory, content_hash FROM collection_files WHERE upload_id = ? AND path = ?",
                    (upload_id, path),
                ).fetchone()
                state = "complete" if known and tuple(known) == (
                    size, directory, content_hash
                ) else "pending"
                self.db.execute(
                    "INSERT INTO files(round_id, path, size, is_directory, mode, state, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (round_id, path, size, directory, mode, state, content_hash),
                )
                # Only an identical file can inherit the previous round's partial task.
                prior = self.db.execute(
                    "WITH RECURSIVE ancestors(id, depth) AS ("
                    "SELECT previous_round_id, 1 FROM round_replacements WHERE round_id = ? "
                    "UNION ALL SELECT r.previous_round_id, a.depth + 1 FROM round_replacements r "
                    "JOIN ancestors a ON r.round_id = a.id) "
                    "SELECT f.* FROM files f JOIN ancestors a ON f.round_id = a.id "
                    "WHERE f.path = ? AND f.size = ? AND f.content_hash = ? AND f.state = 'pending' "
                    "ORDER BY a.depth LIMIT 1",
                    (round_id, path, size, content_hash),
                ).fetchone()
                if state != "complete" and prior and prior["multipart_upload_id"]:
                    self.db.execute(
                        "UPDATE files SET multipart_upload_id = ?, part_size = ?, part_count = ? "
                        "WHERE round_id = ? AND path = ?",
                        (prior["multipart_upload_id"], prior["part_size"], prior["part_count"], round_id, path),
                    )
        return [{"path": p, "mode": m} for p, _, _, m, _ in normalized]

    def seal_round(self, upload_id, round_id, file_count):
        if not isinstance(file_count, int) or file_count < 1:
            raise ValueError("invalid fileCount")
        with self.lock, self.db:
            row = self._active_round(upload_id, round_id)
            count = self.db.execute(
                "SELECT COUNT(*) AS value FROM files WHERE round_id = ?", (round_id,)
            ).fetchone()["value"]
            if count != file_count:
                raise Conflict("registered file count does not match fileCount")
            if row["expected_files"] not in (None, file_count):
                raise Conflict("manifest was sealed with another file count")
            self.db.execute(
                "UPDATE rounds SET state = 'sealed', expected_files = ? WHERE round_id = ?",
                (file_count, round_id),
            )
        return {"fileCount": file_count, "state": "sealed"}

    def sign_single(self, upload_id, round_id, paths):
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES_PER_REQUEST:
            raise ValueError("invalid file list")
        paths = [normalize_path(path) for path in paths]
        with self.lock:
            self._active_round(upload_id, round_id)
            result = []
            for path in paths:
                row = self.db.execute(
                    "SELECT mode FROM files WHERE round_id = ? AND path = ?", (round_id, path)
                ).fetchone()
                if not row:
                    raise NotFound("file is not registered")
                if row["mode"] != "single":
                    raise Conflict("large file requires multipart upload")
                key = f"{INBOX_PREFIX}/{upload_id}/{path}"
                upload_key = f"{INBOX_PREFIX}/.staging/{upload_id}/{round_id}/{path}"
                result.append(
                    {"path": path, "objectKey": key, "uploadKey": upload_key,
                     "putUrl": self.storage.sign_put(upload_key)}
                )
            return result

    def mark_single_complete(self, upload_id, round_id, files):
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES_PER_REQUEST:
            raise ValueError("invalid completed file list")
        with self.lock, self.db:
            self._active_round(upload_id, round_id)
            for item in files:
                if not isinstance(item, dict):
                    raise ValueError("completed file entry must be an object")
                path = normalize_path(item.get("path"))
                size, etag = item.get("size"), item.get("etag") or ""
                row = self.db.execute(
                    "SELECT * FROM files WHERE round_id = ? AND path = ?", (round_id, path)
                ).fetchone()
                if not row:
                    raise NotFound("file is not registered")
                if row["mode"] != "single" or row["size"] != size:
                    raise Conflict("completed file does not match manifest")
                if row["state"] == "complete":
                    continue
                self.storage.promote(
                    f"{INBOX_PREFIX}/.staging/{upload_id}/{round_id}/{path}",
                    f"{INBOX_PREFIX}/{upload_id}/{path}", etag,
                )
                self.db.execute(
                    "UPDATE files SET state = 'complete', etag = ? WHERE round_id = ? AND path = ?",
                    (etag, round_id, path),
                )
                self._remember_file(upload_id, row)
        # Publish and commit first; a temporary-object cleanup failure must not undo success.
        for item in files:
            try:
                self.storage.discard_staged(
                    f"{INBOX_PREFIX}/.staging/{upload_id}/{round_id}/{normalize_path(item['path'])}"
                )
            except Exception:
                logging.getLogger(__name__).warning("Temporary upload cleanup failed")
        return {"completed": len(files)}

    def _remember_file(self, upload_id, row):
        self.db.execute(
            "INSERT OR REPLACE INTO collection_files VALUES (?, ?, ?, ?, ?)",
            (upload_id, row["path"], row["size"], row["is_directory"], row["content_hash"]),
        )

    def start_multipart(self, upload_id, round_id, path, part_size=None):
        path = normalize_path(path)
        with self.lock, self.db:
            self._active_round(upload_id, round_id)
            row = self.db.execute(
                "SELECT * FROM files WHERE round_id = ? AND path = ?", (round_id, path)
            ).fetchone()
            if not row:
                raise NotFound("file is not registered")
            if row["mode"] != "multipart":
                raise Conflict("file does not require multipart upload")
            if row["multipart_upload_id"]:
                return self._multipart_result(upload_id, row)
            part_size = DEFAULT_PART_SIZE if part_size is None else part_size
            if not isinstance(part_size, int) or not MIN_PART_SIZE <= part_size <= MAX_SINGLE_PUT:
                raise ValueError("partSize must be between 5 MiB and 5 GiB")
            part_count = math.ceil(row["size"] / part_size)
            if part_count < 1 or part_count > 10000:
                raise ValueError("invalid multipart part count")
            key = f"{INBOX_PREFIX}/{upload_id}/{path}"
            multipart_id = self.storage.create_multipart(key)
            self.db.execute(
                "UPDATE files SET multipart_upload_id = ?, part_size = ?, part_count = ? WHERE round_id = ? AND path = ?",
                (multipart_id, part_size, part_count, round_id, path),
            )
            return {
                "path": path,
                "objectKey": key,
                "multipartUploadId": multipart_id,
                "partSize": part_size,
                "partCount": part_count,
            }

    @staticmethod
    def _multipart_result(upload_id, row):
        return {
            "path": row["path"],
            "objectKey": f"{INBOX_PREFIX}/{upload_id}/{row['path']}",
            "multipartUploadId": row["multipart_upload_id"],
            "partSize": row["part_size"],
            "partCount": row["part_count"],
        }

    def _multipart_file(self, upload_id, round_id, path):
        path = normalize_path(path)
        self._active_round(upload_id, round_id)
        row = self.db.execute(
            "SELECT * FROM files WHERE round_id = ? AND path = ?", (round_id, path)
        ).fetchone()
        if not row or not row["multipart_upload_id"]:
            raise NotFound("multipart upload is not initialized")
        return row

    def sign_parts(self, upload_id, round_id, path, part_numbers):
        if not isinstance(part_numbers, list) or not 1 <= len(part_numbers) <= MAX_PARTS_PER_REQUEST:
            raise ValueError("invalid partNumbers")
        with self.lock:
            row = self._multipart_file(upload_id, round_id, path)
            numbers = []
            for number in part_numbers:
                if not isinstance(number, int) or not 1 <= number <= row["part_count"]:
                    raise ValueError("invalid part number")
                numbers.append(number)
            if len(set(numbers)) != len(numbers):
                raise ValueError("duplicate part number")
            key = f"{INBOX_PREFIX}/{upload_id}/{row['path']}"
            return [
                {
                    "partNumber": number,
                    "putUrl": self.storage.sign_part(key, row["multipart_upload_id"], number),
                }
                for number in numbers
            ]

    def list_parts(self, upload_id, round_id, path):
        with self.lock:
            row = self._multipart_file(upload_id, round_id, path)
            key = f"{INBOX_PREFIX}/{upload_id}/{row['path']}"
            return self.storage.list_parts(key, row["multipart_upload_id"])

    def complete_multipart(self, upload_id, round_id, path):
        with self.lock:
            row = self._multipart_file(upload_id, round_id, path)
            key = f"{INBOX_PREFIX}/{upload_id}/{row['path']}"
            if row["state"] == "complete":
                return {"path": row["path"], "objectKey": key, "etag": row["etag"] or ""}
            parts = self.storage.list_parts(key, row["multipart_upload_id"])
            expected = list(range(1, row["part_count"] + 1))
            if [part["partNumber"] for part in parts] != expected:
                raise Conflict("multipart upload has missing parts")
            for index, part in enumerate(parts, start=1):
                expected_size = min(row["part_size"], row["size"] - (index - 1) * row["part_size"])
                if part["size"] != expected_size:
                    raise Conflict("multipart part size does not match manifest")
            etag = self.storage.complete_multipart(key, row["multipart_upload_id"], parts)
            with self.db:
                self.db.execute(
                    "UPDATE files SET state = 'complete', etag = ? WHERE round_id = ? AND path = ?",
                    (etag or "", round_id, row["path"]),
                )
                self._remember_file(upload_id, row)
            return {"path": row["path"], "objectKey": key, "etag": etag or ""}

    def round_status(self, upload_id, round_id):
        with self.lock:
            row = self._round(upload_id, round_id)
            files = self.db.execute(
                "SELECT path, size, mode, state, content_hash, multipart_upload_id, part_size, part_count FROM files WHERE round_id = ? ORDER BY path",
                (round_id,),
            ).fetchall()
            return {
                "uploadId": upload_id,
                "roundId": round_id,
                "state": row["state"],
                "fileCount": row["expected_files"],
                "files": [
                    {
                        "path": item["path"],
                        "size": item["size"],
                        "sha256": item["content_hash"],
                        "mode": item["mode"],
                        "state": item["state"],
                        **(
                            {
                                "multipartUploadId": item["multipart_upload_id"],
                                "partSize": item["part_size"],
                                "partCount": item["part_count"],
                            }
                            if item["multipart_upload_id"]
                            else {}
                        ),
                    }
                    for item in files
                ],
            }

    def complete_round(self, upload_id, round_id):
        with self.lock, self.db:
            row = self._round(upload_id, round_id)
            if row["state"] == "complete":
                return {
                    "uploadId": upload_id,
                    "roundId": round_id,
                    "state": "complete",
                }
            self._active_round(upload_id, round_id)
            if row["state"] != "sealed" or row["expected_files"] is None:
                raise Conflict("manifest is not sealed")
            incomplete = self.db.execute(
                "SELECT COUNT(*) AS value FROM files WHERE round_id = ? AND state != 'complete'",
                (round_id,),
            ).fetchone()["value"]
            if incomplete:
                raise Conflict("upload round still has incomplete files")
            completed_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            self.storage.finish_round(upload_id, round_id, completed_at)
            self.db.execute(
                """
                INSERT OR REPLACE INTO collection_files(upload_id, path, size, is_directory, content_hash)
                SELECT ?, path, size, is_directory, content_hash FROM files WHERE round_id = ?
                """,
                (upload_id, round_id),
            )
            self.db.execute("UPDATE rounds SET state = 'complete' WHERE round_id = ?", (round_id,))
            self.db.execute(
                "UPDATE collections SET current_round_id = NULL WHERE upload_id = ?", (upload_id,)
            )
            return {
                "uploadId": upload_id,
                "roundId": round_id,
                "state": "complete",
                "completedAt": completed_at,
            }

    def ensure_legacy_allowed(self, upload_id):
        if not UPLOAD_ID_RE.fullmatch(upload_id):
            raise ValueError("invalid uploadId")
        with self.lock:
            row = self.db.execute(
                "SELECT current_round_id FROM collections WHERE upload_id = ?", (upload_id,)
            ).fetchone()
            if row and row["current_round_id"]:
                raise Conflict("roundId is required while an upload round is active")
