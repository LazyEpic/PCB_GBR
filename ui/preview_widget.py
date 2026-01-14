# ui/preview_widget.py
# FULL FILE REPLACEMENT
#
# Fixes:
# - Adds fit_to_view() alias (MainWindow expected it)
# - Fixes QPointF usage (Qt.QPointF is invalid in PySide6)
# - Keeps drawing lightweight and robust

from PySide6.QtWidgets import (
    QWidget,
    QGraphicsView,
    QGraphicsScene,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
)
from PySide6.QtGui import QPen, QPainter, QColor, QBrush, QPainterPath, QPolygonF
from PySide6.QtCore import Qt, QEvent, QPointF

from shapely.geometry import (
    Polygon,
    MultiPolygon,
    LineString,
    MultiLineString,
    GeometryCollection,
)


# ---- COLORS ----
COLOR_COPPER_ISO = QColor(255, 200, 0)
COLOR_MASK_CLEAR = QColor(0, 200, 255)
COLOR_SILK = QColor(255, 255, 255)

# Through-board actions: drills + slots + outline
COLOR_THROUGH = QColor(255, 0, 0)

COLOR_GRID = QColor(50, 50, 50)
COLOR_ORIGIN = QColor(255, 0, 0)


class PreviewWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing, True)
        self.view.setBackgroundBrush(QBrush(Qt.black))
        self.view.setFrameShape(QGraphicsView.NoFrame)

        # CNC coordinate system
        self.view.scale(1, -1)

        self.view.setMouseTracking(True)
        self.view.viewport().setMouseTracking(True)
        self.view.viewport().installEventFilter(self)

        # ---- SIDE PANEL ----
        self.side_panel = QWidget()
        self.side_panel.setFixedWidth(220)
        self.side_panel.setStyleSheet("background-color: black;")

        self.coord_label = QLabel("X=0.000  Y=0.000")
        self.coord_label.setStyleSheet("color: white;")

        self.grid_label = QLabel("Grid: -- mm")
        self.grid_label.setStyleSheet("color: white;")

        legend_layout = QVBoxLayout()
        legend_layout.setSpacing(6)

        self._add_legend_entry(legend_layout, "Copper isolation clearance", COLOR_COPPER_ISO)
        self._add_legend_entry(legend_layout, "Soldermask clear (pads)", COLOR_MASK_CLEAR)
        self._add_legend_entry(legend_layout, "Silkscreen", COLOR_SILK)
        self._add_legend_entry(legend_layout, "Through cuts (holes/slots/outline)", COLOR_THROUGH)
        self._add_legend_entry(legend_layout, "Origin (0,0)", COLOR_ORIGIN)

        side_layout = QVBoxLayout(self.side_panel)
        side_layout.setContentsMargins(8, 8, 8, 8)
        side_layout.addWidget(self.coord_label)
        side_layout.addWidget(self.grid_label)
        side_layout.addSpacing(10)
        side_layout.addLayout(legend_layout)
        side_layout.addStretch()

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.view, 1)
        main_layout.addWidget(self.side_panel)

        self.grid_items = []
        self.origin_items = []

    # --------------------------------------------------
    # EVENTS
    # --------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.view.viewport() and event.type() == QEvent.MouseMove:
            p = self.view.mapToScene(event.pos())
            self.coord_label.setText(f"X={p.x():.3f}  Y={p.y():.3f}")
        return super().eventFilter(obj, event)

    def _add_legend_entry(self, layout, name, color):
        row = QHBoxLayout()
        swatch = QLabel()
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(
            f"background-color: rgb({color.red()}, {color.green()}, {color.blue()});"
        )
        label = QLabel(name)
        label.setStyleSheet("color: white;")
        row.addWidget(swatch)
        row.addWidget(label)
        row.addStretch()
        layout.addLayout(row)

    # --------------------------------------------------
    # CLEAR
    # --------------------------------------------------

    def clear(self):
        self.scene.clear()
        self.grid_items.clear()
        self.origin_items.clear()

    # --------------------------------------------------
    # SHAPELY DRAW HELPERS
    # --------------------------------------------------

    def _iter_geom(self, geom):
        if geom is None or geom.is_empty:
            return
        if isinstance(geom, (Polygon, LineString)):
            yield geom
            return
        if isinstance(geom, (MultiPolygon, MultiLineString, GeometryCollection)) or hasattr(geom, "geoms"):
            for g in geom.geoms:
                yield from self._iter_geom(g)

    def _draw_linestring(self, pts, pen):
        if not pts or len(pts) < 2:
            return
        for i in range(len(pts) - 1):
            self.scene.addLine(
                pts[i][0], pts[i][1],
                pts[i + 1][0], pts[i + 1][1],
                pen
            )

    def _draw_polygon_outline(self, poly, pen):
        pts = list(poly.exterior.coords)
        self._draw_linestring(pts, pen)

    def draw_geom_outline(self, geom, color):
        if geom is None or geom.is_empty:
            return
        pen = QPen(color)
        pen.setWidthF(0)
        pen.setCosmetic(True)
        for g in self._iter_geom(geom):
            if isinstance(g, Polygon):
                self._draw_polygon_outline(g, pen)
            elif isinstance(g, LineString):
                self._draw_linestring(list(g.coords), pen)

    def _line_to_path(self, pts):
        if not pts or len(pts) < 2:
            return QPainterPath()
        p = QPainterPath(QPointF(pts[0][0], pts[0][1]))
        for x, y in pts[1:]:
            p.lineTo(QPointF(x, y))
        return p

    def draw_geom_filled(self, geom, outline_color, fill_color, fill_alpha=80, z=0):
        if geom is None or geom.is_empty:
            return

        pen = QPen(outline_color)
        pen.setWidthF(0)
        pen.setCosmetic(True)

        fill = QColor(fill_color)
        fill.setAlpha(fill_alpha)
        brush = QBrush(fill)

        for g in self._iter_geom(geom):
            if isinstance(g, Polygon):
                pts = list(g.exterior.coords)
                if len(pts) < 3:
                    continue
                qpoly = QPolygonF([QPointF(float(x), float(y)) for x, y in pts])
                item = self.scene.addPolygon(qpoly, pen, brush)
                item.setZValue(z)
            elif isinstance(g, LineString):
                pts = list(g.coords)
                if len(pts) < 2:
                    continue
                item = self.scene.addPath(self._line_to_path(pts), pen)
                item.setZValue(z)

    # --------------------------------------------------
    # SEMANTIC LAYERS
    # --------------------------------------------------

    def draw_copper_isolation(self, geom):
        self.draw_geom_outline(geom, COLOR_COPPER_ISO)

    def draw_soldermask_clear(self, geom):
        self.draw_geom_outline(geom, COLOR_MASK_CLEAR)

    def draw_silkscreen(self, geom):
        self.draw_geom_outline(geom, COLOR_SILK)

    def draw_through_outline(self, geom):
        self.draw_geom_outline(geom, COLOR_THROUGH)

    def draw_through_holes(self, drills):
        pen = QPen(COLOR_THROUGH)
        pen.setCosmetic(True)
        pen.setWidth(2)

        fill = QBrush(QColor(COLOR_THROUGH.red(), COLOR_THROUGH.green(), COLOR_THROUGH.blue(), 80))

        for x, y, d in drills:
            r = max(float(d) / 2.0, 0.15)
            item = self.scene.addEllipse(
                x - r, y - r, r * 2, r * 2,
                pen, fill
            )
            item.setZValue(1000)

    def draw_through_slots(self, slots):
        # slots: [((x1,y1),(x2,y2), width_mm)]
        from shapely.geometry import LineString as ShLine
        for (p1, p2, w) in slots:
            try:
                seg = ShLine([p1, p2])
                poly = seg.buffer(float(w) / 2.0, cap_style=1, join_style=1)
                self.draw_geom_outline(poly, COLOR_THROUGH)
            except Exception:
                continue

    # --------------------------------------------------
    # ORIGIN
    # --------------------------------------------------

    def draw_origin(self, size=3.0):
        pen = QPen(COLOR_ORIGIN)
        pen.setCosmetic(True)
        pen.setWidthF(0)

        self.origin_items.append(self.scene.addLine(-size, 0, size, 0, pen))
        self.origin_items.append(self.scene.addLine(0, -size, 0, size, pen))

    # --------------------------------------------------
    # GRID
    # --------------------------------------------------

    def draw_grid(self):
        rect = self.scene.itemsBoundingRect()
        if rect.isNull():
            return

        for item in self.grid_items:
            try:
                self.scene.removeItem(item)
            except Exception:
                pass
        self.grid_items.clear()

        w = max(rect.width(), rect.height())

        if w < 20:
            step = 1
        elif w < 100:
            step = 5
        elif w < 300:
            step = 10
        elif w < 800:
            step = 25
        else:
            step = 50

        self.grid_label.setText(f"Grid: {step} mm")

        pen = QPen(COLOR_GRID)
        pen.setWidthF(0)
        pen.setCosmetic(True)

        left = rect.left() - step
        right = rect.right() + step
        top = rect.top() - step
        bottom = rect.bottom() + step

        x = int(left // step) * step
        while x <= right:
            self.grid_items.append(self.scene.addLine(x, top, x, bottom, pen))
            x += step

        y = int(top // step) * step
        while y <= bottom:
            self.grid_items.append(self.scene.addLine(left, y, right, y, pen))
            y += step

    # --------------------------------------------------
    # FIT
    # --------------------------------------------------

    def fit(self):
        rect = self.scene.itemsBoundingRect()
        if rect.isNull():
            return

        # Don't redraw origin/grid here (caller does it) — just fit view.
        self.view.fitInView(rect.adjusted(-5, -5, 5, 5), Qt.KeepAspectRatio)

    # Compatibility alias (older code expects this name)
    def fit_to_view(self):
        self.fit()
