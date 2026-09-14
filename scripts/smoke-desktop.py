"""Exercise the packaged QML app and its spawned scorer before making an installer."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    from oi_eegqc import REPORT_SCHEMA_VERSION
    from oi_eegqc.scoring_version import SCORING_ALGORITHM_VERSION
    from oi_eegqc.datasets.synthetic import synth_clean
    import numpy as np
    with tempfile.TemporaryDirectory(prefix="eegqc-smoke-") as folder:
        root = Path(folder)
        startup = root / "startup.json"
        subprocess.run([str(executable), "--startup-check", str(startup)], check=True, timeout=60)
        status = json.loads(startup.read_text(encoding="utf-8"))
        if status.get("ui") != "qml" or status.get("heavy_modules") or not status.get("icon_loaded"):
            raise RuntimeError("Packaged QML startup check failed")
        source = root / "sample.npy"
        np.save(source, synth_clean(4, 250, 3))
        source.with_suffix(".json").write_text(json.dumps({"sfreq": 250, "unit": "uV"}), encoding="utf-8")
        result = root / "score.json"
        subprocess.run([str(executable), "--verify", str(source), str(result)], check=True, timeout=120)
        reports = json.loads(result.read_text(encoding="utf-8"))
        if len(reports) != 1 or reports[0].get("schema_version") != REPORT_SCHEMA_VERSION:
            raise RuntimeError("Packaged scorer did not return the current report schema")
        if reports[0]["extras"].get("algorithm_version") != SCORING_ALGORITHM_VERSION:
            raise RuntimeError("Packaged scorer uses a different algorithm")
        if not reports[0]["window_qa"].get("window_evidence") or reports[0]["gqi"] < 80:
            raise RuntimeError("Packaged scorer failed the clean-signal fixture")
    print("PASS: packaged QML startup, resources and spawned scoring process")


if __name__ == "__main__":
    main()
