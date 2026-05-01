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
    with path.open("w") as fh:
        yaml.dump(
            {
                "port": AppConfig.port,
                "allowed_subnets": AppConfig().allowed_subnets,
                "dock": {k: v for k, v in DockConfig().__dict__.items()},
                "theme": {k: v for k, v in ThemeConfig().__dict__.items()},
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
        _merge(cfg.theme, raw["theme"])
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
