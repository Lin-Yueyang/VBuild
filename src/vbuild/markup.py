"""Real rich-text subscripts and crystallographic overbars in Qt controls."""

from html import escape
import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QLabel, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout


def formula_html(text):
    return re.sub(r"(\d+(?:\.\d+)?)", r"<sub>\1</sub>", escape(text))


def spacegroup_html(text):
    text = re.sub(r"_\{?(\d+)\}?", r"<sub>\1</sub>", escape(text))
    return re.sub(r"-(\d+)", r'<span style="text-decoration: overline;">\1</span>', text)


class FormulaButton(QPushButton):
    def __init__(self, formula, parent=None):
        super().__init__(parent)
        self.setAccessibleName(formula)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 3)
        self.label = QLabel(formula_html(formula))
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.label)
        self.setMinimumSize(58, 30)


class RichDelegate(QStyledItemDelegate):
    def document(self, option, index):
        doc = QTextDocument()
        doc.setDocumentMargin(2)
        doc.setDefaultFont(option.font)
        color = option.palette.text().color()
        doc.setHtml('<span style="color:' + color.name() + '">' + index.data(Qt.ItemDataRole.UserRole) + '</span>')
        return doc

    def paint(self, painter, option, index):
        if index.data(Qt.ItemDataRole.UserRole) is None:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        doc = self.document(opt, index)
        opt.text = ""
        opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setClipRect(opt.rect)
        painter.translate(opt.rect.x() + (opt.rect.width() - doc.idealWidth()) / 2,
                          opt.rect.y() + (opt.rect.height() - doc.size().height()) / 2)
        doc.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index):
        if index.data(Qt.ItemDataRole.UserRole) is None:
            return super().sizeHint(option, index)
        doc = self.document(option, index)
        return QSize(round(doc.idealWidth()) + 8, round(doc.size().height()) + 6)
