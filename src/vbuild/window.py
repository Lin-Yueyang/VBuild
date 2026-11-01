"""Compact structure workspace; file read/write boundaries report input errors."""

import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import uuid
from io import BytesIO
from shutil import copyfile
from xml.etree.ElementTree import ParseError
from time import perf_counter

import numpy as np
from PySide6.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon, QImage, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox, QTabBar, QToolBar, QToolButton,
    QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)
from .atoms import AtomDialog, AtomPanel, PeriodicTable
from .catalog import molecule_group
from .clipboard import ClipboardPanel, shared_clipboard
from .look import KINDS, default_style, read_style, style_bytes, validate as validate_style
from .style_panel import StylePanel
from .view import rotate_group
from .draft import DraftDocument
from .history import History
from .wrap import wrap_molecules
from .poscar import PoscarDocument
from .scene import Scene
from .session import Session
from .close import CloseDialog
from .display import DisplayPanel
from .bond_dialog import BondDialog
from .player import IndexThread, Player
from .trajectory import Trajectory, XyzDocument
from .relax import Relaxation, xdatcar_chunks
from .thermal import free_directions
from .relax_worker import RelaxController
from .relax_panel import RelaxPanel, RelaxInfo
from .results import AnalysisPanel, DataPanel, CurvePanel, csv_data
from .formats import serialize, output_kind, FrameDocument
from .inputs import input_kind, ImportThread
from .import_dialog import LammpsDialog
from .report_panel import ReportPanel
from .reports import ReportTrajectory
from .volume_panel import VolumePanel, defaults as volume_defaults
from .slice_panel import SlicePanel
from .mlip import model_paths
from .forces import ForceController
from .force_panel import ForcePanel
from .export_dialog import ExportDialog
from .model import Velocities
from .writing import group_order, vasp_header, vasp_rows
from .warmup import start as start_warmup
from .tabs import StructureTabs
from .mp_key import read as read_mp_key
from .modebar import ModeBar
from .history_menu import HistoryMenu
from .styles import TITLES, builtins as builtin_styles, insert as insert_style
from .paths import work_dir, data_dir
from .keys import bindings
from .cell_info import composition_text
from .grid import defaults as grid_defaults
from .grid_panel import GridPanel

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_GROUPS = ("编辑与视角", "原子与键", "电子数据", "文字", "快捷键", "Python 与 MLIP")
MODES = {
    "rotate": ("旋转", "左键旋转视角"),
    "select": ("选择", "左键点选/框选；Ctrl 多选；Shift+左键选相连分子"),
    "distance": ("距离", "依次点击两个原子"),
    "angle": ("角度", "依次点击三个原子，中间原子为顶点"),
    "dihedral": ("二面角", "依次点击四个原子"),
    "plane": ("面夹角", "依次点击两组三个原子；测量两平面较小夹角"),
    "edit": ("编辑", "Ctrl 多选；Shift+左键选相连分子"),
}
ARROWS = {"left": (-1, 0), "right": (1, 0), "up": (0, 1), "down": (0, -1)}
ROTATIONS = {"up": ("向上旋转", "pitch", 1), "down": ("向下旋转", "pitch", -1),
             "left": ("向左旋转", "yaw", 1), "right": ("向右旋转", "yaw", -1),
             "ccw": ("逆时针侧转", "roll", -1), "cw": ("顺时针侧转", "roll", 1)}


