"""Non-modal cell search with explicit candidates and preview-before-apply."""

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from ase.cell import Cell
from PySide6.QtCore import QSignalBlocker, QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                              QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView,
                              QLabel, QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
                              QTableWidgetItem, QVBoxLayout, QWidget)

from .build_dialog import preview_info
from .recell import build, normal_c, primitive, search, unit_candidate
from .matching import Matches, instruction, joint_search, order_matches
from .model import Structure
from .cell_preview import CellPreview, title


@dataclass
class Reference:
    structure: Structure
    path: Path


class SearchTask(QThread):
    ready = Signal(object, object)
    failed = Signal(str)

    def __init__(self, structure, tolerance, options, perpendicular, parent, *, reference=None, max_atoms=1000):
        super().__init__(parent)
        self.structure, self.tolerance, self.options = structure, tolerance, options
        self.perpendicular = perpendicular
        self.reference, self.max_atoms = reference, max_atoms

    def run(self):
        try:
            reduced = primitive(self.structure, self.tolerance, surface=self.options['kind'] != 'primitive3d')
            if self.perpendicular:
                reduced = normal_c(reduced, self.structure.cell)
            if self.reference is None:
                candidates = search(reduced, **self.options, cancelled=self.isInterruptionRequested)
            else:
                reference = primitive(self.reference, self.tolerance)
                if self.perpendicular:
                    reference = normal_c(reference, self.reference.cell)
                candidates = joint_search(reduced, reference, **self.options, max_atoms=self.max_atoms,
                                          cancelled=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.ready.emit(reduced, candidates)
        except (ValueError, RuntimeError) as error:
            self.failed.emit(str(error))


def number(value, lower, upper, decimals, step):
    box = QDoubleSpinBox()
    box.setRange(lower, upper)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setValue(value)
    box.setFixedWidth(108)
    return box


def row(boxes):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    for box in boxes:
        layout.addWidget(box)
    layout.addStretch()
    return widget


class RecellDialog(QDialog):
    preview_requested = Signal(object)
    kind = 'recell'

    def __init__(self, structure, references, parent):
        super().__init__(parent)
        self.original, self.prepared, self.reduced = structure, None, None
        self.prepared_reference, self.matched = None, None
        self.candidates, self.worker, self.closing = [], None, False
        self.initialized = False
        self.setWindowTitle('改胞')
        available = self.screen().availableGeometry()
        self.resize(min(1380, int(available.width()*.95)), min(920, int(available.height()*.92)))
        layout = QVBoxLayout(self)
        self.sections = QSplitter(Qt.Orientation.Vertical)
        self.sections.setChildrenCollapsible(False)
        self.sections.setHandleWidth(7)
        layout.addWidget(self.sections, 1)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.sections.addWidget(self.splitter)
        self.previews = CellPreview(self)
        parameters = QWidget()
        form = QFormLayout(parameters)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.parameter_scroll = QScrollArea()
        self.parameter_scroll.setWidgetResizable(True)
        self.parameter_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.parameter_scroll.setWidget(parameters)
        self.parameter_scroll.setMinimumHeight(130)
        self.splitter.addWidget(self.parameter_scroll)
        self.splitter.addWidget(self.previews)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.results_widget = QWidget()
        results = QVBoxLayout(self.results_widget)
        results.setContentsMargins(0, 0, 0, 0)
        self.sections.addWidget(self.results_widget)
        self.mode = QComboBox()
        self.mode.setFixedWidth(174)
        for label, name in [('长方形', 'rectangle'), ('接近正方形', 'square'), ('自定义角度', 'angles'),
                            ('表面最小原胞', 'primitive'), ('三维最小原胞', 'primitive3d')]:
            self.mode.addItem(label, name)
        form.addRow('目标', self.mode)
        self.note = QLabel('调整 a/b 边界，保留 c、表面厚度和真空；不施加应变。')
        self.note.setWordWrap(True)
        form.addRow(self.note)
        self.angles = [number(90, 1, 179, 2, 1) for _ in range(3)]
        form.addRow('目标 α / β / γ', row(self.angles))
        self.angle_tolerance = number(1, .01, 15, 2, .1)
        self.angle_tolerance.setSuffix(' °')
        self.target_enabled = QCheckBox('匹配参考结构 / 指定边长')
        form.addRow(self.target_enabled)
        self.target_widget = QWidget()
        targets = QFormLayout(self.target_widget)
        targets.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        targets.setContentsMargins(0, 0, 0, 0)
        self.reference = QComboBox()
        self.reference.setFixedWidth(350)
        self.reference.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.reference.addItem('手动输入', None)
        for title, value, path in references:
            self.reference.addItem(title, Reference(deepcopy(value), path))
        targets.addRow('参考结构', self.reference)
        self.joint = QCheckBox('两侧共同扩胞')
        self.joint.setToolTip('同时搜索两侧周期边界，比较原子数量与边长偏差。')
        targets.addRow(self.joint)
        self.lengths = [number(value, .01, 10000, 3, .1) for value in Cell(structure.cell).lengths()[:2]]
        targets.addRow('a / b（Å）', row(self.lengths))
        self.max_atoms = QSpinBox()
        self.max_atoms.setRange(2, 100000)
        self.max_atoms.setSingleStep(100)
        self.max_atoms.setValue(1000)
        self.max_atoms.setFixedWidth(108)
        self.priority = QComboBox()
        self.priority.setFixedWidth(144)
        for title, key in [('折中方案', 'balanced'), ('原子数优先', 'atoms'), ('偏差优先', 'misfit')]:
            self.priority.addItem(title, key)
        self.joint_options = row([self.max_atoms, self.priority])
        targets.addRow('原子上限 / 排序', self.joint_options)
        form.addRow(self.target_widget)
        self.length_tolerance = number(5, .01, 50, 2, .5)
        self.length_tolerance.setSuffix(' %')
        form.addRow('角度 / 边长容差', row([self.angle_tolerance, self.length_tolerance]))
        self.advanced = QCheckBox('搜索范围')
        form.addRow(self.advanced)
        self.advanced_widget = QWidget()
        advanced = QFormLayout(self.advanced_widget)
        advanced.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.multiple, self.bound = QSpinBox(), QSpinBox()
        self.multiple.setRange(1, 256)
        self.multiple.setValue(36)
        self.multiple.setFixedWidth(108)
        self.multiple.setToolTip('相对识别出的面内原胞，限制候选面积倍数；共同扩胞时分别限制两侧。')
        self.bound.setRange(1, 24)
        self.bound.setValue(8)
        self.bound.setFixedWidth(108)
        self.bound.setToolTip('新边界 a′=m·a+n·b；限制每个整数 m、n 的绝对值。')
        self.tolerance = number(.001, .001, .1, 3, .001)
        self.tolerance.setToolTip('判断原子能否通过平移重合的坐标误差，单位 Å；不是界面边长容差。')
        advanced.addRow('最大面积倍数（相对原胞）', self.multiple)
        advanced.addRow('整数系数 ±', self.bound)
        advanced.addRow('平移识别容差（Å）', self.tolerance)
        form.addRow(self.advanced_widget)
        self.align = QCheckBox('对齐 a 到 x、表面法向到 z')
        self.align.setChecked(True)
        self.perpendicular = QCheckBox('c 垂直表面（有真空的 slab）')
        self.perpendicular.setToolTip('使 α、β 为 90°，γ 不变；保留原子位置和法向高度，改变真空两侧横向错位。共同扩胞时作用于两侧。')
        form.addRow(self.align)
        form.addRow(self.perpendicular)
        self.find = QPushButton('搜索')
        self.find.setObjectName('recell_search')
        self.find.clicked.connect(self.start_search)
        results.addWidget(self.find)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(['原子数', 'a（Å）', 'b（Å）', 'α（°）', 'β（°）', 'γ（°）', '面积倍数', '边长偏差'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(105)
        self.table.itemSelectionChanged.connect(self.selected)
        results.addWidget(self.table, 1)
        self.plan = QLabel()
        self.plan.setWordWrap(True)
        self.plan.hide()
        results.addWidget(self.plan)
        self.preview = QPushButton('预览')
        self.preview.setObjectName('recell_preview')
        self.preview.clicked.connect(self.prepare)
        results.addWidget(self.preview)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info_scroll = QScrollArea()
        self.info_scroll.setWidgetResizable(True)
        self.info_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.info_scroll.setWidget(self.info)
        self.info_scroll.setMinimumHeight(115)
        self.sections.addWidget(self.info_scroll)
        self.sections.setSizes([460, 230, 185])
        self.splitter.setSizes([505, 850])
        self.fix_unmapped = QCheckBox('将无法等价表示的部分方向约束改为全固定')
        self.fix_unmapped.toggled.connect(self.update_accept)
        layout.addWidget(self.fix_unmapped)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('应用')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.addWidget(QLabel('输出'))
        self.output = QComboBox()
        self.output.addItem('匹配后的结构', 'supercell')
        self.output.addItem('改胞单元（扩胞前）', 'unit')
        self.output.setToolTip('改胞单元沿候选 a/b 方向去掉整倍重复，保留层数和真空。')
        self.output.currentIndexChanged.connect(self.output_changed)
        footer.addWidget(self.output)
        footer.addWidget(QLabel('请先预览'))
        footer.addStretch()
        footer.addWidget(self.buttons)
        layout.addLayout(footer)
        self.inputs = [self.mode, self.target_enabled, self.reference, self.joint, self.max_atoms, self.advanced, self.align, self.perpendicular,
                       self.multiple, self.bound, self.tolerance, self.angle_tolerance, self.length_tolerance,
                       *self.angles, *self.lengths]
        for widget in self.inputs:
            signal = (widget.currentIndexChanged if isinstance(widget, QComboBox) else
                      widget.toggled if isinstance(widget, QCheckBox) else widget.valueChanged)
            signal.connect(self.selected if widget is self.align else self.advanced_widget.setVisible if widget is self.advanced else self.changed)
        self.reference.currentIndexChanged.connect(self.use_reference)
        self.priority.currentIndexChanged.connect(self.sort_matches)
        self.changed()
        if structure.temperature_pending:
            self.find.setEnabled(False)
            self.info.setText('请先完成原 Direct 速度的温度换算。')

    def changed(self, *_args):
        if self.worker is not None:
            return
        self.controls()
        self.reduced, self.candidates = None, []
        self.matched = None
        self.table.setRowCount(0)
        self.selected()
        self.info.setText('搜索并选择一个候选晶胞。')

    def controls(self):
        kind = self.mode.currentData()
        is_primitive = kind in ('primitive', 'primitive3d')
        self.note.setText('缩小三个周期方向，保留实际几何。' if kind == 'primitive3d' else
                          '调整 a/b 边界，保留 c、表面厚度和真空；不施加应变。')
        for box in self.angles:
            box.setEnabled(kind == 'angles')
            if kind in ('rectangle', 'square'):
                with QSignalBlocker(box):
                    box.setValue(90)
        self.angle_tolerance.setEnabled(not is_primitive)
        self.target_enabled.setEnabled(not is_primitive)
        self.perpendicular.setEnabled(kind != 'primitive3d')
        self.target_widget.setVisible(self.target_enabled.isChecked() and not is_primitive)
        joint = self.target_enabled.isChecked() and self.joint.isChecked() and self.reference.currentData() is not None and not is_primitive
        self.joint.setEnabled(self.reference.currentData() is not None)
        for box in self.lengths:
            box.setEnabled(not joint)
        targets = self.target_widget.layout()
        targets.setRowVisible(self.joint_options, joint)
        self.priority.setEnabled(self.worker is None)
        self.length_tolerance.setEnabled(kind == 'square' or self.target_enabled.isChecked() and not is_primitive)
        self.advanced_widget.setVisible(self.advanced.isChecked())
        self.multiple.setEnabled(not is_primitive)
        self.bound.setEnabled(not is_primitive)

    def use_reference(self):
        source = self.reference.currentData()
        if source is not None:
            for box, value in zip(self.lengths, Cell(source.structure.cell).lengths()):
                box.setValue(value)
            if self.initialized:
                self.previews.reference.display('参考', source.structure, reset=True)
        else:
            self.previews.reference.clear()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.initialized:
            self.initialized = True
            QTimer.singleShot(0, self.initial_previews)

    def initial_previews(self):
        if self.prepared is None:
            self.previews.current.display('本结构', self.original, reset=True)
            self.use_reference()

    def start_search(self):
        self.changed()
        options = dict(kind=self.mode.currentData(), angles=[box.value() for box in self.angles],
                       angle_tolerance=self.angle_tolerance.value(), max_multiple=self.multiple.value(),
                       bound=self.bound.value(), length_tolerance=self.length_tolerance.value())
        source = self.reference.currentData()
        joint = self.target_enabled.isEnabled() and self.target_enabled.isChecked() and self.joint.isChecked() and source is not None
        if not joint:
            options['target'] = [box.value() for box in self.lengths] if self.target_enabled.isEnabled() and self.target_enabled.isChecked() else None
        self.worker = SearchTask(self.original, self.tolerance.value(), options,
                                 self.perpendicular.isChecked() and self.mode.currentData() != 'primitive3d', self,
                                 reference=source.structure if joint else None, max_atoms=self.max_atoms.value())
        self.worker.ready.connect(self.results)
        self.worker.failed.connect(self.info.setText)
        self.worker.finished.connect(self.search_finished)
        self.find.setEnabled(False)
        for widget in self.inputs:
            widget.setEnabled(False)
        self.priority.setEnabled(False)
        self.output.setEnabled(False)
        self.info.setText('正在识别平移对称性并搜索晶胞…')
        self.worker.start()

    def search_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.find.setEnabled(True)
        for widget in self.inputs:
            widget.setEnabled(True)
        self.controls()
        self.output.setEnabled(True)
        if self.closing:
            self.reject()

    def results(self, reduced, candidates):
        self.reduced = reduced
        self.matched = candidates if isinstance(candidates, Matches) else None
        self.candidates = order_matches(candidates.candidates, self.priority.currentData()) if self.matched is not None else candidates
        self.populate_candidates()
        if self.candidates:
            self.info.setText(f'重复单元：{len(self.original.symbols)} → {len(reduced.atoms)} 个原子；'
                              f'找到 {len(self.candidates)} 个候选。' +
                              ('列表保留原子数与边长偏差互有取舍的方案。' if self.matched is not None else ''))
        else:
            self.info.setText('当前范围内没有符合条件的周期晶胞。可调整容差、整数系数或面积倍数；'
                              '固定参考边长无解时可启用“两侧共同扩胞”；c 倾斜的含真空 slab 可选择“c 垂直表面”。')

    def populate_candidates(self):
        joint = self.matched is not None
        current_title = title('本结构', self.original)
        reference = self.reference.currentData()
        reference_title = title('参考', reference.structure) if reference is not None else '参考'
        headers = ([current_title + '\n原子', reference_title + '\n原子', current_title + '\na / b（Å）', reference_title + '\na / b（Å）', 'Δa / Δb', 'Δγ', '最大偏差'] if joint else
                   ['原子数', 'a（Å）', 'b（Å）', 'α（°）', 'β（°）', 'γ（°）', '面积倍数', '边长偏差'])
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.candidates))
        for row_index, candidate in enumerate(self.candidates):
            if joint:
                values = [str(candidate.current.count), str(candidate.reference.count),
                          '\n'.join(f'{x:.3f}' for x in candidate.current.lengths[:2]),
                          '\n'.join(f'{x:.3f}' for x in candidate.reference.lengths[:2]),
                          '\n'.join(f'{x:+.2f}%' for x in candidate.deltas), f'{candidate.angle_delta:.3f}°', f'{candidate.mismatch:.2f}%']
            else:
                values = [str(candidate.count), *(f'{value:.3f}' for value in candidate.lengths[:2]),
                          *(f'{value:.3f}' for value in candidate.angles), str(candidate.multiple),
                          '—' if candidate.mismatch is None else f'{candidate.mismatch:.2f}%']
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_index, column, item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents if joint else QHeaderView.ResizeMode.Stretch)
        self.table.resizeRowsToContents()
        if self.candidates:
            self.table.selectRow(0)
        self.selected()

    def sort_matches(self):
        if self.matched is not None:
            self.candidates = order_matches(self.matched.candidates, self.priority.currentData())
            self.populate_candidates()

    def selected(self, *_args):
        self.prepared, self.prepared_reference = None, None
        self.fix_unmapped.hide()
        self.fix_unmapped.setChecked(False)
        self.preview.setEnabled(self.reduced is not None and self.table.currentRow() >= 0)
        show_plan = self.matched is not None and self.table.currentRow() >= 0
        self.plan.setVisible(show_plan)
        if show_plan:
            match = self.candidates[self.table.currentRow()]
            if self.output.currentData() == 'unit':
                _unit, current_repeats = unit_candidate(self.reduced, match.current)
                _unit, reference_repeats = unit_candidate(self.matched.reference, match.reference)
                self.plan.setText(title('本结构', self.original) + '：输出改胞单元，恢复候选需重复 ' + ' × '.join(map(str, current_repeats)) +
                                  '\n' + title('参考', self.reference.currentData().structure) + '：输出改胞单元，恢复候选需重复 ' + ' × '.join(map(str, reference_repeats)))
            else:
                self.plan.setText(title('本结构', self.original) + '：先取面内原胞，再' + instruction(match.current.matrix) +
                                  '\n' + title('参考', self.reference.currentData().structure) + '：' + instruction(match.reference.matrix @ self.matched.reference.basis) + '（自动完成）')
        self.update_accept()

    def output_changed(self):
        prepared = self.prepared is not None
        self.selected()
        if prepared:
            self.prepare()

    def prepare(self):
        candidate = self.candidates[self.table.currentRow()]
        current = candidate.current if self.matched is not None else candidate
        current_unit, current_repeats = unit_candidate(self.reduced, current)
        if self.output.currentData() == 'unit':
            current = current_unit
        if current.count > 100000:
            self.info.setText('预览上限为 100000 个原子，请选择较小的候选晶胞。')
            return
        self.prepared = build(self.original, self.reduced, current, align=self.align.isChecked())
        matrix = current.matrix @ self.reduced.basis
        text = title('本结构', self.original) + '<br>' + preview_info(self.original, self.prepared.structure)
        text += '<br>对齐前：' + instruction(matrix)
        if self.output.currentData() == 'unit':
            text += '<br>此单元重复 ' + ' × '.join(map(str, current_repeats)) + ' 可恢复候选结构。'
        text += f'<br>平移对应最大误差：{self.reduced.error:.3g} Å'
        if self.matched is not None:
            source = self.reference.currentData()
            reference_unit, reference_repeats = unit_candidate(self.matched.reference, candidate.reference)
            reference = reference_unit if self.output.currentData() == 'unit' else candidate.reference
            self.prepared_reference = build(source.structure, self.matched.reference, reference, align=self.align.isChecked())
            reference_text = title('参考', source.structure) + '<br>' + preview_info(source.structure, self.prepared_reference.structure)
            reference_text += '<br>对齐前：' + instruction(reference.matrix @ self.matched.reference.basis)
            if self.output.currentData() == 'unit':
                reference_text += '<br>此单元重复 ' + ' × '.join(map(str, reference_repeats)) + ' 可恢复候选结构。'
            text = '<table width="100%"><tr><td width="50%" valign="top">' + text + '</td><td valign="top">' + reference_text + '</td></tr></table>'
            text += f'<br>Δγ={candidate.angle_delta:.3f}°。两侧未施加应变。换轴/反向已自动完成。'
        if self.perpendicular.isChecked() and self.mode.currentData() != 'primitive3d':
            text += '<br>c 已垂直表面，真空两侧的横向周期错位已改变。'
        if self.unresolved():
            text += f'<br>{self.unresolved()} 个原子的部分方向约束无法等价表示。'
        if self.no_thermal_dof():
            text += '<br>全部原子固定时不能维持正温度，请先调整温度或约束。'
        self.info.setText(text)
        self.fix_unmapped.setVisible(self.unresolved() > 0)
        self.update_accept()
        self.previews.current.display('本结构', self.prepared.structure)
        if self.prepared_reference is not None:
            self.previews.reference.display('参考', self.prepared_reference.structure)
        self.preview_requested.emit(self.prepared)

    def update_accept(self, *_args):
        ready = self.prepared is not None and (not self.unresolved() or self.fix_unmapped.isChecked()) and not self.no_thermal_dof()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ready)

    def no_thermal_dof(self):
        structures = [result.structure for result in (self.prepared, self.prepared_reference) if result is not None]
        return any(value.temperature is not None and value.temperature > 0 and value.free is not None and not value.free.any() for value in structures)

    def unresolved(self):
        return sum(result.unresolved for result in (self.prepared, self.prepared_reference) if result is not None)

    def reject(self):
        if self.worker is not None:
            self.closing = True
            self.worker.requestInterruption()
            self.info.setText('正在停止搜索…')
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)

    def done(self, result):
        super().done(result)
        self.previews.close_views()
