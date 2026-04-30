"""Overlay widget implementation."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QPointF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from tasklight.config import AppConfig
from tasklight.model import AgentState, AgentStateModel
from tasklight.overlay.layout import build_layout, elide, hit_test
from tasklight.overlay.presentation import glyph_for_state, hex_color
from tasklight.overlay.types import AgentRow, HeaderRow, OverlayLayout
from tasklight.overlay.view_model import build_rows


@dataclass(frozen=True)
class OverlayColors:
    background: QColor
    foreground: QColor
    dimmed: QColor
    approval_background: QColor


class OverlayWidget(QWidget):
    _CURSOR_POINTS = [
        QPoint(0, 0),
        QPoint(0, 18),
        QPoint(4, 14),
        QPoint(7, 22),
        QPoint(11, 21),
        QPoint(8, 13),
        QPoint(14, 13),
    ]

    def __init__(self, model: AgentStateModel, cfg: AppConfig) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._model = model
        self._cfg = cfg
        self._frame = 0
        self._cursor_pos: QPointF | None = None
        self._collapsed_groups: set[str] = set()
        self._context_menu: QMenu | None = None
        self._colors = self._build_colors()

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(True)
        self.setFixedWidth(cfg.dock.width)

        model.dataChanged.connect(self._refresh)
        model.rowsInserted.connect(self._refresh)
        model.rowsRemoved.connect(self._refresh)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self.update)
        self._clock.start()

        self._anim = QTimer(self)
        self._anim.setInterval(125)
        self._anim.timeout.connect(self._tick_spinner)

        self._refresh()
        self._move_to_dock()

    def set_context_menu(self, menu: QMenu) -> None:
        self._context_menu = menu

    def apply_config(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._colors = self._build_colors()
        self.setFixedWidth(cfg.dock.width)
        self._refresh()
        self._move_to_dock()

    def closeEvent(self, event) -> None:  # noqa: N802
        if event is not None:
            event.ignore()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._font())

        layout = self._layout(painter.fontMetrics())
        if not layout.rows:
            painter.end()
            return

        painter.setBrush(self._colors.background)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(
            self.rect(),
            self._cfg.theme.corner_radius,
            self._cfg.theme.corner_radius,
        )

        metrics = layout.metrics
        for layout_row in layout.rows:
            row = layout_row.row
            status = row.summary if isinstance(row, HeaderRow) else row
            if status is not None and status.state == AgentState.APPROVAL:
                row_rect = self.rect().adjusted(
                    0,
                    layout_row.top,
                    0,
                    -(self.height() - layout_row.top - layout_row.height),
                )
                painter.fillRect(row_rect, self._colors.approval_background)

            baseline = layout_row.top + painter.fontMetrics().ascent()
            if isinstance(row, HeaderRow):
                self._paint_header(painter, row, baseline, layout)
            else:
                self._paint_agent_row(painter, row, baseline, layout)

        self._draw_cursor(painter)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event is None:
            return

        if event.button() == Qt.MouseButton.RightButton:
            if self._context_menu is not None:
                self._context_menu.popup(event.globalPosition().toPoint())
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        row = hit_test(self._layout(QFontMetrics(self.font())), event.position())
        if isinstance(row, HeaderRow):
            self._toggle_group(row.dirname)
            return

        if (
            isinstance(row, AgentRow)
            and row.record_session_id
            and row.state == AgentState.DONE
        ):
            self._model.dismiss(row.record_session_id)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event is not None:
            self._cursor_pos = event.position()
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        if event is not None and hasattr(event, "position"):
            self._cursor_pos = event.position()
        self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._cursor_pos = None
        self.update()

    def _font(self) -> QFont:
        font = QFont(self._cfg.theme.font_family)
        font.setPixelSize(self._cfg.theme.font_size_px)
        return font

    def _layout(self, fm: QFontMetrics) -> OverlayLayout:
        rows = build_rows(self._model.records(), self._collapsed_groups)
        return build_layout(rows, fm, self.width())

    def _build_colors(self) -> OverlayColors:
        theme = self._cfg.theme
        return OverlayColors(
            background=hex_color(theme.background, theme.background_alpha),
            foreground=hex_color(theme.foreground),
            dimmed=hex_color(theme.dimmed),
            approval_background=hex_color(theme.approval_row_bg),
        )

    def _has_active_rows(self, layout: OverlayLayout) -> bool:
        if not self._cfg.theme.animate_spinners:
            return False
        return any(
            (
                isinstance(layout_row.row, AgentRow)
                and layout_row.row.state in (AgentState.THINKING, AgentState.TOOL)
            )
            or (
                isinstance(layout_row.row, HeaderRow)
                and layout_row.row.summary is not None
                and layout_row.row.summary.state in (AgentState.THINKING, AgentState.TOOL)
            )
            for layout_row in layout.rows
        )

    def _paint_header(
        self,
        painter: QPainter,
        row: HeaderRow,
        baseline: int,
        layout: OverlayLayout,
    ) -> None:
        fm = painter.fontMetrics()
        metrics = layout.metrics
        if row.summary is None:
            painter.setPen(QPen(self._colors.dimmed))
            painter.drawText(
                metrics.pad,
                baseline,
                elide(fm, f"/{row.dirname}", metrics.content_right - metrics.pad),
            )
            return

        elapsed_x = self.width() - metrics.pad - metrics.elapsed_width
        dirname_text = elide(
            fm,
            f"/{row.dirname}",
            max(metrics.em * 8, (elapsed_x - metrics.pad) // 3),
        )
        dirname_width = fm.horizontalAdvance(dirname_text)
        glyph_x = metrics.pad + dirname_width + metrics.text_gap
        label_x = glyph_x + metrics.glyph_width + metrics.text_gap
        label_width = elapsed_x - label_x - metrics.text_gap

        painter.setPen(QPen(self._colors.dimmed))
        painter.drawText(metrics.pad, baseline, dirname_text)
        glyph, glyph_color = glyph_for_state(
            row.summary.source,
            row.summary.state,
            frame=self._frame,
            animate=self._cfg.theme.animate_spinners,
            theme=self._cfg.theme,
        )
        painter.setPen(QPen(glyph_color))
        painter.drawText(glyph_x, baseline, glyph)
        painter.setPen(QPen(self._colors.foreground))
        painter.drawText(label_x, baseline, elide(fm, row.summary.label, label_width))
        painter.setPen(QPen(self._colors.dimmed))
        painter.drawText(elapsed_x, baseline, row.summary.elapsed)

    def _paint_agent_row(
        self,
        painter: QPainter,
        row: AgentRow,
        baseline: int,
        layout: OverlayLayout,
    ) -> None:
        fm = painter.fontMetrics()
        metrics = layout.metrics
        if not row.record_session_id:
            painter.setPen(QPen(self._colors.foreground))
            painter.drawText(
                metrics.label_x,
                baseline,
                elide(fm, row.label, metrics.label_width),
            )
            return

        glyph, glyph_color = glyph_for_state(
            row.source,
            row.state,
            frame=self._frame,
            animate=self._cfg.theme.animate_spinners,
            theme=self._cfg.theme,
        )
        painter.setPen(QPen(glyph_color))
        painter.drawText(metrics.glyph_x, baseline, glyph)

        elapsed_x = self.width() - metrics.pad - metrics.elapsed_width
        painter.setPen(QPen(self._colors.foreground))
        painter.drawText(
            metrics.label_x,
            baseline,
            elide(fm, row.label, metrics.label_width),
        )
        painter.setPen(QPen(self._colors.dimmed))
        painter.drawText(elapsed_x, baseline, row.elapsed)

    def _draw_cursor(self, painter: QPainter) -> None:
        if self._cursor_pos is None:
            return

        painter.save()
        painter.translate(self._cursor_pos)
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.drawPolygon(self._CURSOR_POINTS)
        painter.restore()

    def _toggle_group(self, dirname: str) -> None:
        if dirname in self._collapsed_groups:
            self._collapsed_groups.remove(dirname)
        else:
            self._collapsed_groups.add(dirname)
        self._refresh()

    def _tick_spinner(self) -> None:
        self._frame += 1
        self.update()

    def _refresh(self) -> None:
        self.setFont(self._font())
        layout = self._layout(QFontMetrics(self.font()))
        self.setFixedHeight(layout.total_height)
        if self._has_active_rows(layout):
            if not self._anim.isActive():
                self._anim.start()
        else:
            self._anim.stop()
        self.show()
        self.update()

    def _move_to_dock(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        margin = self._cfg.dock.margin
        position = self._cfg.dock.position
        width, height = self.width(), self.height()

        x = {
            "L": geometry.left() + margin,
            "C": geometry.center().x() - width // 2,
            "R": geometry.right() - width - margin,
        }[position[1] if len(position) == 2 else "R"]

        y = {
            "T": geometry.top() + margin,
            "M": geometry.center().y() - height // 2,
            "B": geometry.bottom() - height - margin,
        }[position[0]]

        self.move(x, y)
