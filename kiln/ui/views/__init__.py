"""The list screens that open beside the main window.

Each screen is a package of small pieces rather than one class: base.ViewWindow
frames a list, an optional side panel, an optional bottom panel, and a right
click menu, and every one of those lives in its own module. Adding a screen
means writing the pieces it needs and naming them here.
"""

from kiln.ui.views.base import ViewWindow
from kiln.ui.views.changes_window import ChangesWindow
from kiln.ui.views.locks_window import LocksWindow

#: The window class for each named view, for the main window to open.
VIEW_WINDOWS = {
    ChangesWindow.view_name: ChangesWindow,
    LocksWindow.view_name: LocksWindow,
}

__all__ = ["VIEW_WINDOWS", "ChangesWindow", "LocksWindow", "ViewWindow"]
