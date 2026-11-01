"""Per-button visibility and press-hold ordering within fixed toolbar sections."""

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import QMenu, QRubberBand, QToolBar


class ModeBar(QToolBar):
    preferences_changed = Signal(object)

    def __init__(self, parent):
        super().__init__("操作模式", parent)
        self.setObjectName("modes")
        self.setMinimumWidth(42)
        self.sections, self.names, self.hidden = [], {}, set()
        self.source, self.target, self.dragging = None, None, False
        self.drag_button = None
        self.hold = QTimer(self)
        self.hold.setSingleShot(True)
        self.hold.setInterval(350)
        self.hold.timeout.connect(self.begin_move)
        self.marker = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.popup = None

    def configure(self, names, saved):
        self.separators = [action for action in self.actions() if action.isSeparator()]
        self.names = {name: action for name, action in names.items() if action in self.actions()}
        section = []
        for action in self.actions():
            if action.isSeparator():
                self.sections.append(section)
                section = []
            else:
                section.append(next(name for name, value in self.names.items() if action is value))
        self.sections.append(section)
        self.defaults = [group.copy() for group in self.sections]
        for index, stored in enumerate(saved.get("order", [])[:len(self.sections)]):
            group = self.sections[index]
            ordered = list(dict.fromkeys(name for name in stored if name in group))
            self.sections[index] = ordered + [name for name in group if name not in ordered]
        self.hidden = set(saved.get("hidden", [])) & self.names.keys()
        self.rebuild()

    def rebuild(self):
        for action in self.actions():
            self.removeAction(action)
        # Keep the same separator actions; rebuilding does not grow QObject children.
        for index, group in enumerate(self.sections):
            if index:
                self.addAction(self.separators[index - 1])
            for name in group:
                if name not in self.hidden:
                    action = self.names[name]
                    self.addAction(action)
                    button = self.widgetForAction(action)
                    button.setFixedSize(34, 34)
                    button.setProperty("modebar_name", name)
                    button.installEventFilter(self)
        self.updateGeometry()

    def saved(self):
        return {"order": [group.copy() for group in self.sections], "hidden": sorted(self.hidden)}

    def show_button(self, name, visible):
        self.hidden.discard(name) if visible else self.hidden.add(name)
        self.rebuild()
        self.preferences_changed.emit(self.saved())

    def restore(self):
        self.sections = [group.copy() for group in self.defaults]
        self.hidden.clear()
        self.rebuild()
        self.preferences_changed.emit(self.saved())

    def context_menu(self, name=None):
        menu = QMenu(self)
        if name is not None:
            menu.addAction("隐藏“" + self.names[name].text() + "”", lambda: self.show_button(name, False))
            menu.addSeparator()
        for index, group in enumerate(self.sections):
            if index:
                menu.addSeparator()
            for item in group:
                action = menu.addAction(self.names[item].text())
                action.setCheckable(True)
                action.setChecked(item not in self.hidden)
                action.toggled.connect(lambda visible, key=item: self.show_button(key, visible))
        menu.addSeparator()
        menu.addAction("恢复默认排列", self.restore)
        return menu

    def contextMenuEvent(self, event):
        self.popup = self.context_menu()
        self.popup.popup(event.globalPos())
        event.accept()

    def begin_move(self):
        self.dragging = True
        button = self.drag_button
        button.setDown(False)
        button.setCursor(Qt.CursorShape.ClosedHandCursor)

    def move_button(self, source, target):
        group = next(group for group in self.sections if source in group)
        if target not in group or source == target:
            return
        destination = group.index(target)
        group.remove(source)
        group.insert(destination, source)
        self.rebuild()
        self.preferences_changed.emit(self.saved())

    def eventFilter(self, watched, event):
        name = watched.property("modebar_name")
        kind = event.type()
        if kind == QEvent.Type.ContextMenu:
            self.popup = self.context_menu(name)
            self.popup.popup(event.globalPos())
            return True
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.source, self.target, self.dragging = name, None, False
            self.drag_button = watched
            self.hold.start()
        elif kind == QEvent.Type.MouseMove and self.dragging:
            action = self.actionAt(self.mapFromGlobal(event.globalPosition().toPoint()))
            group = next(group for group in self.sections if self.source in group)
            self.target = next((key for key in group if self.names[key] is action), None)
            self.marker.setVisible(self.target is not None)
            if self.target is not None:
                self.marker.setGeometry(self.widgetForAction(action).geometry())
            return True
        elif kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self.hold.stop()
            dragging, source, target = self.dragging, self.source, self.target
            self.source, self.target, self.dragging = None, None, False
            self.drag_button = None
            self.marker.hide()
            watched.unsetCursor()
            if dragging:
                watched.setDown(False)
                watched.releaseMouse()
                if target is not None:
                    QTimer.singleShot(0, lambda: self.move_button(source, target))
                return True
        elif kind in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate, QEvent.Type.UngrabMouse) and self.drag_button is watched:
            self.hold.stop()
            self.source, self.target, self.dragging = None, None, False
            self.drag_button = None
            self.marker.hide()
            watched.unsetCursor()
        return super().eventFilter(watched, event)
