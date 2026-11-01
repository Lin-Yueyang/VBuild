"""Explicit Chinese choices for closing a structure or the whole application."""

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout


class CloseDialog(QDialog):
    def __init__(self, name: str, *, all_windows: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存修改")
        self.choice = "cancel"
        layout = QVBoxLayout(self)
        label = QLabel(f"“{name}”有未保存的修改。是否保存？")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.apply_all = QCheckBox("应用于所有窗口")
        self.apply_all.setObjectName("apply_all")
        self.apply_all.setVisible(all_windows)
        layout.addWidget(self.apply_all)
        buttons = QDialogButtonBox()
        for name, label, role in (
                ("save", "保存", QDialogButtonBox.ButtonRole.AcceptRole),
                ("discard", "不保存", QDialogButtonBox.ButtonRole.DestructiveRole),
                ("cancel", "取消", QDialogButtonBox.ButtonRole.RejectRole)):
            button = buttons.addButton(label, role)
            button.setObjectName(name)
            button.clicked.connect(lambda _checked, choice=name: self._choose(choice))
        layout.addWidget(buttons)

    def _choose(self, choice: str):
        self.choice = choice
        self.accept()
