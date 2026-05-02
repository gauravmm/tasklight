"""Configuration dataclasses, YAML loader, and hot-reload watcher."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

import yaml
from PyQt6.QtCore import QFileSystemWatcher, QObject, pyqtSignal


@dataclass
class DockConfig:
    position: str = "BR"  # TL|TC|TR|ML|MR|BL|BC|BR
    margin: int = 16
    width: int = 240


# ---------------------------------------------------------------------------
# Theme: window / text / states / behavior
# ---------------------------------------------------------------------------


@dataclass
class WindowConfig:
    """The overlay panel itself."""

    background: str = "#1e1e1e"
    background_alpha: float = 0.85
    corner_radius: int = 10


@dataclass
class TextConfig:
    """Text colors and font."""

    foreground: str = "#e8e8e8"
    dirname_fg: str = "#888888"
    hostname_fg: str = "#5599cc"
    elapsed_fg: str = ""  # empty = use dirname_fg
    font_family: str = "monospace"
    font_size: int = 13


@dataclass
class StatesConfig:
    """Per-AgentState colors."""

    done_fg: str = "#44cc77"
    done_bg: str = ""
    approval_fg: str = "#ff4444"
    approval_bg: str = "#a47000"


@dataclass
class BehaviorConfig:
    """UX toggles."""

    system_cursor: bool = True
    animate_spinners: bool = True


# ---------------------------------------------------------------------------
# Token-rate sparkline: sampling / bands / time_axis / context
# ---------------------------------------------------------------------------


@dataclass
class SamplingConfig:
    """How token samples are gathered, aged out, and segmented."""

    window_s: int = 300  # sliding window length
    reset_fraction: float = 0.20  # total drop ratio that triggers reset
    smoothing_tau_s: float = 0.0  # 0 = auto = window_s / 30


@dataclass
class BandsConfig:
    """The stacked rate sparkline (bottom-up: cache_read, cache_creation, input)."""

    cache_read_color: str = "#5599cc"
    cache_creation_color: str = "#ff9933"
    input_color: str = "#cccccc"
    fill_alpha: float = 0.35
    stroke_alpha: float = 0.0
    scale_headroom: float = 2.5  # y-axis headroom multiplier


@dataclass
class TimeAxisConfig:
    """Pixel-to-time mapping for the rate chart."""

    # 1.0 = linear; >1 stretches recent activity near the right edge
    # and compresses older samples toward the left.
    time_curve_exponent: float = 2.0
    # Render the chart as if "now" were render_lag_s seconds ago — hides
    # the empty-then-jump pattern between hook fires.
    render_lag_s: float = 5.0
    # Fraction of chart width over which the left edge fades in from
    # transparent to full opacity. 0.0 disables.
    left_fade_fraction: float = 0.15


@dataclass
class ContextLineConfig:
    """The thin horizontal bar at the bottom showing context fill level."""

    height_px: int = 1
    alpha: float = 0.65
    color: str = ""  # empty = use threshold-lerp color


@dataclass
class ContextMarkerConfig:
    """The triangular marker that points down at the bar's head."""

    size_px: int = 4  # 0 disables; height and half-width


@dataclass
class ContextTintConfig:
    """Ambient background tint that shifts color as context fills."""

    alpha: float = 0.01
    color_safe: str = "#ffffff"
    color_warn: str = "#ffaa00"
    warn_start_fraction: float = 0.60
    warn_full_fraction: float = 0.80


@dataclass
class ContextConfig:
    """Family of indicators that show absolute context-window fill."""

    window_max: int = 200000  # 0 disables the whole family
    line: ContextLineConfig = field(default_factory=ContextLineConfig)
    marker: ContextMarkerConfig = field(default_factory=ContextMarkerConfig)
    tint: ContextTintConfig = field(default_factory=ContextTintConfig)


@dataclass
class TokenRateConfig:
    enabled: bool = True
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    bands: BandsConfig = field(default_factory=BandsConfig)
    time_axis: TimeAxisConfig = field(default_factory=TimeAxisConfig)
    context: ContextConfig = field(default_factory=ContextConfig)


@dataclass
class ThemeConfig:
    window: WindowConfig = field(default_factory=WindowConfig)
    text: TextConfig = field(default_factory=TextConfig)
    states: StatesConfig = field(default_factory=StatesConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    token_rate: TokenRateConfig = field(default_factory=TokenRateConfig)


@dataclass
class TimeoutsConfig:
    done_auto_remove_s: int = 0
    exit_grace_s: int = 30


@dataclass
class AppConfig:
    port: int = 57017
    allowed_subnets: list[str] = field(default_factory=lambda: ["127.0.0.0/8", "172.16.0.0/12"])
    dock: DockConfig = field(default_factory=DockConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------


def _merge_dataclass(instance, mapping: dict) -> None:
    """Recursively merge a dict into a dataclass instance.

    Nested-dataclass fields with corresponding dict subtrees are merged
    in place; scalar fields are overwritten. Unknown keys are ignored.
    """
    if not isinstance(mapping, dict):
        return
    valid_names = {f.name for f in fields(instance)}
    for key, value in mapping.items():
        if key not in valid_names:
            continue
        current = getattr(instance, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge_dataclass(current, value)
        else:
            setattr(instance, key, value)


def _write_defaults(path: Path) -> None:
    """Write a default config file. Skips if the parent directory doesn't exist."""
    if not path.parent.exists():
        return
    with path.open("w") as fh:
        yaml.dump(
            asdict(AppConfig()),
            fh,
            default_flow_style=False,
            sort_keys=False,
        )
    print(f"[config] wrote defaults to {path}", file=sys.stderr)


def load_config(path: Path) -> AppConfig:
    cfg = AppConfig()
    if not path.exists():
        _write_defaults(path)
        return cfg
    with path.open() as fh:
        raw = yaml.safe_load(fh) or {}
    _merge_dataclass(cfg, raw)
    return cfg


def save_config(path: Path, cfg: AppConfig) -> None:
    """Write the current config back to disk."""
    if not path.parent.exists():
        return
    with path.open("w") as fh:
        yaml.dump(
            asdict(cfg),
            fh,
            default_flow_style=False,
            sort_keys=False,
        )


class ConfigWatcher(QObject):
    config_changed = pyqtSignal(object)  # emits AppConfig

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = path
        self._watcher = QFileSystemWatcher(self)
        if path.exists():
            self._watcher.addPath(str(path))
        # Watch the parent directory so we catch atomic-save creates.
        self._watcher.addPath(str(path.parent))
        self._watcher.fileChanged.connect(self._on_changed)
        self._watcher.directoryChanged.connect(self._on_dir_changed)

    def _reload(self) -> None:
        try:
            self.config_changed.emit(load_config(self._path))
        except Exception as exc:
            print(f"[config] parse error: {exc}", file=sys.stderr)

    def _on_changed(self) -> None:
        # Re-add after atomic save (editor replaced the inode).
        if str(self._path) not in self._watcher.files() and self._path.exists():
            self._watcher.addPath(str(self._path))
        self._reload()

    def _on_dir_changed(self) -> None:
        # File may have been created for the first time.
        if self._path.exists() and str(self._path) not in self._watcher.files():
            self._watcher.addPath(str(self._path))
            self._reload()
