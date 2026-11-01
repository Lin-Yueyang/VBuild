"""Element checkboxes in a compact popup, independent of other label layers."""

from PySide6.QtCore import QPoint, QSignalBlocker, Signal, Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QPushButton


class ElementFilter(QPushButton):
    changed = Signal()

    def __init__(self, parent):
        super().__init__("显示元素…", parent)
        self.hidden, self.boxes = set(), {}
        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.setStyleSheet("QFrame { background: white; }")
        self.grid = QGridLayout(self.popup)
        self.clicked.connect(self.open)

    def populate(self, symbols, hidden):
        self.hidden = set(hidden)
        elements = tuple(dict.fromkeys(symbols))
        if elements != tuple(self.boxes):
            while self.grid.count():
                self.grid.takeAt(0).widget().deleteLater()
            self.boxes = {}
            for index, symbol in enumerate(elements):
                box = QCheckBox(symbol)
                box.setObjectName("element_" + symbol)
                box.toggled.connect(lambda checked, element=symbol: self.filter(element, checked))
                self.boxes[symbol] = box
                self.grid.addWidget(box, index // 4, index % 4)
        for symbol, box in self.boxes.items():
            with QSignalBlocker(box):
                box.setChecked(symbol not in hidden)
        self.setText(f"显示元素… ({sum(symbol not in hidden for symbol in elements)}/{len(elements)})")

    def filter(self, symbol, checked):
        if checked:
            self.hidden.discard(symbol)
        else:
            self.hidden.add(symbol)
        self.changed.emit()

    def open(self):
        self.popup.adjustSize()
        self.popup.move(self.mapToGlobal(QPoint(0, self.height())))
        self.popup.show()
