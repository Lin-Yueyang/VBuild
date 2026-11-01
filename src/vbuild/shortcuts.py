"""Editable Qt shortcuts, shared by normal views and comparison cells."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHeaderView, QKeySequenceEdit,
                              QLabel, QPushButton, QTableWidget, QVBoxLayout, QWidget)

from .keys import bindings


def duplicates(values):
    seen, errors = {}, []
    for name, text in values.items():
        key = QKeySequence(text).toString(QKeySequence.SequenceFormat.PortableText)
        if not key:
            continue
        if key in seen:
            errors.append((seen[key], name, key))
        seen[key] = name
    return errors


class ShortcutEditor(QWidget):
    valid_changed = Signal(bool)

    def __init__(self, actions, current, parent=None):
        super().__init__(parent)
        self.actions, self.fields = actions, {}
        layout = QVBoxLayout(self)
        defaults = bindings()
        rows = [name for name in actions if name in defaults and name != 'molecule_modifier']
        self.table = QTableWidget(len(rows), 2)
        self.table.setHorizontalHeaderLabels(['操作', '快捷键'])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        from PySide6.QtWidgets import QTableWidgetItem
        for row, name in enumerate(rows):
            item = QTableWidgetItem(actions[name].text().replace('\n', ''))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, item)
            field = QKeySequenceEdit(QKeySequence(current[name]))
            field.setMaximumSequenceLength(1)
            field.setClearButtonEnabled(True)
            field.setObjectName('shortcut_' + name)
            self.table.setCellWidget(row, 1, field)
            self.fields[name] = field
            field.keySequenceChanged.connect(self.validate)
        layout.addWidget(self.table)
        form = QFormLayout()
        self.molecule = QComboBox()
        self.molecule.addItems(['Shift', 'Alt'])
        self.molecule.setCurrentText(current['molecule_modifier'])
        form.addRow('整分子选择 + 左键', self.molecule)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        reset = QPushButton('恢复默认快捷键')
        reset.clicked.connect(self.reset)
        layout.addWidget(reset)

    def values(self):
        return {name: field.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                for name, field in self.fields.items()}

    def validate(self, *_args):
        errors = duplicates(self.values())
        self.error.setText('\n'.join(f'{self.actions[a].text()} 与 {self.actions[b].text()} 重复：'
                                    + QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)
                                    for a, b, key in errors))
        self.valid_changed.emit(not errors)
        return not errors

    def reset(self):
        defaults = bindings()
        for name, field in self.fields.items():
            field.setKeySequence(QKeySequence(defaults[name]))
        self.molecule.setCurrentText(defaults['molecule_modifier'])
