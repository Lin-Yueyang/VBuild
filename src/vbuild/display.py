"""Small optional viewport panels and batched global atom labels."""

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QSpinBox, QToolButton, QVBoxLayout

from .label_view import AtomLabels
from .overlay import Overlay


class DisplayPanel(Overlay):
    periods_changed = Signal(object)
    hidden_changed = Signal(object)
    font_changed = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("display_panel")
        self.setStyleSheet("#display_panel { background: #ffffff; border: 1px solid #bbc3cc; border-radius: 4px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.grip.setText("显示设置 · 拖动移动")
        layout.addWidget(self.grip)
        self.periods = QGroupBox("周期显示")
        grid = QGridLayout(self.periods)
        self.counts = []
        for row, axis in enumerate("abc"):
            grid.addWidget(QLabel(axis), row, 0)
            spin = QSpinBox()
            spin.setObjectName("period_" + axis)
            spin.setRange(1, 10)
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.valueChanged.connect(lambda _number: self.periods_changed.emit(tuple(box.value() for box in self.counts)))
            minus, plus = QToolButton(), QToolButton()
            minus.setText("−")
            plus.setText("+")
            minus.setObjectName("minus_" + axis)
            plus.setObjectName("plus_" + axis)
            minus.clicked.connect(spin.stepDown)
            plus.clicked.connect(spin.stepUp)
            grid.addWidget(minus, row, 1)
            grid.addWidget(spin, row, 2)
            grid.addWidget(plus, row, 3)
            self.counts.append(spin)
        grid.addWidget(QLabel("仅显示镜像，不改变结构"), 3, 0, 1, 4)
        layout.addWidget(self.periods)
        self.labels = QGroupBox("原子序号")
        label_group = QVBoxLayout(self.labels)
        row = QHBoxLayout()
        row.addWidget(QLabel("字号"))
        self.font_size = QSpinBox()
        self.font_size.setObjectName("index_font")
        self.font_size.setRange(8, 48)
        self.font_size.setSuffix(" px")
        self.font_size.setValue(20)
        self.font_size.valueChanged.connect(self.font_changed)
        row.addWidget(self.font_size)
        label_group.addLayout(row)
        self.label_layout = QVBoxLayout()
        label_group.addLayout(self.label_layout)
        layout.addWidget(self.labels)
        self.element_boxes = {}
        self.hidden = set()
        self.hide()

    def populate(self, symbols, counts, hidden, periodic, labels):
        for spin, count in zip(self.counts, counts):
            with QSignalBlocker(spin):
                spin.setValue(count)
        self.hidden = set(hidden)
        elements = tuple(dict.fromkeys(symbols))
        if elements != tuple(self.element_boxes):
            while self.label_layout.count():
                self.label_layout.takeAt(0).widget().deleteLater()
            self.element_boxes = {}
            for symbol in elements:
                box = QCheckBox(symbol)
                box.setObjectName("index_" + symbol)
                box.toggled.connect(lambda checked, element=symbol: self._filter(element, checked))
                self.element_boxes[symbol] = box
                self.label_layout.addWidget(box)
        for symbol, box in self.element_boxes.items():
            with QSignalBlocker(box):
                box.setChecked(symbol not in hidden)
        self.show_sections(periodic, labels)

    def _filter(self, symbol, checked):
        if checked:
            self.hidden.discard(symbol)
        else:
            self.hidden.add(symbol)
        self.hidden_changed.emit(self.hidden.copy())

    def show_sections(self, periodic, labels):
        self.periods.setVisible(periodic)
        self.labels.setVisible(labels)
        self.setVisible(periodic or labels)
        self.adjustSize()
