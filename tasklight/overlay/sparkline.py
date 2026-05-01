"""Token-rate sparkline: pure rate functions + QPainter rendering."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from PyQt6.QtCore import QPointF, QRect, QRectF
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPolygonF

from tasklight.config import TokenRateConfig
from tasklight.model import TokenSample


@dataclass(frozen=True)
class Segment:
    """A segment between two consecutive TokenSamples.

    Carries per-component rates so the renderer can stack input,
    cache_creation, and cache_read independently. ``rate`` is the
    convenience total (input + cache_creation + cache_read).
    """

    t_a: float
    t_b: float
    midpoint: float
    dt: float
    input_rate: float
    cache_creation_rate: float
    cache_read_rate: float

    @property
    def rate(self) -> float:
        return self.input_rate + self.cache_creation_rate + self.cache_read_rate


def iter_segments(history: list[TokenSample]) -> Iterable[Segment]:
    """Yield Segment objects for consecutive sample pairs."""
    for i in range(len(history) - 1):
        a = history[i]
        b = history[i + 1]
        dt = b.t - a.t
        if dt <= 0:
            continue
        midpoint = (a.t + b.t) / 2.0
        yield Segment(
            t_a=a.t,
            t_b=b.t,
            midpoint=midpoint,
            dt=dt,
            input_rate=(b.input_tokens - a.input_tokens) / dt,
            cache_creation_rate=(b.cache_creation_tokens - a.cache_creation_tokens) / dt,
            cache_read_rate=(b.cache_read_tokens - a.cache_read_tokens) / dt,
        )


def compute_mean_rate(
    history: list[TokenSample],
    window_s: int,
    now: float,
) -> float:
    """Time-weighted mean total rate over the window (§4.2).

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


def smoothed_rates(
    history: list[TokenSample],
    t: float,
    tau_s: float,
) -> tuple[float, float, float]:
    """Exponential-kernel smoothed (input, cache_creation, cache_read) at t.

    Single pass over segments — three components are convolved together
    with the same kernel weights so they stay in phase.
    """
    if len(history) < 2:
        return (0.0, 0.0, 0.0)

    cutoff_low = t - 5.0 * tau_s
    cutoff_high = t + 5.0 * tau_s

    total_weight = 0.0
    sum_input = 0.0
    sum_creation = 0.0
    sum_read = 0.0

    for seg in iter_segments(history):
        if seg.midpoint < cutoff_low:
            continue
        if seg.midpoint > cutoff_high:
            break
        w = seg.dt * math.exp(-abs(t - seg.midpoint) / tau_s)
        sum_input += seg.input_rate * w
        sum_creation += seg.cache_creation_rate * w
        sum_read += seg.cache_read_rate * w
        total_weight += w

    if total_weight <= 0.0:
        return (0.0, 0.0, 0.0)
    return (
        sum_input / total_weight,
        sum_creation / total_weight,
        sum_read / total_weight,
    )


