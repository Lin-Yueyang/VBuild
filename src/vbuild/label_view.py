"""One textured glyph atlas and one draw call for centered, outlined world labels."""

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints, vtkStringArray
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkImageData, vtkPolyData
from vtkmodules.vtkRenderingCore import vtkPolyDataMapper2D, vtkTexture, vtkTexturedActor2D


class AtomLabels:
    def __init__(self, renderer, *, pixel_ratio=lambda: 1, kind='index'):
        self.renderer, self.data, self.geometry = renderer, vtkPolyData(), vtkPolyData()
        self.mapper = vtkPolyDataMapper2D()
        self.mapper.SetInputData(self.geometry)
        self.mapper.ScalarVisibilityOff()
        self.texture = vtkTexture()
        self.texture.InterpolateOn()
        self.actor = vtkTexturedActor2D()
        self.actor.SetMapper(self.mapper)
        self.actor.SetTexture(self.texture)
        self.actor.PickableOff()
        self.actor.UseBoundsOff()
        self.actor.VisibilityOff()
        renderer.AddViewProp(self.actor)
        renderer.AddObserver("StartEvent", self.project)
        self.actors = [self.actor]
        self.positions, self.texts = np.empty((0, 3)), ()
        self.characters, self.color = (), (0, 0, 0)
        self.size, self.pixel_ratio = 20, pixel_ratio
        self.kind, self.styles, self.line_kinds = kind, {}, ()
        self.ratio = self.pixel_ratio()
        self.font = QFont("Arial")
        self.font.setPixelSize(round(self.size * self.ratio))
        self.font.setBold(True)
        self.metrics = QFontMetrics(self.font)
        self.owners = np.empty(0, dtype=int)
        self.screen_centers = np.empty((0, 2))

    def update(self, positions, sources, symbols, hidden, visible, charges=None, moments=None):
        if not visible and charges is None and moments is None:
            self.hide()
            return
        texts, kinds = [], []
        for index in sources:
            lines = [f"{symbols[index]} {index + 1}"] if visible and symbols[index] not in hidden else []
            types = ['index'] if lines else []
            if charges is not None and charges[index]:
                lines.append(charges[index])
                types.append('bader')
            if moments is not None and moments[index]:
                lines.append(moments[index])
                types.append('moment')
            texts.append("\n".join(lines))
            kinds.append(tuple(types))
        keep = np.array([bool(text) for text in texts], dtype=bool)
        self.set_text(positions[keep], [text for text in texts if text], True, [kind for text, kind in zip(texts, kinds) if text])

    def text_style(self, kind):
        value = dict(family='Arial', size=self.size, bold=True, color=None, outline='#ffffff', width=1.) | self.styles.get(kind, {})
        if kind == self.kind:
            value['size'] = self.size
        font = QFont(value['family'])
        font.setPixelSize(round(value['size'] * self.ratio))
        font.setBold(value['bold'])
        return value, font, QFontMetrics(font)

    def make_atlas(self):
        self.formats = {kind: self.text_style(kind) for kind, _char in self.characters}
        margin = round((2 + max(value[0]['width'] for value in self.formats.values())) * self.ratio)
        self.margin = margin
        self.glyph_width = max(self.formats[kind][2].horizontalAdvance(char) for kind, char in self.characters) + 2 * margin
        self.glyph_height = max(value[2].height() for value in self.formats.values()) + 2 * margin
        columns, rows = 16, (len(self.characters) + 15) // 16
        width, height = columns * self.glyph_width, rows * self.glyph_height
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.cells = {}
        for index, (kind, char) in enumerate(self.characters):
            value, font, metrics = self.formats[kind]
            color = QColor(value['color']) if value['color'] is not None else QColor.fromRgbF(*self.color)
            painter.setPen(QPen(QColor(value['outline']), 2 * value['width'] * self.ratio,
                               Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                           if value['width'] else Qt.PenStyle.NoPen)
            painter.setBrush(color)
            x, y = index % columns * self.glyph_width, index // columns * self.glyph_height
            path = QPainterPath()
            path.addText(x + margin, y + margin + metrics.ascent(), font, char)
            painter.drawPath(path)
            painter.fillPath(path, color)
            self.cells[kind, char] = np.array([[x / width, 1 - (y + self.glyph_height) / height],
                [(x + self.glyph_width) / width, 1 - (y + self.glyph_height) / height],
                [(x + self.glyph_width) / width, 1 - y / height], [x / width, 1 - y / height]])
        painter.end()
        self.pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(height, width, 4)[::-1].copy()
        grid = vtkImageData()
        grid.SetDimensions(width, height, 1)
        grid.GetPointData().SetScalars(numpy_to_vtk(self.pixels.reshape(-1, 4), deep=True))
        self.texture.SetInputData(grid)

    def layout(self, texts):
        characters = tuple(sorted({(kind, char) for text, kinds in zip(texts, self.line_kinds)
                                   for line, kind in zip(text.split('\n'), kinds) for char in line if char != ' '}))
        if not characters:
            self.owners = np.empty(0, dtype=int)
            return
        if characters != self.characters:
            self.characters = characters
            self.make_atlas()
        owners, offsets, texture = [], [], []
        width, height = self.glyph_width, self.glyph_height
        corners = np.array([[0, 0], [width, 0], [width, height], [0, height]])
        for owner, text in enumerate(texts):
            offset, previous = 0., None
            for content, kind in zip(text.split("\n"), self.line_kinds[owner]):
                metrics = self.formats[kind][2]
                line_height = metrics.height() + 2 * self.margin
                if previous is not None:
                    offset += (previous + line_height) / 2
                previous = line_height
                x = -sum(metrics.horizontalAdvance(char) for char in content) / 2 - self.margin
                for char in content:
                    if char != " ":
                        owners.append(owner)
                        offsets.append(corners + [x, line_height / 2 - height - offset])
                        texture.append(self.cells[kind, char])
                    x += metrics.horizontalAdvance(char)
        self.owners, self.offsets = np.array(owners), np.array(offsets)
        coordinates = numpy_to_vtk(np.asarray(texture, dtype=np.float32).reshape(-1, 2), deep=True)
        self.geometry.GetPointData().SetTCoords(coordinates)

    def set_text(self, positions, texts, visible, kinds=None):
        self.actor.SetVisibility(visible and bool(len(positions)))
        if not visible:
            return
        self.positions = positions.copy()
        points = vtkPoints()
        points.SetData(numpy_to_vtk(self.positions, deep=True))
        self.data.SetPoints(points)
        texts = tuple(texts)
        kinds = tuple(tuple(row) for row in kinds) if kinds is not None else tuple(tuple(self.kind for _line in text.split('\n')) for text in texts)
        if texts != self.texts or kinds != self.line_kinds:
            self.line_kinds = kinds
            self.layout(texts)
            self.texts = texts
            labels = vtkStringArray()
            labels.SetName("index")
            for text in texts:
                labels.InsertNextValue(text)
            self.data.GetPointData().AddArray(labels)
        self.data.Modified()

    def project(self, *_args):
        if not self.actor.GetVisibility() or not len(self.owners):
            return
        if self.ratio != self.pixel_ratio():
            self.set_size(self.size)
        width, height = self.renderer.GetSize()
        if not width or not height:
            return
        matrix = self.renderer.GetActiveCamera().GetCompositeProjectionTransformMatrix(width / height, -1, 1)
        transform = np.array([[matrix.GetElement(row, col) for col in range(4)] for row in range(4)])
        clip = np.column_stack((self.positions, np.ones(len(self.positions)))) @ transform.T
        ndc = clip[:, :3] / clip[:, 3:4]
        self.screen_centers = (ndc[:, :2] + 1) * [width / 2, height / 2] + self.renderer.GetOrigin()
        xy = self.screen_centers[self.owners, None, :] + self.offsets
        vertices = np.column_stack((xy.reshape(-1, 2), np.zeros(len(self.owners) * 4))).astype(np.float32)
        points = vtkPoints()
        points.SetData(numpy_to_vtk(vertices, deep=True))
        self.geometry.SetPoints(points)
        visible = (abs(ndc[self.owners]) <= [1.1, 1.1, 1]).all(axis=1) & (clip[self.owners, 3] > 0)
        quads = np.arange(len(self.owners) * 4).reshape(-1, 4)[visible]
        cells = vtkCellArray()
        cells.SetCells(len(quads), numpy_to_vtkIdTypeArray(np.column_stack((np.full(len(quads), 4), quads)).ravel(), deep=True))
        self.geometry.SetPolys(cells)

    def hide(self):
        self.actor.VisibilityOff()

    def set_size(self, size, *, force=False):
        if not force and self.size == size and self.ratio == self.pixel_ratio():
            return
        self.size, self.ratio = size, self.pixel_ratio()
        _value, self.font, self.metrics = self.text_style(self.kind)
        self.characters = ()
        if self.texts:
            self.layout(self.texts)

    def configure(self, styles):
        if styles != self.styles:
            from copy import deepcopy
            self.styles = deepcopy(styles)
            self.set_size(self.size, force=True)

    def set_color(self, color):
        if tuple(color) == self.color:
            return
        self.color = tuple(color)
        if self.characters:
            self.make_atlas()
