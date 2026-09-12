# Build with: python -m PyInstaller --noconfirm eegqc.spec
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
datas = [(str(root / "LICENSE"), "licenses/oi-eegqc"),
         (str(root / "assets" / "omni-intelli logo" / "OMNI_LOGO_100x100.ico"), "assets/omni-intelli logo")]
datas += [(str(root / "src" / "oi_eegqc" / "qml"), "oi_eegqc/qml")]
datas += [(str(root / "src" / "oi_eegqc" / "layouts"), "oi_eegqc/layouts")]
for package in ("mne", "numpy", "scipy", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6"):
    datas += copy_metadata(package)
a = Analysis([str(root / "desktop_entry.py")], pathex=[str(root / "src")],
             binaries=[], datas=datas,
             hiddenimports=["oi_eegqc.config", "oi_eegqc.datasets", "oi_eegqc.io",
                            "oi_eegqc.intake", "oi_eegqc.desktop_service", "oi_eegqc.desktop_update",
                            "oi_eegqc.layouts", "oi_eegqc.quick", "oi_eegqc.desktop_upload", "oi_eegqc.upload_ui",
                            "oi_eegqc.pipeline", "oi_eegqc.protocol", "oi_eegqc.types"]
                           + ["mne.io.edf.edf", "mne._fiff.pick"],
             excludes=["tkinter", "pytest", "IPython", "notebook", "matplotlib",
                       "PySide6.QtWebEngineCore"],
             noarchive=False)

# PyInstaller's Qt QML hook follows the QtQuick.Controls dependency graph into
# optional PySide6 Addons modules.  The application imports only QtQuick,
# Controls.Basic, Layouts, and Dialogs; shipping the unrelated modules adds
# hundreds of megabytes and thousands of files without providing a feature.
unused_qml_roots = tuple(
    f"pyside6/qml/{name}/" for name in (
        "qt3d", "qt5compat", "qtcharts", "qtdatavisualization", "qtgraphs",
        "qtlocation", "qtmultimedia", "qtpositioning", "qtquick3d",
        "qtremoteobjects", "qtscxml", "qtsensors", "qttest", "qttexttospeech",
        "qtwebchannel", "qtwebengine", "qtwebsockets", "qtwebview",
    )
)
unused_control_styles = tuple(
    f"pyside6/qml/qtquick/controls/{name}/" for name in (
        "fluentwinui3", "fusion", "imagine", "material", "universal",
        "windows", "macos", "ios",
    )
)
unused_qtquick_roots = tuple(
    f"pyside6/qml/qtquick/{name}/" for name in (
        "effects", "localstorage", "particles", "pdf", "scene2d", "scene3d",
        "timeline", "tooling", "vectorimage", "virtualkeyboard",
    )
)
unused_qt_families = (
    "qt3d", "qt5compat", "qtcharts", "qtdatavisualization", "qtgraphs",
    "qtlocation", "qtmultimedia", "qtpdf", "qtpositioning", "qtquick3d",
    "qtremoteobjects", "qtscxml", "qtsensors", "qtspatialaudio", "qttest",
    "qttexttospeech", "qtvirtualkeyboard", "qtwebchannel", "qtwebengine",
    "qtwebsockets", "qtwebview",
)
unused_style_families = tuple(
    "qtquickcontrols2" + name for name in (
        "fluentwinui3", "fusion", "imagine", "material", "universal", "windows",
    )
)


def keep_runtime(entry):
    destination = str(entry[0]).replace("\\", "/").casefold()
    if destination.startswith(unused_qml_roots + unused_control_styles + unused_qtquick_roots):
        return False
    leaf = destination.rsplit("/", 1)[-1]
    family_leaf = "qt" + leaf[3:] if leaf.startswith("qt6") else leaf
    if destination.startswith("pyside6/") and family_leaf.startswith(unused_qt_families + unused_style_families):
        return False
    return not (
        destination.startswith("pyside6/resources/qtwebengine")
        or destination.startswith("pyside6/translations/qtwebengine_locales/")
        or destination.startswith("pyside6/plugins/qmltooling/")
        or (destination.startswith("pyside6/translations/")
            and destination.endswith(".qm") and "zh_cn" not in leaf)
    )


a.binaries = [entry for entry in a.binaries if keep_runtime(entry)]
a.datas = [entry for entry in a.datas if keep_runtime(entry)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="OI-EEGQC", console=False,
          icon=str(root / "assets" / "omni-intelli logo" / "OMNI_LOGO_100x100.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="OI-EEGQC")
