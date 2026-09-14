"""One local/CI verification entry point for the shipped desktop product."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from oi_eegqc import __version__
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibrate", action="store_true", help="Run the injected-fault score checks")
    parser.add_argument("--tag", help="Release tag, which must match the application version")
    args = parser.parse_args()
    if args.tag and args.tag != f"v{__version__}":
        parser.error(f"Release tag must be v{__version__}")
    missing = [name for name in ("pytest", "PySide6", "mne") if importlib.util.find_spec(name) is None]
    if missing:
        parser.error("Install .[desktop,dev] first; missing " + ", ".join(missing))
    commands = [[sys.executable, "-m", "pytest", "-q", "--tb=short"]]
    if args.calibrate:
        commands.append([sys.executable, "examples/calibrate_thresholds.py", "-o", "build/calibration.json"])
    for command in commands:
        result = subprocess.run(command, cwd=root)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
