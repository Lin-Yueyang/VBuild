"""Compact Python selection and opt-in MLIP downloads."""

import codecs
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QStyle, QToolButton, QVBoxLayout)

from .mlip import MODELS, ROOT
from . import runtime
from .devices import gpu_device
from .paths import work_dir, models_dir


class EnvironmentDialog(QDialog):
    updated = Signal()

    def __init__(self, parent=None, *, root=ROOT):
        super().__init__(parent)
        self.root, self.action, self.output = Path(root), '', ''
        self.setWindowTitle('Python 与 MLIP')
        self.resize(540, 330)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Python 3.11–3.13（64 位），推荐 3.12。'))
        row = QHBoxLayout()
        config = work_dir(self.root) / 'python.txt'
        self.python = QLineEdit(config.read_text(encoding='utf-8-sig').strip() if config.exists()
                                else str(runtime.console_python(sys.executable)))
        self.python.setObjectName('environment_python')
        self.python.setPlaceholderText('选择 Python 路径' if sys.platform == 'darwin' else '选择 python.exe')
        row.addWidget(self.python, 1)
        self.browse = QPushButton('选择…')
        self.browse.clicked.connect(self.choose_python)
        row.addWidget(self.browse)
        layout.addLayout(row)
        self.apply = QPushButton('检测并使用')
        self.apply.clicked.connect(lambda: self.run('select'))
        layout.addWidget(self.apply)
        self.gui_install = QPushButton('安装建模依赖')
        self.gui_install.setObjectName('install_gui_dependencies')
        self.gui_install.clicked.connect(self.confirm_gui)
        layout.addWidget(self.gui_install)
        group = QGroupBox('MLIP（可选）')
        form = QVBoxLayout(group)
        self.models, self.model_status, self.folders = {}, {}, {}
        for key, model in MODELS.items():
            row = QHBoxLayout()
            box = QCheckBox(model.name)
            self.models[key] = box
            row.addWidget(box, 1)
            state = QLabel()
            self.model_status[key] = state
            row.addWidget(state)
            folder = QToolButton()
            folder.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
            folder.setToolTip("打开模型目录")
            folder.clicked.connect(lambda _checked=False, name=key: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str((models_dir(self.root) / MODELS[name].file).parent))))
            self.folders[key] = folder
            row.addWidget(folder)
            form.addLayout(row)
        self.gpu = QCheckBox('使用 Apple GPU（MACE；NEP 使用 CPU）' if sys.platform == 'darwin' else '使用 NVIDIA GPU（CUDA）')
        self.gpu.setToolTip('为勾选的模型安装对应计算后端；不安装系统显卡驱动。')
        form.addWidget(self.gpu)
        self.install = QPushButton('下载')
        self.install.setAutoDefault(False)
        self.install.clicked.connect(lambda: self.run('install'))
        form.addWidget(self.install)
        layout.addWidget(group)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.details = QPushButton('显示详情')
        self.details.setCheckable(True)
        self.details.toggled.connect(self.toggle_details)
        layout.addWidget(self.details)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.hide()
        layout.addWidget(self.log)
        row = QHBoxLayout()
        self.cancel = QPushButton('取消下载')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.stop)
        row.addWidget(self.cancel)
        close = QPushButton('关闭')
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.process = QProcess(self)
        if sys.platform == 'darwin':
            self.process.setUnixProcessParameters(QProcess.UnixProcessFlag.CreateNewSession)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.controls = (self.python, self.browse, self.apply, self.gui_install, self.install, self.gpu, *self.models.values())
        self.gui_packages = None
        self.python.textChanged.connect(lambda _text: setattr(self, 'gui_packages', None))
        self.refresh()
        QTimer.singleShot(0, lambda: self.run('probe'))

    @property
    def running(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def refresh(self):
        for key in self.models:
            path = models_dir(self.root) / MODELS[key].file
            available = path.is_file()
            self.model_status[key].setText('已下载' if available else '未下载')
            self.model_status[key].setEnabled(not available)
            self.folders[key].setEnabled(path.parent.is_dir())

    def choose_python(self):
        file_filter = '所有文件 (*)' if sys.platform == 'darwin' else 'Python (python.exe pythonw.exe)'
        path, _filter = QFileDialog.getOpenFileName(self, '选择 Python', '', file_filter)
        if path:
            self.python.setText(path)
            self.run('probe')

    def confirm_gui(self):
        if self.gui_packages is None:
            self.status.setText('请先检测 Python。')
            return
        packages = runtime.gui_plan(self.gui_packages)
        if not packages:
            self.status.setText('建模依赖已齐全。')
            return
        text = '安装到：' + self.python.text().strip() + '\n\n' + '、'.join(packages) + '\n\nQt/VTK 体积较大；不安装 MLIP 或 CUDA。'
        if QMessageBox.question(self, '安装建模依赖', text,
                               QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Ok:
            self.run('gui')

    def toggle_details(self, shown):
        self.log.setVisible(shown)
        self.details.setText('收起详情' if shown else '显示详情')
        self.resize(self.width(), 600 if shown else self.minimumSizeHint().height())

    def run(self, action):
        if self.running:
            return
        models = [key for key, box in self.models.items() if box.isChecked()]
        if action == 'install' and not models:
            self.status.setText('请勾选模型。')
            return
        self.action, self.output = action, ''
        self.gui_packages = None
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.status.setText('正在安装建模依赖…' if action == 'gui' else '正在下载…' if action == 'install' else '正在检测…')
        self.log.clear()
        for widget in self.controls:
            widget.setEnabled(False)
        self.cancel.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.show()
        arguments = ['-B', '-u', str(Path(runtime.__file__)), 'probe' if action == 'select' else action, '--root', str(self.root),
                     '--python', self.python.text().strip()]
        if action == 'install':
            arguments.extend(['--models', *models, '--device', gpu_device() if self.gpu.isChecked() else 'cpu'])
        environment = QProcessEnvironment()
        for name, value in runtime.process_env(sys.executable).items():
            environment.insert(name, value)
        self.process.setProcessEnvironment(environment)
        self.process.start(str(runtime.console_python(sys.executable)), arguments)

    def read_output(self):
        text = self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        self.output += text
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        self.log.insertPlainText(text)

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.complete('无法启动检测：' + self.process.errorString())

    def complete(self, text):
        for widget in self.controls:
            widget.setEnabled(True)
        self.cancel.setEnabled(False)
        self.progress.hide()
        self.status.setText(text)
        self.refresh()

    def process_finished(self, code, _status):
        self.read_output()
        logs = work_dir(self.root) / 'log'
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / 'setup.log').open('a', encoding='utf-8') as stream:
            stream.write('\n' + self.action + '\n' + self.output)
        if code:
            lines = [line for line in self.output.splitlines() if line.strip()]
            self.complete('失败或已取消。' + ('\n' + lines[-1] if lines else ''))
        elif self.action in ('probe', 'select'):
            item = json.loads(self.output)
            self.gui_packages = item['packages'] if item['supported'] else None
            problems = runtime.version_problems(item['packages'], runtime.CATALOG['gui_bounds'])
            text = 'Python 可用。' if item['supported'] and not problems else (
                          '请选择 Python 3.11–3.13（64 位）。' if not item['supported'] else
                          '缺少建模依赖：' + '、'.join(problems))
            if self.action == 'select' and item['supported'] and not problems:
                try:
                    runtime.remember_python(item, self.root)
                    text = '检测通过，重启软件后使用。'
                except OSError as error:
                    text = 'Python 设置无法保存：' + str(error)
            self.complete(text)
        elif self.action == 'gui':
            item = json.loads(next(line.removeprefix('VBUILD_RESULT ') for line in self.output.splitlines() if line.startswith('VBUILD_RESULT ')))
            self.gui_packages = item['packages']
            self.complete('建模依赖安装完成，可以检测并使用该 Python。')
        else:
            self.complete('下载完成，重新加载模型后使用。')
            self.updated.emit()

    def stop(self):
        if sys.platform == 'win32' and self.running:
            result = subprocess.run(['taskkill', '/PID', str(self.process.processId()), '/T', '/F'],
                capture_output=True, creationflags=runtime.NO_WINDOW)
            if result.returncode:
                self.status.setText('取消失败：' + result.stderr.decode('mbcs', errors='replace').strip())
        elif sys.platform == 'darwin' and self.running:
            os.killpg(int(self.process.processId()), signal.SIGKILL)
        else:
            self.process.kill()

    def reject(self):
        if not self.running:
            super().reject()

    def closeEvent(self, event):
        if self.running:
            self.status.setText('请先等待完成或取消下载。')
            event.ignore()
        else:
            super().closeEvent(event)
