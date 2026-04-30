"""Application bootstrap and composition root."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from tasklight.config import AppConfig, ConfigWatcher, load_config, save_config
from tasklight.model import AgentStateModel
from tasklight.overlay import OverlayWidget
from tasklight.server import HookServer
from tasklight.tray import build_context_menu, create_tray


def _install_excepthook() -> None:
    def _excepthook(exc_type, exc_value, exc_tb):
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


def run(config_path: Path) -> int:
    _install_excepthook()

    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    cfg = load_config(config_path)
    model = AgentStateModel()

    server = HookServer(port=cfg.port)
    server.event_received.connect(model.apply_event)
    server.start()

    overlay = OverlayWidget(model, cfg)
    context_menu = build_context_menu(overlay)
    overlay.set_context_menu(context_menu)

    def _persist_dock_position(position: str) -> None:
        current_cfg = load_config(config_path)
        updated_cfg = AppConfig(
            port=current_cfg.port,
            dock=current_cfg.dock,
            theme=current_cfg.theme,
            timeouts=current_cfg.timeouts,
        )
        updated_cfg.dock.position = position
        save_config(config_path, updated_cfg)

    overlay.dock_position_changed.connect(_persist_dock_position)

    watcher = ConfigWatcher(config_path)
    watcher.config_changed.connect(overlay.apply_config)

    tray = create_tray(overlay, context_menu)
    if tray is None:
        print("Warning: system tray not available.", file=sys.stderr)

    app.aboutToQuit.connect(server.stop)
    return app.exec()
