"""Application-wide atom snapshots and an on-demand 3D clipboard browser."""

from collections import Counter

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout

from .model import Structure
from .scene import Scene


class Clipboard(QObject):
    changed = Signal()

    def __init__(self, limit=5, parent=None):
        super().__init__(parent)
        self.limit, self.entries = limit, []

    def copy(self, symbols, positions):
        points = positions.copy()
        points.setflags(write=False)
        self.entries.insert(0, (tuple(symbols), points))
        self.entries = self.entries[:self.limit]
        self.changed.emit()

    def set_limit(self, limit):
        self.limit = limit
        self.entries = self.entries[:limit]
        self.changed.emit()


def shared_clipboard(limit):
    app = QApplication.instance()
    if not hasattr(app, "atom_clipboard"):
        app.atom_clipboard = Clipboard(limit, app)
    return app.atom_clipboard


class ClipboardPanel(QDialog):
    chosen = Signal(int)

    def __init__(self, store, parent):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("原子剪贴板")
        self.resize(520, 540)
        layout = QVBoxLayout(self)
        self.info = QLabel()
        layout.addWidget(self.info)
        self.items = QListWidget()
        self.items.setMaximumHeight(170)
        self.items.currentRowChanged.connect(self.preview)
        self.items.itemDoubleClicked.connect(lambda _item: self.place())
        layout.addWidget(self.items)
        self.scene = Scene(self)
        self.scene.setMinimumHeight(280)
        self.scene.previewing = True
        self.scene.set_bond_ranges(parent.bond_ranges)
        self.scene.set_bond_scale(parent.bond_scale)
        self.scene.set_bond_radius(parent.settings["bond_radius"])
        layout.addWidget(self.scene, 1)
        buttons = QHBoxLayout()
        self.paste = QPushButton("粘贴")
        self.paste.clicked.connect(self.place)
        close = QPushButton("关闭")
        close.clicked.connect(self.hide)
        buttons.addStretch()
        buttons.addWidget(self.paste)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        store.changed.connect(self.populate)
        self.populate()

    def populate(self):
        self.info.setText(f"最近 {self.store.limit} 次复制；选择一项查看，双击或点击粘贴。")
        self.items.clear()
        for index, (symbols, _positions) in enumerate(self.store.entries):
            counts = Counter(symbols)
            formula = "".join(symbol + (str(count) if count > 1 else "") for symbol, count in counts.items())
            self.items.addItem(f"{index + 1}  ·  {formula}  ·  {len(symbols)} 原子")
        self.paste.setEnabled(bool(self.store.entries) and self.parent().actions["quick_add"].isEnabled())
        self.items.setCurrentRow(0 if self.store.entries else -1)

    def preview(self, index):
        if index < 0:
            self.scene.clear_structure()
            return
        symbols, positions = self.store.entries[index]
        host = self.parent().scene
        self.scene.set_look(host.visual.kind, host.visual.profile, True, rebuild=False)
        self.scene.background = host.background
        structure = Structure(symbols, None, positions.copy(), None, None, symbols, (None,) * len(symbols))
        self.scene.set_structure(structure, reset=True)

    def place(self):
        index = self.items.currentRow()
        if index >= 0 and self.paste.isEnabled():
            self.hide()
            self.chosen.emit(index)

    def dispose(self):
        self.store.changed.disconnect(self.populate)
        self.scene.plotter.close()
