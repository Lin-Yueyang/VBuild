"""On-demand public GitHub checks, download and install controls."""

import json
from pathlib import Path
import sys
import tomllib

from PySide6.QtCore import QProcess, QProcessEnvironment, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel,
                              QProgressBar, QPushButton, QVBoxLayout)

from . import runtime, update
from .paths import ROOT
from .update_files import version


class UpdateDialog(QDialog):
    def __init__(self, parent=None, *, root=ROOT):
        super().__init__(parent)
        self.root, self.info, self.downloaded = Path(root), None, None
        self.install_stage, self.closing = None, False
        self.setWindowTitle('检查更新')
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setMinimumWidth(460)
        self.current = tomllib.loads((self.root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
        form = QFormLayout()
        self.local_label, self.latest_label = QLabel(self.current), QLabel('尚未检查')
        form.addRow('当前版本', self.local_label)
        form.addRow('最新版本', self.latest_label)
        form.addRow('来源', QLabel(update.REPOSITORY))
        layout.addLayout(form)
        row = QHBoxLayout()
        self.check = QPushButton('检查更新')
        self.check.setObjectName('check_updates')
        self.check.clicked.connect(lambda: self.start('check'))
        self.download = QPushButton('下载更新')
        self.download.setEnabled(False)
        self.download.clicked.connect(lambda: self.start('download'))
        self.install = QPushButton('安装并重启')
        self.install.setEnabled(False)
        self.install.clicked.connect(self.request_install)
        for button in (self.check, self.download, self.install):
            button.setAutoDefault(False)
            row.addWidget(button)
        layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        self.open_folder = QPushButton('打开下载目录')
        self.open_folder.setEnabled(False)
        self.open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.downloaded['archive']).parent))))
        self.close_button = QPushButton('关闭')
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.open_folder)
        footer.addStretch()
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.started.connect(self.send_request)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished_process)
        self.process.errorOccurred.connect(self.process_error)
        self.resize(500, self.sizeHint().height())
        if sys.platform != 'win32':
            self.check.setEnabled(False)
            self.status.setText('此更新入口适用于 Windows 轻量版。')

    @property
    def running(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, action):
        self.action, self.buffer, self.response, self.failure = action, b'', None, ''
        self.request = dict(info=self.info)
        if action == 'check':
            self.info, self.downloaded = None, None
            self.latest_label.setText('正在检查…')
        for button in (self.check, self.download, self.install):
            button.setEnabled(False)
        self.open_folder.setEnabled(False)
        self.status.setText('正在检查 GitHub…' if action == 'check' else '正在下载并校验…')
        self.progress.setRange(0, 0)
        self.progress.show()
        self.close_button.setText('取消')
        environment = QProcessEnvironment()
        for name, value in runtime.process_env(sys.executable).items():
            environment.insert(name, value)
        self.process.setProcessEnvironment(environment)
        self.process.start(str(runtime.console_python(sys.executable)),
                           ['-B', '-u', str(Path(update.__file__)), action, '--root', str(self.root)])

    def send_request(self):
        self.process.write((json.dumps(self.request) + '\n').encode('utf-8'))
        self.process.closeWriteChannel()
        self.request = None

    def read_output(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            text = line.decode('utf-8').strip()
            if text.startswith('VBUILD_RESULT '):
                self.response = json.loads(text.removeprefix('VBUILD_RESULT '))
            elif text.startswith('VBUILD_ERROR '):
                self.failure = json.loads(text.removeprefix('VBUILD_ERROR '))['message']
            elif text.startswith('VBUILD_PROGRESS '):
                progress = json.loads(text.removeprefix('VBUILD_PROGRESS '))
                self.progress.setRange(0, 100 if progress['total'] else 0)
                if progress['total']:
                    self.progress.setValue(round(100 * progress['current'] / progress['total']))

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.failure = '无法启动更新检查：' + self.process.errorString()
            self.finish_controls()

    def finish_controls(self):
        self.progress.hide()
        self.check.setEnabled(True)
        self.close_button.setText('关闭')
        if self.failure:
            self.status.setText(self.failure)
            if self.action == 'check':
                self.latest_label.setText('未能获取')

    def finished_process(self, code, _status):
        self.read_output()
        if self.closing:
            super().reject()
            return
        if code and not self.failure:
            self.failure = '更新检查未完成，请重试。'
        self.finish_controls()
        if self.failure:
            return
        if self.action == 'check':
            self.info = self.response
            self.latest_label.setText(self.info['version'])
            newer = version(self.info['version']) > version(self.current)
            self.download.setEnabled(newer)
            self.status.setText('发现新版本，可以下载。' if newer else '当前已是最新版本。' if version(self.info['version']) == version(self.current) else '本地版本高于 GitHub 版本。')
        else:
            self.downloaded = self.response
            self.open_folder.setEnabled(True)
            self.install.setEnabled((self.root / 'manifest.json').is_file())
            self.status.setText('下载和校验完成，安装前会备份旧程序。' if self.install.isEnabled()
                                else '下载和校验完成；开发目录请手动使用下载的文件。')

    def request_install(self):
        self.install_stage = Path(self.downloaded['stage'])
        self.accept()

    def reject(self):
        if self.running:
            self.closing = True
            self.process.kill()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.running:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)
