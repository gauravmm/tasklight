"""Token-rate sparkline: pure rate functions + QPainter rendering."""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass

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


def _lerp_color(c_safe: QColor, c_warn: QColor, t: float) -> QColor:
    """Linearly interpolate two QColors in RGB space."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(c_safe.red() * (1.0 - t) + c_warn.red() * t),
        int(c_safe.green() * (1.0 - t) + c_warn.green() * t),
        int(c_safe.blue() * (1.0 - t) + c_warn.blue() * t),
    )


def _fill_threshold_color(cfg: TokenRateConfig, fill_fraction: float) -> QColor:
    """Color for the fill-driven indicators (tint + line).

    Below ``tint_warn_start_fraction`` the color is ``tint_color_safe``;
    above ``tint_warn_full_fraction`` it's ``tint_color_warn``; between
    the two it lerps linearly.
    """
    tint = cfg.context.tint
    start = tint.warn_start_fraction
    full = tint.warn_full_fraction
    if full <= start:
        t = 1.0 if fill_fraction >= full else 0.0
    elif fill_fraction <= start:
        t = 0.0
    elif fill_fraction >= full:
        t = 1.0
    else:
        t = (fill_fraction - start) / (full - start)
    return _lerp_color(QColor(tint.color_safe), QColor(tint.color_warn), t)


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

    sampling = cfg.sampling
    bands = cfg.bands
    time_axis = cfg.time_axis
    context = cfg.context
    line_cfg = context.line
    marker_cfg = context.marker
    tint_cfg = context.tint

    window_s = sampling.window_s
    tau_s = sampling.smoothing_tau_s if sampling.smoothing_tau_s > 0.0 else window_s / 30.0
    curve = max(1.0, time_axis.time_curve_exponent)
    display_now = now - max(0.0, time_axis.render_lag_s)

    # Effective context-window cap: prefer the per-record value supplied
    # by the caller (derived from the latest hook payload's model
    # field); fall back to the config default if that's unknown.
    effective_cwm = context_window_max if context_window_max > 0 else context.window_max

    # Reserve the bottom rows for the context-window line if enabled.
    # The triangular marker (if enabled) overlaps the bands rather
    # than taking its own reserved row.
    show_context_line = effective_cwm > 0 and line_cfg.height_px > 0
    line_h = line_cfg.height_px if show_context_line else 0
    show_marker = show_context_line and marker_cfg.size_px > 0
    marker_h = marker_cfg.size_px if show_marker else 0
    # 1px gap between bands and line so the line reads as separate.
    bands_bottom = chart_bottom - (line_h + 1 if show_context_line else 0)

    mean_rate = compute_mean_rate(history, window_s, now)
    chart_height = bands_bottom - chart_top - 1
    if chart_height <= 0:
        # Row too short for both bands and line; skip bands.
        chart_height = 0
    y_scale = chart_height / max(mean_rate * bands.scale_headroom, 1.0) if chart_height > 0 else 0.0

    # Compute fill_fraction once: drives the background tint and the
    # line color. Falls back to 0 if context_window_max is unknown.
    fill_fraction = 0.0
    if effective_cwm > 0 and history:
        fill_fraction = min(1.0, history[-1].total / effective_cwm)
    threshold_color = _fill_threshold_color(cfg, fill_fraction)

    # For each pixel column, compute three cumulative top-of-band y values:
    # y_read (top of read band), y_creation (top of creation band),
    # y_input (top of input band = top of full stack).
    n_cols = chart_right - chart_left + 1
    y_read: list[float] = []
    y_creation: list[float] = []
    y_input: list[float] = []

    for x in range(chart_left, chart_right + 1):
        u = (chart_right - x) / chart_width
        t_x = display_now - window_s * (u**curve)

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

    # Left-edge fade fraction is shared by tint, bands, and line.
    fade_frac = max(0.0, min(1.0, time_axis.left_fade_fraction))

    # Ambient tint: fills only the filled portion of the chart rect
    # (left edge to fill-fraction position) with the threshold-derived
    # color at low alpha. Spatially aligned with the line indicator
    # at the top, and uses the same left-edge fade as the bands.
    # Painted FIRST so bands and line draw over it.
    if tint_cfg.alpha > 0.0 and effective_cwm > 0 and fill_fraction > 0.0:
        tint = QColor(threshold_color)
        tint.setAlphaF(tint_cfg.alpha)
        tint_rect = QRectF(
            float(chart_left),
            float(chart_top),
            float(chart_width * fill_fraction),
            float(rect.height()),
        )
        if fade_frac > 0.0:
            gradient = QLinearGradient(
                QPointF(float(chart_left), 0.0),
                QPointF(float(chart_right), 0.0),
            )
            transparent = QColor(tint)
            transparent.setAlphaF(0.0)
            gradient.setColorAt(0.0, transparent)
            gradient.setColorAt(fade_frac, tint)
            gradient.setColorAt(1.0, tint)
            painter.fillRect(tint_rect, QBrush(gradient))
        else:
            painter.fillRect(tint_rect, tint)

    # Left-edge fade applied to every band: 0% alpha at chart_left,
    # fill_alpha at the fade-in stop, constant to chart_right.
    def band_brush(color_hex: str) -> QBrush:
        color = QColor(color_hex)
        color.setAlphaF(bands.fill_alpha)
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
        fill_band(y_read, None, bands.cache_read_color)
        fill_band(y_creation, y_read, bands.cache_creation_color)
        fill_band(y_input, y_creation, bands.input_color)

        if bands.stroke_alpha > 0.0:
            stroke_color = QColor(bands.input_color)
            stroke_color.setAlphaF(bands.stroke_alpha)
            from PyQt6.QtGui import QPen

            painter.setPen(QPen(stroke_color, 1.0))
            painter.setBrush(QColor(0, 0, 0, 0))
            painter.drawPolyline(QPolygonF([QPointF(chart_left + i, y_input[i]) for i in range(n_cols)]))
            painter.setPen(QColor(0, 0, 0, 0))

    # Context-window indicator: thin solid line at the bottom of the
    # chart band whose length is proportional to current context fill.
    # Rendered with the same left-fade so it visually anchors to the
    # band stack above it.
    if show_context_line:
        if fill_fraction > 0.0:
            if line_cfg.color:
                line_color = QColor(line_cfg.color)
            else:
                line_color = QColor(threshold_color)
            line_color.setAlphaF(line_cfg.alpha)
            line_w = chart_width * fill_fraction
            line_y = chart_bottom - line_h + 1
            line_rect = QRectF(float(chart_left), float(line_y), float(line_w), float(line_h))
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

            # Triangular marker pointing down at the head of the bar.
            # Apex sits 1 px above the line; base is `marker_h` px above
            # the apex. Half-width equal to height for a clean arrow.
            if show_marker:
                apex_x = chart_left + chart_width * fill_fraction
                apex_y = line_y - 1
                base_y = apex_y - marker_h
                half_w = marker_h
                marker_color = QColor(line_color)
                marker_color.setAlphaF(line_cfg.alpha)
                painter.setBrush(marker_color)
                painter.drawPolygon(
                    QPolygonF(
                        [
                            QPointF(apex_x - half_w, float(base_y)),
                            QPointF(apex_x + half_w, float(base_y)),
                            QPointF(apex_x, float(apex_y)),
                        ]
                    )
                )

    painter.restore()
