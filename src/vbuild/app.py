"""Keep Qt imports out of the file-processing command-line path."""

from pathlib import Path
import sys
import ctypes
import json
from time import perf_counter

from PySide6.QtCore import QByteArray, QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMainWindow, QToolBar, QVBoxLayout
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtGui import QIcon
from .paths import work_dir


class Application(QApplication):
    """Receive Finder Open/drag events during startup and after the UI is ready."""

    def __init__(self, arguments):
        super().__init__(arguments)
        self.shell = None
        self.pending_files = []

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            path = Path(event.file())
            if self.shell is None:
                self.pending_files.append(path)
            elif self.shell.workspace is None:
                self.shell.paths.append(path)
            else:
                self.shell.workspace.open_path(path)
            event.accept()
            return True
        return super().event(event)


class StartupWindow(QMainWindow):
    """Paint a responsive Qt window before importing the 3D/scientific modules."""

    def __init__(self, paths, settings_path=None):
        super().__init__()
        self.paths = list(paths)
        self.workspace, self.closing = None, False
        self.resize(1100, 760)
        self.setWindowTitle("VBuild")
        self.setWindowIcon(QApplication.windowIcon())
        settings = work_dir() / "settings.json" if settings_path is None else settings_path
        self.settings_path = settings
        if settings.exists():
            geometry = json.loads(settings.read_text(encoding="utf-8")).get("geometry")
            if geometry:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        surface = QOpenGLWidget()
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(0, 0, 0, 0)
        body = QLabel("正在准备三维视窗…")
        self.loading_message = body
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setStyleSheet("background: white; color: #657483; font-size: 16px;")
        layout.addWidget(body)
        self.setCentralWidget(surface)
        self.load_status = QLabel("正在载入三维视窗")
        self.statusBar().addPermanentWidget(self.load_status)
        file = self.menuBar().addMenu("文件")
        file.addAction("打开", self.open_files)
        file.addAction("退出", self.close)
        for name in ("编辑", "视图", "模式", "设置"):
            menu = self.menuBar().addMenu(name)
            menu.addAction("载入中，请稍候…", self.wait_message)
        bar = QToolBar("视角", self)
        self.loading_toolbar = bar
        self.addToolBar(bar)
        for name in ("a", "b", "c", "切面扩胞", "添加原子", "添加分子"):
            bar.addAction(name, self.wait_message)
        self.setAcceptDrops(True)

    def wait_message(self):
        self.statusBar().showMessage("功能载入中，请稍候…", 3000)

    def createPopupMenu(self):
        return self.workspace.createPopupMenu() if self.workspace is not None else None

    def open_files(self):
        paths, _filter = QFileDialog.getOpenFileNames(self, "导入结构", "", "所有文件 (*)")
        self.paths.extend(Path(path) for path in paths)
        self.wait_message()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if self.workspace is None:
            self.paths.extend(paths)
        else:
            for path in paths:
                self.workspace.open_path(path)
        event.acceptProposedAction()
        self.wait_message()

    def closeEvent(self, event):
        if self.workspace is not None:
            self.workspace.closeEvent(event)
            return
        self.closing = True
        settings = json.loads(self.settings_path.read_text(encoding='utf-8')) if self.settings_path.exists() else {}
        settings['geometry'] = bytes(self.saveGeometry().toBase64()).decode('ascii')
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
        event.accept()


def launch(path: Path | None = None, *, paths=(), settings_path=None):
    started = perf_counter()
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VBuild.StructureEditor")
    application = Application(sys.argv[:1])
    application.setApplicationName("VBuild")
    application.setApplicationDisplayName("VBuild")
    from .language import chinese
    chinese()
    application.setWindowIcon(QIcon(str(Path(__file__).with_name("icons") / "app.png")))
    from .warmup import ImportTask
    shell = StartupWindow(([path] if path is not None else []) + list(paths), settings_path)
    application.shell = shell
    shell.paths.extend(application.pending_files)
    application.pending_files.clear()
    task = ImportTask((("三维视窗", "vbuild.scene"), ("界面功能", "vbuild.window")), application)
    task.progress.connect(lambda name: shell.load_status.setText("正在载入 " + name))
    def failed(error, trace):
        shell.load_status.setText("载入失败")
        shell.loading_message.setText("载入失败：" + str(error))
        sys.excepthook(type(error), error, trace)
    task.failed.connect(failed)
    application.aboutToQuit.connect(task.finish)
    window = None

    def ready():
        nonlocal window
        if shell.closing:
            return
        from .window import Window
        window = Window(parent=shell, settings_path=settings_path)
        shell.workspace = window
        shell.menuBar().hide()
        shell.statusBar().hide()
        shell.removeToolBar(shell.loading_toolbar)
        shell.loading_toolbar.hide()
        shell.setCentralWidget(window)
        shell.setWindowTitle(window.windowTitle())
        window.windowTitleChanged.connect(shell.setWindowTitle)
        window.show()
        if sys.platform == 'darwin':
            shell.setMenuBar(window.menuBar())
            shell.menuBar().show()
        print(f"Startup: workspace ready {perf_counter() - started:.3f}s", flush=True)
        for source in shell.paths:
            window.open_path(source)

    task.completed.connect(ready)
    shell.show()
    QTimer.singleShot(0, lambda: (print(f"Startup: Qt window visible {perf_counter() - started:.3f}s", flush=True), task.start()))
    sys.exit(application.exec())
