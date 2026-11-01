"""One explicit format selection and export options, including trajectory stride."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QLabel, QSpinBox, QWidget

from .language import chinese


@dataclass
class ExportOptions:
    keep_constraints: bool = True
    temperature: float | None = None
    interval: int = 1
    coordinate_mode: str = 'direct'


class ExportDialog(QFileDialog):
    def __init__(self, parent, title, target, filters, *, trajectory=False, frames=1, preserved_mode=None):
        chinese()
        super().__init__(parent, title, str(Path(target).parent))
        self.setOption(QFileDialog.Option.DontUseNativeDialog)
        self.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        self.setFileMode(QFileDialog.FileMode.AnyFile)
        for label, text in ((QFileDialog.DialogLabel.LookIn, "位置"),
                            (QFileDialog.DialogLabel.FileName, "文件名"),
                            (QFileDialog.DialogLabel.FileType, "文件类型"),
                            (QFileDialog.DialogLabel.Accept, "保存"),
                            (QFileDialog.DialogLabel.Reject, "取消")):
            self.setLabelText(label, text)
        self.filters, self.trajectory = filters, trajectory
        self.preserved_mode = preserved_mode
        self.setNameFilters(list(filters))
        self.selectNameFilter(next(iter(filters)))
        self.selectFile(str(target))
        options = QWidget(self)
        grid = QGridLayout(options)
        self.keep = QCheckBox('保持约束')
        self.keep.setChecked(True)
        self.thermal = QCheckBox('重新赋予温度')
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 1e7)
        self.temperature.setDecimals(2)
        self.temperature.setSuffix(' K')
        self.temperature.setValue(300)
        self.stride = QCheckBox('间隔帧保存')
        self.stride.setEnabled(trajectory and frames > 1)
        self.interval = QSpinBox()
        self.interval.setRange(1, 1000000000)
        self.interval.setValue(1)
        self.interval.setEnabled(False)
        self.stride.toggled.connect(self.interval.setEnabled)
        self.thermal.toggled.connect(self.temperature.setEnabled)
        grid.addWidget(self.keep, 0, 0)
        grid.addWidget(self.thermal, 0, 1)
        grid.addWidget(self.temperature, 0, 2)
        grid.addWidget(self.stride, 1, 0)
        grid.addWidget(self.interval, 1, 1)
        grid.addWidget(QLabel('间隔 10：第 1、11、21…帧' if trajectory else '导出当前单帧'), 1, 2)
        self.coordinates = QComboBox()
        self.coordinates.setObjectName('export_coordinates')
        self.coordinates.addItem('分数坐标（Direct）', 'direct')
        self.coordinates.addItem('笛卡尔坐标（Cartesian，Å）', 'cartesian')
        if preserved_mode is not None:
            self.coordinates.setCurrentIndex(self.coordinates.findData(preserved_mode))
        self.coordinates.setToolTip('文件含续算数据，保留原始坐标格式。' if preserved_mode is not None else
                                    'VASP 单帧导出按分数坐标 [0,1) 归入晶胞；斜晶胞的笛卡尔分量仍可能为负。')
        grid.addWidget(QLabel('VASP 坐标'), 2, 0)
        grid.addWidget(self.coordinates, 2, 1, 1, 2)
        self.layout().addWidget(options, self.layout().rowCount(), 0, 1, self.layout().columnCount())
        self.filterSelected.connect(self.format_changed)
        self.format_changed(self.selectedNameFilter())

    @property
    def kind(self):
        return self.filters[self.selectedNameFilter()]

    def format_changed(self, selected):
        kind = self.filters[selected]
        self.keep.setEnabled(not self.trajectory and kind == 'vasp')
        self.thermal.setEnabled(not self.trajectory and kind in ('vasp', 'lammps-data'))
        self.temperature.setEnabled(self.thermal.isEnabled() and self.thermal.isChecked())
        self.coordinates.setEnabled(not self.trajectory and kind == 'vasp' and self.preserved_mode is None)
        self.setDefaultSuffix({'xyz': 'xyz', 'cif': 'cif', 'lammps-data': 'data', 'xdatcar': 'xdatcar'}.get(kind, ''))
        files = self.selectedFiles()
        if files:
            path = Path(files[0])
            suffix = {'vasp': '.vasp', 'xyz': '.xyz', 'cif': '.cif', 'lammps-data': '.data', 'xdatcar': '.xdatcar'}[kind]
            if path.suffix.lower() in ('.xyz', '.cif', '.data', '.lmp', '.lammps', '.vasp', '.xdatcar') and path.suffix.lower() != suffix:
                self.selectFile(str(path.with_suffix(suffix)))

    def export_options(self):
        return ExportOptions(self.keep.isChecked(),
                             self.temperature.value() if self.thermal.isEnabled() and self.thermal.isChecked() else None,
                             self.interval.value() if self.stride.isEnabled() and self.stride.isChecked() else 1,
                             self.coordinates.currentData())

    def accept(self):
        self.format_changed(self.selectedNameFilter())
        super().accept()
