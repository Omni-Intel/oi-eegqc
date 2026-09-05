from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from oi_eegqc.cli import main
from oi_eegqc.datasets import open_dataset, score_adapter
from oi_eegqc.io.reports import write_bench_json
from oi_eegqc.pipeline import evaluate_recording
from oi_eegqc.protocol import PROTOCOL_SCHEMA_VERSION, ProtocolError
from oi_eegqc.serve import run_stdio
from oi_eegqc.types import REPORT_SCHEMA_VERSION, RecordingInput
from oi_eegqc.datasets import synth_clean


def test_report_carries_schema_version():
    rec = RecordingInput(
        data=synth_clean(8, 250.0, 6.0, seed=3),
        sfreq=250.0,
        ch_names=[f"E{i}" for i in range(8)],
        unit="uV",
        expected_n_channels=8,
        clip_id="clip",
    )
    payload = evaluate_recording(rec).to_dict()
    assert payload["schema_version"] == REPORT_SCHEMA_VERSION
    assert payload["clip_id"] == "clip"
    assert "threshold_version" in payload


def test_score_adapter_cancel_keeps_partial_rows():
    adapter = open_dataset("synthetic", n_channels=8, duration_s=4.0)
    seen: list[str] = []

    def cancel() -> bool:
        return len(seen) >= 1

    def on_recording(rec, report) -> None:
        seen.append(rec.clip_id or "")

    rows, summary = score_adapter(adapter, on_recording=on_recording, cancel=cancel)
    assert summary["cancelled"] is True
    assert summary["n_total"] == 1
    assert len(rows) == 1
    assert rows[0]["clip_id"] == seen[0]


def test_write_bench_json_uses_protocol_envelope(tmp_path: Path):
    adapter = open_dataset("synthetic", n_channels=8, duration_s=4.0)
    rows, summary = score_adapter(adapter)
    path = write_bench_json(
        tmp_path / "bench.json",
        threshold_version="oi-eegqc-v0.2.0",
        reports=rows,
        summary=summary,
        extra={"dataset": "synthetic"},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["schema_version"] == PROTOCOL_SCHEMA_VERSION
    assert payload["kind"] == "batch"
    assert payload["event"] == "done"
    assert payload["dataset"] == "synthetic"
    assert payload["reports"][0]["schema_version"] == REPORT_SCHEMA_VERSION
    assert payload["reports"][0]["extras"]["dataset"] == "synthetic"


def test_cli_datasets_json(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["--json", "datasets"])
    assert ei.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["schema_version"] == PROTOCOL_SCHEMA_VERSION
    assert payload["kind"] == "datasets"
    names = {d["name"] for d in payload["datasets"]}
    assert names == {"npy", "hw", "nod", "things", "synthetic"}


def test_cli_datasets_json_after_subcommand(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["datasets", "--json"])
    assert ei.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "datasets"


def test_cli_ndjson_bench_synthetic(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["--ndjson", "bench", "synthetic", "--channels", "8", "--duration", "4"])
    assert ei.value.code == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    events = [json.loads(ln) for ln in lines]
    assert events[0]["event"] == "progress"
    done = [e for e in events if e.get("event") == "done"]
    assert len(done) == 1
    assert done[0]["kind"] == "batch"
    assert done[0]["ok"] is True
    assert done[0]["summary"]["n_total"] == 4
    assert done[0]["summary"]["cancelled"] is False


def test_cli_json_bench_requires_explicit_root(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["--json", "bench", "hw"])
    assert ei.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["code"] == "missing_root"


def test_stdio_ping_and_list_datasets():
    stdin = StringIO(
        json.dumps({"id": "1", "op": "ping"})
        + "\n"
        + json.dumps({"id": "2", "op": "list_datasets"})
        + "\n"
        + json.dumps({"id": "3", "op": "shutdown"})
        + "\n"
    )
    stdout = StringIO()
    assert run_stdio(stdin, stdout) == 0
    events = [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert events[0]["id"] == "1"
    assert events[0]["event"] == "pong"
    assert events[0]["protocol"] == PROTOCOL_SCHEMA_VERSION
    assert events[1]["kind"] == "datasets"
    assert {d["name"] for d in events[1]["datasets"]} >= {"npy", "synthetic"}
    assert events[2]["event"] == "shutdown"


def test_stdio_score_dataset_synthetic():
    stdin = StringIO(
        json.dumps(
            {
                "id": "job-1",
                "op": "score_dataset",
                "dataset": "synthetic",
                "n_channels": 8,
                "duration_s": 4,
            }
        )
        + "\n"
    )
    stdout = StringIO()
    assert run_stdio(stdin, stdout) == 0
    events = [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]
    progress = [e for e in events if e.get("event") == "progress"]
    done = [e for e in events if e.get("event") == "done"]
    assert len(progress) == 4
    assert len(done) == 1
    assert done[0]["id"] == "job-1"
    assert done[0]["summary"]["n_total"] == 4
    assert done[0]["reports"][0]["extras"]["dataset"] == "synthetic"


def test_stdio_unknown_op():
    stdin = StringIO(json.dumps({"id": "x", "op": "explode"}) + "\n")
    stdout = StringIO()
    assert run_stdio(stdin, stdout) == 0
    payload = json.loads(stdout.getvalue().splitlines()[0])
    assert payload["ok"] is False
    assert payload["code"] == "unknown_op"


def test_protocol_error_maps_unknown_unit():
    rec = RecordingInput(
        data=synth_clean(4, 250.0, 2.0, seed=0),
        sfreq=250.0,
        ch_names=["a", "b", "c", "d"],
        unit="furlongs",
    )
    with pytest.raises(ValueError, match="Unknown unit"):
        rec.to_uv_scale()
    err = ProtocolError("unknown_unit", "Unknown unit 'furlongs'")
    body = err.to_dict("1")
    assert body["ok"] is False
    assert body["id"] == "1"
    assert body["schema_version"] == PROTOCOL_SCHEMA_VERSION
