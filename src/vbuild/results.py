"""Incremental step table, CSV data and hoverable Qt-painted time curves."""

import csv
from io import StringIO
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHeaderView, QLayout, QMenu, QTabWidget,
                              QTableView, QVBoxLayout)

from .overlay import Overlay
from .curves import Curves, CurvePanel

COLUMNS = (("number", "步数"), ("time_fs", "时间 (fs)"), ("temperature", "温度 (K)"),
           ("energy", "势能 (eV)"), ("per_atom", "每原子势能 (eV)"),
           ("kinetic", "动能 (eV)"), ("total", "总能 (eV)"),
           ("force", "原子最大受力 (eV/Å)"), ("raw_force", "原始最大力 (eV/Å)"),
           ("elapsed", "用时 (秒)"), ("speed", "平均步/秒"))
OPT_OMITTED = {"time_fs", "temperature", "kinetic", "total", "raw_force"}


def values(step):
    total = None if step.kinetic is None or step.energy is None else step.kinetic + step.energy
    per_atom = None if step.energy is None else step.energy / len(step.positions)
    return [step.number, step.time_fs, step.temperature, step.energy, per_atom, step.kinetic, total,
            step.force, step.raw_force, step.elapsed, step.number / step.elapsed if step.elapsed else None]


def csv_data(state):
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([key for key, _label in COLUMNS])
    writer.writerows(values(step) for step in state.frames)
    return stream.getvalue().encode("utf-8-sig")


class StepTable(QAbstractTableModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.frames = None
        self.count = 0

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.count

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][1] if orientation == Qt.Orientation.Horizontal else str(section + 1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            value = values(self.frames[index.row()])[index.column()]
            return "—" if value is None else str(value) if index.column() == 0 else f"{value:.5f}"

    def populate(self, state):
        if state.frames is not self.frames:
            self.beginResetModel()
            self.frames, self.count = state.frames, len(state.frames)
            self.endResetModel()
        elif len(self.frames) > self.count:
            self.beginInsertRows(QModelIndex(), self.count, len(self.frames) - 1)
            self.count = len(self.frames)
            self.endInsertRows()
        if self.count:
            self.dataChanged.emit(self.index(0, 0), self.index(0, len(COLUMNS) - 1))

    def clear(self):
        if self.frames is not None:
            self.beginResetModel()
            self.frames, self.count = None, 0
            self.endResetModel()


class DataPanel(QFrame):
    frame_requested = Signal(int)
    header_changed = Signal(str, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("data_panel")
        self.setStyleSheet("#data_panel { background: white; border: 1px solid #adb3ba; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        self.table = QTableView()
        self.model = StepTable(self)
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self.column_menu)
        self.table.verticalHeader().setSectionsMovable(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda index: self.frame_requested.emit(index.row()))
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(0, 55)
        for column in range(1, len(COLUMNS)):
            self.table.setColumnWidth(column, 110)
        self.kind, self.header_states = "md", {}
        self.default_header = self.table.horizontalHeader().saveState()
        self.header_timer = QTimer(self)
        self.header_timer.setSingleShot(True)
        self.header_timer.setInterval(250)
        header = self.table.horizontalHeader()
        header.sectionMoved.connect(lambda *_args: self.header_timer.start())
        header.sectionResized.connect(lambda *_args: self.header_timer.start())
        self.header_timer.timeout.connect(self.remember_header)
        layout.addWidget(self.table)
        self.setMinimumWidth(190)
        self.hide()

    def column_menu(self, position):
        header = self.table.horizontalHeader()
        menu = QMenu(header)
        for column, (key, label) in enumerate(COLUMNS):
            if self.kind == 'opt' and key in OPT_OMITTED:
                continue
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(column))
            action.toggled.connect(lambda checked, index=column: self.show_column(index, checked))
        menu.exec(header.mapToGlobal(position))

    def show_column(self, column, visible):
        self.table.setColumnHidden(column, not visible)
        self.header_timer.start()

    def remember_header(self):
        self.header_timer.stop()
        state = self.table.horizontalHeader().saveState()
        self.header_states[self.kind] = state
        self.header_changed.emit(self.kind, state)

    def set_kind(self, kind):
        if kind == self.kind:
            return
        self.remember_header()
        self.kind = kind
        header = self.table.horizontalHeader()
        with QSignalBlocker(header):
            header.restoreState(self.header_states.get(kind, self.default_header))
            if kind == 'opt':
                for column, (key, _label) in enumerate(COLUMNS):
                    if key in OPT_OMITTED:
                        self.table.setColumnHidden(column, True)


class AnalysisPanel(Overlay):
    close_requested = Signal(str)
    current_changed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent, resizable=True, vertical=True)
        self.setObjectName("analysis_panel")
        self.setStyleSheet("#analysis_panel { background: #f3f3f3; border: 1px solid #adb3ba; }")
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(4)
        self.grip.setText("数据与曲线 · 拖动移动 / 边缘调整大小")
        layout.addWidget(self.grip)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(lambda index: self.close_requested.emit(self.name_at(index)))
        self.tabs.currentChanged.connect(self.select_tab)
        layout.addWidget(self.tabs, 1)
        self.panes = {}
        self.titles = {}
        self.bottom = parent.height()
        self.setMinimumWidth(340)
        self.setMinimumHeight(200)
        self.resize(550, 460)
        self.hide()

    def add(self, name, title, content):
        self.panes[name], self.titles[name] = content, title
        content.hide()

    def name_at(self, index):
        return next(name for name, pane in self.panes.items() if pane is self.tabs.widget(index))

    def select_tab(self, index):
        if index >= 0:
            self.current_changed.emit(self.name_at(index))

    def set_panels(self, visible, active):
        # Removing a tab does not delete its page; the same widgets display the active Session.
        with QSignalBlocker(self.tabs):
            for name, pane in self.panes.items():
                index = self.tabs.indexOf(pane)
                if visible.get(name, False) and index < 0:
                    self.tabs.addTab(pane, self.titles[name])
                elif visible.get(name, False):
                    self.tabs.setTabText(index, self.titles[name])
                elif not visible.get(name, False) and index >= 0:
                    self.tabs.removeTab(index)
                    pane.hide()
            if visible.get(active, False):
                self.tabs.setCurrentWidget(self.panes[active])
        self.setVisible(any(visible.values()))

    def fit(self, bottom):
        self.bottom = bottom
        required = max(self.minimumHeight(), self.minimumSizeHint().height())
        self.resize(min(self.width(), self.parentWidget().width()-24), min(max(self.height(), required), bottom-20))
        if not self.manual:
            self.move(self.parentWidget().width()-self.width()-12, 12)
        self.keep_visible()

    def keep_visible(self):
        super().keep_visible()
        if self.isVisible():
            self.resize(min(self.width(), self.parentWidget().width()-24), min(self.height(), max(0, self.bottom-8)))
            self.move(max(0, min(self.x(), self.parentWidget().width() - self.width() - 8)),
                      max(0, min(self.y(), self.bottom-self.height()-8)))
