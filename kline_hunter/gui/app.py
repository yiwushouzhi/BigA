from __future__ import annotations

import sys

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from ..config import APP_NAME, ORG_NAME, AppConfig
from .main_window import MainWindow
from .settings import load_saved_config


def run(config: AppConfig | None = None) -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationDisplayName("K线智能筛选定位器")
    app.setStyle("Fusion")

    if sys.platform == "win32":
        family = "Microsoft YaHei UI"
    elif sys.platform == "darwin":
        family = "PingFang SC"
    else:
        family = "Noto Sans CJK SC"
    font = QFont(family, 10)
    app.setFont(font)

    resolved = load_saved_config(config or AppConfig())
    window = MainWindow(resolved)
    window.show()
    return app.exec()