def smoothed_rate(history: list[TokenSample], t: float, tau_s: float) -> float:
    """Total smoothed rate at t (sum of the three components)."""
    a, b, c = smoothed_rates(history, t, tau_s)
    return a + b + c


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
    context_window_max: int = 0,
) -> None:
    """Paint a stacked token-rate sparkline filling ``rect`` (§5).

    Three colored bands stack from the bottom: cache_read (warm cache,
    cheapest), cache_creation (loading new context), and input (real
    conversation). Text is painted on top by the caller.
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
    curve = max(1.0, cfg.time_curve_exponent)
    display_now = now - max(0.0, cfg.render_lag_s)

    # Effective context-window cap: prefer the per-record value supplied
    # by the caller (derived from the latest hook payload's model
    # field); fall back to the config default if that's unknown.
    effective_cwm = context_window_max if context_window_max > 0 else cfg.context_window_max

    # Reserve the bottom rows for the context-window line if enabled.
    show_context_line = effective_cwm > 0 and cfg.context_line_height_px > 0
    line_h = cfg.context_line_height_px if show_context_line else 0
    # 1px gap between bands and line so the line reads as separate.
    bands_bottom = chart_bottom - (line_h + 1 if show_context_line else 0)

    mean_rate = compute_mean_rate(history, window_s, now)
    chart_height = bands_bottom - chart_top - 1
    if chart_height <= 0:
        # Row too short for both bands and line; skip bands.
        chart_height = 0
    y_scale = chart_height / max(mean_rate * cfg.scale_headroom, 1.0) if chart_height > 0 else 0.0

    # For each pixel column, compute three cumulative top-of-band y values:
    # y_read (top of read band), y_creation (top of creation band),
    # y_input (top of input band = top of full stack).
    n_cols = chart_right - chart_left + 1
    y_read: list[float] = []
    y_creation: list[float] = []
    y_input: list[float] = []

    for x in range(chart_left, chart_right + 1):
        u = (chart_right - x) / chart_width
        t_x = display_now - window_s * (u ** curve)

        if is_in_reset_edge(resets, t_x):
            y_read.append(float(bands_bottom))
            y_creation.append(float(bands_bottom))
            y_input.append(float(bands_bottom))
            continue

        r_in, r_creation, r_read = smoothed_rates(history, t_x, tau_s)
        # Clamp negatives to zero per band so the stack never goes
        # below the floor when one component drops faster than another
        # rises.
        r_in = max(0.0, r_in)
        r_creation = max(0.0, r_creation)
        r_read = max(0.0, r_read)

        h_read = min(r_read * y_scale, chart_height)
        h_creation = min((r_read + r_creation) * y_scale, chart_height)
        h_total = min((r_read + r_creation + r_in) * y_scale, chart_height)

        y_read.append(bands_bottom - h_read)
        y_creation.append(bands_bottom - h_creation)
        y_input.append(bands_bottom - h_total)

    painter.save()
    painter.setClipRect(rect)
    painter.setPen(QColor(0, 0, 0, 0))

    # Left-edge fade: a horizontal alpha gradient applied to every band.
    # 0% alpha at chart_left, fade_alpha (= cfg.fill_alpha) at the
    # fade-in stop, constant from there to chart_right.
    fade_frac = max(0.0, min(1.0, cfg.left_fade_fraction))

    def band_brush(color_hex: str) -> QBrush:
        color = QColor(color_hex)
        color.setAlphaF(cfg.fill_alpha)
        if fade_frac <= 0.0 or chart_width <= 0:
            return QBrush(color)
        gradient = QLinearGradient(
            QPointF(float(chart_left), 0.0),
            QPointF(float(chart_right), 0.0),
        )
        transparent = QColor(color)
        transparent.setAlphaF(0.0)
        gradient.setColorAt(0.0, transparent)
        gradient.setColorAt(fade_frac, color)
        gradient.setColorAt(1.0, color)
        return QBrush(gradient)

    def fill_band(
        top_ys: list[float],
        bottom_ys: list[float] | None,
        color_hex: str,
    ) -> None:
        polygon: list[QPointF] = []
        for i, x in enumerate(range(chart_left, chart_right + 1)):
            polygon.append(QPointF(x, top_ys[i]))
        if bottom_ys is None:
            polygon.append(QPointF(chart_right, bands_bottom))
            polygon.append(QPointF(chart_left, bands_bottom))
        else:
            for i in range(n_cols - 1, -1, -1):
                polygon.append(QPointF(chart_left + i, bottom_ys[i]))
        painter.setBrush(band_brush(color_hex))
        painter.drawPolygon(QPolygonF(polygon))

    if chart_height > 0:
        fill_band(y_read, None, cfg.cache_read_color)
        fill_band(y_creation, y_read, cfg.cache_creation_color)
        fill_band(y_input, y_creation, cfg.input_color)

        if cfg.stroke_alpha > 0.0:
            stroke_color = QColor(cfg.input_color)
            stroke_color.setAlphaF(cfg.stroke_alpha)
            from PyQt6.QtGui import QPen
            painter.setPen(QPen(stroke_color, 1.0))
            painter.setBrush(QColor(0, 0, 0, 0))
            painter.drawPolyline(
                QPolygonF([QPointF(chart_left + i, y_input[i]) for i in range(n_cols)])
            )
            painter.setPen(QColor(0, 0, 0, 0))

    # Context-window indicator: thin solid line at the bottom of the
    # chart band whose length is proportional to current context fill.
    # Rendered with the same left-fade so it visually anchors to the
    # band stack above it.
    if show_context_line:
        latest_total = history[-1].total
        fill_fraction = min(1.0, latest_total / effective_cwm)
        if fill_fraction > 0.0:
            line_color_hex = cfg.context_line_color or cfg.input_color
            line_color = QColor(line_color_hex)
            line_color.setAlphaF(cfg.context_line_alpha)
            line_w = chart_width * fill_fraction
            line_y = chart_bottom - line_h + 1
            line_rect = QRectF(
                float(chart_left), float(line_y), float(line_w), float(line_h)
            )
            if fade_frac > 0.0:
                gradient = QLinearGradient(
                    QPointF(float(chart_left), 0.0),
                    QPointF(float(chart_right), 0.0),
                )
                transparent = QColor(line_color)
                transparent.setAlphaF(0.0)
                gradient.setColorAt(0.0, transparent)
                gradient.setColorAt(fade_frac, line_color)
                gradient.setColorAt(1.0, line_color)
                painter.setBrush(QBrush(gradient))
            else:
                painter.setBrush(line_color)
            painter.drawRect(line_rect)

    painter.restore()
