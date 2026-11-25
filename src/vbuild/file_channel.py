"""Forward Windows file-open requests to the workspace for this installation."""
import getpass
import hashlib
import json
import time
from pathlib import Path

from PySide6.QtCore import QDir, QLockFile, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def route_files(paths, receive, settings_path):
    identity = (str(Path(__file__).resolve().parent), getpass.getuser(), str(settings_path or ""))
    name = "VBuild-" + hashlib.sha256(repr(identity).encode()).hexdigest()[:32]
    lock = QLockFile(QDir.temp().filePath(name + ".lock"))
    lock.setStaleLockTime(0)
    deadline = time.monotonic() + 5
    while not lock.tryLock(0):
        peer = QLocalSocket()
        peer.connectToServer(name)
        if peer.waitForConnected(120):
            payload = json.dumps([str(Path(p).absolute()) for p in paths]).encode() + b"\n"
            peer.write(payload)
            peer.flush()
            if peer.bytesToWrite() and not peer.waitForBytesWritten(3000):
                raise OSError("无法发送文件到已有 VBuild 窗口")
            peer.disconnectFromServer()
            return None
        if time.monotonic() >= deadline:
            raise OSError("已有 VBuild 窗口暂时无法接收文件")
        time.sleep(.05)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(name):
        raise OSError(server.errorString())

    def accept():
        peer = server.nextPendingConnection()
        def read():
            if not peer.canReadLine():
                return
            try:
                files = json.loads(bytes(peer.readLine()))
            except (ValueError, UnicodeError):
                return
            if isinstance(files, list) and all(isinstance(p, str) and Path(p).is_absolute() for p in files):
                QTimer.singleShot(0, lambda: receive([Path(p) for p in files]))
        peer.readyRead.connect(read)
        peer.disconnected.connect(peer.deleteLater)
        read()
    server.newConnection.connect(accept)
    return lock, server
