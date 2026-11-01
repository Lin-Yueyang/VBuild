"""Independent text appearance for atom indices, Bader charges, moments and measurements."""

from copy import deepcopy

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
    QFormLayout, QPushButton, QSpinBox, QVBoxLayout, QWidget)

KINDS = {'index': '原子序号', 'bader': 'Bader 电荷', 'moment': '磁矩', 'measure': '测量数值', 'force': '原子受力'}


def profiles(settings):
    defaults = dict(family='Arial', size=20, bold=True, color=None, outline='#ffffff', width=1.)
    result = {name: defaults | {'size': settings['index_font'] if name in ('index', 'bader', 'moment') else 20}
              | settings.get('label_styles', {}).get(name, {}) for name in KINDS}
    result['index']['size'] = settings['index_font']
    return result


class Preview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(96)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#dce2e7'))
        font = QFont(self.value['family'])
        font.setPixelSize(self.value['size'])
        font.setBold(self.value['bold'])
        path = QPainterPath()
        path.addText(0, 0, font, self.text)
        bounds = path.boundingRect()
        painter.translate(self.width() / 2 - bounds.center().x(), self.height() / 2 - bounds.center().y())
        painter.setPen(QPen(QColor(self.value['outline']), 2 * self.value['width'],
                           Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                       if self.value['width'] else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.value['color'] or '#000000'))
        painter.drawPath(path)
        painter.fillPath(path, QColor(self.value['color'] or '#000000'))


class LabelEditor(QWidget):
    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.values = deepcopy(values)
        self.current = 'index'
        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.kind = QComboBox()
        for key, name in KINDS.items():
            self.kind.addItem(name, key)
        form.addRow('文字类型', self.kind)
        self.family = QComboBox()
        installed = set(QFontDatabase.families())
        self.family.addItems([name for name in ('Arial', 'Times New Roman', 'Verdana', 'Courier New',
                                                'Consolas', 'Microsoft YaHei', 'PingFang SC') if name in installed])
        self.family.setFixedWidth(180)
        form.addRow('字体', self.family)
        self.size = QSpinBox()
        self.size.setObjectName('label_font_size')
        self.size.setRange(8, 48)
        self.size.setSuffix(' px')
        form.addRow('字号', self.size)
        self.bold = QCheckBox('加粗')
        form.addRow(self.bold)
        self.color, self.outline = QPushButton(), QPushButton()
        self.color.clicked.connect(lambda: self.pick('color'))
        self.outline.clicked.connect(lambda: self.pick('outline'))
        form.addRow('文字颜色', self.color)
        form.addRow('描边颜色', self.outline)
        self.width = QDoubleSpinBox()
        self.width.setRange(0, 4)
        self.width.setDecimals(1)
        self.width.setSingleStep(.5)
        self.width.setSuffix(' px')
        form.addRow('描边宽度', self.width)
        root.addLayout(form)
        self.preview = Preview()
        root.addWidget(self.preview)
        self.reset = QPushButton('恢复默认')
        self.reset.setToolTip('恢复当前文字类型的默认设置')
        self.reset.clicked.connect(self.restore)
        root.addWidget(self.reset, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addStretch()
        self.kind.currentIndexChanged.connect(self.select)
        self.family.currentTextChanged.connect(self.edit)
        self.size.valueChanged.connect(self.edit)
        self.bold.toggled.connect(self.edit)
        self.width.valueChanged.connect(self.edit)
        self.select()

    def select(self, *_args):
        self.current = self.kind.currentData()
        value = self.values[self.current]
        blocks = [QSignalBlocker(widget) for widget in (self.family, self.size, self.bold, self.width)]
        if self.family.findText(value['family']) < 0:
            self.family.addItem(value['family'])
        self.family.setCurrentText(value['family'])
        self.size.setValue(value['size'])
        self.bold.setChecked(value['bold'])
        self.width.setValue(value['width'])
        self.color.setText(value['color'] or '默认')
        self.outline.setText(value['outline'])
        del blocks
        self.edit()

    def edit(self, *_args):
        value = self.values[self.current]
        value.update(family=self.family.currentText(), size=self.size.value(), bold=self.bold.isChecked(),
                     color=None if self.color.text() == '默认' else self.color.text(),
                     outline=self.outline.text(), width=self.width.value())
        self.preview.value = value
        self.preview.text = {'index': 'Fe 1', 'bader': '+0.28 e', 'moment': 'm +2.20 μB',
                             'measure': '1.23 Å', 'force': '1.20 eV/Å'}[self.current]
        self.preview.update()

    def restore(self):
        self.values[self.current] = profiles({'index_font': 20})[self.current]
        self.select()

    def pick(self, key):
        button = self.color if key == 'color' else self.outline
        color = QColorDialog.getColor(QColor('#000000' if button.text() == '默认' else button.text()), self, '选择颜色')
        if color.isValid():
            button.setText(color.name())
            self.edit()
