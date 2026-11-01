"""NEB page orchestration; keep optional readers/widgets out of ordinary startup."""

import csv
from io import StringIO
from pathlib import Path

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from .neb import NebRun
from .neb_dialog import NebDialog
from .neb_panel import NebPanel, NebDataPanel
from .neb_plot import NebCurvePanel
from .neb_view import NebView
from .session import Session

PANES = {"neb_data": "show_data", "neb_history": "show_history", "neb_profile": "show_profile"}


class NebWorkspace:
    def __init__(self, window):
        self.window = window
        self.panel = NebPanel(window.scene.plotter.interactor)
        self.view = NebView(window.scene)
        window.scene.neb_view = self.view
        self.data = NebDataPanel(window.scene.plotter.interactor)
        self.history = NebCurvePanel("history", window.scene.plotter.interactor)
        self.profile = NebCurvePanel("profile", window.scene.plotter.interactor)
        for key, title, pane in (("neb_data", "NEB 数据", self.data),
                                 ("neb_history", "NEB 优化曲线", self.history),
                                 ("neb_profile", "NEB 能量路径", self.profile)):
            window.analysis.add(key, title, pane)
        self.panel.changed.connect(self.changed)
        self.panel.pane_requested.connect(self.toggle)
        self.panel.refresh_requested.connect(self.rescan)
        self.data.changed.connect(self.sync)
        for pane in (self.history, self.profile):
            pane.selection_changed.connect(self.sync)
        for signal in (self.history.plot.jump, self.profile.plot.jump):
            signal.connect(self.jump)
        self.data.export_requested.connect(self.export_data)
        for pane in (self.history, self.profile):
            pane.export_requested.connect(self.export_image)
        window.overlays["neb"] = self.panel
        if "neb" in window.settings.get("panels", {}):
            self.panel.restore(window.settings["panels"]["neb"])
        self.panel.geometry_changed.connect(lambda: window.save_panel("neb"))

    def set_scene(self, scene):
        if not hasattr(scene, "neb_view"):
            scene.neb_view = NebView(scene)
        self.view = scene.neb_view

    @property
    def session(self):
        window = self.window
        return window.sessions[window.current_index] if window.current_index >= 0 else None

    @property
    def run(self):
        session = self.session
        reader = None if session is None else session.trajectory
        return reader if isinstance(reader, NebRun) else None

    def open(self, root=None, replace=None):
        window = self.window
        if root is None:
            value = QFileDialog.getExistingDirectory(window, "选择 NEB 根目录", window.settings.get("neb_folder", ""))
            if not value:
                return
            root = Path(value)
        window.player.pause()
        dialog = NebDialog(root, window)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        run = dialog.prepared
        dialog.deleteLater()
        if not accepted:
            return
        window.settings["neb_folder"] = str(root)
        window.persist_settings()
        if replace is None:
            window._store_current()
            path = root / (root.name + "_NEB.vasp")
            session = Session(run.frame(0), path, trajectory=run, readonly=True, show_player=True,
                              analysis_tab="neb_profile")
            window.sessions.append(session)
            index = window.add_session_tab()
        else:
            window._store_current()
            session, previous = replace, replace.trajectory
            names = {previous.images[index].name for index in previous.selected}
            old_names = {image.name for image in previous.images}
            run.selected = {index for index, image in enumerate(run.images) if image.name in names or image.name not in old_names}
            data_names = {previous.images[index].name for index in previous.analysis_selected}
            run.analysis_selected = {index for index, image in enumerate(run.images) if image.name in data_names or image.name not in old_names}
            opaque_names = {previous.images[index].name for index in previous.opaque}
            run.opaque = {index for index, image in enumerate(run.images) if image.name in opaque_names or
                          image.name not in old_names and image.endpoint}
            active = previous.images[previous.active].name
            run.active = next((index for index, image in enumerate(run.images) if image.name == active), run.active)
            for name in ("opacity", "bond_opacity", "energy_key", *PANES.values()):
                setattr(run, name, getattr(previous, name))
            session.trajectory = run
            session.frame = min(session.frame, len(run.offsets)-1)
            session.document = run.frame(session.frame)
            previous.close_cache()
            index = window.sessions.index(session)
        if replace is None:
            window.activate_document(index)
        else:
            window._activate_session(index)
        self.panel.raise_()
        window.analysis.raise_()

    def prepare(self, index):
        if self.run is not None:
            self.view.update(self.run, index)

    def visible(self):
        run = self.run
        if run is None:
            self.panel.hide()
            self.view.hide()
            return {}
        frame = self.session.frame
        self.panel.populate(run, frame)
        self.panel.show()
        self.prepare(frame)
        visible = {key: getattr(run, value) for key, value in PANES.items()}
        if visible["neb_data"]:
            self.data.populate(run, frame)
        self.history.populate(run, frame)
        self.profile.populate(run, frame)
        reference, _energies = run.energy_reference(frame)
        self.profile.relative.setText("相对能量" if reference is None else "相对图像 " + run.images[reference].name + " 能量")
        return visible

    def sync(self):
        self.window.populate_results()
        self.window.place_panels()
        self.window.scene.plotter.render()

    def changed(self):
        self.sync()

    def jump(self, frame, image):
        self.run.active = image
        self.window.player.pause()
        self.window.seek_frame(frame, force=True)

    def toggle(self, key, visible):
        setattr(self.run, PANES[key], visible)
        if visible:
            self.session.analysis_tab = key
        self.sync()
        if visible:
            self.window.analysis.raise_()

    def close_pane(self, key):
        setattr(self.run, PANES[key], False)

    def place(self, bottom):
        if self.panel.isVisible():
            self.panel.fit(bottom)

    def rescan(self):
        self.open(self.run.path, replace=self.session)

    def export_data(self):
        run, window = self.run, self.window
        name, _filter = QFileDialog.getSaveFileName(window, "导出 NEB 数据", str(run.path / "NEB_data.csv"), "CSV (*.csv)")
        if not name:
            return
        content = StringIO(newline="")
        writer = csv.writer(content)
        writer.writerow(["image", "frame", "source_frame", "source", "converged_EDIFFG", "E0_eV", "without_entropy_eV", "free_energy_eV", "max_force_eV_A", "raw_max_force_eV_A"])
        for image_index in sorted(run.analysis_selected):
            image = run.images[image_index]
            for index, record in enumerate(image.records):
                writer.writerow([image.name, index+1, image.source_step if image.reader is None else index+1, image.path.name,
                                 run.convergence(image_index, index), *(record.get(key, "") for key in
                                 ("e_0_energy", "e_wo_entrp", "e_fr_energy", "max_force", "raw_max_force"))])
        try:
            window.write_content(Path(name), content.getvalue().encode("utf-8-sig"), overwrite=True)
        except (ValueError, OSError) as error:
            QMessageBox.critical(window, "NEB 导出失败", str(error))

    def export_image(self, kind):
        run, window = self.run, self.window
        name, _filter = QFileDialog.getSaveFileName(window, "导出 NEB 曲线", str(run.path / ("NEB_"+kind+".png")), "PNG (*.png)")
        if not name:
            return
        plot = self.profile.plot if kind == "profile" else self.history.plot
        try:
            plot.export(name)
        except (ValueError, OSError) as error:
            QMessageBox.critical(window, "NEB 图片导出失败", str(error))
