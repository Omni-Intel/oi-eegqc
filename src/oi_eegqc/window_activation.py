"""Local window activation with explicit Windows foreground permission."""
import hashlib
import json
import os
from pathlib import Path
from PySide6.QtNetwork import QLocalSocket

def server_name(path):
    return 'omni-eegqc-'+hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:24]

def notify_existing(path):
    socket=QLocalSocket();socket.connectToServer(server_name(path))
    if not socket.waitForConnected(150):return False
    if not socket.canReadLine():socket.waitForReadyRead(1000)
    try:pid=int(json.loads(bytes(socket.readLine()))['pid'])
    except (ValueError,KeyError,TypeError):raise RuntimeError('EEGQC 窗口未响应，请关闭旧窗口后重试')
    if os.name=='nt':
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(pid)
    socket.write(b'activate\n');socket.flush();socket.waitForBytesWritten(300)
    if not socket.canReadLine():socket.waitForReadyRead(1000)
    shown=bytes(socket.readLine()).strip()==b'shown'
    socket.disconnectFromServer()
    if not shown:raise RuntimeError('EEGQC 未确认窗口显示，请重试')
    return True

def show_window(window):
    window.showNormal();window.raise_();window.requestActivate()
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        user=ctypes.windll.user32
        user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
        user.SetForegroundWindow.argtypes=[wintypes.HWND]
        user.ShowWindow(int(window.winId()),9)
        user.SetForegroundWindow(int(window.winId()))
