# Build with: python -m PyInstaller --noconfirm eegqc.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / "LICENSE"), "licenses/oi-eegqc"),
         (str(root / "assets" / "omni-intelli logo" / "OMNI_LOGO_100x100.ico"), "assets/omni-intelli logo")]
datas += collect_data_files("mne")
for package in ("mne", "numpy", "scipy", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6"):
    datas += copy_metadata(package)
a = Analysis([str(root / "desktop_entry.py")], pathex=[str(root / "src")],
             binaries=[], datas=datas,
             hiddenimports=["oi_eegqc.config", "oi_eegqc.datasets", "oi_eegqc.io",
                            "oi_eegqc.pipeline", "oi_eegqc.protocol", "oi_eegqc.types"]
                           + collect_submodules("mne", filter=lambda name: ".tests" not in name),
             excludes=["tkinter", "pytest", "IPython", "notebook", "PySide6.QtWebEngineCore"],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="OI-EEGQC", console=False,
          icon=str(root / "assets" / "omni-intelli logo" / "OMNI_LOGO_100x100.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="OI-EEGQC")
