"""Application bootstrap and composition root."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from tasklight.config import AppConfig, ConfigWatcher, load_config, save_config
from tasklight.model import AgentStateModel
from tasklight.overlay import OverlayWidget
from tasklight.server import HookServer
from tasklight.tray import build_context_menu, create_tray, load_app_icon


def _install_excepthook() -> None:
    def _excepthook(exc_type, exc_value, exc_tb):
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


def run(config_path: Path) -> int:
    _install_excepthook()

    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Tasklight")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(load_app_icon())

    cfg = load_config(config_path)
    model = AgentStateModel()

    server = HookServer(port=cfg.port, allowed_subnets=cfg.allowed_subnets)
    server.event_received.connect(model.apply_event)
    server.start()

    overlay = OverlayWidget(model, cfg)
    context_menu = build_context_menu(overlay, model)
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
    watcher.config_changed.connect(lambda c: server.update_subnets(c.allowed_subnets))

    tray = create_tray(overlay, context_menu)
    if tray is None:
        print("Warning: system tray not available.", file=sys.stderr)

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Qt's event loop doesn't yield to Python often enough to deliver SIGINT
    # without a periodic wakeup. This no-op timer unblocks signal checking.
    _sigint_wakeup = QTimer()
    _sigint_wakeup.start(200)
    _sigint_wakeup.timeout.connect(lambda: None)

    app.aboutToQuit.connect(server.stop)
    return app.exec()


def cli() -> None:
    parser = argparse.ArgumentParser(description="Tasklight agent monitor")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=Path("tasklight.yaml"),
        metavar="PATH",
        help="Config file (default: ./tasklight.yaml)",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.config))
