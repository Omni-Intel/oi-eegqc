from test_quick import quick,recording,wait
from PySide6.QtCore import QObject,QPoint,QPointF
from PySide6.QtTest import QTest
from PySide6.QtGui import QGuiApplication,QCursor
import pytest

@pytest.fixture
def mouse_position():
    original=QCursor.pos()
    yield
    if QGuiApplication.platformName()=='windows':QCursor.setPos(original)

def move(window,point):
    if QGuiApplication.platformName()=='windows':QCursor.setPos(window.mapToGlobal(point))
    QTest.mouseMove(window,point)

def test_stationary_hover_remains_stable_with_grouped_and_standalone_files(quick,tmp_path,mouse_position):
    app,c,engine,window=quick
    if QGuiApplication.platformName()=='windows':
        from oi_eegqc.window_activation import show_window
        show_window(window)
    folder=tmp_path/'folder';folder.mkdir()
    recording(folder/'grouped.npy');recording(tmp_path/'standalone.npy')
    c.add_paths([str(folder),str(tmp_path/'standalone.npy')]);wait(app,lambda:not c._busy)
    QTest.qWait(100)
    for index in (0,1):
        row=find_visual(window.contentItem(),'fileRow'+str(index));assert row is not None
        point=row.mapToScene(QPointF(50,27))
        move(window,QPoint(int(point.x()),int(point.y())))
        QTest.qWait(100);assert row.property('hovered')
        changes=[];row.hoveredChanged.connect(lambda:changes.append(True))
        # Cross several old tooltip delays and the controller refresh interval.
        for _ in range(12):
            c.changed.emit();QTest.qWait(100)
            assert row.property('hovered')
        assert not changes
        move(window,QPoint(10,10));QTest.qWait(50)
        assert not row.property('hovered')


def test_hover_entry_and_exit_never_flash_dark(quick,tmp_path,mouse_position):
    app,c,engine,window=quick
    if QGuiApplication.platformName()=='windows':
        from oi_eegqc.window_activation import show_window
        show_window(window)
    folder=tmp_path/'folder';folder.mkdir()
    recording(folder/'grouped.npy');recording(tmp_path/'standalone.npy')
    c.add_paths([str(folder),str(tmp_path/'standalone.npy')]);wait(app,lambda:not c._busy)
    move(window,QPoint(10,10));QTest.qWait(150)
    for index in (0,1):
        row=find_visual(window.contentItem(),'fileRow'+str(index))
        sample=row.mapToScene(QPointF(8,27))
        inside=row.mapToScene(QPointF(50,27)).toPoint()
        levels=[]
        # Observe the transition itself, including interrupted entry/exit.
        for duration in (150,20,150):
            for point in (inside,QPoint(10,10)):
                move(window,point)
                for _ in range(max(1,duration//10)):
                    QTest.qWait(10)
                    frame=window.grabWindow()
                    if not frame.isNull():
                        color=frame.pixelColor(int(sample.x()*frame.width()/window.width()),int(sample.y()*frame.height()/window.height()))
                    else:
                        color=row.property('color')
                    # Composite over the white sheet (offscreen fallback).
                    levels.append(min(round(v*color.alphaF()+255*(1-color.alphaF())) for v in (color.red(),color.green(),color.blue())))
        assert levels and min(levels)>=246, f'row {index}: transient background reached {min(levels)}'


def find_visual(item,name):
    if item.objectName()==name:return item
    for child in item.childItems():
        found=find_visual(child,name)
        if found is not None:return found
