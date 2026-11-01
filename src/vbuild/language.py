"""Use Qt's own Simplified Chinese catalog for standard dialogs and file models."""

from PySide6.QtCore import QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication


def chinese():
    application = QApplication.instance()
    if application.findChild(QTranslator, "vbuild_chinese") is None:
        translator = QTranslator(application)
        translator.setObjectName("vbuild_chinese")
        translator.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
        application.installTranslator(translator)
