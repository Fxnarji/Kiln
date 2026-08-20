"""Create or edit a pinned folder.

The depth control is the only unusual idea in the UI, so the dialog shows what
each depth would actually include rather than making the artist guess.
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from kiln.core.models import DEPTH_DESCRIPTIONS, DEPTH_UNLIMITED, Pin

PREVIEW_LIMIT = 8


class PinDialog(QDialog):
    def __init__(self, folder: str, all_files: list[str], existing: Pin | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pin a folder")
        self.setMinimumWidth(520)

        self._folder = folder
        self._all_files = all_files
        self._color = QColor(existing.color) if existing and existing.color else QColor()

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.label_edit = QLineEdit(existing.label if existing else folder.split("/")[-1])
        form.addRow("Name", self.label_edit)
        form.addRow("Folder", QLabel(folder or "(repository root)"))

        self.depth_box = QComboBox()
        for depth in range(1, DEPTH_UNLIMITED + 1):
            self.depth_box.addItem(f"{depth if depth < DEPTH_UNLIMITED else 'all'}  —  {DEPTH_DESCRIPTIONS[depth]}", depth)
        self.depth_box.setCurrentIndex((existing.depth if existing else 2) - 1)
        self.depth_box.currentIndexChanged.connect(self._update_preview)
        form.addRow("Depth", self.depth_box)

        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._choose_color)
        form.addRow("Color", self.color_button)

        layout.addLayout(form)

        self.preview_label = QLabel()
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_preview()
        self._update_color_button()

    def _choose_color(self) -> None:
        initial = self._color if self._color.isValid() else QColor("#E2703A")
        chosen = QColorDialog.getColor(initial, self, "Pin color")
        if chosen.isValid():
            self._color = chosen
            self._update_color_button()

    def _update_color_button(self) -> None:
        self.color_button.setText(self._color.name() if self._color.isValid() else "Automatic")

    def _update_preview(self) -> None:
        pin = self.pin()
        matching = [path for path in self._all_files if pin.contains(path)]

        if not matching:
            self.preview_label.setText("This depth matches no files.")
            return

        shown = matching[:PREVIEW_LIMIT]
        listing = "\n".join(f"    {path}" for path in shown)
        more = (
            f"\n    …and {len(matching) - PREVIEW_LIMIT} more"
            if len(matching) > PREVIEW_LIMIT
            else ""
        )
        self.preview_label.setText(f"{len(matching)} file(s):\n{listing}{more}")

    def pin(self) -> Pin:
        return Pin(
            path=self._folder,
            depth=self.depth_box.currentData(),
            label=self.label_edit.text().strip(),
            color=self._color.name() if self._color.isValid() else "",
        )