class Window(QMainWindow):
    def __init__(self, *, parent=None, settings_path: Path | None = None, downloads_path: Path | None = None):
        started = perf_counter()
        super().__init__(parent, Qt.WindowType.Widget if parent is not None else Qt.WindowType.Window)
        if parent is not None:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("icons") / "app.png")))
        self.resize(1100, 760)
        self.settings_path = work_dir() / "settings.json" if settings_path is None else settings_path
        self.downloads_path = data_dir() / "in" / "mp" if downloads_path is None else downloads_path
        defaults = Path(__file__).with_name("settings.json")
        settings = json.loads(defaults.read_text(encoding="utf-8"))
        if self.settings_path.exists():
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
            settings |= saved
            if "bond_radius" not in saved and "bond_width" in saved:
                settings["bond_radius"] = max(.01, saved["bond_width"] * .008)
        settings.pop("bond_width", None)
        self.settings = settings
        from .label_settings import profiles
        self.settings.setdefault('label_styles', profiles(settings))
        self.move_step, self.bond_scale = settings["move_step"], settings["bond_scale"]
        self.rotation_step = settings["rotation_step"]
        self.bond_ranges = settings["bond_ranges"]
        self.document: PoscarDocument | XyzDocument | DraftDocument | None = None
        self.path: Path | None = None
        self.history = History()
        self.sessions: list[Session] = []
        self.current_index = -1
        self.loaders = {}
        self.closing = False
        self.preload_started = False
        self.preload_task = None
        self.pending_features = []
        self.load_status = QLabel("等待后台载入")
        self.statusBar().addPermanentWidget(self.load_status)
        self.builder = None
        self.waiting_water_close = False
        self.neb_workspace = None
        self.bader_panel, self.bader_table = None, None
        self.moment_panel, self.moment_table = None, None
        self.environment = None
        self.comparison = None
        self.comparisons = []
        self.compare_number = 0
        self.clipboard = shared_clipboard(settings["clipboard_limit"])
        self.settings["clipboard_limit"] = self.clipboard.limit
        self.clip_panel, self.paste_index = None, None
        self.styles_path = self.settings_path.parent / "styles.json"
        self.style_library, self.render_panel = None, None
        self.view_menu = None
        self.mp_key = None if sys.platform == 'darwin' else read_mp_key()
        self.quick_symbol = "H"
        scene_started = perf_counter()
        self.scene = Scene(self)
        print(f'Startup: create 3D viewport {perf_counter() - scene_started:.3f}s', flush=True)
        self.single_scene = self.scene
        self.scene.contact_factor = settings["contact_factor"]
        self.scene.set_bond_scale(self.bond_scale)
        self.scene.set_bond_ranges(self.bond_ranges)
        workspace = QWidget(self)
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.tabs = StructureTabs(workspace)
        self.tabs.set_double(settings.get("directory_tabs", True))
        self.tabs.setObjectName("structures")
        self.tabs.setTabsClosable(True)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self.switch_document)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        layout.addWidget(self.tabs)
        self.viewport = QStackedWidget(workspace)
        self.viewport.addWidget(self.scene)
        layout.addWidget(self.viewport, 1)
        self.player = Player(self.scene.plotter.interactor)
        self.player.seek.connect(self.seek_frame)
        self.player.speed_changed.connect(self.set_playback_speed)
        self.player.height_changed.connect(self.place_panels)
        self.setCentralWidget(workspace)
        self.atom_panel = AtomPanel(self.scene.plotter.interactor)
        self.atom_panel.apply_requested.connect(self.edit_atom)
        self.atom_panel.element_requested.connect(self.change_atom_element)
        self.display_panel = DisplayPanel(self.scene.plotter.interactor)
        self.display_panel.periods_changed.connect(self.set_periods)
        self.display_panel.hidden_changed.connect(self.filter_indices)
        self.display_panel.font_size.setValue(settings['index_font'])
        self.scene.labels.set_size(settings['index_font'])
        self.display_panel.font_changed.connect(self.set_index_font)
        self.grid_panel = GridPanel(self.scene.plotter.interactor)
        self.grid_panel.changed.connect(self.set_grid)
        self.grid_panel.layout_changed.connect(self.place_panels)
        self.style_panel = StylePanel(self.scene.plotter.interactor)
        self.style_panel.chosen.connect(self.set_representation)
        self.style_panel.closed.connect(lambda: self.toggle_style(False))
        paths = model_paths()
        paths["mp-0"] = Path(settings.get("mace_path", str(paths["mp-0"])))
        paths.update({name: Path(value) for name, value in settings.get("mace_paths", {}).items()})
        selected_model = settings.get("mace_model", "nep89")
        self.relaxer = RelaxController(paths[selected_model], self, model=selected_model, paths=paths)
        self.relaxer.changed.connect(self.relax_changed)
        self.relaxer.frames_ready.connect(self.relax_frames)
        self.relax_panel = RelaxPanel(self.relaxer, self.scene.plotter.interactor)
        self.relax_panel.start_requested.connect(self.start_relax)
        self.relax_panel.path_changed.connect(self.save_model_path)
        self.relax_panel.export_requested.connect(self.export_result)
        self.relax_panel.view_requested.connect(self.toggle_results)
        self.relax_panel.walls_changed.connect(self.update_walls)
        force_settings = settings.get("forces", {})
        force_paths = paths | {name: Path(value) for name, value in force_settings.get("paths", {}).items()}
        force_model = force_settings.get("model", selected_model)
        self.forcer = ForceController(force_paths[force_model], self, model=force_model, paths=force_paths)
        self.forcer.device = force_settings.get("device", self.forcer.device)
        self.forcer.threshold, self.forcer.scale = force_settings.get("threshold", 1.0), force_settings.get("scale", 1.0)
        self.force_panel = ForcePanel(self.forcer, self.scene.plotter.interactor)
        self.force_panel.preferences_changed.connect(self.save_force_settings)
        self.forcer.changed.connect(self.force_changed)
        self.relax_info = RelaxInfo(self.scene.plotter.interactor)
        self.relax_info.close_requested.connect(lambda: self.toggle_info(False))
        self.data_panel = DataPanel(self.scene.plotter.interactor)
        self.data_panel.frame_requested.connect(self.browse_frame)
        headers = {'md': settings['data_header']} if 'data_header' in settings else {}
        headers.update(settings.get('data_headers', {}))
        self.data_panel.header_states = {kind: QByteArray.fromBase64(value.encode('ascii')) for kind, value in headers.items()}
        if 'md' in self.data_panel.header_states:
            self.data_panel.table.horizontalHeader().restoreState(self.data_panel.header_states['md'])
        self.data_panel.header_timer.stop()
        self.data_panel.header_changed.connect(self.save_data_header)
        self.curve_panel = CurvePanel(self.scene.plotter.interactor)
        self.curve_panel.plot.frame_requested.connect(self.browse_frame)
        self.curve_panel.export_requested.connect(self.export_curve_image)
        self.analysis = AnalysisPanel(self.scene.plotter.interactor)
        self.analysis.add("data", "逐步数据", self.data_panel)
        self.analysis.add("curves", "预平衡曲线", self.curve_panel)
        self.report_panel = ReportPanel(self.scene.plotter.interactor)
        self.report_panel.frame_requested.connect(self.browse_frame)
        self.analysis.add("report", "文件能量与力", self.report_panel)
        self.slice_panel = SlicePanel(self.scene.plotter.interactor)
        self.slice_panel.export_requested.connect(self.export_elf_slice)
        self.analysis.add("elf", "ELF 二维切片", self.slice_panel)
        self.volume_panels = {family: VolumePanel(family, self.scene.plotter.interactor) for family in ("charge", "elf")}
        self.volume_panels["charge"].set_level_step(settings["charge_level_step"])
        self.volume_panels["elf"].slice_requested.connect(self.toggle_elf_slice)
        for panel in self.volume_panels.values():
            panel.changed.connect(self.update_volumes)
        self.analysis.close_requested.connect(self.close_analysis)
        self.analysis.current_changed.connect(self.select_analysis)
        for name, box in self.curve_panel.boxes.items():
            box.setChecked(name in settings.get("curve_channels", self.curve_panel.boxes))
        self.curve_panel.filter_channels()
        self.curve_panel.choices['opt'] = set(settings.get('opt_curve_channels', ('force', 'energy')))
        self.curve_panel.channels_changed.connect(self.save_channels)
        self.overlays = {"display": self.display_panel, "mace": self.relax_panel, "forces": self.force_panel,
                         "grid": self.grid_panel,
                         "analysis": self.analysis, "info": self.relax_info, "style": self.style_panel,
                         **self.volume_panels}
        for name, panel in self.overlays.items():
            if name in settings.get("panels", {}):
                panel.restore(settings["panels"][name])
            panel.geometry_changed.connect(lambda key=name: self.save_panel(key))
        self.setAcceptDrops(True)
        self.register_scene(self.scene)
        self.actions: dict[str, QAction] = {}
        keys = bindings(self.settings.get('shortcuts'))
        self.scene.molecule_modifier = getattr(Qt.KeyboardModifier, keys["molecule_modifier"] + "Modifier")
        definitions = [
            ("new", "新建结构", self.show_new_menu),
            ("new_blank", "纯空白", lambda: self.new_structure("blank")),
            ("new_build", "构建结构", lambda: self.new_structure("build")),
            ("clipboard", "剪贴板", self.show_clipboard),
            ("style", "模型风格", self.toggle_style),
            ("style_cycle", "切换模型风格", self.cycle_representation),
            ("render", "渲染", self.new_render),
            ("render_controls", "显示渲染设置", self.toggle_render_controls),
            ("open", "打开", self.open_file),
            ("save", "导出结构", self.save_as),
            ("undo", "撤销", self.undo),
            ("redo", "重做", self.redo),
            ("all", "全选", self.select_all),
            ("free", "固定约束", self.free_dialog),
            ("fix", "切换所选原子固定/可动", self.fix_atoms),
            ("close", "关闭当前页面", lambda: self.close_tab(self.tabs.currentIndex())),
            ("quit", "退出", self.close),
            ("next", "下一个结构", lambda: self.cycle_document(1)),
            ("previous", "上一个结构", lambda: self.cycle_document(-1)),
            ("reset", "重置视角", lambda: self.scene.reset_camera()),
            ("settings", "设置", self.settings_dialog),
            ("add", "添加原子", self.add_dialog),
            ("quick_add", "添加\n原子", self.quick_add),
            ("add_molecule", "添加\n分子", self.add_molecule),
            ("remove", "移除所选原子", self.remove_atoms),
            ("slab", "切面\n扩胞", lambda: self.build_dialog("slab")),
            ('recell', '改胞', lambda: self.build_dialog('recell')),
            ("wrap", "分子归入晶胞", self.wrap_atoms),
            ("relax", "MLIP 结构优化", self.new_relax),
            ("md", "MLIP 预平衡", self.new_md),
            ("neb", "NEB", self.open_neb),
            ("show_info", "显示计算过程", self.toggle_info),
            ("export_xdatcar", "导出轨迹", self.export_xdatcar),
            ("copy", "复制原子", self.copy_atoms),
            ("paste", "粘贴原子", self.begin_paste),
            ("cancel", "取消粘贴", lambda: self.scene.cancel_paste()),
        ]
        for name, label, callback in definitions:
            action = QAction(label, self)
            action.setShortcut(QKeySequence(keys[name]))
            action.triggered.connect(callback)
            self.actions[name] = action
            self.addAction(action)
        if sys.platform == 'darwin':
            self.actions['remove'].setShortcuts([QKeySequence(keys['remove']), QKeySequence('Delete')])
        self.actions["show_info"].setCheckable(True)
        self.actions["style"].setCheckable(True)
        self.actions["render_controls"].setCheckable(True)
        self.actions["style_cycle"].setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.actions["style_cycle"].setAutoRepeat(False)
        self.scene.plotter.interactor.addAction(self.actions["style_cycle"])
        self.mode_group = QActionGroup(self)
        self.mode_group.setExclusive(True)
        modes = ModeBar(self)
        self.modebar = modes
        modes.setMovable(False)
        modes.setFloatable(False)
        modes.setIconSize(QSize(24, 24))
        modes.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        modes.setStyleSheet("QToolButton { padding: 3px; border: 1px solid transparent; }"
                           "QToolButton:checked { background: #d8eaff; border-color: #80b8ee; }"
                           "QToolButton:hover { border-color: #80b8ee; }"
                           "QToolButton:pressed { background: #c8def5; }")
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, modes)
        for name, (label, hint) in MODES.items():
            action = QAction(QIcon(str(Path(__file__).with_name("icons") / (name + ".svg"))), label, self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(keys[name]))
            hint = hint.replace('Ctrl', 'Command') if sys.platform == 'darwin' else hint
            action.setToolTip(label + (" (" + keys[name] + ")" if keys[name] else "") + "：" + hint)
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.setAutoRepeat(False)
            action.triggered.connect(lambda _checked, mode=name: self.set_mode(mode))
            self.mode_group.addAction(action)
            self.actions[name] = action
            self.addAction(action)
            self.scene.plotter.interactor.addAction(action)
            modes.addAction(action)
        self.actions["style"].setIcon(QIcon(str(Path(__file__).with_name("icons") / "style_ball.svg")))
        modes.addAction(self.actions["style"])
        modes.addSeparator()
        contacts = QAction(QIcon(str(Path(__file__).with_name("icons") / "contacts.svg")), "检测原子距离", self)
        contacts.setCheckable(True)
        contacts.setToolTip("检测过近原子并显示橙色标记；阈值可在设置中调整")
        contacts.toggled.connect(lambda enabled: self.scene.set_contacts(enabled))
        self.actions["show_contacts"] = contacts
        self.addAction(contacts)
        modes.addAction(contacts)
        forces = QAction(QIcon(str(Path(__file__).with_name("icons") / "forces.svg")), "显示原子受力", self)
        forces.setCheckable(True)
        forces.setToolTip("显示原子受力")
        forces.triggered.connect(self.show_force_settings)
        self.actions["show_forces"] = forces
        self.addAction(forces)
        fixed = QAction(QIcon(str(Path(__file__).with_name("icons") / "fixed.svg")), "显示固定原子", self)
        fixed.setCheckable(True)
        fixed.setShortcut(QKeySequence(keys["show_fixed"]))
        fixed.setToolTip("显示固定原子")
        fixed.toggled.connect(lambda enabled: self.scene.set_fixed_visible(enabled))
        self.actions["show_fixed"] = fixed
        self.addAction(fixed)
        modes.addAction(fixed)
        cell_info = QAction(QIcon(str(Path(__file__).with_name('icons') / 'cell.svg')), '显示晶胞参数', self)
        cell_info.setCheckable(True)
        cell_info.toggled.connect(self.toggle_cell_info)
        self.actions['show_cell_info'] = cell_info
        self.addAction(cell_info)
        modes.addAction(cell_info)
        grid = QAction(QIcon(str(Path(__file__).with_name('icons') / 'grid.svg')), '显示轴/网格', self)
        grid.setCheckable(True)
        grid.toggled.connect(self.toggle_grid)
        self.actions['show_grid'] = grid
        self.addAction(grid)
        modes.addAction(grid)
        for name, label, icon, callback in (
                ("show_periodic", "显示周期性", "periodic", self.toggle_periodic),
                ("show_indices", "显示原子序号", "indices", self.toggle_indices),
                ("show_player", "显示播放轴", "player", self.toggle_player)):
            action = QAction(QIcon(str(Path(__file__).with_name("icons") / (icon + ".svg"))), label, self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(keys[name]))
            action.toggled.connect(callback)
            self.actions[name] = action
            self.addAction(action)
            modes.addAction(action)
        modes.addSeparator()
        for name, title, icon in (("show_report", "文件能量与力", "report"),
                                  ("show_charge", "CHGCAR / 电子数据", "charge"),
                                  ("bader", "Bader 电荷", "bader"),
                                  ("moments", "磁矩", "moment"),
                                  ("show_elf", "ELFCAR", "elf")):
            action = QAction(QIcon(str(Path(__file__).with_name("icons") / (icon + ".svg"))), title, self)
            action.setCheckable(True)
            callback = {"bader": self.show_bader, "moments": self.show_moments, "show_report": self.toggle_report}.get(name)
            action.triggered.connect(callback if callback is not None else lambda _checked, family=icon: self.show_volume(family))
            self.actions[name] = action
            action.setEnabled(False)
            modes.addAction(action)
        self.actions["neb"].setIcon(QIcon(str(Path(__file__).with_name("icons") / "neb.svg")))
        self.actions["neb"].setToolTip("NEB：扫描图像目录，叠加查看路径、优化帧及原始能量/力")
        modes.addAction(self.actions["neb"])
        modes.addSeparator()
        modes.addAction(forces)
        for name in ("relax", "md", "render"):
            self.actions[name].setIcon(QIcon(str(Path(__file__).with_name("icons") / (name + ".svg"))))
            modes.addAction(self.actions[name])
        for action in modes.actions():
            if not action.isSeparator():
                modes.widgetForAction(action).setFixedSize(34, 34)
        modes.configure(self.actions, self.settings.get("modebar", {}))
        modes.preferences_changed.connect(self.save_modebar)
        for name, direction in ARROWS.items():
            action = QAction(self)
            action.setShortcut(QKeySequence(keys[name]))
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(lambda _checked, vector=direction: self.nudge(*vector))
            self.actions[name] = action
            self.addAction(action)
            self.scene.plotter.interactor.addAction(action)
        self.actions["rotate"].setChecked(True)
        self.new_menu = QMenu("新建结构", self)
        self.new_menu.addAction(self.actions["new_blank"])
        self.new_menu.addAction(self.actions["new_build"])
        self.actions["new"].setMenu(self.new_menu)
        for title, names in (("文件", ("new", "open", "save", "export_xdatcar", "close", "quit")),
                             ("编辑", ("undo", "redo", "copy", "paste", "all", "free", "fix", "add", "remove", "wrap")),
                             ("视图", ("reset", "style", "style_cycle", "render_controls", "show_contacts", "show_forces", "show_fixed", "show_cell_info", "show_grid", "show_periodic", "show_indices", "show_player", "show_info", "show_report", "show_charge", "bader", "moments", "show_elf", "next", "previous"))):
            menu = self.menuBar().addMenu(title)
            for name in names:
                menu.addAction(self.actions[name])
        menu = self.menuBar().addMenu("模式")
        for name in MODES:
            menu.addAction(self.actions[name])
        menu = self.menuBar().addMenu("设置")
        for index, title in enumerate(SETTINGS_GROUPS):
            menu.addAction(title, lambda _checked=False, page=index: self.settings_dialog(page))
        toolbar = QToolBar("视角", self)
        toolbar.setObjectName("view_controls")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setStyleSheet("QToolButton { padding: 3px 9px; }")
        self.addToolBar(toolbar)
        self.history_buttons, self.history_menus = {}, {}
        for name in ("undo", "redo"):
            self.actions[name].setIcon(QIcon(str(Path(__file__).with_name("icons") / (name + ".svg"))))
            button = QToolButton(toolbar)
            button.setObjectName(name + "_history")
            button.setDefaultAction(self.actions[name])
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            button.setFixedSize(44, 30)
            button.setStyleSheet("QToolButton { padding: 0px; }")
            menu = HistoryMenu(button)
            menu.aboutToShow.connect(lambda key=name: self.populate_history_menu(key))
            button.setMenu(menu)
            self.history_buttons[name], self.history_menus[name] = button, menu
            toolbar.addWidget(button)
        toolbar.addSeparator()
        for axis, label in enumerate("abc"):
            action = QAction(label, self)
            action.setToolTip("沿晶胞 " + label + " 方向观察；重复点击切换正/负方向")
            action.triggered.connect(lambda _checked, index=axis: self.view_axis(index))
            self.actions["view_" + label] = action
            toolbar.addAction(action)
            button = toolbar.widgetForAction(action)
            button.setFixedSize(30, 30)
            font = button.font()
            font.setPointSize(font.pointSize() + 3)
            button.setFont(font)
            button.setStyleSheet("QToolButton { padding: 0px; }")
        toolbar.addSeparator()
        for name, (label, motion, sign) in ROTATIONS.items():
            action = QAction(QIcon(str(Path(__file__).with_name("icons") / ("turn_" + name + ".svg"))), label, self)
            action.triggered.connect(lambda _checked, operation=motion, direction=sign: self.turn(operation, direction))
            self.actions["turn_" + name] = action
            toolbar.addAction(action)
            toolbar.widgetForAction(action).setFixedSize(30, 30)
            toolbar.widgetForAction(action).setStyleSheet("QToolButton { padding: 0px; }")
        toolbar.addSeparator()
        self.actions["slab"].setToolTip("切面扩胞")
        toolbar.addAction(self.actions["slab"])
        toolbar.widgetForAction(self.actions["slab"]).setFixedSize(30, 30)
        toolbar.widgetForAction(self.actions["slab"]).setStyleSheet("QToolButton { padding: 0px; font-size: 10px; }")
        self.actions["quick_add"].setToolTip("添加一个原子；Esc 取消放置")
        toolbar.addAction(self.actions["quick_add"])
        button = toolbar.widgetForAction(self.actions["quick_add"])
        button.setText("添加\n原子")
        button.setFixedSize(30, 30)
        button.setStyleSheet("QToolButton { padding: 0px; font-size: 10px; }")
        toolbar.addAction(self.actions["add_molecule"])
        button = toolbar.widgetForAction(self.actions["add_molecule"])
        button.setFixedSize(30, 30)
        button.setStyleSheet("QToolButton { padding: 0px; font-size: 10px; }")
        self.actions["add_molecule"].setToolTip("添加常见小分子、水层或指定数量的水")
        toolbar.addAction(self.actions['recell'])
        toolbar.widgetForAction(self.actions['recell']).setFixedSize(30, 30)
        toolbar.widgetForAction(self.actions['recell']).setStyleSheet('QToolButton { padding: 0px; font-size: 11px; }')
        self.actions["clipboard"].setIcon(QIcon(str(Path(__file__).with_name("icons") / "clipboard.svg")))
        toolbar.addAction(self.actions["clipboard"])
        toolbar.widgetForAction(self.actions["clipboard"]).setFixedSize(30, 30)
        toolbar.widgetForAction(self.actions["clipboard"]).setStyleSheet("QToolButton { padding: 0px; }")
        self.actions["wrap"].setIcon(QIcon(str(Path(__file__).with_name("icons") / "wrap.svg")))
        toolbar.addAction(self.actions["wrap"])
        button = toolbar.widgetForAction(self.actions["wrap"])
        button.setFixedSize(30, 30)
        button.setStyleSheet("QToolButton { padding: 0px; }")
        self.actions["wrap"].setToolTip("归入晶胞：将所有原子周期进晶胞")
        for name, title, icon, callback in (("pages", "分页窗口", "pages", self.show_pages),
                                          ("directory_tabs", "双行目录标签", "tabs", self.toggle_directory_tabs)):
            action = QAction(QIcon(str(Path(__file__).with_name("icons") / (icon + ".svg"))), title, self)
            action.setCheckable(name == "directory_tabs")
            action.setChecked(self.tabs.double if name == "directory_tabs" else False)
            action.triggered.connect(callback)
            self.actions[name] = action
            toolbar.addAction(action)
            toolbar.widgetForAction(action).setFixedSize(30, 30)
            if name == "pages":
                self.pages_button = toolbar.widgetForAction(action)
                self.pages_menu = None
        action = QAction(QIcon(str(Path(__file__).with_name("icons") / "palette.svg")), "显示风格", self)
        action.triggered.connect(self.show_view_styles)
        self.actions["appearance"] = action
        toolbar.addAction(action)
        self.view_button = toolbar.widgetForAction(action)
        self.view_button.setFixedSize(30, 30)
        self.clipboard.changed.connect(self.clipboard_changed)
        self.apply_shortcuts()
        self.update_rotation_hints()
        self.refresh()
        if parent is None and "geometry" in settings:
            self.restoreGeometry(QByteArray.fromBase64(settings["geometry"].encode("ascii")))
        print(f'Startup: build workspace {perf_counter() - started:.3f}s', flush=True)

    def show_new_menu(self):
        self.new_menu.popup(QCursor.pos())

    def show_pages(self, _checked=False):
        from .compare import LAYOUTS
        if self.pages_menu is None:
            self.pages_menu = QMenu(self)
            for text, shape in LAYOUTS.items():
                action = self.pages_menu.addAction(text)
                action.setData(shape)
                action.triggered.connect(lambda _checked=False, layout=shape: self.new_comparison(layout))
        self.pages_menu.popup(self.pages_button.mapToGlobal(self.pages_button.rect().bottomLeft()))

    def tab_for(self, page):
        return next(index for index in range(self.tabs.count()) if self.tabs.tabData(index) is page)

    def add_session_tab(self):
        session = self.sessions[-1]
        with QSignalBlocker(self.tabs):
            tab = self.tabs.addTab(session.path.name)
            self.tabs.setTabData(tab, session)
            self.tabs.setCurrentIndex(tab)
        return len(self.sessions)-1

    def new_comparison(self, shape):
        from .compare import Comparison
        self._store_current()
        source = self.sessions[self.current_index] if self.current_index >= 0 else None
        self.compare_number += 1
        page = Comparison(self, shape, self.compare_number)
        self.comparisons.append(page)
        self.viewport.addWidget(page)
        with QSignalBlocker(self.tabs):
            index = self.tabs.addTab("对比 " + str(page.number))
            self.tabs.setTabData(index, page)
            self.tabs.setCurrentIndex(index)
        if source is None:
            self.activate_cell(page.active)
        else:
            page.active.assign(source)
        return page

    def register_scene(self, scene):
        scene.session = None
        scene.axis_marker.anchor = self.settings.get('axis_position', [0., 1.]).copy()
        scene.axis_marker.place()
        scene.axis_moved.connect(self.save_axis_position)
        if scene is not self.single_scene:
            for name in (*MODES, *ARROWS, "style_cycle"):
                scene.plotter.interactor.addAction(self.actions[name])
        scene.contact_factor = self.settings["contact_factor"]
        scene.show_cell_boundary = self.settings['show_cell_boundary']
        scene.set_orientation_axes(self.settings['show_orientation_axes'])
        scene.bond_scale, scene.bond_ranges = self.bond_scale, self.bond_ranges
        scene.set_bond_radius(self.settings["bond_radius"])
        scene.labels.set_size(self.settings["index_font"])
        from .label_settings import profiles
        scene.configure_labels(profiles(self.settings))
        scene.ghost.configure(self.settings["move_ghost"], self.settings["ghost_opacity"], self.settings["move_decimals"])
        scene.set_measure_decimals(self.settings["measure_decimals"])
        keys = bindings(self.settings.get('shortcuts'))
        scene.molecule_modifier = getattr(Qt.KeyboardModifier, keys["molecule_modifier"] + "Modifier")
        for signal, callback in ((scene.selection_changed, self._status), (scene.move_requested, self.move_atoms),
                                 (scene.measurement_changed, self._status), (scene.paste_requested, self.paste_atoms),
                                 (scene.paste_changed, self._status), (scene.geometry_changed, self.request_forces),
                                 (scene.geometry_changed, self.update_bader), (scene.geometry_changed, self.update_moments)):
            signal.connect(lambda *args, owner=scene, call=callback: call(*args) if self.scene is owner else None)
        scene.plotter.interactor.setAcceptDrops(True)
        scene.plotter.interactor.installEventFilter(self)

    def change_scene(self, scene):
        if self.scene is scene:
            return
        self.scene.axis_marker.place()
        self.scene = scene
        for panel in (*self.overlays.values(), self.atom_panel, self.player):
            visible = not panel.isHidden()
            panel.setParent(scene.plotter.interactor)
            panel.setVisible(visible)
        if self.neb_workspace is not None:
            self.neb_workspace.set_scene(scene)

    def activate_cell(self, cell, *, store=True):
        if self.builder is not None or self.closing:
            return
        if self.comparison is cell.page and cell.page.active is cell and (
                self.scene is cell.scene and self.current_index >= 0 and self.sessions[self.current_index] is cell.source
                or cell.source is None and self.document is None):
            return
        if store:
            self._store_current()
        self.comparison = cell.page
        cell.page.active = cell
        self.viewport.setCurrentWidget(cell.page)
        with QSignalBlocker(self.tabs):
            self.tabs.setCurrentIndex(self.tab_for(cell.page))
        if cell.source is None:
            self.change_scene(self.single_scene)
            self.clear_document()
        else:
            camera = cell.scene.camera_snapshot()
            self.change_scene(cell.scene)
            self._activate_session(self.sessions.index(cell.source), camera=camera)
        cell.page.sync()

    def activate_document(self, index):
        self.comparison = None
        self.viewport.setCurrentWidget(self.single_scene)
        camera = self.single_scene.camera_snapshot() if self.single_scene.session is self.sessions[index] else None
        self.change_scene(self.single_scene)
        self._activate_session(index, camera=camera)

    def clear_document(self):
        self.current_index = -1
        self.document, self.path, self.history = None, None, History()
        self.scene.session = None
        self.player.hide()
        if self.neb_workspace is not None:
            self.neb_workspace.view.hide()
        self.scene.clear_structure()
        self.scene.set_look("ball", rebuild=False)
        self.scene.plotter.set_background("white")
        self.refresh()

    def finish_import(self, session, target=None):
        self._store_current()
        self.sessions.append(session)
        index = self.add_session_tab()
        if target is not None and target.page in self.comparisons:
            target.assign(session)
        else:
            self.activate_document(index)

    def toggle_directory_tabs(self, checked):
        self.settings["directory_tabs"] = checked
        self.tabs.set_double(checked)
        self.refresh()
        self.persist_settings()

    def new_structure(self, choice):
        if not self.feature_ready("vbuild.create_dialog", lambda: self.new_structure(choice)):
            return
        from .create_dialog import BlankDialog, ConstructDialog
        key_error = ''
        if choice == 'build' and self.mp_key is None:
            try:
                self.mp_key = read_mp_key()
            except OSError as error:
                self.mp_key, key_error = '', str(error)
        dialog = BlankDialog(self) if choice == "blank" else ConstructDialog(self.mp_key, self)
        if choice == "build":
            dialog.materials.key_changed.connect(self.set_mp_key)
            if key_error:
                dialog.materials.status.setText('API key 无法读取：' + key_error)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if choice == "build" and dialog.content is not None:
            target = self.downloads_path / (dialog.material_id + "_" + uuid.uuid4().hex[:8])
            path = target / "POSCAR"
            try:
                target.mkdir(parents=True)
                path.write_bytes(dialog.content)
            except OSError as error:
                QMessageBox.critical(self, "下载结构无法保存", str(error))
                return
            self.open_path(path)
        else:
            self.add_draft(dialog.document, "空白" if choice == "blank" else ("石墨烯" if dialog.kind == "graphene" else "碳纳米管"))

    def set_mp_key(self, key):
        self.mp_key = key

    def add_draft(self, document, name):
        self._store_current()
        suffix = ".xyz" if document.structure.cell is None else ".vasp"
        path = data_dir() / "out" / (name + "_" + str(len(self.sessions) + 1) + suffix)
        self.sessions.append(Session(document, path, mode="edit"))
        self.activate_document(self.add_session_tab())

    def quick_add(self):
        dialog = PeriodicTable(self.quick_symbol, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.set_mode("edit")
        center = np.array(self.scene.plotter.camera.focal_point)
        self.quick_symbol = dialog.symbol
        symbols, positions = (dialog.symbol,), center[None, :]
        self.paste_selection = self.scene.selection.copy()
        self.paste_index = None
        self.scene.start_paste(symbols, positions)
        self.scene.plotter.interactor.setFocus()

    def add_molecule(self):
        if not self.feature_ready("vbuild.molecules", self.add_molecule):
            return
        from .molecules import MoleculeDialog
        dialog = MoleculeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.choice == "smiles":
            from .smiles_dialog import SmilesDialog
            preview = SmilesDialog(self)
            if preview.exec() == QDialog.DialogCode.Accepted:
                self.set_mode("edit")
                self.clipboard.copy(preview.prepared.symbols, preview.prepared.positions)
                self.scene.plotter.interactor.setFocus()
            return
        if dialog.choice in ("layer", "count"):
            self.water_dialog(dialog.choice)
            return
        self.set_mode("edit")
        symbols, relative = molecule_group(dialog.choice)
        self.paste_selection = self.scene.selection.copy()
        self.paste_index = None
        self.scene.start_paste(symbols, relative + self.scene.plotter.camera.focal_point)
        self.scene.plotter.interactor.setFocus()

    def water_dialog(self, kind):
        if not self.feature_ready("vbuild.molecules", lambda: self.water_dialog(kind)):
            return
        from .molecules import WaterDialog
        self.scene.cancel_gesture()
        self.scene.cancel_paste()
        structure = self.document.structure
        dialog = WaterDialog(kind, structure, self)
        self.builder = (dialog, structure, self.scene.camera_snapshot(), self.scene.selection.copy(), self.scene.mode)
        self.scene.previewing = True
        self.scene.set_mode("rotate")
        self.actions["rotate"].setChecked(True)
        self.tabs.setEnabled(False)

        def bounds(region):
            self.scene.water_walls.update(region)
            self.scene.plotter.render()

        def preview(result, indices):
            self.scene.selection = indices
            self.scene.set_structure(result)
            self.scene.plotter.reset_camera_clipping_range()
            self._status()

        def invalidate():
            self.scene.selection = []
            self.scene.set_structure(structure)
            self._status()

        dialog.range_requested.connect(bounds)
        dialog.preview_requested.connect(preview)
        dialog.invalidated.connect(invalidate)
        dialog.finished.connect(self.finish_water)
        dialog.setModal(False)
        dialog.show()
        self.refresh()

    def finish_water(self, code):
        dialog, structure, camera, selection, mode = self.builder
        self.builder = None
        self.scene.previewing = False
        self.actions[mode].setChecked(True)
        self.tabs.setEnabled(True)
        self.scene.water_walls.update(None)
        if code == QDialog.DialogCode.Accepted:
            indices = self.history.apply(self.document, lambda: structure.add_group(*dialog.prepared),
                                         selection=selection, selection_after=lambda result: result,
                                         label="添加水层" if dialog.kind == "layer" else "添加水分子")
            self.scene.selection = indices
        else:
            self.scene.selection = selection
        self.scene.set_structure(structure)
        self.scene.set_mode(mode)
        self.scene.restore_camera(camera)
        dialog.deleteLater()
        self.refresh()
        self.scene.plotter.interactor.setFocus()

        if self.waiting_water_close:
            self.waiting_water_close = False
            QTimer.singleShot(0, self.close)

    def load(self, path: str | Path, *, lammps_options=None, target=None):
        path = Path(path).resolve()
        if path.suffix.lower() == ".vscene":
            self.load_render_scene(path, target=target)
            return
        kind = input_kind(path)
        if kind not in ("vasp", "xyz", "xdatcar"):
            options = {} if lammps_options is None else lammps_options
            if kind in ("lammps-data", "dump") and lammps_options is None:
                from .lammps import probe
                dialog = LammpsDialog(probe(path, kind == "dump"), self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                options = dialog.options()
            task = ImportThread(path, kind, options, self)
            self.loaders[task] = task
            task.ready.connect(lambda loaded: self.import_ready(path, loaded, target=target))
            task.failed.connect(lambda error: self.import_failed(path, error))
            task.finished.connect(lambda: self.index_finished(task))
            self.statusBar().showMessage("正在导入 " + path.name + "…")
            task.start()
            return
        is_trajectory = kind != "vasp"
        if is_trajectory:
            reader, document = Trajectory.begin(path, kind)
            session = Session(document, path, trajectory=reader, show_player=True, indexing=True)
        else:
            document = PoscarDocument.read(path)
            session = Session(document, path)
        self.finish_import(session, target)
        if is_trajectory:
            self.start_index(session)

    def start_index(self, session):
        task = IndexThread(session.trajectory, self)
        self.loaders[session] = task
        task.ready.connect(lambda indexed: self.index_ready(session, indexed))
        task.failed.connect(lambda error: self.index_failed(session, error))
        task.progress.connect(lambda count: self.index_progress(session, count))
        task.finished.connect(lambda: self.index_finished(session))
        task.start()

    def import_failed(self, path, error):
        if not self.closing:
            QMessageBox.critical(self, "文件无法打开", path.name + "\n" + error)
            self._status()

    def import_ready(self, path, loaded, *, target=None):
        if self.closing:
            return
        volume = loaded.volume
        for number, document in enumerate(loaded.documents):
            self._store_current()
            source = path if number == 0 else path.with_name(path.stem + f"_block{number + 1}" + path.suffix)
            session = Session(document, source, trajectory=loaded.reader, show_player=loaded.reader is not None,
                              indexing=loaded.reader is not None, readonly=volume is not None or loaded.readonly)
            if volume is not None:
                self.attach_volume(session, volume)
            self.finish_import(session, target if number == 0 else None)
            if loaded.reader is not None:
                self.start_index(session)
        if target is not None and target.page in self.comparisons:
            target.activate()
        if loaded.notice:
            self.statusBar().showMessage(loaded.notice, 8000)

    def show_bader(self, _checked=False):
        if not self.feature_ready("vbuild.bader_panel", self.show_bader):
            return
        from .bader import read_bader
        from .bader_panel import BaderPanel, BaderTable
        session = self.sessions[self.current_index]
        try:
            data = read_bader(session.path.parent, session.document.structure)
        except (ValueError, OSError) as error:
            with QSignalBlocker(self.actions["bader"]):
                self.actions["bader"].setChecked(session.show_bader)
            QMessageBox.information(self, "Bader 电荷", str(error))
            return
        if session.bader is not None:
            data.mode, data.decimals = session.bader.mode, session.bader.decimals
            data.hidden_elements = session.bader.hidden_elements.copy()
        session.bader = data
        if self.bader_panel is None:
            self.bader_panel = BaderPanel(self.scene.plotter.interactor)
            self.bader_table = BaderTable(self.scene.plotter.interactor)
            self.bader_panel.changed.connect(self.edit_bader)
            self.bader_panel.table_requested.connect(self.toggle_bader_data)
            self.overlays["bader"] = self.bader_panel
            if "bader" in self.settings.get("panels", {}):
                self.bader_panel.restore(self.settings["panels"]["bader"])
            self.bader_panel.geometry_changed.connect(lambda: self.save_panel("bader"))
            self.analysis.add("bader", "Bader 原始数据与实际电荷", self.bader_table)
        self.update_bader()
        self.bader_panel.show()
        self.bader_panel.raise_()
        self.place_panels()

    def edit_bader(self):
        session = self.sessions[self.current_index]
        session.bader.mode = self.bader_panel.mode.currentData()
        session.bader.decimals = self.bader_panel.decimals.value()
        session.show_bader = self.bader_panel.toggle.isChecked()
        session.bader.hidden_elements = self.bader_panel.elements.hidden.copy()
        self.update_bader()
        self.populate_results()
        self.place_panels()
        if self.comparison is not None:
            self._store_current(interrupt=False)
            self.comparison.sync()

    def update_bader(self):
        session = self.sessions[self.current_index] if self.document is not None else None
        data = None if session is None else session.bader
        valid = data is not None and data.matches(session.document.structure)
        shown = data is not None and session.show_bader
        self.scene.set_charges(data.labels() if shown and valid else None)
        if "bader" in self.actions:
            self.actions["bader"].setEnabled(session is not None and bool(session.document.structure.symbols) and self.builder is None)
            with QSignalBlocker(self.actions["bader"]):
                self.actions["bader"].setChecked(bool(shown and valid and data.charges() is not None))
        if self.bader_panel is not None:
            if data is None:
                self.bader_panel.hide()
            else:
                self.bader_panel.populate(data, shown, session.show_bader_data, valid)
                self.bader_table.populate(data, valid)

    def toggle_bader_data(self, checked):
        session = self.sessions[self.current_index]
        session.show_bader_data = checked
        if checked:
            session.analysis_tab = "bader"
        self.update_bader()
        self.populate_results()
        self.place_panels()
        if checked:
            self.analysis.raise_()

    def show_moments(self, _checked=False):
        if not self.feature_ready("vbuild.moment_panel", self.show_moments):
            return
        from .moment_panel import MomentPanel, MomentTable
        if self.moment_panel is None:
            self.moment_panel = MomentPanel(self.scene.plotter.interactor)
            self.moment_table = MomentTable(self.scene.plotter.interactor)
            self.moment_panel.changed.connect(self.edit_moments)
            self.moment_panel.reload_requested.connect(self.load_moments)
            self.moment_panel.table_requested.connect(self.toggle_moments_data)
            self.analysis.add("moments", "磁矩原始数据", self.moment_table)
            self.overlays["moments"] = self.moment_panel
            if "moments" in self.settings.get("panels", {}):
                self.moment_panel.restore(self.settings["panels"]["moments"])
            self.moment_panel.geometry_changed.connect(lambda: self.save_panel("moments"))
        session = self.sessions[self.current_index]
        if session.moments is None and not session.moments_loading:
            self.load_moments()
        self.update_moments()
        self.moment_panel.show()
        self.moment_panel.raise_()
        self.place_panels()

    def load_moments(self):
        from .moments import MomentTask
        session = self.sessions[self.current_index]
        source = session.path if session.path.name.lower().split(".", 1)[0] == "outcar" else session.path.parent
        task = MomentTask(source, self)
        self.loaders[task] = task
        session.moments_loading = True
        task.ready.connect(lambda data: self.moments_ready(session, data))
        task.failed.connect(lambda error: self.moments_failed(session, error))
        task.finished.connect(lambda: self.index_finished(task))
        task.start()
        self.update_moments()

    def moments_ready(self, session, data):
        session.moments_loading = False
        if session not in self.sessions or self.closing:
            return
        if session.moments is not None:
            data.decimals, data.hidden_elements = session.moments.decimals, session.moments.hidden_elements.copy()
            if session.moments.orbital in data.orbital_names:
                data.orbital = session.moments.orbital
            if data.moments.shape[1] == session.moments.moments.shape[1]:
                data.component = session.moments.component
        session.moments = data
        if self.current_index >= 0 and self.sessions[self.current_index] is session:
            self.update_moments()
            self.place_panels()
        self._store_current(interrupt=False)
        for page in self.comparisons:
            page.sync()

    def moments_failed(self, session, error):
        session.moments_loading = False
        if not self.closing and session in self.sessions:
            self.update_moments()
            QMessageBox.information(self, "磁矩", session.path.parent.name + "\n" + error)

    def edit_moments(self):
        session = self.sessions[self.current_index]
        data = session.moments
        data.orbital = self.moment_panel.orbital.currentData()
        data.component = self.moment_panel.component.currentData()
        data.decimals = self.moment_panel.decimals.value()
        data.hidden_elements = self.moment_panel.elements.hidden.copy()
        session.show_moments = self.moment_panel.toggle.isChecked()
        self.update_moments()
        if self.comparison is not None:
            self._store_current(interrupt=False)
            self.comparison.sync()

    def update_moments(self):
        session = self.sessions[self.current_index] if self.document is not None else None
        data = None if session is None else session.moments
        valid = data is not None and data.matches(session.document.structure)
        shown = data is not None and session.show_moments
        summary = self.scene.moment_summary
        previous = (summary.isHidden(), summary.height())
        self.scene.set_moment_data(data if shown and valid else None)
        action = self.actions["moments"]
        action.setEnabled(session is not None and bool(session.document.structure.symbols) and self.builder is None)
        with QSignalBlocker(action):
            action.setChecked(bool(shown and valid))
        if self.moment_panel is not None:
            if session is None:
                self.moment_panel.hide()
            else:
                self.moment_panel.populate(data, shown, valid, session.moments_loading, session.show_moments_data)
                if data is not None and session.show_moments_data:
                    self.moment_table.populate(data, valid)
        if previous != (summary.isHidden(), summary.height()):
            self.place_panels()

    def toggle_moments_data(self, checked):
        session = self.sessions[self.current_index]
        session.show_moments_data = checked
        if checked:
            session.analysis_tab = "moments"
        self.populate_results()
        self.place_panels()
        if checked:
            self.analysis.raise_()

    def attach_volume(self, session, volume):
        family = volume.family
        if family not in session.volumes:
            session.volumes[family] = []
            session.volume_options[family] = volume_defaults(volume)
        sources = session.volumes[family]
        index = next((index for index, old in enumerate(sources) if old.path == volume.path), None)
        if index is None:
            sources.append(volume)
        else:
            sources[index] = volume
            if session.volume_options[family]["source"] == index:
                enabled = session.volume_options[family]["enabled"]
                session.volume_options[family] = volume_defaults(volume) | {"source": index, "enabled": enabled}

    def show_volume(self, family):
        self.update_volumes()
        self.volume_panels[family].show()
        self.volume_panels[family].raise_()
        self.place_panels()

    def update_volumes(self):
        session = self.sessions[self.current_index] if self.document is not None else None
        for family, panel in self.volume_panels.items():
            sources = [] if session is None else session.volumes.get(family, [])
            self.actions["show_" + family].setEnabled(bool(sources))
            options = None if not sources else session.volume_options[family]
            with QSignalBlocker(self.actions["show_" + family]):
                self.actions["show_" + family].setChecked(bool(sources) and options["enabled"])
            if sources:
                panel.populate(sources, options)
            else:
                panel.clear()
            self.scene.volume_views[family].update(None if not sources else sources[options["source"]], options)
        self.populate_results()
        for family, view in self.scene.volume_views.items():
            if view.actor is None and (family != "elf" or session is None or not session.show_elf_slice):
                view.grid, view.key = None, None
        self.place_panels()
        self.scene.plotter.render()

    def toggle_elf_slice(self, checked):
        if checked and not self.feature_ready("matplotlib.backends.backend_qtagg", lambda: self.toggle_elf_slice(True)):
            self.volume_panels["elf"].set_slice_visible(False)
            return
        session = self.sessions[self.current_index]
        session.show_elf_slice = checked
        if checked:
            session.volume_options["elf"]["mode"] = "slice"
            session.analysis_tab = "elf"
        self.update_volumes()
        if checked:
            self.analysis.raise_()

    def export_curve_image(self):
        path = self.path.with_name(self.path.stem + '_curves.png')
        name, _filter = QFileDialog.getSaveFileName(self, '导出预平衡曲线', str(path), 'PNG 图片 (*.png)')
        if name:
            try:
                self.write_content(Path(name), self.curve_panel.plot.image_bytes(), overwrite=True)
            except (ValueError, OSError) as error:
                QMessageBox.critical(self, '图片无法保存', str(error))

    def export_elf_slice(self):
        name, _filter = QFileDialog.getSaveFileName(self, "导出 ELF 二维切片", str(data_dir() / "out" / "ELF_slice.png"), "PNG 图片 (*.png)")
        if name:
            try:
                self.write_content(Path(name), self.slice_panel.image_bytes(), overwrite=True)
            except (OSError, ValueError) as error:
                QMessageBox.critical(self, "图片导出失败", str(error))

    def toggle_report(self, checked):
        session = self.sessions[self.current_index]
        session.show_report = checked
        if checked:
            session.analysis_tab = "report"
        self.populate_results()
        self.place_panels()
        if checked:
            self.analysis.raise_()

    def _store_current(self, *, interrupt=True):
        if interrupt:
            self.player.pause()
        if self.comparison is not None:
            self.comparison.flush_view()
        if self.current_index < 0:
            return
        if interrupt:
            self.scene.cancel_gesture()
            self.scene.cancel_paste()
        session = self.sessions[self.current_index]
        session.document, session.path, session.history = self.document, self.path, self.history
        session.selection = self.scene.selection.copy()
        session.mode = self.scene.mode
        session.measurement = self.scene.measurement_snapshot()
        session.camera = self.scene.camera_snapshot()
        session.show_fixed = self.scene.show_fixed
        session.show_cell_info = self.scene.show_cell_info
        session.grid = deepcopy(self.scene.grid_options)
        session.show_grid_panel = self.grid_panel.isVisible()
        session.show_contacts = self.scene.show_contacts
        session.show_forces = self.forcer.enabled
        session.show_periodic, session.periods = self.scene.show_periodic, self.scene.periods
        session.show_indices = self.scene.show_indices
        session.hidden_elements = self.scene.hidden_elements.copy()
        session.representation = self.scene.visual.kind

    def _activate_session(self, index: int, *, camera=None):
        if self.bader_panel is not None:
            self.bader_panel.hide()
        if self.moment_panel is not None:
            self.moment_panel.hide()
        if self.neb_workspace is not None:
            self.neb_workspace.view.hide()
        self.current_index = index
        session = self.sessions[index]
        self.scene.session = session
        camera = session.camera if camera is None else camera
        for view in self.scene.volume_views.values():
            view.hide()
        self.document, self.path, self.history = session.document, session.path, session.history
        self.forcer.enabled = session.show_forces
        self.scene.labels.set_size(session.render_font or self.settings["index_font"])
        self.sync_render_atoms(session)
        view_style = self.view_profile(session)
        self.scene.bond_visibility = session.show_bonds
        if session.render_style is not None and session.show_bonds is not None:
            session.render_style["bonds"]["show"] = session.show_bonds
        self.scene.background = "#d5d7da" if session.relaxation is not None else None
        self.scene.set_look(session.representation, session.render_style, session.render_style is not None, rebuild=False,
                            overrides=session.render_atoms, view_style=view_style)
        self.scene.selection = session.selection.copy()
        if self.render_panel is not None and session.render_style is not None:
            self.render_panel.populate(session.render_style, self.style_library, self.document.structure.symbols)
        self.actions[session.mode].setChecked(True)
        self.scene.show_fixed = session.show_fixed
        self.scene.show_cell_info = session.show_cell_info
        self.scene.grid_options = grid_defaults() | deepcopy(self.settings.get('grid_defaults', {})) | deepcopy(session.grid)
        self.scene.show_contacts = session.show_contacts
        self.scene.show_periodic, self.scene.periods = session.show_periodic, session.periods
        self.scene.show_indices, self.scene.hidden_elements = session.show_indices, session.hidden_elements.copy()
        for name in ("show_contacts", "show_fixed", "show_cell_info", "show_periodic", "show_indices", "show_player"):
            with QSignalBlocker(self.actions[name]):
                self.actions[name].setChecked(getattr(session, name))
        self.scene.set_structure(self.document.structure, reset=camera is None, preserve_measurement=False)
        session.grid = deepcopy(self.scene.grid_options)
        self.grid_panel.setVisible(session.show_grid_panel)
        self.scene.set_mode(session.mode)
        self.scene.restore_measurement(session.measurement)
        self.update_style_controls()
        if self.render_panel is not None:
            self.render_panel.setVisible(session.render_style is not None and session.show_render)
        if camera is not None:
            self.scene.restore_camera(camera)
        self.scene.plotter.interactor.setFocus()
        self.refresh()
        self.configure_player()
        self.force_changed()
        self.update_volumes()

    def show_force_settings(self, _checked=False):
        self.force_changed()
        self.force_panel.show()
        self.force_panel.raise_()
        self.place_panels()
        self.forcer.detect()

    def request_forces(self):
        structure = self.scene.structure
        if structure is not None:
            self.forcer.set_structure(structure)
        snapshot = None if structure is None or not self.forcer.enabled else replace(structure, positions=self.scene.displayed)
        values = self.forcer.request(snapshot)
        if structure is not None and (values is not None or self.scene.force_arrows.actor.GetVisibility()):
            self.scene.set_forces(values, self.forcer.threshold, self.forcer.scale)

    def force_changed(self):
        if self.closing:
            if not self.forcer.busy and not self.relaxer.busy and not self.loaders:
                self.close()
            return
        self.force_panel.sync()
        with QSignalBlocker(self.actions["show_forces"]):
            self.actions["show_forces"].setChecked(self.forcer.enabled)
        if self.document is not None:
            self.sessions[self.current_index].show_forces = self.forcer.enabled
        self.request_forces()

    def save_force_settings(self):
        self.settings["forces"] = {"model": self.forcer.model, "device": self.forcer.device,
                                   "threshold": self.forcer.threshold, "scale": self.forcer.scale,
                                   "paths": {key: str(path) for key, path in self.forcer.paths.items()}}
        self.persist_settings()

    def switch_document(self, index: int):
        if index < 0:
            return
        page = self.tabs.tabData(index)
        if isinstance(page, Session):
            if self.comparison is None and self.document is not None and self.sessions[self.current_index] is page:
                return
            self._store_current()
            self.activate_document(self.sessions.index(page))
        else:
            self.activate_cell(page.active)

    def cycle_document(self, delta: int):
        self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % self.tabs.count())

    def open_file(self):
        names, _filter = QFileDialog.getOpenFileNames(self, "导入结构", str(ROOT / "examples"),
                         "结构与轨迹 (POSCAR* CONTCAR* OUTCAR* XDATCAR* vasprun.xml* CHGCAR* ELFCAR* CHG* LOCPOT* PARCHG* AECCAR* data.* *.vasp *.xdatcar *.xyz *.extxyz *.cif *.data *.lmp *.lammps *.dump *.lammpstrj *.xml *.vscene);;渲染场景 (*.vscene);;所有文件 (*)")
        for name in names:
            self.open_path(Path(name))

    def open_path(self, path: Path, *, target=None):
        try:
            self.load(path, target=target)
        except (ValueError, KeyError, TypeError, OSError) as error:
            QMessageBox.critical(self, "文件无法打开", str(error))

    def save_as(self, *, final=False):
        session = self.sessions[self.current_index]
        xyz = self.document.structure.cell is None
        target = self.path
        suffix = ".xyz" if xyz or not all(self.document.structure.pbc) else ".vasp"
        if target.suffix.lower() == ".vscene":
            target = target.with_suffix(suffix)
        if session.trajectory is not None or session.relaxation is not None or session.readonly:
            tag = "_final" if final else f"_frame{session.frame + 1}"
            stem = ("structure_" if session.volumes else "") + self.path.stem
            target = self.path.with_name(stem + tag + suffix)
        elif self.path.suffix.lower() in (".cif", ".data", ".lmp", ".lammps"):
            target = self.path.with_suffix(suffix)
        filters = {"VASP：晶胞、约束、温度 (*.vasp POSCAR* CONTCAR*)": "vasp",
                   "XYZ：仅元素坐标，无晶胞/约束/温度 (*.xyz)": "xyz",
                   "CIF：晶胞与原子，无约束/温度 (*.cif)": "cif",
                   "LAMMPS data：atomic/metal，晶胞坐标及标准元素质量 (*.data *.lmp)": "lammps-data"}
        if xyz:
            filters = {key: value for key, value in filters.items() if value == "xyz"}
        elif not all(self.document.structure.pbc):
            filters = {key: value for key, value in filters.items() if value not in ("vasp", "cif")}
        dialog = ExportDialog(self, "导出最终结构" if final else "导出当前帧结构", target, filters,
                              preserved_mode=self.document.mode if self.document.restart else None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            self.save_to(Path(dialog.selectedFiles()[0]), overwrite=True, format=dialog.kind, final=final,
                         options=dialog.export_options())
        except (ValueError, OSError) as error:
            QMessageBox.critical(self, "文件无法保存", str(error))
            return False
        return True

    def save_to(self, path: Path, *, overwrite=False, format=None, final=False, options=None):
        session = self.sessions[self.current_index]
        detected = output_kind(path)
        kind = detected if format is None else format
        if (path.suffix.lower() in (".xyz", ".cif", ".data", ".lmp", ".lammps", ".vasp") or path.name.startswith("data.")) and detected != kind:
            raise ValueError("文件后缀与选择的导出格式不一致，请调整文件名。")
        if session.trajectory is not None and path.resolve() == session.trajectory.path.resolve():
            raise ValueError("导出请选择新路径，不能覆盖正在读取的源文件。")
        if any(path.resolve() == volume.path.resolve() for sources in session.volumes.values() for volume in sources):
            raise ValueError("电子数据仅支持导入；结构导出不能覆盖电子源文件。")
        if session.readonly and path.resolve() == session.path.resolve():
            raise ValueError("此文件仅支持导入；请将结构导出到新路径。")
        document = self.export_document(session, final=final)
        if options is not None:
            if not options.keep_constraints:
                document.structure.free = None
            document.structure.set_temperature(options.temperature)
        content = serialize(document, kind, regenerate=options is None or options.temperature is not None,
                            coordinate_mode=None if options is None else options.coordinate_mode)
        self.write_content(path, content, overwrite=overwrite)
        if options is not None:
            return
        if session.trajectory is not None or session.relaxation is not None or session.readonly:
            return
        if kind not in ("vasp", "xyz") or (kind == "xyz" and self.document.structure.cell is not None):
            return
        if kind == "xyz":
            self.document = DraftDocument(deepcopy(self.document.structure), self.document.title, unsaved=False)
        else:
            self.document = PoscarDocument.from_bytes(content)
        self.path = path
        self.history = History()
        self.scene.set_structure(self.document.structure)
        self.refresh()

    def write_content(self, path, content, *, overwrite=False):
        if overwrite and path.exists():
            backups = work_dir() / "backup"
            backups.mkdir(parents=True, exist_ok=True)
            copyfile(path, backups / (path.name + "." + uuid.uuid4().hex[:8]))
        with path.open("wb" if overwrite else "xb") as stream:
            if isinstance(content, bytes):
                stream.write(content)
            else:
                for chunk in content:
                    stream.write(chunk)

    def export_document(self, session, *, final=False):
        document = deepcopy(session.document)
        state = session.relaxation
        if state is None:
            return document
        step = state.frames[-1] if final else state.frames[session.frame]
        document.structure.positions = step.positions.copy()
        if state.kind == "opt":
            document.structure.free = None if state.original_free is None else state.original_free.copy()
        elif step.temperature is not None:
            document.structure.set_temperature(step.temperature)
        if isinstance(document, XyzDocument):
            lines = [str(len(document.structure.symbols)), "VBuild MLIP pre-equilibration"]
            lines += [symbol + " " + " ".join(format(x, ".17g") for x in position)
                      for symbol, position in zip(document.structure.symbols, step.positions)]
            return XyzDocument.read_frame(BytesIO(("\n".join(lines) + "\n").encode("utf-8")))
        velocities = None
        if state.kind == "md" and step.velocities is not None:
            velocities = Velocities(step.velocities.copy(), "cartesian")
        return FrameDocument(document.structure, "VBuild MLIP " + state.kind, input_velocities=velocities)

    def trajectory_content(self, session, target, kind="xdatcar", *, interval=1):
        detected = output_kind(target)
        if (kind == "xyz" and target.suffix.lower() not in ("", ".xyz")) or (kind == "xdatcar" and detected != "vasp"):
            raise ValueError("轨迹后缀与导出格式不一致，请使用 XYZ 或 XDATCAR 文件名。")
        if session.trajectory is not None and target.resolve() == session.trajectory.path.resolve():
            raise ValueError("导出轨迹请选择新路径，不能覆盖正在读取的源文件。")
        if interval < 1:
            raise ValueError("间隔帧必须至少为 1。")
        return self.convert_frames(session, kind, interval)

    def convert_frames(self, session, kind, interval=1):
        previous = None
        count = len(session.relaxation.frames) if session.relaxation is not None else len(session.trajectory.offsets)
        if session.trajectory is not None and session.trajectory.kind == "neb":
            count = session.trajectory.export_count
        preferred = (session.document.original_labels if isinstance(session.document, PoscarDocument)
                     else session.document.original.labels if isinstance(session.document, DraftDocument) else ())
        for index in range(0, count, interval):
            if session.relaxation is not None:
                temporary = replace(session, frame=index)
                document = self.export_document(temporary)
            else:
                document = (session.trajectory.export_frame(index) if session.trajectory.kind == "neb"
                            else session.trajectory.frame(index))
            structure = document.structure
            if kind == "xyz":
                yield serialize(document, "xyz")
                continue
            if structure.cell is None or not all(structure.pbc):
                raise ValueError("XDATCAR 需要每帧都有三维周期晶胞。")
            order, names, counts = group_order(structure.labels, preferred)
            header = ("\n".join(vasp_header(structure.cell, names, counts, "VBuild trajectory")) + "\n").encode()
            signature = (tuple(names), tuple(counts), structure.cell.tobytes())
            if signature != previous:
                yield header
            previous = signature
            yield f"Direct configuration= {index + 1}\n".encode()
            fraction = np.linalg.solve(structure.cell.T, structure.positions[order].T).T
            yield ("\n".join(vasp_rows(fraction)) + "\n").encode()

    def export_xdatcar(self):
        session = self.sessions[self.current_index]
        periodic = session.document.structure.cell is not None and all(session.document.structure.pbc)
        filters = {"XDATCAR（无固定约束） (*.xdatcar XDATCAR*)": "xdatcar", "XYZ（无晶胞/附加数据） (*.xyz)": "xyz"}
        if not periodic:
            filters = {name: kind for name, kind in filters.items() if kind == "xyz"}
        target = session.path.with_name("XDATCAR_export.xdatcar" if periodic else session.path.stem + "_export.xyz")
        count = len(session.relaxation.frames) if session.relaxation is not None else len(session.trajectory.offsets)
        if session.trajectory is not None and session.trajectory.kind == "neb":
            count = session.trajectory.export_count
        dialog = ExportDialog(self, "导出轨迹", target, filters, trajectory=True, frames=count)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                target = Path(dialog.selectedFiles()[0])
                self.write_content(target, self.trajectory_content(session, target, dialog.kind,
                                   interval=dialog.export_options().interval), overwrite=True)
            except (ValueError, OSError) as error:
                QMessageBox.critical(self, "轨迹无法保存", str(error))

    def export_result(self, kind):
        if kind == "trajectory":
            self.export_xdatcar()
            return
        if kind in ("final", "structure"):
            self.save_as(final=True)
            return
        session = self.sessions[self.current_index]
        target = session.path.with_name(session.path.stem + "_data.csv")
        name, _filter = QFileDialog.getSaveFileName(self, "导出数据", str(target), "CSV 数据 (*.csv)")
        if name:
            try:
                content = csv_data(session.relaxation)
                self.write_content(Path(name), content, overwrite=True)
            except (ValueError, OSError) as error:
                QMessageBox.critical(self, "导出失败", str(error))

    def toggle_results(self, kind):
        session = self.sessions[self.current_index]
        if kind == "info":
            self.toggle_info(not session.show_info)
            return
        if kind == "walls":
            session.show_walls = not session.show_walls
            self.update_walls()
            return
        key = "show_data" if kind == "table" else "show_curves"
        setattr(session, key, not getattr(session, key))
        if getattr(session, key):
            session.analysis_tab = "data" if kind == "table" else "curves"
        self.populate_results()
        self.place_panels()
        if getattr(session, key):
            self.analysis.raise_()

    def toggle_info(self, visible):
        self.sessions[self.current_index].show_info = visible
        self.populate_results()
        self.place_panels()
        if visible:
            self.relax_info.raise_()

    def close_analysis(self, name):
        session = self.sessions[self.current_index]
        if name.startswith("neb_"):
            self.neb_workspace.close_pane(name)
            self.populate_results()
            self.place_panels()
            return
        key = {"data": "show_data", "curves": "show_curves", "report": "show_report", "elf": "show_elf_slice", "bader": "show_bader_data", "moments": "show_moments_data"}[name]
        setattr(session, key, False)
        session.analysis_tab = "curves" if name == "data" else "data"
        self.populate_results()
        self.place_panels()

    def save_channels(self, channels):
        self.curve_panel.export.setEnabled(bool(channels) and bool(self.curve_panel.plot.frames))
        key = 'curve_channels' if self.curve_panel.plot.kind == 'md' else 'opt_curve_channels'
        self.settings[key] = channels
        self.persist_settings()

    def select_analysis(self, name):
        self.sessions[self.current_index].analysis_tab = name

    def populate_results(self):
        session = self.sessions[self.current_index] if self.document is not None else None
        state = None if session is None else session.relaxation
        if state is not None:
            self.data_panel.set_kind(state.kind)
            self.curve_panel.set_kind(state.kind)
            self.analysis.titles['curves'] = '预平衡曲线' if state.kind == 'md' else '优化曲线'
        neb = session is not None and session.trajectory is not None and session.trajectory.kind == "neb"
        imported = session is not None and state is None and not neb and (isinstance(session.trajectory, ReportTrajectory) or
                   bool(getattr(session.document, "observations", {})))
        visible = {"data": state is not None and session.show_data,
                   "bader": session is not None and session.bader is not None and session.show_bader_data,
                   "moments": session is not None and session.moments is not None and session.show_moments_data,
                   "curves": state is not None and session.show_curves,
                   "report": imported and session.show_report,
                   "elf": session is not None and bool(session.volumes.get("elf")) and session.show_elf_slice}
        self.volume_panels["elf"].set_slice_visible(visible["elf"])
        if visible["bader"]:
            self.bader_table.populate(session.bader, session.bader.matches(session.document.structure))
        if self.bader_panel is not None and session is not None and session.bader is not None:
            self.bader_panel.populate(session.bader, session.show_bader, visible["bader"], session.bader.matches(session.document.structure))
        if visible["moments"]:
            self.moment_table.populate(session.moments, session.moments.matches(session.document.structure))
        if self.moment_panel is not None and session is not None:
            self.moment_panel.populate(session.moments, session.show_moments,
                session.moments is not None and session.moments.matches(session.document.structure),
                session.moments_loading, visible["moments"])
        if visible["elf"]:
            options = session.volume_options["elf"]
            self.slice_panel.populate(session.volumes["elf"][options["source"]], options, self.scene.volume_views["elf"])
        else:
            self.slice_panel.clear()
        self.actions["show_report"].setEnabled(imported)
        with QSignalBlocker(self.actions["show_report"]):
            self.actions["show_report"].setChecked(visible["report"])
        if visible["report"]:
            records = session.trajectory.records if isinstance(session.trajectory, ReportTrajectory) else [session.document.observations]
            self.report_panel.populate(records, session.document, session.frame)
        else:
            self.report_panel.clear()
        if self.neb_workspace is not None:
            visible.update(self.neb_workspace.visible())
        self.analysis.set_panels(visible, session.analysis_tab if session is not None else "data")
        info = state is not None and session.show_info
        self.relax_info.populate(state, len(self.document.structure.symbols) if state is not None else 0, info)
        self.actions["show_info"].setEnabled(state is not None)
        with QSignalBlocker(self.actions["show_info"]):
            self.actions["show_info"].setChecked(info)
        if visible["data"]:
            self.data_panel.model.populate(state)
        else:
            self.data_panel.model.clear()
        if visible["curves"]:
            self.curve_panel.plot.frames = state.frames
            self.curve_panel.plot.update()
        else:
            self.curve_panel.plot.frames = []
        self.curve_panel.export.setEnabled(bool(self.curve_panel.plot.frames) and bool(self.curve_panel.plot.channels))
        self.relax_panel.exports["table"].setChecked(visible["data"])
        self.relax_panel.exports["curves"].setChecked(visible["curves"])
        self.relax_panel.exports["info"].setChecked(info)

    def update_walls(self):
        session = self.sessions[self.current_index] if self.document is not None else None
        state = None if session is None else session.relaxation
        visible = state is not None and state.kind == "md" and session.show_walls
        self.relax_panel.preview_walls.setChecked(visible)
        changed = self.scene.walls.update(self.scene.structure, None if state is None else state.walls, visible,
                                          self.scene.periods if self.scene.show_periodic else (1, 1, 1))
        if changed:
            self.scene.plotter.render()
        self.place_panels()

    def persist_settings(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings, indent=2) + "\n", encoding="utf-8")

    def save_modebar(self, preferences):
        self.settings["modebar"] = preferences
        self.persist_settings()

    def createPopupMenu(self):
        return self.modebar.context_menu()

    def save_panel(self, name):
        self.settings.setdefault("panels", {})[name] = self.overlays[name].saved()
        self.persist_settings()

    def save_axis_position(self, position):
        self.settings['axis_position'] = position
        self.persist_settings()
        for scene in self.scenes():
            scene.axis_marker.anchor = position.copy()
            scene.axis_marker.place(scene.axis_marker.bottom)

    def scenes(self):
        scenes = [self.single_scene] + [cell.scene for page in self.comparisons for cell in page.cells if cell.scene is not None]
        if self.clip_panel is not None:
            scenes.append(self.clip_panel.scene)
        return scenes

    def set_mode(self, mode: str):
        self.actions[mode].setChecked(True)
        self.scene.set_mode(mode)
        self._status()

    def nudge(self, horizontal: int, vertical: int):
        if self.scene.paste.active:
            self.cycle_paste(vertical)
            return
        self.scene.cancel_gesture()
        right, up = self.scene.screen_basis()
        delta = self.move_step * (horizontal * right + vertical * up)
        self.move_atoms(self.scene.selection.copy(), delta)

    def view_axis(self, axis):
        session = self.sessions[self.current_index]
        self.scene.view_axis(axis, session.axis_signs[axis])
        session.axis_signs[axis] *= -1

    def update_rotation_hints(self):
        for name, (label, _motion, _sign) in ROTATIONS.items():
            self.actions["turn_" + name].setToolTip(f"视角{label.replace('侧转', '旋转')}{self.rotation_step:g}°；选中 ≥3 个原子时旋转原子组")

    def turn(self, motion, sign):
        if len(self.scene.selection) <= 2:
            self.scene.rotate_screen(motion, sign * self.rotation_step)
            return
        indices = self.scene.selection.copy()
        right, up = self.scene.screen_basis()
        camera = self.scene.plotter.camera
        view = np.array(camera.position) - np.array(camera.focal_point)
        axis = {"pitch": right, "yaw": up, "roll": view}[motion] * self.scene.mirror_sign()
        structure = self.document.structure

        def rotate():
            structure.positions[indices] = rotate_group(structure.positions[indices], axis, -sign * self.rotation_step)

        self.history.apply(self.document, rotate, selection=indices, label=f"旋转 {len(indices)} 个原子")
        self.scene.set_structure(structure)
        self.refresh()

    def update_style_controls(self):
        session = self.sessions[self.current_index]
        self.style_panel.populate(session.representation)
        with QSignalBlocker(self.actions["style"]):
            self.actions["style"].setChecked(session.show_style)
        self.style_panel.setVisible(session.show_style)
        icon = Path(__file__).with_name("icons") / ("style_" + session.representation + ".svg")
        self.actions["style"].setIcon(QIcon(str(icon)))
        self.actions["style"].setToolTip(dict(KINDS)[session.representation] + " · Space 切换模型风格")

    def toggle_style(self, visible):
        self.sessions[self.current_index].show_style = visible
        self.update_style_controls()
        self.place_panels()

    def set_representation(self, kind):
        session = self.sessions[self.current_index]
        session.representation = kind
        if session.render_style is not None:
            session.render_style["representation"] = kind
            self.render_panel.updating = True
            self.render_panel.representation.setCurrentIndex(self.render_panel.representation.findData(kind))
            self.render_panel.updating = False
        self.scene.set_look(kind, session.render_style, session.render_style is not None,
                            overrides=session.render_atoms, view_style=self.view_profile(session))
        self.update_style_controls()
        self.scene.plotter.interactor.setFocus()
        self.sync_appearance()
        self._status()

    def cycle_representation(self):
        kinds = list(dict(KINDS))
        self.set_representation(kinds[(kinds.index(self.scene.visual.kind) + 1) % len(kinds)])

    def ensure_style_library(self):
        if self.style_library is None:
            library = builtin_styles()
            if self.styles_path.exists():
                data = json.loads(self.styles_path.read_text(encoding="utf-8"))
                if data["version"] != 1:
                    raise ValueError("本地样式库版本不支持。")
                for value in data["styles"].values():
                    style = validate_style(value)
                    insert_style(library, style)
            self.style_library = library

    def view_profile(self, session):
        if session.view_style is None:
            self.ensure_style_library()
            name = self.settings.get("view_style", "Default")
            if name in ("清晰", "柔和", "平面", "经典") and name not in self.style_library:
                name = "Default"
            session.view_style = deepcopy(self.style_library[name])
            if session.render_style is None:
                session.show_bonds = self.settings.get("view_bonds")
        return session.view_style

    def show_view_styles(self, _checked=False):
        self.ensure_style_library()
        if self.view_menu is not None:
            self.view_menu.deleteLater()
        self.view_menu = QMenu(self)
        group = QActionGroup(self.view_menu)
        current = self.settings.get("view_style", "Default")
        if self.document is not None:
            session = self.sessions[self.current_index]
            current = (session.render_style or self.view_profile(session))["name"]
        for index, (name, profile) in enumerate(self.style_library.items()):
            if index == len(TITLES):
                self.view_menu.addSeparator()
            action = self.view_menu.addAction(TITLES.get(name, name))
            action.setCheckable(True)
            action.setChecked(name == current)
            action.setData(name)
            action.setToolTip(profile["description"])
            group.addAction(action)
            action.triggered.connect(lambda _checked, key=name: self.choose_view_style(key))
        self.view_menu.setToolTipsVisible(True)
        self.view_menu.addSeparator()
        bonds = self.view_menu.addAction("显示键")
        bonds.setCheckable(True)
        bonds.setChecked(self.scene.visual.profile["bonds"]["show"] if self.document is not None else self.settings.get("view_bonds", True))
        bonds.setData("show_bonds")
        bonds.triggered.connect(self.toggle_bonds)
        models = self.view_menu.addMenu("模型")
        model_group = QActionGroup(models)
        for key, label in KINDS:
            action = models.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.document is not None and self.scene.visual.kind == key)
            action.setEnabled(self.document is not None)
            model_group.addAction(action)
            action.triggered.connect(lambda _checked, kind=key: self.set_representation(kind))
        self.view_menu.addSeparator()
        self.view_menu.addAction("导入样式…", self.import_view_style)
        self.view_menu.popup(self.view_button.mapToGlobal(self.view_button.rect().bottomLeft()))

    def toggle_bonds(self, visible):
        self.settings["view_bonds"] = visible
        self.persist_settings()
        self.scene.bond_visibility = visible
        if self.document is None:
            return
        session = self.sessions[self.current_index]
        session.show_bonds = visible
        if session.render_style is not None:
            session.render_style["bonds"]["show"] = visible
            self.render_panel.populate(session.render_style, self.style_library, self.document.structure.symbols)
            self.render_changed(session.render_style)
        else:
            self.scene.set_look(session.representation, view_style=self.view_profile(session))
            self.sync_appearance()

    def sync_appearance(self):
        if self.document is not None:
            trajectory = self.sessions[self.current_index].trajectory
            if trajectory is not None and trajectory.kind == "neb":
                self.neb_workspace.sync()
        for page in self.comparisons:
            page.sync()

    def choose_view_style(self, name):
        profile = deepcopy(self.style_library[name])
        self.settings["view_style"] = name
        self.persist_settings()
        if self.document is None:
            self.scene.set_look("ball", view_style=profile)
            return
        session = self.sessions[self.current_index]
        if session.render_style is not None:
            self.choose_render_style(name)
            return
        session.view_style = profile
        self.scene.set_look(session.representation, view_style=profile)
        self.update_moments()
        self.sync_appearance()
        self.scene.plotter.interactor.setFocus()

    def import_view_style(self):
        name, _filter = QFileDialog.getOpenFileName(self, "导入样式", str(self.styles_path.parent), "VBuild 样式 (*.json)")
        if not name:
            return
        try:
            profile = read_style(Path(name))
            self.ensure_style_library()
            profile = insert_style(self.style_library, profile)
            content = json.dumps({"version": 1, "styles": self.style_library}, ensure_ascii=False, indent=2).encode("utf-8")
            self.write_content(self.styles_path, content, overwrite=True)
            self.choose_view_style(profile["name"])
        except (ValueError, KeyError, TypeError, OSError) as error:
            QMessageBox.critical(self, "样式无法导入", str(error))

    def new_render(self):
        try:
            self.ensure_style_library()
            name = self.settings.get("render_style", "Default")
            profile = deepcopy(self.style_library[name])
            if name == "Default":
                profile["scene"].update(background="#000000", grid=True)
        except (ValueError, KeyError, TypeError, OSError) as error:
            QMessageBox.critical(self, "样式库无法读取", str(error))
            return
        source = self.export_document(self.sessions[self.current_index])
        document = DraftDocument(deepcopy(source.structure), "VBuild rendering snapshot", unsaved=False)
        stem = ("structure_" if self.sessions[self.current_index].volumes else "") + self.path.stem
        path = self.path.with_name(stem + "_render" + (".xyz" if source.structure.cell is None else ".vasp"))
        self._store_current()
        self.ensure_render_panel()
        self.sessions.append(Session(document, path, representation=profile["representation"], render_style=profile,
                                     show_bonds=self.scene.visual.profile["bonds"]["show"]))
        self.activate_document(self.add_session_tab())

    def ensure_render_panel(self):
        if self.render_panel is None:
            from .render_panel import RenderPanel
            self.render_panel = RenderPanel(self.scene.plotter.interactor)
            self.render_panel.changed.connect(self.render_changed)
            self.render_panel.atom_changed.connect(self.render_atom_changed)
            self.render_panel.atom_requested.connect(lambda index: self.scene.set_selection([index]))
            self.render_panel.selected.connect(self.choose_render_style)
            self.render_panel.file_requested.connect(self.render_file)
            self.render_panel.closed.connect(lambda: self.toggle_render_controls(False))
            self.overlays["render"] = self.render_panel
            if "render" in self.settings.get("panels", {}):
                self.render_panel.restore(self.settings["panels"]["render"])
            self.render_panel.geometry_changed.connect(lambda: self.save_panel("render"))

    def render_scene_bytes(self):
        from .render_save import scene_bytes
        self._store_current()
        session, camera = self.sessions[self.current_index], self.scene.plotter.camera
        view = {"camera": {"position": list(camera.position), "focal_point": list(camera.focal_point), "up": list(camera.up),
                           "parallel_scale": camera.parallel_scale, "view_angle": camera.view_angle,
                           "parallel_projection": bool(camera.parallel_projection), "window_center": list(camera.GetWindowCenter()),
                           "reflection": self.scene.mirror_sign()}, "mode": session.mode, "selection": session.selection,
                "show_periodic": session.show_periodic, "periods": list(session.periods), "show_indices": session.show_indices,
                "hidden_elements": sorted(session.hidden_elements), "show_fixed": session.show_fixed,
                "index_font": self.scene.labels.size}
        return scene_bytes(session.document, session.render_style, session.render_atoms, view)

    def load_render_scene(self, path, *, target=None):
        from .render_save import read_scene
        from vtkmodules.vtkCommonTransforms import vtkTransform
        from vtkmodules.vtkRenderingCore import vtkCamera
        saved = read_scene(path)
        self.ensure_style_library()
        self.ensure_render_panel()
        view, values = saved.view, saved.view["camera"]
        camera = vtkCamera()
        camera.SetPosition(*values["position"])
        camera.SetFocalPoint(*values["focal_point"])
        camera.SetViewUp(*values["up"])
        camera.SetParallelScale(values["parallel_scale"])
        camera.SetViewAngle(values["view_angle"])
        camera.SetParallelProjection(values["parallel_projection"])
        camera.SetWindowCenter(*values["window_center"])
        transform = vtkTransform()
        transform.Scale(values["reflection"], 1, 1)
        camera.SetUserViewTransform(transform)
        session = Session(saved.document, path, camera=camera, mode=view["mode"], selection=view["selection"],
                          render_style=saved.style, representation=saved.style["representation"], render_atoms=saved.atoms,
                          render_font=view["index_font"], show_periodic=view["show_periodic"], periods=tuple(view["periods"]),
                          show_indices=view["show_indices"], hidden_elements=set(view["hidden_elements"]), show_fixed=view["show_fixed"])
        self.finish_import(session, target)
        self.scene.plotter.reset_camera_clipping_range()
        self.scene.plotter.render()
        self.render_panel.status.setText("已恢复渲染场景；通用样式库保持独立。")

    def render_changed(self, profile):
        session = self.sessions[self.current_index]
        session.render_style = profile
        session.show_bonds = profile["bonds"]["show"]
        session.representation = profile["representation"]
        self.scene.set_look(session.representation, profile, True, overrides=session.render_atoms)
        self.update_moments()
        self.update_style_controls()
        self._status()
        self.sync_appearance()

    def render_atom_changed(self, index, key, value):
        session = self.sessions[self.current_index]
        if key == "reset":
            session.render_atoms.pop(index, None)
        else:
            session.render_atoms.setdefault(index, {})[key] = value
        self.render_changed(session.render_style)

    def sync_render_atoms(self, session):
        structure = session.document.structure
        topology = (structure.symbols, structure.origins)
        changed = bool(session.render_topology) and session.render_topology != topology
        if changed:
            session.render_atoms.clear()
        session.render_topology = topology
        return changed

    def choose_render_style(self, name):
        session = self.sessions[self.current_index]
        session.render_style = deepcopy(self.style_library[name])
        if session.show_bonds is not None:
            session.render_style["bonds"]["show"] = session.show_bonds
        self.render_panel.populate(session.render_style, self.style_library, self.document.structure.symbols)
        self.render_changed(session.render_style)
        self.render_panel.status.setText("当前样式：" + name + "；单原子设置仅作用于此结构。")

    def toggle_render_controls(self, visible):
        self.sessions[self.current_index].show_render = visible
        self.render_panel.setVisible(visible)
        with QSignalBlocker(self.actions["render_controls"]):
            self.actions["render_controls"].setChecked(visible)
        self.place_panels()

    def save_render_style(self, profile):
        profile = validate_style(profile)
        if profile["name"] in TITLES:
            profile = insert_style(self.style_library, profile)
        library = self.style_library | {profile["name"]: profile}
        self.styles_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps({"version": 1, "styles": library}, ensure_ascii=False, indent=2).encode("utf-8")
        self.write_content(self.styles_path, content, overwrite=True)
        self.style_library = library
        self.sessions[self.current_index].render_style = profile
        self.settings["render_style"] = profile["name"]
        self.persist_settings()
        self.render_panel.populate(profile, library, self.document.structure.symbols)
        self.render_panel.status.setText("样式已保存：" + profile["name"])
        return profile["name"]

    def import_render_style(self, path):
        profile = read_style(path)
        name = self.save_render_style(profile)
        self.choose_render_style(name)

    def render_file(self, kind):
        profile = self.sessions[self.current_index].render_style
        try:
            if kind == "save":
                self.save_render_style(profile)
                return
            if kind == "open_scene":
                name, _filter = QFileDialog.getOpenFileName(self, "打开渲染场景", str(self.path.parent), "VBuild 渲染场景 (*.vscene)")
                if name:
                    self.open_path(Path(name))
                return
            if kind == "save_scene":
                name, _filter = QFileDialog.getSaveFileName(self, "保存渲染场景", str(self.path.with_suffix(".vscene")), "VBuild 渲染场景 (*.vscene)")
                if name:
                    self.write_content(Path(name), self.render_scene_bytes(), overwrite=True)
                    self.render_panel.status.setText("渲染场景已保存：" + Path(name).name)
                return
            if kind == "import":
                name, _filter = QFileDialog.getOpenFileName(self, "导入渲染样式", str(self.styles_path.parent), "VBuild 样式 (*.json)")
                if name:
                    self.import_render_style(Path(name))
                return
            target = self.path.with_suffix(".png" if kind == "image" else ".json")
            name, _filter = QFileDialog.getSaveFileName(self, "导出 PNG 图片" if kind == "image" else "导出渲染样式", str(target),
                                                       "PNG 图片 (*.png)" if kind == "image" else "VBuild 样式 (*.json)")
            if name:
                content = self.render_image(profile) if kind == "image" else style_bytes(profile)
                self.write_content(Path(name), content, overwrite=True)
        except (ValueError, KeyError, TypeError, OSError) as error:
            QMessageBox.critical(self, "渲染输出失败", str(error))

    def render_image(self, profile):
        selected = self.scene.selection.copy()
        self.scene.set_selection([])
        actors = [self.scene.stage.grid_actor, self.scene.fixed.actor, self.scene.measure_actor,
                  self.scene.measure_labels.actor, *self.scene.walls.actors.values(), *self.scene.coordinate_grid.actors]
        if self.scene.cell_actor is not None:
            actors.append(self.scene.cell_actor)
        visible = [actor.GetVisibility() for actor in actors]
        axes = self.scene.plotter.renderer.axes_widget.GetEnabled()
        if not profile["image"]["guides"]:
            for actor in actors:
                actor.VisibilityOff()
            self.scene.plotter.renderer.axes_widget.SetEnabled(False)
        pixels = self.scene.plotter.screenshot(transparent_background=profile["image"]["transparent"], scale=profile["image"]["scale"])
        for actor, value in zip(actors, visible):
            actor.SetVisibility(value)
        self.scene.plotter.renderer.axes_widget.SetEnabled(axes)
        self.scene.set_selection(selected)
        pixels = np.ascontiguousarray(pixels)
        height, width, channels = pixels.shape
        image = QImage(pixels.data, width, height, pixels.strides[0], QImage.Format.Format_RGBA8888 if channels == 4 else QImage.Format.Format_RGB888)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(buffer.data())

    def place_panels(self):
        top = self.atom_panel.y() + self.atom_panel.height() + 8 if self.atom_panel.isVisible() else 12
        if self.scene.cell_info.isVisible():
            self.scene.cell_info.move(12, top)
            top += self.scene.cell_info.height() + 8
        summary = self.scene.moment_summary
        if summary.isVisible():
            summary.move(12, top)
            top += summary.height() + 8
        self.display_panel.place(12, top)
        self.player.place()
        bottom = self.player.y() if self.player.isVisible() else self.scene.plotter.interactor.height()
        self.scene.axis_marker.place(self.player.y() if self.player.isVisible() else None)
        if self.grid_panel.isVisible():
            self.grid_panel.place(12, top)
            self.grid_panel.fit(bottom)
        for panel in self.volume_panels.values():
            if panel.isVisible():
                panel.place(12, top)
                panel.move(panel.x(), max(0, min(panel.y(), bottom - panel.height())))
        for panel in (self.bader_panel, self.moment_panel):
            if panel is not None and panel.isVisible():
                panel.place(12, top)
                panel.move(panel.x(), max(0, min(panel.y(), bottom - panel.height())))
        if self.force_panel.isVisible():
            self.force_panel.place(12, top)
            if self.force_panel.y() + self.force_panel.height() > bottom:
                self.force_panel.move(self.force_panel.x(), max(0, bottom - self.force_panel.height()))
        if self.relax_panel.isVisible():
            left = max(top, self.display_panel.y() + self.display_panel.height() + 8) if self.display_panel.isVisible() else top
            self.relax_panel.place(12, left)
            self.relax_panel.fit(bottom)
        if self.relax_info.isVisible():
            self.relax_info.bottom = bottom
            self.relax_info.place(self.scene.plotter.interactor.width() - self.relax_info.width() - 12, 12)
        if self.analysis.isVisible():
            self.analysis.fit(bottom)
        if self.neb_workspace is not None:
            self.neb_workspace.place(bottom)
        if self.style_panel.isVisible():
            self.style_panel.place(self.scene.plotter.interactor.width() - self.style_panel.width() - 12, 12)
            if self.style_panel.y() + self.style_panel.height() > bottom:
                self.style_panel.move(self.style_panel.x(), max(0, bottom - self.style_panel.height()))
        if self.render_panel is not None and self.render_panel.isVisible():
            self.render_panel.place(12, top)
            self.render_panel.fit(bottom)

    def toggle_cell_info(self, visible):
        self.scene.set_cell_info(visible)
        if self.current_index >= 0:
            self.sessions[self.current_index].show_cell_info = visible
        self.place_panels()
        self.sync_appearance()

    def toggle_grid(self, visible):
        session = self.sessions[self.current_index]
        session.show_grid_panel = visible
        self.grid_panel.setVisible(visible)
        self.set_grid(self.scene.grid_options | {'show': visible})
        self.place_panels()
        if visible:
            self.grid_panel.raise_()

    def set_grid(self, options):
        try:
            self.scene.set_grid(options)
        except ValueError as error:
            self.grid_panel.populate(options, self.scene.structure, self.scene.period_counts())
            self.grid_panel.message.setText(str(error))
            with QSignalBlocker(self.actions['show_grid']):
                self.actions['show_grid'].setChecked(self.scene.grid_options['show'])
            return
        session = self.sessions[self.current_index]
        session.grid = deepcopy(options)
        preferred_basis = self.settings.get('grid_defaults', {}).get('basis', 'cell')
        self.settings['grid_defaults'] = deepcopy(options) | {'show': False}
        if self.scene.structure.cell is None:
            self.settings['grid_defaults']['basis'] = preferred_basis
        self.persist_settings()
        with QSignalBlocker(self.actions['show_grid']):
            self.actions['show_grid'].setChecked(options['show'])
        self.grid_panel.populate(options, self.scene.structure, self.scene.period_counts())
        self.sync_appearance()

    def toggle_periodic(self, visible):
        self.scene.set_periodic(visible, self.scene.periods)
        self.display_panel.show_sections(visible, self.scene.show_indices)
        self.place_panels()
        if visible:
            self.display_panel.raise_()
        self.update_walls()
        if self.neb_workspace is not None and self.neb_workspace.run is not None:
            self.neb_workspace.sync()

    def set_periods(self, counts):
        self.scene.set_periodic(True, counts)
        self.update_walls()
        if self.neb_workspace is not None and self.neb_workspace.run is not None:
            self.neb_workspace.sync()

    def toggle_indices(self, visible):
        self.scene.set_indices(visible, self.scene.hidden_elements)
        self.display_panel.show_sections(self.scene.show_periodic, visible)
        self.place_panels()
        if visible:
            self.display_panel.raise_()

    def filter_indices(self, hidden):
        self.scene.set_indices(self.scene.show_indices, hidden)

    def set_index_font(self, size):
        self.scene.labels.set_size(size)
        if self.current_index >= 0 and self.sessions[self.current_index].render_style is not None:
            self.sessions[self.current_index].render_font = size
        self.scene.plotter.render()
        self.settings['index_font'] = size
        self.persist_settings()

    def apply_shortcuts(self):
        keys = bindings(self.settings.get('shortcuts'))
        self.keymap = keys
        for name, action in self.actions.items():
            if name in keys:
                action.setShortcut(QKeySequence(keys[name]))
        if sys.platform == 'darwin' and keys['remove'] == 'Backspace':
            self.actions['remove'].setShortcuts([QKeySequence('Backspace'), QKeySequence('Delete')])
        for scene in self.scenes():
            scene.molecule_modifier = getattr(Qt.KeyboardModifier, keys['molecule_modifier'] + 'Modifier')
        native = lambda name: QKeySequence(keys[name]).toString(QKeySequence.SequenceFormat.NativeText)
        self.atom_panel.edit_shortcut = native('edit')
        self.relax_panel.fix_shortcut = native('fix')
        self.relax_panel.update_guide()
        cancel = native('cancel')
        self.actions['quick_add'].setToolTip('添加一个原子' + (f'；{cancel} 取消放置' if cancel else ''))
        for name, (label, hint) in MODES.items():
            text = QKeySequence(keys[name]).toString(QKeySequence.SequenceFormat.NativeText)
            modifier = 'Command' if sys.platform == 'darwin' else 'Ctrl'
            hint = hint.replace('Ctrl', modifier).replace('Shift+', keys['molecule_modifier'] + '+')
            self.actions[name].setToolTip(label + (f' ({text})' if text else '') + '：' + hint)

    def configure_player(self):
        session = self.sessions[self.current_index]
        reader = session.trajectory
        count = 1 if reader is None else len(reader.offsets)
        ready = reader is not None and not session.indexing and not session.index_error
        message = "正在索引轨迹…" if session.indexing else session.index_error
        if reader is not None and not session.indexing and not session.index_error:
            message = getattr(reader, "notice", "")
        self.player.message.setToolTip(message)
        if reader is not None and reader.kind == "neb":
            message = f"NEB · {len(reader.images)} 个图像 · 同步查看优化帧"
        if reader is None:
            message = "当前为静态结构"
        if session.relaxation is not None:
            count, ready = len(session.relaxation.frames), True
            latest = session.relaxation.frames[-1]
            label = "预平衡" if session.relaxation.kind == "md" else "优化"
            message = f"{label} · {latest.number / latest.elapsed:.2f} 步/秒" if latest.elapsed else label + " · 初始结构"
        self.player.configure(count, session.frame, session.playback_speed, ready, message,
                              live=session.relaxation is not None and session.relaxation.running)
        self.player.setVisible(session.show_player)
        self.player.place()
        self.place_panels()

    def toggle_player(self, visible):
        self.sessions[self.current_index].show_player = visible
        self.player.setVisible(visible)
        self.player.place()
        self.place_panels()
        if not visible:
            self.player.pause()

    def set_playback_speed(self, number):
        self.sessions[self.current_index].playback_speed = number

    def index_ready(self, session, reader):
        if session not in self.sessions:
            return
        session.trajectory, session.indexing = reader, False
        if reader.kind == "xyz" and len(reader.offsets) == 1 and all(len(line.split()) == 4 for line in session.document.source.splitlines()[2:]):
            active = self.current_index >= 0 and self.sessions[self.current_index] is session
            if active:
                self._store_current()
            session.document = DraftDocument(deepcopy(session.document.structure), session.document.comment, unsaved=False)
            session.trajectory, session.show_player = None, False
            if active:
                self._activate_session(self.current_index)
        if self.current_index >= 0 and self.sessions[self.current_index] is session:
            self.configure_player()
            self.refresh()

    def index_failed(self, session, error):
        session.indexing, session.index_error = False, "索引失败：" + error
        if session in self.sessions and self.current_index >= 0 and self.sessions[self.current_index] is session:
            self.configure_player()

    def index_progress(self, session, count):
        if session in self.sessions and self.current_index >= 0 and self.sessions[self.current_index] is session:
            self.player.message.setText(f"正在索引… 已发现 {count} 帧")

    def index_finished(self, session):
        self.loaders.pop(session).deleteLater()
        if self.closing and not self.loaders and not self.relaxer.busy and not self.forcer.busy:
            self.close()

    def seek_frame(self, index, *, force=False):
        session = self.sessions[self.current_index]
        if session.relaxation is not None:
            session.follow_latest = False
            session.frame = index
            self.document.structure.positions = session.relaxation.frames[index].positions.copy()
            if session.relaxation.kind == "md" and session.relaxation.frames[index].temperature is not None:
                self.document.structure.set_temperature(session.relaxation.frames[index].temperature)
            self.scene.set_frame(self.document.structure)
            self.player.set_frame(index)
            self.refresh()
            return
        reader = session.trajectory
        if reader is None or session.indexing or index == session.frame and not force:
            self.player.set_frame(session.frame)
            return
        try:
            document = reader.frame(index)
        except (ValueError, OSError, KeyError, IndexError, ParseError, NotImplementedError) as error:
            self.player.pause()
            self.player.set_frame(session.frame)
            self.player.message.setText("读取失败：" + str(error))
            return
        old_symbols = self.document.structure.symbols
        if reader.kind == "neb":
            self.neb_workspace.prepare(index)
        session.frame, session.document = index, document
        self.document = document
        self.scene.selection = [value for value in self.scene.selection if value < len(document.structure.symbols)]
        self.scene.set_frame(document.structure)
        self.player.set_frame(index)
        self.player.message.clear()
        if old_symbols != document.structure.symbols:
            self.refresh()
        else:
            self._status()
        if isinstance(reader, ReportTrajectory):
            self.populate_results()
        if reader.kind == "neb":
            self.neb_workspace.sync()

    def browse_frame(self, index):
        self.player.pause()
        self.seek_frame(index)

    def save_data_header(self, kind, state):
        value = bytes(state.toBase64()).decode('ascii')
        self.settings.setdefault('data_headers', {})[kind] = value
        if kind == 'md':
            self.settings['data_header'] = value
        self.persist_settings()

    def settings_dialog(self, page=0):
        dialog = QDialog(self)
        dialog.setWindowTitle("设置")
        root = QVBoxLayout(dialog)
        groups = QTabWidget()
        groups.setObjectName("settings_groups")
        root.addWidget(groups)
        forms = []
        for title in SETTINGS_GROUPS[:3]:
            tab = QWidget()
            forms.append(QFormLayout(tab))
            groups.addTab(tab, title)
        editing, atoms, electrons = forms
        from .label_settings import LabelEditor, profiles
        from .shortcuts import ShortcutEditor
        labels = LabelEditor(profiles(self.settings))
        groups.addTab(labels, SETTINGS_GROUPS[3])
        shortcuts = ShortcutEditor(self.actions, bindings(self.settings.get('shortcuts')))
        groups.addTab(shortcuts, SETTINGS_GROUPS[4])
        environment = QWidget()
        environment_layout = QVBoxLayout(environment)
        environment_layout.addWidget(QLabel('选择 Python，按需下载模型和 GPU 环境。'))
        manage = QPushButton('Python 与 MLIP…')
        manage.setObjectName('manage_environment')
        def manage_environment():
            if not shortcuts.validate():
                groups.setCurrentWidget(shortcuts)
                return
            dialog.accept()
            QTimer.singleShot(0, self.show_environment)
        manage.clicked.connect(manage_environment)
        environment_layout.addWidget(manage)
        environment_layout.addStretch()
        groups.addTab(environment, SETTINGS_GROUPS[5])
        groups.setCurrentIndex(page)
        editing.addRow(QLabel("编辑模式的方向键沿屏幕上下左右移动原子。"))
        step = QDoubleSpinBox()
        step.setObjectName("move_step")
        step.setRange(0.01, 100)
        step.setDecimals(2)
        step.setSuffix(" Å")
        step.setSingleStep(0.01)
        step.setValue(self.move_step)
        editing.addRow("每步位移", step)
        boundary = QCheckBox('显示周期性边界')
        boundary.setObjectName('show_cell_boundary')
        boundary.setChecked(self.settings['show_cell_boundary'])
        atoms.addRow(boundary)
        orientation = QCheckBox('显示 xyz 方向轴')
        orientation.setObjectName('show_orientation_axes')
        orientation.setChecked(self.settings['show_orientation_axes'])
        atoms.addRow(orientation)
        bond = QDoubleSpinBox()
        bond.setObjectName("bond_scale")
        bond.setRange(0.1, 3)
        bond.setDecimals(2)
        bond.setSingleStep(0.05)
        bond.setValue(self.bond_scale)
        atoms.addRow("成键半径系数", bond)
        atoms.addRow(QLabel("距离 < 系数 ×（两原子的共价半径之和）时显示键。"))
        radius = QDoubleSpinBox()
        radius.setObjectName("bond_radius")
        radius.setRange(.01, 2)
        radius.setDecimals(2)
        radius.setSingleStep(.01)
        radius.setSuffix(" Å")
        radius.setValue(self.settings["bond_radius"])
        atoms.addRow("键半径", radius)
        contact = QDoubleSpinBox()
        contact.setObjectName("contact_factor")
        contact.setRange(0.05, 1.00)
        contact.setDecimals(2)
        contact.setSingleStep(0.01)
        contact.setValue(self.scene.contact_factor)
        atoms.addRow("过近距离检测系数", contact)
        atoms.addRow(QLabel("距离 < 系数 ×（两原子的范德华半径之和）时提示，不限制操作。"))
        rotation = QDoubleSpinBox()
        rotation.setObjectName("rotation_step")
        rotation.setRange(0.01, 180)
        rotation.setDecimals(2)
        rotation.setSuffix("°")
        rotation.setValue(self.rotation_step)
        editing.addRow("每次旋转角度", rotation)
        precision = QSpinBox()
        precision.setObjectName("measure_decimals")
        precision.setRange(0, 6)
        precision.setValue(self.settings["measure_decimals"])
        editing.addRow("测量小数位", precision)
        capacity = QSpinBox()
        capacity.setObjectName("clipboard_limit")
        capacity.setRange(1, 100)
        capacity.setValue(self.clipboard.limit)
        editing.addRow("剪贴板保留数量", capacity)
        ghost = QCheckBox("显示移动前虚影及位移")
        ghost.setObjectName("move_ghost")
        ghost.setChecked(self.scene.ghost.enabled)
        editing.addRow(ghost)
        opacity = QDoubleSpinBox()
        opacity.setObjectName("ghost_opacity")
        opacity.setRange(.01, 1)
        opacity.setDecimals(2)
        opacity.setSingleStep(.05)
        opacity.setValue(self.scene.ghost.opacity)
        decimals = QSpinBox()
        decimals.setObjectName("move_decimals")
        decimals.setRange(0, 6)
        decimals.setValue(self.scene.ghost.decimals)
        for label, field in (("虚影透明度", opacity), ("位移小数位", decimals)):
            field.setEnabled(ghost.isChecked())
            ghost.toggled.connect(field.setEnabled)
            editing.addRow(label, field)
        from .volume_panel import Number
        level_step = Number()
        level_step.setObjectName("charge_level_step")
        level_step.setRange(.000001, 100)
        level_step.setSingleStep(.01)
        level_step.setValue(self.settings["charge_level_step"])
        electrons.addRow("CHGCAR 等值步进", level_step)
        ranges = self.bond_ranges.copy()
        lengths = QPushButton("显示键长范围…")
        lengths.setObjectName("edit_bond_ranges")
        lengths.setEnabled(self.document is not None)

        def edit_ranges():
            editor = BondDialog(self.document.structure.symbols, bond.value(), ranges, dialog)
            def apply_ranges(value):
                nonlocal ranges
                ranges = value
                self.bond_ranges, self.bond_scale = dict(value), bond.value()
                self.settings.update(bond_ranges=self.bond_ranges, bond_scale=self.bond_scale)
                for scene in self.scenes():
                    scene.bond_ranges = dict(value)
                    scene.set_bond_scale(self.bond_scale)
                self.persist_settings()
            editor.applied.connect(apply_ranges)
            editor.finished.connect(lambda _result: dialog.reject() if self.closing else dialog.show())
            editor.finished.connect(editor.deleteLater)
            dialog.hide()
            editor.show()

        lengths.clicked.connect(edit_ranges)
        atoms.addRow(lengths)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        shortcuts.valid_changed.connect(buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        shortcuts.validate()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)
        dialog.resize(640, 560)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.move_step = step.value()
            self.bond_scale = bond.value()
            self.rotation_step, self.bond_ranges = rotation.value(), ranges
            self.update_rotation_hints()
            self.settings.update(move_step=self.move_step, bond_scale=self.bond_scale,
                                 show_cell_boundary=boundary.isChecked(),
                                 show_orientation_axes=orientation.isChecked(),
                                 bond_radius=radius.value(),
                                 contact_factor=contact.value(), charge_level_step=level_step.value(),
                                 rotation_step=self.rotation_step, bond_ranges=self.bond_ranges,
                                 clipboard_limit=capacity.value(), move_ghost=ghost.isChecked(),
                                 ghost_opacity=opacity.value(), move_decimals=decimals.value(), measure_decimals=precision.value())
            self.settings['shortcuts'] = shortcuts.values() | {'molecule_modifier': shortcuts.molecule.currentText()}
            self.settings['label_styles'] = labels.values
            self.settings['index_font'] = labels.values['index']['size']
            self.display_panel.font_size.setValue(self.settings['index_font'])
            self.apply_shortcuts()
            for scene in self.scenes():
                scene.set_cell_boundary(boundary.isChecked())
                scene.set_orientation_axes(orientation.isChecked())
                scene.configure_labels(profiles(self.settings))
                scene.labels.set_size(self.settings['index_font'])
                scene.bond_ranges = dict(ranges)
                scene.contact_factor = contact.value()
                scene.set_bond_scale(self.bond_scale)
                scene.set_bond_radius(radius.value())
                scene.ghost.configure(ghost.isChecked(), opacity.value(), decimals.value())
                scene.set_measure_decimals(precision.value())
                scene.plotter.render()
            self.volume_panels["charge"].set_level_step(level_step.value())
            self.clipboard.set_limit(capacity.value())
            self.persist_settings()
            self._status()

    def select_all(self):
        self.scene.set_selection(list(range(len(self.document.structure.symbols))))

    def show_environment(self):
        from .environment import EnvironmentDialog
        if self.environment is None:
            self.environment = EnvironmentDialog(self)
            self.environment.updated.connect(self.environments_changed)
        self.environment.show()
        self.environment.raise_()
        self.environment.activateWindow()

    def environments_changed(self):
        for controller in (self.relaxer, self.forcer):
            controller.gpu, controller.cpu = '检测中', '检测中'
            controller.detect()

    def move_atoms(self, indices: list[int], delta: np.ndarray):
        original = self.document.structure.positions[indices].copy()
        shift = self.scene.ghost.shift.copy() if self.scene.ghost.active else None
        self.history.apply(self.document, lambda: self.document.structure.translate(indices, delta),
                           selection=indices, label=f"移动 {len(indices)} 个原子")
        self.scene.selection = indices.copy()
        self.scene.set_structure(self.document.structure)
        self.scene.ghost.start(tuple(self.document.structure.symbols[index] for index in indices), original,
                              self.scene.paste_context(), shift, pbc=self.document.structure.pbc)
        self.scene.ghost.advance(delta)
        self.scene.plotter.render()
        self.refresh()

    def free_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("固定所选原子")
        layout = QFormLayout(dialog)
        layout.addRow(QLabel("勾选 = 固定（F）；未勾选 = 可动（T）。"))
        flags = self.document.structure.free
        selected = self.scene.selection.copy()
        initial = np.zeros((len(selected), 3), dtype=bool) if flags is None else ~flags[selected]
        boxes = []
        for index, axis in enumerate(("a", "b", "c")):
            box = QCheckBox("固定")
            box.setObjectName("fixed_" + axis)
            mixed = bool(initial[:, index].any() and not initial[:, index].all())
            box.setTristate(mixed)
            box.setCheckState(Qt.CheckState.PartiallyChecked if mixed else
                              Qt.CheckState.Checked if initial[:, index].all() else Qt.CheckState.Unchecked)
            layout.addRow(axis, box)
            boxes.append(box)
        all_fixed = QPushButton("全固定")
        all_fixed.setObjectName("all_fixed")

        def check_all():
            for box in boxes:
                box.setCheckState(Qt.CheckState.Checked)

        all_fixed.clicked.connect(check_all)
        layout.addRow(all_fixed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            fixed = [None if box.checkState() == Qt.CheckState.PartiallyChecked else box.isChecked() for box in boxes]
            self.history.apply(self.document, lambda: self.document.structure.set_fixed(selected, fixed),
                               selection=selected, label=f"修改 {len(selected)} 个原子的约束")
            self.scene.set_structure(self.document.structure)
            self.refresh()

    def fix_atoms(self):
        selected = self.scene.selection.copy()
        flags = self.document.structure.free
        fixed = not (flags is not None and not flags[selected].any())
        self.history.apply(self.document, lambda: self.document.structure.set_fixed(selected, [fixed] * 3),
                           selection=selected, label=("固定" if fixed else "解除固定") + f" {len(selected)} 个原子")
        self.scene.set_structure(self.document.structure)
        self.refresh()

    def add_dialog(self):
        structure = self.document.structure
        symbol, position = "H", np.zeros(3)
        if len(self.scene.selection) == 1:
            index = self.scene.selection[0]
            symbol, position = structure.symbols[index], structure.positions[index]
        dialog = AtomDialog(symbol, structure.cell, position, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_atom(dialog.fields.element.symbol, dialog.fields.coordinates.position)

    def build_dialog(self, kind):
        module = 'vbuild.recell_dialog' if kind == 'recell' else 'vbuild.build_dialog'
        if not self.feature_ready(module, lambda: self.build_dialog(kind)):
            return
        from .build_dialog import BuildDialog
        self.scene.cancel_gesture()
        self.scene.cancel_paste()
        structure = self.document.structure
        camera = self.scene.camera_snapshot()
        selection = self.scene.selection.copy()
        if kind == 'recell':
            from .recell_dialog import RecellDialog
            references = [(session.path.parent.name + ' / ' + session.path.name, session.document.structure, session.path)
                          for session in self.sessions if session.document is not self.document and session.document.structure.cell is not None]
            dialog = RecellDialog(structure, references, self)
        else:
            dialog = BuildDialog(kind, structure, self)
        self.builder = (dialog, structure, camera, selection, self.scene.mode)
        self.scene.previewing = True
        self.scene.set_mode("rotate")
        self.actions["rotate"].setChecked(True)
        self.tabs.setEnabled(False)

        def preview(result):
            current_camera = self.scene.camera_snapshot()
            self.scene.selection = []
            self.scene.set_structure(result.structure)
            self.scene.restore_camera(current_camera, fit_clipping=True)

        if kind != 'recell':
            dialog.preview_requested.connect(preview)
        dialog.finished.connect(self.finish_build)
        dialog.setModal(False)
        dialog.show()
        self.refresh()

    def finish_build(self, code):
        dialog, structure, camera, selection, mode = self.builder
        preview_camera = self.scene.camera_snapshot()
        self.builder = None
        self.scene.previewing = False
        self.scene.set_mode(mode)
        self.actions[mode].setChecked(True)
        self.tabs.setEnabled(True)
        kind = dialog.kind
        accepted = code == QDialog.DialogCode.Accepted
        if accepted and kind == 'recell':
            preview_camera = dialog.previews.current.scene.camera_snapshot()
            self.scene.selection = selection
            self.scene.set_structure(structure)
            self.scene.restore_camera(camera)
            self._store_current()
            source = self.sessions[self.current_index]
            suffix = '_unit.vasp' if dialog.output.currentData() == 'unit' else '_cell.vasp'
            if dialog.prepared_reference is not None:
                reference = dialog.reference.currentData()
                document = DraftDocument(deepcopy(dialog.prepared_reference.structure))
                path = reference.path.with_name(reference.path.stem + suffix)
                self.sessions.append(Session(document, path, show_cell_info=source.show_cell_info))
                self.add_session_tab()
            result = dialog.prepared
            document = DraftDocument(deepcopy(result.structure))
            path = self.path.with_name(self.path.stem + suffix)
            indices = np.flatnonzero(np.isin(result.sources, selection)).tolist()
            self.sessions.append(Session(document, path, selection=indices, camera=preview_camera, mode=mode,
                                         show_cell_info=source.show_cell_info, representation=source.representation,
                                         view_style=deepcopy(source.view_style), show_bonds=source.show_bonds))
            self.activate_document(self.add_session_tab())
        elif accepted and kind == "slab":
            result = dialog.prepared
            snapshot = replace(self.document, structure=result.structure)
            document = PoscarDocument.from_bytes(snapshot.to_bytes())
            document.structure.temperature = document.original_temperature = structure.temperature
            document.unsaved = True
            indices = np.flatnonzero(np.isin(result.sources, selection)).tolist()
            suffix = "_h" + str(dialog.indices[0].value()) + "k" + str(dialog.indices[1].value()) + "l" + str(dialog.indices[2].value()) + ".vasp"
            self.scene.selection = selection
            self.scene.set_structure(structure)
            self.scene.restore_camera(camera)
            self._store_current()
            path = self.path.with_name(self.path.stem + suffix)
            self.sessions.append(Session(document, path, selection=indices, camera=preview_camera))
            self.activate_document(self.add_session_tab())
        elif accepted:
            result = dialog.prepared
            indices = np.flatnonzero(np.isin(result.sources, selection)).tolist()
            self.history.apply(self.document, lambda: result.apply(structure, fix_unmapped=dialog.fix_unmapped.isChecked()), selection=selection,
                               selection_after=lambda _value: indices,
                               label='改胞' if kind == 'recell' else "扩胞 / 增加真空" if dialog.extra_vacuum.value() > 0 else "扩胞")
            self.scene.selection = indices
            self.scene.set_structure(structure)
            self.scene.restore_camera(preview_camera)
        else:
            self.scene.selection = selection
            self.scene.set_structure(structure)
            self.scene.restore_camera(camera)
        dialog.deleteLater()
        self.refresh()
        self.scene.plotter.interactor.setFocus()

        if self.waiting_water_close:
            self.waiting_water_close = False
            QTimer.singleShot(0, self.close)

    def new_relax(self):
        document = self.initial_document('opt')
        if document is None:
            return
        self._store_current()
        structure = document.structure
        original_free = None if structure.free is None else structure.free.copy()
        structure.set_free(list(range(len(structure.symbols))), [False, False, False])
        document.unsaved = True
        state = Relaxation(original_free=original_free)
        state.reset(structure.positions)
        stem = ('structure_' if self.sessions[self.current_index].volumes else '') + self.path.stem
        path = self.path.with_name(stem + "_opt.vasp")
        self.sessions.append(Session(document, path, show_fixed=True, show_player=True, relaxation=state))
        self.activate_document(self.add_session_tab())
        self.relaxer.detect()

    def open_neb(self):
        if not self.feature_ready("vbuild.neb_ui", self.open_neb):
            return
        if self.neb_workspace is None:
            from .neb_ui import NebWorkspace
            self.neb_workspace = NebWorkspace(self)
        self.neb_workspace.open()

    def choose_initial_frame(self, session, kind='md'):
        dialog = QMessageBox(self)
        title = 'MLIP 预平衡' if kind == 'md' else 'MLIP 结构优化'
        dialog.setWindowTitle(title + '初始结构')
        dialog.setText('以哪一帧作为' + title + '初始结构？')
        first = dialog.addButton("第一帧", QMessageBox.ButtonRole.AcceptRole)
        last = dialog.addButton("最后一帧", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(last)
        dialog.exec()
        if dialog.clickedButton() is first:
            return 0
        if dialog.clickedButton() is last:
            return len(session.relaxation.frames) - 1 if session.relaxation is not None else len(session.trajectory.offsets) - 1
        return None

    def initial_document(self, kind):
        source = self.sessions[self.current_index]
        index = 0
        if source.trajectory is not None or source.relaxation is not None and len(source.relaxation.frames) > 1:
            index = self.choose_initial_frame(source, kind)
            if index is None:
                return
        try:
            document = deepcopy(source.document if source.trajectory is None else source.trajectory.frame(index))
        except (ValueError, OSError) as error:
            QMessageBox.critical(self, "初始帧无法读取", str(error))
            return
        if source.relaxation is not None:
            step = source.relaxation.frames[index]
            document.structure.positions = step.positions.copy()
            if source.relaxation.kind == "opt":
                flags = source.relaxation.original_free
                document.structure.free = None if flags is None else flags.copy()
            if step.temperature is not None:
                document.structure.set_temperature(step.temperature)
        return document

    def new_md(self):
        source = self.sessions[self.current_index]
        document = self.initial_document('md')
        if document is None:
            return
        self._store_current()
        flags = document.structure.free
        state = Relaxation(kind="md", max_steps=2000, status="准备预平衡",
                           original_free=None if flags is None else flags.copy())
        state.reset(document.structure.positions)
        if isinstance(document, XyzDocument):
            document.dirty = True
        else:
            document.unsaved = True
        stem = ("structure_" if source.volumes else "") + self.path.stem
        path = self.path.with_name(stem + "_md" + (".xyz" if document.structure.cell is None else ".vasp"))
        self.sessions.append(Session(document, path, show_fixed=True, show_player=True, relaxation=state))
        self.activate_document(self.add_session_tab())
        self.relaxer.detect()

    def save_model_path(self, path):
        self.settings["mace_path"] = str(self.relaxer.paths["mp-0"])
        self.settings["mace_model"] = self.relaxer.model
        self.settings["mace_paths"] = {name: str(value) for name, value in self.relaxer.paths.items()}
        self.persist_settings()

    def start_relax(self):
        session = self.sessions[self.current_index]
        if session.relaxation.kind == "md":
            try:
                session.relaxation.walls.validate()
            except ValueError as error:
                QMessageBox.warning(self, "墙参数无效", str(error))
                return
        self.scene.cancel_gesture()
        self.scene.cancel_paste()
        self.player.pause()
        session.frame = 0
        session.follow_latest = False
        session.history = self.history = History()
        self.relaxer.start(session, deepcopy(self.document.structure))
        self.configure_player()
        self.player.start()
        self.scene.set_mode("rotate")
        self.actions["rotate"].setChecked(True)
        self.refresh()

    def relax_changed(self):
        if self.document is not None and self.sessions[self.current_index].relaxation is not None:
            self.configure_player()
        self.refresh()
        if self.closing and not self.loaders and not self.relaxer.busy and not self.forcer.busy:
            self.close()

    def relax_frames(self, session, frames):
        state = session.relaxation
        active = session in self.sessions and self.current_index >= 0 and self.sessions[self.current_index] is session
        follow = session.follow_latest and not (active and self.player.timer.isActive())
        initial = frames[0].number == 0
        if initial:
            state.frames[0] = frames[0]
            frames = frames[1:]
        state.frames.extend(frames)
        if session not in self.sessions:
            return
        shown = follow or (initial and session.frame == 0)
        if shown:
            if follow:
                session.frame = len(state.frames) - 1
            step = state.frames[session.frame]
            session.document.structure.positions = step.positions.copy()
            if state.kind == "md" and step.temperature is not None:
                session.document.structure.set_temperature(step.temperature)
        if active:
            if shown:
                self.scene.set_frame(self.document.structure)
            self.configure_player()
            self.populate_results()
            self._status()

    def editable(self):
        if self.document is None or self.document.restart or self.builder is not None:
            return False
        session = self.sessions[self.current_index]
        state = session.relaxation
        return not session.readonly and session.trajectory is None and (state is None or not state.running and session.frame == len(state.frames) - 1)

    def add_atom(self, symbol: str, position: np.ndarray):
        structure = self.document.structure
        index = self.history.apply(self.document, lambda: structure.add_atom(symbol, position),
                                   selection=self.scene.selection, selection_after=lambda index: [index],
                                   label=f"添加 {symbol} 原子")
        self.scene.selection = [index]
        self.scene.set_structure(structure)
        self.refresh()

    def remove_atoms(self):
        structure = self.document.structure
        self.history.apply(self.document, lambda: structure.remove_atoms(self.scene.selection.copy(), allow_empty=isinstance(self.document, DraftDocument)),
                           selection=self.scene.selection, selection_after=lambda _result: [],
                           label=f"删除 {len(self.scene.selection)} 个原子")
        self.scene.selection = []
        self.scene.set_structure(structure)
        self.refresh()

    def wrap_atoms(self):
        self.scene.cancel_gesture()
        self.scene.cancel_paste()
        structure = self.document.structure
        factor, ranges = self.scene.visual.bond_parameters(self.bond_scale, self.bond_ranges)
        result = wrap_molecules(structure, factor, ranges)
        if not result.changed:
            self.statusBar().showMessage("原子和分子已归位，无需修改。")
            return
        self.history.apply(self.document, lambda: result.apply(structure),
                           selection=self.scene.selection, selection_after=lambda _value: result.changed.copy(),
                           label=f"周期归位（{len(result.changed)} 个原子）")
        self.scene.selection = result.changed.copy()
        self.scene.set_structure(structure)
        self.refresh()
        message = f"已归位 {len(result.changed)} 个原子、{result.groups} 个有限分子/孤立原子；完整分子中心位于晶胞内。"
        if result.networks:
            message += f" {result.networks} 个周期连续网络按原子选择等价周期像。"
        self.statusBar().showMessage(message)

    def populate_history_menu(self, name):
        menu = self.history_menus[name]
        cursor = self.history.cursor
        indices = range(cursor - 1, -1, -1) if name == "undo" else range(cursor, len(self.history.entries))
        entries = []
        for count, index in enumerate(indices, 1):
            target = index if name == "undo" else index + 1
            entries.append((f"{count}. {self.history.entries[index].label}", target))
        menu.populate(entries, self.restore_history)

    def restore_history(self, target):
        self.scene.cancel_gesture()
        self.scene.cancel_paste()
        self.scene.selection = self.history.seek(self.document, target)
        self.scene.set_structure(self.document.structure)
        self.refresh()

    def undo(self):
        if self.history.cursor:
            self.restore_history(self.history.cursor - 1)

    def redo(self):
        if self.history.cursor < len(self.history.entries):
            self.restore_history(self.history.cursor + 1)

    def edit_atom(self, symbol: str, position: np.ndarray):
        structure = self.document.structure
        index = self.scene.selection[0]
        if structure.symbols[index] == symbol and np.array_equal(structure.positions[index], position):
            return
        new_index = self.history.apply(self.document, lambda: structure.set_atom(index, symbol, position),
                                       selection=[index], selection_after=lambda value: [value], label="修改原子元素 / 坐标")
        self.scene.selection = [new_index]
        self.scene.set_structure(structure)
        self.refresh()
        self.scene.plotter.interactor.setFocus()

    def change_atom_element(self, symbol):
        coordinates = self.atom_panel.fields.coordinates
        position = coordinates.position.copy()
        fraction = None if coordinates.fraction is None else coordinates.fraction.copy()
        pending = [(field, field.text()) for fields in coordinates.fields.values() for field in fields]
        index = self.scene.selection[0]
        self.edit_atom(symbol, self.document.structure.positions[index].copy())
        coordinates.position, coordinates.fraction = position, fraction
        for field, text in pending:
            with QSignalBlocker(field):
                field.setText(text)
        coordinates.valid_changed.emit(all(field.hasAcceptableInput() for field, _text in pending if field.isEnabled()))

    def copy_atoms(self):
        structure = self.document.structure
        indices = self.scene.selection
        self.clipboard.copy(tuple(structure.symbols[index] for index in indices), structure.positions[indices])

    @property
    def copied(self):
        return self.clipboard.entries[0] if self.clipboard.entries else None

    def clipboard_changed(self):
        self.settings["clipboard_limit"] = self.clipboard.limit
        self._status()

    def show_clipboard(self):
        if self.clip_panel is None:
            self.clip_panel = ClipboardPanel(self.clipboard, self)
            self.clip_panel.chosen.connect(self.paste_clip)
        self.clip_panel.populate()
        self.clip_panel.show()
        self.clip_panel.raise_()
        self.clip_panel.activateWindow()

    def begin_paste(self):
        self.paste_clip(0)

    def paste_clip(self, index):
        self.set_mode("edit")
        self.paste_selection = self.scene.selection.copy()
        self.paste_index = index
        self.scene.start_paste(*self.clipboard.entries[index])

    def cycle_paste(self, step):
        self.paste_index = 0 if self.paste_index is None else (self.paste_index + step) % len(self.clipboard.entries)
        center = self.scene.paste.positions.mean(axis=0)
        self.scene.start_paste(*self.clipboard.entries[self.paste_index], anchor=center)

    def paste_atoms(self, positions: np.ndarray):
        structure = self.document.structure
        symbols = self.scene.paste.symbols
        indices = self.history.apply(self.document, lambda: structure.add_group(symbols, positions),
                                     selection=self.paste_selection, selection_after=lambda values: values.copy(),
                                     label=f"粘贴 {len(symbols)} 个原子")
        self.scene.selection = indices
        self.scene.set_structure(structure)
        self.refresh()

    def refresh(self):
        self.update_bader()
        self.update_moments()
        loaded = self.document is not None
        populated = loaded and bool(self.document.structure.symbols)
        trajectory = loaded and self.sessions[self.current_index].trajectory is not None
        editable = self.editable()
        state = self.sessions[self.current_index].relaxation if loaded else None
        rendering = loaded and self.sessions[self.current_index].render_style is not None
        self.actions["style"].setEnabled(loaded)
        self.actions["show_forces"].setEnabled(populated)
        if not loaded:
            self.forcer.stop()
            for family, panel in self.volume_panels.items():
                panel.clear()
                self.actions["show_" + family].setEnabled(False)
                self.actions["show_" + family].setChecked(False)
            self.force_panel.hide()
            with QSignalBlocker(self.actions["show_forces"]):
                self.actions["show_forces"].setChecked(False)
        self.actions["style_cycle"].setEnabled(loaded)
        self.actions["render"].setEnabled(populated and self.builder is None and (state is None or not state.running))
        self.actions["render_controls"].setEnabled(rendering)
        with QSignalBlocker(self.actions["render_controls"]):
            self.actions["render_controls"].setChecked(rendering and self.sessions[self.current_index].show_render)
        if rendering and self.render_panel is not None:
            session = self.sessions[self.current_index]
            if self.sync_render_atoms(session):
                self.scene.set_look(session.representation, session.render_style, True, overrides=session.render_atoms)
                self.render_panel.status.setText("原子列表已改变，单原子外观已恢复为元素设置。")
            self.render_panel.update_elements(self.document.structure.symbols)
        if not loaded:
            self.style_panel.hide()
            if self.render_panel is not None:
                self.render_panel.hide()
        if state is not None and not state.running and len(state.frames) == 1 and not np.array_equal(state.frames[0].positions, self.document.structure.positions):
            state.reset(self.document.structure.positions)
        self.actions["open"].setEnabled(self.builder is None)
        self.actions["neb"].setEnabled(self.builder is None)
        self.actions["new"].setEnabled(self.builder is None)
        for name in ("new_blank", "new_build"):
            self.actions[name].setEnabled(self.builder is None)
        self.actions["save"].setEnabled(populated and self.builder is None)
        self.actions["export_xdatcar"].setEnabled(populated
                                                  and (state is not None or trajectory) and not self.sessions[self.current_index].indexing)
        self.actions["relax"].setEnabled(populated and not self.document.restart and self.builder is None
                                        and self.document.structure.cell is not None
                                        and not self.sessions[self.current_index].indexing
                                        and not self.sessions[self.current_index].index_error
                                        and (state is None or not state.running))
        self.actions["md"].setEnabled(populated and not self.document.restart and self.builder is None
                                     and not self.sessions[self.current_index].indexing
                                     and not self.sessions[self.current_index].index_error
                                     and (state is None or not state.running))
        self.actions["all"].setEnabled(loaded and self.builder is None)
        self.actions["close"].setEnabled(self.tabs.count() > 0)
        for name in ("show_contacts", "show_fixed", "show_periodic", "show_indices", "show_player"):
            self.actions[name].setEnabled(loaded and self.builder is None)
        self.actions["show_periodic"].setEnabled(loaded and self.document.structure.cell is not None and self.builder is None)
        self.actions['show_cell_info'].setEnabled(loaded and self.document.structure.cell is not None)
        with QSignalBlocker(self.actions['show_cell_info']):
            self.actions['show_cell_info'].setChecked(loaded and self.scene.show_cell_info)
        grid_ready = loaded and (populated or self.document.structure.cell is not None)
        self.actions['show_grid'].setEnabled(grid_ready)
        with QSignalBlocker(self.actions['show_grid']):
            self.actions['show_grid'].setChecked(loaded and self.scene.grid_options['show'])
        if grid_ready:
            self.grid_panel.populate(self.scene.grid_options, self.document.structure, self.scene.period_counts())
        else:
            self.grid_panel.hide()
        if loaded:
            self.display_panel.populate(self.document.structure.symbols, self.scene.periods,
                                        self.scene.hidden_elements, self.scene.show_periodic, self.scene.show_indices)
            with QSignalBlocker(self.display_panel.font_size):
                self.display_panel.font_size.setValue(self.scene.labels.size)
        else:
            self.display_panel.hide()
        for name in ("next", "previous"):
            self.actions[name].setEnabled(self.tabs.count() > 1 and self.builder is None)
        inspect_water = False
        if self.builder is not None:
            from .molecules import WaterDialog
            inspect_water = isinstance(self.builder[0], WaterDialog)
        for name in MODES:
            self.actions[name].setEnabled(loaded and (self.builder is None or name == "rotate" or (inspect_water and name != "edit")))
        self.actions["edit"].setEnabled(editable)
        if state is not None:
            self.actions["edit"].setEnabled(editable and len(state.frames) == 1 and self.document.structure.cell is not None)
        self.actions["add"].setEnabled(editable and state is None)
        self.actions["quick_add"].setEnabled(editable and state is None)
        self.actions["add_molecule"].setEnabled(editable and state is None)
        self.actions["slab"].setEnabled(populated and editable and state is None and all(self.document.structure.pbc))
        self.actions['recell'].setEnabled(self.actions['slab'].isEnabled())
        self.actions["wrap"].setEnabled(populated and editable and (state is None or len(state.frames) == 1)
                                       and any(self.document.structure.pbc))
        self.actions["undo"].setEnabled(editable and self.history.cursor > 0)
        self.actions["redo"].setEnabled(editable and self.history.cursor < len(self.history.entries))
        movable = editable and (state is None or free_directions(len(self.document.structure.symbols), self.document.structure.free).any())
        if loaded:
            self.relaxer.set_structure(self.document.structure)
        self.relax_panel.populate(state, movable)
        self.relax_panel.exports["trajectory"].setEnabled(self.actions["export_xdatcar"].isEnabled())
        self.populate_results()
        self.update_walls()
        title = "VBuild · 结构建模"
        if loaded:
            session = self.sessions[self.current_index]
            session.document, session.path, session.history = self.document, self.path, self.history
            title += " · " + self.path.name + (" *" if self.document.dirty else "")
        for index, session in enumerate(self.sessions):
            tab = self.tab_for(session)
            name = (session.trajectory.path.name + " · NEB" if session.trajectory is not None and session.trajectory.kind == "neb" else session.path.name)
            if self.tabs.double:
                name = session.path.parent.name + "\n" + name
            self.tabs.setTabText(tab, name + (" *" if session.document.dirty else ""))
            self.tabs.setTabToolTip(tab, str(session.path))
        for page in self.comparisons:
            rows, columns = page.shape
            self.tabs.setTabText(self.tab_for(page), ("分页对比\n" if self.tabs.double else "") + f"对比 {page.number} · {rows}×{columns}")
        self.setWindowTitle(title)
        if self.comparison is not None:
            self.comparison.sync()
        self._status()

    def _status(self, _indices=None):
        trajectory = self.document is not None and self.sessions[self.current_index].trajectory is not None
        editable = self.editable()
        selected = self.scene.selection
        structure = self.scene.structure
        if self.document is not None and self.sessions[self.current_index].render_style is not None:
            self.render_panel.set_atom(structure.symbols, selected, self.sessions[self.current_index].render_atoms)
        placing = self.scene.paste.active
        state = self.sessions[self.current_index].relaxation if self.document is not None else None
        editing = editable and self.scene.mode == "edit" and (state is None or len(state.frames) == 1)
        selecting = self.scene.mode == "select"
        self.actions["copy"].setEnabled(self.document is not None and self.builder is None and
                                       (selecting or editing) and bool(selected) and not placing)
        self.actions["paste"].setEnabled(editable and (selecting or editing) and self.copied is not None and state is None)
        self.actions["clipboard"].setEnabled(bool(self.clipboard.entries) and self.builder is None)
        if self.clip_panel is not None:
            self.clip_panel.paste.setEnabled(self.actions["quick_add"].isEnabled() and bool(self.clipboard.entries))
        self.actions["cancel"].setEnabled(placing)
        self.actions["reset"].setEnabled(self.document is not None and self.scene.mode != "edit")
        for name in ("view_a", "view_b", "view_c"):
            self.actions[name].setEnabled(self.document is not None and self.scene.mode != "edit")
        group = len(selected) > 2
        rotate_group_enabled = editable and (state is None or len(state.frames) == 1)
        for name in ROTATIONS:
            self.actions["turn_" + name].setEnabled(self.document is not None and not placing and
                (rotate_group_enabled if group else self.scene.mode != "edit"))
        for name in ("view_a", "view_b", "view_c"):
            self.actions[name].setEnabled(self.document is not None and self.document.structure.cell is not None and self.scene.mode != "edit")
        for name in ARROWS:
            self.actions[name].setEnabled(editing and ((bool(selected) and not placing) or
                                          (placing and name in ("up", "down") and bool(self.clipboard.entries))))
        self.actions["free"].setEnabled(editable and bool(selected) and not placing and self.document.structure.cell is not None)
        self.actions["fix"].setEnabled(editable and bool(selected) and not placing and self.document.structure.cell is not None)
        self.actions["remove"].setEnabled(editable and state is None and bool(selected) and not placing
                                          and (isinstance(self.document, DraftDocument) or len(selected) < len(self.document.structure.symbols)))
        if state is not None:
            self.relax_panel.populate(state, editable and free_directions(len(self.document.structure.symbols), self.document.structure.free).any())
        if (editing or self.scene.mode == "select") and len(selected) == 1 and not placing:
            index = selected[0]
            self.atom_panel.populate(index, structure.symbols[index], structure.cell, structure.positions[index], editable=editing)
        else:
            self.atom_panel.hide()
        self.place_panels()
        if self.document is None:
            key = self.actions['open'].shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            self.statusBar().showMessage('拖入结构文件' + (f'，或按 {key} 打开结构' if key else ''))
            return
        count = len(structure.symbols)
        label, hint = MODES[self.scene.mode]
        hint = hint.replace('Shift+', self.keymap['molecule_modifier'] + '+').replace('Ctrl', 'Command' if sys.platform == 'darwin' else 'Ctrl')
        mode = label + "：" + hint
        if self.scene.mode == "edit":
            mode += f" {self.move_step:g} Å"
        if placing:
            cancel = self.actions['cancel'].shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            mode = '粘贴：移动鼠标平移整组；左键放置' + (f'；{cancel} 取消' if cancel else '') + '；↑↓ 切换复制历史'
        if self.scene.measurement_text:
            mode = label + " " + self.scene.measurement_text
        temperature = self.document.structure.temperature
        velocity = "" if temperature is None else f"温度 {temperature:.2f} K"
        if self.document.structure.temperature_pending:
            velocity = "Direct 速度：导出时可重新赋予温度"
        parts = [composition_text(structure.symbols), f'{count} 原子', f'已选 {len(selected)}']
        if velocity:
            parts.append(velocity)
        text = ' | '.join([*parts, mode])
        if len(selected) == 1:
            index = selected[0]
            flags = structure.free
            flag_text = "未设置" if flags is None else " ".join("T" if flag else "F" for flag in flags[index])
            text += f" | #{index + 1} {structure.symbols[index]} 约束 {flag_text}"
        if self.document.restart:
            text += " | 含续算/额外数据：只读"
        if trajectory:
            session = self.sessions[self.current_index]
            text += f" | 轨迹帧 {session.frame + 1}/{len(session.trajectory.offsets)}"
        if state is not None:
            text += f" | 优化帧 {self.sessions[self.current_index].frame + 1}/{len(state.frames)} | {state.status}"
        if self.scene.show_contacts:
            if len(self.scene.contact_rows):
                first, second, distance, limit = self.scene.contact_rows[0]
                symbols = self.scene.structure.symbols
                warning = f"{symbols[int(first)]} {int(first)+1} 原子与 {symbols[int(second)]} {int(second)+1} 原子距离过近：{distance:.3f} Å < {limit:.3f} Å"
                text = warning + (f"（共 {len(self.scene.contact_rows)} 对）" if len(self.scene.contact_rows) > 1 else "") + " | " + text
            else:
                text = "距离检测：未发现过近原子 | " + text
            if self.scene.contact_missing:
                text = "缺少范德华半径，未检查：" + ", ".join(self.scene.contact_missing) + " | " + text
        self.statusBar().showMessage(text)

    def _drop_paths(self, mime) -> list[Path]:
        if self.builder is not None:
            return []
        urls = mime.urls()
        if not urls or not all(url.isLocalFile() for url in urls):
            return []
        paths = [Path(url.toLocalFile()) for url in urls]
        return paths if all(path.is_file() for path in paths) else []

    def dragEnterEvent(self, event):
        if self._drop_paths(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._drop_paths(event.mimeData())
        if paths:
            for path in paths:
                self.open_path(path)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def eventFilter(self, watched, event):
        if self.builder is not None and watched is not self.scene.plotter.interactor and event.type() in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove, QEvent.Type.Wheel):
            return True
        if watched is self.scene.plotter.interactor:
            if event.type() == QEvent.Type.Resize:
                self.place_panels()
            if event.type() == QEvent.Type.DragEnter:
                self.dragEnterEvent(event)
                return True
            if event.type() == QEvent.Type.DragMove:
                self.dragEnterEvent(event)
                return True
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
                return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.preload_started:
            self.preload_started = True
            QTimer.singleShot(100, self.preload)

    def preload(self):
        if self.preload_task is not None:
            return
        task = start_warmup()
        self.preload_task = task
        task.progress.connect(lambda name: self.load_status.setText("正在载入 " + name))
        task.loaded.connect(self.feature_loaded)
        task.completed.connect(lambda: self.load_status.setText("载入完成"))
        task.failed.connect(lambda error, _trace: self.load_status.setText("载入失败：" + str(error)))
        self.load_status.setText("载入失败：" + str(task.error) if task.error is not None else
                                 "正在载入 " + task.current if task.isRunning() else "载入完成")

    def feature_ready(self, module, callback):
        self.preload()
        task = self.preload_task
        if module in task.ready:
            return True
        if task.error is not None:
            QMessageBox.information(self, "功能载入失败", str(task.error))
            return False
        session = self.sessions[self.current_index] if self.current_index >= 0 else None
        self.pending_features = [request for request in self.pending_features if request[0] != module]
        self.pending_features.append((module, callback, session))
        self.statusBar().showMessage("功能载入中，完成后自动打开，请稍候…", 4000)
        return False

    def feature_loaded(self, module):
        waiting, self.pending_features = self.pending_features, []
        session = self.sessions[self.current_index] if self.current_index >= 0 else None
        for name, callback, source in waiting:
            if name != module:
                self.pending_features.append((name, callback, source))
            elif not self.closing and source is session:
                QTimer.singleShot(0, callback)

    def close(self):
        return self.window().close() if self.parentWidget() is not None else super().close()

    def closeEvent(self, event):
        if self.environment is not None and self.environment.running:
            QMessageBox.information(self, '安装仍在进行', '请先在 Python 与 MLIP 窗口等待完成，或取消下载／安装。')
            event.ignore()
            return
        self.player.pause()
        if self.builder is not None:
            self.builder[0].reject()
            if self.builder is not None:
                self.waiting_water_close = True
                event.ignore()
                return
        if not self.closing and not self.confirm_close_all():
            event.ignore()
            return
        self.closing = True
        self.forcer.stop()
        if self.loaders or self.relaxer.busy or self.forcer.busy:
            for task in self.loaders.values():
                task.requestInterruption()
            self.relaxer.stop()
            event.ignore()
            return
        self.settings["geometry"] = bytes(self.window().saveGeometry().toBase64()).decode("ascii")
        self.persist_settings()
        self.clipboard.changed.disconnect(self.clipboard_changed)
        for session in self.sessions:
            if session.trajectory is not None and session.trajectory.kind == "neb":
                session.trajectory.close_cache()
        if self.clip_panel is not None:
            self.clip_panel.dispose()
        for page in self.comparisons:
            page.close_views()
        self.single_scene.plotter.close()
        event.accept()

    def close_choice(self, session: Session, *, all_windows=False):
        name = session.path.parent.name + "/" + session.path.name
        dialog = CloseDialog(name, all_windows=all_windows, parent=self)
        dialog.exec()
        return dialog.choice, dialog.apply_all.isChecked()

    def save_session(self, index: int) -> bool:
        self.tabs.setCurrentIndex(self.tab_for(self.sessions[index]))
        return self.save_as()

    def confirm_close_all(self) -> bool:
        self._store_current()
        choice_for_all = None
        for index, session in enumerate(self.sessions):
            if not session.document.dirty:
                continue
            choice = choice_for_all
            if choice is None:
                choice, apply_all = self.close_choice(session, all_windows=True)
                if apply_all:
                    choice_for_all = choice
            if choice == "cancel" or choice == "save" and not self.save_session(index):
                return False
        return True

    def close_tab(self, index: int):
        if self.builder is not None:
            self.builder[0].reject()
            if self.builder is not None:
                return
        page = self.tabs.tabData(index)
        if isinstance(page, Session) and page.document.dirty:
            session = page
            choice, _all = self.close_choice(session)
            if choice == "cancel" or choice == "save" and not self.save_session(self.sessions.index(session)):
                return
        self._store_current()
        active_page = self.tabs.tabData(self.tabs.currentIndex())
        if isinstance(page, Session):
            if page in self.loaders:
                self.loaders[page].requestInterruption()
            if self.relaxer.owner is page:
                self.relaxer.stop()
            for comparison in self.comparisons:
                for cell in comparison.cells:
                    if cell.source is page:
                        cell.clear()
            if page.trajectory is not None and page.trajectory.kind == "neb":
                page.trajectory.close_cache()
            self.sessions.remove(page)
        else:
            if self.comparison is page:
                self.change_scene(self.single_scene)
                self.comparison = None
            self.comparisons.remove(page)
            self.viewport.removeWidget(page)
            page.close_views()
            page.hide()
            page.deleteLater()
        with QSignalBlocker(self.tabs):
            self.tabs.removeTab(index)
            new_index = self.tab_for(active_page) if active_page is not page else min(index, self.tabs.count()-1)
            self.tabs.setCurrentIndex(new_index)
        self.current_index = -1
        self.document = None
        if new_index >= 0:
            self.switch_document(new_index)
        else:
            self.comparison = None
            self.viewport.setCurrentWidget(self.single_scene)
            self.change_scene(self.single_scene)
            self.clear_document()
