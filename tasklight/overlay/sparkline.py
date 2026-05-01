"""Token-rate sparkline: pure rate functions + QPainter rendering."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Iterable

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QPainter, QPolygon, QPolygonF
from PyQt6.QtCore import QPointF

from tasklight.config import TokenRateConfig
from tasklight.model import TokenSample


@dataclass(frozen=True)
class Segment:
    """A segment between two consecutive TokenSamples."""

    t_a: float        # time of first sample
    t_b: float        # time of second sample
    rate: float       # tokens / second (signed); always 0.0 for reset edges
    midpoint: float   # (t_a + t_b) / 2
    dt: float         # t_b - t_a


def iter_segments(history: list[TokenSample]) -> Iterable[Segment]:
    """Yield Segment objects for consecutive sample pairs."""
    for i in range(len(history) - 1):
        a = history[i]
        b = history[i + 1]
        dt = b.t - a.t
        if dt <= 0:
            continue
        dtok = b.tokens - a.tokens
        rate = dtok / dt
        midpoint = (a.t + b.t) / 2.0
        yield Segment(a.t, b.t, rate, midpoint, dt)


def compute_mean_rate(
    history: list[TokenSample],
    window_s: int,
    now: float,
) -> float:
    """Time-weighted mean rate over the window (§4.2).

    Returns 0.0 when fewer than 2 samples are available.
    """
    if len(history) < 2:
        return 0.0

    cutoff = now - window_s
    total_weight = 0.0
    weighted_sum = 0.0

    for seg in iter_segments(history):
        if seg.t_b < cutoff:
            continue
        weighted_sum += seg.rate * seg.dt
        total_weight += seg.dt

    if total_weight <= 0.0:
        return 0.0
    return weighted_sum / total_weight


def smoothed_rate(
    history: list[TokenSample],
    t: float,
    tau_s: float,
) -> float:
    """Exponential-kernel smoothed rate at time t (§4.3).

    Only segments within ~5*tau_s of t contribute meaningfully.
    """
    if len(history) < 2:
        return 0.0

    cutoff_low = t - 5.0 * tau_s
    cutoff_high = t + 5.0 * tau_s

    total_weight = 0.0
    weighted_sum = 0.0

    for seg in iter_segments(history):
        if seg.midpoint < cutoff_low:
            continue
        if seg.midpoint > cutoff_high:
            # Segments are in time order; once past the window, stop.
            break
        w = seg.dt * math.exp(-abs(t - seg.midpoint) / tau_s)
        weighted_sum += seg.rate * w
        total_weight += w

    if total_weight <= 0.0:
        return 0.0
    return weighted_sum / total_weight


def is_in_reset_edge(resets: list[tuple[float, float]], t: float) -> bool:
    """Return True if t falls inside any recorded reset edge interval."""
    for t_a, t_b in resets:
        if t_a <= t <= t_b:
            return True
    return False


def paint_sparkline(
    painter: QPainter,
    rect: QRect,
    history: list[TokenSample],
    resets: list[tuple[float, float]],
    cfg: TokenRateConfig,
    now: float | None = None,
) -> None:
    """Paint a token-rate sparkline filling ``rect`` (§5).

    ``rect`` is the row's chart rectangle in widget-local coordinates.
    ``resets`` is the list of reset-edge time intervals to render as
    forced floor segments. Does nothing if fewer than 2 samples or
    ``cfg.enabled`` is False.
    """
    if not cfg.enabled or len(history) < 2:
        return

    if now is None:
        now = time.monotonic()

    chart_left = rect.left()
    chart_right = rect.right()
    chart_top = rect.top()
    chart_bottom = rect.bottom()
    chart_width = chart_right - chart_left
    if chart_width <= 0:
        return

    window_s = cfg.window_s
    tau_s = cfg.smoothing_tau_s if cfg.smoothing_tau_s > 0.0 else window_s / 30.0

    mean_rate = compute_mean_rate(history, window_s, now)
    chart_height = chart_bottom - chart_top - 1

    # y_scale: pixels per (token/s).  Clamp to avoid division by zero.
    y_scale = chart_height / max(mean_rate * cfg.scale_headroom, 1.0)

    # Build polyline of (x, y) for each integer pixel column.
    points: list[QPointF] = []
    for x in range(chart_left, chart_right + 1):
        # Map pixel column to time (§5.1).
        t_x = now - ((chart_right - x) / chart_width) * window_s

        if is_in_reset_edge(resets, t_x):
            y = float(chart_bottom)
        else:
            rate = smoothed_rate(history, t_x, tau_s)
            clamped = max(0.0, rate)
            pixel_h = min(clamped * y_scale, chart_height)
            y = chart_bottom - pixel_h

        points.append(QPointF(x, y))

    if not points:
        return

    # Close the polygon down to chart_bottom.
    fill_polygon: list[QPointF] = [QPointF(chart_left, chart_bottom)]
    fill_polygon.extend(points)
    fill_polygon.append(QPointF(chart_right, chart_bottom))

    base_color = QColor(cfg.color)

    painter.save()
    painter.setClipRect(rect)

    # Fill.
    fill_color = QColor(base_color)
    fill_color.setAlphaF(cfg.fill_alpha)
    painter.setPen(QColor(0, 0, 0, 0))  # transparent pen (no border from fill)
    painter.setBrush(fill_color)
    poly = QPolygonF(fill_polygon)
    painter.drawPolygon(poly)

    # Optional stroke.
    if cfg.stroke_alpha > 0.0:
        stroke_color = QColor(base_color)
        stroke_color.setAlphaF(cfg.stroke_alpha)
        from PyQt6.QtGui import QPen
        painter.setPen(QPen(stroke_color, 1.0))
        painter.setBrush(QColor(0, 0, 0, 0))
        poly_line = QPolygonF(points)
        painter.drawPolyline(poly_line)

    painter.restore()
