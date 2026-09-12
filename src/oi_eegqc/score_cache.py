"""Persistent content identities and scoring results for the desktop app."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .types import REPORT_SCHEMA_VERSION


# Bump only when a change can alter score output; UI and transport releases keep the cache.
SCORING_ALGORITHM_VERSION = "oi-eegqc-score-v1"


class CacheCancelled(Exception):
    pass


class ScoreCache:
    def __init__(self, database):
        self.database = Path(database)
        self.db = None

    def _connect(self):
        if self.db is not None:
            return self.db
        self.database.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.database, timeout=15)
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS file_hashes (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS score_reports (
                sha256 TEXT NOT NULL,
                parameters TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                report TEXT NOT NULL,
                PRIMARY KEY (sha256, parameters, algorithm_version)
            );
            """
        )
        self.db = db
        return self.db

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _path_key(path):
        return os.path.normcase(str(Path(path).resolve()))

    @staticmethod
    def parameters_key(metadata):
        return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def digest(self, path, cancelled=lambda: False):
        if cancelled():
            raise CacheCancelled()
        path = Path(path)
        before = path.stat()
        identity = (before.st_size, before.st_mtime_ns)
        key = self._path_key(path)
        with self._connect() as db:
            row = db.execute(
                "SELECT sha256 FROM file_hashes WHERE path = ? AND size = ? AND mtime_ns = ?",
                (key, *identity),
            ).fetchone()
            if row:
                return row[0], identity
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                if cancelled():
                    raise CacheCancelled()
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        after = path.stat()
        if identity != (after.st_size, after.st_mtime_ns):
            raise OSError("文件在核验期间发生变化")
        value = digest.hexdigest()
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO file_hashes(path, size, mtime_ns, sha256) VALUES (?, ?, ?, ?)",
                (key, *identity, value),
            )
        return value, identity

    def lookup(self, sha256, metadata):
        with self._connect() as db:
            row = db.execute(
                "SELECT report FROM score_reports WHERE sha256 = ? AND parameters = ? AND algorithm_version = ?",
                (sha256, self.parameters_key(metadata), SCORING_ALGORITHM_VERSION),
            ).fetchone()
        if not row:
            return None
        report = json.loads(row[0])
        if report.get("schema_version") != REPORT_SCHEMA_VERSION:
            return None
        return report

    def store(self, sha256, metadata, report):
        if report.get("schema_version") != REPORT_SCHEMA_VERSION:
            raise ValueError("unsupported report schema")
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO score_reports(sha256, parameters, algorithm_version, report) VALUES (?, ?, ?, ?)",
                (sha256, self.parameters_key(metadata), SCORING_ALGORITHM_VERSION, encoded),
            )
