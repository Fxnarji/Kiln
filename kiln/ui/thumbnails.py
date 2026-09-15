"""Turning files into grid icons.

Two sources, in order:
  1. a preview embedded in the file itself (core.thumbnails)
  2. a generated placeholder tile — a colour derived from the file extension,
     with the extension written on it

The placeholder is not a fallback to apologise for. Most files will use it, and
it still tells an artist what kind of thing they are looking at from across the
room.

Icons are cached by path, size, and modification time, so a refresh does not
re-read every file on disk.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap

from kiln.core.thumbnails import read_thumbnail

ICON_SIZE = 128
ICON_DIRECTORY = Path(__file__).resolve().parent.parent / "assets" / "icons"

# Blender stores its preview bottom row first, the way OpenGL hands it over,
# so it has to be flipped before display. Confirmed against real .blend files
# and pinned by test_pixels_are_stored_bottom_row_first — without this every
# preview in the grid is upside down.
BLEND_PREVIEW_IS_BOTTOM_UP = True

# A stable colour per extension for the final fallback tile.
EXTENSION_COLOURS = {
    ".blend": "#4A4763",
    ".spp": "#7A5A48",
    ".psd": "#6B5C7A",
    ".png": "#3E5A47",
    ".jpg": "#3E5A47",
    ".jpeg": "#3E5A47",
    ".exr": "#3D5560",
    ".tif": "#3D5560",
    ".tiff": "#3D5560",
    ".fbx": "#56544C",
    ".obj": "#56544C",
    ".abc": "#4E5661",
    ".usd": "#4E5661",
    ".wav": "#63424E",
    ".mp4": "#63424E",
}
DEFAULT_COLOUR = "#3A3F45"


class ThumbnailCache:
    """Icons for the file grid, built once and reused."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self._icons: dict[tuple[str, int, int], QIcon] = {}

    def icon_for(self, repo_relative_path: str, downloaded: bool) -> QIcon:
        """An icon for one file. Cheap after the first call."""
        full_path = self.repo_root / repo_relative_path
        key = self._cache_key(repo_relative_path, full_path)

        cached = self._icons.get(key)
        if cached is not None:
            return cached

        icon = self._build_icon(full_path, downloaded)
        self._icons[key] = icon
        return icon

    def icon_for_folder(self) -> QIcon:
        icon = _asset_icon("folder.png")
        return QIcon(_square(QPixmap(str(icon)))) if icon is not None else QIcon()

    def _cache_key(self, repo_relative_path: str, full_path: Path) -> tuple[str, int, int]:
        """Include size and mtime so an edited file gets a fresh preview."""
        try:
            stat = full_path.stat()
            return (repo_relative_path, stat.st_size, int(stat.st_mtime))
        except OSError:
            return (repo_relative_path, 0, 0)

    def _build_icon(self, full_path: Path, downloaded: bool) -> QIcon:
        """Pick the best source, then square it.

        Every source has a different natural shape — Blender previews are
        typically 128x77, the supplied icons are 512 square except .usd which
        is 456x512, and generated placeholders are 128 square. They all go
        through _square so the grid receives one consistent size.
        """
        if downloaded:
            preview = read_thumbnail(full_path)
            if preview is not None:
                return QIcon(_square(_pixmap_from_preview(preview)))

        asset = _filetype_icon(full_path.suffix)
        if asset is not None:
            return QIcon(_square(QPixmap(str(asset))))

        return QIcon(_placeholder_pixmap(full_path.suffix, downloaded))


def _filetype_icon(suffix: str) -> Path | None:
    """Find a supplied icon, accepting both documented and existing names."""
    normalized = suffix.lower()
    candidates = [
        f"{normalized}.png",
        f"{normalized.lstrip('.')}.png",
        "generic.png",
    ]
    for filename in candidates:
        path = ICON_DIRECTORY / filename
        if path.is_file():
            return path
    return None


def _asset_icon(filename: str) -> Path | None:
    path = ICON_DIRECTORY / filename
    return path if path.is_file() else None


def _square(pixmap: QPixmap) -> QPixmap:
    """Fit a pixmap inside a transparent ICON_SIZE square, centred.

    Every icon handed to the grid has to be exactly the same size. The view is
    told setUniformItemSizes(True), which makes Qt apply the first item's size
    hint to every item — so one 128x77 Blender preview sitting next to a 128
    square placeholder clips whichever kind is taller. Letterboxing here means
    the aspect ratio is preserved and the tile size never varies.
    """
    if pixmap.isNull():
        return pixmap

    scaled = pixmap.scaled(
        ICON_SIZE, ICON_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )
    if scaled.size() == QSize(ICON_SIZE, ICON_SIZE):
        return scaled

    canvas = QPixmap(ICON_SIZE, ICON_SIZE)
    canvas.fill(Qt.transparent)

    painter = QPainter(canvas)
    try:
        painter.drawPixmap(
            (ICON_SIZE - scaled.width()) // 2,
            (ICON_SIZE - scaled.height()) // 2,
            scaled,
        )
    finally:
        painter.end()

    return canvas


def _pixmap_from_preview(preview) -> QPixmap:
    """The embedded preview as a pixmap, at its own aspect ratio.

    Sizing is left to _square, so there is one place that decides how an icon
    is fitted to the grid.
    """
    image = QImage(
        preview.rgba,
        preview.width,
        preview.height,
        preview.width * 4,
        QImage.Format_RGBA8888,
    ).copy()  # copy: the QImage must not outlive the bytes it was built from

    if BLEND_PREVIEW_IS_BOTTOM_UP:
        image = image.flipped(Qt.Vertical)

    return QPixmap.fromImage(image)


def _placeholder_pixmap(suffix: str, downloaded: bool) -> QPixmap:
    """A coloured tile carrying the file extension."""
    colour = QColor(EXTENSION_COLOURS.get(suffix.lower(), DEFAULT_COLOUR))
    if not downloaded:
        colour = colour.darker(180)

    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(colour)

    painter = QPainter(pixmap)
    try:
        label = suffix.lstrip(".").upper() or "FILE"
        font = QFont()
        font.setPointSize(16 if len(label) <= 4 else 12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 190))
        painter.drawText(
            QRect(0, 0, ICON_SIZE, ICON_SIZE), Qt.AlignCenter, label
        )

        if not downloaded:
            small = QFont()
            small.setPointSize(8)
            painter.setFont(small)
            painter.setPen(QColor("#C4574E"))
            painter.drawText(
                QRect(0, ICON_SIZE - 26, ICON_SIZE, 20),
                Qt.AlignCenter,
                "not downloaded",
            )
    finally:
        painter.end()

    return pixmap
