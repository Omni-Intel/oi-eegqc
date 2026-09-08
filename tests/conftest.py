"""Dispose desktop widgets before Python/Qt interpreter shutdown."""
import pytest
import sys


@pytest.fixture(autouse=True)
def dispose_desktop_windows():
    yield
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        shutdown = getattr(widget, "shutdown", None)
        if shutdown is not None:
            shutdown()
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
