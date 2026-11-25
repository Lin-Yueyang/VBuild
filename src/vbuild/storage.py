"""Windows workspace cache preferences and background maintenance."""

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QFormLayout, QLabel, QMessageBox,
                               QPushButton, QSpinBox, QWidget)

from .cache_cleanup import clean


class Sweep(QThread):
    def __init__(self, work, days, parent):
        super().__init__(parent)
        self.work, self.days = work, days

    def run(self):
        try:
            count, size, busy, errors = clean(self.work, self.days)
            self.summary = f"已清理 {count} 个文件，释放 {size / 1048576:.2f} MiB。"
            if busy:
                self.summary += f" 跳过 {busy} 个使用中的目录。"
            if errors:
                self.summary += "\n" + "\n".join(errors)
        except OSError as error:
            self.summary = str(error)


class Maintenance(QObject):
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.task = None
        self.summary = ""
        self.timer = QTimer(self)
        self.timer.setInterval(24 * 3600 * 1000)
        self.timer.timeout.connect(self.scheduled)
        self.timer.start()
        QTimer.singleShot(5000, self.scheduled)
        QApplication.instance().aboutToQuit.connect(self.finish)

    def scheduled(self):
        if not self.window.closing and self.window.settings.get("auto_clean_cache", False):
            self.start(self.window.settings.get("cache_days", 30))

    def start(self, days=None):
        if self.task is not None:
            return
        self.task = Sweep(self.window.settings_path.parent, days, self)
        self.task.finished.connect(self.completed)
        self.summary = "正在清理…"
        self.changed.emit()
        self.task.start()

    def completed(self):
        self.summary = self.task.summary
        self.task.deleteLater()
        self.task = None
        self.changed.emit()

    def finish(self):
        if self.task is not None:
            self.task.wait()


class StoragePage(QWidget):
    def __init__(self, manager, settings):
        super().__init__()
        self.manager = manager
        layout = QFormLayout(self)
        self.auto = QCheckBox("每天自动清理过期缓存")
        self.auto.setChecked(settings.get("auto_clean_cache", False))
        self.days = QSpinBox()
        self.days.setRange(1, 365)
        self.days.setSuffix(" 天")
        self.days.setValue(settings.get("cache_days", 30))
        self.days.setEnabled(self.auto.isChecked())
        self.auto.toggled.connect(self.days.setEnabled)
        layout.addRow(self.auto)
        layout.addRow("保留时间", self.days)
        self.button = QPushButton("立即清理缓存")
        self.button.clicked.connect(self.manual)
        layout.addRow(self.button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addRow(self.status)
        layout.addRow(QLabel("清理范围为闲置 NEB 网格和未完成的轨迹导出。"))
        folder = QPushButton("打开工作目录")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(manager.window.settings_path.parent))))
        layout.addRow(folder)
        manager.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.status.setText(self.manager.summary)
        self.button.setEnabled(self.manager.task is None)

    def manual(self):
        answer = QMessageBox.question(self, "清理缓存",
            "清理全部闲置 NEB 网格和未完成的轨迹导出？\n如有旧版 VBuild 正在使用此目录，请先关闭旧版。")
        if answer == QMessageBox.StandardButton.Yes:
            self.manager.start()
