"""Independent structures share one viewport, with their own edit and view state."""

from dataclasses import dataclass, field
from pathlib import Path

from .history import History
from .draft import DraftDocument
from .poscar import PoscarDocument
from .trajectory import Trajectory, XyzDocument
from .relax import Relaxation


@dataclass(eq=False)
class Session:
    document: PoscarDocument | XyzDocument | DraftDocument
    path: Path
    history: History = field(default_factory=History)
    selection: list[int] = field(default_factory=list)
    mode: str = "rotate"
    measurement: tuple | None = None
    camera: object | None = None
    show_fixed: bool = False
    show_cell_info: bool = False
    grid: dict = field(default_factory=dict)
    show_grid_panel: bool = False
    show_contacts: bool = False
    show_forces: bool = False
    show_periodic: bool = False
    periods: tuple[int, int, int] = (1, 1, 1)
    show_indices: bool = False
    hidden_elements: set[str] = field(default_factory=set)
    axis_signs: list[int] = field(default_factory=lambda: [1, 1, 1])
    trajectory: Trajectory | None = None
    frame: int = 0
    follow_latest: bool = True
    show_player: bool = False
    playback_speed: int = 20
    indexing: bool = False
    index_error: str = ""
    relaxation: Relaxation | None = None
    show_data: bool = False
    show_curves: bool = False
    show_info: bool = True
    analysis_tab: str = "data"
    show_walls: bool = False
    representation: str = "ball"
    show_style: bool = False
    render_style: dict | None = None
    view_style: dict | None = None
    show_bonds: bool | None = None
    render_atoms: dict = field(default_factory=dict)
    render_topology: tuple = ()
    render_font: int | None = None
    show_render: bool = True
    readonly: bool = False
    show_report: bool = False
    show_elf_slice: bool = False
    volumes: dict = field(default_factory=dict)
    volume_options: dict = field(default_factory=dict)
    bader: object | None = None
    show_bader: bool = False
    show_bader_data: bool = False
    moments: object | None = None
    show_moments: bool = False
    show_moments_data: bool = False
    moments_loading: bool = False
