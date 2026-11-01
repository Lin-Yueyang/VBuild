"""Cancelable directory scan and explicit confirmation before expensive NEB indexing."""

from functools import partial
from xml.etree.ElementTree import ParseError

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                              QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .neb import scan, load
from .neb_cache import NebCache


class NebTask(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, operation, value, parent):
        super().__init__(parent)
        self.operation, self.value = operation, value

    def run(self):
        try:
            result = self.operation(self.value, self.progress.emit, self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.ready.emit(result)
        except (OSError, ValueError, KeyError, IndexError, ParseError) as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))


class NebDialog(QDialog):
    def __init__(self, root, parent):
        super().__init__(parent)
        self.root, self.groups, self.task, self.prepared = root, [], None, None
        self.canceling, self.scan_count = False, 0
        self.pending_cache = None
        self.setWindowTitle("导入 NEB · 扫描并确认")
        self.resize(980, 570)
        layout = QVBoxLayout(self)
        self.path = QLabel(str(root))
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path)
        row = QHBoxLayout()
        self.group = QComboBox()
        self.group.currentIndexChanged.connect(self.populate)
        row.addWidget(self.group, 1)
        self.refresh = QPushButton("刷新")
        self.refresh.clicked.connect(self.rescan)
        row.addWidget(self.refresh)
        layout.addLayout(row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["导入", "图像", "发现的文件", "几何/轨迹来源", "结构检查", "端点取末帧"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self.included)
        layout.addWidget(self.table, 1)
        self.config = QPlainTextEdit()
        self.config.setReadOnly(True)
        self.config.setMaximumHeight(110)
        self.config.setPlaceholderText("共用 INCAR / KPOINTS（若存在）")
        layout.addWidget(self.config)
        self.message = QLabel("扫描只检查结构；点击导入后才读取能量、力和完整优化步。")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox()
        self.import_button = self.buttons.addButton("导入", QDialogButtonBox.ButtonRole.AcceptRole)
        self.import_button.clicked.connect(self.import_run)
        self.cancel = self.buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.cancel.clicked.connect(self.reject)
        layout.addWidget(self.buttons)
        self.rescan()

    @property
    def current(self):
        return self.groups[self.group.currentIndex()] if self.groups else None

    def start(self, operation, value, callback):
        self.refresh.setEnabled(False)
        self.group.setEnabled(False)
        self.table.setEnabled(False)
        self.import_button.setEnabled(False)
        self.task = NebTask(operation, value, self)
        self.task.ready.connect(callback)
        self.task.failed.connect(self.failed)
        self.task.progress.connect(self.message.setText)
        self.task.finished.connect(self.finished_task)
        self.task.start()

    def rescan(self):
        self.message.setText("正在扫描结构…")
        self.start(scan, self.root, self.scanned)

    def scanned(self, groups):
        self.groups = groups
        self.scan_count += 1
        self.group.blockSignals(True)
        self.group.clear()
        for group in groups:
            self.group.addItem(str(group.root.relative_to(self.root)) if group.root != self.root else self.root.name)
        self.group.blockSignals(False)
        self.populate()

    def populate(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        group = self.current
        if group is not None:
            self.table.setRowCount(len(group.images))
            for row, item in enumerate(group.images):
                box = QTableWidgetItem()
                box.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                box.setCheckState(Qt.CheckState.Checked if item.included else Qt.CheckState.Unchecked)
                if not item.seeds:
                    box.setFlags(Qt.ItemFlag.NoItemFlags)
                self.table.setItem(row, 0, box)
                endpoint = QTableWidgetItem()
                endpoint.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                endpoint.setCheckState(Qt.CheckState.Checked if item.endpoint else Qt.CheckState.Unchecked)
                endpoint.setToolTip("端点只取所选来源的最后一个完整步；其优化历史不参加 NEB 播放。")
                self.table.setItem(row, 5, endpoint)
                for column, text in ((1, item.folder.name), (2, ", ".join(item.files) or "未发现结构文件"),
                                      (4, "待核对" if item.seed is not None else "没有完整结构")):
                    cell = QTableWidgetItem(text)
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    cell.setToolTip("\n".join(name + ": " + error for name, error in item.errors.items()) or str(item.folder))
                    self.table.setItem(row, column, cell)
                sources = QComboBox()
                for name in item.seeds:
                    sources.addItem(name)
                sources.setCurrentText(item.source)
                sources.currentTextChanged.connect(lambda value, target=item: self.source_changed(target, value))
                self.table.setCellWidget(row, 3, sources)
            self.config.setPlainText("\n\n".join(name + "\n" + value for name, value in group.config.items()))
        else:
            self.config.clear()
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.validate()

    def included(self, cell):
        if cell.column() == 0:
            self.current.images[cell.row()].included = cell.checkState() == Qt.CheckState.Checked
            self.validate()
        elif cell.column() == 5:
            self.current.images[cell.row()].endpoint = cell.checkState() == Qt.CheckState.Checked

    def source_changed(self, item, value):
        item.source = value
        self.validate()

    def validate(self):
        group = self.current
        issues = group.problems() if group is not None else ["未找到 00、01、02… 图像目录。"]
        self.table.blockSignals(True)
        reference = next((item.seed for item in group.images if item.included and item.seed is not None), None) if group else None
        if group is not None:
            from .neb import compatible
            for row, item in enumerate(group.images):
                label = "未选中" if not item.included else "一致" if reference is not None and item.seed is not None and compatible(reference.structure, item.seed.structure) else "不一致"
                self.table.item(row, 4).setText(label)
        self.table.blockSignals(False)
        self.import_button.setEnabled(not issues and self.task is None)
        self.message.setText("；".join(issues) if issues else "结构一致。点击导入后读取能量、力和完整优化步；空能量允许导入。")

    def import_run(self):
        self.message.setText("正在导入完整步骤与原始数据…")
        try:
            self.pending_cache = NebCache(self.parentWidget().settings_path.parent / "cache")
        except OSError as error:
            self.message.setText("无法创建 NEB 临时缓存："+str(error))
            return
        self.start(partial(load, cache=self.pending_cache), self.current, self.imported)

    def imported(self, run):
        self.prepared = run

    def failed(self, text):
        self.prepared = None
        self.message.setText("读取失败：" + text)
        self.last_error = text

    def finished_task(self):
        self.task.deleteLater()
        self.task = None
        self.refresh.setEnabled(True)
        self.group.setEnabled(True)
        self.table.setEnabled(True)
        if self.canceling:
            self.clear_cache()
            self.prepared = None
            super().reject()
        elif self.prepared is not None:
            self.pending_cache = None
            self.accept()
        elif hasattr(self, "last_error"):
            self.clear_cache()
            self.import_button.setEnabled(False)
            del self.last_error
        else:
            self.validate()

    def clear_cache(self):
        if self.pending_cache is not None:
            self.pending_cache.close()
            self.pending_cache = None

    def reject(self):
        if self.task is not None:
            self.canceling = True
            self.task.requestInterruption()
            self.message.setText("正在停止读取…")
            return
        super().reject()

    def closeEvent(self, event):
        if self.task is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
