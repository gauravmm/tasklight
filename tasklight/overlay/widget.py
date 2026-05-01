"""Overlay widget implementation."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPen,
    QScreen,
)
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from tasklight.config import AppConfig
from tasklight.model import AgentState, AgentStateModel, TokenSample
from tasklight.overlay.layout import build_layout, elide, hit_test
from tasklight.overlay.presentation import glyph_for_state, hex_color
from tasklight.overlay.sparkline import paint_sparkline
from tasklight.overlay.types import AgentRow, HeaderRow, LayoutMetrics, LayoutRow, OverlayLayout
from tasklight.overlay.view_model import build_rows


@dataclass(frozen=True)
class OverlayColors:
    background: QColor
    foreground: QColor
    dirname_fg: QColor
    hostname_fg: QColor
    approval_bg: QColor
    done_bg: QColor


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
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._model = model
        self._cfg = cfg
        self._frame = 0
        self._cursor_pos: QPointF | None = None
        self._collapsed_groups: set[tuple[str, str]] = set()
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

        self._cursor_hide = QTimer(self)
        self._cursor_hide.setSingleShot(True)
        self._cursor_hide.setInterval(3000)
        self._cursor_hide.timeout.connect(self._hide_cursor)

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
        now = time.monotonic()
        for layout_row in layout.rows:
            row = layout_row.row
            status = row.summary if isinstance(row, HeaderRow) else row
            if status is not None:
                row_rect = self.rect().adjusted(
                    0,
                    layout_row.top,
                    0,
                    -(self.height() - layout_row.top - layout_row.height),
                )
                if status.state == AgentState.APPROVAL:
                    painter.fillRect(row_rect, self._colors.approval_bg)
                elif (
                    status.state == AgentState.DONE and self._colors.done_bg.alpha() > 0
                ):
                    painter.fillRect(row_rect, self._colors.done_bg)

            # Paint sparkline before text for AgentRow (non-APPROVAL) rows (§5.3).
            if (
                isinstance(row, AgentRow)
                and row.record_session_id
                and row.state != AgentState.APPROVAL
                and self._cfg.theme.token_rate.enabled
            ):
                history = self._token_history_for(row.record_session_id)
                if len(history) >= 2:
                    resets = self._token_resets_for(row.record_session_id)
                    cwm = self._context_window_max_for(row.record_session_id)
                    self._paint_row_sparkline(
                        painter, layout_row, metrics, history, resets, cwm, now
                    )

            baseline = layout_row.top + painter.fontMetrics().ascent()
            if isinstance(row, HeaderRow):
                self._paint_header(painter, row, baseline, layout)
            else:
                self._paint_agent_row(painter, row, baseline, layout)

        if not self._cfg.theme.system_cursor:
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

        self._pressed_row = hit_test(
            self._layout(QFontMetrics(self.font())), event.position()
        )
        self._press_global_pos = event.globalPosition()
        self._drag_offset = event.globalPosition() - QPointF(self.x(), self.y())
        self._drag_screen = self._screen_for_point(event.globalPosition().toPoint())
        self._dragging = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event is None:
            return

        if not self._cfg.theme.system_cursor:
            self._cursor_pos = event.position()
            self._cursor_hide.start()
            self.update()

        if (
            self._press_global_pos is None
            or self._drag_offset is None
            or (
                isinstance(self._pressed_row, AgentRow)
                and self._pressed_row.record_session_id
            )
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
            self._toggle_group(pressed_row)
            return

        if (
            isinstance(pressed_row, AgentRow)
            and pressed_row.record_session_id
            and pressed_row.state == AgentState.DONE
        ):
            self._model.dismiss(pressed_row.record_session_id)

    def enterEvent(self, event) -> None:  # noqa: N802
        if (
            not self._cfg.theme.system_cursor
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
        font.setPixelSize(self._cfg.theme.font_size)
        return font

    def _layout(self, fm: QFontMetrics) -> OverlayLayout:
        rows = build_rows(self._model.records(), self._collapsed_groups)
        return build_layout(rows, fm, self.width())

    def _build_colors(self) -> OverlayColors:
        theme = self._cfg.theme
        return OverlayColors(
            background=hex_color(theme.background, theme.background_alpha),
            foreground=hex_color(theme.foreground),
            dirname_fg=hex_color(theme.dirname_fg),
            hostname_fg=hex_color(theme.hostname_fg),
            approval_bg=hex_color(theme.approval_bg),
            done_bg=hex_color(theme.done_bg) if theme.done_bg else QColor(0, 0, 0, 0),
        )

    def _token_history_for(self, session_id: str) -> list[TokenSample]:
        """Return the token_history list for the given session_id, or empty."""
        for record in self._model.records():
            if record.session_id == session_id:
                return record.token_history
        return []

    def _token_resets_for(self, session_id: str) -> list[tuple[float, float]]:
        """Return the token_resets list for the given session_id, or empty."""
        for record in self._model.records():
            if record.session_id == session_id:
                return record.token_resets
        return []

    def _context_window_max_for(self, session_id: str) -> int:
        """Return the per-record context_window_max, or 0 if unknown."""
        for record in self._model.records():
            if record.session_id == session_id:
                return record.context_window_max
        return 0

    def _has_active_rows(self, layout: OverlayLayout) -> bool:
        # Spinners need the timer if animate_spinners is on.
        spinner_active = self._cfg.theme.animate_spinners and any(
            (
                isinstance(layout_row.row, AgentRow)
                and layout_row.row.state in (AgentState.THINKING, AgentState.TOOL)
            )
            or (
                isinstance(layout_row.row, HeaderRow)
                and layout_row.row.summary is not None
                and layout_row.row.summary.state
                in (AgentState.THINKING, AgentState.TOOL)
            )
            for layout_row in layout.rows
        )
        if spinner_active:
            return True

        # Chart also needs the timer when any visible row has ≥2 samples (§5.5).
        if self._cfg.theme.token_rate.enabled:
            for layout_row in layout.rows:
                row = layout_row.row
                if not isinstance(row, AgentRow) or not row.record_session_id:
                    continue
                history = self._token_history_for(row.record_session_id)
                if len(history) >= 2:
                    return True

        return False

    def _paint_row_sparkline(
        self,
        painter: QPainter,
        layout_row: LayoutRow,
        metrics: LayoutMetrics,
        history: list[TokenSample],
        resets: list[tuple[float, float]],
        context_window_max: int,
        now: float,
    ) -> None:
        """Compute chart geometry and delegate to paint_sparkline (§5.1)."""
        cfg = self._cfg.theme.token_rate
        # Chart spans the full text band: from the start of the label
        # (where dirname/tool text begins) to the row's right edge,
        # passing under the elapsed-time column. Text is painted over
        # the chart so it stays legible.
        chart_left = metrics.label_x
        chart_right = self.width() - metrics.pad
        chart_top = layout_row.top + 1
        chart_bottom = layout_row.top + layout_row.height - 1

        rect = QRect(chart_left, chart_top, chart_right - chart_left, chart_bottom - chart_top)
        paint_sparkline(
            painter,
            rect,
            history,
            resets,
            cfg,
            now,
            context_window_max=context_window_max,
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
        x = metrics.pad
        x = self._paint_hostname_prefix(painter, fm, row.hostname, x, baseline)

        if row.summary is None:
            painter.setPen(QPen(self._colors.dirname_fg))
            painter.drawText(
                x,
                baseline,
                elide(fm, f"/{row.dirname}", metrics.content_right - x),
            )
            return

        elapsed_x = self.width() - metrics.pad - metrics.elapsed_width
        dirname_text = elide(
            fm,
            f"/{row.dirname}",
            max(metrics.em * 8, (elapsed_x - x) // 3),
        )
        dirname_width = fm.horizontalAdvance(dirname_text)
        glyph_x = x + dirname_width + metrics.text_gap
        label_x = glyph_x + metrics.glyph_width + metrics.text_gap
        label_width = elapsed_x - label_x - metrics.text_gap

        painter.setPen(QPen(self._colors.dirname_fg))
        painter.drawText(x, baseline, dirname_text)
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
        painter.setPen(QPen(self._colors.dirname_fg))
        painter.drawText(elapsed_x, baseline, row.summary.elapsed)

    def _paint_hostname_prefix(
        self,
        painter: QPainter,
        fm: QFontMetrics,
        hostname: str,
        x: int,
        baseline: int,
    ) -> int:
        """Paint 'hostname:' in hostname_color and return the new x position."""
        if not hostname:
            return x
        text = f"{hostname}:"
        painter.setPen(QPen(self._colors.hostname_fg))
        painter.drawText(x, baseline, text)
        return x + fm.horizontalAdvance(text)

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
        painter.setPen(QPen(self._colors.dirname_fg))
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

    def _hide_cursor(self) -> None:
        self._cursor_pos = None
        self.update()

    def _apply_cursor_mode(self) -> None:
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(not self._cfg.theme.system_cursor)
        if self._cfg.theme.system_cursor:
            self._cursor_pos = None

    def _toggle_group(self, row: HeaderRow) -> None:
        key = row.group_key
        if key in self._collapsed_groups:
            self._collapsed_groups.discard(key)
        else:
            self._collapsed_groups.add(key)
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
        nearest_edge, nearest_distance = min(
            distances.items(), key=lambda item: item[1]
        )
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
