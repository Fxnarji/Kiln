"""Application look.

Fusion plus a dark palette, and nothing else. No stylesheets, no bundled fonts,
no custom drawing. Qt's own widgets are what get maintained for free.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Roles that carry meaning in the file tables. Kept here so there is one place
# to change if any of them turn out to be hard to read.
# Green and red carry the only question an artist asks about a lock: can I
# have this file, or is somebody else in it. Nothing else in the application
# is allowed to use these two, so the answer is never ambiguous.
COLOUR_LOCKED_BY_ME = QColor("#5FAE6B")
COLOUR_LOCKED_BY_OTHER = QColor("#C4574E")

COLOUR_MODIFIED = QColor("#E2703A")
COLOUR_CONFLICTED = QColor("#C4574E")
COLOUR_NEW = QColor("#6FA8A0")
COLOUR_MUTED = QColor("#8B9099")


def apply_dark_theme(application: QApplication) -> None:
    application.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(32, 34, 37))
    palette.setColor(QPalette.WindowText, QColor(222, 222, 222))
    palette.setColor(QPalette.Base, QColor(24, 26, 28))
    palette.setColor(QPalette.AlternateBase, QColor(38, 40, 43))
    palette.setColor(QPalette.ToolTipBase, QColor(38, 40, 43))
    palette.setColor(QPalette.ToolTipText, QColor(222, 222, 222))
    palette.setColor(QPalette.Text, QColor(222, 222, 222))
    palette.setColor(QPalette.Button, QColor(45, 47, 51))
    palette.setColor(QPalette.ButtonText, QColor(222, 222, 222))
    palette.setColor(QPalette.BrightText, QColor("#E2703A"))
    palette.setColor(QPalette.Highlight, QColor("#8A4526"))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    palette.setColor(QPalette.PlaceholderText, COLOUR_MUTED)

    for group in (QPalette.Disabled,):
        palette.setColor(group, QPalette.Text, COLOUR_MUTED)
        palette.setColor(group, QPalette.ButtonText, COLOUR_MUTED)
        palette.setColor(group, QPalette.WindowText, COLOUR_MUTED)

    application.setPalette(palette)


def lock_colour(is_mine: bool) -> QColor:
    """What a held file is drawn in: green if it is yours, red if it is not."""
    return COLOUR_LOCKED_BY_ME if is_mine else COLOUR_LOCKED_BY_OTHER


def status_colour(status: str) -> QColor | None:
    return {
        "conflicted": COLOUR_CONFLICTED,
        "new": COLOUR_NEW,
        "modified": COLOUR_MODIFIED,
    }.get(status)
