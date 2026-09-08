"""Spawn-safe scoring service. No Qt or numerical imports in the parent."""
import multiprocessing
import time

DEFAULT_FILE_TIMEOUT_S = 15 * 60


def serve_scores(connection):
    try:
        from .desktop_service import score_file
        while True:
            request = connection.recv()
            if request is None:
                break
            try:
                report = score_file(request["path"], **request["metadata"],
                                    progress=lambda phase: connection.send(("phase", phase)))
                connection.send(("result", report.to_dict()))
            except Exception as exc:
                connection.send(("error", str(exc).lower()))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class ScoringProcess:
    def __init__(self, target=serve_scores):
        self.context = multiprocessing.get_context("spawn")
        self.target = target
        self.process = self.connection = None

    def start(self):
        self.close()
        parent, child = self.context.Pipe()
        process = self.context.Process(target=self.target, args=(child,), daemon=True)
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self.process, self.connection = process, parent

    def score(self, path, metadata, cancelled, phase, timeout_s):
        start = time.monotonic()
        if self.process is None or not self.process.is_alive():
            self.start()
        try:
            if cancelled():
                return "cancelled", None
            self.connection.send({"path": path, "metadata": metadata})
            while True:
                if cancelled():
                    return "cancelled", None
                if time.monotonic() - start >= timeout_s:
                    return "timeout", "评分超时，可重试"
                if self.connection.poll(0.05):
                    kind, payload = self.connection.recv()
                    if kind == "phase":
                        phase(payload)
                    else:
                        return kind, payload
                elif not self.process.is_alive():
                    return "crash", "评分进程异常退出，可重试"
        except (EOFError, BrokenPipeError, OSError):
            return "crash", "评分进程异常退出，可重试"

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=1)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=1)
            self.process.close()
            self.process = None
