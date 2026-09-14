"""Qt task adapters shared by the production GUI and legacy regression harness."""
import logging
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QThread, Signal, QStandardPaths
from .scoring_process import ScoringProcess, DEFAULT_FILE_TIMEOUT_S


def configure_logging():
    """Bounded local diagnostics; never record signal arrays."""
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger("oi_eegqc.desktop")
    if logger.handlers:
        return
    try:
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / "desktop.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
    except OSError:
        logger.addHandler(logging.NullHandler())


class UpdateWorker(QThread):
    def run(self):
        from .. import __version__
        from ..desktop_update import check_update, UpdateInfo
        try:
            self.result = check_update(__version__)
        except Exception:
            logging.getLogger("oi_eegqc.desktop").exception("Update check failed")
            self.result = UpdateInfo(status="error", current=__version__)


class ReportView:
    """Display plain report data without unpickling the scientific stack."""
    def __init__(self, payload):
        self.payload = payload
        value = payload.get("availability")
        self.availability = SimpleNamespace(value=value) if value is not None else None

    def __getattr__(self, name):
        try:
            return self.payload[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def to_dict(self):
        return self.payload


class BatchWorker(QThread):
    started_file = Signal(int)
    scored = Signal(int, object)
    failed = Signal(int, str)
    phase_changed = Signal(int, str)
    cancelled_file = Signal(int)

    def __init__(self, jobs, timeout_s=DEFAULT_FILE_TIMEOUT_S, process_factory=ScoringProcess):
        super().__init__()
        self.jobs = jobs
        self.timeout_s = timeout_s
        self.process_factory = process_factory

    def run(self):
        client = self.process_factory()
        caches = {}
        try:
            for job in self.jobs:
                index, path, metadata = job[:3]
                cache_path = job[3] if len(job) > 3 else None
                if self.isInterruptionRequested():
                    break
                self.started_file.emit(index)
                digest = source_stat = cache = None
                if cache_path:
                    try:
                        from ..score_cache import CacheCancelled, ScoreCache

                        cache = caches.setdefault(str(cache_path), ScoreCache(cache_path))
                        self.phase_changed.emit(index, "核验缓存")
                        digest, source_stat = cache.digest(path, self.isInterruptionRequested)
                        cached = cache.lookup(digest, metadata)
                        if cached is not None:
                            report = ReportView(cached)
                            report.content_sha256 = digest
                            self.scored.emit(index, report)
                            continue
                    except CacheCancelled:
                        self.cancelled_file.emit(index)
                        break
                    except Exception:
                        cache = digest = source_stat = None
                        logging.getLogger("oi_eegqc.desktop").exception("Score cache lookup failed")
                try:
                    kind, payload = client.score(path, metadata, self.isInterruptionRequested,
                                                 lambda phase: self.phase_changed.emit(index, phase), self.timeout_s)
                    if kind == "cancelled":
                        client.close()
                        self.cancelled_file.emit(index)
                        break
                    if kind == "result":
                        if cache is not None and digest is not None:
                            try:
                                current = Path(path).stat()
                                if source_stat == (current.st_size, current.st_mtime_ns):
                                    cache.store(digest, metadata, payload)
                            except Exception:
                                logging.getLogger("oi_eegqc.desktop").exception("Score cache store failed")
                        report = ReportView(payload)
                        report.content_sha256 = digest
                        self.scored.emit(index, report)
                    else:
                        client.close()
                        logging.getLogger("oi_eegqc.desktop").warning("Scoring %s: %s", kind, payload)
                        self.failed.emit(index, payload)
                except Exception as exc:
                    client.close()
                    logging.getLogger("oi_eegqc.desktop").exception("Scoring failed")
                    self.failed.emit(index, str(exc).lower())
        finally:
            client.close()
            for cache in caches.values():
                cache.close()
