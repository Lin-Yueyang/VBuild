"""Movable material, scene and portable-style editor for a rendering session."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
                              QScrollArea, QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .atoms import PeriodicTable
from .color import ColorEditor
from .look import KINDS, default_style
from .overlay import Overlay
from .bond_dialog import BondDialog
from .appearance import atom_values


class RenderPanel(Overlay):
    changed = Signal(object)
    selected = Signal(str)
    file_requested = Signal(str)
    closed = Signal()
    atom_changed = Signal(int, str, object)
    atom_requested = Signal(int)

    def __init__(self, parent):
        super().__init__(parent, resizable=True)
        self.profile, self.updating, self.fields = None, False, []
        self.range_editor = None
        self.atom_values = default_style()["elements"]["H"].copy()
        self.atom_index = None
        self.symbols = ()
        self.setObjectName("render_panel")
        self.setStyleSheet("#render_panel { background: #f8f9fa; border: 1px solid #aeb6c2; }")
        self.setMinimumWidth(325)
        self.resize(350, 650)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        row = QHBoxLayout()
        self.grip.setText("渲染 · 样式编辑")
        row.addWidget(self.grip, 1)
        close = QPushButton("×")
        close.setFixedSize(22, 22)
        close.clicked.connect(self.closed)
        row.addWidget(close)
        layout.addLayout(row)
        self.style_toggle = QToolButton()
        self.style_toggle.setText("样式管理")
        self.style_toggle.setCheckable(True)
        self.style_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.style_toggle.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.style_toggle)
        self.style_box = QGroupBox("选择、保存与分享样式")
        management = QVBoxLayout(self.style_box)
        row = QHBoxLayout()
        row.addWidget(QLabel("样式"))
        self.styles = QComboBox()
        self.styles.currentTextChanged.connect(self.choose_style)
        row.addWidget(self.styles, 1)
        management.addLayout(row)
        row = QHBoxLayout()
        for key, label in (("save_scene", "保存渲染场景"), ("open_scene", "打开渲染场景")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked, kind=key: self.file_requested.emit(kind))
            row.addWidget(button)
        management.addLayout(row)
        note = QLabel("样式：可跨体系复用。场景：包含结构、单原子设置和视角。")
        note.setWordWrap(True)
        management.addWidget(note)
        form = QFormLayout()
        self.name = self.text(form, "名称", ("name",))
        self.text(form, "作者", ("author",))
        self.text(form, "说明", ("description",))
        management.addLayout(form)
        row = QHBoxLayout()
        for key, label in (("save", "保存样式"), ("import", "导入"), ("export", "导出")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked, kind=key: self.file_requested.emit(kind))
            row.addWidget(button)
        management.addLayout(row)
        layout.addWidget(self.style_box)
        self.style_box.hide()
        self.style_toggle.toggled.connect(self.show_style_box)
        form = QFormLayout()
        self.representation = self.combo(form, "整体模型", ("representation",), KINDS)
        layout.addLayout(form)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.element = QComboBox()
        self.element.setEditable(True)
        self.element.lineEdit().setReadOnly(True)
        self.element.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.element.currentTextChanged.connect(self.load_element)
        elements = self.page("元素")
        row = QWidget()
        buttons = QHBoxLayout(row)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.element, 1)
        table = QPushButton("周期表")
        table.clicked.connect(self.periodic_table)
        buttons.addWidget(table)
        elements.addRow("元素", row)
        self.check(elements, "显示此元素", ("elements", None, "show"))
        self.check(elements, "显示相连的键", ("elements", None, "bonds"))
        self.combo(elements, "模型", ("elements", None, "representation"), (("inherit", "跟随整体"), *KINDS))
        self.color = ColorEditor()
        self.color.changed.connect(lambda value: self.update_field(("elements", None, "color"), value))
        elements.addRow(self.color)
        for key, label, low, high, step, suffix in (
                ("radius", "球半径", 0.01, 10, 0.01, " Å"), ("filling", "填充半径", 0.01, 10, 0.01, " Å"),
                ("metallic", "金属度", 0, 1, 0.05, ""), ("roughness", "粗糙度", 0, 1, 0.05, ""),
                ("specular", "光泽", 0, 1, 0.05, ""), ("opacity", "不透明度", 0, 1, 0.05, "")):
            self.spin(elements, label, ("elements", None, key), low, high, step, suffix)
        reset = QPushButton("恢复此元素的 Default 外观")
        reset.clicked.connect(self.reset_element)
        elements.addRow(reset)
        all_materials = QPushButton("将此材质应用到所有元素")
        all_materials.setToolTip("统一金属度、粗糙度、光泽及不透明度；各元素颜色和半径独立保留。")
        all_materials.clicked.connect(self.all_materials)
        elements.addRow(all_materials)
        atom = self.page("单个原子")
        self.atom_controls = atom.parentWidget()
        self.atom_number = QSpinBox()
        self.atom_number.setMinimum(1)
        self.atom_number.valueChanged.connect(lambda value: self.atom_requested.emit(value - 1) if not self.updating else None)
        target = QWidget()
        target_row = QHBoxLayout(target)
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.addWidget(self.atom_number, 1)
        choose_atom = QPushButton("选取")
        choose_atom.clicked.connect(lambda: self.atom_requested.emit(self.atom_number.value() - 1))
        target_row.addWidget(choose_atom)
        atom.addRow("原子序号", target)
        self.atom_label = QLabel("选择一个原子，或输入它的序号。")
        atom.addRow(self.atom_label)
        self.check(atom, "显示此原子", ("atom", "show"))
        self.check(atom, "显示相连的键", ("atom", "bonds"))
        self.combo(atom, "模型", ("atom", "representation"), (("inherit", "跟随整体"), *KINDS))
        self.atom_color = ColorEditor()
        self.atom_color.changed.connect(lambda value: self.update_field(("atom", "color"), value))
        atom.addRow(self.atom_color)
        for key, label, low, high, step, suffix in (
                ("radius", "球半径", 0.01, 10, 0.01, " Å"), ("filling", "填充半径", 0.01, 10, 0.01, " Å"),
                ("metallic", "金属度", 0, 1, 0.05, ""), ("roughness", "粗糙度", 0, 1, 0.05, ""),
                ("specular", "光泽", 0, 1, 0.05, ""), ("opacity", "不透明度", 0, 1, 0.05, "")):
            self.spin(atom, label, ("atom", key), low, high, step, suffix)
        reset_atom = QPushButton("恢复此原子的元素设置")
        reset_atom.clicked.connect(lambda: self.atom_changed.emit(self.atom_index, "reset", None) if self.atom_index is not None else None)
        atom.addRow(reset_atom)
        self.atom_controls.setEnabled(False)
        bonds = self.page("键")
        self.check(bonds, "显示键", ("bonds", "show"))
        self.combo(bonds, "形状", ("bonds", "shape"), (("cylinder", "圆柱"), ("line", "线")))
        self.spin(bonds, "半径", ("bonds", "radius"), 0.01, 5, 0.01, " Å")
        self.spin(bonds, "线宽", ("bonds", "width"), 1, 100, 1, " px")
        self.check(bonds, "两端使用元素颜色", ("bonds", "split"))
        self.color_button(bonds, "统一颜色", ("bonds", "color"))
        for key, label in (("metallic", "金属度"), ("roughness", "粗糙度"), ("opacity", "不透明度")):
            self.spin(bonds, label, ("bonds", key), 0, 1, 0.05)
        self.spin(bonds, "成键半径系数", ("bonds", "factor"), 0.1, 5, 0.05)
        ranges = QPushButton("按元素对设置键长范围")
        ranges.clicked.connect(self.bond_ranges)
        bonds.addRow(ranges)
        poly = self.page("多面体")
        self.check(poly, "显示多面体", ("polyhedra", "show"))
        self.text(poly, "中心元素", ("polyhedra", "centers"))
        self.text(poly, "邻居元素", ("polyhedra", "neighbors"))
        note = QLabel("元素用逗号分隔，留空代表所有元素。使用成键范围搜索周期邻居，至少四个不共面的邻居才形成多面体。")
        note.setWordWrap(True)
        poly.addRow(note)
        self.spin(poly, "不透明度", ("polyhedra", "opacity"), 0, 1, 0.05)
        self.check(poly, "显示棱边", ("polyhedra", "edges"))
        scene = self.page("场景")
        from .look import LIGHTS
        self.combo(scene, "表面风格", ("scene", "lighting"), LIGHTS)
        self.spin(scene, "环境亮度", ("scene", "ambient"), 0, 1, 0.05)
        self.color_button(scene, "背景", ("scene", "background"))
        self.check(scene, "显示网格", ("scene", "grid"))
        self.color_button(scene, "网格颜色", ("scene", "grid_color"))
        self.spin(scene, "网格间距", ("scene", "grid_step"), 0.1, 100, 0.5, " Å")
        self.check(scene, "显示晶胞", ("scene", "cell"))
        self.color_button(scene, "晶胞颜色", ("scene", "cell_color"))
        self.color_button(scene, "序号颜色", ("scene", "label_color"))
        self.check(scene, "显示轴标", ("scene", "axes"))
        self.check(scene, "正交投影", ("scene", "parallel"))
        self.spin(scene, "整体原子尺寸", ("scene", "atom_scale"), 0.1, 5, 0.1)
        quality = QSpinBox()
        quality.setRange(16, 96)
        quality.setSingleStep(8)
        self.bind(quality, ("scene", "quality"), "spin")
        scene.addRow("球面细分", quality)
        for key, label in (("key", "主光"), ("fill", "补光"), ("rim", "轮廓光")):
            self.spin(scene, label, ("scene", key), 0, 5, 0.1)
        image = self.page("输出")
        scale = QSpinBox()
        scale.setRange(1, 4)
        self.bind(scale, ("image", "scale"), "spin")
        image.addRow("图片倍率", scale)
        self.check(image, "透明背景", ("image", "transparent"))
        self.check(image, "图片包含晶胞、网格及轴标", ("image", "guides"))
        export_image = QPushButton("导出 PNG 图片")
        export_image.clicked.connect(lambda: self.file_requested.emit("image"))
        image.addRow(export_image)
        note = QLabel("导出只包含三维画面；颜色和尺寸只改变外观，不修改结构坐标或元素。图片导出会隐藏选区高亮。")
        note.setWordWrap(True)
        image.addRow(note)
        self.status = QLabel("修改实时预览；保存样式可供下次使用，导出 JSON 可分享。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.hide()

    def show_style_box(self, visible):
        self.style_box.setVisible(visible)
        self.style_toggle.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)

    def page(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll.setWidget(widget)
        self.tabs.addTab(scroll, title)
        return layout

    def bind(self, widget, path, kind):
        widget.setObjectName("look_" + "_".join(key for key in path if key is not None))
        self.fields.append((widget, path, kind))
        if kind == "spin":
            widget.valueChanged.connect(lambda value: self.update_field(path, value))
        elif kind == "check":
            widget.toggled.connect(lambda value: self.update_field(path, value))
        elif kind == "combo":
            widget.currentIndexChanged.connect(lambda _index: self.update_field(path, widget.currentData()))
        elif kind == "text":
            widget.editingFinished.connect(lambda: self.update_field(path, widget.text()))

    def text(self, form, label, path):
        widget = QLineEdit()
        self.bind(widget, path, "text")
        form.addRow(label, widget)
        return widget

    def spin(self, form, label, path, low, high, step, suffix=""):
        widget = QDoubleSpinBox()
        widget.setDecimals(2)
        widget.setRange(low, high)
        widget.setSingleStep(step)
        widget.setSuffix(suffix)
        if path[-1] == "specular":
            widget.setToolTip("数值用于材质灯光；图形风格有各自固定的质感。")
        self.bind(widget, path, "spin")
        form.addRow(label, widget)
        return widget

    def check(self, form, label, path):
        widget = QCheckBox(label)
        self.bind(widget, path, "check")
        form.addRow(widget)

    def combo(self, form, label, path, options):
        widget = QComboBox()
        for key, text in options:
            widget.addItem(text, key)
        self.bind(widget, path, "combo")
        form.addRow(label, widget)
        return widget

    def color_button(self, form, label, path):
        button = QPushButton()
        self.fields.append((button, path, "color"))
        button.clicked.connect(lambda: self.choose_color(path))
        form.addRow(label, button)

    def entry(self, path):
        if path[0] == "atom":
            return self.atom_values
        data = self.profile
        for key in path[:-1]:
            data = data[self.element.currentText() if key is None else key]
        return data

    def update_field(self, path, value):
        if self.updating:
            return
        if path[0] == "atom":
            if self.atom_index is not None:
                self.atom_changed.emit(self.atom_index, path[-1], value)
            return
        if path[0] == "polyhedra" and path[-1] in ("centers", "neighbors"):
            value = value.replace(" ", ",").split(",")
            value = list(dict.fromkeys(item for item in value if item))
            if any(symbol not in self.profile["elements"] for symbol in value):
                self.status.setText("多面体元素输入无效，请使用元素符号，例如 Fe,O。")
                return
        self.entry(path)[path[-1]] = value
        self.light_controls()
        self.status.setText("外观已修改；保存样式可保留，导出可分享。")
        self.changed.emit(self.profile)

    def populate(self, profile, library, symbols):
        if self.range_editor is not None and profile is not self.profile:
            self.range_editor.reject()
        self.profile = profile
        self.symbols = tuple(dict.fromkeys(symbols))
        self.updating = True
        self.styles.clear()
        self.styles.addItems(library)
        if profile["name"] not in library:
            self.styles.addItem(profile["name"])
        self.styles.setCurrentText(profile["name"])
        self.update_elements(symbols)
        if self.element.currentText() not in self.symbols:
            self.element.blockSignals(True)
            self.element.setCurrentText(next(iter(self.symbols), "H"))
            self.element.blockSignals(False)
        self.load_fields()
        self.updating = False

    def load_fields(self, scope=None):
        for widget, path, kind in self.fields:
            if scope is not None and path[0] != scope:
                continue
            value = self.entry(path)[path[-1]]
            widget.blockSignals(True)
            if kind == "spin":
                widget.setValue(value)
            elif kind == "check":
                widget.setChecked(value)
            elif kind == "combo":
                widget.setCurrentIndex(widget.findData(value))
            elif kind == "text":
                widget.setText(",".join(value) if isinstance(value, list) else value)
            else:
                widget.setText(value)
                widget.setStyleSheet("background: " + value + "; color: " + ("black" if sum(int(value[i:i+2], 16) for i in (1, 3, 5)) > 380 else "white") + ";")
            widget.blockSignals(False)
        if scope != "atom":
            self.color.set_color(self.profile["elements"][self.element.currentText()]["color"])
        self.atom_color.set_color(self.atom_values["color"])
        self.light_controls()

    def light_controls(self):
        directional = self.profile["scene"]["lighting"] in ("soft", "studio")
        for widget, path, _kind in self.fields:
            if path[0] == "scene" and path[-1] in ("key", "fill", "rim", "ambient"):
                widget.setEnabled(directional)
                widget.setToolTip("二维风格使用固定的屏幕明暗；方向灯适用于漫反射或材质灯光。" if not directional else "")

    def update_elements(self, symbols):
        self.symbols = tuple(dict.fromkeys(symbols))
        previous = self.element.currentText()
        self.element.blockSignals(True)
        self.element.clear()
        self.element.addItems(self.symbols)
        self.element.setCurrentText(previous or next(iter(self.symbols), "H"))
        self.element.blockSignals(False)

    def set_atom(self, symbols, selected, overrides):
        self.atom_index = selected[0] if len(selected) == 1 else None
        self.atom_controls.setEnabled(bool(symbols))
        self.updating = True
        self.atom_number.setMaximum(max(1, len(symbols)))
        if self.atom_index is not None:
            index = self.atom_index
            self.atom_number.setValue(index + 1)
            self.atom_values = atom_values(self.profile, symbols[index], overrides, index).copy()
            self.atom_label.setText(f"{symbols[index]} {index + 1} · " + ("独立外观" if index in overrides else "沿用元素外观"))
        else:
            self.atom_label.setText("选中一个原子，或输入它的序号。")
        for widget, path, _kind in self.fields:
            if path[0] == "atom":
                widget.setEnabled(self.atom_index is not None)
        self.atom_color.setEnabled(self.atom_index is not None)
        self.load_fields("atom")
        self.updating = False

    def load_element(self, _symbol):
        if self.profile is not None:
            self.updating = True
            self.load_fields()
            self.updating = False

    def choose_style(self, name):
        if not self.updating:
            self.selected.emit(name)

    def periodic_table(self):
        dialog = PeriodicTable(self.element.currentText(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.element.setCurrentText(dialog.symbol)

    def choose_color(self, path):
        dialog = QDialog(self)
        dialog.setWindowTitle("颜色 · HEX")
        layout = QVBoxLayout(dialog)
        editor = ColorEditor()
        editor.set_color(self.entry(path)[path[-1]])
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.update_field(path, editor.hex.text())
            self.load_element(self.element.currentText())

    def reset_element(self):
        symbol = self.element.currentText()
        self.profile["elements"][symbol] = default_style()["elements"][symbol]
        self.load_element(symbol)
        self.changed.emit(self.profile)

    def all_materials(self):
        chosen = self.profile["elements"][self.element.currentText()]
        for values in self.profile["elements"].values():
            values.update({key: chosen[key] for key in ("metallic", "roughness", "specular", "opacity")})
        self.changed.emit(self.profile)

    def bond_ranges(self):
        if self.range_editor is not None:
            self.range_editor.raise_()
            return
        dialog = BondDialog(self.symbols, self.profile["bonds"]["factor"], self.profile["bonds"]["ranges"], self)
        self.range_editor = dialog
        def apply_ranges(value):
            self.profile["bonds"]["ranges"] = value
            self.changed.emit(self.profile)
        dialog.applied.connect(apply_ranges)
        dialog.finished.connect(lambda _result: setattr(self, "range_editor", None))
        dialog.finished.connect(dialog.deleteLater)
        dialog.show()

    def hideEvent(self, event):
        if self.range_editor is not None:
            self.range_editor.reject()
        super().hideEvent(event)

    def fit(self, bottom):
        if bottom - self.y() < 180:
            self.move(self.x(), max(0, bottom - 180))
        self.resize(self.width(), max(100, min(650, bottom - self.y())))
