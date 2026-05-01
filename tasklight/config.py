"""Configuration dataclasses, YAML loader, and hot-reload watcher."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml
from PyQt6.QtCore import QFileSystemWatcher, QObject, pyqtSignal


@dataclass
class DockConfig:
    position: str = "BR"  # TL|TC|TR|ML|MR|BL|BC|BR
    margin: int = 16
    width: int = 240


@dataclass
class TokenRateConfig:
    enabled: bool = True
    window_s: int = 300            # sliding window length
    width_em: float = 24.0         # chart width in em units (unused; renderer spans full row)
    reset_fraction: float = 0.20   # tokens drop ratio that triggers reset
    scale_headroom: float = 2.5    # y-axis headroom multiplier
    smoothing_tau_s: float = 0.0   # 0 = auto = window_s / 30
    # Time-axis curvature: 1.0 is linear; >1 stretches recent activity
    # near the right edge and compresses older samples toward the left.
    # 2.0 means the right half of the chart shows the last quarter of
    # window_s; 3.0 the right half shows the last eighth.
    time_curve_exponent: float = 2.0
    # Render the chart as if "now" were render_lag_s seconds ago. Hides
    # the empty-then-jump pattern between hook fires by pushing the
    # latency wedge past the right edge of the visible chart. Set to
    # 0.0 to disable.
    render_lag_s: float = 5.0
    # Per-component colors for the stacked bands (bottom-up order):
    #   cache_read    — warm cache reads (cheap, often the bulk)
    #   cache_creation— writes into cache (loading new context)
    #   input         — non-cache input tokens (real conversation)
    cache_read_color: str = "#5599cc"
    cache_creation_color: str = "#cc8844"
    input_color: str = "#cccccc"
    # Legacy alias kept for back-compat with old configs; unused.
    color: str = "#5599cc"
    fill_alpha: float = 0.35
    stroke_alpha: float = 0.0      # 0 = no stroke, just the fill


@dataclass
class ThemeConfig:
    background: str = "#1e1e1e"
    background_alpha: float = 0.85
    foreground: str = "#e8e8e8"
    dirname_fg: str = "#888888"
    hostname_fg: str = "#5599cc"
    system_cursor: bool = True
    animate_spinners: bool = True
    done_fg: str = "#44cc77"
    done_bg: str = ""
    approval_fg: str = "#ff4444"
    approval_bg: str = "#a47000"
    font_family: str = "monospace"
    font_size: int = 13
    corner_radius: int = 10
    token_rate: TokenRateConfig = field(default_factory=TokenRateConfig)


@dataclass
class TimeoutsConfig:
    done_auto_remove_s: int = 0
    exit_grace_s: int = 30


@dataclass
class AppConfig:
    port: int = 57017
    allowed_subnets: list[str] = field(
        default_factory=lambda: ["127.0.0.0/8", "172.16.0.0/12"]
    )
    dock: DockConfig = field(default_factory=DockConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)


def _merge(dataclass_instance, mapping: dict) -> None:
    """Shallow-merge dict keys into a dataclass, ignoring unknown keys."""
    for key, value in mapping.items():
        if hasattr(dataclass_instance, key):
            setattr(dataclass_instance, key, value)


def _write_defaults(path: Path) -> None:
    """Write a default config file. Skips if the parent directory doesn't exist."""
    if not path.parent.exists():
        return
    default_theme = ThemeConfig()
    theme_dict = {k: v for k, v in default_theme.__dict__.items() if k != "token_rate"}
    theme_dict["token_rate"] = {k: v for k, v in TokenRateConfig().__dict__.items()}
    with path.open("w") as fh:
        yaml.dump(
            {
                "port": AppConfig.port,
                "allowed_subnets": AppConfig().allowed_subnets,
                "dock": {k: v for k, v in DockConfig().__dict__.items()},
                "theme": theme_dict,
                "timeouts": {k: v for k, v in TimeoutsConfig().__dict__.items()},
            },
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
    _merge(
        cfg, {k: v for k, v in raw.items() if k not in ("dock", "theme", "timeouts")}
    )
    if "dock" in raw and isinstance(raw["dock"], dict):
        _merge(cfg.dock, raw["dock"])
    if "theme" in raw and isinstance(raw["theme"], dict):
        theme_raw = raw["theme"]
        _merge(cfg.theme, {k: v for k, v in theme_raw.items() if k != "token_rate"})
        if "token_rate" in theme_raw and isinstance(theme_raw["token_rate"], dict):
            _merge(cfg.theme.token_rate, theme_raw["token_rate"])
    if "timeouts" in raw and isinstance(raw["timeouts"], dict):
        _merge(cfg.timeouts, raw["timeouts"])
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
