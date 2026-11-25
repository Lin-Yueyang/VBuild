"""One explicit format selection and export options, including trajectory stride."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QEventLoop, QIdentityProxyModel, QSize, QStandardPaths, QUrl, Qt
from PySide6.QtGui import QActionGroup, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFileDialog, QFileSystemModel, QGridLayout, QGroupBox, QHeaderView, QLabel, QLineEdit,
                              QListView, QMenu, QSpinBox, QSplitter, QToolButton, QTreeView)

from .language import chinese


@dataclass
class ExportOptions:
    keep_constraints: bool = True
    temperature: float | None = None
    interval: int = 1
    coordinate_mode: str = 'direct'


class DirectoryField(QLineEdit):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.returnPressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ExportFiles(QIdentityProxyModel):
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if section == 1 and orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return "大小"
        return super().headerData(section, orientation, role)


class ExportDialog(QFileDialog):
    def __init__(self, parent, title, target, filters, *, trajectory=False, frames=1,
                 preserved_mode=None, on_export=None, image_options=None):
        chinese()
        super().__init__(parent, title, str(Path(target).parent))
        self.setOption(QFileDialog.Option.DontUseNativeDialog)
        self.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        self.setFileMode(QFileDialog.FileMode.AnyFile)
        self.setViewMode(QFileDialog.ViewMode.Detail)
        self.setProxyModel(ExportFiles(self))
        self.exporting = False
        self.resize(820, 520)
        desktop = QUrl.fromLocalFile(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation))
        self.setSidebarUrls([desktop, *(url for url in self.sidebarUrls() if url != desktop)])
        for label, text in ((QFileDialog.DialogLabel.FileName, "文件名"),
                            (QFileDialog.DialogLabel.FileType, "文件类型"),
                            (QFileDialog.DialogLabel.Accept, "导出"),
                            (QFileDialog.DialogLabel.Reject, "取消")):
            self.setLabelText(label, text)
        self.filters, self.trajectory = filters, trajectory
        self.preserved_mode = preserved_mode
        self.on_export = on_export
        self.setNameFilters(list(filters))
        self.selectNameFilter(next(iter(filters)))

        layout = self.layout()
        location = self.findChild(QComboBox, 'lookInCombo')
        self.directory_box = DirectoryField()
        self.directory_box.setObjectName('export_directory')
        self.directory_box.setAccessibleName('导出位置')
        self.directory_box.setPlaceholderText('输入或粘贴文件夹路径')
        self.directory_box.setMinimumHeight(34)
        self.directory_box.setClearButtonEnabled(True)
        self.directory_box.returnPressed.connect(self.go_directory)
        address = layout.itemAtPosition(0, 1).layout()
        address.replaceWidget(location, self.directory_box)
        location.hide()
        self.findChild(QLabel, 'lookInLabel').hide()
        layout.removeItem(address)
        layout.addLayout(address, 0, 0, 1, 3)
        layout.setColumnStretch(1, 1)
        address.setStretchFactor(self.directory_box, 1)
        icons = Path(__file__).with_name('icons')
        for index, (name, label, icon) in enumerate((('backButton', '后退', 'back'),
                                                    ('forwardButton', '前进', 'forward'),
                                                    ('toParentButton', '上一级', 'up'))):
            button = self.findChild(QToolButton, name)
            address.removeWidget(button)
            address.insertWidget(index, button)
            button.setFixedSize(36, 34)
            button.setIconSize(QSize(22, 22))
            button.setIcon(QIcon(str(icons / f'nav_{icon}.svg')))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setToolTip(label)
            button.setAccessibleName(label)
        self.new_folder_button = self.findChild(QToolButton, 'newFolderButton')
        for name in ('newFolderButton', 'listModeButton', 'detailModeButton'):
            self.findChild(QToolButton, name).hide()
        splitter = self.findChild(QSplitter, 'splitter')
        splitter.setSizes([130, 650])
        details = self.findChild(QTreeView, 'treeView')
        details.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        header = details.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 90), (2, 110), (3, 145)):
            header.resizeSection(column, width)
        self.file_views = (details, self.findChild(QListView, 'listView'))
        self.setup_folder_menu()

        self.filename = self.findChild(QLineEdit, 'fileNameEdit')
        self.filename.setText(Path(target).name)
        layout.addWidget(self.filename, 2, 1, 1, 2)
        layout.addWidget(self.findChild(QComboBox, 'fileTypeCombo'), 3, 1, 1, 2)
        self.image_options = image_options
        options = image_options if image_options is not None else self.structure_options(trajectory, frames, preserved_mode)
        layout.addWidget(options, 4, 0, 1, 3)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #a52c2c;')
        self.error.hide()
        layout.addWidget(self.error, 5, 0, 1, 3)
        self.progress_text = QLabel("正在导出图片…", self)
        self.progress_text.hide()
        layout.addWidget(self.progress_text, 5, 0, 1, 3)
        self.show_directory(self.directory().absolutePath())
        self.findChild(QFileSystemModel).rootPathChanged.connect(self.show_directory)
        buttons = self.findChild(QDialogButtonBox, 'buttonBox')
        buttons.setOrientation(Qt.Orientation.Horizontal)
        layout.addWidget(buttons, 6, 0, 1, 3)
        for button in buttons.buttons():
            button.setMinimumSize(90, 34)
        self.directory_box.textChanged.connect(self.error.hide)
        self.filename.textChanged.connect(self.error.hide)
        if image_options is None:
            self.thermal.toggled.connect(self.error.hide)
            self.temperature.valueChanged.connect(self.error.hide)
            self.keep.toggled.connect(self.error.hide)
        self.filterSelected.connect(self.format_changed)
        self.format_changed(self.selectedNameFilter())

    def structure_options(self, trajectory, frames, preserved_mode):
        options = QGroupBox('导出设置', self)
        grid = QGridLayout(options)
        grid.setColumnStretch(2, 1)
        self.keep = QCheckBox('保持约束')
        self.keep.setChecked(True)
        self.thermal = QCheckBox('重新赋予温度')
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 1e7)
        self.temperature.setDecimals(2)
        self.temperature.setSuffix(' K')
        self.temperature.setValue(300)
        self.stride = QCheckBox('间隔帧保存')
        self.stride.setEnabled(trajectory and frames > 1)
        self.interval = QSpinBox()
        self.interval.setRange(1, 1000000000)
        self.interval.setValue(1)
        self.interval.setEnabled(False)
        self.stride.toggled.connect(self.interval.setEnabled)
        self.thermal.toggled.connect(self.temperature.setEnabled)
        grid.addWidget(self.keep, 1, 0)
        grid.addWidget(self.thermal, 1, 1)
        grid.addWidget(self.temperature, 1, 2, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self.stride, 2, 0)
        grid.addWidget(self.interval, 2, 1)
        if trajectory:
            grid.addWidget(QLabel(f'共 {frames} 帧'), 2, 2)
        self.coordinates = QComboBox()
        self.coordinates.setObjectName('export_coordinates')
        self.coordinates.addItem('分数坐标（Direct）', 'direct')
        self.coordinates.addItem('笛卡尔坐标（Cartesian，Å）', 'cartesian')
        if preserved_mode is not None:
            self.coordinates.setCurrentIndex(self.coordinates.findData(preserved_mode))
        self.coordinates.setToolTip('文件含续算数据，保留原始坐标格式。' if preserved_mode is not None else
                                    'VASP 单帧导出按分数坐标 [0,1) 归入晶胞；斜晶胞的笛卡尔分量仍可能为负。')
        grid.addWidget(QLabel('VASP 坐标'), 0, 0)
        grid.addWidget(self.coordinates, 0, 1, 1, 2, Qt.AlignmentFlag.AlignLeft)
        return options

    def setup_folder_menu(self):
        self.folder_menu = QMenu(self)
        self.folder_menu.setStyleSheet("""
            QMenu { background: palette(window); border: 1px solid palette(mid);
                    border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 6px 25px 6px 27px; border-radius: 4px; }
            QMenu::item:selected { background: palette(midlight); }
        """)
        view_menu = self.folder_menu.addMenu('查看')
        view_menu.setIcon(self.findChild(QToolButton, 'detailModeButton').icon())
        view_modes = QActionGroup(view_menu)
        self.view_actions = {}
        for mode, label in ((QFileDialog.ViewMode.Detail, '详细信息'), (QFileDialog.ViewMode.List, '列表')):
            action = view_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, mode=mode: self.setViewMode(mode))
            view_modes.addAction(action)
            self.view_actions[mode] = action
        new_menu = self.folder_menu.addMenu('新建')
        new_menu.setIcon(self.new_folder_button.icon())
        self.new_folder_action = new_menu.addAction('文件夹', self.new_folder_button.click)
        for view in self.file_views:
            view.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ContextMenu:
            for view in self.file_views:
                if watched == view.viewport() and not view.indexAt(event.pos()).isValid():
                    self.view_actions[self.viewMode()].setChecked(True)
                    self.new_folder_action.setEnabled(self.new_folder_button.isEnabled())
                    self.folder_menu.popup(event.globalPos())
                    return True
        return super().eventFilter(watched, event)

    @property
    def kind(self):
        return self.filters[self.selectedNameFilter()]

    def format_changed(self, selected):
        kind = self.filters[selected]
        if self.image_options is not None:
            self.setDefaultSuffix(kind)
            return
        vasp = not self.trajectory and kind == 'vasp'
        editable = self.preserved_mode is None
        self.keep.setEnabled(vasp and editable)
        self.thermal.setEnabled((vasp and editable) or kind == 'lammps-data')
        self.temperature.setEnabled(self.thermal.isEnabled() and self.thermal.isChecked())
        self.coordinates.setEnabled(vasp and editable)
        self.setDefaultSuffix({'xyz': 'xyz', 'cif': 'cif', 'lammps-data': 'data', 'xdatcar': 'xdatcar'}.get(kind, ''))
        name = self.filename.text()
        if name:
            path = Path(name)
            suffix = {'vasp': '.vasp', 'xyz': '.xyz', 'cif': '.cif', 'lammps-data': '.data', 'xdatcar': '.xdatcar'}[kind]
            if path.suffix.lower() in ('.xyz', '.cif', '.data', '.lmp', '.lammps', '.vasp', '.xdatcar') and path.suffix.lower() != suffix:
                self.filename.setText(str(path.with_suffix(suffix)))
        self.error.hide()

    def export_options(self):
        return ExportOptions(not self.keep.isEnabled() or self.keep.isChecked(),
                             self.temperature.value() if self.thermal.isEnabled() and self.thermal.isChecked() else None,
                             self.interval.value() if self.stride.isEnabled() and self.stride.isChecked() else 1,
                             self.coordinates.currentData())

    def go_directory(self):
        text = self.directory_box.text().strip().strip('"')
        path = Path(text).expanduser()
        if not text or not path.is_dir():
            self.show_error('目录不存在，请检查地址。')
            return False
        name = self.filename.text()
        self.setDirectory(str(path))
        self.filename.setText(name)
        self.show_directory(self.directory().absolutePath())
        return True

    def show_directory(self, path):
        native = QDir.toNativeSeparators(path)
        self.directory_box.setText(native)
        self.directory_box.setToolTip(native)
        self.directory_box.setModified(False)
        self.error.hide()

    def show_error(self, message):
        self.error.setText(message)
        self.error.show()

    def accept(self):
        if self.directory_box.isModified() and not self.go_directory():
            return
        self.format_changed(self.selectedNameFilter())
        super().accept()

    def done(self, result):
        if self.exporting:
            return
        if result == QDialog.DialogCode.Accepted and self.on_export is not None:
            image = self.image_options is not None
            try:
                if image:
                    self.exporting = True
                    self.error.hide()
                    self.setEnabled(False)
                    self.setCursor(Qt.CursorShape.WaitCursor)
                    self.progress_text.show()
                    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
                options = self.image_options if image else self.export_options()
                self.on_export(Path(self.selectedFiles()[0]), self.kind, options)
            except (ValueError, OSError) as error:
                self.show_error(str(error))
                return
            finally:
                if image:
                    self.exporting = False
                    self.progress_text.hide()
                    self.unsetCursor()
                    self.setEnabled(True)
        super().done(result)
