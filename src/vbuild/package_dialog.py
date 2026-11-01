"""Explicit optional-feature installation with a reviewed pip plan and live output."""

import codecs
import json
import os
from pathlib import Path
import signal
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                              QProgressBar, QPushButton, QVBoxLayout)

from . import dependencies, runtime
from .paths import ROOT, work_dir

TEXT_SELECTION = Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard


class PackageDialog(QDialog):
    def __init__(self, parent, *, root=ROOT):
        super().__init__(parent)
        self.root, self.python = Path(root), runtime.console_python(sys.executable)
        self.action, self.output, self.plan = '', '', None
        self.approved, self.cancelled, self.restart = False, False, False
        self.job = None
        self.setWindowTitle('安装 RDKit')
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(QLabel('SMILES 预览需要 RDKit，是否安装到当前 Python？'))
        path = QLabel(str(self.python))
        path.setTextInteractionFlags(TEXT_SELECTION)
        path.setWordWrap(True)
        layout.addWidget(path)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.details = QPushButton('显示详情')
        self.details.setCheckable(True)
        self.details.setAutoDefault(False)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMinimumHeight(130)
        self.log.hide()
        self.details.toggled.connect(self.log.setVisible)
        layout.addWidget(self.details)
        layout.addWidget(self.log)
        row = QHBoxLayout()
        row.addStretch()
        self.install = QPushButton('安装')
        self.install.clicked.connect(lambda: self.start('feature-plan'))
        self.install.setDefault(True)
        self.cancel = QPushButton('取消')
        self.cancel.setAutoDefault(False)
        self.cancel.clicked.connect(self.reject)
        row.addWidget(self.install)
        row.addWidget(self.cancel)
        row.addStretch()
        layout.addLayout(row)
        self.process = QProcess(self)
        if sys.platform == 'darwin':
            self.process.setUnixProcessParameters(QProcess.UnixProcessFlag.CreateNewSession)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.started.connect(self.started_process)
        self.process.finished.connect(self.finished_process)
        self.process.errorOccurred.connect(self.process_error)
        self.resize(520, self.sizeHint().height())

    @property
    def running(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, action):
        self.action, self.output, self.cancelled = action, '', False
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.status.setText('正在检查依赖…' if action == 'feature-plan' else '正在安装 RDKit…')
        self.status.show()
        self.install.setEnabled(False)
        self.progress.show()
        environment = QProcessEnvironment()
        for name, value in runtime.process_env(self.python).items():
            environment.insert(name, value)
        self.process.setProcessEnvironment(environment)
        arguments = ['-B', '-u', str(Path(runtime.__file__)), action, '--root', str(self.root),
                     '--python', str(self.python), '--feature', 'molecules']
        if action == 'feature-install':
            arguments.extend(['--plan', str(self.plan)])
            if self.approved:
                arguments.append('--approve-changes')
        self.process.start(str(self.python), arguments)

    def read_output(self):
        text = self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        self.output += text
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        self.log.insertPlainText(text)

    def started_process(self):
        if sys.platform == 'win32':
            from .process_job import ProcessJob
            try:
                self.job = ProcessJob(self.process.processId())
                if self.cancelled:
                    self.job.stop()
            except OSError as error:
                self.output += str(error)
                self.process.kill()
                self.failed('无法管理安装进程：' + str(error))

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.failed('无法启动安装：' + self.process.errorString())

    def failed(self, message):
        self.progress.hide()
        self.install.setEnabled(True)
        self.install.setText('重试')
        self.status.setText(message)
        self.status.show()
        self.details.setChecked(True)

    def approve_changes(self, plan):
        message = QMessageBox(self)
        message.setWindowTitle('调整已有依赖')
        message.setText(dependencies.summary([plan]) + '\n\n是否继续安装？')
        message.setTextInteractionFlags(TEXT_SELECTION)
        proceed = message.addButton('继续安装', QMessageBox.ButtonRole.AcceptRole)
        cancel = message.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        message.setDefaultButton(cancel)
        message.exec()
        return message.clickedButton() is proceed

    def finished_process(self, code, _status):
        self.read_output()
        if self.job is not None:
            self.job.close()
            self.job = None
        logs = work_dir(self.root) / 'log'
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / 'rdkit-install.log').open('a', encoding='utf-8') as stream:
            stream.write('\n' + self.action + '\n' + self.output)
        if self.cancelled:
            super().reject()
            return
        if code:
            self.failed('安装失败，请查看详情。')
            return
        result = json.loads(next(line.removeprefix('VBUILD_RESULT ') for line in self.output.splitlines()
                                 if line.startswith('VBUILD_RESULT ')))
        if self.action == 'feature-plan':
            self.plan = Path(result['plan'])
            plan = json.loads(self.plan.read_text(encoding='utf-8'))
            self.approved = False
            if any(item['current'] is not None for item in plan['changes']):
                self.approved = self.approve_changes(plan)
                if not self.approved:
                    self.progress.hide()
                    self.install.setEnabled(True)
                    self.status.setText('已取消，Python 库未修改。')
                    return
            QTimer.singleShot(0, self.continue_install)
        else:
            self.restart = result['restart']
            self.progress.hide()
            self.accept()

    def continue_install(self):
        if not self.cancelled:
            self.start('feature-install')

    def reject(self):
        self.cancelled = True
        if not self.running:
            super().reject()
            return
        if sys.platform == 'win32':
            if self.job is not None:
                try:
                    self.job.stop()
                except OSError as error:
                    self.status.setText('取消失败：' + str(error))
                    return
        else:
            os.killpg(self.process.processId(), signal.SIGTERM)
        self.status.setText('正在取消…')

    def closeEvent(self, event):
        if self.running:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)
