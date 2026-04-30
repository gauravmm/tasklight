"""Overlay widget implementation."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen, QScreen
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
    dock_position_changed = pyqtSignal(str)
    _SNAP_EDGE_THRESHOLD_PX = 20

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
        self._pressed_row: HeaderRow | AgentRow | None = None
        self._press_global_pos: QPointF | None = None
        self._drag_offset: QPointF | None = None
        self._drag_screen: QScreen | None = None
        self._dragging = False
        self._is_docked = True
        self._docked_screen: QScreen | None = None
        self._colors = self._build_colors()

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._apply_cursor_mode()
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
        self._apply_cursor_mode()
        self.setFixedWidth(cfg.dock.width)
        self._is_docked = True
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

        if not self._cfg.theme.use_system_cursor:
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

        self._pressed_row = hit_test(self._layout(QFontMetrics(self.font())), event.position())
        self._press_global_pos = event.globalPosition()
        self._drag_offset = event.globalPosition() - QPointF(self.x(), self.y())
        self._drag_screen = self._screen_for_point(event.globalPosition().toPoint())
        self._dragging = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event is None:
            return

        if not self._cfg.theme.use_system_cursor:
            self._cursor_pos = event.position()
            self.update()

        if (
            self._press_global_pos is None
            or self._drag_offset is None
            or (isinstance(self._pressed_row, AgentRow) and self._pressed_row.record_session_id)
        ):
            return

        if not self._dragging:
            if (
                event.globalPosition() - self._press_global_pos
            ).manhattanLength() < QApplication.startDragDistance():
                return
            self._dragging = True

        self._drag_screen = self._screen_for_point(event.globalPosition().toPoint())
        target = event.globalPosition() - self._drag_offset
        self.move(int(target.x()), int(target.y()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return

        pressed_row = self._pressed_row
        was_dragging = self._dragging
        snap_enabled = not bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._pressed_row = None
        self._press_global_pos = None
        self._drag_offset = None
        drag_screen = self._drag_screen
        self._drag_screen = None
        self._dragging = False

        if was_dragging:
            self._cursor_pos = None
            if snap_enabled:
                position = self._snap_to_nearest_dock(
                    drag_screen,
                    event.globalPosition().toPoint(),
                )
                if position is not None:
                    self.dock_position_changed.emit(position)
                else:
                    self._is_docked = False
                    self._docked_screen = None
            else:
                self._is_docked = False
                self._docked_screen = None
            return

        if isinstance(pressed_row, HeaderRow):
            self._toggle_group(pressed_row.dirname)
            return

        if (
            isinstance(pressed_row, AgentRow)
            and pressed_row.record_session_id
            and pressed_row.state == AgentState.DONE
        ):
            self._model.dismiss(pressed_row.record_session_id)

    def enterEvent(self, event) -> None:  # noqa: N802
        if (
            not self._cfg.theme.use_system_cursor
            and event is not None
            and hasattr(event, "position")
        ):
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

    def _apply_cursor_mode(self) -> None:
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(not self._cfg.theme.use_system_cursor)
        if self._cfg.theme.use_system_cursor:
            self._cursor_pos = None

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
        if self._is_docked:
            self._move_to_dock(self._docked_screen)
        if self._has_active_rows(layout):
            if not self._anim.isActive():
                self._anim.start()
        else:
            self._anim.stop()
        self.show()
        self.update()

    def _move_to_dock(self, screen: QScreen | None = None) -> None:
        screen = screen or self._current_screen()
        if screen is None:
            return
        self._docked_screen = screen
        position = self._cfg.dock.position
        x, y = self._dock_coordinates(position, screen)
        self.move(x, y)

    def _snap_to_nearest_dock(
        self,
        screen: QScreen | None = None,
        release_point: QPoint | None = None,
    ) -> str | None:
        point = release_point or self._window_center()
        screen = screen or self._screen_for_point(point)
        if screen is None:
            return None

        geometry = screen.availableGeometry()
        distances = {
            "L": abs(point.x() - geometry.left()),
            "R": abs(geometry.right() - point.x()),
            "T": abs(point.y() - geometry.top()),
            "B": abs(geometry.bottom() - point.y()),
        }
        nearest_edge, nearest_distance = min(distances.items(), key=lambda item: item[1])
        if nearest_distance > self._SNAP_EDGE_THRESHOLD_PX:
            return None

        rel_x = point.x() - geometry.left()
        rel_y = point.y() - geometry.top()
        x_third = geometry.width() / 3
        y_third = geometry.height() / 3

        if nearest_edge in ("L", "R"):
            vert = "T" if rel_y < y_third else "B" if rel_y > y_third * 2 else "M"
            position = f"{vert}{nearest_edge}"
        else:
            horiz = "L" if rel_x < x_third else "R" if rel_x > x_third * 2 else "C"
            position = f"{nearest_edge}{horiz}"

        self._is_docked = True
        self._docked_screen = screen
        self._cfg.dock.position = position
        target_x, target_y = self._dock_coordinates(position, screen)
        self.move(target_x, target_y)
        return position

    def _window_center(self) -> QPoint:
        return self.pos() + QPoint(self.width() // 2, self.height() // 2)

    def _screen_for_point(self, point: QPoint) -> QScreen | None:
        return QGuiApplication.screenAt(point) or QApplication.primaryScreen()

    def _current_screen(self) -> QScreen | None:
        return self._docked_screen or self._screen_for_point(self._window_center())

    def _frame_insets(self) -> tuple[int, int, int, int]:
        frame = self.frameGeometry()
        content = self.geometry()
        return (
            content.left() - frame.left(),
            content.top() - frame.top(),
            frame.right() - content.right(),
            frame.bottom() - content.bottom(),
        )

    def _dock_coordinates(self, position: str, screen: QScreen) -> tuple[int, int]:
        geometry = screen.availableGeometry()
        margin = self._cfg.dock.margin
        width, height = self.width(), self.height()
        inset_left, inset_top, _inset_right, _inset_bottom = self._frame_insets()

        x = {
            "L": geometry.x() + margin - inset_left,
            "C": geometry.center().x() - width // 2,
            "R": geometry.x() + geometry.width() - width - margin - inset_left,
        }[position[1] if len(position) == 2 else "R"]

        y = {
            "T": geometry.y() + margin - inset_top,
            "M": geometry.center().y() - height // 2,
            "B": geometry.y() + geometry.height() - height - margin - inset_top,
        }[position[0]]
        return x, y
