"""User-drawn chart annotations (Feature C).

v1 ships horizontal price lines only (``Drawing.kind == "hline"``).
The package is laid out so a future "trend line" / "rectangle"
expansion is a polymorphism on ``kind`` rather than a parallel
subsystem: the same store, the same persistence envelope, the
same hit-test interface. The format-version envelope earns its
keep when v2 introduces ``kind == "trend"`` and we need to read
files written by both.

Public surface:

* :class:`Drawing` — frozen dataclass + ``to_dict`` / ``from_dict``.
* :func:`make_hline_drawing` — factory with UUID + timestamp.
* :func:`normalize_ticker` — canonical-key helper.
* :class:`DrawingStore` — owns the in-memory state + observer bus.
* :func:`read_drawings` / :func:`write_drawings` / :func:`clear_drawings`
  / :func:`drawings_file_path` — module-level persistence helpers.
* :func:`render_drawings` — re-renders a slot's drawings on an axes.
* :func:`pick_drawing` — display-coord hit-test (closest-wins).
"""

from .model import (
    ALLOWED_KINDS,
    DEFAULT_COLOR,
    DEFAULT_STYLE,
    DEFAULT_WIDTH,
    DRAWING_KIND_HLINE,
    VALID_STYLES,
    Drawing,
    find_nearest_ohlc_snap,
    make_hline_drawing,
    normalize_ticker,
    snap_price_to_grid,
)
from .render import DRAWING_ZORDER, pick_drawing, render_drawings
from .store import (
    DRAWINGS_FILE_FORMAT,
    DRAWINGS_FILE_NAME,
    DRAWINGS_FILE_VERSION,
    DrawingStore,
    clear_drawings,
    drawings_file_path,
    read_drawings,
    write_drawings,
)

__all__ = [
    "ALLOWED_KINDS",
    "DEFAULT_COLOR",
    "DEFAULT_STYLE",
    "DEFAULT_WIDTH",
    "DRAWING_KIND_HLINE",
    "DRAWING_ZORDER",
    "DRAWINGS_FILE_FORMAT",
    "DRAWINGS_FILE_NAME",
    "DRAWINGS_FILE_VERSION",
    "Drawing",
    "DrawingStore",
    "VALID_STYLES",
    "clear_drawings",
    "drawings_file_path",
    "find_nearest_ohlc_snap",
    "make_hline_drawing",
    "normalize_ticker",
    "pick_drawing",
    "read_drawings",
    "render_drawings",
    "snap_price_to_grid",
    "write_drawings",
]
