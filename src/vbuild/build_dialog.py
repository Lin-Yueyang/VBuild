"""Preview before applying a supercell or a Miller slab to the current document."""

from collections import Counter

import numpy as np
from ase.cell import Cell
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFormLayout, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                              QSpinBox, QWidget)

from .build import slab, supercell
from .standard import conventional_atoms


def preview_info(original, structure):
    composition = ' '.join(f'{symbol}{count}' for symbol, count in Counter(structure.symbols).items())
    rows = []
    for axis, vector, name, angle in zip('abc', structure.cell, 'αβγ', Cell(structure.cell).angles()):
        values = ''.join(f'<td align="right" style="padding-left:8px">{value:.3f}</td>' for value in vector)
        rows.append(f'<tr><td>{axis}:</td>{values}<td style="padding-left:18px">{name}: {angle:.3f}°</td></tr>')
    return (f'{len(original.symbols)} → {len(structure.symbols)} 个原子<br>{composition}<br>'
            '<table cellspacing="0" cellpadding="0"><tr><td colspan="4">晶胞向量（Å）</td>'
            '<td style="padding-left:18px">晶胞角度</td></tr>' + ''.join(rows) + '</table>')


def integer_row(names, values, lower, upper):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    boxes = []
    for name, value in zip(names, values):
        box = QSpinBox()
        box.setObjectName(name)
        box.setRange(lower, upper)
        box.setValue(value)
        layout.addWidget(box)
        boxes.append(box)
    return widget, boxes


