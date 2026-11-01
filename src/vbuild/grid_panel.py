"""Compact guide controls with cell/Cartesian bases and optional advanced fields."""

from copy import deepcopy
from PySide6.QtCore import QSignalBlocker, Signal, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout,
                              QLabel, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget)
from .grid import coordinate_frame, geometry
from .overlay import Overlay


class GridPanel(Overlay):
    changed = Signal(object)
    layout_changed = Signal()

    def __init__(self, parent):
        super().__init__(parent, resizable=True, vertical=True)
        self.options, self.limits = {}, None
        self.setObjectName('grid_panel')
        self.setStyleSheet('#grid_panel {background:white; border:1px solid #bbc3cc; border-radius:4px;}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        header = QHBoxLayout()
        self.grip.setText('轴 / 网格 · Å')
        header.addWidget(self.grip, 1)
        close = QToolButton()
        close.setText('×')
        close.clicked.connect(self.hide)
        header.addWidget(close)
        layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        self.body = body
        scroll.setWidget(body)
        layout.addWidget(scroll)
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(5)
        content.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.enabled = QCheckBox('显示')
        self.enabled.toggled.connect(lambda value: self.edit('show', value))
        content.addWidget(self.enabled)
        self.basis = QComboBox()
        self.basis.setObjectName('grid_basis')
        self.basis.addItem('沿晶胞 a/b/c', 'cell')
        self.basis.addItem('笛卡尔 x/y/z', 'cartesian')
        self.basis.currentIndexChanged.connect(lambda _index: self.edit('basis', self.basis.currentData()))
        content.addWidget(self.basis)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(5)
        for column, text in enumerate(('', '轴', '网格', '平面')):
            grid.addWidget(QLabel(text), 0, column)
        self.boxes, self.positions = {}, []
        self.axis_titles = []
        for axis, name in enumerate('XYZ'):
            title = QLabel(name)
            self.axis_titles.append(title)
            grid.addWidget(title, axis + 1, 0)
            for column, key in enumerate(('axes', 'grids', 'planes'), 1):
                box = QCheckBox()
                box.setObjectName(f'grid_{key}_{name.lower()}')
                box.setToolTip(name + (' 轴' if key == 'axes' else ' 法向平面'))
                box.toggled.connect(lambda value, key=key, axis=axis: self.edit_axis(key, axis, value))
                grid.addWidget(box, axis + 1, column)
                self.boxes[key, axis] = box
        content.addLayout(grid)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        content.addLayout(form)
        self.advanced_button = QToolButton()
        self.advanced_button.setText('高级参数')
        self.advanced_button.setObjectName('grid_advanced')
        self.advanced_button.setCheckable(True)
        self.advanced_button.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        content.addWidget(self.advanced_button)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        content.addWidget(self.advanced)
        self.values = {}
        definitions = (('interval', '刻度间隔', .01, 1000000, 2, 1., ' Å'),
                       ('decimals', '数字小数位', 0, 5, None, 1, ''),
                       ('offset', '轴距晶胞', 0, 100, 2, .1, ' Å'),
                       ('font', '字号', 8, 48, None, 1, ' px'),
                       ('width', '线宽', .5, 8, 1, .5, ' px'),
                       ('axis_opacity', '轴线不透明度', 0, 1, 2, .05, ''),
                       ('grid_opacity', '网格不透明度', 0, 1, 2, .05, ''),
                       ('plane_opacity', '平面不透明度', 0, 1, 2, .05, ''))
        for key, label, low, high, decimals, step, suffix in definitions:
            box = QSpinBox() if decimals is None else QDoubleSpinBox()
            if decimals is not None:
                box.setDecimals(decimals)
            box.setRange(low, high)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            box.setObjectName('grid_' + key)
            box.valueChanged.connect(lambda value, key=key: self.edit(key, value))
            self.values[key] = box
            (form if key in ('interval', 'decimals') else advanced).addRow(label, box)
        self.position_titles = []
        for axis, name in enumerate('XYZ'):
            box = QDoubleSpinBox()
            box.setDecimals(2)
            box.setRange(-1000000, 1000000)
            box.setSingleStep(1)
            box.setSuffix(' Å')
            box.setObjectName('grid_position_' + name.lower())
            box.valueChanged.connect(lambda value, axis=axis: self.edit_axis('positions', axis, value))
            self.positions.append(box)
            title = QLabel(name + ' 平面位置')
            self.position_titles.append(title)
            form.insertRow(axis, title, box)
        reset = QPushButton('平面位置恢复贴边')
        reset.clicked.connect(lambda: self.edit('positions', [None] * 3))
        advanced.addRow(reset)
        self.advanced.hide()
        self.advanced_button.toggled.connect(self.toggle_advanced)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet('color:#ab3d28;')
        content.addWidget(self.message)
        content.addStretch()
        self.setMinimumSize(255, 160)
        self.resize(285, 330)
        self.hide()

    def populate(self, options, structure, periods):
        self.options, self.structure, self.periods = deepcopy(options), structure, periods
        limits, _basis = coordinate_frame(structure, periods, options['basis'])
        names = 'abc' if options['basis'] == 'cell' else 'XYZ'
        with QSignalBlocker(self.basis):
            self.basis.setCurrentIndex(self.basis.findData(options['basis']))
        self.basis.model().item(self.basis.findData('cell')).setEnabled(structure.cell is not None)
        with QSignalBlocker(self.enabled):
            self.enabled.setChecked(options['show'])
        for (key, axis), box in self.boxes.items():
            with QSignalBlocker(box):
                box.setChecked(options[key][axis])
        for key, box in self.values.items():
            with QSignalBlocker(box):
                box.setValue(options[key])
        for axis, box in enumerate(self.positions):
            self.axis_titles[axis].setText(names[axis])
            self.position_titles[axis].setText(names[axis] + ' 平面位置')
            others = ''.join(name for index, name in enumerate(names) if index != axis)
            for key in ('axes', 'grids', 'planes'):
                self.boxes[key, axis].setToolTip(names[axis] + ' 轴' if key == 'axes' else '平行 ' + others + ' 平面')
            box.setToolTip('沿 ' + names[axis] + ' 方向的距离，单位 Å')
            with QSignalBlocker(box):
                value = options['positions'][axis]
                box.setValue(limits[0, axis] if value is None else value)
            box.setEnabled(options['grids'][axis] or options['planes'][axis])

    def edit_axis(self, key, axis, value):
        values = self.options[key].copy()
        values[axis] = value
        self.edit(key, values)

    def edit(self, key, value):
        options = deepcopy(self.options) | {key: value}
        # User-controlled density is validated before building VTK geometry.
        try:
            limits, basis = coordinate_frame(self.structure, self.periods, options['basis'])
            geometry(limits, options, basis)
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.message.clear()
        self.changed.emit(options)

    def fit(self, bottom):
        preferred = self.body.sizeHint().height() + self.grip.height() + 25
        height = min(self.height(), preferred) if self.manual else preferred
        self.resize(self.width(), min(height, max(self.minimumHeight(), bottom - self.y() - 8)))

    def toggle_advanced(self, visible):
        self.advanced.setVisible(visible)
        self.advanced_button.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)
        self.body.layout().activate()
        self.resize(self.width(), self.body.sizeHint().height() + self.grip.height() + 25)
        self.layout_changed.emit()
