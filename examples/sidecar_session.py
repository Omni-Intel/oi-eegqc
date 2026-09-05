"""Reference stdio client. A Windows shell should mirror this, not the human CLI.

    python examples/sidecar_session.py
    python examples/sidecar_session.py --score-synthetic
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Iterator, TextIO


PROTOCOL = "oi-eegqc-protocol-v1"


class Sidecar:
    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self.proc = proc
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("sidecar stdio not piped")
        self.stdin: TextIO = proc.stdin
        self.stdout: TextIO = proc.stdout

    def send(self, payload: dict[str, Any]) -> None:
        self.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.stdin.flush()

    def events(self) -> Iterator[dict[str, Any]]:
        for line in self.stdout:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("schema_version") != PROTOCOL:
                raise RuntimeError(f"unsupported protocol: {msg.get('schema_version')}")
            yield msg

    def until(self, request_id: str, event: str) -> dict[str, Any]:
        for msg in self.events():
            if msg.get("id") == request_id and msg.get("event") == event:
                return msg
            if msg.get("id") == request_id and msg.get("event") == "error":
                raise RuntimeError("{code}: {message}".format(**msg))
        raise RuntimeError(f"sidecar closed before {event!r} for {request_id}")

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.send({"id": "boot", "op": "shutdown"})
            except BrokenPipeError:
                pass
        self.proc.wait(timeout=10)


def spawn() -> Sidecar:
    cmd = [sys.executable, "-m", "oi_eegqc", "serve", "--stdio"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    return Sidecar(proc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score-synthetic", action="store_true")
    args = parser.parse_args(argv)
    sidecar = spawn()
    try:
        sidecar.send({"id": "boot", "op": "ping"})
        pong = sidecar.until("boot", "pong")
        print("pong", pong.get("version"), pong.get("protocol"))
        if not args.score_synthetic:
            return 0
        sidecar.send(
            {
                "id": "job-1",
                "op": "score_dataset",
                "dataset": "synthetic",
                "n_channels": 8,
                "duration_s": 4,
            }
        )
        done = sidecar.until("job-1", "done")
        summary = done.get("summary") or {}
        print("n_total", summary.get("n_total"), "cancelled", summary.get("cancelled"))
        return 0
    finally:
        sidecar.close()


if __name__ == "__main__":
    raise SystemExit(main())
