"""History popup with at most ten visible records and a real scrollbar."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMenu, QWidgetAction


class HistoryMenu(QMenu):
    def populate(self, entries, restore):
        self.clear()
        if len(entries) <= 10:
            for text, target in entries:
                action = self.addAction(text)
                action.setData(target)
                action.triggered.connect(lambda _checked=False, step=target: restore(step))
            return
        self.records = QListWidget()
        self.records.setObjectName("history_records")
        self.records.setFrameShape(QListWidget.Shape.NoFrame)
        self.records.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.records.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerItem)
        height = self.fontMetrics().height() + 12
        width = max(self.fontMetrics().horizontalAdvance(text) for text, _target in entries) + 44
        self.records.setFixedSize(min(640, max(260, width)), height * 10)
        for text, target in entries:
            item = QListWidgetItem(text, self.records)
            item.setData(Qt.ItemDataRole.UserRole, target)
            item.setSizeHint(QSize(0, height))
        container = QWidgetAction(self)
        container.setDefaultWidget(self.records)
        self.addAction(container)
        self.records.itemClicked.connect(lambda item: self.choose(item, restore))
        self.records.itemActivated.connect(lambda item: self.choose(item, restore))

    def choose(self, item, restore):
        target = item.data(Qt.ItemDataRole.UserRole)
        self.close()
        restore(target)
