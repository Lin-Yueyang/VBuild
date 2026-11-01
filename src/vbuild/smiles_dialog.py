"""An isolated molecule preview; confirmation returns a clipboard snapshot."""

from importlib import invalidate_caches
from importlib.util import find_spec
import site

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from .model import Structure
from .scene import Scene
from .markup import formula_html
from .smiles import generate


class SmilesWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, text, forcefield, parent):
        super().__init__(parent)
        self.text, self.forcefield = text, forcefield

    def run(self):
        try:
            self.ready.emit(generate(self.text, self.forcefield))
        except (ImportError, ValueError, RuntimeError) as error:
            self.failed.emit(str(error))


class SmilesDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("以SMILES添加分子")
        self.resize(600, 560)
        self.prepared, self.worker = None, None
        self.closing = False
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.text = QLineEdit()
        self.text.setObjectName("smiles_text")
        self.text.setPlaceholderText("输入 SMILES，如 CCO")
        self.text.textChanged.connect(self.changed)
        row.addWidget(self.text, 1)
        self.forcefield = QComboBox()
        self.forcefield.addItems(("MMFF94s", "UFF"))
        self.forcefield.currentTextChanged.connect(self.changed)
        row.addWidget(self.forcefield)
        self.preview = QPushButton("预览")
        self.preview.setObjectName("smiles_preview")
        self.preview.setAutoDefault(False)
        self.preview.clicked.connect(self.prepare)
        row.addWidget(self.preview)
        layout.addLayout(row)
        self.formula = QLabel("请输入 SMILES，再点击预览。")
        layout.addWidget(self.formula)
        self.scene = Scene(self)
        self.scene.set_look("ball", view_style=parent.scene.visual.profile, rebuild=False)
        self.scene.set_bond_radius(parent.settings["bond_radius"])
        self.scene.previewing = True
        self.scene.setMinimumHeight(300)
        layout.addWidget(self.scene, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.setCenterButtons(True)
        self.confirm = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm.setText("复制到剪贴板")
        self.confirm.setAutoDefault(False)
        self.confirm.setDefault(False)
        self.confirm.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setAutoDefault(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.preview_keys = [QShortcut(QKeySequence(key), self) for key in ('Return', 'Enter')]
        for shortcut in self.preview_keys:
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.prepare)

    def changed(self, *_args):
        self.prepared = None
        self.confirm.setEnabled(False)
        self.formula.setText("参数已改变，请重新预览。")

    def prepare(self):
        if self.worker is not None:
            return
        if not self.text.text().strip():
            self.status.setText('请输入 SMILES。')
            return
        if find_spec('rdkit') is None:
            from .package_dialog import PackageDialog
            installer = PackageDialog(self)
            if installer.exec() != QDialog.DialogCode.Accepted:
                self.status.setText('已取消安装。')
                return
            if installer.restart:
                self.status.setText('安装完成，已有依赖已更新，请重启软件后预览。')
                return
            if site.ENABLE_USER_SITE:
                site.addsitedir(site.getusersitepackages())
            invalidate_caches()
        self.changed()
        self.worker = SmilesWorker(self.text.text(), self.forcefield.currentText(), self)
        self.worker.ready.connect(self.ready)
        self.worker.failed.connect(self.status.setText)
        self.worker.finished.connect(self.finished_work)
        for widget in (self.text, self.forcefield, self.preview):
            widget.setEnabled(False)
        self.status.setText("正在生成并优化分子构象…")
        self.worker.start()

    def ready(self, molecule):
        self.prepared = molecule
        self.formula.setText("分子式：" + formula_html(molecule.formula))
        structure = Structure(molecule.symbols, None, molecule.positions.copy(), None, None,
                              molecule.symbols, (None,) * len(molecule.symbols))
        self.scene.set_structure(structure, reset=True)
        self.status.setText(f"{len(molecule.symbols)} 个原子；确认后按 Ctrl+V 粘贴。")

    def finished_work(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        for widget in (self.text, self.forcefield, self.preview):
            widget.setEnabled(True)
        self.confirm.setEnabled(self.prepared is not None and not self.closing)
        if self.closing:
            super().reject()

    def reject(self):
        if self.worker is not None:
            self.closing = True
            self.status.setText("生成结束后关闭…")
        else:
            super().reject()

    def done(self, code):
        self.scene.plotter.close()
        super().done(code)