class BuildDialog(QDialog):
    preview_requested = Signal(object)

    def __init__(self, kind, structure, parent):
        super().__init__(parent)
        self.kind, self.original, self.prepared = kind, structure, None
        self.setWindowTitle("切面扩胞")
        self.setMinimumWidth(380)
        self.form = QFormLayout(self)
        self.only = QCheckBox("仅扩胞")
        self.only.setObjectName("only_supercell")
        self.only.setChecked(kind == "supercell")
        self.form.addRow(self.only)
        self.advanced = QCheckBox("高级参数")
        self.advanced.setObjectName("slab_advanced")
        self.form.addRow(self.advanced)
        self.conventional = QCheckBox("常规晶胞基准（标准 Miller 指数）")
        self.conventional.setObjectName("slab_conventional")
        self.conventional.setToolTip("先转换到常规晶胞，再解释 Miller 指数，晶轴可能交换；取消勾选则按当前文件的 a/b/c 解释。保留实际晶格，不理想化原子位置。")
        self.conventional.setChecked(True)
        self.form.addRow(self.conventional)
        self.standard_count = None
        self.conventional.toggled.connect(self.changed)
        self.advanced.toggled.connect(lambda enabled: self.form.setRowVisible(self.conventional, enabled and not self.only.isChecked()))
        self.indices_row, self.indices = integer_row(("miller_h", "miller_k", "miller_l"), (1, 1, 1), -20, 20)
        self.form.addRow("Miller h / k / l", self.indices_row)
        self.repeat_row, self.repeats = integer_row(("repeat_a", "repeat_b", "repeat_c"), (1, 1, 1), 1, 100)
        self.counts = self.repeats
        self.form.addRow("x y z 重复", self.repeat_row)
        self.vacuum = QDoubleSpinBox()
        self.vacuum.setObjectName("slab_vacuum")
        self.vacuum.setRange(0, 1000)
        self.vacuum.setDecimals(2)
        self.vacuum.setValue(15)
        self.vacuum.setSuffix(" Å")
        self.form.addRow("真空层厚度", self.vacuum)
        self.extra_vacuum = QDoubleSpinBox()
        self.extra_vacuum.setObjectName("supercell_vacuum")
        self.extra_vacuum.setRange(0, 1000)
        self.extra_vacuum.setDecimals(2)
        self.extra_vacuum.setSingleStep(1)
        self.extra_vacuum.setSuffix(" Å")
        self.form.addRow("z 向增加真空", self.extra_vacuum)
        self.bottom = QDoubleSpinBox()
        self.bottom.setObjectName("slab_bottom")
        self.bottom.setRange(0, 1000)
        self.bottom.setDecimals(2)
        self.bottom.setSingleStep(0.1)
        self.bottom.setValue(0.5)
        self.bottom.setSuffix(" Å")
        self.form.addRow("底部间距", self.bottom)
        self.vacuum.valueChanged.connect(lambda value: self.bottom.setEnabled(value > 0))
        self.matrix_mode = QCheckBox("使用整数变换矩阵（新晶胞 = 矩阵 × 原晶胞）")
        self.matrix_mode.setObjectName("matrix_mode")
        self.form.addRow(self.matrix_mode)
        self.matrix_widget = QWidget()
        grid = QGridLayout(self.matrix_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        self.matrix = []
        for index in range(3):
            row = []
            for column in range(3):
                box = QSpinBox()
                box.setRange(-20, 20)
                box.setValue(int(index == column))
                box.setObjectName(f"matrix_{index}_{column}")
                grid.addWidget(box, index, column)
                row.append(box)
            self.matrix.append(row)
        self.form.addRow(self.matrix_widget)
        self.matrix_mode.toggled.connect(self.changed)
        self.only.toggled.connect(self.toggle_only)
        inputs = self.indices + self.repeats + [self.vacuum, self.extra_vacuum, self.bottom] + [box for row in self.matrix for box in row]
        self.preview = QPushButton("预览")
        self.preview.setObjectName("build_preview")
        self.preview.clicked.connect(self.prepare)
        self.form.addRow(self.preview)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setToolTip('α 为 b、c 的夹角；β 为 a、c 的夹角；γ 为 a、b 的夹角。')
        self.form.addRow(self.info)
        self.fix_unmapped = QCheckBox("将无法等价表示的部分方向约束改为全固定")
        self.fix_unmapped.setObjectName("fix_unmapped")
        self.fix_unmapped.toggled.connect(self.update_accept)
        self.form.addRow(self.fix_unmapped)
        self.fix_unmapped.hide()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer = QWidget()
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hint = QLabel("请先预览")
        layout.addStretch()
        layout.addWidget(self.hint)
        layout.addWidget(self.buttons)
        self.form.addRow(footer)
        for box in inputs:
            box.valueChanged.connect(self.changed)
        self.toggle_only(self.only.isChecked())

    def toggle_only(self, enabled):
        self.kind = "supercell" if enabled else "slab"
        for widget in (self.advanced, self.indices_row, self.vacuum, self.bottom):
            self.form.setRowVisible(widget, not enabled)
        self.form.setRowVisible(self.conventional, not enabled and self.advanced.isChecked())
        self.form.setRowVisible(self.matrix_mode, enabled)
        self.form.setRowVisible(self.extra_vacuum, enabled)
        self.form.labelForField(self.repeat_row).setText("a b c 重复" if enabled else "x y z 重复")
        self.changed()

    def parameters(self):
        if self.kind == "supercell":
            return np.array([[box.value() for box in row] for row in self.matrix]) if self.matrix_mode.isChecked() else np.diag([box.value() for box in self.counts])
        return [box.value() for box in self.indices]

    def changed(self, *_args):
        matrix = self.kind == "supercell" and self.matrix_mode.isChecked()
        self.form.setRowVisible(self.matrix_widget, matrix)
        row_widget_enabled(self.counts, not matrix)
        self.prepared = None
        self.fix_unmapped.hide()
        self.fix_unmapped.setChecked(False)
        parameters = self.parameters()
        multiplier = abs(round(np.linalg.det(parameters))) if self.kind == "supercell" else int(np.prod([box.value() for box in self.repeats]))
        valid = multiplier > 0 if self.kind == "supercell" else any(parameters)
        if self.kind == "slab":
            valid = valid and (self.vacuum.value() == 0 or self.bottom.value() <= self.vacuum.value())
        pending = self.original.temperature_pending
        self.preview.setEnabled(valid and not pending)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        count = len(self.original.symbols)
        if self.kind == "slab" and self.conventional.isChecked():
            if self.standard_count is None:
                try:
                    self.standard_count = len(conventional_atoms(self.original))
                except (ImportError, ValueError) as error:
                    self.info.setText(str(error))
                    self.preview.setEnabled(False)
                    return
            count = self.standard_count
        text = f"预计原子数：{len(self.original.symbols)} → {count * multiplier}"
        if self.kind == "slab" and not self.conventional.isChecked():
            text += "\n指数基准：当前晶胞"
        if not valid:
            text = "矩阵行列式不能为零。" if self.kind == "supercell" else "Miller 指数不能全部为零。"
        if self.kind == "slab" and self.vacuum.value() > 0 and self.bottom.value() > self.vacuum.value():
            text = "底部间距不能超过真空层厚度。"
        if pending:
            text = "请先在温度窗口提供 POTIM 或指定温度，完成原 Direct 速度的换算。"
        self.info.setText(text)
        self.adjustSize()

    def prepare(self):
        try:
            self.prepared = (supercell(self.original, self.parameters(), self.extra_vacuum.value()) if self.kind == "supercell" else
                           slab(self.original, self.parameters(), [box.value() for box in self.repeats], self.vacuum.value(), self.bottom.value(), conventional=self.conventional.isChecked()))
        except (ImportError, ValueError) as error:
            self.info.setText(str(error))
            return
        structure = self.prepared.structure
        text = preview_info(self.original, structure)
        if self.prepared.unresolved:
            text += f"<br>{self.prepared.unresolved} 个原子的部分约束无法在新 a/b/c 下等价表示。<br>勾选全固定后才能应用，或取消并先调整原约束。"
        if self.no_thermal_dof():
            text += "<br>全部原子固定时不能维持正温度，请取消并先调整温度或约束。"
        self.info.setText(text)
        self.fix_unmapped.setVisible(self.prepared.unresolved > 0)
        self.update_accept()
        self.adjustSize()
        self.preview_requested.emit(self.prepared)

    def no_thermal_dof(self):
        structure = self.prepared.structure
        return structure.temperature is not None and structure.temperature > 0 and structure.free is not None and not structure.free.any()

    def update_accept(self, *_args):
        ready = self.prepared is not None and (not self.prepared.unresolved or self.fix_unmapped.isChecked()) and not self.no_thermal_dof()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ready)


def row_widget_enabled(boxes, enabled):
    for box in boxes:
        box.setEnabled(enabled)
