"""A compact explicit mapping/units choice for files without chemical symbols."""

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                              QHBoxLayout, QLabel, QPushButton, QSpinBox)

from .atoms import ElementButton


class LammpsDialog(QDialog):
    def __init__(self, info, parent):
        super().__init__(parent)
        self.info, self.manual = info, set()
        self.setWindowTitle("导入 LAMMPS")
        layout = self.form = QFormLayout(self)
        hint = QLabel("按文件中的元素名称或标准原子质量预填，请确认后导入。\n同位素或自定义粒子需确认，可点击元素修改。")
        hint.setWordWrap(True)
        layout.addRow(hint)
        self.units = QComboBox()
        self.units.addItems(["metal", "real", "si", "electron"])
        if info.units not in ("metal", "real", "si", "electron"):
            raise ValueError("暂不支持 LAMMPS 单位：" + info.units)
        self.units.setCurrentText(info.units)
        layout.addRow("单位", self.units)
        layout.addRow(QLabel("文件单位：" + info.units + "（可修改）" if "units" in info.declared else "单位：文件未记录，默认 metal，请确认"))
        self.style = QComboBox()
        self.style.addItems(["auto", "atomic", "charge", "full"])
        if info.style not in ("auto", "atomic", "charge", "full"):
            raise ValueError("暂不支持 LAMMPS atom_style：" + info.style)
        self.style.setCurrentText(info.style)
        if not info.dump:
            layout.addRow("atom_style", self.style)
            layout.addRow(QLabel("文件格式：" + info.style if "atom_style" in info.declared else "原子格式：按列数预填，请确认"))
        self.periodic = []
        if not info.dump:
            row = QHBoxLayout()
            for axis, periodic in zip("abc", info.pbc):
                box = QCheckBox(axis)
                box.setChecked(periodic)
                self.periodic.append(box)
                row.addWidget(box)
            layout.addRow("周期边界（" + ("文件已记录" if "boundary" in info.declared else "未记录，请确认") + "）", row)
        else:
            layout.addRow(QLabel("周期边界（文件）：" + " / ".join("周期" if x else "非周期" for x in info.pbc)))
        self.elements, self.details = {}, {}
        for key in info.types:
            button = ElementButton()
            button.set_symbol(info.mapping.get(key, ""))
            self.elements[key] = button
            button.clicked.connect(lambda _checked=False, number=key: self.choose_element(number))
            detail = QLabel()
            self.details[key] = detail
            row = QHBoxLayout()
            row.addWidget(button)
            row.addWidget(detail, 1)
            layout.addRow("类型 " + str(key), row)
        self.extra_type = QSpinBox()
        self.extra_type.setRange(1, 2**31 - 1)
        self.extra_type.setValue(max(info.types, default=0) + 1)
        self.add_type = QPushButton("新增类型")
        self.add_type.clicked.connect(self.append_type)
        row = QHBoxLayout()
        row.addWidget(self.extra_type)
        row.addWidget(self.add_type)
        layout.addRow("其他帧的类型", row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认并导入")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        self.units.currentTextChanged.connect(self.suggest)
        self.suggest()

    def choose_element(self, key):
        self.manual.add(key)
        self.validate()

    def suggest(self):
        suggestions = self.info.suggestions(self.units.currentText())
        for key, detail in self.details.items():
            if key in self.info.mapping:
                detail.setText("文件元素：" + self.info.mapping[key])
            elif key in suggestions:
                symbol, standard, mass = suggestions[key][0]
                if key not in self.manual:
                    self.elements[key].set_symbol(symbol)
                detail.setText(f"质量 {mass:.5g} u → {symbol}（{standard:.5g} u）")
                detail.setToolTip("最近两个标准质量：" + "；".join(f"{x}: {value:.5g} u" for x, value, _mass in suggestions[key]))
            else:
                detail.setText("没有元素名称或质量，请选择")
        self.validate()

    def validate(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(all(button.symbol for button in self.elements.values()))

    def append_type(self):
        key = self.extra_type.value()
        if key in self.elements:
            return
        button = ElementButton()
        button.set_symbol("")
        button.clicked.connect(self.validate)
        self.elements[key] = button
        self.form.insertRow(self.form.rowCount() - 2, "类型 " + str(key), button)
        self.extra_type.stepUp()
        self.validate()

    def options(self):
        return {"units": self.units.currentText(), "style": self.style.currentText(),
                "pbc": self.info.pbc if self.info.dump else tuple(box.isChecked() for box in self.periodic),
                "mapping": {key: button.symbol for key, button in self.elements.items()}}
